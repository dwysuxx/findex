import sys
import argparse
import time
import tracemalloc
from pathlib import Path
from findex.corpus import iter_documents
from findex.tokenize import tokenize
from collections import Counter
from itertools import islice


def main(root: Path, limit: int | None = None) -> None:
    tracemalloc.start()
    start = time.perf_counter()
    docs = iter_documents(root)
    if limit is not None:
        docs = islice(docs, limit)
    all_tokens = (tok for doc in docs for tok in tokenize(doc.text))
    counter = Counter()
    for tok in all_tokens:
        counter[tok] += 1
    elapsed = time.perf_counter() - start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(sum(counter.values()))
    print(len(counter))
    print(counter.most_common(50))
    print(f"Час:{elapsed:.2f}c")
    print(f"Пик памяти: {peak / 1024 / 1024:.2f} МБ")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(args.root, args.limit)