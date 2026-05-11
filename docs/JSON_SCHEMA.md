# Codeward JSON output schema

Every read-only command supports `--json` for machine-parseable output. This document is the contract: programmatic clients (CI tools, MCP servers, IDE plugins, GitHub Actions) can rely on the shapes below.

Backwards compatibility: fields will only ever be **added**. Existing keys won't be removed or have their types changed without a major version bump.

## Conventions

- Top-level field `command` always echoes the subcommand name.
- File paths are repo-relative POSIX strings (`src/foo/bar.py`), even on Windows.
- Line numbers are 1-based.
- Lists are stable-ordered (file paths sorted lexicographically; symbols by line; matches by file then line).
- Missing/empty values are explicit (`[]` or `null`), never omitted.
- Analyzer metadata appears on files, symbols, references, and callgraph steps:
  `analyzer` is `python_ast`, `tree_sitter`, or `regex`; `precision` is
  `exact_range`, `syntax_aware`, or `heuristic`; `confidence` is `high`,
  `medium`, or `low`.

## `codeward map --json`

```json
{
  "command": "map",
  "primary_language": "Python",
  "root": "/abs/path/to/repo",
  "counts": {"code_files": 213, "test_files": 67},
  "languages": {"Python": 213},
  "important_files": [
    {"path": "rich/console.py", "lang": "Python", "lines": 2698, "symbols": 123, "role": "source"}
  ],
  "suggested_next": ["codeward review --changed", "codeward impact --changed", "codeward symbol <Name>"]
}
```

## `codeward read --json <file>`

```json
{
  "command": "read",
  "file": "src/services/user_service.py",
  "role": "domain/service logic",
  "language": "Python",
  "lines": 42,
  "analyzer": "python_ast",
  "precision": "exact_range",
  "confidence": "high",
  "symbols": [
    {
      "name": "UserService",
      "kind": "class",
      "line": 5,
      "end_line": 12,
      "signature": "class UserService",
      "analyzer": "python_ast",
      "precision": "exact_range",
      "confidence": "high",
      "methods": [
        {"name": "create_user", "line": 6, "end_line": 9, "signature": "def create_user(self, email: str) -> dict", "analyzer": "python_ast", "precision": "exact_range", "confidence": "high"}
      ]
    }
  ],
  "imports": ["src.db", "src.emailer"],
  "dependents": ["src/controllers/user_controller.py"],
  "tests": ["tests/test_user_service.py"],
  "side_effects": ["DB write", "Email send"],
  "raw_escape": "!raw cat src/services/user_service.py",
  "flow": ["## def create_user...", "..."]
}
```

`flow` is only present when `--flow` is passed.

## `codeward search --json <query>`

```json
{
  "command": "search",
  "query": "UserService",
  "total_matches": 7,
  "files": [
    {
      "file": "src/services/user_service.py",
      "matches": [{"line": 5, "text": "class UserService:"}],
      "shown": 1,
      "total": 1
    }
  ]
}
```

## `codeward symbol --json <name>`

```json
{
  "command": "symbol",
  "name": "UserService",
  "definitions": [
    {
      "name": "UserService",
      "kind": "class",
      "file": "src/services/user_service.py",
      "line": 5,
      "end_line": 12,
      "signature": "class UserService",
      "analyzer": "python_ast",
      "precision": "exact_range",
      "confidence": "high",
      "methods": ["create_user", "delete_user"],
      "callers": [{"file": "src/controllers/user_controller.py", "line": 4, "text": "...", "analyzer": "python_ast", "precision": "exact_range", "confidence": "high"}],
      "tests": ["tests/test_user_service.py"]
    }
  ]
}
```

If the symbol is not found, `definitions` is `[]` and `text_matches` lists fallback grep hits.

## `codeward callgraph --json <route-or-symbol>`

```json
{
  "command": "callgraph",
  "query": "POST /api/users",
  "chain": [
    {"step": "POST /api/users", "handler": "create_user_controller", "kind": "route", "analyzer": "python_ast", "precision": "exact_range", "confidence": "high"},
    {"caller": "create_user_controller", "callee": "UserService.create_user", "inferred": false, "target_file": "src/services/user_service.py", "analyzer": "python_ast", "precision": "exact_range", "confidence": "high"}
  ],
  "side_effects": ["DB write", "Email send"]
}
```

## `codeward tests-for --json <target>`

```json
{
  "command": "tests-for",
  "target": "src/services/user_service.py",
  "tests": ["tests/test_user_service.py"],
  "suggested_command": "pytest tests/test_user_service.py"
}
```

## `codeward impact --json [--changed | <target>]`

```json
{
  "command": "impact",
  "files": [
    {
      "file": "src/services/user_service.py",
      "dependents": ["src/controllers/user_controller.py"],
      "tests": ["tests/test_user_service.py"],
      "risk": "HIGH",
      "hotspot": true,
      "commits_90d": 7
    }
  ]
}
```

