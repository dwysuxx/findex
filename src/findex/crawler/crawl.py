"""M2: frontier і воркери.

    seeds ─► черги URL по хостах ─► ready: asyncio.Queue[_Host] ─► N воркерів у TaskGroup ─► out ─► async for page
                  ▲                    («токени» хостів, які                    │
                  │                      можна запитувати ЗАРАЗ)                │
                  └────────────────────── нові посилання ◄──────────────────────┘

Чому не одна черга URL. Перша версія мала одну FIFO-чергу: воркер брав URL, а якщо його хост
зайнятий (per_host) чи ще не минула пауза — чекав на семафорі, тримаючи URL. URL вільних хостів
стояли за ним у черзі: head-of-line blocking. На бенчмарку з паузою 0.25 с краул тривав 30 с
при теоретичних ≈ 10 с. Тепер — як у класичному Mercator-фронтирі: у кожного хоста своя черга
URL, а спільна asyncio.Queue містить «токени» хостів, готових до запиту. Хост має per_host
токенів; токен потрапляє в чергу, лише коли в хоста є URL, і не раніше, ніж мине пауза від
попереднього старту (call_at). Воркер бере не URL, а хост, — і ніколи не чекає на зайнятий.

Структура життя задач:
    crawl() (async-генератор)
      └─ runner-задача: TaskGroup
           ├─ worker × concurrency   (беруть хост, fetch його наступного URL, parse, кладуть посилання)
           └─ чекає all_done (немає ні URL у чергах, ні в обробці), потім скасовує воркерів
Генератор лише читає `out`. Якщо споживач зупинився раніше (break, Ctrl-C), finally генератора
скасовує runner — TaskGroup скасовує воркерів — httpx-клієнт закривається. Нічого не висить.
Споживач, що виходить раніше, має закрити генератор: `async with aclosing(crawl(...)) as pages:`.

Між двома await код виконується без перемикань, тому `seen`, черги хостів, лічильники й бюджет —
звичайні змінні без локів. Лок потрібен лише там, де «перевірив — await — записав» (RobotsCache).
"""

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from findex.crawler.fetch import FetchResult, fetch, make_client
from findex.crawler.robots import RobotsCache
from findex.crawler.urls import ParsedPage, canonicalize, host_allowed, origin, parse_html

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Page:
    url: str
    title: str
    text: str
    fetched_at: str  # ISO 8601, UTC

    def to_json(self) -> dict[str, str]:
        """Рядок jsonl, який читає iter_documents (Lab 01): потрібні id і text."""
        return {
            "id": self.url,
            "title": self.title,
            "url": self.url,
            "text": self.text,
            "retrieved_at": self.fetched_at,
        }


@dataclass(slots=True)
class CrawlStats:
    """Живі лічильники: їх читає rich-дисплей CLI (з іншого потоку — лише читання int)."""

    started: float = field(default_factory=time.perf_counter)
    fetched: int = 0  # запитів сторінок завершено (будь-який результат)
    pages: int = 0  # Page віддано споживачу
    errors: int = 0
    retries: int = 0
    in_flight: int = 0
    max_in_flight: int = 0
    queued: int = 0
    skipped_robots: int = 0
    skipped_offsite: int = 0
    bytes: int = 0
    max_in_flight_per_host: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    @property
    def pages_per_s(self) -> float:
        return self.pages / self.elapsed if self.elapsed > 0 else 0.0


class _Host:
    """Ввічливість щодо одного origin: своя черга URL, per_host токенів і пауза між стартами."""

    def __init__(self, key: str, per_host: int, delay: float) -> None:
        self.key = key
        self.urls: deque[str] = deque()
        self.sem = asyncio.Semaphore(per_host)  # жорстка межа; токени гарантують, що на ній не чекають
        self.tokens = 0  # токенів «на руках»: у черзі ready, у таймері або у воркера
        self.waiting = 0  # з них ще не взятих воркером
        self.delay = delay
        self.next_start = 0.0
        self.last_start = -math.inf  # фактичний старт останнього запиту (loop.time())
        self.in_flight = 0


