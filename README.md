<p align="center">
  <img src="./assets/logo.png" alt="Codeward logo — magnifying glass over a hierarchical syntax tree" width="180">
</p>

<h1 align="center">Codeward</h1>

<p align="center"><strong>Symbol-level codebase intelligence for coding agents.</strong></p>

Codeward gives your agent commands the shell can't: "where is this defined?", "who calls it?", "which tests cover it?", "what changed at the symbol level?", "what would break if I edit this file?". It indexes your repo with tree-sitter / Python AST and answers in compact, structured form.

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

The headline feature — the one thing in this category I haven't seen elsewhere — is **[preflight context injection](#preflight-blast-radius-context-before-edits)**: dependents, likely tests, side-effects, and blast radius pushed into the model's context *before* an Edit/Write tool call.

Codeward doesn't replace [RTK](https://github.com/rtk-ai/rtk). They sit on different surfaces and most setups want both — see [How it composes with RTK](#how-it-composes-with-rtk) for the layering.

## When not to use this

Codeward is the wrong tool when:

- **You're chasing one specific bug and already know the symbol name.** Raw `grep`/`sed` (with RTK compressing the output) gets you there faster than building/loading an index.
- **The repo is small (<500 LOC, a handful of files).** The index overhead doesn't pay off.
- **Your agent doesn't read `CLAUDE.md`/`AGENTS.md`/`GEMINI.md` and you haven't installed the hooks.** Without vocabulary teaching or a hook surface, Codeward sits unused.

If you only have budget for one tool: install **RTK** for general-purpose Bash compression. Add Codeward when refactor planning, cross-file impact analysis, or pre-edit blast-radius awareness becomes worth its weight.

## Project status

Codeward is a v0 (0.4.x) **from-source install**. What that means concretely:

