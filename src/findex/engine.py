"""M2 + M4: Searcher — запит -> розпарсене дерево -> збіги -> ранжування -> сніпети.

CLI — у findex.cli:  findex -vv search index.bin '"event loop"' --repeat 2   # другий виклик — cache HIT
"""

import functools
import logging
from dataclasses import dataclass
from functools import _CacheInfo as CacheInfo  # pyright: ignore[reportPrivateUsage]  # публічного імені для типу cache_info() немає
from pathlib import Path
from types import TracebackType
from typing import Self

from findex.explain import explain
from findex.index import Index
from findex.query import parse
from findex.ranking import BM25, ResultList, Scorer, SearchResult, rank
from findex.snippets import TextStore, make_snippet
from findex.utils import timed

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Page:
    """Сторінка видачі: results — це [offset, offset+k), total — скільки документів збіглося."""

    total: int
    results: ResultList


class Searcher:
    """Пошук по Index зі змінним Scorer, кешем запитів і сніпетами.

    Кеш: lru_cache створюється ПЕР-ЕКЗЕМПЛЯРНО (в __init__), бо
      * Index не hashable (Mapping визначає __eq__ => __hash__ = None), тож він не може бути
        аргументом lru_cache;
      * кеш на рівні методу класу тримав би self живим і ділився б між різними індексами.
    Ключ кешу — (рядок запиту, k): обидва hashable. Індекс вважається незмінним.

    Потоки (Lab 07: `def`-маршрути FastAPI виконуються в пулі потоків): lru_cache і TextStore
    потокобезпечні, тож один Searcher обслуговує паралельні запити.
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
        return tuple(ranked), tuple(dict.fromkeys(node.terms())), len(matched)

    def cache_info(self) -> CacheInfo:
        return self._ranked.cache_info()

    @timed
    def search(self, query: str, k: int = 10, snippets: bool = True) -> ResultList:
        return self.search_page(query, k, snippets=snippets).results

    def search_page(self, query: str, k: int = 10, offset: int = 0, *, snippets: bool = True) -> Page:
        """Пагінація: ранжуємо top-(offset+k) і відрізаємо перші offset (ключ кешу — offset+k)."""
        hits_before = self._ranked.cache_info().hits
        ranked, terms, total = self._ranked(query, offset + k)
        cache_hit = self._ranked.cache_info().hits > hits_before
        log.debug("query=%r k=%d cache=%s", query, offset + k, "HIT" if cache_hit else "MISS")

        results: list[SearchResult] = []
        for doc_id, score in ranked[offset:]:
            meta = self.index.doc_meta[doc_id]
            snippet = self._snippet(meta.path, meta.title, terms) if snippets else ""
            results.append(SearchResult(score, doc_id, meta.title, snippet))
        return Page(total, ResultList(results))

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

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()  # None => винятки не ковтаємо
