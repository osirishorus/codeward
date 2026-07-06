# Codeward Guide

How to install Codeward, wire it into a coding agent, and verify the savings. For an overview of what each command does, see the project [README](../README.md).

## 1. Mental model

Codeward replaces noisy codebase exploration with semantic commands. Instead of:

```bash
find . -maxdepth 3 -type f
cat src/app.py
cat src/db.py
rg UserService
git diff
```

an agent driven by Codeward reaches for:

```bash
codeward map
codeward read src/app.py
codeward read src/db.py
codeward search UserService
codeward sdiff
```

The semantic commands answer the same questions with one or two orders of magnitude fewer tokens, because they return structured summaries (symbols, dependents, callers, blast-radius) instead of raw file dumps.

Two surfaces matter:

1. **Vocabulary** — `codeward init` writes the command names into `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` so the agent learns to call them. No hooks.
2. **Hooks** — `codeward init --hook` actively rewrites `cat foo.py` → `codeward read foo.py` and injects preflight context before every `Edit`/`Write`.

Run **vocabulary-only first**. Add hooks when you want the agent's wrong shell habits corrected automatically.

## 2. Install

```bash
pipx install codeward          # recommended — isolated, on PATH
# or
pip install --user codeward
# or
npx codeward init              # Node/JS users; wrapper bootstraps via pipx
```

Python ≥ 3.11 required. Tree-sitter grammars for 17 languages and `watchdog` ship by default.

From source:

```bash
git clone https://github.com/osirishorus/codeward.git
pipx install --editable ./codeward
```

## 3. Default install: vocabulary only

```bash
cd /path/to/your/repo
codeward init       # writes CLAUDE.md + AGENTS.md vocabulary
codeward init --global   # also writes ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ~/.gemini/GEMINI.md
codeward map
codeward doctor
```

Nothing is hooked. The agent learns the verbs from the memory files and uses them when it judges they're cheaper than raw `cat`/`grep`.

## 4. Hook install

When you want Codeward to actively intervene:

```bash
codeward init --hook                            # Claude, project-local
codeward init --hook --global                   # Claude, every repo
codeward init --hook --no-hook-bash             # edit-preflight only (good w/ RTK)
codeward init --hook --global --gemini --codex  # Claude + Gemini + Codex, global
```

What each agent's hook does:

| Agent | What gets wired |
|---|---|
| **Claude Code** | `PreToolUse` on `Bash` (rewrite) + `Edit\|Write\|MultiEdit` (preflight). `~/.claude/settings.json` |
| **Codex CLI** | `PreToolUse` on `^apply_patch$` (preflight only — Codex hooks parse but ignore `updatedInput`, so Bash rewrite is skipped). `~/.codex/hooks.json` |
| **Gemini CLI** | `BeforeTool` on `run_shell_command` (rewrite + preflight). `~/.gemini/settings.json` |

Re-running `codeward init --hook ...` is idempotent.

### Verify the Claude hook output

```bash
printf '%s' '{"tool_input":{"command":"cat src/app.py"}}' | codeward hook --agent claude
```

Expected:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "updatedInput": {
      "command": "CODEWARD_ORIGINAL_COMMAND='cat src/app.py' codeward read src/app.py"
    },
    "permissionDecision": "allow",
    "permissionDecisionReason": "Codeward auto-rewrite"
  }
}
```

Empty stdout = no rewrite, original command runs.

### Verify the Codex hook output

```bash
printf '%s' '{"tool_name":"apply_patch","tool_input":{"file_path":"src/app.py"}}' \
  | codeward hook --agent codex
```

Returns a `hookSpecificOutput.additionalContext` block — preflight (dependents, tests, side-effects, routes, blast-radius) gets injected before the edit reaches the model.

### Verify the Gemini hook output

```bash
printf '%s' '{"tool_name":"run_shell_command","tool_input":{"command":"cat src/app.py"}}' \
  | codeward hook --agent gemini
