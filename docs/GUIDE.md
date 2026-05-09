# Codeward Guide

This guide explains how to use Codeward in real coding-agent workflows, how the hooks work, and how to verify token savings.

## 1. Mental model

Codeward replaces noisy codebase exploration with semantic commands.

Instead of this agent workflow:

```bash
find . -maxdepth 3 -type f
cat src/app.py
cat src/db.py
rg UserService
git diff
pytest -q
```

Codeward steers the agent toward:

```bash
codeward map
codeward read src/app.py
codeward read src/db.py
codeward search UserService
codeward diff
codeward test pytest -q
```

The result is smaller and more useful context.

## 2. Install in a project

Install Codeward once:

```bash
cd /path/to/codeward
python3 -m pip install -e .
```

Then move to the repository you want agents to work on:

```bash
cd /path/to/target/repo
codeward map
```

If `codeward map` prints a repo overview, Codeward is ready.

## 3. Default install: semantic commands only

Codeward is built to complement RTK rather than compete with it. The default install does **not** touch any hooks. It writes the semantic-command vocabulary to **both** `CLAUDE.md` (Claude Code's auto-discovered memory file) and `AGENTS.md` (the Codex/Cursor convention):

```bash
codeward init
```

After this, an agent navigating the repo can use `codeward map`, `codeward read`, `codeward symbol`, `codeward callgraph`, `codeward tests-for`, `codeward impact`, `codeward review`, plus symbol-level commands `codeward slice`, `codeward refs`, `codeward blame`, `codeward sdiff`, `codeward api`, and `codeward preflight`. `refs`, `symbol`, and `callgraph` label analyzer confidence instead of presenting heuristic matches as exact. RTK keeps owning Bash output compression for `cat`, `rg`, `grep`, `find`, `git status`, etc.

### Optional: hook-mode install

If you want Codeward to also rewrite Bash commands AND inject preflight context before `Edit`/`Write`:

```bash
codeward init --hook              # project-local hook (both Bash + Edit/Write)
codeward init --hook --global     # also wire ~/.claude/settings.json
codeward init --hook --no-hook-edit  # Bash-only, skip Edit/Write preflight
```

Two PreToolUse entries are installed:

- `matcher: "Bash"` — rewrites `cat foo.py` → `codeward read foo.py` and tracks savings.
- `matcher: "Edit|Write|MultiEdit"` — runs `codeward preflight <file>` and injects dependents/tests/side-effects/security-flags via `additionalContext` before the edit happens.

The `--global` form inserts the Bash entry **before** any existing `rtk hook claude` entry. RTK only matches `Bash`, so the Edit/Write hook never clashes with it. The installer is idempotent — re-running is a no-op.

Restart Claude Code (or start a new session in the repo) for hook changes to take effect.

### Verify Claude hook output

```bash
printf '%s' '{"tool_input":{"command":"cat src/app.py"}}' | codeward hook --agent claude
```

Expected shape:

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

If Codeward does not rewrite, stdout is empty and the original command runs.

## 4. Universal PATH shims

For Codex, Gemini CLI, OpenCode, terminal-based agents, or any tool that executes shell commands:

```bash
codeward init-agent
export PATH="$PWD/.codeward/bin:$PATH"
```

If RTK is installed, `init-agent` refuses by default — its shims would intercept `rtk`'s child lookups and double-transform commands. Use `codeward init-agent --force` if you really want both layered. In most setups with RTK, you don't need the shims at all: RTK already handles the cat/grep/find surface.

This installs shims for:

```text
cat head tail rg grep find tree git pytest npm pnpm yarn cargo go
```

### Verify shim behavior

```bash
PATH="$PWD/.codeward/bin:$PATH" cat src/app.py
```

If `src/app.py` is indexed and code-like, output should be a semantic `codeward read` summary.

For a non-code file, Codeward should pass through:

```bash
PATH="$PWD/.codeward/bin:$PATH" cat README.md
```

## 5. Gemini CLI

If you want a native Gemini hook, configure `codeward hook --agent gemini` as a `BeforeTool` hook for `run_shell_command`.

Example `.gemini/settings.json`:

```json
{
  "hooks": {
    "BeforeTool": [
      {
        "matcher": "run_shell_command",
        "hooks": [
          {
            "type": "command",
            "name": "codeward-rewrite",
            "command": "codeward hook --agent gemini"
          }
        ]
      }
    ]
  }
}
```

Verify:

```bash
printf '%s' '{"tool_name":"run_shell_command","tool_input":{"command":"cat src/app.py"}}' | codeward hook --agent gemini
```

Expected shape:

```json
{
  "decision": "allow",
  "hookSpecificOutput": {
    "tool_input": {
      "command": "CODEWARD_ORIGINAL_COMMAND='cat src/app.py' codeward read src/app.py"
    }
  }
}
```

No rewrite returns:

```json
{"decision":"allow"}
```

## 6. Cursor and generic wrappers

Cursor-style hook:

```bash
codeward hook --agent cursor
```

Generic hook shape:

```bash
codeward hook --agent generic
```

Generic output is useful for custom shell wrappers and plugin systems that understand `updatedInput.command`.

## 7. OpenCode plugin pattern

OpenCode plugin integrations should call Codeward's rewrite primitive and mutate the command if it changes.

Pseudo-code:

```ts
const result = await $`codeward run --dry-run --shell-command ${command}`.quiet().nothrow()
const rewritten = String(result.stdout).trim()

if (rewritten && rewritten !== command) {
  args.command = rewritten
}
```

Keep plugin code thin. All rewrite policy should stay in `codeward.hooks.rewrite_command`.

## 8. Token savings tracking

Codeward records history in:

```text
.codeward/history.jsonl
```

Show current savings:

```bash
codeward gain
```

Benchmark raw-vs-Codeward behavior:

```bash
codeward savings --no-history \
  --command 'cat src/app.py' \
  --command 'find . -maxdepth 3 -type f' \
  --command 'git status'
```

During real hook/shim usage, Codeward tracks savings by carrying the original command through `CODEWARD_ORIGINAL_COMMAND`:

```bash
CODEWARD_ORIGINAL_COMMAND='cat src/app.py' codeward read src/app.py
```

The semantic command records raw-token estimate, output-token estimate, and saved tokens.

## 9. Safe rewrite policy

Codeward rewrites only simple commands where semantics are preserved.

Safe examples:

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

Unsafe or ambiguous examples that pass through:

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

This avoids losing shell semantics or narrowing the user's intended scope incorrectly.

## 10. Working with RTK or other compressors

Codeward and RTK own different layers and compose cleanly:

| Layer | Owner | Examples |
|---|---|---|
| Bash output compression | RTK | `cat`, `rg`, `grep`, `find`, `git status`, `pytest` — RTK runs the command and squeezes the output |
| Semantic codebase queries | Codeward | `codeward symbol`, `callgraph`, `tests-for`, `impact`, `review` — answer questions RTK can't |

The default `codeward init` (no `--hook`) does not touch hooks at all, so there is nothing to fight RTK over. The semantic commands are agent-invoked.

If you opt into the hook layer with `codeward init --hook --global`, Codeward's hook is placed **before** RTK's in `~/.claude/settings.json`. Both fire on `Bash`. Codeward rewrites first to `codeward <semantic>`; RTK then receives a command starting with `codeward`, which it passes through unchanged (RTK has the same ignore convention). No recursion, no double-wrapping.

Codeward never rewrites commands starting with:

```text
codeward
rtk
contextzip
snip
```

Run `codeward doctor` at any time to check RTK presence, hook position, PATH-shim conflicts, and index freshness.

## 11. Real-world benchmark recipe

This is the benchmark used to validate Codeward against Flask.

```bash
rm -rf /tmp/codeward-bench
mkdir -p /tmp/codeward-bench
git clone --depth 1 https://github.com/pallets/flask.git /tmp/codeward-bench/flask
cd /tmp/codeward-bench/flask

codeward init
codeward init-agent
codeward index
rm -f .codeward/history.jsonl

PATH="$PWD/.codeward/bin:$PATH" claude -p \
  "Use ONLY Bash. Do not use Read/Grep/Glob/Edit. Do not modify files. First run these exact shell commands one by one, then use their compact output to analyze Flask architecture: find . -maxdepth 3 -type f ; cat src/flask/app.py ; cat src/flask/ctx.py ; cat src/flask/cli.py ; cat tests/test_basic.py ; git status. Then write a concise report on how well Codeward hook compression worked and what architecture you learned." \
  --allowedTools "Bash" \
  --max-turns 15 \
  --output-format json

codeward gain
```

Observed representative side-by-side savings:

```text
Commands analyzed: 6
Total raw tokens: 45196
Total Codeward tokens: 2816
Total saved: 42380 (93.8%)
```

## 12. Troubleshooting

### Hook does nothing

Check that `codeward` is installed and available:

```bash
which codeward
codeward --help
```

For Claude Code, verify:

```bash
python3 -m json.tool .claude/settings.local.json
```

### PATH shim recurses or hangs

Codeward shims should remove `.codeward/bin` before pass-through. Reinstall shims:

```bash
codeward init-agent
```

Then verify direct pass-through:

```bash
PATH="$PWD/.codeward/bin:$PATH" cat README.md | head
```

### Agent needs exact raw output

Use:

```bash
!raw <command>
```

or temporarily remove the shim path:

```bash
PATH=$(printf '%s' "$PATH" | tr ':' '\n' | grep -v '/.codeward/bin' | paste -sd: -)
```

### Savings look wrong

`codeward savings` estimates tokens with `len(text) // 4`. It is meant for relative context reduction, not billing-grade accounting. For small commands, semantic output can be larger than raw output; Codeward records saved tokens as zero in that case.
