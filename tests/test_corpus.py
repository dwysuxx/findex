import logging
from pathlib import Path

import pytest

from findex.corpus import iter_documents
from findex.stats import count_tokens, index_stats


def test_bad_lines_are_skipped_with_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(
        '{"id": "ok", "text": "a\\nb"}\n\n{broken json\n{"id": "no-text"}\n{"id": 7, "text": "c"}\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="findex.corpus"):
        docs = list(iter_documents(corpus))
    assert [(d.doc_id, d.text) for d in docs] == [("ok", "a b"), ("7", "c")]  # \n -> пробіл, id -> str
    assert docs[0].path == Path(f"{corpus}#ok")
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2 and ":3" in messages[0] and ":4" in messages[1]  # номер рядка в повідомленні


def test_count_tokens_and_index_stats_agree(tiny_corpus: Path, tiny_index) -> None:
    counter = count_tokens(iter_documents(tiny_corpus))
    s = index_stats(tiny_index, top=3)
    assert s.total_tokens == counter.total()
    assert s.unique_terms == len(counter)
    assert s.top_terms == counter.most_common(3)
