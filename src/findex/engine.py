"""M2 + M4: Searcher — запит -> розпарсене дерево -> збіги -> ранжування -> сніпети.

CLI — у findex.cli:  findex -vv search index.bin '"event loop"' --repeat 2   # другий виклик — cache HIT
"""

import functools
import logging
from dataclasses import dataclass
from functools import _CacheInfo as CacheInfo  # pyright: ignore[reportPrivateUsage]  # публічного імені для типу cache_info() немає
from pathlib import Path
from types import TracebackType
from typing import Literal, Self, cast

from findex.explain import explain
from findex.index import Index
from findex.query import parse
from findex.ranking import BM25, ResultList, Scorer, SearchResult, rank
from findex.semantic import Embedder, SemanticIndex, Vectors, bag_of_words, normalized, rrf, semantic_text
from findex.snippets import TextStore, make_snippet
from findex.tokenize import tokenize
from findex.utils import timed
from findex.vector import BatchScorer, VectorIndex, mask, supports_batch
from findex.vector import rank as rank_vector

type Backend = Literal["numpy", "python"]
type Mode = Literal["keyword", "semantic", "hybrid"]

# Скільки документів з кожного списку зливає RRF. Глибше — майже нічого не змінює: внесок 100-го
# місця 1/160 проти 1/61 у першого.
HYBRID_DEPTH = 100

# Точка перетину (README L8.4): у NumPy фіксована ціна ≈ 35–50 мкс на запит (маски, flatnonzero,
# lexsort), у Python — ≈ 1.5 мкс на постінг. Нижче ≈ 30 постінгів Python-цикл швидший.
NUMPY_MIN_POSTINGS = 32

log = logging.getLogger(__name__)


class SemanticUnavailable(RuntimeError):
    """mode=semantic|hybrid, а ембедингів (findex embed) або моделі немає."""


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
        backend: Backend = "numpy",
        vector: VectorIndex | None = None,
        semantic: SemanticIndex | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.index = index
        self.scorer: Scorer = scorer if scorer is not None else BM25()
        # Lab 08: NumPy — за замовчуванням; довільний Scorer-функція (без batch) лишається на Python.
        self.backend: Backend = backend if supports_batch(self.scorer) else "python"
        self._vector = vector  # спільний для кількох Searcher (TF-IDF і BM25 у веб-сервері)
        self._corpus_path = corpus_path
        self._texts: TextStore | None = None
        self._ranked = functools.lru_cache(maxsize=cache_size)(self._rank_ids)
        self.semantic = semantic
        self.embedder = embedder
        self._query_vector = functools.lru_cache(maxsize=cache_size)(self._embed_query)

    @property
    def vector(self) -> VectorIndex:
        if self._vector is None:
            self._vector = VectorIndex.from_index(self.index)  # ліниво: Python-бекенду не потрібен
        return self._vector

    @timed
    def _rank_ids(self, query: str, k: int):
        node = parse(query)
        terms = tuple(dict.fromkeys(node.terms()))
        if self.backend == "numpy" and sum(self.vector.df(t) for t in terms) >= NUMPY_MIN_POSTINGS:
            ranked, total = rank_vector(
                self.vector, mask(node, self.vector), terms, cast(BatchScorer, self.scorer), k
            )
        else:
            matched = node.evaluate(self.index)
            ranked, total = rank(self.index, matched, terms, self.scorer, k), len(matched)
        return tuple(ranked), terms, total

    def cache_info(self) -> CacheInfo:
        return self._ranked.cache_info()

    @timed
    def search(self, query: str, k: int = 10, snippets: bool = True) -> ResultList:
        return self.search_page(query, k, snippets=snippets).results

    def _embed_query(self, text: str) -> Vectors:
        assert self.embedder is not None
        return normalized(self.embedder.embed_query(text))

    @property
    def has_semantic(self) -> bool:
        return self.semantic is not None and self.embedder is not None

    def search_page(
        self, query: str, k: int = 10, offset: int = 0, *, snippets: bool = True, mode: Mode = "keyword"
    ) -> Page:
        """Пагінація: ранжуємо top-(offset+k) і відрізаємо перші offset (ключ кешу — offset+k).

        mode (Lab 08): keyword — BM25/TF-IDF по мові запитів; semantic — косинус найкращого шматка;
        hybrid — обидва списки (по HYBRID_DEPTH) злиті RRF. Score у кожному режимі свій (BM25,
        косинус, RRF): між режимами порівнюється лише порядок.
        """
        q: Vectors | None = None
        if mode == "keyword":
            hits_before = self._ranked.cache_info().hits
            ranked, terms, total = self._ranked(query, offset + k)
            cache_hit = self._ranked.cache_info().hits > hits_before
            log.debug("query=%r k=%d cache=%s", query, offset + k, "HIT" if cache_hit else "MISS")
        else:
            if self.semantic is None or self.embedder is None:
                raise SemanticUnavailable("семантичний пошук вимкнено: немає ембедингів (findex embed)")
            q = self._query_vector(semantic_text(query))
            depth = max(HYBRID_DEPTH, offset + k)
            dense = self.semantic.search(q, depth)
            if mode == "semantic":
                ranked, total = dense, self.semantic.docs_with_chunks
                terms = tuple(dict.fromkeys(tokenize(semantic_text(query))))
            else:
                keyword, terms, _ = self._ranked(bag_of_words(query), depth)
                ranked = rrf([[d for d, _ in keyword], [d for d, _ in dense]])
                total = len(ranked)
            terms = self._informative(terms)

        results: list[SearchResult] = []
        for doc_id, score in ranked[offset : offset + k]:
            meta = self.index.doc_meta[doc_id]
            snippet = ""
            if snippets:
                chunk = self.semantic.best_chunk(q, doc_id) if q is not None and self.semantic else None
                snippet = self._snippet(meta.path, meta.title, terms, chunk, within=mode == "semantic")
            results.append(SearchResult(score, doc_id, meta.title, snippet))
        return Page(total, ResultList(results))

    def explain(self, query: str, doc_id: int) -> str:
        """Чому doc_id отримав свій score на цьому запиті: розклад по термах."""
        return explain(self.scorer, self.index, doc_id, parse(query).terms())

    def _informative(self, terms: tuple[str, ...]) -> tuple[str, ...]:
        """Слова природної мови для підсвітки: без тих, що є в більш ніж половині документів
        (the, and, hero...). Стоп-список не потрібен — його дає статистика індексу (idf)."""
        n = self.index.num_docs
        return tuple(t for t in terms if 0 < self.index.df(t) <= n / 2)

    def _snippet(
        self,
        path: str,
        doc_id: str,
        terms: tuple[str, ...],
        chunk: tuple[int, int] | None = None,
        within: bool = False,
    ) -> str:
        """chunk — найкращий шматок (семантика): там модель «побачила» сенс запиту. within=True —
        сніпет лише з нього; інакше з нього починається сніпет, якщо слів запиту в документі немає."""
        if self._texts is None:
            corpus = self._corpus_path or path.rpartition("#")[0]  # path = "<файл.jsonl>#<id>"
            self._texts = TextStore(corpus)
        doc = self._texts.prepared(doc_id)
        if doc is None or not doc.text:
            return ""
        return make_snippet(
            doc, terms, fallback_at=chunk[0] if chunk else 0, within=chunk if within else None
        )

    def close(self) -> None:
        if self._texts is not None:
            self._texts.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()  # None => винятки не ковтаємо
