"""M4 — memory study: three representations of postings, same corpus.

Run from the project root:
    python -m findex.memory_study data/dota2wiki.jsonl

For every variant measures (each in a fresh tracemalloc session):
  * peak memory while building the index from the lazily streamed corpus,
  * pickle file size,
  * load time (pickle.load),
and prints a markdown table ready to paste into the README.
"""

import argparse
import gc
import pickle
import sys
import time
import tracemalloc
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from findex.corpus import iter_documents
from findex.tokenize import tokenize


# --- variant 1: plain dataclass (every instance carries a __dict__) ---------
@dataclass
class PlainPosting:
    doc_id: int
    tf: int


# --- variant 2: slotted (and frozen, like the real Posting) -----------------
@dataclass(frozen=True, slots=True)
class SlotPosting:
    doc_id: int
    tf: int


def build_objects(root: Path, cls) -> dict:
    postings: dict[str, list] = defaultdict(list)
    for doc_id, doc in enumerate(iter_documents(root)):
        for term, tf in Counter(tokenize(doc.text or "")).items():
            postings[term].append(cls(doc_id, tf))
    return dict(postings)  # plain dict: defaultdict of a local class isn't needed on disk


# --- variant 3: no objects, two parallel array('I') per term ----------------
def build_arrays(root: Path) -> dict:
    postings: dict[str, tuple[array, array]] = {}
    for doc_id, doc in enumerate(iter_documents(root)):
        for term, tf in Counter(tokenize(doc.text or "")).items():
            pair = postings.get(term)
            if pair is None:
                pair = postings[term] = (array("I"), array("I"))
            pair[0].append(doc_id)
            pair[1].append(tf)
    return postings


VARIANTS = {
    "`list[Posting]`, plain `@dataclass`": lambda r: build_objects(r, PlainPosting),
    "`list[Posting]`, `slots=True`": lambda r: build_objects(r, SlotPosting),
    "`array('I')` pairs (doc_ids, tfs)": build_arrays,
}


def measure(name: str, build, root: Path, tmp: Path) -> tuple[str, float, float, float, int]:
    gc.collect()
    tracemalloc.start()
    postings = build(root)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    with open(tmp, "wb") as f:
        pickle.dump(postings, f, protocol=pickle.HIGHEST_PROTOCOL)
    size = tmp.stat().st_size
    n_post = sum(len(v) if isinstance(v, list) else len(v[0]) for v in postings.values())
    del postings
    gc.collect()

    t0 = time.perf_counter()
    with open(tmp, "rb") as f:
        pickle.load(f)  # our own file — see the security note in store.py
    load = time.perf_counter() - t0
    tmp.unlink()
    return name, peak / 2**20, size / 2**20, load, n_post


def main(root: Path) -> None:
    rows = [measure(n, b, root, Path("_m4_tmp.bin")) for n, b in VARIANTS.items()]
    print(f"Постингів усього: {rows[0][4]:,}\n")
    print("| Postings representation | Peak memory (build), MB | Index file size, MB | Load time, s |")
    print("|---|---|---|---|")
    for name, peak, size, load, _ in rows:
        print(f"| {name} | {peak:.2f} | {size:.2f} | {load:.4f} |")
    base = rows[0][1]
    print()
    for name, peak, *_ in rows[1:]:
        print(f"{name}: {base / peak:.2f}× менше пікової пам'яті, ніж plain dataclass")
    print("\nПам'ятка (розмір одного елемента, байт): "
          f"int={sys.getsizeof(1000)}, plain Posting shallow={sys.getsizeof(PlainPosting(1, 1))} "
          f"+ __dict__={sys.getsizeof(PlainPosting(1, 1).__dict__)}, "
          f"slots Posting={sys.getsizeof(SlotPosting(1, 1))}, array('I') item=4")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    main(ap.parse_args().root)
