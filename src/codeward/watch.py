"""Background re-indexer.

`codeward watch` keeps an in-memory RepoIndex hot. On file-system events it
reanalyzes only the changed file and writes the SQLite cache back to disk.
Other CLI invocations can refresh stale cache entries themselves, but a hot
watcher keeps that work off the command path on large repos.

Design intentionally small:
  - Foreground process (no fork / pidfile / daemonization). Caller wraps in
    nohup/systemd/launchd if they want it backgrounded.
  - No socket RPC. CLI commands still construct their own RepoIndex; the
    benefit is that the SQLite cache is already fresh when they start.
  - Falls back to mtime polling every 2s if `watchdog` is not installed.
  - Debounces: a burst of saves triggers exactly one reindex, not N.
"""
from __future__ import annotations

import os
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Timer

from .index import (
    RepoIndex,
    IGNORE_DIRS,
    CODE_EXTS,
    MAX_INDEXABLE_BYTES,
    FileInfo,
    analyze_file,
    extract_file_routes,
    extract_routes,
    extract_side_effects,
    lang_for,
    strip_comments_and_docstrings,
    _extract_imports_only,
)


DEFAULT_DEBOUNCE_SECONDS = 0.2
DEFAULT_PARSE_CACHE_SIZE = 128


@dataclass
class _BatchResult:
    updated: int = 0
    skipped_unchanged: int = 0
    deleted: int = 0
    oversized: int = 0
    full_parse_count: int = 0
    incremental_parse_count: int = 0
    events_seen: int = 0
    unique_paths: int = 0

    @property
    def events_coalesced(self) -> int:
        return max(0, self.events_seen - self.unique_paths)


@dataclass
class _Snapshot:
    mtime_ns: int
    size: int


class _PathSnapshotCache:
    def __init__(self) -> None:
        self._snapshots: dict[str, _Snapshot] = {}

    @classmethod
    def from_index(cls, idx: RepoIndex) -> "_PathSnapshotCache":
        cache = cls()
        for rel, info in idx.files.items():
            cache._snapshots[rel] = _Snapshot(info.mtime_ns, info.size)
        return cache

    def unchanged(self, rel: str, st: os.stat_result) -> bool:
        snapshot = self._snapshots.get(rel)
        return snapshot is not None and snapshot.mtime_ns == st.st_mtime_ns and snapshot.size == st.st_size

    def update(self, rel: str, st: os.stat_result) -> None:
        self._snapshots[rel] = _Snapshot(st.st_mtime_ns, st.st_size)

    def remove(self, rel: str) -> None:
        self._snapshots.pop(rel, None)


class _IncrementalParseCache:
    def __init__(self, max_entries: int = DEFAULT_PARSE_CACHE_SIZE) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[object, bytes]] = OrderedDict()

    def get(self, rel: str) -> tuple[object, bytes] | None:
        entry = self._entries.get(rel)
        if entry is None:
            return None
        self._entries.move_to_end(rel)
        return entry

    def put(self, rel: str, tree, src: bytes) -> None:
        self._entries[rel] = (tree, src)
        self._entries.move_to_end(rel)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def remove(self, rel: str) -> None:
        self._entries.pop(rel, None)


class _Debouncer:
    """Coalesce a stream of pending paths and call `flush(paths)` once they
    settle for `delay` seconds."""

    def __init__(self, delay: float, flush_fn) -> None:
        self.delay = delay
        self.flush_fn = flush_fn
        self.lock = Lock()
        self.pending: set[str] = set()
        self.pending_events = 0
        self.timer: Timer | None = None

    def schedule(self, path: str) -> None:
        with self.lock:
            self.pending.add(path)
            self.pending_events += 1
            if self.timer is not None:
                self.timer.cancel()
            self.timer = Timer(self.delay, self._fire)
            self.timer.daemon = True
            self.timer.start()

    def _fire(self) -> None:
        with self.lock:
            paths, self.pending = self.pending, set()
            event_count, self.pending_events = self.pending_events, 0
            self.timer = None
        if paths:
            try:
                self.flush_fn(paths, event_count)
            except Exception as e:  # never let the daemon die on user code errors
                print(f"[codeward watch] flush error: {e}", file=sys.stderr)


