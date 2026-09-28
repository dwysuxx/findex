import time
import tracemalloc
from pathlib import Path
from collections import Counter
from findex.corpus import iter_documents
from findex.tokenize import tokenize


def eager_load_documents(root):
    return list(iter_documents(root))


def eager_tokenize_all(docs):
    result = []
    for doc in docs:
        result.append(list(tokenize(doc.text)))
    return result


def main_eager(root: Path) -> None:
    tracemalloc.start()
    start = time.perf_counter()

    docs = eager_load_documents(root)
    all_tokens = eager_tokenize_all(docs)
    all_tokens_flat = [tok for doc_tokens in all_tokens for tok in doc_tokens]
    counter = Counter(all_tokens_flat)

    elapsed = time.perf_counter() - start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(sum(counter.values()))
    print(len(counter))
    print(f"Время: {elapsed:.2f}с")
    print(f"Пик памяти: {peak / 1024 / 1024:.2f} МБ")


if __name__ == "__main__":
    import sys
    main_eager(Path(sys.argv[1]))