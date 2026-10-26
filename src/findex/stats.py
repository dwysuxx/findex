"""Статистика корпусу (Lab 01) — ліниво, потоком, і з готового індексу (для `findex stats`)."""

import argparse
import time
import tracemalloc
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from findex.corpus import Document, iter_documents
from findex.index import Index
from findex.tokenize import tokenize


@dataclass(frozen=True, slots=True)
class CorpusStats:
    num_docs: int
    total_tokens: int
    unique_terms: int
    avg_doc_length: float
    top_terms: list[tuple[str, int]]


def count_tokens(docs: Iterable[Document]) -> Counter[str]:
    """Чиста логіка: приймає будь-який Iterable документів, нічого не читає з диска сама."""
    counter: Counter[str] = Counter()
    for doc in docs:
        counter.update(tokenize(doc.text or ""))
    return counter


def index_stats(index: Index, top: int = 20) -> CorpusStats:
    """Ті самі числа, що й Lab 01, але з індексу: частота терма = сума tf по постінгах."""
    cf = Counter({term: sum(p.tf for p in plist) for term, plist in index.items()})
    total = sum(index.doc_lengths.values())
    return CorpusStats(index.num_docs, total, len(index), index.avg_doc_length, cf.most_common(top))


def main(root: Path, limit: int | None = None) -> None:
    tracemalloc.start()
    start = time.perf_counter()
    docs: Iterable[Document] = iter_documents(root)
    if limit is not None:
        docs = islice(docs, limit)
    counter = count_tokens(docs)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(counter.total())
    print(len(counter))
    print(counter.most_common(50))
    print(f"Час: {elapsed:.2f} c")
    print(f"Пік пам'яті: {peak / 1024 / 1024:.2f} МБ")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(args.root, args.limit)
