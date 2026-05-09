# Codeward roadmap

Tracks shipped features and forward direction. For per-release detail see `CHANGELOG.md`.

## Position

Codeward is the **semantic-query layer** for coding agents. It composes with [RTK](https://github.com/rtk-ai/rtk) (which owns the Bash output-compression layer) — different surfaces, no clash.

## Shipped (v0.4.0)

### Semantic queries
- `codeward map` — repo orientation
- `codeward read <file>` — symbols, dependents, tests, side effects (with optional `--flow` for compact method bodies)
- `codeward search <q>` — index-grouped search
- `codeward symbol <name>` — definitions + confidence-ranked callers + tests
- `codeward callgraph <route|symbol>` — confidence-ranked flow summary
- `codeward tests-for <target>` — likely covering tests
- `codeward impact [--changed | <target>]` — dependents + tests + risk for changed files
- `codeward review [--changed] [--security]` — pre-commit semantic review

### Symbol-level commands (Phase B)
- `codeward slice <Class.method>` — exact bytes when AST/tree-sitter line ranges exist; replaces `sed -n 'X,Yp'`
- `codeward refs <symbol>` — confidence-ranked reference sites, separate from definitions
- `codeward blame <symbol>` — `git blame` aggregated by author over the symbol's range
- `codeward sdiff [--base <ref>]` — semantic diff: symbols added/removed/changed
- `codeward api <file-or-dir>` — public API surface only

### Edit-time hooks (Phase C)
- `codeward preflight <file>` — context an editor should see before changing a file
- `PreToolUse` on `Edit|Write|MultiEdit` — auto-injects preflight via `additionalContext`

### Performance / tooling (Phase D + 0)
- `codeward watch` — `watchdog`-based incremental SQLite re-indexer
- `--json` on every read-only command (stable schema in `docs/JSON_SCHEMA.md`)
- `.codeward/config.toml` — per-repo ignore dirs, test dirs, custom side-effect rules
- Tree-sitter language support (Go, Rust, TS, JS, Java, Ruby, PHP, C#) in the default install
- Analyzer metadata on indexed files/symbols and JSON rows: `analyzer`, `precision`, `confidence`

### Agent integrations
- Claude Code: native `PreToolUse` hook (Bash + Edit/Write)
- Cursor / Gemini CLI / generic: `codeward hook --agent <name>`
- Codex / OpenCode / shell-based agents: PATH shims via `codeward init-agent`
- All agents: `CLAUDE.md` + `AGENTS.md` vocabulary written by `codeward init`

## Forward direction

### Likely next (high leverage, no RTK overlap)
- **MCP server** — single integration point for Cursor / Continue / Zed / Cline / Goose / Claude Desktop. Codeward's commands as MCP tools.
- **Symbol-aware diff in PR comments** — GitHub Action wrapping `codeward sdiff` and `review --security`.
- **Incremental tree-sitter parses** in watch mode (currently full-file reanalyze on each event).

### Maybe (waiting on usage signal)
- LSP-backed mode for exact references and call-graphs (currently Python AST high-confidence, tree-sitter syntax-aware, regex heuristic fallback).
- VS Code extension (heavier; defer until terminal CLI is rock-solid).
- Multi-repo workspace support (currently per-repo).

### Won't do
- Bash output compression (RTK's lane).
- File-bundling for LLMs (Aider repomap / repomix / llm-context already cover this).
- Structural search/rewrite (ast-grep / comby cover this).

## Architecture summary

- **Default install includes precision tooling.** `pip install codeward` includes tree-sitter grammars and `watchdog`.
- **Optional `[full]`** remains as a backward-compatible empty alias.
- **No daemon required.** `codeward watch` is foreground; users wrap in systemd/launchd if they want it backgrounded.
- **No remote services.** Everything is local; `.codeward/index.sqlite` per repo.
