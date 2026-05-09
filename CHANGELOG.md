# Changelog

All notable changes to Codeward will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning after `0.1.0`.

## [0.3.0] - 2026-05-08

Phases B/C/D landed. Codeward now ships a complete symbol-level toolchain that lives entirely outside RTK's lane.

### Added — Phase B (symbol commands)

- `codeward slice <Class.method>` — exact bytes of one method body via AST line range. Replaces `sed -n 'X,Yp'`. Optional `--no-comments`, `--signature-only`. Handles Go pointer-receiver decoration like `(*Engine).ServeHTTP`.
- `codeward refs <symbol>` — every reference site (file:line) using the resolved index. Excludes definition sites by default; `--include-defs` to keep them.
- `codeward blame <symbol>` — `git blame --line-porcelain` aggregated per-author over the symbol's exact line range. Last-touched commit + summary.
- `codeward sdiff [--base ref]` — semantic diff: lists symbols added / removed / signature-changed between current state and a git ref. Symbol-level, not line-level.
- `codeward api <file-or-dir>` — public API surface: top-level non-underscore symbols only, with signatures. Skips test files.

### Added — Phase C (edit-time hooks)

- `codeward preflight <file>` — compact "what an editor should know" summary: language, lines, symbols, dependents, likely tests, side effects, security flags, blast-radius (LOW / MEDIUM / HIGH).
- **PreToolUse hook on `Edit|Write|MultiEdit`** — different tool surface from RTK's `Bash` matcher (RTK's README explicitly says it doesn't touch Edit/Write). Hook runs `codeward preflight` against the target file and returns `additionalContext` so the agent sees the impact info before editing. Auto-installed by `codeward init --hook`; opt-out with `--no-hook-edit`.

### Added — Phase D (watch daemon)

- `codeward watch [--debounce 0.5]` — foreground re-indexer that holds a hot in-memory `RepoIndex` and writes the SQLite cache on file events. Subsequent CLI invocations load from the fresh cache instead of rebuilding. Uses `watchdog` when installed (the `[full]` extra), falls back to 2-second mtime polling otherwise.

### Changed

- `codeward status`, `codeward diff`, `codeward test` now defer to `rtk` when RTK is on PATH (RTK does the same compression and is its core competency). Pass `--force` to use the Codeward variant anyway. This eliminates the only feature overlap with RTK.
- `find_symbol` ranks results: exact name matches first, then method-suffix, then methods-list. Fixes a bug where `slice "Foo.bar"` could return the `Foo` class instead of the `bar` method when both matched.

### Verified on

- Phase B / tree-sitter validated on **gin** (Go, 99 files): `slice (*Engine).ServeHTTP` returns the correct 14-line body in one call. `blame` aggregates by author.
- Phase B validated on **zod** (TypeScript, 402 files): symbol extraction with end_lines for a 1293-line `checks.ts` (72 symbols).
- A/B comparison on gin: same-quality answer in 11 turns ($0.56) with Codeward vs. 9 turns ($0.40) baseline. v0.2.0 result was tied-quality but cost more; v0.3.0 `slice`/`refs` directly target the gap.

## [0.2.0] - 2026-05-08

**Minimum Python bumped to 3.11** (stdlib `tomllib` for config).

### Added

- `--json` output on every read-only command. Stable schema in `docs/JSON_SCHEMA.md`.
- Per-repo `.codeward/config.toml` for custom ignore dirs, extra test directories, extra test filename patterns, and custom side-effect rules. Schema in `docs/CONFIG.md`.
- Tree-sitter language layer as opt-in `[full]` extra. Accurate symbol extraction with end_lines, signatures, and method linkage for **Go, Rust, TypeScript, JavaScript, Java, Ruby, PHP, and C#**. Validated on gin (Go) and zod (TS) — full method signatures, nested method linkage, sub-2s indexing on 400-file repos.
- `codeward doctor` reports config validity and language-pack status.

### Changed

- `codeward init` writes to **both** `CLAUDE.md` (Claude Code's auto-discovered memory) and `AGENTS.md` (Codex/Cursor convention). Previously only AGENTS.md, which Claude Code doesn't auto-load.
- `codeward read` symbol output groups class methods under their declaring class with full type-annotated signatures.

### Fixed

- **Direct-invocation tracking.** When the agent calls `codeward read foo.py` directly (pure CLAUDE.md mode, no hook env var), `codeward gain` now records the savings against the inferred raw analogue (`cat foo.py`). Previously only hook/shim-routed invocations were tracked.
- Side-effect heuristics no longer false-positive on `args.insert()` (list builtin) or local helpers like `_fetch(`. Patterns now require library/ORM context or SQL keywords.
- `is_test_file` no longer matches any path containing the substring "test" (so `src/click/testing.py` is correctly classified as production code, not a test).
- `TEST_PATTERNS` no longer includes `*.rs` (which incorrectly classified every Rust file as a test).

## [0.1.0] - 2026-05-08

### Added

- Semantic repository map via `codeward map`.
- Semantic file summaries via `codeward read`.
- Compact grouped search via `codeward search`.
- Symbol lookup, route/symbol callgraph summaries, tests-for matching, impact analysis, and semantic review.
- Optional heuristic security review with `codeward review --security`.
- Test-output compression via `codeward test`.
- Persistent SQLite index export via `codeward index`.
- RTK-style command proxy via `codeward run`.
- Universal PATH shim installer via `codeward init-agent`.
- Claude Code native hook installer via `codeward init`.
- Native hook adapters for Claude Code, Gemini CLI, Cursor, and generic wrappers.
- `!raw <command>` escape hatch.
- RTK/contextzip/snip coexistence guards.
- Token-savings history via `codeward gain`.
- Side-by-side savings benchmark command via `codeward savings`.
- Automatic hook/PATH-shim savings tracking using `CODEWARD_ORIGINAL_COMMAND`.
- Flask live benchmark guide and public-ready docs.

### Safety

- Conservative rewrite policy for ambiguous commands.
- Fail-open hook behavior for invalid JSON and unsupported tool payloads.
- PATH shim recursion avoidance.
- Raw escape hatches avoid auto-approving Claude permissions.
