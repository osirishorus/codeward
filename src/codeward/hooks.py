from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX in normal agent environments
    fcntl = None

HISTORY = ".codeward/history.jsonl"
CODE_LIKE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".rb", ".php", ".cs"}


def global_history_path() -> Path:
    """Resolve ~/.codeward/history.jsonl at CALL time (not import time) so tests
    can isolate via HOME=<tmp> and end users are unaffected."""
    return Path.home() / ".codeward" / "history.jsonl"


# Backward-compat module-level alias for callers that imported GLOBAL_HISTORY.
# This is captured once at import; new code should call global_history_path().
GLOBAL_HISTORY = global_history_path()


def _append_history(path: Path, row: dict) -> None:
    """Append a single JSON row with file locking. Caller ensures path.parent exists."""
    with path.open("a", encoding="utf-8") as f:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(row) + "\n")
        finally:
            if fcntl is not None:
                fcntl.flock(f, fcntl.LOCK_UN)


def record(root: Path, command: str, raw_tokens: int, out_tokens: int) -> None:
    """Record a savings row to BOTH locations:

    - Per-repo `<root>/.codeward/history.jsonl` — only when that directory already
      exists (to avoid spamming `.codeward/` into random cwds where the user
      hasn't set up codeward).
    - Global `~/.codeward/history.jsonl` — always, with the originating repo path
      tagged on the row so `gain --global` can break down by repo.
    """
    row = {
        "ts": time.time(),
        "command": command,
        "raw_tokens": raw_tokens,
        "output_tokens": out_tokens,
        "saved_tokens": max(raw_tokens - out_tokens, 0),
    }
    # Per-repo: only if the user has initialized codeward in this repo.
    repo_path = root / HISTORY
    wrote_repo = False
    if repo_path.parent.is_dir():
        _append_history(repo_path, row)
        wrote_repo = True
    # Global: tag with repo so we can group later. Resolved at call time so
    # test HOME isolation works and changes propagate immediately. Skip if
    # the global path resolves to the same file we just wrote (happens when
    # HOME == repo root, e.g., during isolated tests).
    try:
        ghp = global_history_path()
        if not (wrote_repo and ghp.resolve() == repo_path.resolve()):
            ghp.parent.mkdir(parents=True, exist_ok=True)
            global_row = dict(row, repo=str(root))
            _append_history(ghp, global_row)
    except OSError:
        pass  # never let tracking failures break the actual command


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def rewrite_command(cmd: str) -> str | None:
    stripped = cmd.strip()
    if stripped.startswith("!raw "):
        return stripped[5:]
    if has_shell_metacharacters(stripped):
        return None
    try:
        parts = shlex.split(stripped)
    except ValueError:
        return None
    if not parts:
        return None
    head = parts[0]
    if head in {"codeward", "rtk", "contextzip", "snip"}:
        return None
    if head in {"cat", "head", "tail"} and len(parts) >= 2:
        if head == "tail" and any(p in {"-f", "-F", "--follow"} or p.startswith("--follow=") for p in parts[1:]):
            return None
        # Rewrite only single-file reads; multi-file cat/head/tail output is not equivalent.
        paths = [p for p in parts[1:] if not p.startswith("-")]
        if len(paths) == 1 and Path(paths[0]).suffix in CODE_LIKE_EXTS:
            return "codeward read " + shlex.quote(paths[0])
    if head in {"rg", "grep"} and len(parts) == 2:
        if any(p.startswith("-") for p in parts[1:]):
            return None
        query = parts[1]
        if query:
            return "codeward search " + shlex.quote(query)
    if head in {"find", "tree"}:
        return "codeward map"
    if head == "git" and len(parts) == 2:
        if parts[1] == "diff":
            return "codeward diff"
        if parts[1] == "status":
            return "codeward status"
    if head in {"pytest", "npm", "pnpm", "yarn", "cargo", "go"}:
        if head == "pytest" or "test" in parts:
            return "codeward test " + shlex.join(parts)
    return None


def has_shell_metacharacters(cmd: str) -> bool:
    return any(token in cmd for token in ["&&", "||", ";", "|", "`", "$(", ">", "<", "\n"])


def noop_response(agent: str) -> dict | None:
    if agent == "gemini":
        return {"decision": "allow"}
    if agent == "cursor":
        return {}
    if agent == "codex":
        # Codex treats `exit 0 / no stdout` as "no-op" (no context injected,
        # default permission). An empty dict prints `{}` which Codex still
        # parses as a no-op; we return None to skip writing anything.
        return None
    return None


