import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def run_cli(args, cwd):
    cmd = [sys.executable, "-m", "codeward.cli", *args]
    # HOME=cwd isolates each test's global history write (~/.codeward/...)
    # to its own tmp dir so tests don't pollute the developer's real ~/.codeward.
    return subprocess.run(
        cmd, cwd=cwd, text=True, capture_output=True,
        env={"PYTHONPATH": str(SRC), "HOME": str(cwd)},
    )


@pytest.fixture
def sample_repo(tmp_path):
    (tmp_path / "src" / "services").mkdir(parents=True)
    (tmp_path / "src" / "controllers").mkdir(parents=True)
    (tmp_path / "src" / "routes").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "services" / "user_service.py").write_text('''
from src.db import db
from src.emailer import send_welcome_email

class UserService:
    def create_user(self, email: str) -> dict:
        user = db.users.create({"email": email})
        send_welcome_email(email)
        return user

    def delete_user(self, user_id: str) -> None:
        db.users.delete(user_id)
''')
    (tmp_path / "src" / "controllers" / "user_controller.py").write_text('''
from src.services.user_service import UserService

def create_user_controller(request):
    return UserService().create_user(request["email"])
''')
    (tmp_path / "src" / "routes" / "user_routes.py").write_text('''
from src.controllers.user_controller import create_user_controller

ROUTES = {"POST /api/users": create_user_controller}
''')
    (tmp_path / "src" / "db.py").write_text('''
class Users:
    def create(self, data): return data
    def delete(self, id): return None
class DB:
    users = Users()
db = DB()
''')
    (tmp_path / "src" / "emailer.py").write_text('''
def send_welcome_email(email):
    return True
''')
    (tmp_path / "tests" / "test_user_service.py").write_text('''
from src.services.user_service import UserService

def test_create_user():
    assert UserService().create_user("a@test.com")["email"] == "a@test.com"
''')
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=tmp_path)
    subprocess.run(["git", "config", "user.name", "A"], cwd=tmp_path)
    subprocess.run(["git", "add", "."], cwd=tmp_path)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, text=True)
    return tmp_path


def test_map_summarizes_repo(sample_repo):
    result = run_cli(["map"], sample_repo)
    assert result.returncode == 0
    assert "Python repo" in result.stdout
    assert "src/services/user_service.py" in result.stdout
    assert "tests/test_user_service.py" in result.stdout


def test_read_returns_semantic_summary(sample_repo):
    result = run_cli(["read", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert "Role:" in result.stdout
    assert "class UserService" in result.stdout
    assert "create_user" in result.stdout
    assert "Used by:" in result.stdout
    assert "src/controllers/user_controller.py" in result.stdout


def test_symbol_finds_definition_callers_and_tests(sample_repo):
    result = run_cli(["symbol", "UserService"], sample_repo)
    assert result.returncode == 0
    assert "Defined:" in result.stdout
    assert "src/services/user_service.py" in result.stdout
    assert "Callers:" in result.stdout
    assert "Tests:" in result.stdout


def test_search_groups_matches(sample_repo):
    result = run_cli(["search", "UserService"], sample_repo)
    assert result.returncode == 0
    assert "3 matches" in result.stdout
    assert "src/services/user_service.py" in result.stdout
    assert "src/controllers/user_controller.py" in result.stdout


def test_callgraph_for_route(sample_repo):
    result = run_cli(["callgraph", "POST /api/users"], sample_repo)
    assert result.returncode == 0
    assert "POST /api/users" in result.stdout
    assert "create_user_controller" in result.stdout
    assert "UserService.create_user" in result.stdout
    assert "Side effects:" in result.stdout


def test_callgraph_traces_non_fixture_route_without_global_side_effects(sample_repo):
    (sample_repo / "src" / "services" / "order_service.py").write_text('''
from src.db import db

class OrderService:
    def submit_order(self, payload):
        return db.orders.create(payload)
''')
    (sample_repo / "src" / "controllers" / "order_controller.py").write_text('''
from src.services.order_service import OrderService

def submit_order_controller(request):
    return OrderService().submit_order(request["json"])
''')
    (sample_repo / "src" / "routes" / "order_routes.py").write_text('''
from src.controllers.order_controller import submit_order_controller

ROUTES = {"POST /api/orders": submit_order_controller}
''')
    result = run_cli(["callgraph", "POST /api/orders"], sample_repo)
    assert result.returncode == 0
    assert "submit_order_controller" in result.stdout
    assert "OrderService.submit_order" in result.stdout
    assert "UserService.create_user" not in result.stdout
    assert "Email send" not in result.stdout
    assert "DB write" in result.stdout


def test_tests_for_file(sample_repo):
    result = run_cli(["tests-for", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert "tests/test_user_service.py" in result.stdout
    assert "Suggested command:" in result.stdout


def test_tests_for_file_avoids_short_stem_false_positives(sample_repo):
    (sample_repo / "tests" / "test_user_routes.py").write_text('''
def test_routes():
    assert True
''')
    result = run_cli(["tests-for", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert "tests/test_user_service.py" in result.stdout
    assert "tests/test_user_routes.py" not in result.stdout


def test_impact_changed_file(sample_repo):
    target = sample_repo / "src" / "services" / "user_service.py"
    target.write_text(target.read_text() + "\n# changed\n")
    result = run_cli(["impact", "--changed"], sample_repo)
    assert result.returncode == 0
    assert "src/services/user_service.py" in result.stdout
    assert "Direct dependents:" in result.stdout
    assert "tests/test_user_service.py" in result.stdout


def test_review_changed_flags_side_effects_and_tests(sample_repo):
    target = sample_repo / "src" / "services" / "user_service.py"
    target.write_text(target.read_text() + "\ndef audit():\n    db.users.create({'audit': True})\n")
    result = run_cli(["review", "--changed"], sample_repo)
    assert result.returncode == 0
    assert "Review summary:" in result.stdout
    assert "DB write" in result.stdout
    assert "Suggested commands:" in result.stdout


def test_hook_rewrites_noisy_commands(sample_repo):
    payload = {"tool_input": {"command": "cat src/services/user_service.py"}}
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "hook"],
        input=json.dumps(payload),
        cwd=sample_repo,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    command = data["hookSpecificOutput"]["updatedInput"]["command"]
    assert command.startswith("CODEWARD_ORIGINAL_COMMAND=")
    assert command.endswith("codeward read src/services/user_service.py")


def test_hook_outputs_agent_specific_rewrite_formats(sample_repo):
    cases = [
        ("claude", {"tool_input": {"command": "cat src/services/user_service.py"}}, ["hookSpecificOutput", "updatedInput"]),
        ("cursor", {"tool_input": {"command": "cat src/services/user_service.py"}}, ["updated_input"]),
        ("gemini", {"tool_name": "run_shell_command", "tool_input": {"command": "cat src/services/user_service.py"}}, ["hookSpecificOutput", "tool_input"]),
    ]
    for agent, payload, path in cases:
        proc = subprocess.run(
            [sys.executable, "-m", "codeward.cli", "hook", "--agent", agent],
            input=json.dumps(payload),
            cwd=sample_repo,
            text=True,
            capture_output=True,
            env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        node = data
        for key in path:
            node = node[key]
        command = node["command"]
        assert command.startswith("CODEWARD_ORIGINAL_COMMAND=")
        assert command.endswith("codeward read src/services/user_service.py")


def test_gemini_hook_no_rewrite_returns_allow(sample_repo):
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "hook", "--agent", "gemini"],
        input=json.dumps({"tool_name": "run_shell_command", "tool_input": {"command": "git branch"}}),
        cwd=sample_repo,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"decision": "allow"}


def test_claude_hook_no_rewrite_and_invalid_json_are_silent_passthrough(sample_repo):
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "hook", "--agent", "claude"],
        input=json.dumps({"tool_input": {"command": "git branch"}}),
        cwd=sample_repo,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
    )
    assert proc.returncode == 0
    assert proc.stdout == ""

    bad = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "hook", "--agent", "claude"],
        input="{not json",
        cwd=sample_repo,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
    )
    assert bad.returncode == 0
    assert bad.stdout == ""
    assert "Invalid hook JSON" in bad.stderr


