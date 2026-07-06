# Changelog

All notable changes to Codeward will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning after `0.1.0`.

## [Unreleased]

### Added

- **`codeward pr-report [--base <ref>] [--security]`** — GitHub-flavored Markdown PR report combining symbol-level `sdiff`, review findings, affected files, minimal tests, and a sticky-comment marker; ships with a composite GitHub Action and dogfood workflow.

### Changed

- **`codeward watch`** — incremental watch mode now debounces/coalesces file events, skips unchanged files by mtime+size, reuses tree-sitter incremental reparses through a bounded LRU cache, and exposes `--debounce-ms` plus `--stats`.

## [0.6.0] - 2026-07-02

### Added

- **`codeward todos [target]`** — lists TODO/FIXME/HACK/XXX/BUG comment markers grouped by file, with JSON output and MCP parity via `codeward_todos`.
- `codeward search` now supports `--regex` and `-i` / `--ignore-case`; JSON output includes append-only `regex` and `ignore_case` booleans.
- Route detection now recognizes Next.js App Router file routes (`app/**/route.ts|js`), `pages/api/*`, Phoenix router declarations, and Ktor route blocks.
- **`codeward affected [--changed | <target>] [--depth N]`** — CI test-selection. Walks the reverse-dependency graph transitively from the change set to every impacted file, maps those to covering tests, and emits the minimal test set plus a ready-to-run command (`pytest`/`jest`/`vitest`/`go` auto-detected). `--tests-only` prints just the command for `$(codeward affected --tests-only)` in CI. Unlike `impact` (single hop), this is the full transitive blast radius.
- **`codeward why <fileA> <fileB> [--direction forward|reverse|any]`** — shortest import/dependency path between two files (BFS over the resolved-dependency graph). Explains transitive coupling; reports `connected: false` with `path: null` when there is no path either way.
- **`codeward dead [target] [--min-confidence] [--include-public]`** — top-level functions/classes with zero references anywhere (same-file internal usages count, so private helpers stay off the list). Excludes route handlers, console entrypoints, dunders, and test files; public (exported) symbols are excluded by default since external consumers may import them (`--include-public` to include). Confidence-gated (`high` = Python AST, `medium` = tree-sitter, `low` = regex). Heuristic — dynamic dispatch / reflection is undetectable.
- **`codeward owners [target | --changed] [--top N] [--no-dependents] [--exclude-bots]`** — suggests reviewers by aggregating `git blame` authorship over the target/changed files (weight 1.0) and their direct dependents (weight 0.4), ranked by weighted lines and deduped by email.
- All four are exposed as MCP tools (`codeward_affected`, `codeward_why`, `codeward_dead`, `codeward_owners`) and support `--json`.

### Changed

- Import/dependency extraction now covers Java, Kotlin, Scala, C#, Swift, PHP, Ruby, quoted C/C++ includes, and Elixir aliases/imports/use statements; analyzer caches invalidate via a bumped analyzer version.
- Reference results are genuinely confidence-ranked before truncation, and qualified Python references demote same-name receiver collisions instead of treating every bare method name as equivalent.
- `api` now honors literal Python `__all__` as the authoritative public surface when present.
- Not-found exits are standardized on code 2 for symbol lookup and other indexed-target misses.
- SQLite cache writes use an atomic replacement flow with busy timeouts so concurrent readers do not observe half-written cache files.
- The npm wrapper installs the matching PyPI version (`codeward==<npm package version>`) instead of an unpinned latest package.
- Extracted `_is_public_symbol` (shared by `api` + `dead`) and `_blame_authors` (shared by `blame` + `owners`); added `RepoIndex.transitively_affected` and `RepoIndex.dependency_path` graph primitives.

### Fixed

- `dead` no longer keeps an unrelated same-name symbol alive just because another file references a private helper with the same short name.
- Regex fallback references ignore comments and string literals, reducing false positives in `refs`, `symbol`, `callgraph`, and `dead`.
- Tree-sitter live-reference walks are guarded against pathological recursion instead of crashing the command.
- Removed stale `doctor` PATH-shim reporting; shims were removed in 0.5.2.
- Clarification: `watchdog` is a core dependency now. Older changelog text that says `codeward watch` uses watchdog from the `[full]` extra describes the historical 0.4-era packaging state.

## [0.5.2] - 2026-05-16

### Removed

