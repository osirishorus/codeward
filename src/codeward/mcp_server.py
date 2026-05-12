"""MCP server exposing Codeward's semantic queries as MCP tools.

Why this exists:
    Most agentic coding tools (Claude Desktop, Cursor, Continue, Zed, Cline,
    Goose, Windsurf, ChatGPT Desktop) speak MCP. Before this module, each tool
    needed bespoke hook wiring to use Codeward (Claude Code hook, Gemini hook,
    PATH shims for Codex/OpenCode, etc.). With the MCP server, any
    MCP-compatible client gets Codeward's full semantic surface from one
    config entry.

Design:
    - Optional dep: `pip install 'codeward[mcp]'`. Codeward itself does not
      import `mcp` unless this module is loaded.
    - Each tool wraps the existing `cmd_*` function in `cli.py`, sets
      `json_output=True`, and captures stdout. We parse the JSON the CLI
      already emits — the schema lives in `docs/JSON_SCHEMA.md` and stays
      authoritative.
    - Tools default to `Path.cwd()` (whatever directory the client launched
      the server in). For clients that don't set cwd, `codeward mcp --cwd
      <path>` pins it at startup.

Transport: stdio (the default for local MCP servers).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
from typing import Any

from . import cli as _cli


def _run(cmd_func, namespace: argparse.Namespace) -> dict[str, Any]:
    """Call a cmd_* function with stdout captured and return the parsed JSON.

    Every cmd_* in `cli.py` prints either a JSON document (when
    `json_output=True`) or human-readable text. We always set
    `json_output=True` for MCP — agents want structured output.

    Falls back to `{"raw_output": ..., "exit_code": rc}` for the small set
    of commands that haven't been wired for JSON yet (so the tool never
    silently swallows output)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = cmd_func(namespace)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    except Exception as e:  # never let a single bad query kill the server
        return {"error": f"{type(e).__name__}: {e}", "exit_code": 1}
    out = buf.getvalue().strip()
    if not out:
        return {"exit_code": rc or 0, "result": None}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"exit_code": rc or 0, "raw_output": out}


def _ns(**fields: Any) -> argparse.Namespace:
    """Build an argparse.Namespace with json_output=True and given fields."""
    return argparse.Namespace(json_output=True, **fields)


