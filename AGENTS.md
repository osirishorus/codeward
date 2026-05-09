# Codeward — Contributor / Agent guide

This file is for coding agents (and humans) working **on** Codeward itself.

## Quick orientation

- `src/codeward/cli.py` — every `cmd_*` function and the argparse setup. New commands go here.
- `src/codeward/index.py` — `RepoIndex`, `analyze_file`, side-effect/security/test heuristics, SQLite cache.
- `src/codeward/analyzers/treesitter.py` — opt-in tree-sitter symbol extraction for Go/Rust/TS/JS/Java/Ruby/PHP/C#.
- `src/codeward/hooks.py` — Bash and Edit/Write `PreToolUse` hook responses; rewrite logic.
- `src/codeward/watch.py` — foreground re-indexer (incremental SQLite updates on file events).
- `tests/test_cli.py` — every command has a regression test.

## How to test

```bash
python3 -m pytest tests/ -q
```

Skips for missing tree-sitter packages are expected when the `[full]` extra isn't installed in the test env.

## Conventions

- All read-only commands accept `--json` via the parent parser. Build a payload dict alongside text lines and pass `payload=...` and `json_mode=getattr(args, "json_output", False)` to `emit_tracked`.
- For commands with a clear raw analogue (`read` ↔ `cat`, `slice` ↔ `sed -n`), pass `raw_token_estimate=` so direct invocations record savings in `codeward gain`.
- Don't duplicate RTK. RTK owns the Bash output-compression layer (`cat/grep/find/git status`); Codeward owns semantic queries (`symbol/refs/slice/preflight/...`). When in doubt, check whether RTK is on PATH and defer (see `_defer_to_rtk`).

## Where benchmarks live

`docs/BENCHMARKS.md` — A/B comparisons across architecture-planning, refactor, code-review, bug-fix, Go workflows. Update it when you ship a new feature that materially changes the numbers.
