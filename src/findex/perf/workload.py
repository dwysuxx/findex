"""M1: навантаження для профайлерів — гаряча частина без старту інтерпретатора й завантаження індексу.

    python -m cProfile -s cumtime -m findex.perf.workload search index.bin "hero damage attack"
    py-spy record -o docs/search.svg -- python -m findex.perf.workload search index.bin "hero damage attack"
    scalene --cli --cpu-only ---  -m findex.perf.workload search index.bin "hero damage attack"
    py-spy record -o docs/index.svg -- python -m findex.perf.workload index data/dota2wiki.jsonl

Один `findex search` — це ≈ 0.7 с завантаження pickle і ≈ 0.05 с пошуку: профіль такого запуску
показав би лише pickle. Тут індекс вантажиться один раз, а запит повторюється N разів без кешу.
"""

import argparse
import time
from pathlib import Path

from findex.engine import Searcher
from findex.index import build_index
from findex.ranking import BM25, TfIdf
from findex.store import load


def search(index_path: Path, query: str, n: int, scorer: str, snippets: bool) -> None:
    index = load(index_path)
    sc = TfIdf() if scorer == "tfidf" else BM25()
    with Searcher(index, sc, cache_size=0) as searcher:  # cache_size=0: кожен виклик рахує заново
        searcher.search(query, snippets=snippets)  # прогрів: TextStore будує зміщення
        t0 = time.perf_counter()
        for _ in range(n):
            searcher.search(query, snippets=snippets)
        dt = time.perf_counter() - t0
    print(f"{n} x search({query!r}): {dt / n * 1000:.2f} ms/запит")


def index(corpus: Path, positions: bool) -> None:
    t0 = time.perf_counter()
    ix = build_index(corpus, positions=positions)
    print(f"{ix!r}: {time.perf_counter() - t0:.2f} s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("index", type=Path)
    s.add_argument("query")
    s.add_argument("-n", type=int, default=200)
    s.add_argument("--scorer", choices=["bm25", "tfidf"], default="bm25")
    s.add_argument("--no-snippets", dest="snippets", action="store_false")
    i = sub.add_parser("index")
    i.add_argument("corpus", type=Path)
    i.add_argument("--positions", action="store_true")
    a = ap.parse_args()
    if a.cmd == "search":
        search(a.index, a.query, a.n, a.scorer, a.snippets)
    else:
        index(a.corpus, a.positions)
