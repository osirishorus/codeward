"""Tests for the Codeward MCP server.

Three layers:
  1. Unit tests on `_run()` and `_ns()` — fast, no subprocess.
  2. In-process test that `create_server()` registers the expected tools.
  3. End-to-end test that spawns `codeward mcp` and exchanges JSON-RPC
     over stdio.

The `mcp` package is an optional extra — tests skip cleanly when it
isn't installed."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

mcp = pytest.importorskip("mcp", reason="codeward[mcp] extra not installed")

# Bring in sample_repo fixture from the main CLI test module
from tests.test_cli import sample_repo  # noqa: E402,F401


def test_run_helper_returns_parsed_json_for_codeward_command():
    """_run() should capture cmd_*'s stdout and return parsed JSON."""
    sys.path.insert(0, str(SRC))
    try:
        from codeward.mcp_server import _ns, _run
        from codeward import cli
    finally:
        sys.path.pop(0)
    # cmd_doctor always returns a JSON envelope when json_output=True
    result = _run(cli.cmd_doctor, _ns())
    assert isinstance(result, dict)
    assert result.get("command") == "doctor"
    assert "ok" in result


def test_create_server_registers_expected_tools():
    """The server should expose every read-only Codeward command."""
    sys.path.insert(0, str(SRC))
    try:
        from codeward.mcp_server import create_server
    finally:
        sys.path.pop(0)
    server = create_server()
    # FastMCP stores registered tools in an internal tool manager; use the
    # documented introspection helper instead of poking at internals.
    import asyncio
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    expected = {
        "codeward_map", "codeward_read", "codeward_search", "codeward_symbol",
        "codeward_callgraph", "codeward_tests_for", "codeward_impact",
        "codeward_review", "codeward_slice", "codeward_refs", "codeward_blame",
        "codeward_sdiff", "codeward_api", "codeward_preflight",
        "codeward_hotspots", "codeward_neighbors", "codeward_pack",
        "codeward_budget", "codeward_doctor", "codeward_diff_pack",
    }
    missing = expected - names
    assert not missing, f"Missing MCP tools: {missing}"


def _spawn_mcp(cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "codeward.cli", "mcp", "--cwd", str(cwd)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PYTHONPATH": str(SRC), "HOME": str(cwd), "PATH": "/usr/bin:/bin"},
    )


def _rpc(p: subprocess.Popen, msg: dict) -> dict | None:
    p.stdin.write((json.dumps(msg) + "\n").encode())
    p.stdin.flush()
    if "id" not in msg:
        return None  # notification, no response expected
    line = p.stdout.readline()
    if not line:
        return None
    return json.loads(line.decode())


def test_end_to_end_initialize_list_call(sample_repo):
    """Full handshake: initialize -> tools/list -> codeward_map call."""
    p = _spawn_mcp(sample_repo)
    try:
        r = _rpc(p, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0"},
            },
        })
        assert r and "result" in r, f"initialize failed: {r}"

        _rpc(p, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        r = _rpc(p, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in r["result"]["tools"]}
        assert "codeward_map" in names
        assert "codeward_hotspots" in names

        r = _rpc(p, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "codeward_map", "arguments": {}},
        })
        # The result content is either text JSON or structuredContent.
        content = r["result"].get("content", [])
        text = content[0].get("text", "") if content else ""
        payload = json.loads(text) if text.strip().startswith("{") else r["result"].get("structuredContent", {})
        # Sample repo is Python and contains user_service.py.
        assert payload.get("primary_language") == "Python"
        files = payload.get("important_files") or []
        assert any("user_service.py" in f.get("path", "") for f in files)
    finally:
        p.terminate()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()


def test_end_to_end_symbol_lookup(sample_repo):
    """Tool call with arguments: codeward_symbol returns expected definitions."""
    p = _spawn_mcp(sample_repo)
    try:
        _rpc(p, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}},
        })
        _rpc(p, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = _rpc(p, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "codeward_symbol", "arguments": {"name": "UserService"}},
        })
        content = r["result"].get("content", [])
        text = content[0].get("text", "") if content else ""
        payload = json.loads(text) if text.strip().startswith("{") else r["result"].get("structuredContent", {})
        defs = payload.get("definitions", [])
        assert len(defs) >= 1
        assert defs[0]["name"] == "UserService"
        assert "user_service.py" in defs[0]["file"]
    finally:
        p.terminate()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()
