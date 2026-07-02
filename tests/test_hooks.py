import sys

import pytest

from codeward import hooks


@pytest.mark.parametrize(
    "command, expected",
    [
        ("cat src/app.py", "codeward read src/app.py"),
        ("rg UserService", "codeward search UserService"),
        ("find . -maxdepth 2 -type f", "codeward map"),
        ("git status", "codeward status"),
        ("pytest tests/", "codeward test pytest tests/"),
        ("!raw cat src/app.py", "cat src/app.py"),
    ],
)
def test_rewrite_command_table_cases(command, expected):
    assert hooks.rewrite_command(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "cat src/app.py && rm -rf build",
        "rg User | cat",
        "grep User -- src/app.py",
        "codeward map",
        "rtk cat src/app.py",
    ],
)
def test_rewrite_command_rejects_shell_metachars_and_unsafe_forms(command):
    assert hooks.rewrite_command(command) is None


@pytest.mark.parametrize(
    "agent, expected_key",
    [
        ("claude", "hookSpecificOutput"),
        ("cursor", "updated_input"),
        ("gemini", "hookSpecificOutput"),
        ("generic", "updatedInput"),
    ],
)
def test_hook_response_shapes_by_agent(agent, expected_key):
    response = hooks.hook_response({"tool_name": "Bash", "tool_input": {"command": "cat src/app.py"}}, agent)

    assert response is not None
    assert expected_key in response


def test_codex_bash_hook_is_noop_because_updated_input_is_ignored():
    assert hooks.hook_response({"tool_name": "Bash", "tool_input": {"command": "cat src/app.py"}}, "codex") is None


@pytest.mark.parametrize("agent", ["claude", "codex", "cursor", "gemini", "generic"])
def test_edit_hook_response_shapes(monkeypatch, agent):
    monkeypatch.setattr(hooks, "_preflight_for_file", lambda file_path, root: "preflight summary")

    response = hooks.edit_hook_response({"tool_input": {"file_path": "src/app.py"}}, agent)

    assert response is not None
    assert "preflight summary" in str(response)


def test_compact_test_output_pass_and_fail(tmp_path):
    passing = [sys.executable, "-c", "print('2 passed in 0.01s')"]
    rc, raw, summary = hooks.compact_test_output(passing, tmp_path)
    assert rc == 0
    assert "2 passed" in raw
    assert "tests passed" in summary

    failing = [sys.executable, "-c", "import sys; print('AssertionError: boom'); sys.exit(1)"]
    rc, raw, summary = hooks.compact_test_output(failing, tmp_path)
    assert rc == 1
    assert "AssertionError" in raw
    assert "tests failed" in summary
    assert "AssertionError" in summary
