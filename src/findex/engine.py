"""M2 + M4: Searcher — запит -> розпарсене дерево -> збіги -> ранжування -> сніпети.

    python -m findex.engine index.bin "python AND (async OR await) NOT java" --scorer bm25 -k 5 -v
    python -m findex.engine index.bin '"event loop"' --repeat 2 -v      # другий виклик — cache HIT
"""

import argparse
import functools
import logging
import textwrap
import time
import tracemalloc
from pathlib import Path
from typing import Self

from findex.explain import explain
from findex.index import Index, open_index
from findex.query import parse
from findex.ranking import BM25, ResultList, Scorer, SearchResult, TfIdf, rank
from findex.snippets import TextStore, make_snippet
from findex.utils import timed

log = logging.getLogger(__name__)


class Searcher:
    """Пошук по Index зі змінним Scorer, кешем запитів і сніпетами.

    Кеш: lru_cache створюється ПЕР-ЕКЗЕМПЛЯРНО (в __init__), бо
      * Index не hashable (Mapping визначає __eq__ => __hash__ = None), тож він не може бути
        аргументом lru_cache;
      * кеш на рівні методу класу тримав би self живим і ділився б між різними індексами.
    Ключ кешу — (рядок запиту, k): обидва hashable. Індекс вважається незмінним.
    """

    def __init__(
        self,
        index: Index,
        scorer: Scorer | None = None,
        *,
        cache_size: int = 256,
        corpus_path: Path | str | None = None,
    ) -> None:
        self.index = index
        self.scorer: Scorer = scorer if scorer is not None else BM25()
        self._corpus_path = corpus_path
        self._texts: TextStore | None = None
        self._ranked = functools.lru_cache(maxsize=cache_size)(self._rank_ids)

    @timed
    def _rank_ids(self, query: str, k: int):
        node = parse(query)
        matched = node.evaluate(self.index)
        ranked = rank(self.index, matched, node.terms(), self.scorer, k)
        return tuple(ranked), tuple(dict.fromkeys(node.terms()))

    def cache_info(self):
        return self._ranked.cache_info()

    @timed
    def search(self, query: str, k: int = 10, snippets: bool = True) -> ResultList:
        hits_before = self._ranked.cache_info().hits
        ranked, terms = self._ranked(query, k)
        cache_hit = self._ranked.cache_info().hits > hits_before
        log.debug("query=%r k=%d cache=%s", query, k, "HIT" if cache_hit else "MISS")

        results = []
        for doc_id, score in ranked:
            meta = self.index.doc_meta[doc_id]
            snippet = self._snippet(meta.path, meta.title, terms) if snippets else ""
            results.append(SearchResult(score, doc_id, meta.title, snippet))
        return ResultList(results)

    def explain(self, query: str, doc_id: int) -> str:
        """Чому doc_id отримав свій score на цьому запиті: розклад по термах."""
        return explain(self.scorer, self.index, doc_id, parse(query).terms())

    def _snippet(self, path: str, doc_id: str, terms: tuple[str, ...]) -> str:
        if self._texts is None:
            corpus = self._corpus_path or path.rpartition("#")[0]  # path = "<файл.jsonl>#<id>"
            self._texts = TextStore(corpus)
        text = self._texts.get(doc_id)
        return make_snippet(text, terms) if text else ""

    def close(self) -> None:
        if self._texts is not None:
            self._texts.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False  # винятки не ковтаємо


SCORERS = {"bm25": BM25, "tfidf": TfIdf}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("index_path", type=Path)
    parser.add_argument("query", type=str)
    parser.add_argument("--scorer", choices=SCORERS, default="bm25")
    parser.add_argument("-k", type=int, default=10)
    parser.add_argument("--format", choices=["pickle", "json"], default="pickle")
    parser.add_argument("--corpus", type=Path, default=None, help="jsonl для сніпетів (за замовч. зі шляху в індексі)")
    parser.add_argument("--repeat", type=int, default=1, help="повторити запит N разів (показати cache HIT)")
    parser.add_argument("--explain", action="store_true", help="показати дерево запиту і розклад score")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG-логи: час і cache HIT/MISS")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    tracemalloc.start()
    t0 = time.perf_counter()
    with open_index(args.index_path, args.format) as index:
        load_time = time.perf_counter() - t0
        print(repr(index))
        if args.explain:
            print(explain(parse(args.query)))
        with Searcher(index, SCORERS[args.scorer](), corpus_path=args.corpus) as searcher:
            for _ in range(args.repeat):
                t1 = time.perf_counter()
                results = searcher.search(args.query, k=args.k)
                search_time = time.perf_counter() - t1
            print(f"Знайдено (top-{args.k}): {len(results)}")
            for r in results:
                print(f"  {r}")
                if r.snippet:
                    print(f"      {r.snippet}")
                if args.explain:
                    print(textwrap.indent(searcher.explain(args.query, r.doc_id), "      "))
            print(f"Кеш: {searcher.cache_info()}")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"Час завантаження: {load_time:.4f} с")
    print(f"Час пошуку (останній виклик): {search_time * 1000:.3f} мс")
    print(f"Пік пам'яті: {peak / (1024 * 1024):.2f} МБ")
