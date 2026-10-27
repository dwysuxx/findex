"""Lab 07, M2 + M4: def vs async def vs to_thread vs воркери — на справжньому сервері.

    uv run python -m findex.web.bench burst            # M2: 20 одночасних запитів + /health під час них
    uv run python -m findex.web.bench oha              # M4: таблиця oha -> bench/web_results.jsonl, web_table.md
    uv run python -m findex.web.bench oha --url https://<deployed>   # рядок для розгорнутого сервісу

Бенчмарк-застосунок = звичайний create_app() + три службові маршрути з тією самою роботою, що й
/search, але інакше оголошені. У продакшн-застосунку їх немає.
CACHE_SIZE=0: кожен запит рахує заново, інакше після першого запиту міряли б lru_cache.
"""

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import FastAPI, Query

from findex.web import create_app
from findex.web.deps import EngineDep
from findex.web.models import SearchRequest, SearchResponse
from findex.web.service import run_search

QUERY = "hero damage attack"  # збігається з усіма 124 документами: ~45 мс CPU на запит (сніпети)
BENCH = Path("bench")


def create_bench_app() -> FastAPI:
    app = create_app()

    @app.get("/bench/search-async", response_model=SearchResponse, include_in_schema=False)
    async def search_async(params: Annotated[SearchRequest, Query()], engine: EngineDep) -> SearchResponse:  # pyright: ignore[reportUnusedFunction]
        """НЕПРАВИЛЬНО: блокуюча CPU-робота прямо в циклі подій (баг Lab 06 у новому вбранні)."""
        return run_search(engine, params)

    @app.get("/bench/search-thread", response_model=SearchResponse, include_in_schema=False)
    async def search_thread(params: Annotated[SearchRequest, Query()], engine: EngineDep) -> SearchResponse:  # pyright: ignore[reportUnusedFunction]
        """async def + to_thread: те саме, що робить FastAPI для def, але вручну."""
        return await asyncio.to_thread(run_search, engine, params)

    @app.get("/bench/sleep-async", include_in_schema=False)
    async def sleep_async() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        time.sleep(1)  # «Prove it» №3: блокуючий sleep в async def
        return {"ok": "async"}

    @app.get("/bench/sleep-def", include_in_schema=False)
    def sleep_def() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        time.sleep(1)
        return {"ok": "def"}

    return app


ROUTES = {
    "async def": "/bench/search-async",
    "async def + to_thread": "/bench/search-thread",
    "def": "/search",
}


class Server:
    """uvicorn у підпроцесі; чекаємо /health = 200 (тобто lifespan уже завантажив індекс)."""

    def __init__(self, port: int, workers: int = 1, index: str = "index.bin") -> None:
        self.base = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "INDEX_PATH": index,
            "CORPUS_PATH": "data/dota2wiki.jsonl",
            "CACHE_SIZE": "0",
            "LOG_LEVEL": "WARNING",  # рядок доступу на кожен запит теж коштує — у бенчмарку вимкнено
        }
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "findex.web.bench:create_bench_app",
            "--factory",
            "--port",
            str(port),
            "--workers",
            str(workers),
            "--no-access-log",
            "--log-level",
            "warning",
        ]
        self.proc = subprocess.Popen(cmd, env=env)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{self.base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            self.stop()
            raise RuntimeError("server did not start")
        if workers > 1:  # /health відповів один воркер; даємо піднятися решті
            time.sleep(3)

    def stop(self) -> None:
        if sys.platform == "win32":
            # terminate() на Windows — TerminateProcess лише цього PID; .venv\Scripts\python.exe —
            # лаунчер, а воркери uvicorn — його онуки, вони б лишилися жити. Тож убиваємо дерево.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True, check=False
            )
        else:
            self.proc.terminate()  # SIGTERM: uvicorn зупиняє воркерів сам
        self.proc.wait(timeout=30)

    def __enter__(self) -> "Server":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


async def _burst(base: str, path: str, n: int) -> dict[str, Any]:
    """n одночасних пошуків + /health, запущений через 50 мс: чи відповідає сервер, поки рахує?"""
    async with httpx.AsyncClient(base_url=base, timeout=120, limits=httpx.Limits(max_connections=n + 5)) as c:
        await c.get(path, params={"q": QUERY})  # прогрів

        async def one(p: str, **params: str) -> float:
            t = time.perf_counter()
            r = await c.get(p, params=params)
            r.raise_for_status()
            return time.perf_counter() - t

        async def probe() -> float:
            await asyncio.sleep(0.05)
            return await one("/health")

        t0 = time.perf_counter()
        *lat, health = await asyncio.gather(*(one(path, q=QUERY) for _ in range(n)), probe())
        wall = time.perf_counter() - t0
    lat.sort()
    return {
        "wall_s": round(wall, 3),
        "min_ms": round(lat[0] * 1000, 1),
        "median_ms": round(statistics.median(lat) * 1000, 1),
        "max_ms": round(lat[-1] * 1000, 1),
        "health_ms": round(health * 1000, 1),
    }