def test_hook_handles_string_input_and_ignores_non_shell_tools(sample_repo):
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "hook", "--agent", "generic"],
        input=json.dumps({"input": "cat src/services/user_service.py"}),
        cwd=sample_repo,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
    )
    assert proc.returncode == 0
    command = json.loads(proc.stdout)["updatedInput"]["command"]
    assert command.startswith("CODEWARD_ORIGINAL_COMMAND=")
    assert command.endswith("codeward read src/services/user_service.py")

    edit = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "hook", "--agent", "claude"],
        input=json.dumps({"tool_name": "Edit", "tool_input": {"command": "cat src/services/user_service.py"}}),
        cwd=sample_repo,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
    )
    assert edit.returncode == 0
    assert edit.stdout == ""


def test_rewrite_avoids_unsafe_shell_and_flaggy_commands(sample_repo):
    cases = [
        ["run", "--dry-run", "--shell-command", "cat src/services/user_service.py && echo done"],
        ["run", "--dry-run", "--shell-command", "rg --type py User"],
        ["run", "--dry-run", "--shell-command", "rg User src tests"],
        ["run", "--dry-run", "--shell-command", "grep User src/services/user_service.py"],
        ["run", "--dry-run", "--shell-command", "cat src/services/user_service.py src/db.py"],
        ["run", "--dry-run", "--shell-command", "tail -f app.log"],
        ["run", "--dry-run", "--shell-command", "cat README.md"],
        ["run", "--dry-run", "--shell-command", "git diff main...HEAD"],
        ["run", "--dry-run", "--shell-command", "git status -s"],
    ]
    for args in cases:
        result = run_cli(args, sample_repo)
        assert result.returncode == 0
        assert result.stdout.strip() == args[-1]


def test_hook_raw_escape_hatch(sample_repo):
    payload = {"tool_input": {"command": "!raw cat src/services/user_service.py"}}
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "hook"],
        input=json.dumps(payload),
        cwd=sample_repo,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["hookSpecificOutput"]["updatedInput"]["command"] == "cat src/services/user_service.py"


