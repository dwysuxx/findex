"""Фабрика застосунку: lifespan, middleware, обробка помилок, маршрути.

    uvicorn findex.web:app                               # розробка (app — лінивий, див. __init__)
    findex serve --index index.bin --workers 4           # те саме через CLI: create_app у кожному воркері

Індекс вантажиться ОДИН раз — у lifespan, до першого запиту; закривається при SIGTERM (uvicorn
дочікує активні запити, потім виходить із lifespan, тож teardown виконується і при зупинці
контейнера).
"""

import logging
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from findex import __version__
from findex.index import guess_format, open_index
from findex.web import api, ui
from findex.web.logs import request_id_var
from findex.web.settings import Settings

log = logging.getLogger(__name__)
access_log = logging.getLogger("findex.web.access")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lab 03 open_index (контекстний менеджер), тільки тепер навколо всього життя сервера."""
    settings: Settings = app.state.settings
    t0 = time.perf_counter()
    with open_index(settings.index_path, guess_format(settings.index_path)) as index:
        app.state.index = index
        app.state.started_at = time.monotonic()
        log.info(
            "index loaded",
            extra={
                "path": str(settings.index_path),
                "docs": index.num_docs,
                "terms": len(index),
                "load_ms": round((time.perf_counter() - t0) * 1000, 1),
            },
        )
        app.state.semantic = app.state.embedder = None
        if settings.embeddings_path is not None:
            _load_semantic(app, settings)
        try:
            yield
        finally:
            engine = getattr(app.state, "engine", None)
            if engine is not None:
                engine.close()
            app.state.index = app.state.engine = None
            log.info("index closed")


def _load_semantic(app: FastAPI, settings: Settings) -> None:
    """Ембединги + модель — раз на процес, як і індекс. Модель — та, якою рахували ембединги."""
    from findex.semantic import FastEmbedder, SemanticIndex, check_corpus

    assert settings.embeddings_path is not None
    t0 = time.perf_counter()
    index = app.state.index
    sem = SemanticIndex.load(settings.embeddings_path)
    if sem.num_docs != index.num_docs:
        raise RuntimeError(
            f"{settings.embeddings_path}: {sem.num_docs} документів, а в індексі {index.num_docs}"
        )
    check_corpus(sem, settings.corpus_path or Path(index.doc_meta[0].path.rpartition("#")[0]))
    app.state.embedder = FastEmbedder(sem.model, cache_dir=settings.fastembed_cache_path)
    app.state.semantic = sem
    log.info(
        "embeddings loaded",
        extra={
            "model": sem.model,
            "chunks": len(sem.chunk_doc),
            "load_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    )


class RequestContextMiddleware:
    """Чистий ASGI-middleware: request id, рядок доступу з часом, і 500 для непередбачених винятків.

    Не BaseHTTPMiddleware: той загортає кожен запит в окрему задачу й потоки тіла, це помітно на
    бенчмарку. Тут — лише обгортка над send.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming[:64] if incoming.isprintable() and incoming else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        scope.setdefault("state", {})["request_id"] = rid
        status = 500
        started = False
        t0 = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status, started
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            # Сюди доходить лише те, що не перетворили на відповідь HTTPException/валідація.
            log.exception("unhandled error", extra={"method": scope["method"], "path": scope["path"]})
            if started:  # заголовки вже пішли — коректну 500 не надіслати, лише обірвати
                raise
            status = 500
            body = b'{"detail":"Internal Server Error","request_id":"' + rid.encode() + b'"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json"), (b"x-request-id", rid.encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
        finally:
            access_log.info(
                "%s %s %d",
                scope["method"],
                scope["path"],
                status,
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "query": scope.get("query_string", b"").decode("latin-1"),
                    "status": status,
                    "ms": round((time.perf_counter() - t0) * 1000, 2),
                },
            )
            request_id_var.reset(token)


def create_app(settings: Settings | None = None) -> FastAPI:
    """settings=None -> читаємо з оточення (і .env). Хибний INDEX_PATH -> ValidationError тут же."""
    settings = settings if settings is not None else Settings()  # pyright: ignore[reportCallIssue]
    app = FastAPI(
        title="findex",
        version=__version__,
        summary="Пошук по Dota 2 wiki: інвертований індекс, BM25/TF-IDF, мова запитів.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.started_at = time.monotonic()
    app.state.engine_lock = threading.Lock()

    app.include_router(api.router)
    app.include_router(ui.router)
    app.mount("/static", StaticFiles(directory=ui.STATIC_DIR), name="static")

    if settings.cors_origins:  # UI на тому ж origin CORS не потребує; лише для чужого фронтенду
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET"], allow_headers=["*"]
        )
    app.add_middleware(RequestContextMiddleware)  # додано останнім => зовнішній, бачить усе
    return app
