import time
import tracemalloc
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from findex.corpus import iter_documents
from findex.tokenize import tokenize


@dataclass(frozen=True, slots=True)
class Posting:
    doc_id: int
    tf: int


@dataclass(frozen=True, slots=True)
class DocMeta:
    title: str
    path: str


@dataclass(slots=True)
class Index:
    postings: dict[str, list[Posting]]
    doc_lengths: dict[int, int]
    doc_meta: dict[int, DocMeta]


def build_index(root: Path) -> Index:
    postings: dict[str, list[Posting]] = defaultdict(list)
    doc_lengths: dict[int, int] = {}
    doc_meta: dict[int, DocMeta] = {}

    for internal_id, doc in enumerate(iter_documents(root)):
        tokens = list(tokenize(doc.text or ""))
        doc_lengths[internal_id] = len(tokens)
        doc_meta[internal_id] = DocMeta(title=doc.doc_id, path=str(doc.path))

        counts = Counter(tokens)
        for term, tf in counts.items():
            postings[term].append(Posting(doc_id=internal_id, tf=tf))

    return Index(postings=postings, doc_lengths=doc_lengths, doc_meta=doc_meta)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    args = parser.parse_args()

    tracemalloc.start()
    t0 = time.perf_counter()
    idx = build_index(args.root)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"Документів: {len(idx.doc_lengths)}")
    print(f"Унікальних термінів (словник): {len(idx.postings)}")
    print(f"Час побудови: {elapsed:.2f} с")
    print(f"Пік пам'яті: {peak / (1024 * 1024):.2f} МБ")