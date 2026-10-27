"""JSON API: /search, /docs/{doc_id}, /stats, /health. Схеми — у models.py, OpenAPI — на /docs."""

import time
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from findex import __version__
from findex.web.deps import EngineDep, IndexDep, SettingsDep
from findex.web.models import (
    DocumentResponse,
    ErrorResponse,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    StatsResponse,
)
from findex.web.service import run_search

router = APIRouter(tags=["api"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Невалідні параметри або синтаксис запиту"},
    503: {"model": ErrorResponse, "description": "Індекс не завантажено"},
}


@router.get("/search", response_model=SearchResponse, responses=_ERRORS)
def search(params: Annotated[SearchRequest, Query()], engine: EngineDep) -> SearchResponse:
    """Ранжований пошук із пагінацією.

    `def`, а не `async def`: робота CPU-bound і нічого не чекає, тож FastAPI виконує її в пулі
    потоків, а цикл подій тим часом приймає інші запити (/health не стоїть у черзі за BM25).
    """
    return run_search(engine, params)


@router.get(
    "/docs/{doc_id}",
    response_model=DocumentResponse,
    responses={404: {"model": ErrorResponse, "description": "Немає такого документа"}},
)
def document(doc_id: int, engine: EngineDep) -> DocumentResponse:
    """Повний текст документа (читається з jsonl за зміщенням — тому теж `def`)."""
    meta = engine.index.doc_meta.get(doc_id)
    text = engine.text(doc_id) if meta is not None else None
    if meta is None or text is None:
        raise HTTPException(status_code=404, detail=f"document {doc_id} not found")
    return DocumentResponse(
        doc_id=doc_id, title=meta.title, length=engine.index.doc_length(doc_id), text=text
    )


@router.get("/stats", response_model=StatsResponse, responses=_ERRORS)
async def stats(request: Request, index: IndexDep, settings: SettingsDep) -> StatsResponse:
    """Розмір індексу і аптайм. Усе — O(кількість документів), без обходу постінгів."""
    return StatsResponse(
        version=__version__,
        num_docs=index.num_docs,
        vocabulary=len(index),
        total_tokens=sum(index.doc_lengths.values()),
        avg_doc_length=round(index.avg_doc_length, 2),
        has_positions=index.has_positions,
        index_bytes=settings.index_path.stat().st_size,
        uptime_s=round(time.monotonic() - request.app.state.started_at, 3),
    )


@router.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
async def health(request: Request) -> HealthResponse | JSONResponse:
    """Проба для платформи: 200, лише коли індекс завантажено і в ньому є документи.

    Навмисно без get_index: проба не має падати винятком — вона має відповісти 503.
    """
    index = getattr(request.app.state, "index", None)
    if index is None or index.closed or index.num_docs == 0:
        return JSONResponse(HealthResponse(status="unavailable").model_dump(), status_code=503)
    return HealthResponse(status="ok", num_docs=index.num_docs)
