# Security Policy

## Reporting vulnerabilities

Please report security issues privately to the maintainers before public disclosure.

If this repository is hosted on GitHub, use GitHub Security Advisories when available. Otherwise, open a minimal issue asking for a private contact path without including exploit details.

## Scope

Security-sensitive areas include:

- command rewrite logic in `codeward.hooks`
- hook JSON parsing and response generation
- PATH shim recursion / command execution behavior
- `codeward review --security` heuristics
- accidental inclusion of secrets in generated output or history

## Design safety principles

Codeward is an optimizer, not a sandbox.

- It should fail open for malformed optimizer hook input.
- It should not block user commands unless a future explicit safety mode is added.
- It should not rewrite ambiguous shell commands where semantics may change.
- It should not unwrap or fight other command wrappers such as RTK.
- Raw escape hatches must not imply permission approval.

## Sensitive data

Codeward writes local history to:

```text
.codeward/history.jsonl
```

This file may contain command strings and approximate token counts. It should not be committed. The default `.gitignore` excludes `.codeward/`.

`codeward review --security` is heuristic and may produce false positives or false negatives. It is not a replacement for dedicated tools such as Semgrep, Bandit, npm audit, cargo audit, or OSV scanners.
