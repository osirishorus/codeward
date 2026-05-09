# Codeward per-repo configuration

Drop a `.codeward/config.toml` at the repo root to override Codeward defaults for that one repository. The config is reloaded every time Codeward runs — there's no daemon to restart.

If the file is missing, Codeward uses its built-in defaults. If the file is present but malformed (TOML syntax error, wrong types), Codeward falls back to defaults and `codeward doctor` reports the error with a non-zero exit. Codeward never silently uses a half-loaded config.

## Schema

```toml
[index]
# Directories to skip in addition to the built-in IGNORE_DIRS
# (.git, node_modules, .venv, venv, __pycache__, dist, build, target, .next, .cache).
ignore_dirs = ["legacy", "vendor", "third_party"]

# Directory segments to treat as test locations in addition to the built-ins
# (tests, test, __tests__, spec, specs). Lowercased automatically.
extra_test_dirs = ["e2e", "integration_tests"]

# Filename patterns (fnmatch) to additionally treat as tests. Built-ins:
# test_*.py, *_test.py, *.test.{ts,tsx,js,jsx}, *.spec.{ts,tsx,js,jsx},
# *_test.go, *_test.rs.
extra_test_patterns = ["*.smoke.py"]

# Custom side-effect rules. Each rule pairs a regex with a label that gets
# attached to any file whose contents match (after comments/docstrings are
# stripped). Useful for repo-specific risk markers.
[[side_effects.custom_rules]]
pattern = '\baudit_log\s*\('
label = "Audit log"

[[side_effects.custom_rules]]
pattern = '\bbilling\.charge\s*\('
label = "Billing event"

[[side_effects.custom_rules]]
pattern = '\b(?:enqueue|publish)_(?:job|event)\s*\('
label = "Async dispatch"
```

## Notes

- All keys are optional. Omit any section you don't need.
- `ignore_dirs` adds to the defaults rather than replacing them. To remove a default ignore, you can't via config — file an issue if this becomes important.
- `custom_rules` patterns are compiled with Python's stdlib `re`. Anchors and groups behave normally; backslashes need to be escaped per TOML string rules (use single-quoted literal strings to avoid double-escaping, as shown above).
- `pattern` and `label` must both be strings; rules with missing/wrong types are silently skipped.
- Side-effect rules run on a comment-stripped, docstring-stripped version of the source, so a rule won't fire on prose mentioning the pattern in a docstring.

## Verification

After editing the config, run:

```bash
codeward doctor
```

It will report:

- `Config: .codeward/config.toml (loaded: ignore_dirs, extra_test_dirs, custom_side_effect_rules)` — config parsed and applied.
- `Config: .codeward/config.toml (malformed: ...)` — TOML syntax error; defaults used.
- `Config: no .codeward/config.toml (using defaults)` — no config present.

To check a custom side-effect rule fires, run `codeward read <file>` on a file that should match. The label appears under "Side effects".
