"""Робота маршрутів без HTTP: звичайні синхронні функції, які однаково кличуть API, UI і бенчмарк.

run_search — CPU-bound (парсинг, BM25, сніпети), тому маршрут /search — це `def`: FastAPI виконує
його в пулі потоків і цикл подій вільний. Чому саме так — README, розділ L7.4.
"""

import math
import time

from fastapi import HTTPException

from findex.query import QueryParseError, parse
from findex.web.deps import SearchEngine
from findex.web.models import SearchRequest, SearchResponse, SearchResult


def run_search(engine: SearchEngine, req: SearchRequest) -> SearchResponse:
    t0 = time.perf_counter()
    try:
        page = engine.searcher(req.scorer).search_page(req.q, req.k, req.offset, snippets=req.snippets)
    except QueryParseError as e:
        raise HTTPException(status_code=422, detail=f"query syntax error: {e}") from e
    except ValueError as e:  # фраза в індексі без positions
        raise HTTPException(status_code=422, detail=str(e)) from e
    return SearchResponse(
        query=req.q,
        scorer=req.scorer,
        total=page.total,
        page=req.page,
        k=req.k,
        pages=math.ceil(page.total / req.k),
        took_ms=round((time.perf_counter() - t0) * 1000, 3),
        results=[SearchResult.from_internal(r) for r in page.results],
    )


def query_terms(q: str) -> list[str]:
    """Терми запиту для підсвітки в документі; битий запит — нічого не підсвічуємо."""
    try:
        return parse(q).terms() if q.strip() else []
    except QueryParseError:
        return []
