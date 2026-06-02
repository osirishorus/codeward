from __future__ import annotations

import ast
import fnmatch
import hashlib
import io
import os
import re
import sqlite3
import subprocess
import tokenize
import tomllib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

CODE_EXTS = {
    ".py",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx",
    ".rs", ".go", ".java", ".rb", ".php", ".cs",
    # Extended language coverage (tree-sitter analyzers; regex fallback if grammar missing).
    ".c", ".h",
    ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx",
    ".kt", ".kts",
    ".swift",
    ".scala", ".sc",
    ".sh", ".bash",
    ".lua",
    ".ex", ".exs",
}
# Skip files larger than this — almost always generated, vendored, or minified bundles
# that would dominate index time without producing useful semantic information.
MAX_INDEXABLE_BYTES = 1_500_000
TEST_PATTERNS = [
    "test_*.py", "*_test.py",
    "*.test.ts", "*.test.tsx", "*.test.js", "*.test.jsx",
    "*.spec.ts", "*.spec.tsx", "*.spec.js", "*.spec.jsx",
    "*_test.go", "*_test.rs",
]
TEST_DIR_SEGMENTS = {"tests", "test", "__tests__", "spec", "specs"}
IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".next", ".cache"}
CACHE_SCHEMA_VERSION = "2"
ANALYZER_VERSION = "2026-05-17"


@dataclass
class Symbol:
    name: str
    kind: str
    file: str
    line: int
    methods: list[str] = field(default_factory=list)
    signature: str = ""
    end_line: int = 0
    analyzer: str = "regex"
    precision: str = "heuristic"
    confidence: str = "low"


@dataclass
class Reference:
    file: str
    line: int
    text: str
    analyzer: str = "regex"
    precision: str = "heuristic"
    confidence: str = "low"
    kind: str = "reference"
    # Column (0-based) used to disambiguate multiple occurrences on the same
    # line — critical when a definition and its call site share a line (common
    # in compact Java/PHP/C# code, or `def foo(): return foo()` patterns).
    column: int = -1


@dataclass
class FileInfo:
    path: str
    lang: str
    lines: int
    imports: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    routes: dict[str, str] = field(default_factory=dict)
    side_effects: list[str] = field(default_factory=list)
    # Raw import targets captured at parse time. Tuples of (level, module_or_path).
    # level=0 means absolute import; level>0 is Python relative imports.
    # For non-Python languages, this stores the literal path string from
    # `require('...')` or `from '...'` and level is always 0.
    raw_imports: list[tuple[int, str]] = field(default_factory=list)
    # Files in this repo that this file actually depends on, resolved post-build.
    resolved_deps: list[str] = field(default_factory=list)
    analyzer: str = "regex"
    precision: str = "heuristic"
    confidence: str = "low"


