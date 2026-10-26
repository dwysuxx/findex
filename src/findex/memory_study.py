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
from collections.abc import Callable
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


type ObjPostings = dict[str, list[PlainPosting]] | dict[str, list[SlotPosting]]
type ArrayPostings = dict[str, tuple[array[int], array[int]]]


def build_objects[P: (PlainPosting, SlotPosting)](root: Path, cls: type[P]) -> dict[str, list[P]]:
    postings: defaultdict[str, list[P]] = defaultdict(list)
    for doc_id, doc in enumerate(iter_documents(root)):
        for term, tf in Counter(tokenize(doc.text or "")).items():
            postings[term].append(cls(doc_id, tf))
    return dict(postings)  # plain dict: defaultdict of a local class isn't needed on disk


# --- variant 3: no objects, two parallel array('I') per term ----------------
def build_arrays(root: Path) -> ArrayPostings:
    postings: ArrayPostings = {}
    for doc_id, doc in enumerate(iter_documents(root)):
        for term, tf in Counter(tokenize(doc.text or "")).items():
            pair = postings.get(term)
            if pair is None:
                pair = postings[term] = (array("I"), array("I"))
            pair[0].append(doc_id)
            pair[1].append(tf)
    return postings


type Builder = Callable[[Path], ObjPostings | ArrayPostings]


def _plain(root: Path) -> ObjPostings:
    return build_objects(root, PlainPosting)


def _slots(root: Path) -> ObjPostings:
    return build_objects(root, SlotPosting)


VARIANTS: dict[str, Builder] = {
    "`list[Posting]`, plain `@dataclass`": _plain,
    "`list[Posting]`, `slots=True`": _slots,
    "`array('I')` pairs (doc_ids, tfs)": build_arrays,
}


def count_postings(postings: ObjPostings | ArrayPostings) -> int:
    total = 0
    for v in postings.values():
        total += len(v[0]) if isinstance(v, tuple) else len(v)  # звуження: пара масивів чи список
    return total


type Row = tuple[str, float, float, float, int]


def measure(name: str, build: Builder, root: Path, tmp: Path) -> Row:
    gc.collect()
    tracemalloc.start()
    postings = build(root)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    with open(tmp, "wb") as f:
        pickle.dump(postings, f, protocol=pickle.HIGHEST_PROTOCOL)
    size = tmp.stat().st_size
    n_post = count_postings(postings)
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
    print(
        "\nПам'ятка (розмір одного елемента, байт): "
        f"int={sys.getsizeof(1000)}, plain Posting shallow={sys.getsizeof(PlainPosting(1, 1))} "
        f"+ __dict__={sys.getsizeof(PlainPosting(1, 1).__dict__)}, "
        f"slots Posting={sys.getsizeof(SlotPosting(1, 1))}, array('I') item=4"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    main(ap.parse_args().root)