def test_coach_recommends_better_semantic_command(sample_repo):
    result = run_cli(["coach", "cat", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert "Better command:" in result.stdout
    assert "codeward read src/services/user_service.py" in result.stdout


def test_hook_does_not_fight_rtk_or_codeward_wrapped_commands(sample_repo):
    for command in [
        "rtk cat src/services/user_service.py",
        "contextzip cat src/services/user_service.py",
        "codeward read src/services/user_service.py",
    ]:
        proc = subprocess.run(
            [sys.executable, "-m", "codeward.cli", "hook"],
            input=json.dumps({"tool_input": {"command": command}}),
            cwd=sample_repo,
            text=True,
            capture_output=True,
            env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
        )
        assert proc.returncode == 0
        assert proc.stdout == ""


def test_security_review_flags_secret_sql_eval_and_shell(sample_repo):
    target = sample_repo / "src" / "danger.py"
    target.write_text('''
import os
import subprocess

API_KEY = "fake_test_fixture_AAAAAAAAAAAAAAAA"

def find_user(user_id):
    sql = "SELECT * FROM users WHERE id = %s" % user_id
    eval(user_id)
    subprocess.run(user_id, shell=True)
    return sql
''')
    result = run_cli(["review", "src/danger.py", "--security"], sample_repo)
    assert result.returncode == 0
    assert "Security findings:" in result.stdout
    assert "possible hardcoded secret" in result.stdout
    assert "possible SQL injection" in result.stdout
    assert "unsafe eval/exec" in result.stdout
    assert "shell=True command execution" in result.stdout


def test_persistent_index_writes_sqlite_cache(sample_repo):
    result = run_cli(["index"], sample_repo)
    assert result.returncode == 0
    assert "Indexed" in result.stdout
    db = sample_repo / ".codeward" / "index.sqlite"
    assert db.exists()

    import sqlite3
    con = sqlite3.connect(db)
    try:
        count = con.execute("select count(*) from files").fetchone()[0]
        symbols = [r[0] for r in con.execute("select name from symbols order by name")]
    finally:
        con.close()
    assert count >= 5
    assert "UserService" in symbols


def test_sqlite_cache_rebuilds_old_schema_and_persists_metadata(sample_repo):
    db = sample_repo / ".codeward" / "index.sqlite"
    db.parent.mkdir(exist_ok=True)
    import sqlite3
    con = sqlite3.connect(db)
    try:
        con.executescript(
            """
            create table files(path text primary key, lang text not null, lines integer not null, is_test integer not null);
            create table imports(file text not null, name text not null);
            create table symbols(file text not null, name text not null, kind text not null, line integer not null, methods text not null, signature text not null default '', end_line integer not null default 0);
            create table routes(file text not null, route text not null, handler text not null);
            create table side_effects(file text not null, label text not null);
            create table resolved_deps(file text not null, dep text not null);
            insert into files(path, lang, lines, is_test) values ('stale.py', 'Python', 1, 0);
            """
        )
        con.commit()
    finally:
        con.close()
    result = run_cli(["map"], sample_repo)
    assert result.returncode == 0
    con = sqlite3.connect(db)
    try:
        file_cols = {r[1] for r in con.execute("pragma table_info(files)")}
        sym_cols = {r[1] for r in con.execute("pragma table_info(symbols)")}
        assert {"analyzer", "precision", "confidence"} <= file_cols
        assert {"analyzer", "precision", "confidence"} <= sym_cols
        row = con.execute("select analyzer, precision, confidence from symbols where name='UserService'").fetchone()
    finally:
        con.close()
    assert row == ("python_ast", "exact_range", "high")


def test_run_dry_run_rewrites_for_agent_shell_shims(sample_repo):
    result = run_cli(["run", "--dry-run", "--tool", "cat", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert result.stdout.strip() == "codeward read src/services/user_service.py"

    passthrough = run_cli(["run", "--dry-run", "--tool", "git", "branch"], sample_repo)
    assert passthrough.returncode == 0
    assert passthrough.stdout.strip() == "git branch"


def test_init_agent_installs_path_shims_and_agent_instructions(sample_repo):
    result = run_cli(["init-agent"], sample_repo)
    assert result.returncode == 0
    assert "Installed Codeward agent shims" in result.stdout

    shim = sample_repo / ".codeward" / "bin" / "cat"
    assert shim.exists()
    assert shim.stat().st_mode & 0o111
    assert "codeward run --tool cat" in shim.read_text()

    agents = sample_repo / "AGENTS.md"
    text = agents.read_text()
    assert "Codex" in text
    assert "Gemini" in text
    assert "export PATH=\"$PWD/.codeward/bin:$PATH\"" in text
    assert "codeward run --tool" in text


def test_raw_escape_does_not_auto_allow_claude_permissions(sample_repo):
    payload = {"tool_input": {"command": "!raw git diff"}}
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "hook", "--agent", "claude"],
        input=json.dumps(payload),
        cwd=sample_repo,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": str(SRC), "HOME": str(sample_repo)},
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    out = data["hookSpecificOutput"]
    assert out["updatedInput"]["command"] == "git diff"
    assert "permissionDecision" not in out


def test_init_agent_updates_marked_agents_block_without_duplication(sample_repo):
    agents = sample_repo / "AGENTS.md"
    agents.write_text("# Existing\n\n<!-- codeward-shims:start -->\nold\n<!-- codeward-shims:end -->\n")
    result = run_cli(["init-agent", "--bin-dir", ".alt/bin"], sample_repo)
    assert result.returncode == 0
    text = agents.read_text()
    assert text.count("<!-- codeward-shims:start -->") == 1
    assert "old" not in text
    assert "export PATH=\"$PWD/.alt/bin:$PATH\"" in text


def test_security_randomness_only_flags_sensitive_context(sample_repo):
    random_file = sample_repo / "src" / "sampling.py"
    random_file.write_text("import random\ndef pick(xs):\n    return random.choice(xs)\n")
    assert "non-cryptographic randomness" not in run_cli(["review", "src/sampling.py", "--security"], sample_repo).stdout

    token_file = sample_repo / "src" / "token_gen.py"
    token_file.write_text("import random\ndef make_token():\n    return str(random.randint(1, 999999))\n")
    assert "non-cryptographic randomness" in run_cli(["review", "src/token_gen.py", "--security"], sample_repo).stdout


def test_dependents_avoid_bare_stem_false_positives(sample_repo):
    unrelated = sample_repo / "src" / "notes.py"
    unrelated.write_text('''
def explain():
    return "this db word is documentation only"
''')
    result = run_cli(["read", "src/db.py"], sample_repo)
    assert result.returncode == 0
    assert "src/services/user_service.py" in result.stdout
    assert "src/notes.py" not in result.stdout


def test_test_summary_ignores_okay_noise(sample_repo):
    script = sample_repo / "noisy_ok.py"
    script.write_text('''
print("checking npm okay")
print("1 passed in 0.01s")
''')
    result = run_cli(["test", "--force", sys.executable, str(script)], sample_repo)
    assert result.returncode == 0
    assert "1 passed in 0.01s" in result.stdout
    assert "checking npm okay" not in result.stdout


def test_status_defers_to_rtk_when_installed(sample_repo, tmp_path):
    """When RTK is on PATH, codeward status/diff/test should defer to RTK
    rather than re-implementing the same compression."""
    # Drop a fake `rtk` on PATH so the deferral path fires regardless of host env.
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir(exist_ok=True)
    rtk = fake_bin / "rtk"
    rtk.write_text("#!/usr/bin/env bash\necho 'rtk fake'\n")
    rtk.chmod(0o755)
    cmd = [sys.executable, "-m", "codeward.cli", "status"]
    env = {"PYTHONPATH": str(SRC), "PATH": f"{fake_bin}:/usr/bin:/bin"}
    result = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert result.returncode == 0
    assert "deferring to RTK" in result.stdout, result.stdout
    forced = subprocess.run(
        [*cmd[:-1], "status", "--force"], cwd=sample_repo, text=True, capture_output=True, env=env,
    )
    assert forced.returncode == 0
    assert "deferring to RTK" not in forced.stdout


def test_savings_command_reports_estimated_token_reduction(sample_repo):
    result = run_cli(["savings", "--no-history", "--command", "cat src/services/user_service.py", "--command", "find . -maxdepth 3 -type f"], sample_repo)
    assert result.returncode == 0
    assert "Codeward savings analysis" in result.stdout
    assert "Total saved:" in result.stdout
    assert "cat src/services/user_service.py" in result.stdout
    assert "codeward read src/services/user_service.py" in result.stdout


def test_hook_rewritten_commands_record_token_savings(sample_repo):
    env = {"PYTHONPATH": str(SRC), "CODEWARD_ORIGINAL_COMMAND": "cat src/services/user_service.py", "HOME": str(sample_repo)}
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "read", "src/services/user_service.py"],
        cwd=sample_repo,
        text=True,
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0
    result = run_cli(["gain"], sample_repo)
    # New format: shows "1 commands tracked" + per-row "original → rewrite" + raw/cs/saved
    assert "1 commands tracked" in result.stdout
    assert "cat src/services/user_service.py" in result.stdout
    assert "codeward read src/services/user_service.py" in result.stdout
    assert "raw" in result.stdout and "saved" in result.stdout


def test_init_default_writes_agents_md_and_does_not_touch_settings(sample_repo, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    result = run_cli(["init"], sample_repo)
    assert result.returncode == 0
    agents = (sample_repo / "AGENTS.md").read_text()
    assert "codeward-semantic:start" in agents
    assert "codeward read <file>" in agents
    assert not (sample_repo / ".claude").exists()
    assert not (fake_home / ".claude" / "settings.json").exists()


def test_init_hook_global_orders_before_rtk_and_is_idempotent(sample_repo, tmp_path):
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    settings = fake_home / ".claude" / "settings.json"
    settings.write_text(json.dumps({
        "model": "opus",
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}
        ]}
    }))
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home)}
    cmd = [sys.executable, "-m", "codeward.cli", "init", "--hook", "--global"]
    first = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert first.returncode == 0
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    pre = data["hooks"]["PreToolUse"]
    # First entry must be codeward (Bash matcher, ordered before rtk)
    assert "codeward" in pre[0]["hooks"][0]["command"]
    assert pre[0]["matcher"] == "Bash"
    # Second is the existing rtk entry (Bash matcher)
    assert "rtk" in pre[1]["hooks"][0]["command"]
    # Third is the codeward Edit/Write preflight hook (different matcher, no clash)
    edit_entries = [e for e in pre if e.get("matcher") == "Edit|Write|MultiEdit"]
    assert len(edit_entries) == 1
    assert "codeward" in edit_entries[0]["hooks"][0]["command"]
    # Idempotent: re-running should not duplicate codeward entries
    second = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert second.returncode == 0
    data2 = json.loads(settings.read_text())
    pre2 = data2["hooks"]["PreToolUse"]
    cw_bash = [e for e in pre2 if e.get("matcher") == "Bash" and any("codeward" in h["command"] for h in e["hooks"])]
    cw_edit = [e for e in pre2 if e.get("matcher") == "Edit|Write|MultiEdit" and any("codeward" in h["command"] for h in e["hooks"])]
    assert len(cw_bash) == 1, f"expected 1 codeward Bash entry, got {len(cw_bash)}"
    assert len(cw_edit) == 1, f"expected 1 codeward Edit/Write entry, got {len(cw_edit)}"