class RepoIndex:
    def __init__(self, root: Path | str = ".", *, use_cache: bool | None = None) -> None:
        self.root = Path(root).resolve()
        self.files: dict[str, FileInfo] = {}
        self._text_cache: dict[str, str] = {}
        self._inverse_deps: dict[str, set[str]] = {}
        self._loaded_from_cache = False
        # Per-repo overrides loaded from .codeward/config.toml. Falls back to
        # module-level defaults when no config file is present.
        self.config = load_repo_config(self.root)
        self._cache_config_hash = config_fingerprint(self.root)
        self.ignore_dirs = IGNORE_DIRS | set(self.config.get("ignore_dirs", []))
        self.test_patterns = list(TEST_PATTERNS) + list(self.config.get("extra_test_patterns", []))
        self.extra_test_dirs = set(self.config.get("extra_test_dirs", []))
        if use_cache is None:
            use_cache = os.environ.get("CODEWARD_NO_CACHE") != "1"
        if use_cache and self._try_load_cache():
            self._loaded_from_cache = True
            self._rebuild_inverse_deps()
            return
        self._build()
        if use_cache:
            try:
                self.write_sqlite()
            except (OSError, sqlite3.Error):
                pass

    def _iter_files(self) -> Iterable[Path]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in self.ignore_dirs and not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix in CODE_EXTS or any(fnmatch.fnmatch(name, pat) for pat in self.test_patterns):
                    yield p

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def _source_file_state(self) -> tuple[float, set[str]]:
        """Return (newest_mtime, set_of_relpaths) of all indexable files under root.
        Used to invalidate the cache on adds, deletes, or modifications.
        Mirrors the size filter in _build() so the cached file set agrees."""
        newest = 0.0
        rels: set[str] = set()
        for p in self._iter_files():
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > MAX_INDEXABLE_BYTES:
                continue
            if st.st_mtime > newest:
                newest = st.st_mtime
            try:
                rels.add(p.relative_to(self.root).as_posix())
            except ValueError:
                pass
        return newest, rels

    def _newest_source_mtime(self) -> float:
        return self._source_file_state()[0]

    def _try_load_cache(self) -> bool:
        db_path = self.root / ".codeward" / "index.sqlite"
        if not db_path.exists():
            return False
        try:
            cache_mtime = db_path.stat().st_mtime
        except OSError:
            return False
        newest, current_files = self._source_file_state()
        if newest >= cache_mtime:
            return False
        try:
            self._load_sqlite(db_path)
        except (sqlite3.Error, OSError):
            self.files = {}
            return False
        # If the cached file set diverges from the on-disk set (file added or deleted
        # without modifying any other source mtime), force a rebuild.
        if set(self.files.keys()) != current_files:
            self.files = {}
            return False
        return True

    def _load_sqlite(self, db_path: Path) -> None:
        con = sqlite3.connect(db_path)
        try:
            tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
            if "resolved_deps" not in tables or "metadata" not in tables:
                # Old cache format — force a rebuild.
                raise sqlite3.Error("cache schema outdated")
            metadata = {
                key: value
                for key, value in con.execute("select key, value from metadata")
            }
            expected = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "analyzer_version": ANALYZER_VERSION,
                "config_hash": self._cache_config_hash,
            }
            if any(metadata.get(key) != value for key, value in expected.items()):
                raise sqlite3.Error("cache metadata stale")
            sym_cols = {row[1] for row in con.execute("pragma table_info(symbols)")}
            file_cols = {row[1] for row in con.execute("pragma table_info(files)")}
            if not {"analyzer", "precision", "confidence"} <= file_cols:
                raise sqlite3.Error("cache schema outdated")
            if not {"signature", "end_line", "analyzer", "precision", "confidence"} <= sym_cols:
                # Pre-signature cache; rebuild to populate the new columns.
                raise sqlite3.Error("cache schema outdated")
            files = {
                row[0]: FileInfo(path=row[0], lang=row[1], lines=row[2], analyzer=row[3], precision=row[4], confidence=row[5])
                for row in con.execute("select path, lang, lines, analyzer, precision, confidence from files")
            }
            for file_path, name in con.execute("select file, name from imports"):
                if file_path in files:
                    files[file_path].imports.append(name)
            for file_path, name, kind, line, methods, signature, end_line, analyzer, precision, confidence in con.execute("select file, name, kind, line, methods, signature, end_line, analyzer, precision, confidence from symbols"):
                if file_path in files:
                    method_list = [m for m in methods.split(",") if m] if methods else []
                    files[file_path].symbols.append(Symbol(name=name, kind=kind, file=file_path, line=line, methods=method_list, signature=signature or "", end_line=end_line or 0, analyzer=analyzer, precision=precision, confidence=confidence))
            for file_path, route, handler in con.execute("select file, route, handler from routes"):
                if file_path in files:
                    files[file_path].routes[route] = handler
            for file_path, label in con.execute("select file, label from side_effects"):
                if file_path in files:
                    files[file_path].side_effects.append(label)
            for file_path, dep in con.execute("select file, dep from resolved_deps"):
                if file_path in files:
                    files[file_path].resolved_deps.append(dep)
        finally:
            con.close()
        self.files = files

    def _build(self) -> None:
        for p in self._iter_files():
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > MAX_INDEXABLE_BYTES:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = self._rel(p)
            self._text_cache[rel] = text
            self.files[rel] = analyze_file(rel, text, custom_side_effect_rules=self.config.get("custom_side_effect_rules"))
        self._resolve_all_imports()

    def is_test_file(self, path: str) -> bool:
        """Instance method: applies repo config (extra_test_dirs, extra_test_patterns)."""
        return is_test_file(path, extra_dirs=self.extra_test_dirs, extra_patterns=self.config.get("extra_test_patterns"))

    def _resolve_all_imports(self) -> None:
        """Second pass: turn raw imports into concrete file paths inside the repo."""
        by_suffix: dict[str, list[str]] = {}
        for rel in self.files:
            for s in _path_suffixes(rel):
                by_suffix.setdefault(s, []).append(rel)
        for rel, info in self.files.items():
            resolved: list[str] = []
            for level, target in info.raw_imports:
                hit = self._resolve_one(rel, level, target, by_suffix)
                if hit and hit != rel:
                    resolved.append(hit)
            info.resolved_deps = sorted(set(resolved))
        self._rebuild_inverse_deps()

    def _rebuild_inverse_deps(self) -> None:
        """Precompute reverse adjacency so dependents_of_file is O(1) lookup."""
        inv: dict[str, set[str]] = {}
        for rel, info in self.files.items():
            for dep in info.resolved_deps:
                inv.setdefault(dep, set()).add(rel)
        self._inverse_deps = inv

    def _resolve_one(self, from_rel: str, level: int, target: str, by_suffix: dict[str, list[str]]) -> str | None:
        if level > 0:
            anchor = Path(from_rel).parent
            for _ in range(level - 1):
                anchor = anchor.parent if anchor != Path(".") else anchor
            base = (anchor / target.replace(".", "/")) if target else anchor
            return _first_existing(self.files, [
                f"{base.as_posix()}.py",
                f"{base.as_posix()}/__init__.py",
            ])
        if not target:
            return None
        if target.startswith("./") or target.startswith("../"):
            # Resolve lexically against self.root rather than process cwd, so the
            # index is correct regardless of where RepoIndex was constructed from.
            anchor_parts = Path(from_rel).parent.parts
            target_parts = Path(target).parts
            stack = list(anchor_parts)
            for part in target_parts:
                if part == ".":
                    continue
                if part == "..":
                    if stack:
                        stack.pop()
                    continue
                stack.append(part)
            rel_base = "/".join(stack)
            # ESM/TS convention: imports use '.js' but the actual file may be '.ts'.
            # Strip JS-family extensions before trying candidates so we also match the source.
            stems = [rel_base]
            for ext in (".js", ".mjs", ".cjs", ".jsx"):
                if rel_base.endswith(ext):
                    stems.append(rel_base[: -len(ext)])
                    break
            candidates = []
            for stem in stems:
                candidates.extend([
                    stem,
                    f"{stem}.py", f"{stem}.js", f"{stem}.jsx", f"{stem}.ts", f"{stem}.tsx",
                    f"{stem}.mjs", f"{stem}.cjs",
                    f"{stem}/index.js", f"{stem}/index.ts", f"{stem}/index.jsx", f"{stem}/index.tsx",
                    f"{stem}/__init__.py",
                ])
            return _first_existing(self.files, candidates)
        # Absolute module path: try suffix-match against indexed files.
        # Prefer packages (__init__.py / index.{js,ts}) over single-file modules so that
        # `import flask` resolves to `src/flask/__init__.py` rather than a random `flask.py`.
        target_path = target.replace(".", "/")
        candidate_suffixes = [
            f"{target_path}/__init__.py",
            f"{target_path}/index.js",
            f"{target_path}/index.ts",
            f"{target_path}/index.jsx",
            f"{target_path}/index.tsx",
            f"{target_path}.py",
            f"{target_path}.js",
            f"{target_path}.ts",
            f"{target_path}.jsx",
            f"{target_path}.tsx",
            f"{target_path}.mjs",
            f"{target_path}.cjs",
        ]
        for suf in candidate_suffixes:
            if suf in self.files:
                return suf
            matches = by_suffix.get(suf, [])
            if matches:
                # Prefer paths under common source roots (src/, lib/) and shortest path overall.
                def score(p: str) -> tuple[int, int, int]:
                    parts = p.split("/")
                    in_src = 0 if parts[0] in {"src", "lib", "pkg", "internal"} else 1
                    not_in_test = 0 if "test" not in p.lower() else 1
                    return (not_in_test, in_src, len(p))
                return min(matches, key=score)
        return None

    def text(self, rel: str) -> str:
        rel = rel.replace("\\", "/")
        if rel not in self._text_cache:
            p = self.root / rel
            return p.read_text(encoding="utf-8", errors="replace")
        return self._text_cache[rel]

    @property
    def code_files(self) -> list[str]:
        return sorted(p for p in self.files if Path(p).suffix in CODE_EXTS)

    @property
    def test_files(self) -> list[str]:
        return sorted(p for p in self.files if self.is_test_file(p))

    def find_symbol(self, name: str) -> list[Symbol]:
        """Returns matching symbols ordered by match quality: exact name match
        first, then method-suffix match, then methods-list fallback. Callers
        like `cmd_slice` use `syms[0]`, so ranking matters."""
        exact: list[Symbol] = []
        method_suffix: list[Symbol] = []
        methods_list_match: list[Symbol] = []
        for info in self.files.values():
            for sym in info.symbols:
                if sym.name == name:
                    exact.append(sym)
                    continue
                if sym.kind == "method" and sym.name.endswith("." + name):
                    method_suffix.append(sym)
                    continue
                if name in [f"{sym.name}.{m}" for m in sym.methods]:
                    methods_list_match.append(sym)
        return exact + method_suffix + methods_list_match

    def find_symbol_fuzzy(self, name: str, limit: int = 10) -> list[Symbol]:
        """Loose fallback for `find_symbol`. Tries case-insensitive exact match,
        then case-insensitive trailing-dot suffix, then case-insensitive
        substring. Returns at most `limit` unique symbols. Empty list when
        nothing plausible exists; callers should use this only after the
        strict path returns empty."""
        if not name:
            return []
        lower = name.lower()
        ci_exact: list[Symbol] = []
        ci_suffix: list[Symbol] = []
        ci_substring: list[Symbol] = []
        seen: set[tuple[str, str, int]] = set()
        for info in self.files.values():
            for sym in info.symbols:
                sym_lower = sym.name.lower()
                bucket: list[Symbol] | None = None
                if sym_lower == lower:
                    bucket = ci_exact
                elif sym_lower.endswith("." + lower):
                    bucket = ci_suffix
                elif lower in sym_lower:
                    bucket = ci_substring
                if bucket is None:
                    continue
                key = (sym.name, sym.file, sym.line)
                if key in seen:
                    continue
                seen.add(key)
                bucket.append(sym)
        return (ci_exact + ci_suffix + ci_substring)[:limit]

    def callers_of(self, name: str, scope: set[str] | None = None) -> list[tuple[str, int, str]]:
        """Find lines that reference `name`. If `scope` is provided, only those
        files are scanned (typical use: limit to direct dependents). Otherwise
        all files are scanned, which is slow on large repos."""
        return [(r.file, r.line, r.text) for r in self.references_to(name, scope=scope)]

    def references_to(self, name: str, scope: set[str] | None = None) -> list[Reference]:
        """Find reference sites with analyzer provenance.

        Python files use AST nodes so definition sites and local shadowing can be
        excluded structurally. Tree-sitter languages use syntax nodes. Regex is
        retained as the low-confidence fallback for parse failures and text-like
        languages.
        """
        hits: list[Reference] = []
        target = name.split(".")[-1]
        pattern = re.compile(rf"\b{re.escape(name.split('.')[-1])}\b")
        targets = scope if scope is not None else self.files.keys()
        for rel in targets:
            if rel not in self.files:
                continue
            text = self.text(rel)
            info = self.files[rel]
            if info.analyzer == "python_ast":
                hits.extend(_python_references(rel, text, name))
                continue
            if info.analyzer == "tree_sitter":
                ts_hits = _treesitter_references(rel, text, target)
                if ts_hits:
                    hits.extend(ts_hits)
                    continue
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                for m in pattern.finditer(line):
                    # Skip the definition-line occurrence; keep call sites that
                    # share the same line (common in single-line class bodies).
                    col = m.start()
                    def_match = re.search(rf"\b(class|def|function|fn|func)\s+{re.escape(target)}\b", line)
                    if def_match and def_match.start() <= col < def_match.end():
                        continue
                    hits.append(Reference(rel, i, stripped, column=col))
        return sorted(_dedupe_refs(hits), key=lambda r: (r.file, r.line, r.column, r.text))

    def dependents_of_file(self, rel: str) -> list[str]:
        deps: set[str] = set(self._inverse_deps.get(rel, set()))
        deps.discard(rel)
        return sorted(deps)

    def transitively_affected(self, seeds: Iterable[str], *, max_depth: int | None = None) -> list[str]:
        """Every file that transitively depends on any of `seeds`, via the
        reverse-dependency graph (`_inverse_deps`). Seeds that are indexed
        files are included in the result. `max_depth` caps the number of hops
        from a seed (None = unbounded). Visited-set guards import cycles.

        Used by `codeward affected` to turn a change set into the full blast
        radius — `dependents_of_file` only answers the single-hop case."""
        result: set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        seen: set[str] = set()
        for s in seeds:
            s = s.replace("\\", "/")
            if s in seen:
                continue
            seen.add(s)
            if s in self.files:
                result.add(s)
            queue.append((s, 0))
        while queue:
            node, depth = queue.popleft()
            if max_depth is not None and depth >= max_depth:
                continue
            for dependent in sorted(self._inverse_deps.get(node, set())):
                if dependent in seen:
                    continue
                seen.add(dependent)
                result.add(dependent)
                queue.append((dependent, depth + 1))
        return sorted(result)

    def dependency_path(self, src: str, dst: str, *, direction: str = "forward") -> list[str] | None:
        """Shortest path from `src` to `dst` over the dependency graph,
        returned as an inclusive node list (or None if unconnected).

        `forward` follows `resolved_deps` (src imports … imports dst).
        `reverse` follows `_inverse_deps` (dst imports … imports src), so the
        returned path still reads src→…→dst but explains reverse coupling.
        BFS gives the shortest chain; visited-set guards cycles."""
        src = src.replace("\\", "/")
        dst = dst.replace("\\", "/")
        if src not in self.files or dst not in self.files:
            return None
        if src == dst:
            return [src]

        def neighbors(node: str) -> list[str]:
            if direction == "reverse":
                return sorted(self._inverse_deps.get(node, set()))
            info = self.files.get(node)
            return list(info.resolved_deps) if info else []

        prev: dict[str, str | None] = {src: None}
        queue: deque[str] = deque([src])
        while queue:
            node = queue.popleft()
            for nb in neighbors(node):
                if nb in prev:
                    continue
                prev[nb] = node
                if nb == dst:
                    path: list[str] = []
                    cur: str | None = dst
                    while cur is not None:
                        path.append(cur)
                        cur = prev[cur]
                    return list(reversed(path))
                queue.append(nb)
        return None

    def tests_for(self, target: str) -> list[str]:
        target = target.replace("\\", "/")
        path = Path(target)
        base = path.stem.replace("test_", "")
        base_stem = (base or "").lower().replace("_", "")
        module_parts = [p.lower().replace("_", "") for p in path.with_suffix("").parts if p not in {"src", "lib"}]
        normalized_module = "/".join(module_parts)
        # Tests that resolve-import the target file via the precomputed graph (fast).
        importers = {f for f in self._inverse_deps.get(target, set()) if self.is_test_file(f)}
        # Prefer exact repo-local naming relationships before falling back to
        # fuzzy stem containment. This catches `cli.py -> test_cli.py` and
        # `src/services/user_service.py -> tests/test_user_service.py`.
        out = set(importers)
        for tf in self.test_files:
            tf_path = Path(tf)
            tf_stem = tf_path.stem.lower().replace("_", "")
            test_base = tf_stem[4:] if tf_stem.startswith("test") else tf_stem
            normalized_test_path = "/".join(
                p.lower().replace("_", "")
                for p in tf_path.with_suffix("").parts
                if p not in {"tests", "test", "spec"}
            )
            if base_stem and test_base == base_stem:
                out.add(tf)
                continue
            if normalized_module and normalized_module in normalized_test_path:
                out.add(tf)
                continue
            if base_stem and len(base_stem) >= 4 and base_stem in tf_stem:
                out.add(tf)
        return sorted(out)

    def search(self, query: str, include_tests: bool = False) -> list[tuple[str, int, str]]:
        hits = []
        for rel in self.files:
            if not include_tests and is_test_file(rel):
                continue
            text = self.text(rel)
            for i, line in enumerate(text.splitlines(), 1):
                if query in line:
                    hits.append((rel, i, line.strip()))
        return hits

    def changed_files(self, base: str | None = None) -> list[str]:
        cmd = ["git", "diff", "--name-only"]
        if base:
            cmd.insert(2, base)
        try:
            cp = subprocess.run(cmd, cwd=self.root, text=True, capture_output=True, timeout=10)
            files = [f.strip() for f in cp.stdout.splitlines() if f.strip()]
            cp2 = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=self.root, text=True, capture_output=True, timeout=10)
            files += [f.strip() for f in cp2.stdout.splitlines() if f.strip()]
            return sorted(set(f for f in files if f in self.files or (self.root / f).exists()))
        except Exception:
            return []

    def write_sqlite(self, db_path: Path | None = None) -> Path:
        """Persist the current lightweight semantic index for large-repo reuse/tools."""
        db_path = db_path or (self.root / ".codeward" / "index.sqlite")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db_path)
        try:
            con.executescript(
                """
                drop table if exists files;
                drop table if exists imports;
                drop table if exists symbols;
                drop table if exists routes;
                drop table if exists side_effects;
                drop table if exists resolved_deps;
                drop table if exists metadata;
                create table files(path text primary key, lang text not null, lines integer not null, is_test integer not null, analyzer text not null default 'regex', precision text not null default 'heuristic', confidence text not null default 'low');
                create table imports(file text not null, name text not null);
                create table symbols(file text not null, name text not null, kind text not null, line integer not null, methods text not null, signature text not null default '', end_line integer not null default 0, analyzer text not null default 'regex', precision text not null default 'heuristic', confidence text not null default 'low');
                create table routes(file text not null, route text not null, handler text not null);
                create table side_effects(file text not null, label text not null);
                create table resolved_deps(file text not null, dep text not null);
                create table metadata(key text primary key, value text not null);
                """
            )
            con.executemany(
                "insert into metadata(key, value) values (?, ?)",
                [
                    ("schema_version", CACHE_SCHEMA_VERSION),
                    ("analyzer_version", ANALYZER_VERSION),
                    ("config_hash", self._cache_config_hash),
                ],
            )
            for info in self.files.values():
                con.execute(
                    "insert into files(path, lang, lines, is_test, analyzer, precision, confidence) values (?, ?, ?, ?, ?, ?, ?)",
                    (info.path, info.lang, info.lines, int(self.is_test_file(info.path)), info.analyzer, info.precision, info.confidence),
                )
                con.executemany("insert into imports(file, name) values (?, ?)", [(info.path, x) for x in info.imports])
                con.executemany(
                    "insert into symbols(file, name, kind, line, methods, signature, end_line, analyzer, precision, confidence) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(info.path, s.name, s.kind, s.line, ",".join(s.methods), s.signature, s.end_line, s.analyzer, s.precision, s.confidence) for s in info.symbols],
                )
                con.executemany("insert into routes(file, route, handler) values (?, ?, ?)", [(info.path, r, h) for r, h in info.routes.items()])
                con.executemany("insert into side_effects(file, label) values (?, ?)", [(info.path, x) for x in info.side_effects])
                con.executemany("insert into resolved_deps(file, dep) values (?, ?)", [(info.path, d) for d in info.resolved_deps])
            con.commit()
        finally:
            con.close()
        return db_path