- **Solid:** the `core` commands listed below (`map`, `read`, `search`, `symbol`, `slice`, `refs`, `tests-for`, `impact`, `preflight`), the SQLite index with mtime invalidation, the Claude / Gemini hook adapters, the `--json` schema, the `gain` history.
- **Maturity varies by language:** `callgraph`, `blame`, `sdiff`, `api`, `review --security`. These work, but precision differs across the eight supported languages — see the [Commands](#commands) table for the maturity tag on each.
- **Not on PyPI yet.** Install from source until v0.5. The package layout, CLI surface, and JSON schema are stable across `0.4.x`.

## Install

```bash
git clone https://github.com/osirishorus/codeward.git
cd codeward
pip install -e .
```

Or with pipx (recommended — gives you a global `codeward` command):

```bash
git clone https://github.com/osirishorus/codeward.git
pipx install --editable ./codeward
```

Python ≥ 3.11 required. Tree-sitter grammars (Go, Rust, TS/JS, Java, Ruby, PHP, C#) and `watchdog` are pulled in by default.

When Codeward lands on PyPI this becomes `pipx install codeward` directly. Watch [releases](https://github.com/osirishorus/codeward/releases).

## Quick start

Inside any repository:

```bash
codeward init      # writes CLAUDE.md + AGENTS.md vocabulary (no hooks)
codeward map       # repo overview — auto-builds the index on first run
codeward doctor    # verify environment
```

That's it. Your agent reads `CLAUDE.md`/`AGENTS.md` and starts using `codeward` commands when they fit.

> **You don't need to run `codeward index` manually.** The first read-only command in a repo (`map`, `read`, `symbol`, …) walks the codebase and writes `.codeward/index.sqlite`. Subsequent commands load from that cache, with mtime-based invalidation.

When you'd run `codeward index` explicitly: pre-warming a large repo before an agent session, CI setup scripts, or as a flush after large file rewrites. For long sessions, prefer `codeward watch` — a foreground re-indexer that keeps the SQLite cache hot via file events.

## Optional: hook integration

```bash
codeward init --hook                       # both Bash + Edit/Write hooks (project-local)
codeward init --hook --global              # also wire ~/.claude/settings.json (global)
codeward init --hook --no-hook-edit        # Bash rewrite only — skip Edit preflight
codeward init --hook --no-hook-bash        # Edit/Write preflight only — skip Bash rewrite
                                           # (recommended when RTK already owns Bash)
```

This installs `PreToolUse` entries in Claude Code, controlled independently:

- **`matcher: "Bash"`** — rewrites `cat foo.py` → `codeward read foo.py` and tracks savings. Inserted *before* RTK's Bash entry; RTK passes `codeward …` through unchanged. Skip with `--no-hook-bash` if you'd rather RTK own the Bash surface alone.
- **`matcher: "Edit|Write|MultiEdit"`** — runs `codeward preflight <file>` and injects dependents/tests/side-effects/blast-radius via `additionalContext` *before* the edit happens. RTK doesn't touch this surface. Skip with `--no-hook-edit` if you don't want pre-edit context.

The two hooks are independent. **The most common setup if you already use RTK:** `codeward init --hook --no-hook-bash` — preflight context on edits, no overlap with RTK's Bash compression.

`codeward doctor` verifies (a) which hooks are installed, (b) that the Bash hook (if installed) is ordered **before** `rtk hook claude` in `~/.claude/settings.json` — if RTK ran first, it would compress `cat`/`grep` output before Codeward got a chance to rewrite them, defeating both layers — and (c) that the index is fresh.

## Preflight: blast-radius context before edits

When the Edit/Write hook is installed, Codeward injects a compact "what to know before changing this" payload as `additionalContext` *before* the edit reaches the model. Real example, captured from an actual edit to `fastapi/routing.py`:

```text
# Codeward preflight: fastapi/routing.py
  language=Python, lines=4956, symbols=47, blast_radius=HIGH
  dependents (14): docs_src/custom_request_and_route/tutorial001_an_py310.py,
                   tests/test_router_redirect_slashes.py, tests/test_route_scope.py, …
  likely tests: tests/test_custom_route_class.py, tests/test_route_scope.py, …
  side effects: Network call (httpx, line 142), DB write (sqlalchemy, line 891)
  security flags: 0
```

**149 tokens, injected once, on the right surface** — fires on `Edit`/`Write`/`MultiEdit`, not on `Grep` or `Read`. The agent receives this as part of its context for the edit and adapts: smaller patches on HIGH blast-radius files, awareness of which dependents/tests to consider, awareness of side effects (network, DB write, queue publish, etc.) the file actively performs.

What goes in the payload is configurable via `.codeward/config.toml` (custom side-effect rules, ignored directories, extra test paths). The design constraint is signal-to-noise: enough to inform, not enough to drown the rest of the prompt.

Verified end-to-end on the FastAPI repo: hook fires once per Edit tool call, agent acknowledges the context, edit content is identical to the no-hook baseline (the agent doesn't get distracted — it gets informed). Full transcript in [docs/BENCHMARKS.md#editwrite-benchmark](docs/BENCHMARKS.md#editwrite-benchmark--preflight-hook-in-action).

## Commands

### Core (load-bearing, exercised in benchmarks)

All read-only commands support `--json` with a stable schema.

| Command | What it does | Replaces |
|---|---|---|
| `codeward map` | Repo overview: language, important files, suggested next steps | `find . -maxdepth 3 -type f` |
| `codeward read <file>` | Symbols + signatures + dependents + tests + side effects (`--flow` adds compact method bodies) | `cat <file>` |
| `codeward search <query>` | Index-grouped search hits | `grep -rn <query>` |
| `codeward symbol <name>` | Definition + confidence-ranked callers + tests | grep + sed |
| `codeward slice <Class.method>` | **Exact bytes of one method** when AST/tree-sitter ranges exist | `sed -n 'X,Yp'` |
| `codeward refs <symbol>` | Confidence-ranked reference sites (file:line) | recursive grep |
| `codeward tests-for <target>` | Likely covering tests | guessing |
| `codeward impact [--changed\|<target>]` | Dependents + tests + risk for changed files | manual review |
| `codeward preflight <file>` | Compact "what to know before editing this" — see [section above](#preflight-blast-radius-context-before-edits) | n/a |

### Maturity varies by language

These commands work, but precision depends on which analyzer fires for the file in question. The `doctor` command surfaces analyzer coverage; each command annotates rows with `analyzer`/`precision`/`confidence` in `--json` output.

| Command | What it does | Best on | Falls back to |
|---|---|---|---|
| `codeward callgraph <route\|symbol>` | Confidence-ranked flow summary | Python AST: high precision | tree-sitter (TS/Go/Rust/etc.): syntax-aware; regex: heuristic |
| `codeward blame <symbol>` | `git blame` aggregated by author over the symbol's range | Languages with extracted method ranges (Py + tree-sitter set) | File-level blame |
| `codeward sdiff [--base <ref>]` | **Symbols** added/removed/changed (not raw lines) | Python + tree-sitter languages | Raw diff with file-level summary |
| `codeward api <file-or-dir>` | Public API surface (top-level non-underscore) | Python (`__all__` aware), TypeScript | Best-effort symbol enumeration |
| `codeward review [--changed] [--security]` | Pre-commit semantic + pattern-based security review | All languages, but security checks are heuristic — not a SAST replacement | — |

### Operations

- `codeward gain [--global\|--all]` — token savings history (per-repo and global), formatted like `rtk gain`
- `codeward doctor` — environment / hook ordering / index health check
- `codeward index [--output PATH]` — explicitly persist `.codeward/index.sqlite` (rarely needed; auto-builds)
- `codeward watch [--debounce 0.5]` — foreground re-indexer; keeps SQLite hot via file events

### Install / adapters

- `codeward init [--hook] [--global] [--gemini] [--no-hook-bash] [--no-hook-edit]` — vocabulary + optional hooks
- `codeward init-agent [--force]` — PATH shims for Codex / Aider / shell agents (refuses by default if RTK detected)
- `codeward hook --agent {claude,cursor,gemini,generic}` — agent hook adapter (stdin → stdout)

### Deferred to RTK when present

`codeward status`, `codeward diff`, `codeward test` defer to `rtk` when RTK is on PATH (their core competency). Pass `--force` to use the Codeward variant.

## Per-repo configuration

Drop `.codeward/config.toml` for custom rules:

```toml
[index]
ignore_dirs = ["legacy", "vendor"]
extra_test_dirs = ["e2e"]

[[side_effects.custom_rules]]
pattern = '\baudit_log\s*\('
label = "Audit log"
```

The full schema (currently ~12 keys across `[index]`, `[side_effects]`, `[security]`, `[preflight]`): [docs/CONFIG.md](docs/CONFIG.md).

## JSON output

Every read-only command supports `--json` with a stable schema:

```bash
codeward read --json src/foo.py | jq '.symbols[] | .signature'
codeward refs --json UserService | jq '.references | length'
```

Schema: [docs/JSON_SCHEMA.md](docs/JSON_SCHEMA.md). Symbol and reference rows include `analyzer`, `precision`, and `confidence` — Python AST is high-confidence; tree-sitter languages are syntax-aware; regex fallbacks are explicitly labeled heuristic.

## Agent integrations

The four major agents have very different hook architectures. Codeward meets each in its native shape:

| Agent | Native hook system? | What `codeward init` gives you | How it actually fires |
|---|---|---|---|
| **Claude Code** | ✅ `PreToolUse` array with matchers | `--hook` (project) / `--hook --global` writes `~/.claude/settings.json` automatically. Two matchers: `Bash` (rewrite) and `Edit\|Write\|MultiEdit` (preflight) | Claude pipes tool-call JSON to the hook script, which runs `codeward hook --agent claude` and returns `updatedInput` (Bash) or `additionalContext` (Edit/Write) |
| **Gemini CLI** | ✅ `BeforeTool` array with matchers | `--gemini` writes `~/.gemini/settings.json` automatically (matcher: `run_shell_command`) | Gemini invokes `codeward hook --agent gemini` on every shell call; gets back `updated_input.command` |
| **Cursor** | ✅ Extension API | None automatic — paste `codeward hook --agent cursor` into a Cursor plugin manually | Cursor extension sends JSON, gets `permission` + `updated_input` |
| **Codex (OpenAI)** | ❌ No shell hook | No hook; relies on vocabulary only. `~/.codex/AGENTS.md` is written by `codeward init --global`; Codex chooses to invoke `codeward` directly when the task fits | Or: `codeward init-agent` writes PATH shims under `.codeward/bin/` to force-rewrite `cat`/`grep`/etc. (refuses if RTK detected — `--force` to override) |
| **Aider / OpenCode / shell agents** | ❌ No hook | Same as Codex — vocabulary in `AGENTS.md` + optional PATH shims via `init-agent` | OpenCode plugin can call `codeward run --dry-run --shell-command "<cmd>"` and mutate the command if rewritten |
| **Custom wrappers** | varies | n/a | `codeward hook --agent generic` returns `{updatedInput: {command: …}}` for any wrapper that consumes that shape |

### Install matrix

```bash
# Claude Code, project-local
codeward init --hook

# Claude Code, globally (every repo)
codeward init --hook --global

# Claude Code, edit-preflight only (recommended when RTK already owns Bash)
codeward init --hook --no-hook-bash

# Gemini CLI, globally
codeward init --hook --gemini

# Everything everywhere (Claude global + Gemini global + global memory files)
codeward init --hook --global --gemini

# Codex / Aider / shell agents — vocabulary only (no shell-hook surface available)
codeward init --global              # writes ~/.codex/AGENTS.md, ~/.gemini/GEMINI.md too

# Codex / Aider — force-rewrite via PATH shims (alternative when vocab isn't enough)
codeward init-agent
export PATH="$PWD/.codeward/bin:$PATH"
```

## Case study: refactor on FastAPI

One task, one repo, three agents — a case study, not a benchmark suite. Full numbers (six Claude task variants, Edit/Write hook trace, Go/gin and Python compression sessions) in [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

**Task:** "find every callsite of `APIRoute.get_route_handler` and produce a refactor plan." Same prompt, same model, same `--max-turns`. Baseline = clean clone. Codeward = `init` + `index`.

| Agent | Shell cmds | Headline savings |
|---|---|---|
| **Claude** Sonnet 4.6 | 18 → 11 (−39%) | Tool tokens **−49%** (5,524 → 2,837) |
| **Codex** gpt-5 | 15 → 12 (−20%) | Output tokens **−18%** (3,649 → 2,990) |
| **Gemini** 3-flash-preview | 44 → 19 (−57%) | Input tokens **−60%** (954k → 386k) |

Each row uses the most representative axis its CLI/SDK exposes — Anthropic surfaces `tool_tokens` but not input/output split; Codex/Gemini surface input/output but not tool-token isolation.

**On the Gemini number.** Its baseline ran 44 commands vs Claude's 18 / Codex's 15 — vanilla Gemini was floundering on this task, so the −60% reflects how much guidance helps a looping agent. **Codex's −18% is the more conservative real-world expectation.** Both are real wins; different shapes.

**Claude wins without calling `codeward` once** — it reads `CLAUDE.md` and adopts the "scope first, then targeted reads" idiom Codeward teaches. The teaching does the work.

### Per-command compression — separate from the case study

Whenever an agent invokes `codeward` directly, the output is measured against the raw shell analogue (`cat fastapi/routing.py` ≈ 200KB vs `codeward slice APIRoute.get_route_handler` ≈ 700 tokens). Recorded by `codeward gain`. In a 17-call FastAPI planning session: **561,579 tokens saved (85.2%) compared to the raw shell equivalents** — durable per-call compression, independent of how many turns the task takes.

For long, context-budget-constrained sessions, per-command compression matters more than headline turn-count numbers. A single `codeward slice` returning 700 tokens vs `cat`'s 50,000 is a 70× context-pressure reduction even when turn count is identical.

### Where the case study extrapolates well, and where it doesn't

This is one task. With that caveat very firmly stated, the qualitative observations from the broader six-task Claude run (in BENCHMARKS.md) are:

- **Refactor / find-all-callsites / cross-cutting impact** — Codeward wins consistently. Up to **−49% tool tokens (Claude)**, **−18% output (Codex)**, **−60% input (Gemini, with the floundering caveat above)**.
- **Cross-language orientation** — Go/gin showed −30% tool tokens; tree-sitter delivers accurate symbol extraction where regex would fail.
- **Architecture overviews / code review** — Roughly tie. The agent uses lots of `codeward`, but extra turns offset per-call savings.
- **Targeted bug-finding when the agent already knows the symbol** — Codeward is **net-negative**. Raw grep+sed with RTK compression is faster.

Cost is intentionally not in any of these tables: token counts are stable across this document's lifetime; per-token cost depends on which model you choose and changes monthly. The unit that survives is the unit we report.

## How it composes with RTK

Codeward is heavily inspired by [RTK (Rust Token Killer)](https://github.com/rtk-ai/rtk). RTK pioneered the "wrap your agent's shell commands and minify the output" approach — single Rust binary, transparent hook, real measurable token reductions on every `cat`/`grep`/`git status`. Several design choices Codeward inherits (`gain` history, `--json` output, hook-adapter shape for Claude/Cursor/Gemini, deferral semantics) are RTK's. Credit where it's due.

The two tools sit on different surfaces:

- **RTK** — Bash output compression. Runs `cat`/`grep`/`find`/`git status`/`pytest` and minifies what comes back. Stable, fast, broad.
- **Codeward** — symbol-level semantic queries the shell can't answer. `slice`, `refs`, `blame`, `sdiff`, `api`, `preflight`. A compressor only sees the bytes a tool printed; Codeward parses your repo and answers structural questions about it.

The Bash hook (when enabled) orders before RTK's. RTK passes `codeward …` through unchanged. The Edit/Write hook is on a different matcher entirely (`Edit|Write|MultiEdit`) — RTK doesn't touch it. No way for them to clash; `codeward doctor` verifies the ordering.

If you only have budget for one tool: **install RTK first.** Add Codeward when you want symbol-level queries or pre-edit blast-radius context.

## Documentation

- [docs/GUIDE.md](docs/GUIDE.md) — full user / integration guide
- [docs/JSON_SCHEMA.md](docs/JSON_SCHEMA.md) — `--json` output schema
- [docs/CONFIG.md](docs/CONFIG.md) — `.codeward/config.toml` reference
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — full A/B numbers (six tasks on Claude + cross-agent + Edit/Write trace)
- [docs/PLAN.md](docs/PLAN.md) — roadmap
- [CHANGELOG.md](CHANGELOG.md) — release history

## Development

```bash
git clone https://github.com/osirishorus/codeward.git
cd codeward
python3 -m pip install -e .
python3 -m pytest tests/ -q
```

## License

MIT. See [LICENSE](LICENSE).
