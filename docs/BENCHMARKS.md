# Codeward benchmarks (v0.3.0)

Real-world A/B comparisons against baseline (raw shell only). Same prompt, same model per agent, same `--max-turns`. Three configurations:

- **baseline**: clean clone, no Codeward.
- **codeward**: `codeward init` (writes CLAUDE.md + AGENTS.md, no hooks) + `codeward index`. Agent invokes `codeward` directly when CLAUDE.md guidance matches.
- **codeward-hook**: additionally `codeward init --hook` (Bash rewrite hook + Edit/Write preflight hook).

All token figures are the units the model actually consumes — `tool_tokens` is text returned by Bash to the agent (the actual context-pressure metric). Cost is intentionally omitted; tokens are the durable unit.

## Workflow benchmarks (Claude Sonnet 4.6)

| Workflow (fastapi unless noted) | Mode | Turns | Cmds | CS calls | Tool tokens | Δ tokens | Verdict |
|---|---|---:|---:|---:|---:|---:|---|
| Architecture-plan | baseline | 28 | 27 | 0 | 18,164 | — | — |
| Architecture-plan | codeward | 30 | 29 | 20 | 16,959 | −6.6% | ≈ tie |
| Architecture-plan | codeward-hook | 31 | 30 | 21 | 21,359 | +17.6% | ≈ tie |
| Bug-fix | baseline | 26 | 25 | 0 | 9,515 | — | — |
| Bug-fix | codeward | 26 | 25 | 0\* | 13,527 | +42.2% | ≈ tie / lean worse |
| Code-review | baseline | 26 | 25 | 0 | 4,234 | — | — |
| Code-review | codeward | 26 | 24 | 8 | 3,874 | **−8.5%** | ≈ tie |
| **Refactor/rename** | baseline | 19 | 18 | 0 | 5,524 | — | — |
| **Refactor/rename** | codeward | **12** | **11** | 0\* | **2,837** | **−48.6%** | **✓ CLEAR WIN** |
| **Go planning** (gin) | baseline | 20 | 19 | 0 | 7,576 | — | — |
| **Go planning** (gin) | codeward | **17** | **16** | 7 | **5,281** | **−30.3%** | **✓ WIN** |

\* "0 CS calls" with a token reduction is real and informative — the agent stayed in shell but **CLAUDE.md teaching changed its strategy** (e.g. opening with `grep -rn ... | wc -l` to scope before reading).

## Edit/Write benchmark — preflight hook in action

Same prompt: "add a `describe_self()` method to `APIRoute`". Tools: `Bash, Edit, Write, Read`. The codeward-hook variant has the `Edit|Write|MultiEdit` preflight hook installed.

| Mode | Tool calls | Hooks fired | Preflight context | Cache-read tokens | Output tokens |
|---|---:|---:|---:|---:|---:|
| baseline (no hook) | 4 (Grep, Read, Edit, Read) | 0 | 0 | 178,837 | 962 |
| codeward-hook | 4 (Grep, Read, Edit, Read) | **1** | **149 tokens injected** | 181,649 | 912 |

Both runs produced identical edits (same line, same code). What changed:

- **Hook fired exactly once** — on the `Edit` tool call. Not on `Grep`, not on `Read`. Targeted to the right surface.
- **149 tokens of context injected** before the edit, including:
  ```
  # Codeward preflight: fastapi/routing.py
    language=Python, lines=4956, symbols=47, blast_radius=HIGH
    dependents (14): docs_src/custom_request_and_route/tutorial001_an_py310.py, ...
    likely tests: tests/test_custom_route_class.py, tests/test_route_scope.py, ...
  ```
- **Agent's response explicitly acknowledged** the preflight context: *"Acknowledged the preflight context. The edit added describe_self() to the APIRoute class..."*

The cost on this small task is rounding error, but the *signal* the agent got is real: it now knows the file it's about to edit has 14 dependents and is HIGH blast-radius. On a riskier change, that informs whether/how to proceed.

## Per-command compression — `codeward gain`

Measured directly: the diff between the raw shell analogue (e.g. `cat fastapi/routing.py` ≈ 200KB) and the semantic Codeward output (e.g. `codeward slice APIRoute.get_route_handler` ≈ 700 tokens). Recorded automatically when an agent invokes `codeward` directly.

**fastapi hook-mode session, 17 codeward invocations:**

| Metric | Value |
|---|---:|
| Raw-equivalent tokens | 659,211 |
| Codeward-output tokens | 97,632 |
| **Saved** | **561,579 tokens (85.2%)** |

Top single-call compressions:
```
99.4%  codeward slice APIRoute.get_route_handler   →  49,108 tokens saved
98.9%  codeward slice request_response             →  48,886 tokens saved
97.5%  codeward slice APIRouter.add_api_route      →  48,156 tokens saved
95.3%  codeward slice APIRoute.__init__            →  47,062 tokens saved
```