def _dedupe_refs(refs: list[Reference]) -> list[Reference]:
    seen: set[tuple[str, int, int, str]] = set()
    out: list[Reference] = []
    for r in refs:
        # Include column so two references on the same line (a definition and
        # an in-body call) don't collapse into one.
        key = (r.file, r.line, r.column, r.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _python_references(rel: str, text: str, name: str) -> list[Reference]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    bare = name.rsplit(".", 1)[-1]
    aliases = {bare}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == bare or alias.name.rsplit(".", 1)[-1] == bare:
                    aliases.add(alias.asname or alias.name.rsplit(".", 1)[-1])

    refs: list[Reference] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.shadow_stack: list[set[str]] = []

        def _line(self, node: ast.AST) -> str:
            lineno = getattr(node, "lineno", 0) or 0
            return lines[lineno - 1].strip() if 1 <= lineno <= len(lines) else ""

        def _shadowed(self, value: str) -> bool:
            return any(value in scope for scope in self.shadow_stack)

        def _local_defs(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
            names: set[str] = set()
            args = node.args
            for arg in [*getattr(args, "posonlyargs", []), *args.args, *args.kwonlyargs]:
                names.add(arg.arg)
            if args.vararg:
                names.add(args.vararg.arg)
            if args.kwarg:
                names.add(args.kwarg.arg)
            for child in ast.walk(node):
                if child is node:
                    continue
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                for target in getattr(child, "targets", []):
                    names.update(_assigned_names(target))
                if isinstance(child, ast.NamedExpr):
                    names.update(_assigned_names(child.target))
                elif isinstance(child, (ast.For, ast.AsyncFor)):
                    names.update(_assigned_names(child.target))
                elif isinstance(child, (ast.With, ast.AsyncWith)):
                    for item in child.items:
                        if item.optional_vars:
                            names.update(_assigned_names(item.optional_vars))
                elif isinstance(child, ast.ExceptHandler) and child.name:
                    names.add(child.name)
            return names

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.shadow_stack.append(self._local_defs(node))
            self.generic_visit(node)
            self.shadow_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:
            self.shadow_stack.append(self._local_defs(node))
            self.generic_visit(node)
            self.shadow_stack.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword)
            for stmt in node.body:
                self.visit(stmt)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load) and node.id in aliases and not self._shadowed(node.id):
                refs.append(Reference(rel, node.lineno, self._line(node), "python_ast", "exact_range", "high", "name", getattr(node, "col_offset", -1)))

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr == bare:
                refs.append(Reference(rel, node.lineno, self._line(node), "python_ast", "exact_range", "high", "attribute", getattr(node, "col_offset", -1)))
            self.generic_visit(node)

    Visitor().visit(tree)
    return _dedupe_refs(refs)


