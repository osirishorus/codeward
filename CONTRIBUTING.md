# Contributing to Codeward Agent

Thanks for helping improve Codeward.

## Development setup

```bash
git clone https://github.com/<owner>/codeward.git
cd codeward
python3 -m pip install -e .
python3 -m pytest -q
```

Codeward intentionally has no runtime dependencies beyond the Python standard library.

## Test expectations

Run the full suite before submitting changes:

```bash
python3 -m pytest -q
```

Add tests for any behavior change, especially hook output shapes and rewrite safety.

## Hook integration rules

When adding or changing agent integrations:

1. Keep rewrite policy centralized in `codeward.hooks.rewrite_command`.
2. Keep agent adapters thin: parse stdin JSON, call the rewrite primitive, emit the agent's expected JSON shape.
3. Fail open for optimizer hooks. Invalid JSON, unsupported tools, and no-rewrite cases must not block the user's original command.
4. Do not rewrite compound shell commands unless semantics are preserved.
5. Avoid recursion. PATH shims must remove `.codeward/bin` before pass-through and rewritten execution.
6. Treat `!raw <command>` as command substitution, not permission approval.
7. Add fixtures for every supported agent shape: Claude, Gemini, Cursor, generic.

## Conservative rewrite policy

Only rewrite when the semantic equivalent is clear. Prefer a no-rewrite pass-through over a lossy rewrite.

Examples that should pass through unchanged:

```bash
cat file1.py file2.py
cat README.md
tail -f app.log
rg --type py Query
rg Query src tests
git diff main...HEAD
git status -s
```

## Documentation

If a feature changes user behavior, update:

- `README.md`
- `docs/GUIDE.md`
- `docs/PLAN.md` if it affects shipped/future scope

## Release checklist

Before publishing:

```bash
python3 -m pytest -q
python3 -m pip install build
python3 -m build
python3 -m twine check dist/*
```

Also verify:

```bash
codeward --help
codeward savings --no-history --command 'cat src/codeward/cli.py'
```
