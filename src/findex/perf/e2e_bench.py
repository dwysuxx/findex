"""M2: увесь запит «до» (код тегу lab-07) і «після» (поточний) — parse + збіги + BM25 + top-10 + сніпети.

«До» — не емуляція, а справжній код Lab 07 з git worktree; кожна версія — в окремому процесі
з власним PYTHONPATH, на тому самому index.bin і тих самих запитах:

    git worktree add --detach ../lab07 lab-07
    python -m findex.perf.e2e_bench --before ../lab07/src --index index.bin
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

QUERIES = ["hero damage attack", "pudge", '"meat hook"', "illusion OR illusions", "stun OR slow OR silence"]

# Виконується всередині дочірнього процесу: лише API, що є в обох версіях (Searcher, load).
CHILD = r"""
import json, statistics, sys, time
from findex.engine import Searcher
from findex.store import load
index_path, queries = sys.argv[1], json.loads(sys.argv[2])
index = load(index_path)
out = {}
with Searcher(index, cache_size=0) as s:
    for q in queries:
        for snippets in (False, True):
            s.search(q, snippets=snippets)  # прогрів (TextStore, ліниві структури)
            samples = []
            for _ in range(9):
                n, t0 = 0, time.perf_counter()
                while (dt := time.perf_counter() - t0) < 0.15:
                    s.search(q, snippets=snippets); n += 1
                samples.append(dt / n * 1000)
            out[f"{q}|{snippets}"] = statistics.median(samples)
        out[f"{q}|total"] = s.search_page(q, 1).total
print(json.dumps(out))
"""


def measure(src: Path, index: Path) -> dict[str, float]:
    env = os.environ | {"PYTHONPATH": str(src.resolve()), "PYTHONIOENCODING": "utf-8"}
    res = subprocess.run(
        [sys.executable, "-c", CHILD, str(index), json.dumps(QUERIES)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(res.stdout)


def interleaved(
    before: Path, after: Path, index: Path, rounds: int
) -> tuple[dict[str, float], dict[str, float]]:
    """До/після по черзі, rounds разів, медіана кожної клітинки: дрейф машини (турбо, фонові задачі)
    діє на обидві версії, а не лише на ту, що мірялась другою. Перший прогін мав ±50 % між запусками."""
    runs_b: list[dict[str, float]] = []
    runs_a: list[dict[str, float]] = []
    for _ in range(rounds):
        runs_b.append(measure(before, index))
        runs_a.append(measure(after, index))

    def med(runs: list[dict[str, float]]) -> dict[str, float]:
        return {k: statistics.median(r[k] for r in runs) for k in runs[0]}

    return med(runs_b), med(runs_a)


def main(before: Path, after: Path, index: Path, rounds: int = 3) -> None:
    b, a = interleaved(before, index=index, after=after, rounds=rounds)
    lines = [
        "| Query | Docs matched | Lab 07: ranking (ms) | Lab 08: ranking (ms) | Lab 07: + snippets (ms) "
        "| Lab 08: + snippets (ms) | Speedup (whole request) |",
        "|---|---|---|---|---|---|---|",
    ]
    rows: list[dict[str, object]] = []
    for q in QUERIES:
        r: dict[str, object] = {
            "query": q,
            "matched": int(a[f"{q}|total"]),
            "lab07_rank_ms": round(b[f"{q}|False"], 3),
            "lab08_rank_ms": round(a[f"{q}|False"], 3),
            "lab07_full_ms": round(b[f"{q}|True"], 3),
            "lab08_full_ms": round(a[f"{q}|True"], 3),
        }
        r["speedup"] = round(b[f"{q}|True"] / a[f"{q}|True"], 1)
        rows.append(r)
        lines.append(
            f"| `{q}` | {r['matched']} | {r['lab07_rank_ms']:.3f} | {r['lab08_rank_ms']:.3f} "
            f"| {r['lab07_full_ms']:.2f} | {r['lab08_full_ms']:.2f} | **{r['speedup']}×** |"
        )
    Path("bench").mkdir(exist_ok=True)
    Path("bench/e2e_results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    Path("bench/e2e_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", type=Path, required=True, help="src/ версії «до» (worktree тегу lab-07)")
    ap.add_argument("--after", type=Path, default=Path("src"))
    ap.add_argument("--index", type=Path, default=Path("index.bin"))
    a = ap.parse_args()
    main(a.before, a.after, a.index)
