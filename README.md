<p align="center">
  <img src="./assets/logo.png" alt="Codeward logo — magnifying glass over a hierarchical syntax tree" width="180">
</p>

<h1 align="center">Codeward</h1>

<p align="center"><strong>Symbol-level codebase intelligence for coding agents.</strong></p>

Codeward gives your agent commands the shell can't: "where is this defined?", "who calls it?", "which tests cover it?", "what changed at the symbol level?", "what would break if I edit this file?". It indexes your repo with tree-sitter / Python AST.

```text
What does this repo do?            →  codeward map
Where is APIRouter defined?        →  codeward symbol APIRouter
Show me Engine.ServeHTTP's body    →  codeward slice "(*Engine).ServeHTTP"
What calls this method?            →  codeward refs ServeHTTP
What tests cover this file?        →  codeward tests-for fastapi/routing.py
Who wrote this method?             →  codeward blame APIRoute.get_route_handler
What changed at the symbol level?  →  codeward sdiff --base HEAD~1
What's the public API of this?     →  codeward api fastapi/applications.py
What could break if I edit this?   →  codeward preflight fastapi/routing.py  (auto-injected on Edit/Write)
```

Headline feature: **[preflight context injection](#preflight-blast-radius-context-before-edits)** — dependents, tests, side-effects, blast-radius pushed into the model *before* an Edit/Write call. Codeward composes with [RTK](https://github.com/rtk-ai/rtk) rather than replacing it ([details](#how-it-composes-with-rtk)).

## When not to use this

- **Chasing a specific bug, symbol already known** — raw `grep`/`sed` (with RTK compressing) is faster.
- **Small repo (<500 LOC)** — index overhead doesn't pay off.
- **Agent ignores `CLAUDE.md`/`AGENTS.md`/`GEMINI.md` and no hooks installed** — Codeward sits unused.

If budget for only one tool, install **RTK** first. Add Codeward for refactor planning, cross-file impact, or pre-edit blast-radius.

## Project status

v0.4.x, **install from source**. CLI surface and JSON schema are stable across `0.4.x`.

- **Solid:** core commands (`map`, `read`, `search`, `symbol`, `slice`, `refs`, `tests-for`, `impact`, `preflight`), SQLite index with mtime invalidation, Claude/Gemini hook adapters, `--json`, `gain` history.
- **Maturity varies by language:** `callgraph`, `blame`, `sdiff`, `api`, `review --security` — see the [Commands](#commands) table.

## Install

```bash
git clone https://github.com/osirishorus/codeward.git
pipx install --editable ./codeward     # recommended
# or: cd codeward && pip install -e .
```

Python ≥ 3.11. Tree-sitter grammars (Go, Rust, TS/JS, Java, Ruby, PHP, C#) and `watchdog` pulled in by default. PyPI release in v0.5 — watch [releases](https://github.com/osirishorus/codeward/releases).

## Quick start

```bash
codeward init      # writes CLAUDE.md + AGENTS.md vocabulary (no hooks)
codeward map       # repo overview — auto-builds the index on first run
codeward doctor    # verify environment
```

The index auto-builds on the first read-only command and lives at `.codeward/index.sqlite` with mtime invalidation. Run `codeward index` only to pre-warm a large repo or in CI; for long sessions use `codeward watch` (foreground re-indexer).

## Optional: hook integration

```bash
codeward init --hook                    # both Bash + Edit/Write hooks (project-local)
codeward init --hook --global           # also wire ~/.claude/settings.json
codeward init --hook --no-hook-bash     # Edit/Write preflight only (recommended w/ RTK)
codeward init --hook --no-hook-edit     # Bash rewrite only
codeward init --hook --gemini           # also wire ~/.gemini/settings.json
```

Two independent `PreToolUse` entries:

- **`Bash`** — rewrites `cat foo.py` → `codeward read foo.py`. Inserted *before* RTK's Bash entry; RTK passes `codeward …` through.
- **`Edit|Write|MultiEdit`** — runs `codeward preflight <file>` and injects `additionalContext` before the edit. Different matcher from RTK; cannot clash.

`codeward doctor` checks which hooks are installed, that the Bash hook is ordered **before** `rtk hook claude` (otherwise RTK runs first and compresses output before Codeward can rewrite), and that the index is fresh.

## Preflight: blast-radius context before edits

When the Edit/Write hook is installed, Codeward injects a compact `additionalContext` payload before the edit reaches the model. Real example from `fastapi/routing.py`:

```text
# Codeward preflight: fastapi/routing.py
  language=Python, lines=4956, symbols=47, blast_radius=HIGH
  dependents (14): docs_src/custom_request_and_route/tutorial001_an_py310.py,
                   tests/test_router_redirect_slashes.py, tests/test_route_scope.py, …
  likely tests: tests/test_custom_route_class.py, tests/test_route_scope.py, …
  side effects: Network call (httpx, line 142), DB write (sqlalchemy, line 891)
  security flags: 0
```

**149 tokens, once per Edit call, on the right surface** — fires on `Edit`/`Write`/`MultiEdit`, not on `Grep` or `Read`. The agent adapts: smaller patches on HIGH blast-radius, awareness of dependents/tests/side-effects.

Payload contents are configurable via `.codeward/config.toml` (custom side-effect rules, ignored dirs, extra test paths). End-to-end transcript: [docs/BENCHMARKS.md](docs/BENCHMARKS.md#editwrite-benchmark--preflight-hook-in-action).

## Commands

### Core (load-bearing, exercised in benchmarks)

All read-only commands support `--json`.

| Command | What it does | Replaces |
|---|---|---|
| `codeward map` | Repo overview: language, important files, suggested next steps | `find . -maxdepth 3 -type f` |
| `codeward read <file>` | Symbols + signatures + dependents + tests + side effects (`--flow` adds method bodies) | `cat <file>` |
| `codeward search <query>` | Index-grouped search hits | `grep -rn <query>` |
| `codeward symbol <name>` | Definition + ranked callers + tests | grep + sed |
| `codeward slice <Class.method>` | **Exact bytes of one method** | `sed -n 'X,Yp'` |
| `codeward refs <symbol>` | Ranked reference sites (file:line) | recursive grep |
| `codeward tests-for <target>` | Likely covering tests | guessing |
| `codeward impact [--changed\|<target>]` | Dependents + tests + risk | manual review |
| `codeward preflight <file>` | "What to know before editing this" — see [above](#preflight-blast-radius-context-before-edits) | n/a |
| `codeward budget [target]` | Token hotspot audit + cheaper command recommendations | blind `cat`/`find` exploration |
| `codeward pack <target>` | Budgeted context bundle for a file/dir/symbol/query | dumping many files into context |
| `codeward hotspots [--since 90d]` | Files ranked by churn × dependents — where bugs concentrate | `git log` + `wc -l` + intuition |
| `codeward neighbors <file>` | Files that historically change together with `<file>` | scanning `git log --name-only` by hand |

### Maturity varies by language

Precision depends on the analyzer for the file. `--json` output annotates each row with `analyzer`/`precision`/`confidence`.

| Command | What it does | Best on |
|---|---|---|
| `codeward callgraph <route\|symbol>` | Confidence-ranked flow summary | Python AST → high; tree-sitter → syntax-aware; regex fallback |
| `codeward blame <symbol>` | `git blame` aggregated by author over the symbol's range | Languages with extracted method ranges (Py + tree-sitter) |
| `codeward sdiff [--base <ref>]` | **Symbols** added/removed/changed | Python + tree-sitter languages |
| `codeward api <file-or-dir>` | Public API surface (top-level non-underscore) | Python (`__all__` aware), TypeScript |
| `codeward review [--changed] [--security]` | Pre-commit semantic + heuristic security review | All languages — security checks are pattern-based, not SAST |

### Operations & adapters

- `codeward gain [--global\|--all]` — token savings history (per-repo + global), formatted like `rtk gain`
- `codeward doctor` — environment / hook ordering / index health
- `codeward index` / `codeward watch` — explicit / continuous indexing
- `codeward init [--hook] [--global] [--gemini] [--no-hook-bash] [--no-hook-edit]` — vocabulary + optional hooks
- `codeward init-agent [--force]` — PATH shims for Codex / Aider / shell agents (refuses if RTK detected)
- `codeward hook --agent {claude,cursor,gemini,generic}` — agent hook adapter (stdin → stdout)

### Deferred to RTK when present

`codeward status`, `codeward diff`, `codeward test` defer to `rtk` when RTK is on PATH. Pass `--force` to use the Codeward variant.

## Configuration

Drop `.codeward/config.toml`:

```toml
[index]
ignore_dirs = ["legacy", "vendor"]
extra_test_dirs = ["e2e"]

[[side_effects.custom_rules]]
pattern = '\baudit_log\s*\('
label = "Audit log"
```

Full schema (~12 keys across `[index]`, `[side_effects]`, `[security]`, `[preflight]`): [docs/CONFIG.md](docs/CONFIG.md).

## JSON output

```bash
codeward read --json src/foo.py | jq '.symbols[] | .signature'
codeward refs --json UserService | jq '.references | length'
```

Schema: [docs/JSON_SCHEMA.md](docs/JSON_SCHEMA.md). Rows include `analyzer`/`precision`/`confidence` — Python AST is high-confidence, tree-sitter is syntax-aware, regex fallbacks are explicitly heuristic.

## Agent integrations

| Agent | Native hook? | What `codeward init` gives you |
|---|---|---|
| **Claude Code** | ✅ `PreToolUse` | `--hook` / `--hook --global` writes `~/.claude/settings.json`. Two matchers: `Bash` (rewrite) + `Edit\|Write\|MultiEdit` (preflight) |
| **Gemini CLI** | ✅ `BeforeTool` | `--gemini` writes `~/.gemini/settings.json` (matcher: `run_shell_command`) |
| **Cursor** | ✅ Extension API | None automatic — paste `codeward hook --agent cursor` into a Cursor plugin |
| **Codex** | ❌ no shell hook | Vocabulary only via `~/.codex/AGENTS.md` (written by `init --global`); or `init-agent` for PATH shims |
| **Aider / OpenCode / shell agents** | ❌ no shell hook | Same as Codex — vocabulary + optional `init-agent` shims |

```bash
# Most common combinations
codeward init --hook                          # Claude, project-local
codeward init --hook --global                 # Claude, every repo
codeward init --hook --no-hook-bash           # Claude edit-preflight only (w/ RTK)
codeward init --hook --global --gemini        # Claude + Gemini, global
codeward init --global                        # vocab only (writes CLAUDE/AGENTS/GEMINI.md)
codeward init-agent && export PATH="$PWD/.codeward/bin:$PATH"   # Codex/Aider PATH shims
```

## Case study: refactor on FastAPI

One task, three agents. Full numbers (six Claude task variants, Edit/Write hook trace, Go/gin sessions): [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

**Task:** "find every callsite of `APIRoute.get_route_handler` and produce a refactor plan." Same prompt, same model, same `--max-turns`.

| Agent | Shell cmds | Headline savings |
|---|---|---|
| **Claude** Sonnet 4.6 | 18 → 11 (−39%) | Tool tokens **−49%** (5,524 → 2,837) |
| **Codex** gpt-5.5 | 15 → 12 (−20%) | Output tokens **−18%** (3,649 → 2,990) |
| **Gemini** 3-flash-preview | 44 → 19 (−57%) | Input tokens **−60%** (954k → 386k) |

Each row uses the most representative axis its CLI/SDK exposes. Cost is omitted — token counts are stable, per-token cost depends on model choice and changes monthly.

**On the Gemini number.** Vanilla Gemini ran 44 cmds vs Claude's 18 / Codex's 15 — it was floundering, so −60% reflects how much guidance helps a looping agent. **Codex's −18% is the more conservative real-world expectation.**

**Claude wins without calling `codeward` once** — it reads `CLAUDE.md` and adopts the "scope first, then targeted reads" idiom Codeward teaches. The teaching does the work.

### Per-command compression

Separate measurement: raw shell analogue vs `codeward` output, recorded by `codeward gain`. In a 17-call FastAPI session: **561,579 tokens saved (85.2%)**. A `codeward slice` returning 700 tokens vs `cat`'s 50,000 is a 70× reduction even when turn count is identical — durable per-call savings independent of task length.

### Across six Claude tasks (qualitative)

- **Refactor / find-all-callsites / cross-cutting impact** — wins consistently. **−49% tool tokens (Claude)**, **−18% output (Codex)**, **−60% input (Gemini, with caveat above)**.
- **Cross-language orientation** — Go/gin showed −30% tool tokens; tree-sitter beats regex.
- **Architecture overview / code review** — roughly tie. Per-call savings offset by extra turns.
- **Targeted bug-finding when symbol is known** — **net-negative**. Raw grep+sed with RTK is faster.

## How it composes with RTK

Codeward is heavily inspired by [RTK](https://github.com/rtk-ai/rtk). RTK pioneered the "wrap shell commands and minify output" approach for coding agents — `gain` history, `--json`, the hook-adapter shape Codeward uses for Claude/Cursor/Gemini, deferral semantics. Credit where it's due.

The two sit on different surfaces:

- **RTK** — Bash output compression (`cat`/`grep`/`find`/`git status`/`pytest`). Stable, fast, broad.
- **Codeward** — symbol-level queries the shell can't answer (`slice`/`refs`/`blame`/`sdiff`/`api`/`preflight`).

Codeward's Bash hook orders before RTK's; RTK passes `codeward …` through. Edit/Write hook is on a different matcher; no overlap. `codeward doctor` verifies the ordering.

If you can install only one, install **RTK** first.

## Documentation

- [docs/GUIDE.md](docs/GUIDE.md) — full user / integration guide
- [docs/JSON_SCHEMA.md](docs/JSON_SCHEMA.md) — `--json` schema
- [docs/CONFIG.md](docs/CONFIG.md) — `.codeward/config.toml` reference
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — full A/B numbers
- [docs/PLAN.md](docs/PLAN.md) — roadmap
- [CHANGELOG.md](CHANGELOG.md) — release history

## License

MIT. See [LICENSE](LICENSE).