def tracked_rewrite_command(original: str, rewritten: str, track: bool = True) -> str:
    if not track:
        return rewritten
    return "CODEWARD_ORIGINAL_COMMAND=" + shlex.quote(original) + " " + rewritten


EDIT_TOOL_NAMES = {
    "edit", "write", "multiedit", "notebookedit", "create_file", "str_replace_editor",
    # Codex CLI calls every file mutation through `apply_patch` — that tool
    # name is the one to match for PreToolUse on Codex. It's also used as an
    # alias for Edit/Write per the Codex hooks docs.
    "apply_patch",
}


def _preflight_for_file(file_path: str, root: Path) -> str | None:
    """Run preflight against the target file and return a short string
    summarizing what the agent should know before editing.
    Returns None if the file isn't in the indexed repo or preflight fails.

    Uses `<sys.executable> -m codeward.cli preflight` rather than bare
    `codeward` so the hook works regardless of whether the CLI script is
    installed on PATH (works in dev/test env, in pipx, in fresh venvs)."""
    import sys
    if not file_path:
        return None
    p = Path(file_path)
    try:
        rel = p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None
    cp = subprocess.run(
        [sys.executable, "-m", "codeward.cli", "preflight", rel],
        cwd=root, text=True, capture_output=True, timeout=10,
    )
    if cp.returncode != 0:
        return None
    return cp.stdout.strip()


def edit_hook_response(payload: dict, agent: str = "claude") -> dict | None:
    """Build a preflight response for Edit/Write tool calls.
    Doesn't block the edit — just attaches preflight info as additionalContext
    so the agent sees dependents+tests+side-effects before changing the file."""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or {}
    if isinstance(tool_input, str):
        tool_input = {"file_path": tool_input}
    elif not isinstance(tool_input, dict):
        return noop_response(agent)
    file_path = (
        tool_input.get("file_path") or tool_input.get("path")
        or tool_input.get("filename") or tool_input.get("file")
    )
    if not file_path:
        return noop_response(agent)
    summary = _preflight_for_file(str(file_path), Path.cwd())
    if not summary:
        return noop_response(agent)
    if agent in ("claude", "codex"):
        # Codex CLI adopted Claude's `hookSpecificOutput.additionalContext`
        # response shape verbatim, so a single branch handles both. Codex
        # does NOT accept `updatedInput`, but that's only relevant for the
        # Bash rewrite path — preflight only needs additionalContext.
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": summary,
                "permissionDecision": "allow",
                "permissionDecisionReason": "Codeward preflight",
            }
        }
    if agent == "cursor":
        return {"permission": "allow", "context": summary}
    if agent == "gemini":
        return {"decision": "allow", "hookSpecificOutput": {"context": summary}}
    return {"context": summary}


def hook_response(payload: dict, agent: str = "claude") -> dict | None:
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    if tool_name and tool_name.lower() in EDIT_TOOL_NAMES:
        return edit_hook_response(payload, agent)
    if tool_name and tool_name.lower() not in {"bash", "shell", "run_shell_command"}:
        return noop_response(agent)
    tool_input = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or payload
    if isinstance(tool_input, str):
        tool_input = {"command": tool_input}
    elif not isinstance(tool_input, dict):
        tool_input = {}
    cmd = tool_input.get("command") or tool_input.get("cmd") or ""
    rewritten = rewrite_command(str(cmd))
    if not rewritten:
        return noop_response(agent)
    # Codex PreToolUse hooks parse but do not honor `updatedInput`
    # (documented as "fail open"). We can't substitute the agent's command
    # there, so the Bash branch is a no-op on Codex — preflight via
    # apply_patch is still wired up separately and still works.
    if agent == "codex":
        return noop_response(agent)
    updated = dict(tool_input)
    is_raw_escape = str(cmd).strip().startswith("!raw ")
    updated["command"] = tracked_rewrite_command(str(cmd), rewritten, track=not is_raw_escape)
    if agent == "cursor":
        return {"permission": "allow", "updated_input": updated}
    if agent == "gemini":
        return {"decision": "allow", "hookSpecificOutput": {"tool_input": updated}}
    if agent == "claude":
        output = {
            "hookEventName": "PreToolUse",
            "updatedInput": updated,
        }
        if not is_raw_escape:
            output["permissionDecision"] = "allow"
            output["permissionDecisionReason"] = "Codeward auto-rewrite"
        return {"hookSpecificOutput": output}
    # Backwards-compatible generic shape for custom wrappers.
    return {"updatedInput": updated}


