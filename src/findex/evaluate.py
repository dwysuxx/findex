"""M4: precision@k для TF-IDF і BM25 за ВРУЧНУ розміченими запитами.

    python -m findex.evaluate data/dota2wiki.jsonl eval_queries.json
    python -m findex.evaluate data/dota2wiki.jsonl eval_queries.json --suggest   # кандидати для розмітки

Формат eval_queries.json: [{"query": "...", "relevant": ["<id документа з корпусу>", ...]}, ...]
(id — це поле "id" у jsonl, тобто DocMeta.title). Розмічайте, читаючи документи, а не довіряючи
видачі системи: інакше метрика міряє лише те, на що ви вже подивилися.
Запити з "фразами" потребують індексу з positions (для .jsonl він будується автоматично).
"""

import argparse
import json
from pathlib import Path

from findex.engine import Searcher
from findex.index import load_or_build
from findex.ranking import BM25, SearchResult, TfIdf


def precision_at_k(results: list[SearchResult], relevant: set[str], k: int) -> float:
    """Частка релевантних у top-k. Знаменник завжди k: коротка видача не «підвищує» точність."""
    return sum(1 for r in results[:k] if r.title in relevant) / k


def main(path: Path, queries_path: Path, k: int, fmt: str, suggest: bool) -> None:
    index = load_or_build(path, fmt, positions=True)
    queries = json.loads(queries_path.read_text(encoding="utf-8"))
    scorers = {"TF-IDF": TfIdf(), "BM25": BM25()}
    searchers = {name: Searcher(index, sc) for name, sc in scorers.items()}

    if suggest:
        for item in queries:
            print(f"\n### {item['query']}")
            for r in searchers["BM25"].search(item["query"], k=k):
                print(f"- `{r.title}`  {r.snippet}")
        return

    rows, sums = [], {name: 0.0 for name in scorers}
    labeled = 0
    for item in queries:
        relevant = {str(x) for x in item.get("relevant", [])}
        if not relevant:
            print(f"# пропущено (немає розмітки): {item['query']!r}")
            continue
        labeled += 1
        cells = []
        for name, searcher in searchers.items():
            p = precision_at_k(searcher.search(item["query"], k=k, snippets=False), relevant, k)
            sums[name] += p
            cells.append(f"{p:.2f}")
        rows.append(f"| `{item['query']}` | {len(relevant)} | " + " | ".join(cells) + " |")

    print("| Запит | релевантних | " + " | ".join(f"P@{k} {n}" for n in scorers) + " |")
    print("|---|---|" + "---|" * len(scorers))
    print("\n".join(rows))
    if labeled:
        print(f"| **середнє ({labeled} запитів)** | | " + " | ".join(f"**{sums[n] / labeled:.2f}**" for n in scorers) + " |")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("queries", type=Path)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--format", choices=["pickle", "json"], default="pickle")
    ap.add_argument("--suggest", action="store_true")
    a = ap.parse_args()
    main(a.path, a.queries, a.k, a.format, a.suggest)