def test_init_hook_no_bash_installs_only_preflight(sample_repo, tmp_path):
    """`init --hook --no-hook-bash` should install only the Edit/Write preflight
    matcher, leaving Bash to RTK. Common setup for users who already run RTK."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home)}
    cmd = [sys.executable, "-m", "codeward.cli", "init", "--hook", "--no-hook-bash"]
    result = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert result.returncode == 0
    settings = json.loads((sample_repo / ".claude" / "settings.local.json").read_text())
    pre = settings["hooks"]["PreToolUse"]
    matchers = {e.get("matcher") for e in pre}
    assert "Edit|Write|MultiEdit" in matchers
    assert "Bash" not in matchers, f"Bash matcher should not be present; got {matchers}"


def test_init_hook_no_edit_installs_only_bash(sample_repo, tmp_path):
    """`init --hook --no-hook-edit` (existing) installs only the Bash rewrite hook."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home)}
    cmd = [sys.executable, "-m", "codeward.cli", "init", "--hook", "--no-hook-edit"]
    result = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert result.returncode == 0
    settings = json.loads((sample_repo / ".claude" / "settings.local.json").read_text())
    pre = settings["hooks"]["PreToolUse"]
    matchers = {e.get("matcher") for e in pre}
    assert "Bash" in matchers
    assert "Edit|Write|MultiEdit" not in matchers


def test_init_hook_gemini_writes_before_tool_entry(sample_repo, tmp_path):
    """`init --hook --gemini` writes a BeforeTool/run_shell_command entry to
    ~/.gemini/settings.json so Gemini CLI invokes `codeward hook --agent gemini`
    on every shell call. Idempotent on re-run."""
    fake_home = tmp_path / "home"
    (fake_home / ".gemini").mkdir(parents=True)
    # Pre-populate settings.json with unrelated existing config — must be preserved
    (fake_home / ".gemini" / "settings.json").write_text(json.dumps({
        "general": {"previewFeatures": True},
        "security": {"auth": {"selectedType": "oauth-personal"}},
    }))
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home)}
    cmd = [sys.executable, "-m", "codeward.cli", "init", "--hook", "--gemini"]
    result = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert result.returncode == 0
    settings = json.loads((fake_home / ".gemini" / "settings.json").read_text())
    # Existing config preserved
    assert settings["general"]["previewFeatures"] is True
    assert settings["security"]["auth"]["selectedType"] == "oauth-personal"
    # Hook section added
    before = settings["hooks"]["BeforeTool"]
    assert any(
        e.get("matcher") == "run_shell_command"
        and any("codeward" in (h.get("command") or "") for h in (e.get("hooks") or []))
        for e in before
    ), settings
    # Idempotent on re-run
    second = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert second.returncode == 0
    settings2 = json.loads((fake_home / ".gemini" / "settings.json").read_text())
    cs_entries = [
        e for e in settings2["hooks"]["BeforeTool"]
        if e.get("matcher") == "run_shell_command"
        and any("codeward" in (h.get("command") or "") for h in (e.get("hooks") or []))
    ]
    assert len(cs_entries) == 1, f"expected exactly 1 codeward entry, got {len(cs_entries)}"


def test_init_gemini_skipped_when_dir_missing(sample_repo, tmp_path):
    """If ~/.gemini doesn't exist (Gemini CLI not installed), skip with a notice."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home)}
    cmd = [sys.executable, "-m", "codeward.cli", "init", "--hook", "--gemini"]
    result = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert result.returncode == 0
    assert "Skipped Gemini hook" in result.stdout
    assert not (fake_home / ".gemini").exists()


def test_init_hook_both_skip_flags_errors(sample_repo, tmp_path):
    """Setting both --no-hook-bash and --no-hook-edit would install nothing —
    error out instead of silently doing nothing useful."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home)}
    cmd = [sys.executable, "-m", "codeward.cli", "init", "--hook", "--no-hook-bash", "--no-hook-edit"]
    result = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert result.returncode == 2
    assert "cannot both be set" in result.stderr


def test_init_agent_refuses_when_rtk_present(sample_repo, tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rtk_stub = fake_bin / "rtk"
    rtk_stub.write_text("#!/usr/bin/env bash\necho rtk-stub\n")
    rtk_stub.chmod(0o755)
    env = {"PYTHONPATH": str(SRC), "PATH": f"{fake_bin}:/usr/bin:/bin"}
    refused = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "init-agent"],
        cwd=sample_repo, text=True, capture_output=True, env=env,
    )
    assert refused.returncode == 1
    assert "RTK is active" in refused.stderr
    forced = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "init-agent", "--force"],
        cwd=sample_repo, text=True, capture_output=True, env=env,
    )
    assert forced.returncode == 0
    assert (sample_repo / ".codeward" / "bin" / "cat").exists()


def test_doctor_reports_rtk_and_hook_position(sample_repo, tmp_path):
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    settings = fake_home / ".claude" / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "codeward hook --agent claude"}]},
        ]}
    }))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rtk_stub = fake_bin / "rtk"
    rtk_stub.write_text("#!/usr/bin/env bash\necho 'rtk 1.0.0'\n")
    rtk_stub.chmod(0o755)
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home), "PATH": f"{fake_bin}:/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "doctor"],
        cwd=sample_repo, text=True, capture_output=True, env=env,
    )
    assert "RTK: present" in result.stdout
    assert "rtk runs first" in result.stdout
    assert result.returncode == 1


def test_security_findings_skip_comments_and_docstrings(sample_repo):
    target = sample_repo / "src" / "documented.py"
    target.write_text('''"""
This module is for examples.
Do not commit real values like API_KEY = "do_not_use_this_real_key_123".
"""

# password = "hunter2hunter2hunter2"

def safe():
    return None
''')
    result = run_cli(["review", "src/documented.py", "--security"], sample_repo)
    assert result.returncode == 0
    assert "possible hardcoded secret" not in result.stdout


def test_estimate_zero_for_missing_file_does_not_record(sample_repo):
    env = {"PYTHONPATH": str(SRC), "CODEWARD_ORIGINAL_COMMAND": "cat src/nonexistent_file.py", "HOME": str(sample_repo)}
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "read", "src/services/user_service.py"],
        cwd=sample_repo, text=True, capture_output=True, env=env,
    )
    assert proc.returncode == 0
    history = sample_repo / ".codeward" / "history.jsonl"
    assert not history.exists() or "nonexistent_file" not in history.read_text()


