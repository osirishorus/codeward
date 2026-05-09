"""Background re-indexer.

`codeward watch` keeps an in-memory RepoIndex hot. On file-system events it
reanalyzes only the changed file and writes the SQLite cache back to disk.
Every other CLI invocation in the same repo then loads from that fresh cache,
which is much faster than rebuilding from scratch on a large repo.

Design intentionally small:
  - Foreground process (no fork / pidfile / daemonization). Caller wraps in
    nohup/systemd/launchd if they want it backgrounded.
  - No socket RPC. CLI commands still construct their own RepoIndex; the
    benefit is that the SQLite cache stays fresh under their feet.
  - Falls back to mtime polling every 2s if `watchdog` is not installed.
  - Debounces: a burst of saves triggers exactly one reindex, not N.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from threading import Lock, Timer

from .index import RepoIndex, IGNORE_DIRS, CODE_EXTS, MAX_INDEXABLE_BYTES, analyze_file


class _Debouncer:
    """Coalesce a stream of pending paths and call `flush(paths)` once they
    settle for `delay` seconds."""

    def __init__(self, delay: float, flush_fn) -> None:
        self.delay = delay
        self.flush_fn = flush_fn
        self.lock = Lock()
        self.pending: set[str] = set()
        self.timer: Timer | None = None

    def schedule(self, path: str) -> None:
        with self.lock:
            self.pending.add(path)
            if self.timer is not None:
                self.timer.cancel()
            self.timer = Timer(self.delay, self._fire)
            self.timer.daemon = True
            self.timer.start()

    def _fire(self) -> None:
        with self.lock:
            paths, self.pending = self.pending, set()
            self.timer = None
        if paths:
            try:
                self.flush_fn(paths)
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


def _reindex_paths(idx: RepoIndex, root: Path, paths: set[str]) -> int:
    """Reanalyze the set of files. Returns count of files actually updated."""
    updated = 0
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
                updated += 1
            continue
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
        try:
            info = analyze_file(rel, text, custom_side_effect_rules=idx.config.get("custom_side_effect_rules"))
        except Exception as e:
            print(f"[codeward watch] analyze failed for {rel}: {e}", file=sys.stderr)
            continue
        idx.files[rel] = info
        idx._text_cache[rel] = text
        updated += 1
    if updated:
        # Re-resolve imports + inverse-deps. On big repos this is ~10ms; cheap
        # enough that we don't need an incremental scheme.
        idx._resolve_all_imports()
        idx._rebuild_inverse_deps()
        try:
            idx.write_sqlite()
        except Exception as e:
            print(f"[codeward watch] sqlite write failed: {e}", file=sys.stderr)
    return updated


def run_watch(root: Path, debounce: float = 0.5) -> int:
    root = root.resolve()
    print(f"[codeward watch] building initial index at {root} ...")
    t0 = time.time()
    idx = RepoIndex(root)
    print(f"[codeward watch] indexed {len(idx.files)} files in {time.time()-t0:.2f}s; watching for changes")

    debouncer = _Debouncer(debounce, lambda paths: _flush(idx, root, paths))

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
            if getattr(e, "is_directory", False):
                return
            p = Path(getattr(e, "dest_path", None) or e.src_path)
            if _is_relevant_file(p, root):
                debouncer.schedule(str(p))

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


def _flush(idx: RepoIndex, root: Path, paths: set[str]) -> None:
    n = _reindex_paths(idx, root, paths)
    if n:
        files_summary = ", ".join(sorted({Path(p).name for p in paths})[:5])
        more = f" (+{len(paths) - 5} more)" if len(paths) > 5 else ""
        print(f"[codeward watch] reindexed {n} file(s): {files_summary}{more}")


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
