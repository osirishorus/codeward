# Release guide

For maintainers preparing a public release.

## 1. Clean generated state

```bash
rm -rf dist build src/*.egg-info .pytest_cache .mypy_cache
rm -rf src/codeward/__pycache__ tests/__pycache__
rm -rf .codeward .claude
```

## 2. Verify package metadata + tests

```bash
python3 -m pip install -e .
codeward --help
python3 -m pytest tests/ -q
```

All tests should pass on Python 3.11+. Tree-sitter parser tests skip only when a grammar package cannot be imported.

## 3. Build artifacts

```bash
python3 -m pip install --upgrade build twine
python3 -m build
python3 -m twine check dist/*
```

Confirm both `dist/codeward_agent-X.Y.Z.tar.gz` and `dist/codeward_agent-X.Y.Z-py3-none-any.whl` are produced and pass `twine check`.

## 4. Smoke-test in a fresh venv

```bash
python3 -m venv /tmp/codeward-test
source /tmp/codeward-test/bin/activate
pip install dist/codeward_agent-*.whl
codeward --version
codeward --help
deactivate
rm -rf /tmp/codeward-test
```

## 5. End-to-end smoke on a real repo

```bash
rm -rf /tmp/codeward-smoke
git clone --depth 1 https://github.com/pallets/flask.git /tmp/codeward-smoke
cd /tmp/codeward-smoke
codeward init
codeward index
codeward map
codeward read src/flask/app.py | head -30
codeward slice "Flask.run" --signature-only
codeward gain
```

Expected: semantic output, working slice, savings recorded.

## 6. Tag release

```bash
git tag -a v0.3.0 -m "Codeward v0.3.0 — Phase B/C/D"
git push origin main --tags
```

## 7. Publish to PyPI

```bash
# TestPyPI first (recommended)
python3 -m twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ codeward  # verify

# Real PyPI
python3 -m twine upload dist/*
```

## 8. GitHub release notes

Use the `CHANGELOG.md` section for that version. Highlight:

- New commands (slice, refs, blame, sdiff, api, preflight, watch)
- Edit/Write hook integration
- Tree-sitter optional dep
- Honest benchmark results from `docs/BENCHMARKS.md`
- RTK coexistence guarantees
