import json
from collections.abc import Sequence

import pytest

from findex.engine import Searcher
from findex.explain import explain
from findex.index import build_index
from findex.query import parse
from findex.ranking import BM25, ResultList, SearchResult, TfIdf

DOCS = [
    "python async await event loop python",
    "python java",
    "java event loop",
    "async python tutorial " + "filler " * 40,
    "rareword python",
]


@pytest.fixture
def index(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text(
        "\n".join(json.dumps({"id": f"d{i}", "text": t}) for i, t in enumerate(DOCS)), encoding="utf-8"
    )
    return build_index(path, positions=True)


# --- ResultList ---------------------------------------------------------------------
@pytest.fixture
def results():
    return ResultList(SearchResult(10.0 - i, i, f"d{i}") for i in range(7))


def test_result_list_is_a_sequence(results):
    assert isinstance(results, Sequence)
    assert len(results) == 7 and bool(results) and not ResultList()
    assert results[0].doc_id == 0 and results[-1].doc_id == 6
    assert results[2] in results
    assert results.index(results[3]) == 3  # mixin-метод із Sequence
    assert [r.doc_id for r in reversed(results)] == [6, 5, 4, 3, 2, 1, 0]
    with pytest.raises(IndexError):
        results[7]


def test_slicing_returns_result_list(results):
    top = results[:5]
    assert type(top) is ResultList and len(top) == 5
    assert results[::-2] == ResultList([results[6], results[4], results[2], results[0]])
    assert repr(results[:1]).startswith("ResultList([SearchResult(")
    assert results != list(results)  # не плутаємо з list


def test_search_returns_result_list(index):
    with Searcher(index) as s:
        res = s.search("python", k=10, snippets=False)
    assert type(res) is ResultList and type(res[:2]) is ResultList


# --- explain(node) ------------------------------------------------------------------
def test_explain_query_tree():
    expected = """\
OR
├── TERM a
└── AND
    ├── TERM b
    ├── TERM c
    ├── NOT
    │   └── TERM d
    └── PHRASE "x y\""""
    assert explain(parse('a OR b c NOT d "x y"')) == expected


def test_explain_unknown_type():
    with pytest.raises(TypeError):
        explain(42)


# --- explain(scorer, ...) -----------------------------------------------------------
def _total(text: str) -> float:
    return float(text.split(" = ", 1)[0])


@pytest.mark.parametrize("scorer", [BM25(), TfIdf(), BM25(k1=1.2, b=0.0)])
def test_explain_score_matches_search(index, scorer):
    query = "python OR rareword OR async"
    with Searcher(index, scorer) as s:
        for r in s.search(query, k=5, snippets=False):
            text = s.explain(query, r.doc_id)
            assert _total(text) == pytest.approx(r.score, abs=1e-3)
            parts = [_total(line.strip()) for line in text.splitlines()[1:] if "'" in line]
            assert sum(parts) == pytest.approx(r.score, abs=3e-3)  # сума внесків = score


def test_explain_shows_missing_terms(index):
    text = explain(BM25(), index, 1, ["python", "rareword"])
    assert "'rareword': немає в документі" in text and "idf" in text and "dl=2" in text


def test_explain_any_scorer_function(index):
    def const(term, posting, ix):
        return 1.0

    text = explain(const, index, 4, ["python", "rareword", "java"])
    assert text.startswith("2.000 = const") and "'java': немає в документі" in text
