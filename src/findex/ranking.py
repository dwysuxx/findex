"""M2: ранжування. Scorer — це протокол (structural typing), а не базовий клас."""

import heapq
import math
from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, overload, runtime_checkable

from findex.index import Index, Posting


@runtime_checkable
class Scorer(Protocol):
    """Будь-який виклик із такою сигнатурою — Scorer; успадковувати нічого не треба."""

    def __call__(self, term: str, posting: Posting, index: Index) -> float: ...


@dataclass(frozen=True)  # frozen => hashable, тож scorer можна використовувати як ключ кешу
class TfIdf:
    """score = (1 + ln tf) * ln(N / df). Терм, що є в кожному документі, дає 0."""

    def __call__(self, term: str, posting: Posting, index: Index) -> float:
        df = index.df(term)
        if df == 0 or posting.tf <= 0:
            return 0.0
        return (1.0 + math.log(posting.tf)) * math.log(index.num_docs / df)


@dataclass(frozen=True)
class BM25:
    """BM25 (варіант Lucene з idf, що завжди > 0).

    score = idf * tf*(k1+1) / (tf + k1*(1 - b + b*dl/avgdl))
    k1 керує насиченням tf; b — нормалізацією за довжиною документа (b=0: довжина ігнорується).
    """

    k1: float = 1.5
    b: float = 0.75

    @staticmethod
    def idf(df: int, num_docs: int) -> float:
        return math.log(1.0 + (num_docs - df + 0.5) / (df + 0.5))

    def term_score(self, tf: int, df: int, doc_len: float, num_docs: int, avg_len: float) -> float:
        """Чиста формула (без Index) — зручно для демонстрацій і тестів."""
        if tf <= 0 or df <= 0:
            return 0.0
        length_ratio = doc_len / avg_len if avg_len else 1.0
        norm = 1.0 - self.b + self.b * length_ratio
        return self.idf(df, num_docs) * tf * (self.k1 + 1.0) / (tf + self.k1 * norm)

    def __call__(self, term: str, posting: Posting, index: Index) -> float:
        return self.term_score(
            posting.tf,
            index.df(term),
            index.doc_length(posting.doc_id),
            index.num_docs,
            index.avg_doc_length,
        )


@dataclass(order=True)
class SearchResult:
    """order=True порівнює поля за порядком оголошення, тож sorted(results) сортує за score
    (потім за doc_id). snippet не бере участі в порівнянні."""

    score: float
    doc_id: int
    title: str = ""
    snippet: str = field(default="", compare=False, repr=False)

    def __str__(self) -> str:
        return f"[{self.doc_id}] {self.score:7.3f}  {self.title}"


class ResultList(Sequence[SearchResult]):
    """Видача пошуку. Sequence дає __contains__, __iter__, __reversed__, index(), count()
    безкоштовно; ми реалізуємо лише __getitem__ і __len__.

    Зріз повертає ResultList, а не list: results[:5] лишається видачею (з тим самим repr/str).
    """

    __slots__ = ("_items",)

    def __init__(self, items: Iterable[SearchResult] = ()) -> None:
        self._items: tuple[SearchResult, ...] = tuple(items)

    @overload
    def __getitem__(self, i: int) -> SearchResult: ...
    @overload
    def __getitem__(self, i: slice) -> "ResultList": ...
    def __getitem__(self, i: int | slice) -> "SearchResult | ResultList":
        if isinstance(i, slice):
            return ResultList(self._items[i])
        return self._items[i]  # IndexError/TypeError — як у вбудованих послідовностей

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ResultList):
            return self._items == other._items
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]  # як у list: рівність за вмістом, не хешується

    def __repr__(self) -> str:
        return f"ResultList({list(self._items)!r})"

    def __str__(self) -> str:
        return "\n".join(map(str, self._items)) if self._items else "(немає результатів)"


def rank(
    index: Index,
    matched: Collection[int],
    terms: Iterable[str],
    scorer: Scorer,
    k: int = 10,
) -> list[tuple[int, float]]:
    """Накопичує score[doc_id] += вага по постінгах кожного терма й бере top-k.

    heapq.nlargest — O(n log k), без повного сортування O(n log n).
    Документи, що збіглися, але не мають жодного оцінюваного терма, отримують 0.0.
    """
    scores: dict[int, float] = defaultdict(float)
    for term in dict.fromkeys(terms):  # унікальні терми зі збереженням порядку
        for posting in index.get(term, ()):
            if posting.doc_id in matched:
                scores[posting.doc_id] += scorer(term, posting, index)
    for doc_id in matched:
        scores.setdefault(doc_id, 0.0)
    # при рівних score — менший doc_id вище (детермінованість)
    return heapq.nlargest(k, scores.items(), key=lambda kv: (kv[1], -kv[0]))
