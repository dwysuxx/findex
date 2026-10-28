"""Lab 08, M2: NumPy-ранжування дає ТЕ САМЕ, що Lab 03 — ті самі doc_id, той самий порядок, ті самі
біти score. Порівнюємо ranking.rank (Python) і vector.rank на крихітному й на справжньому корпусі."""

import json
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import REAL_CORPUS
from findex.engine import Searcher
from findex.index import Index, build_index
from findex.query import parse
from findex.ranking import BM25, Scorer, TfIdf, rank
from findex.vector import VectorIndex, mask, top_k
from findex.vector import rank as rank_vector

SCORERS = [BM25(), TfIdf(), BM25(k1=0.9, b=0.4)]


def both(index: Index, vx: VectorIndex, query: str, scorer: Scorer, k: int):
    node = parse(query)
    terms = node.terms()
    py = rank(index, node.evaluate(index), terms, scorer, k)
    np_ranked, total = rank_vector(vx, mask(node, vx), terms, scorer, k)  # pyright: ignore[reportArgumentType]
    return py, np_ranked, total, len(node.evaluate(index))


def test_csr_layout_matches_postings(tiny_index: Index) -> None:
    vx = VectorIndex.from_index(tiny_index)
    assert vx.doc_ids.dtype == np.int32 and vx.tfs.dtype == np.int32
    for term, plist in tiny_index.postings.items():
        ids, tfs = vx.postings(term)
        assert ids.tolist() == [p.doc_id for p in plist]
        assert tfs.tolist() == [p.tf for p in plist]
        assert ids.base is not None  # view у спільний масив, а не копія
    assert vx.postings("nonexistent")[0].size == 0
    assert vx.doc_len.tolist() == [tiny_index.doc_lengths[d] for d in range(tiny_index.num_docs)]


@pytest.mark.parametrize(
    "query",
    [
        "python",
        "python async",
        "python OR java",
        "NOT python",
        "event loop",
        '"event loop"',
        "(python OR java) NOT loop",
        "rareword",
        "filler",
        "nonexistent",
        "python OR nonexistent",
        "привіт",
    ],
)
@pytest.mark.parametrize("scorer", SCORERS, ids=repr)
@pytest.mark.parametrize("k", [1, 2, 3, 10])
def test_identical_ranking_tiny(tiny_index: Index, query: str, scorer: Scorer, k: int) -> None:
    vx = VectorIndex.from_index(tiny_index)
    py, vec, total, matched = both(tiny_index, vx, query, scorer, k)
    assert vec == py  # ті самі пари (doc_id, score) у тому самому порядку — float порівнюється точно
    assert total == matched


def test_top_k_ties_prefer_lower_doc_id() -> None:
    scores = np.array([1.0, 3.0, 3.0, 3.0, 2.0, 3.0])
    candidates = np.arange(6, dtype=np.int32)
    assert top_k(scores, candidates, 2) == [(1, 3.0), (2, 3.0)]
    assert top_k(scores, candidates, 5) == [(1, 3.0), (2, 3.0), (3, 3.0), (5, 3.0), (4, 2.0)]
    assert top_k(scores, np.array([0, 4], dtype=np.int32), 10) == [(4, 2.0), (0, 1.0)]
    assert top_k(scores, np.empty(0, dtype=np.int32), 3) == []


@settings(max_examples=150, deadline=None)
@given(
    scores=st.lists(st.sampled_from([0.0, 0.5, 1.0, 2.0, 7.25]), min_size=0, max_size=40),
    k=st.integers(1, 45),
)
def test_top_k_matches_heapq(scores: list[float], k: int) -> None:
    """Багато рівних значень — найгірший випадок для argpartition."""
    import heapq

    expected = heapq.nlargest(k, enumerate(scores), key=lambda kv: (kv[1], -kv[0]))
    got = top_k(np.array(scores, dtype=np.float64), np.arange(len(scores), dtype=np.int32), k)
    assert got == expected


def test_searcher_backends_agree(tiny_index: Index) -> None:
    for scorer in SCORERS:
        fast = Searcher(tiny_index, scorer, backend="numpy")
        slow = Searcher(tiny_index, scorer, backend="python")
        for q in ["python", "python OR java", "NOT java", '"event loop"']:
            assert fast.search_page(q, k=3, offset=1, snippets=False) == slow.search_page(
                q, k=3, offset=1, snippets=False
            )


def test_plain_function_scorer_falls_back_to_python(tiny_index: Index) -> None:
    def ones(term: str, posting: object, index: Index) -> float:
        return 1.0

    s = Searcher(tiny_index, ones)
    assert s.backend == "python"
    assert [r.score for r in s.search("python", snippets=False)][:1] == [1.0]


def test_save_and_memmap_roundtrip(tiny_index: Index, tmp_path: Path) -> None:
    vx = VectorIndex.from_index(tiny_index)
    vx.save(tmp_path / "arrays")
    arrays = VectorIndex.load_arrays(tmp_path / "arrays", mmap=True)
    assert isinstance(arrays["doc_ids"], np.memmap)
    for name in ("offsets", "doc_ids", "tfs", "doc_len"):
        assert np.array_equal(arrays[name], getattr(vx, name))
    del arrays  # Windows: відображений файл не можна видалити, поки є memmap


# --- справжній корпус: усі розмічені запити Lab 03 + випадкові комбінації зі словника ----------
@pytest.fixture(scope="module")
def real() -> tuple[Index, VectorIndex]:
    if not REAL_CORPUS.is_file():
        pytest.skip("немає data/dota2wiki.jsonl")
    index = build_index(REAL_CORPUS, positions=True)
    return index, VectorIndex.from_index(index)


@pytest.mark.slow
@pytest.mark.parametrize("scorer", SCORERS[:2], ids=repr)
def test_identical_ranking_real_eval_queries(real: tuple[Index, VectorIndex], scorer: Scorer) -> None:
    index, vx = real
    queries = [
        q["query"] for q in json.loads((Path(__file__).parent / "eval_queries.json").read_text("utf-8"))
    ]
    for query in [*queries, "hero damage attack", "hero", "NOT hero", "stun OR slow OR silence"]:
        for k in (5, 10, 124, 1000):
            py, vec, total, matched = both(index, vx, query, scorer, k)
            assert vec == py, (query, k)
            assert total == matched


@pytest.mark.slow
@settings(max_examples=60, deadline=None)
@given(data=st.data())
def test_identical_ranking_real_random_queries(real: tuple[Index, VectorIndex], data: st.DataObject) -> None:
    index, vx = real
    vocab = sorted(index.postings)
    words = data.draw(st.lists(st.sampled_from(vocab), min_size=1, max_size=4))
    op = data.draw(st.sampled_from([" ", " OR ", " AND NOT "]))
    query = op.join(words)
    py, vec, _, _ = both(index, vx, query, data.draw(st.sampled_from(SCORERS)), data.draw(st.integers(1, 50)))
    assert vec == py, query