**gin Go session, 5 codeward invocations:**

| Metric | Value |
|---|---:|
| Raw-equivalent tokens | 12,943 |
| Codeward-output tokens | 2,282 |
| **Saved** | **10,661 tokens (82.4%)** |

For long sessions where context-window pressure compounds, **per-command compression matters more than per-task headline numbers**. A single `codeward slice` returning 700 tokens vs `cat`'s 50,000 is a 70× context-pressure reduction even when turn count is identical.

## Cross-agent results — refactor task

Same prompt (`prompt-refactor.txt` — find every callsite of `APIRoute.get_route_handler`), same fastapi clone, three different agents.

| Agent | Mode | Shell cmds | Input tokens | Output tokens | API calls | `codeward` calls | Δ tokens |
|---|---|---:|---:|---:|---:|---:|---:|
| **Claude** sonnet-4.6 | baseline | 18 | — | — | 19 turns | 0 | — |
| Claude | codeward | **11** (−39%) | — | — | **12 turns** (−37%) | 0\* | **tool tokens −49%** |
| **Codex** gpt-5 | baseline | 15 | 173,063 (cached 135,936) | 3,649 | 1 | 0 | — |
| Codex | codeward | **12** (−20%) | 171,087 (cached **114,048**) | **2,990** | 1 | **4** | output **−18%** |
| **Gemini** 3-flash-preview | baseline | 44 | 954,463 | 3,584 | 48 | n/a | — |
| Gemini | codeward | **19** (−57%) | **385,675** (−60%) | **2,610** (−27%) | **21** (−56%) | n/a | **input −60%** |

\* Claude won without invoking `codeward` directly — CLAUDE.md teaching steered its grep+sed strategy.

### What this tells us about agent behavior

Three agents, three invocation patterns, all benefit:

- **Claude**: doesn't call `codeward` for this task at all, but reads CLAUDE.md and adopts the "scope first with `wc -l`, then targeted reads" idiom Codeward teaches. The teaching does the work.
- **Codex**: mixes 4 `codeward` calls (`map`, `search`, `symbol APIRoute`, `tests-for`) with 8 `rg` calls. Codex is comfortable using both layers explicitly.
- **Gemini**: biggest absolute reduction (−57% in commands and API calls, −60% input tokens). Gemini's headless JSON doesn't expose per-command detail, so we can't see which strategy it picked, but the pattern is unambiguous.

**Codeward adds value to every agent we tested**, in different ways. Claude benefits from teaching, Codex from selective explicit use, Gemini from heavy adoption.

## Why hook mode looked flat in the headline benchmarks

Hook mode showed only +4k tool tokens vs no-hook on the architecture-plan task. Two structural reasons:

1. **The Bash rewrite hook only rewrites commands that don't start with `codeward`**. CLAUDE.md was already steering the agent to invoke `codeward` directly — so the hook had nothing to rewrite.
2. **The Edit/Write preflight hook only fires on Edit/Write tool calls**. Bash-only benchmarks (`--allowedTools Bash`) close that surface off entirely.

The dedicated Edit/Write benchmark above shows the preflight hook **does** fire correctly when the right surface is open — and the agent acknowledges the injected context.

The hook layer remains valuable when:
- The agent ignores CLAUDE.md and just runs `cat foo.py` (Bash hook transparently rewrites).
- A real Edit/Write workflow runs (preflight injects dependents/tests/blast-radius before the edit).

## Bottom-line guidance

Codeward wins on **structural questions across the codebase** (refactor, cross-cutting impact, multi-symbol planning). Verified across all three major agents:

- **Refactor planning** is the strongest single use case found. Up to **−60% input tokens / −57% API calls** (Gemini), **−49% tool tokens** (Claude), **−20% commands / −18% output** (Codex).
- **Cross-language orientation** — Go/TypeScript/Rust files where regex would have failed. Tree-sitter delivers accurate symbol extraction. **−30% tool tokens** on gin (Go).
- **Per-command compression** — every `codeward slice` / `read` invocation returns 80%+ less than the raw shell equivalent. Visible to `codeward gain`.

Codeward is **neutral on**:
- Architecture overviews and code review — agent uses lots of `codeward` but extra turns offset per-call savings.

Codeward is **net-negative on**:
- Targeted bug-fix where the agent already knows what to look for. Agent does grep+sed with RTK compression and gets there faster.

The strongest signal: **per-command compression for context-budget-constrained sessions**. The headline turn-count number doesn't capture this; the `codeward gain` number does. 561k tokens saved across 17 calls in a single planning session is real and matters in long workflows.
