"""M1: robots.txt — по одному запиту на origin, далі з кешу.

Правила відсутнього/битого robots.txt — як у Google:
    2xx       -> розібрати файл;
    4xx       -> обмежень немає (файлу немає);
    5xx, мережа, таймаут -> не краулити цей origin (сервер «не в настрої» — не навантажуємо).
"""

import asyncio
from urllib.robotparser import RobotFileParser

import httpx

from findex.crawler import ROBOTS_AGENT
from findex.crawler.fetch import fetch
from findex.crawler.urls import origin


class RobotsCache:
    def __init__(
        self, client: httpx.AsyncClient, *, agent: str = ROBOTS_AGENT, timeout: float = 10.0
    ) -> None:
        self.client = client
        self.agent = agent
        self.timeout = timeout
        self._parsers: dict[str, RobotFileParser] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.fetches = 0  # скільки разів реально ходили по robots.txt (тест кешу)

    async def _parser(self, url: str) -> RobotFileParser:
        key = origin(url)
        if (cached := self._parsers.get(key)) is not None:
            return cached
        # Lock на origin: 10 воркерів, що одночасно прийшли на новий хост, роблять ОДИН запит.
        # Між перевіркою кешу й записом є await, тож без лока була б класична гонка — навіть в asyncio.
        async with self._locks.setdefault(key, asyncio.Lock()):
            if (cached := self._parsers.get(key)) is not None:
                return cached
            self.fetches += 1
            result = await fetch(self.client, f"{key}/robots.txt", timeout=self.timeout, max_attempts=2)
            parser = RobotFileParser(f"{key}/robots.txt")
            if result.ok:
                parser.parse(result.text.splitlines())
            elif result.status is not None and 400 <= result.status < 500:
                parser.parse([])  # порожні правила = дозволено все
            else:
                parser.parse(["User-agent: *", "Disallow: /"])
            self._parsers[key] = parser
            return parser

    async def allowed(self, url: str) -> bool:
        return (await self._parser(url)).can_fetch(self.agent, url)

    async def crawl_delay(self, url: str) -> float | None:
        delay = (await self._parser(url)).crawl_delay(self.agent)
        return float(delay) if delay is not None else None
