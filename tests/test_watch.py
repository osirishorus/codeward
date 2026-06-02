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
