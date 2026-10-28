"""Lab 07: FastAPI-застосунок. Справжній файл індексу не потрібен — індекс підміняє
dependency_overrides[get_index]; lifespan перевіряється окремо на крихітному файлі з conftest."""

import logging
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from conftest import TINY_DOCS
from findex.engine import Searcher
from findex.index import Index, build_index
from findex.snippets import TextStore
from findex.web import create_app
from findex.web.deps import get_engine, get_index
from findex.web.render import highlight_html, pretty_title, snippet_html
from findex.web.settings import Settings


@pytest.fixture
def settings(tiny_index_file: Path) -> Settings:
    return Settings(index_path=tiny_index_file, _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def app(settings: Settings, tiny_index: Index) -> FastAPI:
    app = create_app(settings)
    app.dependency_overrides[get_index] = lambda: tiny_index
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)  # без `with`: lifespan не запускається, індекс — з override


# --- /search


def test_search_ok(client: TestClient) -> None:
    r = client.get("/search", params={"q": "python"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "python" and body["scorer"] == "bm25" and body["page"] == 1
    assert body["total"] == 4  # усі документи зі словом python
    scores = [x["score"] for x in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert set(body["results"][0]) == {"doc_id", "title", "score", "snippet"}
    assert "**python**" in body["results"][0]["snippet"].lower()
    assert body["took_ms"] >= 0


def test_search_pagination_matches_one_big_page(client: TestClient) -> None:
    big = client.get("/search", params={"q": "python", "k": 6}).json()["results"]
    pages = [client.get("/search", params={"q": "python", "k": 3, "page": p}).json() for p in (1, 2)]
    assert [r for p in pages for r in p["results"]] == big
    assert {p["pages"] for p in pages} == {2} and {p["total"] for p in pages} == {4}
    assert len(pages[1]["results"]) == 1  # остання сторінка неповна


def test_search_page_past_the_end_is_empty_not_error(client: TestClient) -> None:
    body = client.get("/search", params={"q": "python", "k": 10, "page": 5}).json()
    assert body["results"] == [] and body["total"] == 4


def test_search_no_matches(client: TestClient) -> None:
    body = client.get("/search", params={"q": "nosuchterm"}).json()
    assert body["total"] == 0 and body["pages"] == 0 and body["results"] == []


def test_search_tfidf_and_snippets_off(client: TestClient) -> None:
    body = client.get("/search", params={"q": "rareword", "scorer": "tfidf", "snippets": "false"}).json()
    assert body["scorer"] == "tfidf" and body["results"][0]["title"] == "rare"
    assert body["results"][0]["snippet"] == ""


@pytest.mark.parametrize(
    "params",
    [
        {},  # q обов'язковий
        {"q": ""},
        {"q": "x" * 201},
        {"q": "a", "k": "abc"},
        {"q": "a", "k": 0},
        {"q": "a", "k": 101},
        {"q": "a", "page": 0},
        {"q": "a", "k": 100, "page": 11},  # page * k > 1000
        {"q": "a", "scorer": "pagerank"},
        {"q": "a", "kk": 5},  # невідомий параметр — помилка, а не мовчазне ігнорування
    ],
)
def test_search_validation_422(client: TestClient, params: dict[str, object]) -> None:
    r = client.get("/search", params=params)
    assert r.status_code == 422
    assert "detail" in r.json()


def test_search_query_syntax_error_is_422_with_message(client: TestClient) -> None:
    r = client.get("/search", params={"q": "(python"})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("query syntax error")


def test_phrase_on_index_without_positions_is_422(app: FastAPI, tiny_corpus: Path) -> None:
    plain = build_index(tiny_corpus, positions=False)
    app.dependency_overrides[get_index] = lambda: plain
    r = TestClient(app).get("/search", params={"q": '"event loop"'})
    assert r.status_code == 422 and "positions" in r.json()["detail"]


# --- /docs/{id}, /stats, /health


def test_document_ok(client: TestClient, tiny_index: Index) -> None:
    doc_id = next(i for i, m in tiny_index.doc_meta.items() if m.title == "py-java")
    body = client.get(f"/docs/{doc_id}").json()
    assert body == {"doc_id": doc_id, "title": "py-java", "length": 2, "text": "python java"}


@pytest.mark.parametrize(("path", "status"), [("/docs/9999", 404), ("/docs/-1", 404), ("/docs/abc", 422)])
def test_document_errors(client: TestClient, path: str, status: int) -> None:
    r = client.get(path)
    assert r.status_code == status and "detail" in r.json()


def test_openapi_docs_page_still_works(client: TestClient) -> None:
    """/docs/{doc_id} не перекриває Swagger UI на /docs."""
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    params = {p["name"]: p for p in schema["paths"]["/search"]["get"]["parameters"]}
    assert set(params) == {"q", "k", "page", "scorer", "mode", "snippets"}
    assert params["k"]["schema"]["maximum"] == 100 and params["q"]["required"] is True
    assert "/" not in schema["paths"]  # HTML-сторінки не в схемі API


def test_stats(client: TestClient, tiny_index_file: Path) -> None:
    body = client.get("/stats").json()
    assert body["num_docs"] == len(TINY_DOCS) and body["vocabulary"] > 0 and body["has_positions"]
    assert body["index_bytes"] == tiny_index_file.stat().st_size
    assert body["uptime_s"] >= 0


def test_health_503_when_index_not_loaded(client: TestClient) -> None:
    """lifespan не запускався => індексу в app.state немає. Проба мусить сказати 503, а не впасти."""
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"


def test_routes_503_without_index(settings: Settings) -> None:
    r = TestClient(create_app(settings)).get("/search", params={"q": "python"})
    assert r.status_code == 503 and r.json() == {"detail": "index is not loaded"}


def test_lifespan_loads_index_once_and_closes_it(settings: Settings) -> None:
    app = create_app(settings)  # без override: справжній lifespan з файлу
    with TestClient(app) as c:
        index = app.state.index
        assert c.get("/health").json() == {"status": "ok", "num_docs": len(TINY_DOCS)}
        c.get("/search", params={"q": "python"})
        c.get("/search", params={"q": "java"})
        assert app.state.index is index  # той самий об'єкт — не перевантажується на запит
    assert index.closed and app.state.index is None
    assert TestClient(app).get("/health").status_code == 503


# --- помилки, request id, логи


def test_unexpected_exception_is_clean_500_with_request_id(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    def boom() -> None:
        raise RuntimeError("kaboom")

    app.dependency_overrides[get_engine] = boom
    with caplog.at_level(logging.ERROR, logger="findex.web.application"):
        r = TestClient(app).get("/search", params={"q": "python"}, headers={"X-Request-Id": "req-42"})
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal Server Error", "request_id": "req-42"}
    assert r.headers["x-request-id"] == "req-42"
    rec = next(rec for rec in caplog.records if rec.getMessage() == "unhandled error")
    assert rec.exc_info is not None and rec.__dict__["path"] == "/search"
    assert "kaboom" not in r.text  # деталі — у лог, не клієнтові


def test_request_id_generated_and_in_access_log(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="findex.web.access"):
        r = client.get("/stats")
    rid = r.headers["x-request-id"]
    assert len(rid) == 16
    rec = next(rec for rec in caplog.records if rec.name == "findex.web.access")
    assert rec.__dict__["status"] == 200 and rec.__dict__["path"] == "/stats"


def test_json_formatter_includes_request_id_and_extras() -> None:
    import json

    from findex.web.logs import JsonFormatter, RequestIdFilter, request_id_var

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.status = 200
    token = request_id_var.set("abc")
    try:
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)
    out = json.loads(JsonFormatter().format(record))
    assert out["msg"] == "hello world" and out["request_id"] == "abc" and out["status"] == 200


# --- UI


def test_ui_landing(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert '<form class="search"' in r.text and "Спробуйте" in r.text
    assert r.headers["vary"] == "HX-Request"


def test_ui_results_full_page_and_htmx_fragment(client: TestClient) -> None:
    full = client.get("/", params={"q": "python", "k": 2})
    assert full.status_code == 200 and "<html" in full.text
    assert "<mark>python</mark>" in full.text and 'aria-current="page"' in full.text
    frag = client.get("/", params={"q": "python", "k": 2}, headers={"HX-Request": "true"})
    assert "<html" not in frag.text and '<ol class="hits"' in frag.text
    restore = client.get(
        "/", params={"q": "python"}, headers={"HX-Request": "true", "HX-History-Restore-Request": "true"}
    )
    assert "<html" in restore.text  # відновлення історії htmx — ціла сторінка


def test_ui_errors_are_rendered_not_json(client: TestClient) -> None:
    bad_k = client.get("/", params={"q": "python", "k": "abc"})
    assert bad_k.status_code == 422 and 'class="error"' in bad_k.text and "k:" in bad_k.text
    bad_q = client.get("/", params={"q": "(python"})
    assert bad_q.status_code == 422 and "query syntax error" in bad_q.text


def test_ui_doc_page(client: TestClient, tiny_index: Index) -> None:
    doc_id = next(i for i, m in tiny_index.doc_meta.items() if m.title == "py-async")
    r = client.get(f"/doc/{doc_id}", params={"q": "python"})
    assert r.status_code == 200 and r.text.count("<mark>python</mark>") == 2
    assert client.get("/doc/9999").status_code == 404


def test_static_css_served(client: TestClient) -> None:
    r = client.get("/static/style.css")
    assert r.status_code == 200 and "text/css" in r.headers["content-type"]


def test_render_escapes_before_marking() -> None:
    assert snippet_html("<b>**x**</b> & y") == "&lt;b&gt;<mark>x</mark>&lt;/b&gt; &amp; y"
    body, n = highlight_html("Python <script>python</script>", ["python"])
    assert n == 2 and "<script>" not in body and body.count("<mark>") == 2
    assert highlight_html("<i>", []) == ("&lt;i&gt;", 0)
    assert pretty_title("anti_mage") == "Anti Mage"


# --- налаштування


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch, tiny_index_file: Path) -> None:
    monkeypatch.setenv("INDEX_PATH", str(tiny_index_file))
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.index_path == tiny_index_file and s.port == 9000
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_settings_missing_index_fails_at_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INDEX_PATH", str(tmp_path / "nope.bin"))
    with pytest.raises(ValidationError, match="index_path"):
        create_app()  # ще до сервера і сокета


def test_cors_only_when_configured(tiny_index_file: Path, tiny_index: Index) -> None:
    preflight = {"Origin": "https://a.example", "Access-Control-Request-Method": "GET"}
    s = Settings(index_path=tiny_index_file, cors_origins=["https://a.example"], _env_file=None)  # type: ignore[call-arg]
    r = TestClient(create_app(s)).options("/search", headers=preflight)
    assert r.headers["access-control-allow-origin"] == "https://a.example"
    s2 = Settings(index_path=tiny_index_file, _env_file=None)  # type: ignore[call-arg]
    r2 = TestClient(create_app(s2)).options("/search", headers=preflight)
    assert "access-control-allow-origin" not in r2.headers


def test_lazy_module_app(monkeypatch: pytest.MonkeyPatch, tiny_index_file: Path) -> None:
    import findex.web

    monkeypatch.setenv("INDEX_PATH", str(tiny_index_file))
    findex.web._default_app.cache_clear()
    try:
        assert isinstance(findex.web.app, FastAPI) and findex.web.app is findex.web.app
    finally:
        findex.web._default_app.cache_clear()
    with pytest.raises(AttributeError):
        _ = findex.web.nope


# --- ядро: пагінація і потоки


def test_search_page_offsets(tiny_index: Index) -> None:
    s = Searcher(tiny_index)
    whole = s.search("python", k=5, snippets=False)
    page = s.search_page("python", k=2, offset=2, snippets=False)
    assert page.total == 4 and list(page.results) == list(whole[2:4])


def test_text_store_is_thread_safe(tiny_corpus: Path) -> None:
    """Без замка в TextStore.get потоки перехоплюють seek одне одного й читають чужі рядки."""
    store = TextStore(tiny_corpus)
    errors: list[str] = []

    def worker(doc_id: str) -> None:
        for _ in range(200):
            if store.get(doc_id) != TINY_DOCS[doc_id]:
                errors.append(doc_id)

    threads = [threading.Thread(target=worker, args=(d,)) for d in TINY_DOCS for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.close()
    assert errors == []


def test_concurrent_requests_through_thread_pool(client: TestClient) -> None:
    """def-маршрути виконуються в пулі потоків: паралельні запити дають ті самі відповіді."""
    from concurrent.futures import ThreadPoolExecutor

    expected = client.get("/search", params={"q": "python", "k": 3}).json()["results"]

    def call(_: int) -> list[object]:
        return client.get("/search", params={"q": "python", "k": 3}).json()["results"]

    with ThreadPoolExecutor(8) as pool:
        assert all(r == expected for r in pool.map(call, range(40)))


# --- findex serve


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, object]]:
    """Підміняє uvicorn.run; serve пише INDEX_PATH в os.environ — відновлюємо оточення після тесту."""
    import uvicorn

    calls: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
    saved = os.environ.copy()
    for var in ("INDEX_PATH", "CORPUS_PATH", "PORT", "HOST"):
        os.environ.pop(var, None)
    try:
        yield calls
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_serve_passes_settings_to_uvicorn(uvicorn_calls: dict[str, object], tiny_index_file: Path) -> None:
    from typer.testing import CliRunner

    from findex.cli import app as cli

    r = CliRunner().invoke(
        cli, ["serve", "--index", str(tiny_index_file), "--workers", "3", "--port", "9001"]
    )
    assert r.exit_code == 0, r.output
    assert uvicorn_calls["app"] == "findex.web:create_app" and uvicorn_calls["factory"] is True
    assert uvicorn_calls["workers"] == 3 and uvicorn_calls["port"] == 9001
    assert uvicorn_calls["host"] == "127.0.0.1" and uvicorn_calls["access_log"] is False
    assert os.environ["INDEX_PATH"] == str(tiny_index_file)  # воркери читають його з оточення


def test_serve_bad_index_fails_before_uvicorn(uvicorn_calls: dict[str, object], tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from findex.cli import app as cli

    r = CliRunner().invoke(cli, ["serve", "--index", str(tmp_path / "missing.bin")])
    assert r.exit_code == 1 and "INDEX_PATH" in r.output
    assert uvicorn_calls == {}
