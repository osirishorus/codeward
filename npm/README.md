# codeward (npm wrapper)

This npm package is a thin wrapper around the [Codeward](https://github.com/osirishorus/codeward)
Python CLI. It exists so you can run:

```bash
npx codeward init
npx codeward map
npm i -g codeward    # global install
```

without thinking about pip / pipx.

On first run, the wrapper:

1. Checks for an existing `codeward` on `PATH` — if present, just forwards.
2. Otherwise, installs Codeward once via `pipx install codeward`
   (or `pip install --user codeward` as a fallback).
3. Then forwards the arguments to the installed binary.

## Requirements

- Node ≥ 18
- Python ≥ 3.11

If you don't have Python, install it from <https://python.org> first.

## Why not bundle Python?

Codeward depends on native tree-sitter grammars. Bundling them across all
platforms via Node would multiply the package size for no real benefit —
`pipx`/`pip` already solve that problem. The wrapper exists for ergonomics:
one command, one mental model.
