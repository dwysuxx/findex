"""Lab 06 M4: бенчмарк краулера на локальному дзеркалі.

    python -m findex.crawler.bench run --runs 3      # -> bench/crawl_results.jsonl
    python -m findex.crawler.bench table             # markdown для README

Кожен прогін: свіжий процес дзеркала (лічильники «першого запиту» скинуто — збої ті самі)
і свіжий процес краулу (чесний пік RSS). Час — від старту crawl() до останньої сторінки.
"""

import argparse
import asyncio
import json
import logging
import os
import statistics
import subprocess
import sys
from contextlib import aclosing
from pathlib import Path
from typing import Any

from findex.crawler.crawl import CrawlStats, crawl
from findex.utils import peak_rss_bytes

RESULTS = Path("bench/crawl_results.jsonl")
SRC = Path(__file__).resolve().parents[2]
# (concurrency, per_host, delay): основна таблиця завдання + два рядки «ціна ввічливості»
CONFIGS: list[tuple[int, int, float]] = [(1, 2, 0.0), (5, 2, 0.0), (20, 2, 0.0), (20, 4, 0.0), (20, 2, 0.25)]

type Row = dict[str, Any]


async def _one(
    seed: str, max_pages: int, concurrency: int, per_host: int, delay: float, timeout: float
) -> CrawlStats:
    stats = CrawlStats()
    pages = crawl([seed], max_pages=max_pages, concurrency=concurrency, per_host=per_host, min_delay=delay, timeout=timeout, stats=stats)  # fmt: skip
    async with aclosing(pages):
        async for _ in pages:
            pass
    return stats


def one(
    seed: str, max_pages: int, concurrency: int, per_host: int, delay: float, timeout: float, debug: bool
) -> Row:
    slow: list[str] = []

    class Count(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "took" in record.getMessage():
                slow.append(record.getMessage())

    logging.getLogger("asyncio").addHandler(Count())
    logging.getLogger("findex.crawler").setLevel(logging.ERROR)  # ретраї — у stats, не в stderr
    stats = asyncio.run(_one(seed, max_pages, concurrency, per_host, delay, timeout), debug=debug)
    return {
        "concurrency": concurrency,
        "per_host": per_host,
        "delay": delay,
        "max_pages": max_pages,
        "debug": debug,
        "wall": stats.elapsed,
        "pages": stats.pages,
        "fetched": stats.fetched,
        "errors": stats.errors,
        "retries": stats.retries,
        "max_in_flight": stats.max_in_flight,
        "rss": peak_rss_bytes(),
        "slow_callbacks": len(slow),
        "slow_messages": slow[:5],
    }


def _start_mirror() -> tuple[subprocess.Popen[str], str]:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "findex.crawler.mirror", "--origins", "5", "--copies", "2"],
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.stdout is not None
    while not (line := proc.stdout.readline()).startswith("ready"):
        if not line:
            raise RuntimeError("mirror died")
    return proc, line.split()[1]  # перший origin — seed


def run(runs: int, max_pages: int, timeout: float, debug: bool) -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}
    RESULTS.parent.mkdir(exist_ok=True)
    configs = CONFIGS[2:3] if debug else CONFIGS
    for concurrency, per_host, delay in configs:
        for i in range(runs):
            mirror, seed = _start_mirror()
            try:
                cmd = [sys.executable, "-m", "findex.crawler.bench", "one", seed, str(max_pages), str(concurrency), str(per_host), str(delay), str(timeout)]  # fmt: skip
                if debug:
                    cmd.append("--debug")
                out = subprocess.run(
                    cmd, env=env, capture_output=True, text=True, encoding="utf-8", check=True
                )
            finally:
                mirror.terminate()
                mirror.wait()
            row = json.loads(out.stdout) | {"run": i}
            with RESULTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            print(
                f"c={concurrency:<2} per_host={per_host} delay={delay:<4} debug={debug!s:<5} wall {row['wall']:6.2f}s "
                f"pages {row['pages']} errors {row['errors']} retries {row['retries']} slow_cb {row['slow_callbacks']}",
                flush=True,
            )


def table(rows: list[Row]) -> str:
    groups: dict[tuple[int, int, float], list[Row]] = {}
    for r in rows:
        if not r["debug"]:
            groups.setdefault((r["concurrency"], r["per_host"], r["delay"]), []).append(r)
    base = statistics.median(r["wall"] for r in groups[(1, 2, 0.0)])
    lines = [
        "| Concurrency | Per-host | Delay, с | Pages | Wall, с (медіана ± розкид) | Pages/s | Errors | Retries | Макс. у польоті | Peak RSS, МБ | Speedup |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (c, ph, d), rs in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
        walls = [r["wall"] for r in rs]
        wall = statistics.median(walls)
        pages = statistics.median(r["pages"] for r in rs)
        lines.append(
            f"| {c} | {ph} | {d:g} | {pages:.0f} | {wall:.2f} ± {max(walls) - min(walls):.2f} | {pages / wall:.1f} "
            f"| {statistics.median(r['errors'] for r in rs):.0f} | {statistics.median(r['retries'] for r in rs):.0f} "
            f"| {max(r['max_in_flight'] for r in rs)} | {statistics.median(r['rss'] for r in rs) / 2**20:.0f} "
            f"| {base / wall:.1f}× |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("one")
    p.add_argument("seed")
    p.add_argument("max_pages", type=int)
    p.add_argument("concurrency", type=int)
    p.add_argument("per_host", type=int)
    p.add_argument("delay", type=float)
    p.add_argument("timeout", type=float)
    p.add_argument("--debug", action="store_true")
    p = sub.add_parser("run")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--max-pages", type=int, default=200)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--debug", action="store_true", help="один конфіг (c=20) з asyncio debug=True")
    sub.add_parser("table")
    args = parser.parse_args()
    match args.cmd:
        case "one":
            row = one(
                args.seed,
                args.max_pages,
                args.concurrency,
                args.per_host,
                args.delay,
                args.timeout,
                args.debug,
            )
            print(json.dumps(row))
        case "run":
            run(args.runs, args.max_pages, args.timeout, args.debug)
        case "table":
            rows = [
                json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            print(table(rows))
        case _:
            parser.error(args.cmd)


if __name__ == "__main__":
    main()