`risk` is one of `LOW`, `MEDIUM`, `HIGH`. `hotspot` is `true` when the file is in the top decile of recent (90d) churn with a 3-commit floor; risk is bumped to `HIGH` for hotspots regardless of dependent count. `commits_90d` is the file's commit count in the last 90 days (0 in non-git directories).

## `codeward review --json [--changed | <target>] [--security]`

```json
{
  "command": "review",
  "files": [
    {
      "file": "src/services/user_service.py",
      "analyzer": "python_ast",
      "precision": "exact_range",
      "confidence": "high",
      "symbols": [{"name": "UserService", "kind": "class", "analyzer": "python_ast", "precision": "exact_range", "confidence": "high"}],
      "risks": ["DB write"],
      "security_findings": [],
      "tests": ["tests/test_user_service.py"]
    }
  ],
  "security_findings": [],
  "suggested_command": "pytest tests/test_user_service.py"
}
```

## `codeward status --json` / `codeward diff --json`

```json
{"command": "status", "changed_files": 3, "counts": {"M": 2, "??": 1}, "files": ["M  foo.py", "??  bar.py"]}
{"command": "diff", "stat": " src/foo.py | 12 +++++------\n 1 file changed, 6 insertions(+), 6 deletions(-)\n"}
```

## `codeward gain --json [--repo|--all]`

```json
{
  "command": "gain",
  "scope": "global",
  "rows": [
    {"ts": 1778277470.5, "command": "direct: codeward read rich/console.py", "raw_tokens": 33929, "output_tokens": 1718, "saved_tokens": 32211, "repo": "/home/me/projects/example"}
  ],
  "summary": {"commands_tracked": 1, "tokens_saved": 32211, "raw_tokens": 33929, "pct_saved": 94.94},
  "rtk_active": true
}
```

`scope` is `global` (default — aggregated across all repos), `repo` (just the current repo), or `all` (deduped union). Rows in `global`/`all` scope carry a `repo` field with the originating repo's absolute path.

## `codeward budget --json [target]`

```json
{
  "command": "budget",
  "target": ".",
  "estimated_raw_code_tokens": 61236,
  "files_analyzed": 8,
  "files": [
    {
      "path": "src/codeward/cli.py",
      "language": "Python",
      "lines": 1993,
      "raw_tokens": 23131,
      "symbols": 59,
      "dependents": 0,
      "tests": 0,
      "recommended_command": "codeward read src/codeward/cli.py"
    }
  ],
  "tips": ["Use codeward read <file> before raw cat/head/tail."]
}
```

## `codeward pack --json <target>`

```json
{
  "command": "pack",
  "target": "src/services/user_service.py",
  "max_tokens": 800,
  "estimated_pack_tokens": 180,
  "included_files": ["src/services/user_service.py", "tests/test_user_service.py"],
  "files": [
    {
      "path": "src/services/user_service.py",
      "relation": "target",
      "language": "Python",
      "lines": 42,
      "raw_tokens": 420,
      "symbols": ["class UserService", "def create_user(self, email: str) -> dict"],
      "side_effects": ["DB write"],
      "dependents": ["src/controllers/user_controller.py"],
      "tests": ["tests/test_user_service.py"]
    }
  ]
}
```

`relation` is one of `target`, `likely-test`, `dependent`, `co-change`, `search-hit`. `co-change` rows come from git history (files that historically move together with the target); only the top 3 are included.

## `codeward hotspots --json [--since 90d] [--top N]`

```json
{
  "command": "hotspots",
  "since": "90d",
  "top": 10,
  "files_analyzed": 24,
  "files": [
    {
      "path": "src/codeward/cli.py",
      "commits": 42,
      "dependents": 7,
      "risk_score": 336,
      "rationale": "42 commits in 90d x 7 dependents = 336"
    }
  ]
}
```

`risk_score = commits * (1 + dependents)`. Returns `{"files": []}` in non-git directories or when the window has no commits.

## `codeward neighbors --json <file> [--since 90d] [--top N]`

```json
{
  "command": "neighbors",
  "file": "src/codeward/cli.py",
  "since": "90d",
  "top": 10,
  "neighbors": [
    {"path": "src/codeward/index.py", "co_changes": 14},
    {"path": "tests/test_cli.py", "co_changes": 11}
  ]
}
```

Exit code is `2` when `<file>` is not in the index. Returns `{"neighbors": []}` when the file is indexed but the git history window contains no commits touching it.

## `codeward doctor --json`

```json
{
  "command": "doctor",
  "lines": ["Codeward doctor", "RTK: present at /usr/local/bin/rtk (rtk 0.39.0)", "..."],
  "issues": [],
  "ok": true
}
```

`ok` mirrors the exit code (`0` ↔ `true`, `1` ↔ `false`).
