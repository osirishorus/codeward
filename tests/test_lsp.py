import json
import subprocess
import sys
from pathlib import Path

import pytest

from codeward.lsp import (
    LspSession,
    LspUnavailable,
    _decode_message,
    _encode_message,
    lsp_character_to_column,
    position_for_symbol,
    str_column_to_lsp_character,
)


ROOT = Path(__file__).resolve().parents[1]


def test_framing_round_trips_json_rpc_message():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"x": "雪"}}
    raw = _encode_message(msg)

    assert raw.startswith(b"Content-Length: ")
    assert _decode_message(raw) == msg


def test_utf16_position_conversion_handles_emoji_and_cjk():
    line = "a😀中b"

    assert str_column_to_lsp_character(line, 0) == 0
    assert str_column_to_lsp_character(line, 1) == 1
    assert str_column_to_lsp_character(line, 2) == 3
    assert str_column_to_lsp_character(line, 3) == 4
    assert str_column_to_lsp_character(line, 4) == 5
    assert lsp_character_to_column(line, 0) == 0
    assert lsp_character_to_column(line, 1) == 1
    assert lsp_character_to_column(line, 3) == 2
    assert lsp_character_to_column(line, 4) == 3
    assert lsp_character_to_column(line, 5) == 4


def test_lsp_timeout_raises_unavailable(tmp_path, monkeypatch):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"sleep": 2}))
    source = tmp_path / "a.py"
    source.write_text("def target():\n    return 1\n")
    monkeypatch.setenv("CODEWARD_LSP_TIMEOUT", "0.1")

    with pytest.raises(LspUnavailable):
        with LspSession(tmp_path, "python", source, [sys.executable, str(ROOT / "tests" / "fake_lsp_server.py"), str(fixture)]) as session:
            session.references(1, 4)


def test_end_to_end_lsp_session_with_fake_server(tmp_path, monkeypatch):
    (tmp_path / "pkg").mkdir()
    target = tmp_path / "pkg" / "mod.py"
    target.write_text(
        "class Target:\n"
        "    pass\n"
        "\n"
        "def use():\n"
        "    return Target()\n"
    )
    extra = tmp_path / "pkg" / "extra.py"
    extra.write_text("from pkg.mod import Target\n\nvalue = Target()\n")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({
        "references": [
            {"uri": target.resolve().as_uri(), "range": {"start": {"line": 4, "character": 11}}},
            {"uri": extra.resolve().as_uri(), "range": {"start": {"line": 2, "character": 8}}},
        ]
    }))
    with LspSession(tmp_path, "python", target, [sys.executable, str(ROOT / "tests" / "fake_lsp_server.py"), str(fixture)]) as session:
        refs = session.references(1, 6)

    assert len(refs) == 2
    assert refs[0]["uri"] == target.resolve().as_uri()
    assert refs[1]["uri"] == extra.resolve().as_uri()


def test_position_for_symbol_uses_utf16_character(tmp_path):
    source = tmp_path / "emoji.py"
    source.write_text("😀中Target = 1\n")
    line, character = position_for_symbol(source, 1, "Target")

    assert line == 0
    assert character == 3
