"""Lab 06 «Prove it to yourself» + один експеримент зверху (потоки проти задач).

python -m findex.crawler.experiments             # усі
python -m findex.crawler.experiments 2 3         # вибрані: 1 2 3 4 5 6
"""

import asyncio
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx

from findex.crawler.mirror import MirrorConfig, load_articles, serve
from findex.utils import peak_rss_bytes


async def one() -> int:
    return 1


def gen_one():
    yield 1


def exp1() -> None:
    print("1. Виклик корутинної функції нічого не виконує — як і виклик генераторної")
    coro = one()
    print(f"  one()            -> {coro!r}")
    coro.close()  # інакше RuntimeWarning: coroutine 'one' was never awaited
    print(f"  gen_one()        -> {gen_one()!r}")
    print(f"  asyncio.run(one()) -> {asyncio.run(one())}")
    print(f"  next(gen_one())  -> {next(gen_one())}")


def _timed(label: str, fn: Callable[[], object]) -> None:
    t0 = time.perf_counter()
    fn()
    print(f"  {label:<34} {time.perf_counter() - t0:5.2f} s")


async def nap() -> None:
    await asyncio.sleep(1)


async def blocking_nap() -> None:
    time.sleep(1)


def exp2() -> None:
    print("2. Три корутини по 1 с")

    async def sequential() -> None:
        await nap()
        await nap()
        await nap()

    async def group(fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
        async with asyncio.TaskGroup() as tg:
            for _ in range(3):
                tg.create_task(fn())

    _timed("await послідовно", lambda: asyncio.run(sequential()))
    _timed("TaskGroup + asyncio.sleep", lambda: asyncio.run(group(nap)))
    _timed("TaskGroup + time.sleep", lambda: asyncio.run(group(blocking_nap)))


def exp3() -> None:
    print("3. TaskGroup: одна задача падає через 0.1 с, інша спить 10 с")

    async def fails() -> None:
        await asyncio.sleep(0.1)
        raise ValueError("boom")

    async def sleeper() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            print("  sleeper: отримав CancelledError — група скасувала сусіда")
            raise

    async def main() -> None:
        t0 = time.perf_counter()
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(fails())
                tg.create_task(sleeper())
        except* ValueError as eg:
            print(
                f"  except* ValueError: {type(eg).__name__}{eg.exceptions!r} через {time.perf_counter() - t0:.2f} s"
            )

    asyncio.run(main())


def exp4() -> None:
    print("4. 30 URL з дзеркала (затримка 20 мс): новий клієнт на запит проти одного спільного")

    async def per_request(urls: list[str]) -> None:
        async def get(u: str) -> None:
            async with httpx.AsyncClient() as c:  # новий пул, нове з'єднання, новий SSL-контекст
                (await c.get(u)).raise_for_status()

        async with asyncio.TaskGroup() as tg:
            for u in urls:
                tg.create_task(get(u))

    async def shared(urls: list[str]) -> None:
        async with httpx.AsyncClient() as c, asyncio.TaskGroup() as tg:
            for u in urls:
                tg.create_task(c.get(u))

    config = MirrorConfig(origins=1, copies=1, latency=(0.02, 0.02), faults=False)
    with serve(load_articles(Path("data/dota2wiki.jsonl"))[:30], config) as site:
        urls = [site.url(o, p) for (o, p) in site.pages if p.startswith("/wiki/")]
        for label, fn in (
            ("новий AsyncClient на кожен запит", per_request),
            ("один спільний AsyncClient", shared),
        ):
            walls: list[float] = []
            for _ in range(3):
                t0 = time.perf_counter()
                asyncio.run(fn(urls))
                walls.append(time.perf_counter() - t0)
            print(f"  {label:<34} {sorted(walls)[1]:5.2f} s (медіана з 3)")


def exp5() -> None:
    print("5. asyncio.run(main(), debug=True) з time.sleep(0.5) у корутині")
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    log = logging.getLogger("asyncio")
    handler = Grab()
    log.addHandler(handler)
    try:

        async def main() -> None:
            time.sleep(0.5)

        asyncio.run(main(), debug=True)
    finally:
        log.removeHandler(handler)
    for msg in records:
        print("  asyncio WARNING:", msg[:60] + " ... " + msg[-30:])


def _rss_mb_after(kind: str, n: int) -> tuple[float, float]:
    """У ЦЬОМУ процесі: запустити n потоків/задач, що чекають 1 с; -> (секунди, пік RSS МБ)."""
    base = peak_rss_bytes()
    t0 = time.perf_counter()
    if kind == "threads":
        threads = [threading.Thread(target=time.sleep, args=(1,)) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    else:

        async def main() -> None:
            async with asyncio.TaskGroup() as tg:
                for _ in range(n):
                    tg.create_task(asyncio.sleep(1))

        asyncio.run(main())
    return time.perf_counter() - t0, (peak_rss_bytes() - base) / 2**20


def exp6() -> None:
    print("6. 1 000 потоків проти 1 000 задач, кожна «чекає на мережу» 1 с (окремі процеси)")
    for kind in ("threads", "tasks"):
        out = subprocess.run(
            [sys.executable, "-m", "findex.crawler.experiments", "_rss", kind, "1000"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        print(f"  {kind:<8} wall {float(out[0]):5.2f} s, +{float(out[1]):6.1f} МБ RSS")


EXPERIMENTS = {"1": exp1, "2": exp2, "3": exp3, "4": exp4, "5": exp5, "6": exp6}

if __name__ == "__main__":
    if sys.argv[1:2] == ["_rss"]:
        seconds, mb = _rss_mb_after(sys.argv[2], int(sys.argv[3]))
        print(seconds, mb)
    else:
        for name in sys.argv[1:] or EXPERIMENTS:
            EXPERIMENTS[name]()
            print()
