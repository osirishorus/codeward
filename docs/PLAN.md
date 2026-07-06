# Codeward roadmap

Tracks shipped surface and forward direction. For per-release detail see `CHANGELOG.md`.

## Position

Codeward is the **semantic-query layer** for coding agents. It composes with [RTK](https://github.com/rtk-ai/rtk) (which owns the Bash output-compression layer) — different surfaces, no clash.

## Shipped (v0.5.x)

### Repo queries
- `codeward map` — repo orientation
- `codeward read <file>` — symbols, dependents, tests, side effects (`--flow` adds compact method bodies)
- `codeward search <q>` — index-grouped search
- `codeward symbol <name>` — definition + confidence-ranked callers + tests (fuzzy fallback included)
- `codeward callgraph <route|symbol>` — confidence-ranked flow summary; accepts route patterns directly
- `codeward tests-for <target>` — likely covering tests
- `codeward impact [--changed | <target>]` — dependents + tests + risk
- `codeward review [--changed] [--security]` — pre-commit semantic + heuristic security review
- `codeward api <file-or-dir>` — public API surface only
- `codeward routes [target] [--method] [--filter] [--include-tests]` — framework-aware URL → handler mapping across FastAPI, Flask, Django, Express, NestJS, Spring, Gin, Actix, Rails, Laravel, ASP.NET Core

### Symbol-level
- `codeward slice <Class.method>` — exact bytes via AST/tree-sitter ranges
- `codeward refs <symbol>` — confidence-ranked reference sites (column-aware filtering)
- `codeward blame <symbol>` — `git blame` aggregated by author over the symbol's range
- `codeward sdiff [--base <ref>]` — semantic diff: symbols added/removed/changed

### Edit-time hooks
- `codeward preflight <file>` — context an editor should see before changing a file (routes/dependents/tests/side-effects/blast-radius)
- Claude `PreToolUse` on `Edit|Write|MultiEdit` — auto-injects preflight
- Codex `PreToolUse` on `^apply_patch$` — same response shape, edit-only
- Gemini `BeforeTool` on `run_shell_command`

### Git-history awareness
- `codeward hotspots [--since 90d] [--top N]` — files ranked by churn × dependents
- `codeward neighbors <file>` — files that historically change together
- `codeward owners [target | --changed]` — suggest reviewers from blame over the change set + its dependents
- `codeward pack` / `diff-pack` include co-change neighbors
- `codeward impact` flags high-churn files as hotspots

### Cross-file reasoning
- `codeward affected [--changed | <target>] [--depth N]` — transitive blast radius of a change + the minimal tests to run (CI test-selection)
- `codeward why <fileA> <fileB>` — shortest import/dependency path between two files
- `codeward dead [target]` — top-level symbols with zero external references (candidate dead code), confidence-gated
- `codeward pr-report [--base <ref>] [--security]` — GitHub-flavored PR report combining symbol-level `sdiff`, review findings, affected files, and minimal tests
- Composite GitHub Action — runs `pr-report`, writes the step summary, and optionally posts/updates a sticky PR comment

### Token-budget bundling
- `codeward budget [target]` — token cost audit + cheaper command recommendations
- `codeward pack <target> [--max-tokens]` — budgeted context bundle
- `codeward diff-pack [--changed] [--base]` — budgeted changed-code bundle

### MCP server
- `codeward mcp [--cwd <path>]` — stdio MCP server. One config entry exposes every read-only command to Claude Desktop, Cursor, Continue, Zed, Cline, Goose, Windsurf, ChatGPT Desktop.
- Optional dep: `pip install 'codeward[mcp]'`

### Language coverage
- 17 languages: Python (AST), Go, Rust, TS/JS, Java, Ruby, PHP, C#, C, C++, Kotlin, Swift, Scala, Bash, Lua, Elixir (tree-sitter)
- Each grammar loads lazily — missing wheels degrade only that language
- LSP-backed precision for `refs` and `symbol` is opt-in for Python, TypeScript/JavaScript, Go, and Rust

### Distribution
- `pipx install codeward` / `pip install codeward` (PyPI)
- `npx codeward` (npm wrapper bootstraps via pipx/pip)
- Native hooks for Claude / Codex / Gemini; MCP for the rest

### Watch mode
- `codeward watch [--debounce-ms N] [--stats]` — debounced/coalesced reindex batches, mtime+size unchanged-file skips, and incremental tree-sitter reparses with a bounded parse cache

## Forward direction

### Likely next (no RTK overlap)
- Editor/CI polish around existing semantic commands.

### Maybe
- VS Code extension (heavier; defer until terminal CLI is rock-solid).
- Multi-repo workspace support (currently per-repo).

### Won't do
- Bash output compression (RTK's lane).
- File-bundling for LLMs (Aider repomap / repomix / llm-context already cover this).
- Structural search/rewrite (ast-grep / comby cover this).

## Architecture summary

- **Tree-sitter + Python AST.** Default install pulls in 16 tree-sitter grammars and `watchdog`.
- **SQLite index** at `.codeward/index.sqlite` per repo; mtime invalidation.
- **No daemon required.** `codeward watch` is foreground; users wrap in systemd/launchd if needed.
- **No remote services.** Everything is local.