```

## 5. Cursor and generic wrappers

```bash
codeward hook --agent cursor
codeward hook --agent generic
```

Generic output writes the rewrite as `{"updatedInput": {"command": "..."}}` — useful for custom shell wrappers and plugin systems.

## 6. MCP server (any MCP client)

```bash
pip install 'codeward[mcp]'
```

Add to your client's `mcpServers` config (Claude Desktop, Cursor, Continue, Zed, Cline, Goose, Windsurf, ChatGPT Desktop):

```json
{
  "mcpServers": {
    "codeward": {
      "command": "codeward",
      "args": ["mcp", "--cwd", "/path/to/your/repo"]
    }
  }
}
```

Every read-only Codeward command becomes a first-class MCP tool. No per-tool hook config.

## 7. PR review in CI

Use the composite action when you want the same symbol-aware diff, review, and affected-test context as a sticky PR comment and step summary:

```yaml
name: Codeward PR report

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write
  issues: write

jobs:
  codeward-pr-report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: osirishorus/codeward@main
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          security: "true"
          comment: "true"
          python-version: "3.12"
```

Inputs: `base` (default: `""`; uses the pull request base, then repository default branch), `security` (default: `"true"`), `comment` (default: `"true"`), `python-version` (default: `"3.12"`), `from-source` (default: `"false"`). Full details: [CODEWARD_PR_WORKFLOW.md](CODEWARD_PR_WORKFLOW.md).

## 8. Token-savings tracking

Codeward records every hook-rewritten or directly-invoked semantic command:

- per-repo: `<repo>/.codeward/history.jsonl`
- global: `~/.codeward/history.jsonl`

Show savings:

```bash
codeward gain               # global (default)
codeward gain --repo        # just this repo
codeward gain --all         # both, deduplicated
```

During hook usage, Codeward carries the original command via the `CODEWARD_ORIGINAL_COMMAND` env var so the size estimate is honest:

```bash
CODEWARD_ORIGINAL_COMMAND='cat src/app.py' codeward read src/app.py
```

## 9. Safe rewrite policy

Codeward rewrites only the simple shell patterns where semantics are preserved.

Rewritten:

```bash
cat src/app.py
head src/app.py
tail src/app.py
rg UserService
find . -maxdepth 3 -type f
git status
git diff
pytest -q
```

Passes through (would change behavior or scope):

```bash
cat src/app.py && echo done
cat src/app.py src/db.py
cat README.md
tail -f app.log
rg --type py UserService
rg UserService src tests
git diff main...HEAD
git status -s
```

To bypass rewriting explicitly, prefix with `!raw`:

```bash
!raw cat src/app.py
```

## 10. Composing with RTK

Codeward and RTK own different layers:

| Layer | Owner | Examples |
|---|---|---|
| Bash output compression | RTK | `cat`, `rg`, `grep`, `find`, `git status`, `pytest` |
| Semantic codebase queries | Codeward | `slice`, `refs`, `blame`, `sdiff`, `routes`, `preflight` |

With both installed, Codeward's Bash hook is inserted *before* RTK's so the rewrite happens first; RTK then passes `codeward ...` through unchanged. Edit/Write preflight is on a different matcher and never clashes.

`codeward doctor` checks hook ordering and warns if RTK runs first.

Codeward never rewrites commands starting with `codeward`, `rtk`, `contextzip`, or `snip`.

## 11. Troubleshooting

### Hook does nothing

```bash
which codeward          # binary on PATH?
codeward doctor         # hooks installed? ordering correct? index fresh?
```

### Index out of date

The SQLite index at `.codeward/index.sqlite` invalidates on mtime change of any indexed file. To force a rebuild:

```bash
CODEWARD_NO_CACHE=1 codeward map
# or
rm -rf .codeward/index.sqlite
codeward index
```

### Long sessions

`codeward watch` runs a foreground re-indexer that keeps the SQLite cache hot via `watchdog` (or 2-second mtime polling if `watchdog` isn't installed). It debounces editor bursts, skips unchanged files by mtime+size, and reuses incremental tree-sitter reparses when the language analyzer supports it.

```bash
codeward watch --debounce-ms 200   # coalesce file events within 200 ms
codeward watch --stats             # print batch and parse-cache stats
```

### Agent needs exact raw output

Prefix with `!raw`, or call the real binary directly.

### Savings look wrong

`gain` estimates tokens with `len(text) // 4`. It's relative-context-reduction, not billing-grade. For small commands, semantic output can be larger than raw output; Codeward records saved-tokens as zero in that case.
