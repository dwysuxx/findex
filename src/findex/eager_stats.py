"""Lab 01: та сама статистика, але жадібно (усе в пам'яті) — для порівняння піку пам'яті."""

import sys
import time
import tracemalloc
from collections import Counter
from pathlib import Path

from findex.corpus import Document, iter_documents
from findex.tokenize import tokenize


def eager_load_documents(root: Path) -> list[Document]:
    return list(iter_documents(root))


def eager_tokenize_all(docs: list[Document]) -> list[list[str]]:
    return [list(tokenize(doc.text or "")) for doc in docs]


def main_eager(root: Path) -> None:
    tracemalloc.start()
    start = time.perf_counter()

    docs = eager_load_documents(root)
    all_tokens = eager_tokenize_all(docs)
    all_tokens_flat = [tok for doc_tokens in all_tokens for tok in doc_tokens]
    counter = Counter(all_tokens_flat)

    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(counter.total())
    print(len(counter))
    print(f"Час: {elapsed:.2f} с")
    print(f"Пік пам'яті: {peak / 1024 / 1024:.2f} МБ")


if __name__ == "__main__":
    main_eager(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/dota2wiki.jsonl"))
