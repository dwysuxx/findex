"""Pydantic-моделі на межі HTTP: недовірені дані ззовні -> перевірені об'єкти, і назад.

Усередині лишаються dataclass-и Lab 02-03 (Posting, DocMeta, ranking.SearchResult): там дані вже
наші й перевіряти нема чого, а валідація на кожен постінг коштувала б часу. Перетворення — в
одному місці (from_internal), це і є «шов» між шарами.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from findex import ranking

ScorerName = Literal["bm25", "tfidf"]

MAX_DEPTH = 1000  # page * k: глибше ранжувати не даємо — top-k купа росте з глибиною


class SearchRequest(BaseModel):
    """Параметри /search (і сторінки /). FastAPI бере їх із query string: Annotated[..., Query()]."""

    model_config = ConfigDict(extra="forbid")  # ?kk=5 — помилка, а не мовчазне ігнорування

    q: str = Field(min_length=1, max_length=200, description='Запит: AND/OR/NOT, дужки, "фрази"')
    k: int = Field(10, ge=1, le=100, description="Результатів на сторінку")
    page: int = Field(1, ge=1, description="Номер сторінки, з 1")
    scorer: ScorerName = "bm25"
    snippets: bool = True

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.k

    @model_validator(mode="after")
    def _depth_limit(self) -> Self:
        if self.page * self.k > MAX_DEPTH:
            raise ValueError(f"page * k must be <= {MAX_DEPTH}")
        return self


class SearchResult(BaseModel):
    doc_id: int
    title: str
    score: float
    snippet: str = Field("", description="Вікно тексту; збіги обгорнуті в **...**")

    @classmethod
    def from_internal(cls, r: ranking.SearchResult) -> Self:
        return cls(doc_id=r.doc_id, title=r.title, score=round(r.score, 6), snippet=r.snippet)


class SearchResponse(BaseModel):
    query: str
    scorer: ScorerName
    total: int = Field(description="Скільки документів збіглося (на всіх сторінках)")
    page: int
    k: int
    pages: int
    took_ms: float
    results: list[SearchResult]


class DocumentResponse(BaseModel):
    doc_id: int
    title: str
    length: int = Field(description="Довжина в токенах")
    text: str


class StatsResponse(BaseModel):
    version: str
    num_docs: int
    vocabulary: int
    total_tokens: int
    avg_doc_length: float
    has_positions: bool
    index_bytes: int
    uptime_s: float


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    num_docs: int = 0


class ErrorResponse(BaseModel):
    detail: str
    request_id: str | None = None
