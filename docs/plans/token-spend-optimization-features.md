# Token Spend Optimization Features Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add practical Codeward commands that help coding agents avoid wasting context on noisy files and oversized raw reads.

**Architecture:** Implement two stdlib-only CLI surfaces in `src/codeward/cli.py` using the existing `RepoIndex`, `estimate_tokens`, and `emit_tracked` conventions. Add tests in `tests/test_cli.py` at the CLI boundary so behavior remains stable for hooks, shims, and agent memory instructions.

**Tech Stack:** Python 3.11 stdlib, argparse CLI, pytest, Codeward's existing SQLite-backed repo index.

---

## Feature ideas considered

1. **`codeward budget`** — repo/file token audit: identify token-heavy files and produce cheaper Codeward alternatives before an agent dumps raw files.
2. **`codeward pack <target>`** — compact context bundle under a token budget: include map summary, target file preflight, related tests, direct dependents, and top symbols without dumping code bodies.
3. **`codeward guard` hook policy** — warn/block extremely expensive raw commands unless prefixed with `!raw`.
4. **`codeward session`** — aggregate gain history by task/session and suggest the next highest-ROI rewrite.
5. **`codeward diff-pack`** — changed-file context pack for PR review.

## Selected implementation

Ship (1) and (2) first because they are immediately useful, low-risk, stdlib-only, and compose with existing commands.

### Task 1: Add CLI regression tests for `budget`

**Objective:** Prove `codeward budget` reports total repo token estimates, high-cost files, and cheaper alternatives.

**Files:**
- Modify: `tests/test_cli.py`

**Steps:**
1. Append `test_budget_reports_token_hotspots_and_alternatives`.
2. Create an oversized `src/services/big_service.py` in the sample repo.
3. Run `codeward budget --top 2`.
4. Assert output includes `Codeward token budget`, `Estimated raw code tokens`, `src/services/big_service.py`, and `codeward read src/services/big_service.py`.

### Task 2: Implement `cmd_budget`

**Objective:** Add a read-only command that ranks raw code token hotspots and recommends compact semantic commands.

**Files:**
- Modify: `src/codeward/cli.py`

**Steps:**
1. Add helper `token_budget_for_file(idx, path)` using `estimate_tokens(idx.text(path))`.
2. Add `cmd_budget(args)` with `--top`, optional `target`, and `--json` support.
3. Register parser: `budget [target] --top N` using `parents=[common]`.
4. Use `emit_tracked(..., command_name="budget", raw_token_estimate=total_raw, payload=..., json_mode=...)`.

### Task 3: Add CLI regression tests for `pack`

**Objective:** Prove `codeward pack` produces a compact, budgeted context bundle for one file.

**Files:**
- Modify: `tests/test_cli.py`

**Steps:**
1. Append `test_pack_builds_budgeted_context_bundle`.
2. Run `codeward pack src/services/user_service.py --max-tokens 220`.
3. Assert output includes `Codeward context pack`, target path, likely test, dependent, and `Estimated pack tokens`.
4. Add a JSON-mode assertion that `.target` equals the file path and `.included_files` contains the target.

### Task 4: Implement `cmd_pack`

**Objective:** Add a compact context-packing command for files/directories/symbols under an approximate token budget.

**Files:**
- Modify: `src/codeward/cli.py`

**Steps:**
1. Add target resolution helper: exact file, directory prefix, else symbol definition file(s), else search query files.
2. Build ordered candidates: target files, tests, direct dependents, then symbol/search matches.
3. Include one-line file summaries and top symbols; do not include bodies.
4. Stop adding rows when `estimate_tokens(rendered_text)` would exceed `--max-tokens`, but always include the first target row.
5. Register parser: `pack <target> --max-tokens 800 --top-symbols 6`.

### Task 5: Verify and document minimally

**Objective:** Ensure features pass tests and are discoverable.

**Files:**
- Modify: `README.md`

**Steps:**
1. Add `budget` and `pack` to the Commands table.
2. Run targeted tests for the two new features.
3. Run full `python3 -m pytest tests/ -q`.
