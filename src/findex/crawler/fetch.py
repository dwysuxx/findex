"""M1: один ввічливий запит.

Кожна спроба — під asyncio.timeout. Повторюємо лише те, що може минути саме: 429, 5xx,
таймаут, обрив з'єднання. Затримка — експоненційна з jitter (щоб сотні клієнтів, які впали
одночасно, не повернулись теж одночасно), а для 429 — рівно стільки, скільки просить
сервер у Retry-After. 404 і решта 4xx не повторюються: відповідь не зміниться.
"""

import asyncio
import importlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from findex.crawler import USER_AGENT

log = logging.getLogger(__name__)

type Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Підсумок по одному URL — рядок crawl.log і основа для відтворюваності прогону."""

    url: str
    final_url: str  # після редиректів
    status: int | None  # None — відповіді не було (таймаут, обрив)
    content_type: str
    text: str
    nbytes: int
    elapsed: float  # з усіма спробами й паузами між ними
    attempts: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    @property
    def is_html(self) -> bool:
        return self.content_type.split(";")[0].strip().lower() in ("text/html", "application/xhtml+xml")


def make_client(timeout: float = 10.0, max_connections: int = 100, **kwargs: object) -> httpx.AsyncClient:
    """Один клієнт на весь прогін: пул з'єднань (keep-alive) — велика частина швидкості async.

    Блокує (SSL-контекст, імпорти), тому crawl() викликає її через asyncio.to_thread.
    """
    # httpcore імпортує anyio і його asyncio-бекенд (~30 модулів) ліниво — на ПЕРШОМУ мережевому
    # запиті, тобто всередині циклу подій. З холодним кешем .pyc це блокувало цикл на 0.17-0.26 с
    # (знайшов asyncio debug=True у бенчмарку). Імпортуємо заздалегідь — тут, поза циклом.
    for module in ("httpcore._backends.anyio", "anyio._backends._asyncio"):
        importlib.import_module(module)
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        max_redirects=5,  # цикл редиректів -> TooManyRedirects, а не нескінченність
        timeout=httpx.Timeout(timeout),
        limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections),
        **kwargs,  # type: ignore[arg-type]  # transport=MockTransport у тестах
    )


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After: секунди ("120") або HTTP-дата ("Wed, 21 Oct 2026 07:28:00 GMT")."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def backoff_delay(retry: int, base: float, cap: float, rng: random.Random) -> float:
    """retry = 0, 1, 2...: min(cap, base·2^retry) + jitter U(0, base)."""
    return min(cap, base * 2**retry) + rng.uniform(0, base)


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 10.0,
    max_attempts: int = 3,
    backoff_base: float = 0.5,
    backoff_cap: float = 10.0,
    max_retry_after: float = 60.0,
    sleep: Sleep = asyncio.sleep,
    rng: random.Random | None = None,
) -> FetchResult:
    """GET із повторами. Ніколи не кидає на мережевих/HTTP-помилках — повертає FetchResult з error.

    CancelledError не перехоплюється: зупинка краулу мусить проходити крізь fetch вільно.
    """
    rng = rng or random.Random()
    t0 = time.perf_counter()

    def result(attempts: int, error: str | None, resp: httpx.Response | None = None) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=str(resp.url) if resp is not None else url,
            status=resp.status_code if resp is not None else None,
            content_type=resp.headers.get("content-type", "") if resp is not None else "",
            text=resp.text if resp is not None and error is None else "",
            nbytes=len(resp.content) if resp is not None else 0,
            elapsed=time.perf_counter() - t0,
            attempts=attempts,
            error=error,
        )

    for attempt in range(1, max_attempts + 1):
        resp: httpx.Response | None = None
        retry_after: float | None = None
        try:
            async with asyncio.timeout(timeout):
                resp = await client.get(url)
        except TimeoutError:
            error, retryable = f"timeout after {timeout:g}s", True
        except httpx.TooManyRedirects:
            return result(attempt, "redirect loop (too many redirects)")
        except httpx.TransportError as e:  # обрив, відмова з'єднання, таймаут самого httpx
            error, retryable = f"{type(e).__name__}: {e}", True
        else:
            status = resp.status_code
            if status < 400:
                return result(attempt, None, resp)
            error = f"HTTP {status}"
            retryable = status == 429 or status >= 500
            if status == 429:
                retry_after = parse_retry_after(resp.headers.get("retry-after"))

        if not retryable or attempt == max_attempts:
            return result(attempt, error, resp)
        if retry_after is not None and retry_after > max_retry_after:
            return result(attempt, f"{error}, Retry-After {retry_after:g}s > {max_retry_after:g}s", resp)
        delay = (
            retry_after
            if retry_after is not None
            else backoff_delay(attempt - 1, backoff_base, backoff_cap, rng)
        )
        why = f"Retry-After {retry_after:g}s" if retry_after is not None else "backoff"
        log.warning("retry %d/%d in %.2fs (%s, %s): %s", attempt + 1, max_attempts, delay, error, why, url)
        await sleep(delay)

    raise AssertionError("unreachable")  # pragma: no cover  # цикл завжди повертає на останній спробі