async def _sleep_pair(base: str) -> dict[str, float]:
    async with httpx.AsyncClient(base_url=base, timeout=30) as c:
        out: dict[str, float] = {}
        for name in ("async", "def"):
            t = time.perf_counter()
            await asyncio.gather(c.get(f"/bench/sleep-{name}"), c.get(f"/bench/sleep-{name}"))
            out[name] = round(time.perf_counter() - t, 2)
        return out


def cmd_burst(args: argparse.Namespace) -> None:
    lines = [f"# M2: {args.n} одночасних GET ?q={QUERY!r}, 1 воркер, CACHE_SIZE=0, {args.runs} прогони", ""]
    with Server(args.port) as srv:
        sleeps = asyncio.run(_sleep_pair(srv.base))
        lines.append(
            f"Prove it №3 — два одночасні запити з time.sleep(1): async def {sleeps['async']} с, "
            f"def {sleeps['def']} с"
        )
        lines.append("")
        lines.append("| маршрут | wall, с | min, мс | медіана, мс | max, мс | /health під час burst, мс |")
        lines.append("|---|---|---|---|---|---|")
        for name, path in ROUTES.items():
            runs = [asyncio.run(_burst(srv.base, path, args.n)) for _ in range(args.runs)]
            best = min(runs, key=lambda r: r["wall_s"])  # найкращий прогін: найменше шуму від ОС
            health = statistics.median(r["health_ms"] for r in runs)
            lines.append(
                f"| `{name}` | {best['wall_s']} | {best['min_ms']} | {best['median_ms']} | "
                f"{best['max_ms']} | {health:.1f} |"
            )
            print(name, runs, file=sys.stderr)
    text = "\n".join(lines) + "\n"
    print(text)
    BENCH.mkdir(exist_ok=True)
    (BENCH / "web_burst.md").write_text(text, encoding="utf-8")


def _oha(url: str, duration: str, conc: int) -> dict[str, Any]:
    oha = shutil.which("oha") or os.environ.get("OHA", "oha")
    out = subprocess.run(
        [oha, "-z", duration, "-c", str(conc), "--no-tui", "--output-format", "json", url],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    ).stdout
    data = json.loads(out)
    pct = data["latencyPercentiles"]
    ok = data["statusCodeDistribution"]
    return {
        "rps": round(data["summary"]["requestsPerSec"], 1),
        "p50_ms": round(pct["p50"] * 1000, 1),
        "p95_ms": round(pct["p95"] * 1000, 1),
        "p99_ms": round(pct["p99"] * 1000, 1),
        "requests": sum(ok.values()),
        "non_200": sum(v for k, v in ok.items() if k != "200"),
    }


def cmd_oha(args: argparse.Namespace) -> None:
    results: list[dict[str, Any]] = []
    q = httpx.QueryParams({"q": QUERY})
    if args.url:  # там CACHE_SIZE за замовч., але кешується лише ранжування (<1 мс), сніпети — щоразу
        row = {
            "target": f"deployed ({args.url})",
            "route": "/search",
            "c": args.c_deployed,
            **_oha(f"{args.url.rstrip('/')}/search?{q}", args.duration, args.c_deployed),
        }
        results.append(row)
    else:
        configs = [(1, "async def"), (1, "async def + to_thread"), (1, "def"), (4, "def")]
        for workers, mode in configs:
            with Server(args.port, workers) as srv:
                url = f"{srv.base}{ROUTES[mode]}?{q}"
                _oha(url, "3s", args.c)  # прогрів
                row = {
                    "target": f"local, {workers} worker{'s' if workers > 1 else ''}, `{mode}`",
                    "route": ROUTES[mode],
                    "c": args.c,
                    **_oha(url, args.duration, args.c),
                }
                print(row, file=sys.stderr)
                results.append(row)

    BENCH.mkdir(exist_ok=True)
    with (BENCH / "web_results.jsonl").open("a", encoding="utf-8") as f:
        for r in results:
            f.write(
                json.dumps(
                    {
                        **r,
                        "query": QUERY,
                        "duration": args.duration,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    rows = [
        "| Target | Route | Concurrency | RPS | p50 (ms) | p95 | p99 | не-200 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    rows += [
        f"| {r['target']} | `{r['route']}` | {r['c']} | {r['rps']} | {r['p50_ms']} | {r['p95_ms']} | "
        f"{r['p99_ms']} | {r['non_200']} |"
        for r in results
    ]
    text = "\n".join(rows) + "\n"
    print(text)
    name = "web_table_deployed.md" if args.url else "web_table.md"
    (BENCH / name).write_text(text, encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)
    b = sub.add_parser("burst")
    b.add_argument("-n", type=int, default=20)
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--port", type=int, default=8770)
    b.set_defaults(fn=cmd_burst)
    o = sub.add_parser("oha")
    o.add_argument("--duration", default="30s")
    o.add_argument("-c", type=int, default=50)
    o.add_argument("--c-deployed", type=int, default=20)
    o.add_argument("--url", default=None)
    o.add_argument("--port", type=int, default=8771)
    o.set_defaults(fn=cmd_oha)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
