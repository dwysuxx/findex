"""Інтеграційні тести на справжньому корпусі (data/dota2wiki.jsonl). Повільні: -m "not slow"."""

import json
from pathlib import Path

import pytest

from conftest import REAL_CORPUS
from findex.engine import Searcher
from findex.evaluate import precision_at_k
from findex.index import Index, build_index
from findex.ranking import BM25, TfIdf

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not REAL_CORPUS.is_file(), reason="немає data/dota2wiki.jsonl"),
]


@pytest.fixture(scope="module")
def real_index() -> Index:
    return build_index(REAL_CORPUS, positions=True)


def test_real_index_shape(real_index: Index) -> None:
    # 124 герої (doc_id 0..123, як у Lab 01–08) + 251 предмет (dotabuff_parser/scripts/build_corpus.py)
    assert real_index.num_docs == 375
    assert real_index.doc_meta[13].title == "pudge"  # герої лишились першими, id не зсунулись
    assert {"blink_dagger", "black_king_bar"} <= {m.title for m in real_index.doc_meta.values()}
    assert len(real_index) > 10_000
    assert "pudge" in real_index


def test_three_sanity_checks_on_real_corpus(real_index: Index) -> None:
    bm = BM25()
    n, avg = real_index.num_docs, real_index.avg_doc_length
    # 1. рідкісний терм важить більше за поширений ("bonus" є в усіх 375 документах)
    assert bm.term_score(1, real_index.df("ascetic"), avg, n, avg) > 100 * bm.term_score(
        1, real_index.df("bonus"), avg, n, avg
    )
    # 2. 20-те входження майже нічого не додає
    df = real_index.df("thrown")
    gain20 = bm.term_score(21, df, avg, n, avg) - bm.term_score(20, df, avg, n, avg)
    assert gain20 < 0.01 * bm.term_score(1, df, avg, n, avg)
    # 3. короткий документ з одним збігом вище за довгий з одним збігом
    ones = [p for p in real_index["date"] if p.tf == 1]
    short = min(ones, key=lambda p: real_index.doc_length(p.doc_id))
    long_ = max(ones, key=lambda p: real_index.doc_length(p.doc_id))
    assert bm("date", short, real_index) > bm("date", long_, real_index)


def test_precision_at_5_bm25_not_worse(real_index: Index) -> None:
    queries = json.loads((Path(__file__).parent / "eval_queries.json").read_text(encoding="utf-8"))
    queries = [q for q in queries if not q.get("paraphrase")]  # парафрази — для семантики (Lab 08)
    mean: dict[str, float] = {}
    for name, scorer in (("tfidf", TfIdf()), ("bm25", BM25())):
        s = Searcher(real_index, scorer)
        mean[name] = sum(
            precision_at_k(s.search(q["query"], k=5, snippets=False), set(q["relevant"]), 5) for q in queries
        ) / len(queries)
    assert mean["bm25"] >= mean["tfidf"] >= 0.4


def test_readme_reports_run(capsys: pytest.CaptureFixture[str]) -> None:
    """Скрипти, чий вивід вставлено в README, виконуються і друкують очікувані таблиці."""
    from findex import evaluate, sanity_checks

    sanity_checks.main(REAL_CORPUS, "pickle")
    means = evaluate.main(
        REAL_CORPUS, Path(__file__).parent / "eval_queries.json", 5, "pickle", suggest=False
    )
    out = capsys.readouterr().out
    assert "Перевірка 1" in out and "Перевірка 3" in out
    assert "| `remnant` |  | 4 |" in out and "середнє: оригінальні (10 запитів)" in out
    assert "середнє: парафрази (5 запитів)" in out
    # ключові слова на парафразах майже безсилі — саме тому Lab 08 додає семантику
    assert means["paraphrase"]["BM25"] < means["original"]["BM25"]