def test_index_uses_sqlite_cache_when_fresh(sample_repo):
    db = sample_repo / ".codeward" / "index.sqlite"
    run_cli(["map"], sample_repo)
    assert db.exists()
    first_mtime = db.stat().st_mtime
    import time as _t
    _t.sleep(1.1)
    run_cli(["map"], sample_repo)
    assert db.stat().st_mtime == first_mtime, "fresh cache should not have been rewritten"
    (sample_repo / "src" / "new_file.py").write_text("def fresh(): pass\n")
    _t.sleep(1.1)
    run_cli(["map"], sample_repo)
    assert db.stat().st_mtime > first_mtime, "cache should rebuild after source change"


def test_callgraph_resolves_lowercase_instance_assignments(sample_repo):
    (sample_repo / "src" / "controllers" / "lower_controller.py").write_text('''
from src.services.user_service import UserService

def lower_controller(request):
    svc = UserService()
    return svc.create_user(request["email"])
''')
    (sample_repo / "src" / "routes" / "lower_routes.py").write_text('''
from src.controllers.lower_controller import lower_controller

ROUTES = {"POST /api/lower": lower_controller}
''')
    result = run_cli(["callgraph", "POST /api/lower"], sample_repo)
    assert result.returncode == 0
    assert "UserService.create_user" in result.stdout
    assert "(inferred)" in result.stdout


def test_cache_invalidates_on_file_deletion(sample_repo):
    db = sample_repo / ".codeward" / "index.sqlite"
    run_cli(["map"], sample_repo)
    assert db.exists()
    first_mtime = db.stat().st_mtime
    import time as _t
    _t.sleep(1.1)
    (sample_repo / "src" / "emailer.py").unlink()
    run_cli(["map"], sample_repo)
    assert db.stat().st_mtime > first_mtime, "cache should rebuild after a file is deleted"


def test_init_hook_refuses_to_clobber_malformed_settings(sample_repo, tmp_path):
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    settings = fake_home / ".claude" / "settings.json"
    settings.write_text("{not valid json")
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home)}
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "init", "--hook", "--global"],
        cwd=sample_repo, text=True, capture_output=True, env=env,
    )
    assert proc.returncode != 0
    assert "malformed" in (proc.stderr + proc.stdout).lower()
    backup = settings.with_suffix(settings.suffix + ".broken")
    assert backup.exists(), "malformed settings should be backed up before refusing"
    assert settings.read_text() == "{not valid json", "original malformed file should be untouched"


def test_init_hook_detects_absolute_path_rtk(sample_repo, tmp_path):
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    settings = fake_home / ".claude" / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/rtk hook claude"}]}
        ]}
    }))
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home)}
    proc = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "init", "--hook", "--global"],
        cwd=sample_repo, text=True, capture_output=True, env=env,
    )
    assert proc.returncode == 0
    pre = json.loads(settings.read_text())["hooks"]["PreToolUse"]
    assert "codeward" in pre[0]["hooks"][0]["command"], f"codeward should be first; got: {pre}"
    assert "/usr/local/bin/rtk" in pre[1]["hooks"][0]["command"]


def test_dependents_word_boundary_skips_string_mentions(sample_repo):
    (sample_repo / "src" / "prose.py").write_text('''
def doc():
    return "see src.db for the data layer"
''')
    result = run_cli(["read", "src/db.py"], sample_repo)
    assert result.returncode == 0
    assert "src/services/user_service.py" in result.stdout
    assert "src/prose.py" not in result.stdout


def test_production_testing_module_is_not_classified_as_test(tmp_path):
    """Regression: src/click/testing.py must NOT be flagged as a test file
    just because filename contains 'test'/'testing'."""
    (tmp_path / "src" / "click").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "click" / "testing.py").write_text("class CliRunner:\n    def invoke(self): pass\n")
    (tmp_path / "src" / "click" / "core.py").write_text("class Command:\n    pass\n")
    (tmp_path / "tests" / "test_real.py").write_text("def test_x(): assert True\n")
    result = run_cli(["map"], tmp_path)
    assert result.returncode == 0
    # tests dir contributes 1 test; src/click/testing.py is production code
    assert "1 tests" in result.stdout, result.stdout
    # Reading testing.py should not call it 'test coverage'
    read = run_cli(["read", "src/click/testing.py"], tmp_path)
    assert read.returncode == 0
    assert "Role: test coverage" not in read.stdout, read.stdout


def test_rust_files_are_not_all_classified_as_tests(tmp_path):
    """Regression: TEST_PATTERNS used to include '*.rs', flagging every Rust
    file as a test. Now only `*_test.rs` should match."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() { println!(\"hi\"); }\n")
    (tmp_path / "src" / "auth_test.rs").write_text("#[test] fn t() {}\n")
    result = run_cli(["map"], tmp_path)
    assert result.returncode == 0
    # 2 indexed code files total, of which 1 (auth_test.rs) is a test.
    # Before the fix, all .rs files matched TEST_PATTERNS so this would have
    # said "2 tests" instead of "1 tests".
    assert "1 tests" in result.stdout, result.stdout
    assert "2 tests" not in result.stdout, result.stdout


def test_parser_module_does_not_get_db_or_network_side_effects(tmp_path):
    """Regression from click benchmark: parser.py used to be tagged with
    'DB write' (because of args.insert) and 'Network call' (because of _fetch).
    Both are false positives — they're list operations and a local helper."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "parser.py").write_text('''
from collections import deque

def _fetch(c: deque):
    return c.popleft() if c else None

def parse(args: list[str]) -> list[str]:
    rargs: list[str] = []
    for arg in args:
        rargs.insert(0, arg)
        if arg == "--":
            break
    return rargs