def _assigned_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for elt in node.elts:
            out.update(_assigned_names(elt))
        return out
    return set()


def _treesitter_references(rel: str, text: str, target: str) -> list[Reference]:
    try:
        from .analyzers.treesitter import parse_for_path
    except Exception:
        return []
    parsed = parse_for_path(rel, text)
    if parsed is None:
        return []
    root, src = parsed
    lines = text.splitlines()
    refs: list[Reference] = []
    definition_parent_types = {
        "function_declaration", "method_declaration", "class_declaration", "interface_declaration",
        "enum_declaration", "struct_item", "enum_item", "trait_item", "function_item",
        "type_declaration", "type_spec", "method_definition", "function_definition",
        "class", "module", "method", "class_declaration", "constructor_declaration",
        "variable_declarator", "type_alias_declaration", "lexical_declaration",
    }

    def parent_type(node) -> str:
        p = getattr(node, "parent", None)
        return p.type if p is not None else ""

    # `name`: PHP uses this for both declaration names and call-site names.
    # `simple_identifier`: Kotlin/Swift/Scala variants.
    # `scoped_identifier`: Java fully-qualified names.
    def visit(node) -> None:
        if node.type in {
            "identifier", "field_identifier", "property_identifier",
            "constant", "type_identifier", "name", "simple_identifier",
            "scoped_identifier",
        }:
            value = src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            if value == target and parent_type(node) not in definition_parent_types:
                line = node.start_point[0] + 1
                col = node.start_point[1]
                refs.append(Reference(rel, line, lines[line - 1].strip() if 1 <= line <= len(lines) else "", "tree_sitter", "syntax_aware", "medium", node.type, col))
        for child in node.children:
            visit(child)

    visit(root)
    return _dedupe_refs(refs)