def _is_relevant_file(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(root.resolve()).parts
    except (ValueError, OSError):
        return False
    if not rel_parts or any(seg in IGNORE_DIRS or seg.startswith(".") for seg in rel_parts[:-1]):
        return False
    if path.suffix not in CODE_EXTS:
        return False
    return True


def _event_paths(e, root: Path) -> set[str]:
    if getattr(e, "is_directory", False):
        return set()
    paths: set[str] = set()
    src_path = getattr(e, "src_path", None)
    if src_path and _is_relevant_file(Path(src_path), root):
        paths.add(str(src_path))
    dest_path = getattr(e, "dest_path", None)
    if dest_path and _is_relevant_file(Path(dest_path), root):
        paths.add(str(dest_path))
    return paths


def _reindex_paths(idx: RepoIndex, root: Path, paths: set[str]) -> int:
    """Reanalyze the set of files. Returns count of files actually updated."""
    return _reindex_batch(idx, root, paths).updated


def _reindex_batch(
    idx: RepoIndex,
    root: Path,
    paths: set[str],
    *,
    snapshots: _PathSnapshotCache | None = None,
    parse_cache: _IncrementalParseCache | None = None,
    events_seen: int = 0,
) -> _BatchResult:
    result = _BatchResult(events_seen=events_seen or len(paths), unique_paths=len(paths))
    for abs_path in paths:
        p = Path(abs_path)
        try:
            rel = p.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            continue
        if not p.exists():
            if rel in idx.files:
                del idx.files[rel]
                idx._text_cache.pop(rel, None)
                idx._treesitter_cache.pop(rel, None)
                if parse_cache is not None:
                    parse_cache.remove(rel)
                if snapshots is not None:
                    snapshots.remove(rel)
                result.updated += 1
                result.deleted += 1
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if snapshots is not None and snapshots.unchanged(rel, st):
            result.skipped_unchanged += 1
            continue
        if st.st_size > MAX_INDEXABLE_BYTES:
            if rel in idx.files:
                del idx.files[rel]
                idx._text_cache.pop(rel, None)
                idx._treesitter_cache.pop(rel, None)
                if parse_cache is not None:
                    parse_cache.remove(rel)
                result.updated += 1
                result.oversized += 1
            if snapshots is not None:
                snapshots.update(rel, st)
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            info, parse_mode = _analyze_watch_file(idx, rel, text, parse_cache=parse_cache)
        except Exception as e:
            print(f"[codeward watch] analyze failed for {rel}: {e}", file=sys.stderr)
            continue
        info.mtime_ns = st.st_mtime_ns
        info.size = st.st_size
        idx.files[rel] = info
        idx._text_cache[rel] = text
        if snapshots is not None:
            snapshots.update(rel, st)
        if parse_mode == "incremental":
            result.incremental_parse_count += 1
        else:
            result.full_parse_count += 1
        result.updated += 1
    if result.updated:
        # Re-resolve imports + inverse-deps. On big repos this is ~10ms; cheap
        # enough that we don't need an incremental scheme.
        idx._resolve_all_imports()
        idx._rebuild_inverse_deps()
        try:
            idx.write_sqlite()
        except Exception as e:
            print(f"[codeward watch] sqlite write failed: {e}", file=sys.stderr)
    return result


def _analyze_watch_file(
    idx: RepoIndex,
    rel: str,
    text: str,
    *,
    parse_cache: _IncrementalParseCache | None,
) -> tuple[FileInfo, str]:
    if parse_cache is not None and Path(rel).suffix != ".py":
        cached = _analyze_with_tree_sitter_cache(idx, rel, text, parse_cache)
        if cached is not None:
            return cached
    return analyze_file(rel, text, custom_side_effect_rules=idx.config.get("custom_side_effect_rules")), "full"


def _analyze_with_tree_sitter_cache(
    idx: RepoIndex,
    rel: str,
    text: str,
    parse_cache: _IncrementalParseCache,
) -> tuple[FileInfo, str] | None:
    try:
        from .analyzers import treesitter

        cached = parse_cache.get(rel)
        if cached is not None:
            old_tree, old_src = cached
            parsed = treesitter.parse_for_path_incremental(rel, text, old_tree, old_src)
            if parsed is not None:
                info = _info_from_treesitter_tree(idx, rel, text, parsed)
                if info is not None:
                    parse_cache.put(rel, parsed[0], parsed[1])
                    return info, "incremental"

        parsed = treesitter.parse_tree_for_path(rel, text)
        if parsed is None:
            return None
        info = _info_from_treesitter_tree(idx, rel, text, parsed)
        if info is None:
            return None
        parse_cache.put(rel, parsed[0], parsed[1])
        return info, "full"
    except Exception:
        parse_cache.remove(rel)
        return None


def _info_from_treesitter_tree(
    idx: RepoIndex,
    rel: str,
    text: str,
    parsed: tuple[object, bytes],
) -> FileInfo | None:
    from .analyzers.treesitter import analyze_treesitter_from_tree

    tree, src = parsed
    info = FileInfo(path=rel, lang=lang_for(rel), lines=len(text.splitlines()))
    if not analyze_treesitter_from_tree(info, text, tree, src):
        return None

    info.analyzer = "tree_sitter"
    info.precision = "syntax_aware"
    info.confidence = "medium"
    for symbol in info.symbols:
        symbol.analyzer = "tree_sitter"
        symbol.precision = "exact_range" if symbol.end_line else "syntax_aware"
        symbol.confidence = "medium"
    _extract_imports_only(info, text)
    route_scan = strip_comments_and_docstrings(text, info.lang)
    info.routes = extract_routes(route_scan)
    info.routes.update(extract_file_routes(rel, route_scan))
    info.side_effects = extract_side_effects(
        text,
        info.lang,
        extra_rules=idx.config.get("custom_side_effect_rules"),
    )
    return info


def run_watch(
    root: Path,
    debounce: float | None = DEFAULT_DEBOUNCE_SECONDS,
    *,
    stats: bool = False,
    parse_cache_size: int = DEFAULT_PARSE_CACHE_SIZE,
) -> int:
    if debounce is None:
        debounce = DEFAULT_DEBOUNCE_SECONDS
    root = root.resolve()
    print(f"[codeward watch] building initial index at {root} ...")
    t0 = time.time()
    idx = RepoIndex(root)
    print(f"[codeward watch] indexed {len(idx.files)} files in {time.time()-t0:.2f}s; watching for changes")

    snapshots = _PathSnapshotCache.from_index(idx)
    parse_cache = _IncrementalParseCache(max_entries=parse_cache_size)
    debouncer = _Debouncer(
        debounce,
        lambda paths, event_count: _flush(
            idx,
            root,
            paths,
            snapshots=snapshots,
            parse_cache=parse_cache,
            event_count=event_count,
            stats=stats,
        ),
    )

    try:
        from watchdog.observers import Observer  # type: ignore
        from watchdog.events import FileSystemEventHandler  # type: ignore
    except ImportError:
        print("[codeward watch] watchdog not installed; falling back to 2s mtime polling", file=sys.stderr)
        return _poll_loop(idx, root, debouncer)

    class Handler(FileSystemEventHandler):
        def on_modified(self, e):
            self._maybe_schedule(e)
        def on_created(self, e):
            self._maybe_schedule(e)
        def on_deleted(self, e):
            self._maybe_schedule(e)
        def on_moved(self, e):
            self._maybe_schedule(e)
        def _maybe_schedule(self, e):
            for path in _event_paths(e, root):
                debouncer.schedule(path)

    observer = Observer()
    observer.schedule(Handler(), str(root), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[codeward watch] shutting down")
    finally:
        observer.stop()
        observer.join(timeout=2)
    return 0


def _flush(
    idx: RepoIndex,
    root: Path,
    paths: set[str],
    *,
    snapshots: _PathSnapshotCache | None = None,
    parse_cache: _IncrementalParseCache | None = None,
    event_count: int = 0,
    stats: bool = False,
) -> None:
    result = _reindex_batch(
        idx,
        root,
        paths,
        snapshots=snapshots,
        parse_cache=parse_cache,
        events_seen=event_count,
    )
    if result.updated:
        files_summary = ", ".join(sorted({Path(p).name for p in paths})[:5])
        more = f" (+{len(paths) - 5} more)" if len(paths) > 5 else ""
        print(f"[codeward watch] reindexed {result.updated} file(s): {files_summary}{more}")
    if stats or result.updated or result.skipped_unchanged:
        print(
            "[codeward watch] batch stats: "
            f"events={result.events_seen} unique_paths={result.unique_paths} "
            f"coalesced={result.events_coalesced} reanalyzed={result.updated} "
            f"skipped_unchanged={result.skipped_unchanged} "
            f"incremental_parses={result.incremental_parse_count} "
            f"full_parses={result.full_parse_count}"
        )


def _poll_loop(idx: RepoIndex, root: Path, debouncer: _Debouncer) -> int:
    """Mtime-poll fallback when watchdog isn't installed. 2-second resolution."""
    seen: dict[str, float] = {}
    for rel in idx.files:
        try:
            seen[rel] = (root / rel).stat().st_mtime
        except OSError:
            pass
    try:
        while True:
            time.sleep(2)
            current: dict[str, float] = {}
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
                for name in filenames:
                    p = Path(dirpath) / name
                    if not _is_relevant_file(p, root):
                        continue
                    rel = p.relative_to(root).as_posix()
                    try:
                        current[rel] = p.stat().st_mtime
                    except OSError:
                        pass
            changed = [rel for rel, mt in current.items() if seen.get(rel) != mt]
            removed = [rel for rel in seen if rel not in current]
            for rel in changed + removed:
                debouncer.schedule(str(root / rel))
            seen = current
    except KeyboardInterrupt:
        print("\n[codeward watch] shutting down")
    return 0