''')
    result = run_cli(["read", "src/parser.py"], tmp_path)
    assert result.returncode == 0
    assert "Side effects" not in result.stdout, result.stdout
    assert "DB write" not in result.stdout
    assert "Network call" not in result.stdout


def test_codeward_branding_in_summary_outputs(sample_repo):
    """The compact output must be clearly attributed to Codeward so
    agents do not mistake it for RTK output."""
    map_out = run_cli(["map"], sample_repo)
    assert "# Codeward semantic summary" in map_out.stdout
    read_out = run_cli(["read", "src/services/user_service.py"], sample_repo)
    assert "# Codeward semantic summary" in read_out.stdout
    sym_out = run_cli(["symbol", "UserService"], sample_repo)
    assert "# Codeward semantic summary" in sym_out.stdout


def test_symbol_signatures_include_args_and_return_type(sample_repo):
    """Symbol output should include parsed signatures (args + return annotation)
    so agents do not need to fetch source for the call shape."""
    result = run_cli(["read", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert "def create_user(self, email: str) -> dict" in result.stdout, result.stdout
    assert "def delete_user(self, user_id: str) -> None" in result.stdout, result.stdout


def test_json_output_for_map_read_symbol(sample_repo):
    """--json must produce machine-parseable output with a stable schema."""
    map_r = run_cli(["map", "--json"], sample_repo)
    map_d = json.loads(map_r.stdout)
    assert map_d["command"] == "map"
    assert "primary_language" in map_d and "important_files" in map_d
    assert all("path" in f and "lang" in f for f in map_d["important_files"])

    read_r = run_cli(["read", "--json", "src/services/user_service.py"], sample_repo)
    read_d = json.loads(read_r.stdout)
    assert read_d["command"] == "read"
    assert read_d["file"] == "src/services/user_service.py"
    assert "symbols" in read_d and "dependents" in read_d
    user_sym = next((s for s in read_d["symbols"] if s["name"] == "UserService"), None)
    assert user_sym is not None and user_sym["kind"] == "class"
    assert any(m["name"] == "create_user" for m in user_sym.get("methods", []))

    sym_r = run_cli(["symbol", "--json", "UserService"], sample_repo)
    sym_d = json.loads(sym_r.stdout)
    assert sym_d["command"] == "symbol"
    assert len(sym_d["definitions"]) >= 1
    assert sym_d["definitions"][0]["signature"].startswith("class UserService")


def test_json_output_for_search_and_callgraph(sample_repo):
    s = json.loads(run_cli(["search", "--json", "UserService"], sample_repo).stdout)
    assert s["command"] == "search"
    assert s["query"] == "UserService"
    assert s["total_matches"] >= 1
    assert all("file" in f and "matches" in f for f in s["files"])

    cg = json.loads(run_cli(["callgraph", "--json", "POST /api/users"], sample_repo).stdout)
    assert cg["command"] == "callgraph"
    assert "chain" in cg and "side_effects" in cg


def test_config_toml_extra_test_dirs_classifies_files(tmp_path):
    """A repo can declare extra test directories via .codeward/config.toml.
    Files under those dirs should be counted as tests."""
    (tmp_path / "src").mkdir()
    (tmp_path / "e2e").mkdir()
    (tmp_path / "src" / "app.py").write_text("def f(): pass\n")
    (tmp_path / "e2e" / "scenario.py").write_text("def s(): pass\n")
    (tmp_path / ".codeward").mkdir()
    (tmp_path / ".codeward" / "config.toml").write_text(
        '[index]\nextra_test_dirs = ["e2e"]\n'
    )
    result = run_cli(["map"], tmp_path)
    assert result.returncode == 0
    # Without config, e2e/ would be source. With config, it's classified test.
    assert "1 tests" in result.stdout, result.stdout


def test_config_toml_custom_side_effect_rule_fires(tmp_path):
    """Custom rules in config let a repo flag domain-specific side effects."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "billing.py").write_text(
        "def charge_card(amount):\n    billing.charge(amount)\n    return True\n"
    )
    (tmp_path / ".codeward").mkdir()
    (tmp_path / ".codeward" / "config.toml").write_text(
        '[[side_effects.custom_rules]]\n'
        'pattern = "\\\\bbilling\\\\.charge\\\\s*\\\\("\n'
        'label = "Billing event"\n'
    )
    result = run_cli(["read", "src/billing.py"], tmp_path)
    assert result.returncode == 0
    assert "Billing event" in result.stdout, result.stdout


def test_config_toml_malformed_reported_by_doctor(tmp_path):
    """Doctor must surface malformed config rather than silently ignoring it."""
    (tmp_path / ".codeward").mkdir()
    (tmp_path / ".codeward" / "config.toml").write_text("this is not valid toml = =\n")
    result = run_cli(["doctor"], tmp_path)
    assert "malformed" in result.stdout.lower(), result.stdout
    assert result.returncode != 0


def test_treesitter_extracts_go_symbols(tmp_path):
    """When tree-sitter is installed, Go symbols get accurate end_lines and
    method linkage. Skip gracefully when the optional dep isn't available."""
    pytest.importorskip("tree_sitter_go")
    (tmp_path / "server.go").write_text(
        "package main\n"
        "type Server struct { Port int }\n"
        "func NewServer(p int) *Server { return &Server{p} }\n"
        "func (s *Server) Start() error {\n"
        "    return nil\n"
        "}\n"
    )
    result = run_cli(["read", "--json", "server.go"], tmp_path)
    assert result.returncode == 0
    d = json.loads(result.stdout)
    names = {s["name"] for s in d["symbols"]}
    assert "Server" in names and "NewServer" in names
    server = next(s for s in d["symbols"] if s["name"] == "Server")
    # Method linkage: Server.Start should be attached to the Server class
    method_names = [m if isinstance(m, str) else m.get("name") for m in server.get("methods", [])]
    assert "Start" in method_names, f"Server methods: {server.get('methods')}"


def test_treesitter_extracts_typescript_class_methods(tmp_path):
    pytest.importorskip("tree_sitter_typescript")
    (tmp_path / "console.ts").write_text(
        'export class Console {\n'
        '  print(text: string): void { this.write(text); }\n'
        '  private write(s: string): void {}\n'
        '}\n'
        'export interface Logger { log(msg: string): void; }\n'
    )
    result = run_cli(["read", "--json", "console.ts"], tmp_path)
    d = json.loads(result.stdout)
    console = next(s for s in d["symbols"] if s["name"] == "Console")
    assert console["kind"] == "class"
    assert console["end_line"] >= 4
    method_names = {m["name"] if isinstance(m, dict) else m for m in console.get("methods", [])}
    assert {"print", "write"} <= method_names


@pytest.mark.parametrize(
    "module_name,filename,source,symbol,ref_text",
    [
        ("tree_sitter_go", "main.go", "package main\ntype Server struct{}\nfunc (s *Server) Start() {}\nfunc main() { s := &Server{}; s.Start() }\n", "Server.Start", "s.Start()"),
        ("tree_sitter_rust", "lib.rs", "struct Server;\nimpl Server { fn start(&self) {} }\nfn main() { let s = Server; s.start(); }\n", "Server.start", "s.start()"),
        ("tree_sitter_typescript", "app.ts", "class Server { start(): void {} }\nconst s = new Server();\ns.start();\n", "Server.start", "s.start()"),
        ("tree_sitter_javascript", "app.js", "class Server { start() {} }\nconst s = new Server();\ns.start();\n", "Server.start", "s.start()"),
        ("tree_sitter_java", "Server.java", "class Server { void start() {} void run() { start(); } }\n", "Server.start", "start()"),
        ("tree_sitter_ruby", "server.rb", "class Server\n  def start\n  end\nend\nServer.new.start\n", "Server.start", "start"),
        ("tree_sitter_php", "server.php", "<?php class Server { function start() {} } $s = new Server(); $s->start();\n", "Server.start", "start"),
        ("tree_sitter_c_sharp", "Server.cs", "class Server { void Start() {} void Run() { Start(); } }\n", "Server.Start", "Start()"),
    ],
)
def test_treesitter_refs_are_syntax_aware(tmp_path, module_name, filename, source, symbol, ref_text):
    pytest.importorskip(module_name)
    (tmp_path / filename).write_text(source)
    read = json.loads(run_cli(["read", "--json", filename], tmp_path).stdout)
    assert read["analyzer"] == "tree_sitter"
    assert read["symbols"], read
    refs = json.loads(run_cli(["refs", "--json", symbol], tmp_path).stdout)
    assert any(ref_text in r["text"] for r in refs["references"]), refs
    assert any(r["precision"] == "syntax_aware" for r in refs["references"]), refs


def test_slice_returns_exact_method_body(sample_repo):
    """codeward slice <Class.method> replaces sed -n 'X,Yp' for known symbols."""
    result = run_cli(["slice", "UserService.create_user"], sample_repo)
    assert result.returncode == 0
    assert "def create_user(self, email: str) -> dict" in result.stdout
    assert "db.users.create" in result.stdout, result.stdout
    # Should NOT include the unrelated delete_user method
    assert "delete_user" not in result.stdout or result.stdout.count("delete_user") < result.stdout.count("create_user")


def test_slice_disambiguates_class_vs_method(sample_repo):
    """Bug regression: find_symbol used to return the class first because
    'create_user' is in its methods list. Now exact-name matches win."""
    # Look up the bare method name
    result = run_cli(["slice", "create_user"], sample_repo)
    assert result.returncode == 0
    # Body of create_user, not the entire UserService class
    assert "db.users.create" in result.stdout
    # The body should be small (one method) not the whole class
    body_lines = result.stdout.count("\n")
    assert body_lines < 15, f"Sliced too much: {body_lines} lines"


def test_slice_signature_only(sample_repo):
    result = run_cli(["slice", "UserService.create_user", "--signature-only"], sample_repo)
    assert result.returncode == 0
    assert "def create_user(self, email: str) -> dict" in result.stdout
    assert "db.users.create" not in result.stdout


def test_slice_unknown_symbol_returns_error(sample_repo):
    result = run_cli(["slice", "DoesNotExist"], sample_repo)
    assert result.returncode == 2
    assert "Symbol not found" in result.stderr


def test_refs_lists_callsites_excluding_definition(sample_repo):
    result = run_cli(["refs", "UserService"], sample_repo)
    assert result.returncode == 0
    assert "src/controllers/user_controller.py" in result.stdout
    # By default the definition site is excluded
    j = json.loads(run_cli(["refs", "--json", "UserService"], sample_repo).stdout)
    assert j["total"] >= 1
    def_files = {(d["file"], d["line"]) for d in j["definitions"]}
    for r in j["references"]:
        assert (r["file"], r["line"]) not in def_files
        assert {"analyzer", "precision", "confidence"} <= set(r)


def test_python_ast_refs_cover_methods_aliases_attributes_and_shadowing(sample_repo):
    (sample_repo / "src" / "controllers" / "alias_controller.py").write_text('''