def _path_suffixes(rel: str) -> list[str]:
    """Return suffixes of `rel` that an absolute import might resolve to.
    For 'src/flask/app.py' yields ['src/flask/app.py', 'flask/app.py', 'app.py']."""
    parts = rel.split("/")
    out = []
    for i in range(len(parts)):
        out.append("/".join(parts[i:]))
    return out


def _first_existing(files: dict, candidates: list[str]) -> str | None:
    for c in candidates:
        c = c.replace("\\", "/")
        if c in files:
            return c
    return None


def is_test_file(path: str, *, extra_dirs: set[str] | None = None, extra_patterns: list[str] | None = None) -> bool:
    p = Path(path)
    name = p.name
    patterns = list(TEST_PATTERNS) + (list(extra_patterns) if extra_patterns else [])
    if any(fnmatch.fnmatch(name, pat) for pat in patterns):
        return True
    parts = {seg.lower() for seg in p.parts[:-1]}
    test_dirs = TEST_DIR_SEGMENTS | (set(extra_dirs) if extra_dirs else set())
    return bool(parts & test_dirs)


def config_fingerprint(root: Path) -> str:
    cfg_path = root / ".codeward" / "config.toml"
    try:
        data = cfg_path.read_bytes()
    except OSError:
        return "missing"
    return hashlib.sha256(data).hexdigest()


def load_repo_config(root: Path) -> dict:
    """Load .codeward/config.toml. Returns flat dict with these keys:
    - ignore_dirs: list[str] (added to IGNORE_DIRS for indexing)
    - extra_test_dirs: list[str] (added to TEST_DIR_SEGMENTS)
    - extra_test_patterns: list[str] (fnmatch patterns added to TEST_PATTERNS)
    - custom_side_effect_rules: list[(compiled_re, label)] (extra effect labels)
    Missing config or malformed TOML returns empty dict. Doctor surfaces errors."""
    cfg_path = root / ".codeward" / "config.toml"
    if not cfg_path.exists():
        return {}
    try:
        data = tomllib.loads(cfg_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {"_error": f"malformed {cfg_path}"}
    out: dict = {}
    idx_section = data.get("index") or {}
    if isinstance(idx_section, dict):
        if isinstance(idx_section.get("ignore_dirs"), list):
            out["ignore_dirs"] = [str(x) for x in idx_section["ignore_dirs"]]
        if isinstance(idx_section.get("extra_test_dirs"), list):
            out["extra_test_dirs"] = [str(x).lower() for x in idx_section["extra_test_dirs"]]
        if isinstance(idx_section.get("extra_test_patterns"), list):
            out["extra_test_patterns"] = [str(x) for x in idx_section["extra_test_patterns"]]
    se = data.get("side_effects") or {}
    if isinstance(se, dict) and isinstance(se.get("custom_rules"), list):
        compiled: list[tuple[re.Pattern, str]] = []
        for rule in se["custom_rules"]:
            if not isinstance(rule, dict):
                continue
            pattern, label = rule.get("pattern"), rule.get("label")
            if not (isinstance(pattern, str) and isinstance(label, str)):
                continue
            try:
                compiled.append((re.compile(pattern), label))
            except re.error:
                continue
        if compiled:
            out["custom_side_effect_rules"] = compiled
    return out


def lang_for(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".py": "Python",
        ".ts": "TypeScript", ".tsx": "TypeScript",
        ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
        ".rs": "Rust",
        ".go": "Go",
        ".rb": "Ruby",
        ".java": "Java",
        ".php": "PHP",
        ".cs": "C#",
        ".c": "C", ".h": "C",
        ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++", ".hxx": "C++",
        ".kt": "Kotlin", ".kts": "Kotlin",
        ".swift": "Swift",
        ".scala": "Scala", ".sc": "Scala",
        ".sh": "Bash", ".bash": "Bash",
        ".lua": "Lua",
        ".ex": "Elixir", ".exs": "Elixir",
    }.get(ext, ext.lstrip(".") or "text")


def analyze_file(path: str, text: str, *, custom_side_effect_rules: list[tuple] | None = None) -> FileInfo:
    info = FileInfo(path=path, lang=lang_for(path), lines=len(text.splitlines()))
    if Path(path).suffix == ".py":
        analyze_python(info, text)
    else:
        # Try tree-sitter for syntax-aware symbol extraction with end_line + signature.
        # Falls back to regex for languages without a grammar or when tree-sitter
        # isn't installed. The regex path also handles imports for all languages
        # (tree-sitter analyzers focus on syntax shape; refs are collected separately).
        ts_used = False
        try:
            from .analyzers.treesitter import analyze_treesitter
            ts_used = analyze_treesitter(info, text)
        except Exception:
            ts_used = False
        if ts_used:
            # Tree-sitter handled symbols; still need imports from the regex pass.
            info.analyzer = "tree_sitter"
            info.precision = "syntax_aware"
            info.confidence = "medium"
            for s in info.symbols:
                s.analyzer = "tree_sitter"
                s.precision = "exact_range" if s.end_line else "syntax_aware"
                s.confidence = "medium"
            _extract_imports_only(info, text)
        else:
            analyze_generic(info, text)
    route_scan = strip_comments_and_docstrings(text, info.lang)
    info.routes = extract_routes(route_scan)
    info.side_effects = extract_side_effects(text, info.lang, extra_rules=custom_side_effect_rules)
    return info


def _extract_imports_only(info: FileInfo, text: str) -> None:
    """When tree-sitter handles symbols, still run the regex pass for imports/raw_imports
    so the dependency graph still works."""
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("//", "#", "/*", "*")):
            continue
        for m in _REQUIRE_PATTERN.finditer(line):
            target = m.group(1)
            info.imports.append(target)
            info.raw_imports.append((0, target))
        for m in _IMPORT_FROM_PATTERN.finditer(line):
            target = m.group(1)
            info.imports.append(target)
            info.raw_imports.append((0, target))
        for m in _GO_IMPORT_PATTERN.finditer(line):
            target = m.group(1)
            info.imports.append(target)
            info.raw_imports.append((0, target))
        for m in _RUST_USE_PATTERN.finditer(line):
            target = m.group(1)
            info.imports.append(target)
            info.raw_imports.append((0, target))


