"""Lab 02: merge-алгоритми, старий булевий search() і збереження/завантаження."""

import pickle
from pathlib import Path

import pytest

from findex.index import Index, build_index
from findex.search import and_merge, and_set, not_merge, not_set, or_merge, or_set, search
from findex.store import load, save

A = [1, 3, 5, 7, 9]
B = [3, 4, 5, 10]


@pytest.mark.parametrize(
    ("merge", "via_set", "expected"),
    [
        (and_merge, and_set, [3, 5]),
        (or_merge, or_set, [1, 3, 4, 5, 7, 9, 10]),
        (not_merge, not_set, [1, 7, 9]),
    ],
)
def test_merge_matches_set_version(merge, via_set, expected) -> None:
    assert merge(A, B) == via_set(A, B) == expected


@pytest.mark.parametrize("merge", [and_merge, or_merge, not_merge])
def test_merge_with_empty(merge) -> None:
    assert merge([], []) == []
    assert merge([], B) == ([] if merge is not or_merge else B)


@pytest.mark.parametrize(
    ("query", "titles"),
    [
        ("python AND java", {"py-java"}),
        ("java OR rareword", {"py-java", "java-loop", "rare"}),
        ("python NOT java", {"py-async", "long", "rare"}),
        ("NOT python", {"java-loop", "uk"}),
    ],
)
@pytest.mark.parametrize("engine", ["merge", "set"])
def test_lab02_boolean_search(tiny_index: Index, query: str, titles: set[str], engine) -> None:
    ids = search(query, tiny_index.postings, tiny_index.doc_lengths, engine=engine)
    assert ids == sorted(ids)
    assert {tiny_index.doc_meta[i].title for i in ids} == titles


@pytest.mark.parametrize("fmt", ["pickle", "json"])
@pytest.mark.parametrize("positions", [False, True])
def test_save_load_round_trip(tiny_corpus: Path, tmp_path: Path, fmt, positions: bool) -> None:
    index = build_index(tiny_corpus, positions=positions)
    path = tmp_path / f"i.{fmt}"
    save(index, path, fmt)
    loaded = load(path, fmt)
    assert loaded == index  # Index.__eq__ порівнює postings, doc_lengths, doc_meta, has_positions
    assert loaded.has_positions is positions


def test_load_rejects_foreign_pickle(tmp_path: Path) -> None:
    path = tmp_path / "not_index.bin"
    path.write_bytes(pickle.dumps({"a": 1}))
    with pytest.raises(TypeError, match="не Index"):
        load(path)


def test_unknown_format(tiny_index: Index, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown format"):
        save(tiny_index, tmp_path / "x", "xml")  # type: ignore[arg-type]  # навмисно хибний Literal


def test_build_with_limit(tiny_corpus: Path) -> None:
    assert build_index(tiny_corpus, limit=2).num_docs == 2


def test_index_invariants(tiny_index: Index) -> None:
    for plist in tiny_index.values():
        ids = [p.doc_id for p in plist]
        assert ids == sorted(set(ids))  # відсортовано і без дублів
        assert all(p.tf == len(p.positions) for p in plist)
    total_tf = sum(p.tf for plist in tiny_index.values() for p in plist)
    assert total_tf == sum(tiny_index.doc_lengths.values())