- `codeward init-agent` and the entire PATH-shim path (`SHIM_TOOLS`, `agent_instructions_block`, `cmd_run`, `clean_shim_env`). Native hooks for Claude / Codex / Gemini cover the same surface; the shim layer added install ceremony and a doctor-warning category nobody asked for.
- `codeward savings` and `run_capture_for_savings`. The benchmark harness shelled out to arbitrary shell commands to size their output — slow, side-effecty, and the number was a rounding error compared to actual `gain` history. `gain` itself is unchanged.
- `codeward coach`. Educational printout for "your command should be X instead" — superseded by the Bash hook actually performing the rewrite.
- `codeward run`. Only used by the PATH-shim path; gone with it.

### Changed

- `estimate_raw_command_tokens` no longer shells out for `find`/`grep`/`tree`/`git` to size raw output. Only `cat`/`head`/`tail` of a single file are sized precisely (by reading the file). Cumulative `gain` numbers are slightly lower on hook-heavy sessions; per-call accuracy is unchanged.
- Test suite trimmed: removed `test_coach_*`, `test_run_dry_run_*`, `test_init_agent_*`, `test_savings_command_*`. Rewrote `test_rewrite_avoids_unsafe_shell_and_flaggy_commands` to call `rewrite_command` directly instead of going through the removed `run --dry-run` interface.
- `docs/GUIDE.md` rewritten for v0.5.x — drops the PATH-shim section, adds the Codex hook section, tightens the rest. `docs/PLAN.md` synced to current surface.

## [0.5.1] - 2026-05-16

### Added

- **Codex CLI hook integration.** OpenAI Codex shipped PreToolUse hooks with Claude's response shape; `--codex` on `codeward init --hook` now writes a `^apply_patch$` PreToolUse entry to `~/.codex/hooks.json` that injects preflight context (dependents, tests, side-effects, routes, blast-radius) before every file edit. Bash rewrite stays off for Codex because Codex hooks parse but don't honor `updatedInput`.
- `codeward hook --agent codex` is now a first-class adapter. `codeward doctor` reports the Codex hook state alongside Claude and Gemini.

## [0.5.0] - 2026-05-16

Focused on reach: more languages, more frameworks, easier install, and three real bug fixes.

### Added

- **`codeward routes [target]`** — framework-aware URL → handler mapping. Recognizes **FastAPI**, **Flask** / **Starlette** / **Sanic**, **Django**, **Express** / **Koa** / **Hono**, **NestJS**, **Spring** (Java/Kotlin), **Gin** / **Echo** / **Chi** (Go), **Actix-web** (Rust), **Sinatra** / **Rails** (Ruby), **Laravel** (PHP), **ASP.NET Core** (C#). Routes also surface in `preflight` payloads for files that declare them, and `callgraph` accepts route patterns directly.
- **Extended language coverage** — added tree-sitter analyzers for **C**, **C++**, **Kotlin**, **Swift**, **Scala**, **Bash**, **Lua**, **Elixir**. Total: 17 languages with first-class symbol extraction.
- **npm wrapper** — `npx codeward` and `npm i -g codeward` now work. The wrapper bootstraps via `pipx` (preferred) or `pip --user`, then forwards to the Python CLI. Node ≥ 18, Python ≥ 3.11.
- **`find_symbol_fuzzy`** — case-insensitive / suffix / substring fallback used by `symbol`, `slice`, `blame`, `refs`, `callgraph` when the strict match returns nothing. Means `codeward symbol initdb` finds `initDB`.

### Changed

- **`gain` output** — banner + date range + unique-command count + per-row mini-meter + ANSI color (auto-disabled when `NO_COLOR` is set or stdout isn't a TTY). Same data, much more scannable.
- **References track column** — `Reference` now carries a `column` field; `cmd_refs` filters definitions vs call sites by analyzer kind, so legitimate calls on the same line as a definition (single-line Java/PHP/C# bodies, recursive Python defs) are no longer dropped.
- **README** — install via PyPI / npx as the primary path; the routes feature and broader language coverage are foregrounded.

### Fixed

- **`codeward review --changed` crash** — `tokenize.TokenizeError` referenced the wrong name; should be `tokenize.TokenError`. Also catches `IndentationError` / `SyntaxError` from the fallback strip path.
- **Symbol lookup too strict** — `symbol`, `refs`, `blame` were returning empty on common camelCase / underscore variants. Fuzzy fallback catches these.
- **Refs over-suppressed** — when a definition and a call site shared a line, the call was filtered out. Fix uses analyzer provenance (tree-sitter / python_ast refs are already syntax-aware) instead of pure (file, line) matching.

### Verified on

- All 99 tests pass against the new analyzers and route extractor.
- Smoke-tested route extraction across 11 frameworks with synthetic samples.
- Smoke-tested symbol extraction across 8 new language samples (C, C++, Kotlin, Swift, Scala, Bash, Lua, Elixir).

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
