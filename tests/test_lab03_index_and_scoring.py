import dataclasses
import json
from collections.abc import Mapping

import pytest

from findex.engine import Searcher
from findex.index import DocMeta, Index, Posting, build_index, open_index
from findex.ranking import BM25, Scorer, SearchResult, TfIdf
from findex.store import save
from findex.utils import timed

DOCS = [
    "python async await event loop python",
    "python java",
    "java event loop",
    "async python tutorial " + "filler " * 40,
    "rareword python",
]


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text(
        "\n".join(json.dumps({"id": f"d{i}", "text": t}) for i, t in enumerate(DOCS)), encoding="utf-8"
    )
    return path


@pytest.fixture
def index(corpus):
    return build_index(corpus, positions=True)


# --- M1: Index как объект ---------------------------------------------------------------
def test_dunder_protocols(index):
    assert isinstance(index, Mapping)
    assert len(index) == len(list(index)) == len(index.postings)
    assert "python" in index and "nope" not in index
    assert [p.doc_id for p in index["python"]] == [0, 1, 3, 4]
    with pytest.raises(KeyError):
        index["nope"]
    assert "nope" not in index  # index["nope"] не должен был создать запись
    assert index.get("nope") is None
    assert repr(index).startswith("Index(terms=") and "docs=5" in repr(index)


def test_derived_attributes(index):
    assert index.num_docs == 5
    assert index.df("python") == 4 and index.df("nope") == 0
    assert index.doc_length(1) == 2
    first = index.avg_doc_length
    assert "avg_doc_length" in index.__dict__  # cached_property закешировал значение
    assert index.avg_doc_length == first


def test_posting_is_hashable_and_frozen():
    assert len({Posting(1, 2), Posting(1, 2), Posting(1, 3)}) == 2
    assert hash(DocMeta("a", "b")) == hash(DocMeta("a", "b"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        Posting(1, 2).tf = 5  # type: ignore[misc]


def test_open_index_closes_even_on_exception(index, tmp_path):
    p = tmp_path / "i.bin"
    save(index, p)
    holder = {}
    with pytest.raises(RuntimeError), open_index(p) as ix:
        holder["ix"] = ix
        assert len(ix) > 0
        raise RuntimeError("boom")
    assert holder["ix"].closed and len(holder["ix"]) == 0


def test_open_index_closes_on_return(index, tmp_path):
    p = tmp_path / "i.bin"
    save(index, p)

    def use():
        with open_index(p) as ix:
            holder.append(ix)
            return len(ix)

    holder = []
    assert use() > 0 and holder[0].closed


def test_json_roundtrip_keeps_positions(index, tmp_path):
    p = tmp_path / "i.json"
    save(index, p, "json")
    from findex.store import load

    loaded = load(p, "json")
    assert loaded.has_positions and loaded["python"] == index["python"]


# --- M2: скореры ----------------------------------------------------------------------
def test_scorer_protocol_accepts_classes_and_plain_functions(index):
    assert isinstance(BM25(), Scorer) and isinstance(TfIdf(), Scorer)
    assert isinstance(lambda term, posting, ix: 1.0, Scorer)  # протоколу всё равно, класс это или функция


def test_rare_term_outranks_common_term(index):
    s = Searcher(index, BM25())
    results = s.search("rareword OR java", k=5, snippets=False)
    assert results[0].title == "d4"  # «rareword» (df=1) выше, чем «java» (df=2)


def test_bm25_saturation_and_length_normalisation():
    bm = BM25()

    def gain(tf: int) -> float:
        return bm.term_score(tf + 1, 5, 100, 1000, 100) - bm.term_score(tf, 5, 100, 1000, 100)

    assert gain(1) > gain(5) > gain(20) > 0
    assert gain(20) < 0.01 * bm.term_score(1, 5, 100, 1000, 100)
    short = bm.term_score(1, 5, 20, 1000, 100)
    long_ = bm.term_score(1, 5, 500, 1000, 100)
    assert short > long_
    flat = BM25(b=0.0)
    assert flat.term_score(1, 5, 20, 1000, 100) == flat.term_score(1, 5, 500, 1000, 100)


def test_tfidf_ignores_terms_present_in_every_doc(index):
    # df == N  =>  ln(N/df) == 0
    full = Index({"x": [Posting(0, 2), Posting(1, 1)]}, {0: 2, 1: 1}, {})
    assert TfIdf()("x", Posting(0, 2), full) == 0.0


def test_search_results_sorted_and_ordered(index):
    s = Searcher(index, BM25())
    results = s.search("python", k=10, snippets=False)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert sorted(results, reverse=True) == sorted(results, key=lambda r: (r.score, r.doc_id), reverse=True)
    assert SearchResult(1.0, 1) < SearchResult(2.0, 0)


# --- M4: кеш, @timed, сниппеты --------------------------------------------------------
def test_lru_cache_hit_on_repeated_query(index):
    s = Searcher(index)
    s.search("python java", snippets=False)
    assert s.cache_info().misses == 1 and s.cache_info().hits == 0
    s.search("python java", snippets=False)
    assert s.cache_info().hits == 1


def test_timed_keeps_metadata():
    @timed
    def search(q):
        """doc"""
        return q

    assert search.__name__ == "search" and search.__doc__ == "doc" and search("x") == "x"


def test_snippet_highlights_terms(index, corpus):
    with Searcher(index, corpus_path=corpus) as s:
        r = s.search("rareword", k=1)[0]
    assert "**rareword**" in r.snippet


def test_snippet_without_corpus_file_is_empty(index, tmp_path):
    with Searcher(index, corpus_path=tmp_path / "missing.jsonl") as s:
        assert s.search("rareword", k=1)[0].snippet == ""
