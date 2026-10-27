"""Lab 06: fetch, robots, канонізація, crawl. Без мережі: httpx.MockTransport (і локальне дзеркало для CLI)."""

import asyncio
import itertools
import json
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from findex.crawler import USER_AGENT
from findex.crawler.crawl import CrawlStats, Page, crawl
from findex.crawler.fetch import backoff_delay, fetch, make_client, parse_retry_after
from findex.crawler.robots import RobotsCache
from findex.crawler.urls import canonicalize, host_allowed, parse_html

type Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


def client_for(handler: Handler) -> httpx.AsyncClient:
    return make_client(timeout=5.0, transport=httpx.MockTransport(handler))


class FakeSleep:
    """Замість asyncio.sleep у fetch: записує паузи й не чекає — тести backoff миттєві."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def responses(*items: httpx.Response | Exception) -> Callable[[httpx.Request], httpx.Response]:
    """Обробник, що віддає відповіді по черзі (виняток — кидає), останню — назавжди."""
    queue = list(items)

    def handler(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return handler


async def do_fetch(handler: Handler, **kwargs: object) -> tuple[object, FakeSleep]:
    sleep = FakeSleep()
    async with client_for(handler) as client:
        result = await fetch(client, "http://site.test/a", sleep=sleep, rng=random.Random(0), **kwargs)  # type: ignore[arg-type]
    return result, sleep


# --- fetch ---------------------------------------------------------------------------------


def test_fetch_ok_and_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, html="<p>hi</p>")

    result, sleep = asyncio.run(do_fetch(handler))
    assert result.ok and result.is_html and result.attempts == 1 and result.text == "<p>hi</p>"  # type: ignore[attr-defined]
    assert sleep.delays == []
    assert seen == [USER_AGENT] and "+https://" in USER_AGENT  # чесний UA з контактом


def test_fetch_retries_5xx_with_backoff_then_succeeds() -> None:
    handler = responses(httpx.Response(503), httpx.Response(502), httpx.Response(200, text="ok"))
    result, sleep = asyncio.run(do_fetch(handler))
    assert result.ok and result.attempts == 3  # type: ignore[attr-defined]
    assert len(sleep.delays) == 2
    assert 0.5 <= sleep.delays[0] <= 1.0 and 1.0 <= sleep.delays[1] <= 1.5  # base·2^n + U(0, base)


def test_fetch_honors_retry_after() -> None:
    handler = responses(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200))
    result, sleep = asyncio.run(do_fetch(handler))
    assert result.ok and sleep.delays == [7.0]  # type: ignore[attr-defined]


def test_fetch_gives_up_when_retry_after_too_long() -> None:
    handler = responses(httpx.Response(429, headers={"Retry-After": "3600"}))
    result, sleep = asyncio.run(do_fetch(handler, max_retry_after=60.0))
    assert result.status == 429 and result.attempts == 1 and "Retry-After" in result.error  # type: ignore[attr-defined]
    assert sleep.delays == []


@pytest.mark.parametrize("status", [400, 403, 404, 410])
def test_fetch_never_retries_4xx(status: int) -> None:
    calls = Counter[str]()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status)

    result, sleep = asyncio.run(do_fetch(handler))
    assert result.error == f"HTTP {status}" and result.attempts == 1 and calls["n"] == 1  # type: ignore[attr-defined]
    assert sleep.delays == []


def test_fetch_gives_up_after_max_attempts() -> None:
    result, sleep = asyncio.run(do_fetch(responses(httpx.Response(500)), max_attempts=3))
    assert result.error == "HTTP 500" and result.attempts == 3 and len(sleep.delays) == 2  # type: ignore[attr-defined]


def test_fetch_timeout_is_retried() -> None:
    calls = Counter[str]()

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(5)  # asyncio.timeout(0.05) скасує це очікування
        return httpx.Response(200)

    result, _ = asyncio.run(do_fetch(handler, timeout=0.05))
    assert result.ok and result.attempts == 2  # type: ignore[attr-defined]


def test_fetch_connection_error_is_retried() -> None:
    handler = responses(httpx.ConnectError("refused"), httpx.Response(200))
    result, _ = asyncio.run(do_fetch(handler))
    assert result.ok and result.attempts == 2  # type: ignore[attr-defined]


def test_fetch_redirect_loop_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        other = "/b" if request.url.path == "/a" else "/a"
        return httpx.Response(302, headers={"Location": other})

    result, sleep = asyncio.run(do_fetch(handler))
    assert result.error and "redirect loop" in result.error and result.attempts == 1  # type: ignore[attr-defined]
    assert sleep.delays == []


def test_fetch_follows_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(301, headers={"Location": "/new"})
        return httpx.Response(200, html="new")

    result, _ = asyncio.run(do_fetch(handler))
    assert result.ok and result.final_url == "http://site.test/new"  # type: ignore[attr-defined]


def test_parse_retry_after() -> None:
    now = datetime(2026, 10, 21, 7, 28, 0, tzinfo=UTC)
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:30 GMT", now=now) == 30.0
    assert parse_retry_after("Wed, 21 Oct 2026 07:00:00 GMT", now=now) == 0.0  # у минулому -> 0
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None


@given(st.integers(0, 20), st.integers(0, 2**32))
def test_backoff_bounded_and_jittered(retry: int, seed: int) -> None:
    d = backoff_delay(retry, base=0.5, cap=10.0, rng=random.Random(seed))
    assert min(10.0, 0.5 * 2**retry) <= d <= min(10.0, 0.5 * 2**retry) + 0.5


# --- robots.txt ----------------------------------------------------------------------------

ROBOTS = "User-agent: *\nDisallow: /private/\nCrawl-delay: 2\n"


def robots_handler(status: int, body: str = "", counter: Counter[str] | None = None) -> Handler:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if counter is not None:
                counter[request.url.host] += 1
            await asyncio.sleep(0.01)  # щоб конкурентні allowed() справді перетнулись
            return httpx.Response(status, text=body)
        return httpx.Response(200, html="ok")

    return handler


def test_robots_rules_and_single_fetch_per_origin() -> None:
    counter = Counter[str]()

    async def go() -> list[bool]:
        async with client_for(robots_handler(200, ROBOTS, counter)) as client:
            robots = RobotsCache(client)
            urls = ["http://a.test/private/x", "http://a.test/public"] * 10
            verdicts = await asyncio.gather(*(robots.allowed(u) for u in urls))
            assert await robots.crawl_delay("http://a.test/") == 2.0
            assert robots.fetches == 1
            return verdicts

    verdicts = asyncio.run(go())
    assert verdicts == [False, True] * 10
    assert counter["a.test"] == 1  # 20 одночасних запитів — один GET robots.txt (Lock на origin)


@pytest.mark.parametrize(("status", "allowed"), [(404, True), (410, True), (500, False), (503, False)])
def test_robots_missing_or_broken(status: int, allowed: bool) -> None:
    async def go() -> bool:
        async with client_for(robots_handler(status)) as client:
            return await RobotsCache(client).allowed("http://a.test/page")

    assert asyncio.run(go()) is allowed


# --- URL і HTML ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTP://Example.COM/a/b/", "http://example.com/a/b"),
        ("http://example.com", "http://example.com/"),
        ("http://example.com:80/x#frag", "http://example.com/x"),
        ("https://example.com:443/", "https://example.com/"),
        ("http://example.com:8080/", "http://example.com:8080/"),
        ("http://example.com/s?b=2&a=1&a=0", "http://example.com/s?a=0&a=1&b=2"),
        ("http://user:pw@example.com/", "http://example.com/"),
        ("http://[::1]:8000/x/", "http://[::1]:8000/x"),
        ("ftp://example.com/", None),
        ("mailto:a@b.c", None),
        ("http://example.com:bad/", None),
        ("not a url", None),
    ],
)
def test_canonicalize(url: str, expected: str | None) -> None:
    assert canonicalize(url) == expected


@given(
    st.sampled_from(["http", "HTTPS"]),
    st.sampled_from(["a.test", "B.Test", "a.test:8080", "a.test:80"]),
    st.lists(st.sampled_from(["x", "Y", "", "z.html"]), max_size=4),
    st.lists(st.tuples(st.sampled_from("abc"), st.sampled_from(["1", "2", ""])), max_size=3),
    st.sampled_from(["", "#top", "/"]),
)
def test_canonicalize_idempotent(
    scheme: str, host: str, segs: list[str], query: list[tuple[str, str]], tail: str
) -> None:
    url = (
        f"{scheme}://{host}/{'/'.join(segs)}"
        + ("?" + "&".join(f"{k}={v}" for k, v in query) if query else "")
        + tail
    )
    once = canonicalize(url)
    assert once is not None and canonicalize(once) == once


def test_host_allowed() -> None:
    domains = frozenset({"example.com"})
    assert host_allowed("http://example.com/", domains)
    assert host_allowed("http://docs.example.com/", domains)
    assert not host_allowed("http://badexample.com/", domains)  # не суфікс-збіг рядка
    assert not host_allowed("http://example.org/", domains)


def test_parse_html() -> None:
    html = """<html><head><title> Pudge &amp; Co </title><script>var x = "hidden";</script>
    <style>p { color: red }</style></head><body><h1>Meat</h1><p>Hook &lt;3</p>
    <a href="/heroes/axe#top">Axe</a><a href="https://other.test/">Other</a><a href="/heroes/axe">dup</a>
    <a href="mailto:x@y.z">mail</a><a href="javascript:void(0)">js</a><a>no href</a></body></html>"""
    page = parse_html(html, "http://site.test/heroes/pudge")
    assert page.title == "Pudge & Co"
    assert page.text == "Meat Hook <3 Axe Other dup mail js no href"
    assert "hidden" not in page.text and "color" not in page.text
    assert page.links == ["http://site.test/heroes/axe", "https://other.test/"]


def test_parse_html_base_tag() -> None:
    page = parse_html('<base href="http://cdn.test/root/"><a href="x">x</a>', "http://site.test/a")
    assert page.links == ["http://cdn.test/root/x"]


# --- crawl ---------------------------------------------------------------------------------


def site(
    pages: dict[str, list[str]],
    *,
    robots: str = "",
    delay: float = 0.0,
    log: list[tuple[str, float]] | None = None,
) -> Handler:
    """Мок-сайт: шлях -> список посилань. Рахує одночасні запити на хост."""
    active = Counter[str]()
    peak = Counter[str]()

    async def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.netloc.decode(), request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots else httpx.Response(404)
        if log is not None:
            log.append((f"{host}{request.url.raw_path.decode()}", time.perf_counter()))
        active[host] += 1
        peak[host] = max(peak[host], active[host])
        try:
            await asyncio.sleep(delay)
            if path.startswith("/redirect-to/"):
                return httpx.Response(301, headers={"Location": "/" + path.removeprefix("/redirect-to/")})
            if path == "/file.pdf":
                return httpx.Response(200, content=b"%PDF", headers={"Content-Type": "application/pdf"})
            key = request.url.raw_path.decode()
            if key not in pages and path not in pages:
                return httpx.Response(404)
            links = "".join(f'<a href="{u}">{u}</a>' for u in pages.get(key, pages.get(path, [])))
            return httpx.Response(200, html=f"<title>{path}</title><p>text of {path}</p>{links}")
        finally:
            active[host] -= 1

    handler.peak = peak  # type: ignore[attr-defined]
    return handler


GRAPH = {
    "/": ["/a", "/b", "/a#dup", "/b/", "http://other.test/x", "/private/p", "/file.pdf", "/missing"],
    "/a": ["/", "/c", "/b?y=2&x=1"],
    "/b": ["/c", "/a"],
    "/c": ["/d"],
    "/d": [],
    "/b?x=1&y=2": [],
}


async def collect(handler: Handler, **kwargs: object) -> tuple[list[Page], CrawlStats]:
    stats = CrawlStats()
    async with client_for(handler) as client:
        gen = crawl(["http://site.test/"], client=client, stats=stats, **kwargs)  # type: ignore[arg-type]
        async with aclosing(gen):
            pages = [p async for p in gen]
    return pages, stats


def test_crawl_visits_each_page_once() -> None:
    log: list[tuple[str, float]] = []
    pages, stats = asyncio.run(
        collect(site(GRAPH, robots="User-agent: *\nDisallow: /private/", log=log), concurrency=4)
    )
    fetched = Counter(u for u, _ in log)
    assert all(n == 1 for n in fetched.values()), fetched  # жодного дубля (фрагмент, слеш, порядок query)
    urls = sorted(p.url for p in pages)
    assert urls == sorted(f"http://site.test{p}" for p in ["/", "/a", "/b", "/c", "/d", "/b?x=1&y=2"])
    assert "site.test/private/p" not in fetched  # robots.txt
    assert not any(u.startswith("other.test") for u in fetched)  # поза allowlist
    assert stats.skipped_robots == 1 and stats.skipped_offsite == 1
    assert stats.errors == 1  # /missing — 404; pdf — не HTML, але не помилка
    assert next(p for p in pages if p.url.endswith("/a")).title == "/a"


def test_crawl_respects_max_pages() -> None:
    graph = {f"/{i}": [f"/{j}" for j in range(i + 1, i + 6)] for i in range(100)} | {"/": ["/0"]}
    log: list[tuple[str, float]] = []
    pages, stats = asyncio.run(collect(site(graph, log=log), max_pages=17, concurrency=8))
    assert len(log) == 17 and stats.fetched == 17 and len(pages) == 17  # бюджет — запити, жодного зайвого


def test_crawl_per_host_and_global_limits() -> None:
    graph = {"/": [f"/p{i}" for i in range(30)]} | {f"/p{i}": [] for i in range(30)}
    handler = site(graph, delay=0.02)
    _, stats = asyncio.run(collect(handler, concurrency=10, per_host=3))
    assert handler.peak["site.test"] == 3  # type: ignore[attr-defined]
    assert stats.max_in_flight <= 3


def test_crawl_min_delay_between_requests() -> None:
    graph = {"/": ["/a", "/b", "/c"], "/a": [], "/b": [], "/c": []}
    log: list[tuple[str, float]] = []
    asyncio.run(collect(site(graph, log=log), concurrency=4, per_host=4, min_delay=0.05))
    starts = [t for _, t in log]
    # Допуск — крок годинника циклу: на Windows з Python 3.12 monotonic тікає по 15.6 мс (впіймав CI).
    tolerance = time.get_clock_info("monotonic").resolution + 0.005
    assert all(b - a >= 0.05 - tolerance for a, b in itertools.pairwise(starts))


def test_crawl_redirect_to_seen_page_is_not_duplicated() -> None:
    graph = {"/": ["/a", "/redirect-to/a"], "/a": []}
    pages, _ = asyncio.run(collect(site(graph), concurrency=1))
    assert sorted(p.url for p in pages) == ["http://site.test/", "http://site.test/a"]


def test_crawl_early_stop_leaves_no_tasks() -> None:
    """Споживач виходить після першої сторінки: aclose -> runner скасовано -> воркерів немає."""
    graph = {"/": [f"/p{i}" for i in range(50)]} | {f"/p{i}": [] for i in range(50)}

    async def go() -> int:
        async with client_for(site(graph, delay=0.05)) as client:
            gen = crawl(["http://site.test/"], client=client, concurrency=10, per_host=10)
            async with aclosing(gen):
                async for _ in gen:
                    break
        return len(asyncio.all_tasks() - {asyncio.current_task()})

    assert asyncio.run(go()) == 0


def test_crawl_bug_in_worker_surfaces() -> None:
    """Не мережева помилка, а баг (тут — у on_fetch) не губиться: ExceptionGroup у споживача."""

    def boom(_: object) -> None:
        raise RuntimeError("bug")

    with pytest.raises(ExceptionGroup) as info:
        asyncio.run(collect(site(GRAPH), on_fetch=boom))
    assert info.group_contains(RuntimeError, match="bug")


def test_pages_feed_findex_index(tmp_path: Path) -> None:
    from findex.index import build_index

    pages, _ = asyncio.run(collect(site(GRAPH)))
    corpus = tmp_path / "crawl.jsonl"
    corpus.write_text("\n".join(json.dumps(p.to_json()) for p in pages), encoding="utf-8")
    index = build_index(corpus)
    assert index.num_docs == len(pages) and "text" in index


def test_crawl_robots_skips_do_not_eat_delay_slots() -> None:
    """Регресія: заборонені robots.txt URL не повинні займати слот паузи (запиту ж немає)."""
    graph = {"/": [f"/private/{i}" for i in range(10)] + ["/a", "/b"], "/a": [], "/b": []}
    log: list[tuple[str, float]] = []
    t0 = time.perf_counter()
    asyncio.run(
        collect(site(graph, robots="User-agent: *\nDisallow: /private/", log=log), per_host=1, min_delay=0.2)
    )
    assert len(log) == 3  # /, /a, /b
    assert time.perf_counter() - t0 < 1.0  # 2 паузи (0.4 с), а не 12 (2.4 с)