type OnFetch = Callable[[FetchResult], None]
_DONE = None


async def crawl(
    seeds: Iterable[str],
    *,
    max_pages: int = 500,
    concurrency: int = 10,
    per_host: int = 2,
    min_delay: float = 0.0,
    allowed_domains: Iterable[str] | None = None,
    timeout: float = 10.0,
    client: httpx.AsyncClient | None = None,
    stats: CrawlStats | None = None,
    on_fetch: OnFetch | None = None,
) -> AsyncGenerator[Page]:
    """Async-генератор сторінок: віддає Page, щойно сторінку завантажено й розібрано.

    max_pages — бюджет запитів сторінок (будь-який результат, без robots.txt). allowed_domains
    за замовчуванням — хости seed-ів. min_delay — мінімум між стартами запитів до одного origin
    (Crawl-delay з robots.txt, якщо більший, перемагає).
    """
    seeds = [c for s in seeds if (c := canonicalize(s))]
    domains = frozenset(d.lower() for d in allowed_domains) if allowed_domains else frozenset(
        (httpx.URL(s).host or "").lower() for s in seeds
    )  # fmt: skip
    stats = stats if stats is not None else CrawlStats()
    own_client = client is None
    # AsyncClient() синхронно будує SSL-контекст (читає сертифікати certifi) — ~0.17 с блокування
    # циклу; знайшов asyncio debug=True. Тому — в потоці.
    client = client or await asyncio.to_thread(make_client, timeout, concurrency)
    robots = RobotsCache(client, timeout=timeout)
    loop = asyncio.get_running_loop()
    ready: asyncio.Queue[_Host] = asyncio.Queue()
    out: asyncio.Queue[Page | None] = asyncio.Queue()
    in_flight_global = asyncio.Semaphore(concurrency)
    hosts: dict[str, _Host] = {}
    seen: set[str] = set()
    budget_used = 0
    outstanding = 0  # URL у чергах хостів + в обробці; 0 -> краул завершено
    all_done = asyncio.Event()

    def offer(host: _Host) -> None:
        """Видати хосту токени: поки є URL без токена і не вичерпано per_host."""
        while host.tokens < per_host and len(host.urls) > host.waiting:
            host.tokens += 1
            host.waiting += 1
            start = max(loop.time(), host.next_start)
            # Резервуємо слот: старти рознесені на delay. Це план — таймер може спрацювати пізніше
            # (Windows будить цикл кроками ~15.6 мс), тому точну паузу тримає перевірка у visit.
            host.next_start = start + host.delay
            if start <= loop.time():
                ready.put_nowait(host)
            else:
                loop.call_at(start, ready.put_nowait, host)

    def enqueue(url: str) -> None:
        nonlocal outstanding
        if budget_used >= max_pages or url in seen:
            return
        seen.add(url)
        if not host_allowed(url, domains):
            stats.skipped_offsite += 1
            return
        key = origin(url)
        host = hosts.get(key) or hosts.setdefault(key, _Host(key, per_host, min_delay))
        host.urls.append(url)
        outstanding += 1
        stats.queued += 1
        offer(host)

    async def admit(url: str, host: _Host) -> bool:
        """Чи робити запит: бюджет, robots.txt. True — бюджет уже зарезервовано під цей URL."""
        nonlocal budget_used
        if budget_used >= max_pages:
            return False  # бюджет вичерпано: решту черг просто «доїдаємо» без запитів
        if not await robots.allowed(url):
            stats.skipped_robots += 1
            log.info("robots.txt disallows %s", url)
            return False
        # Crawl-delay з robots.txt діє з наступних стартів (перший запит хоста його ще не знав).
        host.delay = max(min_delay, await robots.crawl_delay(url) or 0.0)
        if budget_used >= max_pages:  # ще раз: поки чекали robots, інші могли забрати бюджет
            return False
        budget_used += 1  # резерв без await між перевіркою і записом — атомарно для event loop
        return True

    async def visit(url: str, host: _Host) -> None:
        """Запит (бюджет уже зарезервовано в admit), журнал, розбір HTML, нові посилання."""
        async with host.sem, in_flight_global:
            stats.in_flight += 1
            host.in_flight += 1
            stats.max_in_flight = max(stats.max_in_flight, stats.in_flight)
            stats.max_in_flight_per_host[host.key] = max(
                stats.max_in_flight_per_host.get(host.key, 0), host.in_flight
            )
            try:
                # Пауза — від фактичного попереднього старту, а не від запланованого: якщо таймер слота
                # A спрацював на 11 мс пізніше, B інакше стартував би через 39 мс після A (впіймав CI).
                # Після sleep перевіряємо знову: інший воркер цього хоста міг стартувати першим.
                while (wait := host.last_start + host.delay - loop.time()) > 0:
                    await asyncio.sleep(wait)
                host.last_start = loop.time()  # без await між перевіркою і записом
                result = await fetch(client, url, timeout=timeout)
            finally:
                stats.in_flight -= 1
                host.in_flight -= 1

        stats.fetched += 1
        stats.retries += result.attempts - 1
        stats.bytes += result.nbytes
        if result.error:
            stats.errors += 1
            log.warning(
                "FAIL %s after %d attempt(s), %.3fs: %s", result.error, result.attempts, result.elapsed, url
            )
        else:
            log.info(
                "%s %dB %.3fs attempts=%d %s",
                result.status,
                result.nbytes,
                result.elapsed,
                result.attempts,
                url,
            )
        if on_fetch:
            on_fetch(result)
        if not (result.ok and result.is_html):
            return

        final = canonicalize(result.final_url) or url
        if final != url:
            if final in seen:  # редирект привів на вже відому сторінку — не дублюємо
                return
            seen.add(final)
        # Розбір HTML — CPU: в окремому потоці, щоб цикл подій тим часом обслуговував інші запити.
        parsed: ParsedPage = await asyncio.to_thread(parse_html, result.text, final)
        for link in parsed.links:
            enqueue(link)
        stats.pages += 1
        out.put_nowait(
            Page(final, parsed.title or final, parsed.text, datetime.now(UTC).isoformat(timespec="seconds"))
        )

    async def worker() -> None:
        nonlocal outstanding
        while True:
            host = await ready.get()
            host.waiting -= 1
            try:
                # Токен несе зарезервований слот паузи — витрачаємо його на перший URL хоста, що справді
                # потребує запиту. URL, заборонені robots.txt чи поза бюджетом, пропускаємо дорогою:
                # інакше кожен /private/... «з'їдав» би delay без жодного запиту.
                while host.urls:
                    url = host.urls.popleft()
                    stats.queued -= 1
                    try:
                        if await admit(url, host):
                            await visit(url, host)
                            break
                    finally:
                        outstanding -= 1
            finally:
                host.tokens -= 1
                offer(host)  # токен повертається хосту, якщо в нього ще є URL
                if outstanding == 0:
                    all_done.set()

    async def run() -> None:
        try:
            for s in seeds:
                enqueue(s)
            if outstanding == 0:
                all_done.set()
            async with asyncio.TaskGroup() as tg:
                workers = [tg.create_task(worker(), name=f"crawl-worker-{i}") for i in range(concurrency)]
                await all_done.wait()  # кожен URL оброблено або пропущено, нових немає
                for w in workers:
                    w.cancel()  # вони чекають на порожній черзі; скасування — штатна зупинка
        finally:
            out.put_nowait(_DONE)

    runner = asyncio.create_task(run(), name="crawl-runner")
    try:
        while (page := await out.get()) is not None:  # None — _DONE
            yield page
        await runner  # якщо run() упав — виняток (ExceptionGroup) вилітає тут, у споживача
    finally:
        if not runner.done():
            runner.cancel()
            # wait, а не `await runner` під suppress(CancelledError): той проковтнув би й
            # скасування самого генератора ззовні (Ctrl-C), а wait його пропускає.
            await asyncio.wait({runner})
        if not runner.cancelled():
            runner.exception()  # позначити виняток «отриманим», якщо споживач пішов раніше
        if own_client:
            await client.aclose()