def compact_test_output(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    cp = subprocess.run(command, cwd=cwd, text=True, capture_output=True, env=env)
    raw = (cp.stdout or "") + (cp.stderr or "")
    lines = raw.splitlines()
    fail_lines = [l for l in lines if any(k in l.lower() for k in ["failed", "error", "traceback", "assert", "panic", "exception"])]
    if cp.returncode == 0:
        summary = f"✓ tests passed: {' '.join(command)}"
        # preserve last useful summary line if present
        interesting = [l for l in lines if re_test_summary(l)]
        if interesting:
            summary += "\n" + interesting[-1]
    else:
        summary = f"✗ tests failed: {' '.join(command)}\n" + "\n".join(fail_lines[:80])
    return cp.returncode, raw, summary


def re_test_summary(line: str) -> bool:
    l = line.lower()
    return any(x in l for x in [" passed", "passed in", " failed", "failed in", " tests", "coverage", " error"])


def parse_command_field(s: str) -> tuple[str, str, str]:
    """Decompose a stored 'command' field into (kind, original, rewritten).
    Stored shapes:
      - 'hook: cat foo.py -> codeward read foo.py'   (Bash-hook rewrite)
      - 'direct: codeward read foo.py'                (agent invoked codeward directly)
      - 'savings: cat foo.py -> codeward read foo.py' (codeward savings benchmark)
    Falls back to ('', s, s) if the format is unrecognized."""
    if s.startswith("hook: ") and " -> " in s:
        original, rewritten = s[len("hook: "):].split(" -> ", 1)
        return "hook", original.strip(), rewritten.strip()
    if s.startswith("direct: "):
        rewritten = s[len("direct: "):].strip()
        # Synthesize the likely raw analogue from the codeward subcommand.
        # `codeward read foo.py` ↔ `cat foo.py`; `codeward search X` ↔ `grep -rn X`; etc.
        original = _infer_raw_analogue(rewritten)
        return "direct", original, rewritten
    if s.startswith("savings: ") and " -> " in s:
        original, rewritten = s[len("savings: "):].split(" -> ", 1)
        return "savings", original.strip(), rewritten.strip()
    return "", s, s


def _infer_raw_analogue(codeward_cmd: str) -> str:
    """Best-effort guess at what raw shell command an agent would have run instead.
    Used in `gain` output to show 'original → rewrite' even for direct invocations."""
    parts = codeward_cmd.split()
    if len(parts) < 2 or parts[0] != "codeward":
        return codeward_cmd
    sub = parts[1]
    rest = " ".join(parts[2:])
    if sub in ("read", "slice"):
        # `codeward read foo.py` ↔ `cat foo.py`
        # `codeward slice Class.method` ↔ `sed -n 'X,Yp' file`
        return f"cat {rest}" if sub == "read" else f"sed -n ... {rest}"
    if sub == "search":
        return f"grep -rn {rest} ."
    if sub == "map":
        return "find . -maxdepth 3 -type f"
    if sub in ("symbol", "callgraph", "tests-for", "impact", "review", "refs", "blame", "sdiff", "api", "preflight"):
        return f"(no direct shell equivalent — would have required multiple grep+sed)"
    return f"(equivalent of) {codeward_cmd}"


def _read_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


_RULE_HEAVY = "═" * 64
_RULE_LIGHT = "─" * 64


def _color_enabled() -> bool:
    """Honor NO_COLOR and TERM=dumb; otherwise color when stdout is a TTY.
    Tests/MCP capture stdout via redirect, where isatty()==False — colors stay
    off there automatically."""
    import os
    import sys
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return getattr(sys.stdout, "isatty", lambda: False)()


def _c(text: str, code: str) -> str:
    """ANSI-wrap when color is enabled; pass-through otherwise."""
    if not _color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def _bold(text: str) -> str:
    return _c(text, "1")


def _dim(text: str) -> str:
    return _c(text, "2")


def _color_pct(pct: float) -> str:
    """Pick a color band for a savings percentage. Green ≥70, yellow ≥40,
    red below. Keeps the meter readable at a glance."""
    if pct >= 70:
        return "32"  # green
    if pct >= 40:
        return "33"  # yellow
    return "31"      # red


def _meter(pct: float, width: int = 28) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    bar = "█" * filled + "░" * (width - filled)
    return _c(bar, _color_pct(pct))


def _mini_meter(pct: float, width: int = 10) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    bar = "▇" * filled + "·" * (width - filled)
    return _c(bar, _color_pct(pct))


def _is_synthetic_original(s: str) -> bool:
    """True when 'original' came from _infer_raw_analogue's fallback paths
    (no honest shell equivalent). Those rows look cleaner without the arrow."""
    return s.startswith("(equivalent of)") or s.startswith("(no direct shell equivalent")


def _format_gain_row(r: dict, idx: int) -> list[str]:
    """Render one history row as a compact 2- or 3-line block.

    Layout:
        ` 1. original-command`
        `    → rewritten-command`               (omitted for direct/synthetic rows)
        `    ▇▇▇▇▇▇▇▇·· 81.1%  raw 6,005 → cs 1,132  (saved 4,873)`
    """
    raw = r.get("raw_tokens", 0)
    out_t = r.get("output_tokens", 0)
    saved = r.get("saved_tokens", 0)
    pct = (saved / raw * 100) if raw else 0
    _, original, rewritten = parse_command_field(r.get("command", ""))
    lines: list[str] = []
    head = _dim(f" {idx:>2}. ")
    if original != rewritten and not _is_synthetic_original(original):
        lines.append(f"{head}{original}")
        lines.append(f"     {_dim('→')} {_bold(rewritten)}")
    else:
        lines.append(f"{head}{_bold(rewritten)}")
    pct_str = _c(f"{pct:>5.1f}%", _color_pct(pct))
    lines.append(
        f"     {_mini_meter(pct)}  {pct_str}  "
        + _dim(f"raw {raw:>7,} → cs {out_t:>6,}")
        + f"  saved {_bold(format(saved, ',').rjust(7))}"
    )
    return lines


def _format_date_range(rows: list[dict]) -> str:
    """Compact 'YYYY-MM-DD → YYYY-MM-DD' from the oldest/newest ts in `rows`.
    Returns '' if no row has a timestamp."""
    import datetime as _dt
    stamps = [r.get("ts") for r in rows if isinstance(r.get("ts"), (int, float))]
    if not stamps:
        return ""
    oldest = _dt.datetime.fromtimestamp(min(stamps)).strftime("%Y-%m-%d")
    newest = _dt.datetime.fromtimestamp(max(stamps)).strftime("%Y-%m-%d")
    if oldest == newest:
        return oldest
    return f"{oldest} → {newest}"


def gain(root: Path, *, scope: str = "global") -> str:
    """Render token-savings history.

    scope=
      'global'  : ~/.codeward/history.jsonl  — aggregated across all repos (default)
      'repo'    : just <root>/.codeward/history.jsonl                (opt in via --repo)
      'all'     : aggregate of both (deduplicated by ts+command)
    """
    if scope == "repo":
        rows = _read_history(root / HISTORY)
        scope_label = "this repo"
        empty = "No Codeward history in this repo yet. Run `codeward read <file>` or `codeward init --hook`."
    elif scope == "all":
        local = _read_history(root / HISTORY)
        global_rows = _read_history(global_history_path())
        seen = set()
        rows = []
        for r in local + global_rows:
            key = (r.get("ts"), r.get("command"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
        scope_label = "all repos + global"
        empty = "No Codeward history yet. Run a `codeward read`/`slice`/etc. command, or enable hooks."
    else:
        rows = _read_history(global_history_path())
        scope_label = "all repos"
        empty = f"No history yet at {global_history_path()}. Run a `codeward` command first."

    if not rows:
        return empty

    saved = sum(r.get("saved_tokens", 0) for r in rows)
    total_raw = sum(r.get("raw_tokens", 0) for r in rows)
    total_out = sum(r.get("output_tokens", 0) for r in rows)
    pct = (saved / total_raw * 100) if total_raw else 0
    avg_saved = saved // len(rows) if rows else 0
    unique_commands = len({
        parse_command_field(r.get("command", ""))[2] for r in rows
    })
    date_range = _format_date_range(rows)

    top = sorted(rows, key=lambda r: r.get("saved_tokens", 0), reverse=True)[:8]

    pct_color = _color_pct(pct)
    title = _bold(f"Codeward · token savings · {scope_label}")
    sub = _dim(f"{len(rows):,} commands · {unique_commands:,} unique" + (f" · {date_range}" if date_range else ""))

    out = [
        title,
        sub,
        _dim(_RULE_HEAVY),
        f"  Raw tokens      {_dim(f'{total_raw:>12,}')}",
        f"  Output tokens   {_dim(f'{total_out:>12,}')}",
        f"  Tokens saved    " + _c(f"{saved:>12,}", pct_color) + _dim(f"   (avg {avg_saved:,}/cmd)"),
        f"  Efficiency      {_meter(pct)} " + _c(f"{pct:>5.1f}%", pct_color),
        "",
        _bold("Top savings"),
        _dim(_RULE_LIGHT),
    ]
    for i, r in enumerate(top, start=1):
        out.extend(_format_gain_row(r, i))
    return "\n".join(out)
