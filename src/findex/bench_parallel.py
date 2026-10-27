"""Lab 05 M3: бенчмарк паралельної побудови індексу.

    python -m findex.bench_parallel scale data/dota2wiki.jsonl --times 16     # data/dota2wiki_x16.jsonl
    python -m findex.bench_parallel run data/dota2wiki.jsonl --runs 3         # -> bench/results.jsonl
    python -m findex.bench_parallel run data/dota2wiki_x16.jsonl --python path/to/python3.13t.exe \\
        --configs serial:1 threads:1,2,4,8
    python -m findex.bench_parallel table                                     # markdown для README

Кожен прогін — окремий процес (`one`): пік RSS монотонний за життя процесу, тож у спільному
процесі другий прогін успадкував би пік першого. Час рахується лише навколо build_parallel,
без старту інтерпретатора; для процесів сюди входить запуск пулу — його платить і користувач.
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from findex.parallel import EXECUTORS, ExecutorName, Transport, build_parallel, gil_enabled
from findex.utils import peak_rss_bytes

RESULTS = Path("bench/results.jsonl")
SRC = Path(__file__).resolve().parents[1]  # .../src — щоб інший інтерпретатор (3.13t) бачив findex
DEFAULT_CONFIGS = ["serial:1,8", "threads:1,2,4,8", "processes-pickle:1,2,4,8", "processes:1,2,4,8"]

type Row = dict[str, Any]


def python_tag() -> str:
    """3.14 / 3.13t: суфікс t — free-threaded збірка (навіть якщо GIL увімкнули під час виконання)."""
    ft = "t" if _free_threaded_build() else ""
    return f"{sys.version_info.major}.{sys.version_info.minor}{ft}"


def _free_threaded_build() -> bool:
    import sysconfig

    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


def scale(corpus: Path, times: int, out: Path) -> None:
    """Корпус ×N: ті самі статті з унікальними id. Словник не росте — постінги довшають у N разів."""
    lines = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines() if line.strip()]
    with out.open("w", encoding="utf-8") as f:
        for copy in range(times):
            for obj in lines:
                f.write(json.dumps({**obj, "id": f"{obj['id']}~{copy}"}, ensure_ascii=False) + "\n")
    print(f"{out}: {len(lines) * times} docs, {out.stat().st_size / 2**20:.1f} MB")


def one(corpus: Path, executor: ExecutorName, workers: int, positions: bool, transport: Transport) -> Row:
    """Один прогін у цьому процесі -> рядок результату."""
    wall0, cpu0 = time.perf_counter(), time.process_time()
    report = build_parallel(corpus, executor, workers, positions=positions, transport=transport)
    wall, parent_cpu = time.perf_counter() - wall0, time.process_time() - cpu0
    return {
        "python": python_tag(),
        "gil": gil_enabled(),
        "corpus": corpus.name,
        "corpus_mb": round(corpus.stat().st_size / 2**20, 2),
        "positions": positions,
        "executor": executor,
        "transport": transport if executor == "processes" else None,
        "workers": workers,
        "chunks": report.chunks,
        "wall": wall,
        "cpu": parent_cpu + report.child_cpu,  # батько (усі його потоки) + процеси-воркери
        "parent_cpu": parent_cpu,
        "task_cpu": sum(s.task_cpu for s in report.worker_stats),  # чиста робота build_partial
        "plan": report.plan_s,
        "map": report.map_s,
        "merge": report.merge_s,
        "rss_parent": peak_rss_bytes(),
        "rss_children": report.child_peak_rss,
        "docs": report.index.num_docs,
        "terms": len(report.index),
    }


type Config = tuple[ExecutorName, int, Transport]
_BY_NAME: dict[str, ExecutorName] = {e: e for e in EXECUTORS}  # str -> ExecutorName без cast


def parse_configs(specs: list[str]) -> list[Config]:
    """["threads:1,2", "processes-pickle:8"] -> [("threads", 1, ...), ("threads", 2, ...), ("processes", 8, "pickle")]."""
    configs: list[Config] = []
    for spec in specs:
        name, _, counts = spec.partition(":")
        name, _, wire = name.partition("-")
        executor = _BY_NAME.get(name)
        if executor is None or wire not in ("", "compact", "pickle"):
            raise SystemExit(f"unknown executor {spec!r}; choose from {EXECUTORS}, processes-pickle")
        transport: Transport = "pickle" if wire == "pickle" else "compact"
        configs.extend((executor, int(n), transport) for n in counts.split(","))
    return configs


def run(corpus: Path, python: str, configs: list[Config], runs: int, positions: bool) -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}

    def launch(executor: ExecutorName, workers: int, transport: Transport = "compact") -> Row:
        cmd = [python, "-m", "findex.bench_parallel", "one", str(corpus), executor, str(workers)]
        cmd += ["--transport", transport]
        if positions:
            cmd.append("--positions")
        out = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads(out.stdout)

    launch("serial", 1)  # прогрів: файл у кеші ОС, .pyc скомпільовані — далі всі в рівних умовах
    RESULTS.parent.mkdir(exist_ok=True)
    for executor, workers, transport in configs:
        for i in range(runs):
            row = launch(executor, workers, transport)
            row |= {"run": i, "cpu_model": cpu_model(), "logical_cpus": os.cpu_count()}
            with RESULTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            print(
                f"{row['python']:>5} {row['corpus']:<22} {_label(row):<18} x{workers:<2} "
                f"wall {row['wall']:6.2f}s  cpu {row['cpu']:6.2f}s  merge {row['merge']:.3f}s",
                flush=True,
            )


def cpu_model() -> str:
    if sys.platform == "win32":
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    return platform.processor() or platform.machine()


def _label(row: Row) -> str:
    """Як конфігурація зветься в таблиці: processes з pickle-транспортом — окремий рядок."""
    return "processes (pickle)" if row.get("transport") == "pickle" else row["executor"]


def load_rows(path: Path = RESULTS) -> list[Row]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


type Key = tuple[str, str, str, int]  # (corpus, python, executor, workers)


def summarize(rows: list[Row]) -> dict[Key, Row]:
    """Медіани по прогонах + speedup відносно serial x1 на тому ж корпусі й тому ж Python 3.14."""
    groups: dict[Key, list[Row]] = defaultdict(list)
    for r in rows:
        groups[(r["corpus"], r["python"], _label(r), r["workers"])].append(r)

    out: dict[Key, Row] = {}
    for key, rs in groups.items():
        walls = [r["wall"] for r in rs]
        out[key] = {
            "n": len(rs),
            "wall": statistics.median(walls),
            "spread": max(walls) - min(walls),
            "cpu": statistics.median(r["cpu"] for r in rs),
            "rss_parent": statistics.median(r["rss_parent"] for r in rs),
            "rss_children": statistics.median(r["rss_children"] for r in rs),
            "merge": statistics.median(r["merge"] for r in rs),
            "gil": rs[0]["gil"],
        }
    baseline = {k[0]: v["wall"] for k, v in out.items() if k[1:] == (GIL_PYTHON, "serial", 1)}
    for (corpus, *_), v in out.items():
        v["speedup"] = baseline[corpus] / v["wall"] if corpus in baseline else float("nan")
    return out


GIL_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
ORDER = {"serial": 0, "threads": 1, "processes (pickle)": 2, "processes": 3}


def table(rows: list[Row]) -> str:
    summary = summarize(rows)
    lines: list[str] = []
    for corpus in sorted({k[0] for k in summary}, key=len):
        mb = next(r["corpus_mb"] for r in rows if r["corpus"] == corpus)
        lines += [
            f"**{corpus}** ({mb} МБ). Speedup — відносно serial ×1 на Python {GIL_PYTHON}.",
            "",
            "| Python | Executor | Workers | Wall, с (медіана ± розкид) | CPU, с | Peak RSS батька, МБ | RSS воркерів, МБ | Merge, с | Speedup |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        keys = sorted(
            (k for k in summary if k[0] == corpus),
            key=lambda k: (k[1] != GIL_PYTHON, k[1], ORDER[k[2]], k[3]),
        )
        for key in keys:
            v = summary[key]
            gil = "" if v["gil"] else " (GIL off)"
            child = f"{v['rss_children'] / 2**20:.0f}" if v["rss_children"] else "—"
            lines.append(
                f"| {key[1]}{gil} | {key[2]} | {key[3]} | {v['wall']:.2f} ± {v['spread']:.2f} | {v['cpu']:.2f} "
                f"| {v['rss_parent'] / 2**20:.0f} | {child} | {v['merge']:.3f} | {v['speedup']:.2f}× |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scale", help="зробити корпус ×N")
    p.add_argument("corpus", type=Path)
    p.add_argument("--times", type=int, default=16)
    p.add_argument("--out", type=Path)

    p = sub.add_parser("one", help="один прогін, JSON у stdout")
    p.add_argument("corpus", type=Path)
    p.add_argument("executor", choices=EXECUTORS)
    p.add_argument("workers", type=int)
    p.add_argument("--positions", action="store_true")
    p.add_argument("--transport", choices=["compact", "pickle"], default="compact")

    p = sub.add_parser("run", help="серія прогонів у окремих процесах -> bench/results.jsonl")
    p.add_argument("corpus", type=Path)
    p.add_argument("--python", default=sys.executable, help="інтерпретатор (напр. 3.13t)")
    p.add_argument("--configs", nargs="+", default=DEFAULT_CONFIGS, help="executor:1,2,4 ...")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--no-positions", dest="positions", action="store_false")

    sub.add_parser("table", help="markdown-таблиця з bench/results.jsonl")

    args = parser.parse_args()
    match args.cmd:
        case "scale":
            out = args.out or args.corpus.with_name(f"{args.corpus.stem}_x{args.times}.jsonl")
            scale(args.corpus, args.times, out)
        case "one":
            print(json.dumps(one(args.corpus, args.executor, args.workers, args.positions, args.transport)))
        case "run":
            run(args.corpus, args.python, parse_configs(args.configs), args.runs, args.positions)
        case "table":
            print(table(load_rows()))
        case _:
            parser.error(f"unknown command {args.cmd}")


if __name__ == "__main__":  # обов'язково: під spawn воркери імпортують головний модуль заново
    main()