from src.emailer import send_welcome_email as send_welcome
from src.services.user_service import UserService

def alias_controller(request):
    svc = UserService()
    send_welcome(request["email"])
    return svc.create_user(request["email"])

def shadow(UserService):
    return UserService
''')
    user_refs = json.loads(run_cli(["refs", "--json", "UserService"], sample_repo).stdout)
    ref_text = "\n".join(r["text"] for r in user_refs["references"])
    assert "svc = UserService()" in ref_text
    assert not any(r["file"].endswith("alias_controller.py") and r["text"] == "return UserService" for r in user_refs["references"])
    assert all(r["analyzer"] == "python_ast" for r in user_refs["references"] if r["file"].endswith(".py"))

    method_refs = json.loads(run_cli(["refs", "--json", "UserService.create_user"], sample_repo).stdout)
    assert any("svc.create_user" in r["text"] for r in method_refs["references"])

    alias_refs = json.loads(run_cli(["refs", "--json", "send_welcome_email"], sample_repo).stdout)
    assert any("send_welcome(" in r["text"] for r in alias_refs["references"])


def test_json_precision_metadata_for_read_symbol_callgraph_preflight_review(sample_repo):
    read_d = json.loads(run_cli(["read", "--json", "src/services/user_service.py"], sample_repo).stdout)
    assert read_d["analyzer"] == "python_ast"
    assert read_d["precision"] == "exact_range"
    assert read_d["confidence"] == "high"
    assert read_d["symbols"][0]["analyzer"] == "python_ast"

    sym_d = json.loads(run_cli(["symbol", "--json", "UserService"], sample_repo).stdout)
    assert sym_d["definitions"][0]["precision"] == "exact_range"

    cg_d = json.loads(run_cli(["callgraph", "--json", "POST /api/users"], sample_repo).stdout)
    assert any(step.get("analyzer") == "python_ast" for step in cg_d["chain"] if step.get("callee") == "UserService.create_user")

    pre_d = json.loads(run_cli(["preflight", "--json", "src/services/user_service.py"], sample_repo).stdout)
    assert pre_d["analyzer"] == "python_ast"

    review_d = json.loads(run_cli(["review", "--json", "src/services/user_service.py"], sample_repo).stdout)
    assert "symbols" in review_d["files"][0]


def test_blame_aggregates_authors(sample_repo):
    """Blame shells out to git blame on the symbol's range."""
    result = run_cli(["blame", "UserService.create_user"], sample_repo)
    assert result.returncode == 0
    assert "Authors:" in result.stdout
    assert "100.0%" in result.stdout  # only one committer in the fixture


def test_sdiff_detects_added_symbol(sample_repo):
    """Adding a new function must appear as `+` in semantic diff output."""
    extra = sample_repo / "src" / "services" / "user_service.py"
    extra.write_text(extra.read_text() + "\n\ndef brand_new_helper(x: int) -> int:\n    return x + 1\n")
    result = run_cli(["sdiff", "--json", "--base", "HEAD"], sample_repo)
    assert result.returncode == 0
    d = json.loads(result.stdout)
    assert d["files"], f"expected at least one file with changes; got: {d}"
    file_row = next((f for f in d["files"] if f["file"].endswith("user_service.py")), None)
    assert file_row is not None
    added_names = {a["name"] for a in file_row["added"]}
    assert "brand_new_helper" in added_names


def test_watch_reindex_updates_sqlite_on_file_event(sample_repo):
    """Test the core reindex helper directly. The watchdog observer itself
    runs in a thread and isn't suitable for synchronous tests, but the
    `_reindex_paths` helper can be exercised in-process."""
    sys.path.insert(0, str(SRC))
    try:
        from codeward.index import RepoIndex
        from codeward.watch import _reindex_paths
        idx = RepoIndex(sample_repo)
        target = sample_repo / "src" / "services" / "user_service.py"
        # Add a brand-new symbol the initial index won't know about
        target.write_text(target.read_text() + "\n\ndef brand_new_helper():\n    return 1\n")
        n = _reindex_paths(idx, sample_repo, {str(target)})
        assert n == 1, "expected 1 file to be reindexed"
        # The in-memory index must now know about the new symbol
        names = {s.name for s in idx.files["src/services/user_service.py"].symbols}
        assert "brand_new_helper" in names, names
    finally:
        sys.path.remove(str(SRC))


def test_preflight_emits_blast_radius_and_dependents(sample_repo):
    """Preflight is the data the Edit/Write hook injects before changing a file."""
    result = run_cli(["preflight", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert "blast_radius=" in result.stdout
    assert "src/controllers/user_controller.py" in result.stdout
    j = json.loads(run_cli(["preflight", "--json", "src/services/user_service.py"], sample_repo).stdout)
    assert j["command"] == "preflight"
    assert j["blast_radius"] in {"LOW", "MEDIUM", "HIGH"}
    assert isinstance(j["dependents"], list) and j["dependents"]


def test_edit_tool_hook_returns_preflight_context(sample_repo):
    """Simulate Claude Code's Edit tool payload. Hook should return
    additionalContext containing the preflight summary."""
    payload = json.dumps({
        "tool_name": "Edit",
        "tool_input": {"file_path": str(sample_repo / "src" / "services" / "user_service.py")},
    })
    cmd = [sys.executable, "-m", "codeward.cli", "hook", "--agent", "claude"]
    env = {"PYTHONPATH": str(SRC), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    result = subprocess.run(cmd, input=payload, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert result.returncode == 0
    response = json.loads(result.stdout)
    spec = response.get("hookSpecificOutput", {})
    assert "additionalContext" in spec, response
    assert "preflight" in spec["additionalContext"].lower()
    # Bash matcher path still works (sanity check)
    bash_payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat README.md"}})
    bash_result = subprocess.run(cmd, input=bash_payload, cwd=sample_repo, text=True, capture_output=True, env=env)
    if bash_result.stdout.strip():
        bash_resp = json.loads(bash_result.stdout)
        assert "additionalContext" not in bash_resp.get("hookSpecificOutput", {})


def test_api_emits_only_public_top_level_symbols(sample_repo):
    """`api` skips test files, methods, and underscore-prefixed names."""
    result = run_cli(["api", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert "UserService" in result.stdout
    # methods listed under the class but not as top-level entries
    assert result.stdout.count("UserService") >= 1
    # underscore-prefixed should be filtered (none in this fixture, but the path runs)
    j = json.loads(run_cli(["api", "--json", "src/services/user_service.py"], sample_repo).stdout)
    assert j["files"]
    for s in j["files"][0]["symbols"]:
        assert not s["name"].split(".")[-1].startswith("_")


def test_init_writes_both_claude_md_and_agents_md(tmp_path):
    """Claude Code auto-discovers CLAUDE.md, while Codex/Cursor read AGENTS.md.
    Default `init` must write to BOTH so all agents see the semantic vocabulary."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("def f(): pass\n")
    result = run_cli(["init"], tmp_path)
    assert result.returncode == 0
    claude_md = (tmp_path / "CLAUDE.md").read_text()
    agents_md = (tmp_path / "AGENTS.md").read_text()
    for content in (claude_md, agents_md):
        assert "codeward-semantic:start" in content
        assert "codeward map" in content
        assert "codeward read" in content


def test_init_global_writes_to_each_agent_memory_file(tmp_path):
    """`init --global` writes the semantic vocabulary to ~/.claude/CLAUDE.md,
    ~/.codex/AGENTS.md, ~/.gemini/GEMINI.md when those agent dirs exist.
    Tested by faking $HOME so we don't pollute the real one."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".codex").mkdir(parents=True)
    # Intentionally skip ~/.gemini to test the "skip when missing" branch
    (tmp_path / "repo").mkdir()
    cmd = [sys.executable, "-m", "codeward.cli", "init", "--global"]
    env = {"PYTHONPATH": str(SRC), "HOME": str(fake_home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    result = subprocess.run(cmd, cwd=tmp_path / "repo", text=True, capture_output=True, env=env)
    assert result.returncode == 0
    # Per-agent global memory files should exist where the dir was present
    claude_global = (fake_home / ".claude" / "CLAUDE.md").read_text()
    codex_global = (fake_home / ".codex" / "AGENTS.md").read_text()
    assert "codeward-semantic:start" in claude_global
    assert "codeward map" in claude_global
    assert "codeward read" in codex_global
    # Gemini dir didn't exist → file shouldn't be created
    assert not (fake_home / ".gemini").exists()
    # Stdout should mention which dirs were written and which were skipped
    assert "Claude Code" in result.stdout
    assert "Gemini CLI" in result.stdout  # listed under Skipped
    # Project-local files should also be written (default behavior preserved)
    assert (tmp_path / "repo" / "CLAUDE.md").exists()
    assert (tmp_path / "repo" / "AGENTS.md").exists()


def test_direct_read_invocation_records_savings(sample_repo):
    """Pure CLAUDE.md / Codex AGENTS.md mode: agent calls `codeward read foo`
    directly (no hook env var). Tracking must still record raw vs. output diff."""
    # Make the target file big enough that the summary is smaller than raw.
    big = sample_repo / "src" / "services" / "user_service.py"
    big.write_text(big.read_text() + "\n# padding\n" * 400)
    history = sample_repo / ".codeward" / "history.jsonl"
    if history.exists():
        history.unlink()
    result = run_cli(["read", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert history.exists()
    rows = [json.loads(l) for l in history.read_text().splitlines()]
    direct = [r for r in rows if r["command"].startswith("direct:")]
    assert direct, f"expected a direct-invocation row; got: {rows}"
    assert direct[-1]["raw_tokens"] > 0
    assert direct[-1]["saved_tokens"] >= 0  # may be zero on tiny files; always non-negative


def test_hook_invocation_still_recorded_with_env_var(sample_repo):
    """Hook / PATH-shim path: CODEWARD_ORIGINAL_COMMAND env var carries the
    raw command so we can size it exactly. Must record under 'hook:' prefix."""
    history = sample_repo / ".codeward" / "history.jsonl"
    if history.exists():
        history.unlink()
    cmd = [sys.executable, "-m", "codeward.cli", "read", "src/services/user_service.py"]
    env = {"PYTHONPATH": str(SRC), "CODEWARD_ORIGINAL_COMMAND": "cat src/services/user_service.py", "HOME": str(sample_repo)}
    result = subprocess.run(cmd, cwd=sample_repo, text=True, capture_output=True, env=env)
    assert result.returncode == 0
    rows = [json.loads(l) for l in history.read_text().splitlines()]
    hook = [r for r in rows if r["command"].startswith("hook:")]
    assert hook, f"expected a hook-invocation row; got: {rows}"


def test_read_flow_dumps_compact_method_bodies(sample_repo):
    """--flow should emit method bodies for the file's largest symbols."""
    result = run_cli(["read", "--flow", "src/services/user_service.py"], sample_repo)
    assert result.returncode == 0
    assert "Flow (compact method bodies):" in result.stdout
    # Body of create_user should appear in the slice
    assert "db.users.create" in result.stdout, result.stdout
