import pytest


@pytest.fixture
def watch_repo(tmp_path):
    root = tmp_path
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "other").mkdir(parents=True)

    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "big.py").write_text("def big_fn():\n    return 1\n")
    (root / "src" / "pkg" / "consumer.py").write_text(
        "from .big import big_fn\n\n"
        "def use_big():\n"
        "    return big_fn()\n"
    )
    (root / "src" / "pkg" / "moved.py").write_text("def moved_fn():\n    return 2\n")
    return root


def test_reindex_paths_evicts_oversized_file_and_rebuilds_inverse_deps(monkeypatch, watch_repo):
    from codeward.index import RepoIndex
    from codeward import watch
    from codeward.watch import _reindex_paths

    monkeypatch.setattr(watch, "MAX_INDEXABLE_BYTES", 10)

    idx = RepoIndex(watch_repo)
    target = watch_repo / "src" / "pkg" / "big.py"
    target.write_text("def big_fn():\n    return 1\n" + ("x" * 32))

    n = _reindex_paths(idx, watch_repo, {str(target)})

    assert n == 1
    assert "src/pkg/big.py" not in idx.files
    assert "src/pkg/big.py" not in idx._text_cache
    assert idx.files["src/pkg/consumer.py"].resolved_deps == []
    assert idx.dependents_of_file("src/pkg/big.py") == []


def test_paths_for_moved_event_includes_relevant_source_and_destination(watch_repo):
    from codeward.watch import _event_paths

    class Event:
        is_directory = False

        def __init__(self, src_path, dest_path=None):
            self.src_path = src_path
            self.dest_path = dest_path

    moved_out = Event(
        src_path=str(watch_repo / "src" / "pkg" / "moved.py"),
        dest_path=str(watch_repo / "src" / "other" / "moved.txt"),
    )
    moved_in = Event(
        src_path=str(watch_repo / "src" / "other" / "old.txt"),
        dest_path=str(watch_repo / "src" / "pkg" / "moved.py"),
    )

    assert _event_paths(moved_out, watch_repo) == {str(watch_repo / "src" / "pkg" / "moved.py")}
    assert _event_paths(moved_in, watch_repo) == {str(watch_repo / "src" / "pkg" / "moved.py")}


def test_debouncer_reports_many_events_as_one_unique_path(watch_repo):
    from threading import Event

    from codeward.watch import _Debouncer

    flushed = Event()
    calls = []
    target = str(watch_repo / "src" / "pkg" / "big.py")

    def flush(paths, event_count):
        calls.append((paths, event_count))
        flushed.set()

    debouncer = _Debouncer(0.01, flush)
    for _ in range(5):
        debouncer.schedule(target)

    assert flushed.wait(1)
    assert calls == [({target}, 5)]


def test_reindex_batch_skips_unchanged_mtime_and_size(monkeypatch, watch_repo):
    from codeward.index import RepoIndex
    from codeward import watch
    from codeward.watch import _PathSnapshotCache, _reindex_batch

    idx = RepoIndex(watch_repo)
    snapshots = _PathSnapshotCache.from_index(idx)
    target = watch_repo / "src" / "pkg" / "big.py"

    def fail_analyze(*args, **kwargs):
        raise AssertionError("unchanged file should not be reanalyzed")

    monkeypatch.setattr(watch, "analyze_file", fail_analyze)

    result = _reindex_batch(idx, watch_repo, {str(target)}, snapshots=snapshots)

    assert result.updated == 0
    assert result.skipped_unchanged == 1
    assert result.full_parse_count == 0
    assert result.incremental_parse_count == 0


def test_incremental_parse_error_falls_back_to_full_parse(monkeypatch, tmp_path):
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_javascript")

    from codeward.index import RepoIndex
    from codeward import watch
    from codeward.watch import _IncrementalParseCache, _PathSnapshotCache, _reindex_batch
    import codeward.analyzers.treesitter as treesitter

    root = tmp_path
    target = root / "sample.js"
    target.write_text("function alpha() { return 1; }\n")
    idx = RepoIndex(root)
    snapshots = _PathSnapshotCache.from_index(idx)
    parse_cache = _IncrementalParseCache(max_entries=4)

    target.write_text("function alpha() { return 2; }\n")
    first = _reindex_batch(idx, root, {str(target)}, snapshots=snapshots, parse_cache=parse_cache)
    assert first.full_parse_count == 1
    assert parse_cache.get("sample.js") is not None

    def explode(*args, **kwargs):
        raise RuntimeError("incremental parser failed")

    monkeypatch.setattr(treesitter, "parse_for_path_incremental", explode)
    target.write_text("function beta() { return 3; }\n")

    second = _reindex_batch(idx, root, {str(target)}, snapshots=snapshots, parse_cache=parse_cache)

    assert second.updated == 1
    assert second.incremental_parse_count == 0
    assert second.full_parse_count == 1
    assert [symbol.name for symbol in idx.files["sample.js"].symbols] == ["beta"]