def analyze_python(info: FileInfo, text: str) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        analyze_generic(info, text)
        return
    info.analyzer = "python_ast"
    info.precision = "exact_range"
    info.confidence = "high"
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                info.imports.append(alias.name)
                info.raw_imports.append((0, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level or 0
            info.imports.append(module)
            info.raw_imports.append((level, module))
            # `from . import x, y` and `from pkg import submodule` may target sibling
            # files. Try resolving each name as a submodule too — harmless if it
            # resolves to nothing, useful for namespace packages without __init__.py.
            for alias in node.names:
                if alias.name == "*":
                    continue
                combined = f"{module}.{alias.name}" if module else alias.name
                info.raw_imports.append((level, combined))
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            bases = ", ".join(_unparse(b) for b in node.bases)
            sig = f"class {node.name}({bases})" if bases else f"class {node.name}"
            info.symbols.append(Symbol(
                node.name, "class", info.path, node.lineno, methods,
                signature=sig, end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                analyzer="python_ast", precision="exact_range", confidence="high",
            ))
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    info.symbols.append(Symbol(
                        f"{node.name}.{member.name}", "method", info.path, member.lineno,
                        signature=_format_signature(member),
                        end_line=getattr(member, "end_lineno", member.lineno) or member.lineno,
                        analyzer="python_ast", precision="exact_range", confidence="high",
                    ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info.symbols.append(Symbol(
                node.name, "function", info.path, node.lineno,
                signature=_format_signature(node),
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                analyzer="python_ast", precision="exact_range", confidence="high",
            ))


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return getattr(node, "id", "") or type(node).__name__


def _format_signature(node) -> str:
    args = node.args
    parts: list[str] = []
    posonly = list(getattr(args, "posonlyargs", []) or [])
    regular = list(args.args or [])
    kwonly = list(args.kwonlyargs or [])

    pos_defaults = list(args.defaults or [])
    pos_total = posonly + regular
    pad = len(pos_total) - len(pos_defaults)
    pos_defaults = [None] * pad + pos_defaults

    def render(a: ast.arg, default) -> str:
        s = a.arg
        if a.annotation is not None:
            s += f": {_unparse(a.annotation)}"
        if default is not None:
            s += f" = {_unparse(default)}"
        return s

    for i, a in enumerate(posonly):
        parts.append(render(a, pos_defaults[i]))
    if posonly:
        parts.append("/")
    for j, a in enumerate(regular):
        parts.append(render(a, pos_defaults[len(posonly) + j]))
    if args.vararg is not None:
        v = args.vararg
        s = "*" + v.arg
        if v.annotation is not None:
            s += f": {_unparse(v.annotation)}"
        parts.append(s)
    elif kwonly:
        parts.append("*")
    kw_defaults = list(args.kw_defaults or [])
    for k, a in enumerate(kwonly):
        d = kw_defaults[k] if k < len(kw_defaults) else None
        parts.append(render(a, d))
    if args.kwarg is not None:
        v = args.kwarg
        s = "**" + v.arg
        if v.annotation is not None:
            s += f": {_unparse(v.annotation)}"
        parts.append(s)
    sig = f"def {node.name}({', '.join(parts)})"
    if node.returns is not None:
        sig += f" -> {_unparse(node.returns)}"
    return sig


_REQUIRE_PATTERN = re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_IMPORT_FROM_PATTERN = re.compile(r"""\b(?:import|from|export\s+(?:\*|\{[^}]*\}))\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]""")
_GO_IMPORT_PATTERN = re.compile(r"""^\s*(?:import\s+)?(?:[A-Za-z_]\w*\s+)?['"]([^'"]+)['"]""")
_RUST_USE_PATTERN = re.compile(r"""^\s*use\s+([A-Za-z_][\w:]*)""")


def analyze_generic(info: FileInfo, text: str) -> None:
    info.analyzer = "regex"
    info.precision = "heuristic"
    info.confidence = "low"
    lang = info.lang
    for i, line in enumerate(text.splitlines(), 1):
        if m := re.search(r"\b(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", line):
            info.symbols.append(Symbol(m.group(1), "class", info.path, i))
        if m := re.search(r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", line):
            info.symbols.append(Symbol(m.group(1), "function", info.path, i))
        if m := re.search(r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", line):
            info.symbols.append(Symbol(m.group(1), "function", info.path, i))
        stripped = line.strip()
        # Anywhere-in-line: require('...'), import ... from '...', export ... from '...'
        for match in _REQUIRE_PATTERN.finditer(line):
            target = match.group(1)
            info.imports.append(stripped or line.strip())
            info.raw_imports.append((0, target))
        for match in _IMPORT_FROM_PATTERN.finditer(line):
            target = match.group(1)
            info.imports.append(stripped or line.strip())
            info.raw_imports.append((0, target))
        if lang == "Rust":
            for match in _RUST_USE_PATTERN.finditer(line):
                info.imports.append(stripped)
                info.raw_imports.append((0, match.group(1).replace("::", ".")))
        if lang == "Go":
            for match in _GO_IMPORT_PATTERN.finditer(line):
                target = match.group(1)
                if "/" in target or "." not in target:
                    info.imports.append(stripped)
                    info.raw_imports.append((0, target))


# Routes are normalized to "<METHOD> <path>" → handler symbol.
# Each framework is matched by a separate pattern; extraction is lossy by
# design — the goal is "agent can find the handler from a URL" rather than
# perfect REST modeling.

# Generic dict-style routing: {"GET /foo": handler}
_ROUTES_DICT_RE = re.compile(r"['\"]([A-Z]+\s+/[^'\"]+)['\"]\s*:\s*([A-Za-z_][\w.]*)")

# Express / Koa / Hono: router.get('/foo', handler)
_ROUTES_EXPRESS_RE = re.compile(
    r"\b(?:router|app|api)\.(get|post|put|patch|delete|options|head|all|use)\("
    r"\s*['\"`]([^'\"`]+)['\"`]\s*,\s*(?:[^,)]*?,\s*)?([A-Za-z_$][\w$.]*)",
    re.I,
)

# FastAPI / Flask / Starlette / Sanic — both decorator and instance forms.
# `@router.get("/path") \n def handler(...)` and `@blueprint.route("/path", methods=["POST"])`
_ROUTES_PY_DECORATOR_RE = re.compile(
    r"@(?P<prefix>[A-Za-z_][\w]*)\.(?P<method>get|post|put|patch|delete|head|options|route|websocket|api_route)\(\s*"
    r"['\"](?P<path>[^'\"]+)['\"](?P<rest>[^)]*)\)"
    r"\s*\n\s*(?:async\s+)?def\s+(?P<handler>[A-Za-z_]\w*)",
    re.M,
)

# Django urlpatterns: path("foo/", views.bar) | re_path("...", views.bar) | url("...", views.bar)
_ROUTES_DJANGO_RE = re.compile(
    r"\b(?:path|re_path|url)\(\s*r?['\"](?P<path>[^'\"]+)['\"]\s*,\s*(?P<handler>[A-Za-z_][\w.]*)"
)

# NestJS: @Get('foo') / @Post('bar') above a method.
_ROUTES_NEST_RE = re.compile(
    r"@(?P<method>Get|Post|Put|Patch|Delete|Options|Head|All)\(\s*['\"`](?P<path>[^'\"`]*)['\"`]?[^)]*\)"
    r"\s*(?:public\s+|async\s+)*(?P<handler>[A-Za-z_]\w*)\s*\(",
    re.M,
)

# Spring (Java/Kotlin): @GetMapping("/foo") / @RequestMapping(value="/foo", method=RequestMethod.POST)
_ROUTES_SPRING_RE = re.compile(
    r"@(?P<ann>Get|Post|Put|Patch|Delete|Request)Mapping\(\s*"
    r"(?:value\s*=\s*)?['\"](?P<path>[^'\"]+)['\"][^)]*\)"
    r"[\s\S]{0,120}?\b(?:public|private|protected|static|fun|def)\s+[\w<>\[\],?\s]*?\s+(?P<handler>[A-Za-z_]\w*)\s*\("
)

# Gin / Echo / Chi (Go): router.GET("/foo", Handler) — note the uppercase method.
_ROUTES_GO_RE = re.compile(
    r"\b(?:router|r|app|api|e|mux|group|v\d+)\.(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Any|HandleFunc|Handle)\("
    r"\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*(?P<handler>[A-Za-z_][\w.]*)"
)

# Actix-web (Rust): .route("/foo", web::get().to(handler)) and #[get("/foo")]
_ROUTES_RUST_ROUTE_RE = re.compile(
    r"\.route\(\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*web::(?P<method>get|post|put|patch|delete|head|options)\(\)\.to\(\s*(?P<handler>[A-Za-z_][\w:]*)"
)
_ROUTES_RUST_ATTR_RE = re.compile(
    r"#\[(?P<method>get|post|put|patch|delete|head|options)\(\s*['\"](?P<path>[^'\"]+)['\"]\s*\)\]"
    r"\s*(?:async\s+)?fn\s+(?P<handler>[A-Za-z_]\w*)"
)

# Sinatra / Rails routes: get '/foo' do ... | get "/foo", to: "controller#action"
_ROUTES_RUBY_RE = re.compile(
    r"^\s*(?P<method>get|post|put|patch|delete|head|options|match)\s+['\"](?P<path>[^'\"]+)['\"]"
    r"(?:[^\n]*?(?:to:\s*['\"](?P<handler>[A-Za-z_][\w#/]*)['\"]|do\s*$))",
    re.M,
)

# Laravel (PHP): Route::get('/foo', [Controller::class, 'method']) or Route::get('/foo', 'Controller@method')
_ROUTES_LARAVEL_RE = re.compile(
    r"\bRoute::(?P<method>get|post|put|patch|delete|head|options|any|match)\(\s*"
    r"['\"](?P<path>[^'\"]+)['\"]\s*,\s*"
    r"(?:\[([A-Za-z_\\][\w\\]*)::class\s*,\s*['\"](?P<handler1>[A-Za-z_]\w*)['\"]\]"
    r"|['\"](?P<handler2>[A-Za-z_][\w\\]*(?:@[A-Za-z_]\w*)?)['\"])"
)

# ASP.NET Core: [HttpGet("foo")] / [Route("foo")] above a method
_ROUTES_ASPNET_RE = re.compile(
    r"\[Http(?P<method>Get|Post|Put|Patch|Delete|Head|Options)\(\s*['\"](?P<path>[^'\"]+)['\"]\s*\)\]"
    r"[\s\S]{0,200}?\b(?:public|private|protected|internal)\s+[\w<>\[\],?\s]*?\s+(?P<handler>[A-Za-z_]\w*)\s*\("
)
_ROUTES_ASPNET_ROUTE_RE = re.compile(
    r"\[Route\(\s*['\"](?P<path>[^'\"]+)['\"]\s*\)\]"
    r"[\s\S]{0,200}?\b(?:public|private|protected|internal)\s+[\w<>\[\],?\s]*?\s+(?P<handler>[A-Za-z_]\w*)\s*\("
)


def _add_route(routes: dict, method: str, path: str, handler: str) -> None:
    if not path or not handler:
        return
    method = method.upper().strip()
    # Normalize Flask/Starlette decorator that doesn't carry an HTTP verb.
    if method in {"ROUTE", "WEBSOCKET", "API_ROUTE", "ALL", "MATCH", "ANY", "USE", "HANDLEFUNC", "HANDLE"}:
        method = "ANY"
    routes[f"{method} {path}"] = handler


def extract_routes(text: str) -> dict[str, str]:
    """Best-effort URL-pattern → handler-symbol mapping across web frameworks.

    Recognized: FastAPI, Flask, Starlette, Sanic, Django, Express/Koa/Hono,
    NestJS, Spring, Gin/Echo/Chi (Go), Actix-web (Rust), Sinatra/Rails (Ruby),
    Laravel (PHP), ASP.NET Core (C#). Pattern-based — false negatives expected
    on metaprogrammed routes; collisions resolved by last-write-wins so static
    declarations near the top of files take precedence over generic builders.
    """
    routes: dict[str, str] = {}
    for m in _ROUTES_DICT_RE.finditer(text):
        # "GET /foo": handler  — split METHOD and path
        method, _, path = m.group(1).partition(" ")
        _add_route(routes, method, path, m.group(2))
    for m in _ROUTES_EXPRESS_RE.finditer(text):
        _add_route(routes, m.group(1), m.group(2), m.group(3))
    for m in _ROUTES_PY_DECORATOR_RE.finditer(text):
        method = m.group("method")
        rest = m.group("rest") or ""
        # Flask: @app.route("/foo", methods=["POST"])
        if method.lower() == "route":
            verb_match = re.search(r"methods\s*=\s*\[\s*['\"]([A-Z]+)['\"]", rest)
            verb = verb_match.group(1) if verb_match else "ANY"
            _add_route(routes, verb, m.group("path"), m.group("handler"))
        else:
            _add_route(routes, method, m.group("path"), m.group("handler"))
    for m in _ROUTES_DJANGO_RE.finditer(text):
        _add_route(routes, "ANY", m.group("path"), m.group("handler"))
    for m in _ROUTES_NEST_RE.finditer(text):
        _add_route(routes, m.group("method"), m.group("path") or "/", m.group("handler"))
    for m in _ROUTES_SPRING_RE.finditer(text):
        ann = m.group("ann")
        method = "ANY" if ann == "Request" else ann
        if ann == "Request":
            method_match = re.search(r"\bmethod\s*=\s*RequestMethod\.([A-Z]+)\b", m.group(0))
            if method_match:
                method = method_match.group(1)
        _add_route(routes, method, m.group("path"), m.group("handler"))
    for m in _ROUTES_GO_RE.finditer(text):
        _add_route(routes, m.group("method"), m.group("path"), m.group("handler"))
    for m in _ROUTES_RUST_ROUTE_RE.finditer(text):
        _add_route(routes, m.group("method"), m.group("path"), m.group("handler"))
    for m in _ROUTES_RUST_ATTR_RE.finditer(text):
        _add_route(routes, m.group("method"), m.group("path"), m.group("handler"))
    for m in _ROUTES_RUBY_RE.finditer(text):
        handler = m.group("handler") or "block"
        _add_route(routes, m.group("method"), m.group("path"), handler)
    for m in _ROUTES_LARAVEL_RE.finditer(text):
        handler = m.group("handler1") or m.group("handler2") or ""
        _add_route(routes, m.group("method"), m.group("path"), handler)
    for m in _ROUTES_ASPNET_RE.finditer(text):
        _add_route(routes, m.group("method"), m.group("path"), m.group("handler"))
    for m in _ROUTES_ASPNET_ROUTE_RE.finditer(text):
        _add_route(routes, "ANY", m.group("path"), m.group("handler"))
    return routes


_SIDE_EFFECT_CHECKS = [
    # DB writes — require library/ORM context, multi-segment table-style chains,
    # or SQL keywords. Crucially, do NOT match plain `list.insert(...)` (Python builtin)
    # or local helpers like `_fetch(` — those produced false positives against
    # parser/tokenizer modules.
    (re.compile(
        r"\b(?:cursor|conn|connection|session|db|engine)\.execute\s*\(\s*['\"]\s*(?:INSERT|UPDATE|MERGE)\b"
        r"|\b(?:Session|session|db\.session)\.add(?:_all)?\s*\("
        r"|\.objects\.(?:create|bulk_create|update|update_or_create|get_or_create)\s*\("
        r"|\b\w+\.\w+\.(?:create|bulk_create|insert_one|insert_many|update_one|update_many|save)\s*\("
        r"|\b(?:INSERT\s+INTO|UPDATE\s+\w+\s+SET|MERGE\s+INTO)\b",
    ), "DB write"),
    (re.compile(
        r"\b(?:cursor|conn|connection|session|db|engine)\.execute\s*\(\s*['\"]\s*DELETE\b"
        r"|\.objects\.(?:delete|filter\([^)]*\)\.delete)\s*\("
        r"|\b\w+\.\w+\.(?:delete_one|delete_many|remove)\s*\("
        r"|\bDELETE\s+FROM\b",
    ), "DB delete"),
    (re.compile(
        r"\bsmtplib\.|\bsend_mail\s*\(|\bsendmail\s*\(|\bsendEmail\s*\("
        r"|\bEmailMessage\s*\(|\bemail\.mime|\bmailer\.send\s*\(",
    ), "Email send"),
    (re.compile(
        r"\brequests\.(?:get|post|put|patch|delete|head|options|request|Session)\s*\("
        r"|\b(?:httpx|aiohttp|urllib3)\.(?:get|post|put|patch|delete|request|Client|AsyncClient)\b"
        r"|\burllib\.request\.urlopen\s*\("
        r"|\baxios\.(?:get|post|put|patch|delete|request|create)\s*\("
        r"|\bfetch\s*\(\s*['\"`]https?://"
        r"|\bsocket\.(?:connect|create_connection)\s*\("
        r"|\bhttp\.client\.HTTP",
    ), "Network call"),
    (re.compile(
        r"\bsubprocess\.(?:run|call|check_call|check_output|Popen)\s*\("
        r"|\bos\.(?:system|popen|spawn[lvep]+|exec[lvep]+)\s*\("
        r"|\bcommands\.getoutput\s*\("
        r"|\bshell_exec\s*\(",
    ), "Shell execution"),
    (re.compile(
        r"\bopen\s*\([^)]*['\"][wax]\b"
        r"|\bPath\([^)]*\)\.write_(?:text|bytes)\s*\("
        r"|\bshutil\.(?:copy|copy2|copyfile|copytree|rmtree|move)\s*\("
        r"|\bos\.(?:remove|unlink|rename|mkdir|makedirs|rmdir)\s*\(",
    ), "Filesystem write"),
]


def extract_side_effects(text: str, lang: str = "Python", extra_rules: list[tuple] | None = None) -> list[str]:
    scan = strip_comments_and_docstrings(text, lang)
    effects = []
    for pattern, label in _SIDE_EFFECT_CHECKS:
        if pattern.search(scan):
            effects.append(label)
    if extra_rules:
        for pattern, label in extra_rules:
            if pattern.search(scan):
                effects.append(label)
    return sorted(set(effects))


def strip_comments_and_docstrings(text: str, lang: str) -> str:
    """Remove comments and docstrings so heuristic scans don't fire on prose.
    Falls back to original text on parse errors."""
    if lang == "Python":
        return _strip_python(text)
    return _strip_c_like(text)


def _strip_python(text: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    docstring_ranges: list[tuple[int, int]] = []
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                    n = body[0]
                    if hasattr(n, "lineno") and hasattr(n, "end_lineno"):
                        docstring_ranges.append((n.lineno, n.end_lineno or n.lineno))
    lines = text.splitlines()
    keep = [True] * len(lines)
    for start, end in docstring_ranges:
        for i in range(start - 1, min(end, len(lines))):
            keep[i] = False
    stripped = "\n".join(line for line, k in zip(lines, keep) if k)
    try:
        out_tokens = []
        for tok in tokenize.generate_tokens(io.StringIO(stripped).readline):
            if tok.type == tokenize.COMMENT:
                continue
            out_tokens.append(tok)
        return tokenize.untokenize(out_tokens)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # tokenize raises TokenError (not TokenizeError) on EOF mid-string;
        # invalid indentation or syntax surfaces as IndentationError/SyntaxError.
        # All three are recoverable for our heuristic strip — fall back to the
        # pre-untokenize text rather than crashing the entire review.
        return stripped


_C_LIKE_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_C_LIKE_LINE = re.compile(r"//[^\n]*")
_HASH_LINE = re.compile(r"#[^\n]*")


def _strip_c_like(text: str) -> str:
    cleaned = _C_LIKE_BLOCK.sub("", text)
    cleaned = _C_LIKE_LINE.sub("", cleaned)
    cleaned = _HASH_LINE.sub("", cleaned)
    return cleaned


def extract_security_findings(text: str, lang: str = "Python") -> list[str]:
    scan = strip_comments_and_docstrings(text, lang)
    findings = []
    checks = [
        (r"(?i)(api[_-]?key|secret|token|password)\s*=\s*['\"][^'\"]{12,}['\"]", "possible hardcoded secret"),
        (r"(?i)(select|insert|update|delete)\s+.*(%\s*|\.format\(|\+\s*\w+|f['\"])", "possible SQL injection"),
        (r"\b(eval|exec)\s*\(", "unsafe eval/exec"),
        (r"subprocess\.[\w_]+\([^\n)]*shell\s*=\s*True", "shell=True command execution"),
        (r"(?i)pickle\.loads?\s*\(", "unsafe pickle deserialization"),
    ]
    for pattern, label in checks:
        if re.search(pattern, scan):
            findings.append(label)
    for call in re.finditer(r"(?is)\byaml\.load\s*\((?P<args>[^)]*)\)", scan):
        args = call.group("args")
        safe_loader = re.search(r"\bLoader\s*=\s*(?:yaml\.)?(?:CSafeLoader|SafeLoader)\b", args)
        if not safe_loader:
            findings.append("unsafe yaml.load")
    if re.search(r"(?i)random\.(random|randint|choice|choices)\s*\(", scan) and re.search(r"(?i)token|secret|password|salt|nonce|key|crypto", scan):
        findings.append("non-cryptographic randomness")
    return sorted(set(findings))


def path_to_module_names(rel: str) -> set[str]:
    p = Path(rel)
    stem = p.with_suffix("").as_posix()
    parts = stem.split("/")
    names: set[str] = set()
    if len(parts) > 1:
        names.add(stem)
        names.add(stem.replace("/", "."))
        names.add(".".join(parts[-2:]))
        names.add("/".join(parts[-2:]))
    elif len(parts[0]) >= 4:
        names.add(parts[0])
    return names
