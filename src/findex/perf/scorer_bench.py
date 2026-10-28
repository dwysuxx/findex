"""M2: таблиця «Lab 03 scorer проти NumPy scorer» — тільки ранжування (parse + збіги + score + top-k),
без сніпетів. Медіана з REPEAT вимірів, кожен — середнє з кількох викликів.

    python -m findex.perf.scorer_bench data/dota2wiki.jsonl data/dota2wiki_x16.jsonl
"""

import argparse
import json
import statistics
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from findex.index import Index, build_index
from findex.query import Node, parse
from findex.ranking import BM25, rank
from findex.vector import VectorIndex, mask
from findex.vector import rank as rank_vector

REPEAT = 15
OUT = Path("bench")


def median_ms(fn: Callable[[], object], budget_s: float = 0.05) -> tuple[float, float]:
    """(медіана, IQR) у мс. Число викликів на вимір підбирається так, щоб вимір тривав ≈ budget_s."""
    fn()  # прогрів
    t0 = time.perf_counter()
    fn()
    one = max(time.perf_counter() - t0, 1e-7)
    number = max(1, int(budget_s / one))
    samples: list[float] = []
    for _ in range(REPEAT):
        t0 = time.perf_counter()
        for _ in range(number):
            fn()
        samples.append((time.perf_counter() - t0) / number * 1000)
    q = statistics.quantiles(samples, n=4)
    return statistics.median(samples), q[2] - q[0]


def queries_for(index: Index) -> dict[str, str]:
    by_df = sorted(index.postings, key=lambda t: (-len(index.postings[t]), t))
    words = [t for t in by_df if t.isascii() and t.isalpha() and len(t) > 3]  # без "0", "σstr" і стоп-слів
    common = words[0]
    rare = next(t for t in reversed(words) if len(index.postings[t]) >= 2 * index.num_docs // 124)
    return {"common term": common, "rare term": rare, "3-term query": "hero damage attack"}


def run(corpus: Path, k: int = 10) -> list[dict[str, object]]:
    index = build_index(corpus)
    vx = VectorIndex.from_index(index)
    scorer = BM25()
    rows: list[dict[str, object]] = []
    for label, query in queries_for(index).items():
        node = parse(query)
        terms = node.terms()

        def lab03(node: Node = node, terms: list[str] = terms) -> object:
            return rank(index, node.evaluate(index), terms, scorer, k)

        def numpy_(node: Node = node, terms: list[str] = terms) -> object:
            return rank_vector(vx, mask(node, vx), terms, scorer, k)

        assert lab03() == rank_vector(vx, mask(node, vx), terms, scorer, k)[0]
        py_ms, py_iqr = median_ms(lab03)
        np_ms, np_iqr = median_ms(numpy_)
        rows.append(
            {
                "corpus": corpus.name,
                "docs": index.num_docs,
                "label": label,
                "query": query,
                "matched": len(node.evaluate(index)),
                "postings": sum(index.df(t) for t in dict.fromkeys(terms)),
                "lab03_ms": round(py_ms, 4),
                "lab03_iqr": round(py_iqr, 4),
                "numpy_ms": round(np_ms, 4),
                "numpy_iqr": round(np_iqr, 4),
                "speedup": round(py_ms / np_ms, 1),
            }
        )
        print(rows[-1])
    return rows


def table(rows: list[dict[str, object]]) -> str:
    lines = [
        "| Corpus | Query | Docs matched | Postings scored | Lab 3 scorer (ms) | NumPy scorer (ms) | Speedup |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['corpus']} ({r['docs']} docs) | {r['label']}: `{r['query']}` | {r['matched']} | {r['postings']} "
            f"| {r['lab03_ms']:.3f} ± {r['lab03_iqr']:.3f} | {r['numpy_ms']:.3f} ± {r['numpy_iqr']:.3f} "
            f"| **{r['speedup']}×** |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("corpora", type=Path, nargs="+")
    a = ap.parse_args()
    rows = [row for c in a.corpora for row in run(c)]
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with (OUT / "scorer_results.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r | {"ts": stamp}, ensure_ascii=False) + "\n")
    md = table(rows)
    (OUT / "scorer_table.md").write_text(md + "\n", encoding="utf-8")
    print(md)
