"""Веб-інтерфейс: Jinja2 на сервері + htmx для пошуку під час набору.

Без JavaScript усе працює звичайними GET-формами й посиланнями. З htmx (заголовок HX-Request) той
самий маршрут повертає лише фрагмент із результатами, і він підставляється в сторінку без
перезавантаження. Тож це одна логіка й один шаблон результатів, а не два фронтенди.
"""

import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from findex.web.deps import EngineDep
from findex.web.models import SearchRequest, SearchResponse
from findex.web.render import highlight_html, pretty_title, snippet_html
from findex.web.service import query_terms, run_search

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=TEMPLATES_DIR)  # autoescape увімкнено для .html
templates.env.filters["snippet"] = snippet_html
templates.env.filters["pretty"] = pretty_title

router = APIRouter(include_in_schema=False)  # HTML-сторінки не належать до схеми API


def _page_numbers(page: int, pages: int, around: int = 2) -> list[int | None]:
    """1 … 4 5 [6] 7 8 … 20 — None означає пропуск."""
    keep = {1, pages, *range(page - around, page + around + 1)}
    out: list[int | None] = []
    for n in range(1, pages + 1):
        if n in keep:
            out.append(n)
        elif out and out[-1] is not None:
            out.append(None)
    return out


def _wants_fragment(request: Request) -> bool:
    """htmx-запит -> лише фрагмент; відновлення історії htmx просить цілу сторінку."""
    h = request.headers
    return "hx-request" in h and "hx-history-restore-request" not in h


@router.get("/", response_class=HTMLResponse)
def home(request: Request, engine: EngineDep) -> HTMLResponse:
    """`def`: на непорожньому q тут той самий CPU-bound пошук, що й у /search."""
    raw = {k: v for k, v in request.query_params.items() if v != ""}
    q = raw.get("q", "").strip()
    ctx: dict[str, Any] = {
        "q": q,
        "scorer": raw.get("scorer", "bm25"),
        "mode": raw.get("mode", "keyword"),
        "semantic": engine.semantic is not None,
        "num_docs": engine.index.num_docs,
    }
    status = 200
    if q:
        try:
            # той самий SearchRequest, що й в API, але помилку показуємо на сторінці, а не 422 JSON
            req = SearchRequest.model_validate({**raw, "q": q})
        except ValidationError as e:
            ctx["error"] = "; ".join(
                f"{'.'.join(map(str, err['loc'])) or 'запит'}: {err['msg']}" for err in e.errors()
            )
            status = 422
        else:
            try:
                resp: SearchResponse = run_search(engine, req)
            except HTTPException as e:
                ctx["error"] = e.detail
                status = e.status_code
            else:
                ctx.update(resp=resp, req=req, page_numbers=_page_numbers(resp.page, resp.pages))
    name = "_results.html" if _wants_fragment(request) else "index.html"
    response = templates.TemplateResponse(request, name, ctx, status_code=status)
    response.headers["Vary"] = "HX-Request"  # одна URL — дві форми відповіді: кешам треба знати
    return response


@router.get("/doc/{doc_id}", response_class=HTMLResponse)
def doc_page(request: Request, doc_id: int, engine: EngineDep, q: str = "") -> HTMLResponse:
    meta = engine.index.doc_meta.get(doc_id)
    text = engine.text(doc_id) if meta is not None else None
    if meta is None or text is None:
        return templates.TemplateResponse(
            request, "404.html", {"what": f"документа {doc_id}"}, status_code=404
        )
    body, num_marks = highlight_html(text, query_terms(q))
    length = engine.index.doc_length(doc_id)
    ctx = {
        "q": q,
        "doc_id": doc_id,
        "title": meta.title,
        "length": length,
        "body": body,
        "num_marks": num_marks,
        "reading_min": math.ceil(length / 230),  # ~230 слів за хвилину
    }
    return templates.TemplateResponse(request, "doc.html", ctx)