def create_server():
    """Return a configured FastMCP instance. Lazy import so the rest of
    Codeward keeps working when `[mcp]` isn't installed."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise RuntimeError(
            "The MCP server requires the `mcp` package. Install with:\n"
            "  pip install 'codeward[mcp]'"
        ) from e

    mcp = FastMCP("codeward")

    # ---- Repo orientation ----------------------------------------------

    @mcp.tool()
    def codeward_map() -> dict:
        """Repo overview: primary language, important files, suggested next
        steps. Use this FIRST when you don't know a codebase."""
        return _run(_cli.cmd_map, _ns())

    @mcp.tool()
    def codeward_doctor() -> dict:
        """Environment / hook ordering / index health check. Use when
        something feels off."""
        return _run(_cli.cmd_doctor, _ns())

    # ---- File-level ----------------------------------------------------

    @mcp.tool()
    def codeward_read(file: str, flow: bool = False, flow_count: int = 6) -> dict:
        """Semantic summary of a file: symbols, imports, dependents, likely
        tests, side effects. Cheaper than dumping the whole file. Set
        `flow=true` to also include compact bodies of the file's largest
        methods."""
        return _run(_cli.cmd_read, _ns(file=file, flow=flow, flow_count=flow_count))

    @mcp.tool()
    def codeward_preflight(file: str) -> dict:
        """Context an editor should see before changing a file: dependents
        + tests + side effects. Run BEFORE editing to know what'll break."""
        return _run(_cli.cmd_preflight, _ns(file=file))

    @mcp.tool()
    def codeward_api(target: str) -> dict:
        """Public API surface of a file or directory (top-level
        non-underscore symbols). Use to understand what a module exports."""
        return _run(_cli.cmd_api, _ns(target=target))

    # ---- Search ---------------------------------------------------------

    @mcp.tool()
    def codeward_search(query: str, per_file: int = 5) -> dict:
        """Grouped, indexed search across the repo. Prefer this over raw
        grep — results are grouped by file with line numbers and stable
        ordering."""
        return _run(_cli.cmd_search, _ns(query=query, per_file=per_file))

    # ---- Symbol-level ---------------------------------------------------

    @mcp.tool()
    def codeward_symbol(name: str) -> dict:
        """Find a symbol: definitions + confidence-ranked callers + tests.
        Use to ask "where is X defined and who uses it?"."""
        return _run(_cli.cmd_symbol, _ns(name=name))

    @mcp.tool()
    def codeward_callgraph(query: str) -> dict:
        """Confidence-ranked flow summary for a route or symbol, plus the
        side effects it transitively touches. Accepts symbol names or
        route patterns like `POST /api/users`."""
        return _run(_cli.cmd_callgraph, _ns(query=query))

    @mcp.tool()
    def codeward_slice(symbol: str, no_comments: bool = False, signature_only: bool = False) -> dict:
        """Return exact bytes of a function/class body when AST/tree-sitter
        ranges exist. Replaces `sed -n 'X,Yp'` — no line-number guessing.
        `signature_only=true` returns just the def line."""
        return _run(
            _cli.cmd_slice,
            _ns(symbol=symbol, no_comments=no_comments, signature_only=signature_only),
        )

    @mcp.tool()
    def codeward_refs(symbol: str, include_defs: bool = False) -> dict:
        """Confidence-ranked reference sites for a symbol, separate from
        definitions. Replaces `grep -rn` with semantic awareness."""
        return _run(_cli.cmd_refs, _ns(symbol=symbol, include_defs=include_defs))

    @mcp.tool()
    def codeward_blame(symbol: str) -> dict:
        """`git blame` aggregated by author over the symbol's exact line
        range. Use to find who owns / last touched a function or class."""
        return _run(_cli.cmd_blame, _ns(symbol=symbol))

    @mcp.tool()
    def codeward_tests_for(target: str) -> dict:
        """Likely tests for a file or symbol. Returns the test file paths
        plus a suggested pytest command."""
        return _run(_cli.cmd_tests_for, _ns(target=target))

    # ---- Diff / change-aware -------------------------------------------

    @mcp.tool()
    def codeward_sdiff(base: str = "HEAD") -> dict:
        """Semantic diff: which symbols (functions, classes, methods) were
        added / removed / changed since `base`. Cleaner than `git diff`
        when you want to understand the shape of a change."""
        return _run(_cli.cmd_sdiff, _ns(base=base))

    @mcp.tool()
    def codeward_impact(
        target: str | None = None,
        changed: bool = False,
        base: str | None = None,
    ) -> dict:
        """Risk + dependents + tests for changed files (or a specific
        target). Set `changed=true` to use git's working-tree changes; or
        pass `target` for a specific file. Hotspots (high churn) are
        flagged and bumped to HIGH risk."""
        return _run(_cli.cmd_impact, _ns(target=target, changed=changed, base=base))

    @mcp.tool()
    def codeward_review(
        target: str | None = None,
        changed: bool = False,
        base: str | None = None,
        security: bool = False,
    ) -> dict:
        """Pre-commit semantic + (optional) heuristic security review.
        Returns symbols touched, risks, suggested tests. Set
        `security=true` for pattern-based security findings."""
        return _run(
            _cli.cmd_review,
            _ns(target=target, changed=changed, base=base, security=security),
        )

    # ---- Git history awareness -----------------------------------------

    @mcp.tool()
    def codeward_hotspots(since: str = "90d", top: int = 10, max_commits: int = 2000) -> dict:
        """Files ranked by `commits × (1 + dependents)` over the window.
        Surfaces where bugs concentrate. `since` accepts git-log forms:
        `30d`, `6.months`, `2024-01-01`. Returns `{files: []}` in non-git
        repos."""
        return _run(
            _cli.cmd_hotspots,
            _ns(since=since, top=top, max_commits=max_commits),
        )

    @mcp.tool()
    def codeward_neighbors(file: str, since: str = "90d", top: int = 10, max_commits: int = 2000) -> dict:
        """Files that historically change together with `file`, aggregated
        from git log. Use to answer "if I edit X, what else usually
        needs to change?". Exit code 2 if file is not in the index."""
        return _run(
            _cli.cmd_neighbors,
            _ns(file=file, since=since, top=top, max_commits=max_commits),
        )

    # ---- Token-budget / context packing --------------------------------

    @mcp.tool()
    def codeward_budget(target: str | None = None, top: int = 10) -> dict:
        """Estimated raw token cost of a repo/file/directory, plus cheaper
        Codeward commands to fetch the same context. Use BEFORE dumping
        large files to know what it'll cost."""
        return _run(_cli.cmd_budget, _ns(target=target, top=top))

    @mcp.tool()
    def codeward_pack(target: str, max_tokens: int = 800, top_symbols: int = 6) -> dict:
        """Compact context bundle for `target` under a token budget:
        target + tests + dependents + top co-change neighbors. Best
        single tool for loading context when starting work on a file."""
        return _run(
            _cli.cmd_pack,
            _ns(target=target, max_tokens=max_tokens, top_symbols=top_symbols),
        )

    return mcp


def run(cwd: Path | None = None) -> int:
    """Entry point for `codeward mcp`. Starts a FastMCP server on stdio."""
    import os
    import sys

    if cwd is not None:
        try:
            os.chdir(cwd)
        except OSError as e:
            print(f"codeward mcp: cannot chdir to {cwd}: {e}", file=sys.stderr)
            return 1
    try:
        mcp = create_server()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    mcp.run()
    return 0
