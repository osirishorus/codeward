<p align="center">
  <img src="./assets/logo.png" alt="Codeward logo — magnifying glass over a hierarchical syntax tree" width="180">
</p>

<h1 align="center">Codeward</h1>

<p align="center"><strong>Semantic codebase intelligence for coding agents.</strong></p>

Codeward is the layer above raw shell that lets your coding agent ask *meaningful* questions about a codebase: "where is this defined?", "what depends on it?", "what changed at the symbol level?". It composes cleanly with [RTK](https://github.com/rtk-ai/rtk) — RTK compresses Bash output (`cat`, `grep`, `git status`); Codeward answers questions RTK can't.

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

## Install

> **Note:** Codeward isn't on PyPI yet. For now, install from source:

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

Python ≥ 3.11 required. Tree-sitter grammars and `watchdog` are pulled in by default.

Once Codeward lands on PyPI, this will become `pip install codeward` / `pipx install codeward` directly. Watch [releases](https://github.com/osirishorus/codeward/releases) for the announcement.

## Quick start

Inside any repository:

```bash
codeward init      # writes CLAUDE.md + AGENTS.md vocabulary (optional, no hooks)
codeward map       # repo overview — auto-builds the index on first run
codeward doctor    # verify environment
```

That's it. Your agent will read CLAUDE.md/AGENTS.md and start using `codeward` commands when they fit.

> **You don't need to run `codeward index` manually.** The first read-only command in a repo (`map`, `read`, `symbol`, etc.) automatically walks the codebase and writes `.codeward/index.sqlite`. Subsequent commands load from that cache, with mtime-based invalidation when source files change.

When you'd run `codeward index` explicitly:

- **Pre-warming a large repo** before an agent session, so the agent's first command is instant rather than waiting on the build (Django ~9s first run; most repos under 1s).
- **CI setup scripts** that bake the index into a workspace.
- **Background indexing** — use `codeward watch` instead, which holds the index hot and incrementally updates on file events.

`codeward init` is purely for writing vocabulary docs (and optionally hooks). It does not touch the index.

## Optional: hook integration

```bash
codeward init --hook                       # both Bash + Edit/Write hooks (project-local)
codeward init --hook --global              # also wire ~/.claude/settings.json (global)
codeward init --hook --no-hook-edit        # Bash rewrite only — skip Edit preflight
codeward init --hook --no-hook-bash        # Edit/Write preflight only — skip Bash rewrite
                                           # (recommended when RTK already owns Bash)
```

This installs `PreToolUse` entries in Claude Code, controlled independently:

- **`matcher: "Bash"`** — rewrites `cat foo.py` → `codeward read foo.py` and tracks savings. Inserted *before* RTK's Bash entry; RTK passes `codeward ...` through unchanged. Skip with `--no-hook-bash` if you want RTK to handle the Bash surface alone.
- **`matcher: "Edit|Write|MultiEdit"`** — runs `codeward preflight <file>` and injects dependents/tests/side-effects/security flags via `additionalContext` *before* the edit happens. RTK doesn't touch this surface — no clash possible. Skip with `--no-hook-edit` if you don't want pre-edit context.

The two hooks are independent. **The most common setup if you already use RTK:** `codeward init --hook --no-hook-bash` — gives you preflight context on edits without touching Bash compression (RTK keeps that lane).

Run `codeward doctor` to verify ordering and which hooks are installed.

## Commands

### Read-only (all support `--json`)

| Command | What it does | Replaces |
|---|---|---|
| `codeward map` | Repo overview: language, important files, suggested next steps | `find . -maxdepth 3 -type f` |
| `codeward read <file>` | Symbols + signatures + dependents + tests + side effects (`--flow` adds compact method bodies) | `cat <file>` |
| `codeward search <query>` | Index-grouped search hits | `grep -rn <query>` |
| `codeward symbol <name>` | Definition + confidence-ranked callers + tests | grep + sed |
| `codeward callgraph <route\|symbol>` | Confidence-ranked flow summary across files | manual tracing |
| `codeward tests-for <target>` | Likely covering tests | guessing |
| `codeward impact [--changed\|<target>]` | Dependents + tests + risk for changed files | manual review |
| `codeward review [--changed] [--security]` | Pre-commit semantic + security review | linters |
| `codeward slice <Class.method>` | **Exact bytes of one method** when AST/tree-sitter ranges exist | `sed -n 'X,Yp'` |
| `codeward refs <symbol>` | Confidence-ranked reference sites (file:line) | recursive grep |
| `codeward blame <symbol>` | `git blame` aggregated by author | `git blame -L X,Y` |
| `codeward sdiff [--base <ref>]` | **Symbols** added/removed/changed (not raw lines) | `git diff` |
| `codeward api <file-or-dir>` | Public API surface (top-level non-underscore) | grep + `__all__` |
| `codeward preflight <file>` | Compact "what to know before editing this" | n/a |
| `codeward gain` | Token savings history | n/a |
| `codeward doctor` | Environment / hook / index health check | n/a |

### Mutating / control

- `codeward init [--hook] [--global] [--no-hook-edit]` — install vocabulary + optional hooks
- `codeward init-agent [--force]` — install PATH shims for Codex / Aider / shell agents
- `codeward index [--output PATH]` — persist `.codeward/index.sqlite`
- `codeward watch [--debounce 0.5]` — foreground re-indexer; keeps SQLite hot
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

Full schema: [docs/CONFIG.md](docs/CONFIG.md).

## JSON output

Every read-only command supports `--json` with a stable schema:

```bash
codeward read --json src/foo.py | jq '.symbols[] | .signature'
codeward refs --json UserService | jq '.references | length'
```

Schema: [docs/JSON_SCHEMA.md](docs/JSON_SCHEMA.md).

Symbol and reference rows include `analyzer`, `precision`, and `confidence`.
Python AST references are high-confidence; tree-sitter languages are syntax-aware;
regex fallbacks are explicitly labeled heuristic.

## Agent integrations

The four major agents have very different hook architectures. Codeward meets each one in its native shape:

| Agent | Native hook system? | What `codeward init` gives you | How it actually fires |
|---|---|---|---|
| **Claude Code** | ✅ `PreToolUse` array with matchers | `--hook` (project) / `--hook --global` writes `~/.claude/settings.json` automatically. Two matchers: `Bash` (rewrite) and `Edit\|Write\|MultiEdit` (preflight) | Claude pipes tool-call JSON to the hook script, which runs `codeward hook --agent claude` and returns `updatedInput` (Bash) or `additionalContext` (Edit/Write) |
| **Gemini CLI** | ✅ `BeforeTool` array with matchers | `--gemini` writes `~/.gemini/settings.json` automatically (matcher: `run_shell_command`) | Gemini invokes `codeward hook --agent gemini` on every shell call; gets back `updated_input.command` |
| **Cursor** | ✅ Extension API | None automatic — paste `codeward hook --agent cursor` into a Cursor plugin manually | Cursor extension sends JSON, gets `permission` + `updated_input` |
| **Codex (OpenAI)** | ❌ No shell hook | No hook; relies on vocabulary only. `~/.codex/AGENTS.md` is written by `codeward init --global` and Codex chooses to invoke `codeward` directly when the task fits | Or: `codeward init-agent` writes PATH shims under `.codeward/bin/` to force-rewrite `cat`/`grep`/etc. (refuses by default if RTK detected — `--force` to override) |
| **Aider / OpenCode / shell agents** | ❌ No hook | Same as Codex — vocabulary in `AGENTS.md` + optional PATH shims via `init-agent` | OpenCode plugin can call `codeward run --dry-run --shell-command "<cmd>"` and mutate the command if rewritten |
| **Custom wrappers** | varies | n/a | `codeward hook --agent generic` returns `{updatedInput: {command: ...}}` for any wrapper that consumes that shape |

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

## How it composes with RTK

**Codeward is heavily inspired by [RTK (Rust Token Killer)](https://github.com/rtk-ai/rtk).** RTK pioneered the "wrap your shell commands and minify their output" approach for coding agents — single Rust binary, transparent hook, real measurable token reductions on every `cat`/`grep`/`git status`. Most of the design choices Codeward inherits — `gain` history, `--json` output, the hook adapter pattern for Claude/Cursor/Gemini, deferral semantics — are RTK's.

Codeward fills the layer above. The two compose cleanly:

- **RTK** owns the Bash output-compression layer — `cat`, `grep`, `find`, `git status`, `pytest`. RTK runs the command and minifies its output. Mature, fast, broadly applicable.
- **Codeward** owns the semantic-query layer — `slice`, `refs`, `blame`, `sdiff`, `api`, `preflight`. These answer questions RTK can't (a compressor only sees the bytes a tool printed).

The Bash hook (when enabled) orders before RTK's. RTK passes `codeward ...` through unchanged. The Edit/Write hook is on a different matcher entirely (`Edit|Write|MultiEdit`) — RTK doesn't touch it. No way for them to clash.

If you only have one of the two installed, pick **RTK** for general-purpose token compression. Add Codeward when you want symbol-level semantic queries (`refs`, `slice`, `blame`, `sdiff`) or pre-edit context injection.

## Benchmarks

Real A/B numbers across three agents (Claude Sonnet 4.6, Codex gpt-5, Gemini 3-flash-preview) in [docs/BENCHMARKS.md](docs/BENCHMARKS.md). Tokens-only — costs intentionally omitted.

**Refactor task on fastapi (find every callsite of a method) — the win-shaped workflow:**

| Agent | Tool calls | Input tokens | Output tokens | API calls |
|---|---:|---:|---:|---:|
| Claude baseline | 18 | — | — | 19 turns |
| Claude + codeward | 11 (−39%) | — | — | 12 turns (−37%) |
| Codex baseline | 15 | 173,063 | 3,649 | — |
| Codex + codeward | 12 (−20%) | 171,087 | 2,990 (**−18%**) | — |
| Gemini baseline | 44 | 954,463 | 3,584 | 48 |
| Gemini + codeward | 19 (**−57%**) | 385,675 (**−60%**) | 2,610 (−27%) | 21 (−56%) |

**Per-command compression (`codeward gain`):** 80%+ savings consistently. **561k tokens saved across 17 calls in one fastapi planning session.**

**Edit/Write hook:** preflight injects dependents/tests/blast-radius before each edit (~149 tokens of context). Verified to fire on the right surface, agent acknowledges and adapts.

**Where Codeward wins:**
- Refactor / rename / find-all-callsites
- Cross-language orientation (Go/Rust/TS/Java/Ruby/PHP/C# via tree-sitter)
- Long sessions with compounding context pressure (per-call 80%+ compression)

**Where Codeward is neutral:** architecture overviews, code review.

**Where Codeward is net-negative:** targeted bug-finding where the agent already knows the symbol name (use raw shell + RTK).

## Documentation

- [docs/GUIDE.md](docs/GUIDE.md) — full user/integration guide
- [docs/JSON_SCHEMA.md](docs/JSON_SCHEMA.md) — `--json` output schema
- [docs/CONFIG.md](docs/CONFIG.md) — `.codeward/config.toml` reference
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — real-world A/B numbers
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
