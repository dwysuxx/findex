"""M4 (Lab 03) + M3 (Lab 08): precision@k за ВРУЧНУ розміченими запитами — TF-IDF, BM25 і,
якщо є ембединги, семантичний та гібридний режими.

    python -m findex.evaluate data/dota2wiki.jsonl tests/eval_queries.json
    python -m findex.evaluate data/dota2wiki.jsonl tests/eval_queries.json --embeddings index.emb.npz
    python -m findex.evaluate data/dota2wiki.jsonl tests/eval_queries.json --suggest   # кандидати для розмітки

Формат: [{"query": "...", "relevant": ["<id документа з корпусу>", ...], "paraphrase": true?}, ...]
(id — це поле "id" у jsonl, тобто DocMeta.title). Розмічайте, читаючи документи, а не довіряючи
видачі системи: інакше метрика міряє лише те, на що ви вже подивилися. "paraphrase": true —
запит іншими словами, ніж у документах (Lab 08); розмітку парафраз зроблено ДО перегляду видачі.
Запити з "фразами" потребують індексу з positions (для .jsonl він будується автоматично).
"""

import argparse
import json
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from findex.engine import Searcher
from findex.index import Format, load_or_build
from findex.ranking import BM25, SearchResult, TfIdf
from findex.semantic import bag_of_words

log = logging.getLogger(__name__)

type Run = Callable[[str, int], Sequence[SearchResult]]


def precision_at_k(results: Sequence[SearchResult], relevant: set[str], k: int) -> float:
    """Частка релевантних у top-k. Знаменник завжди k: коротка видача не «підвищує» точність."""
    return sum(1 for r in results[:k] if r.title in relevant) / k


def systems(path: Path, fmt: Format, embeddings: Path | None) -> dict[str, Run]:
    """Назва колонки -> «запит, k -> видача». Семантичні колонки — лише з ембедингами."""
    index = load_or_build(path, fmt, positions=True)
    tfidf, bm25 = Searcher(index, TfIdf()), Searcher(index, BM25())
    runs: dict[str, Run] = {
        "TF-IDF": lambda q, k: tfidf.search(q, k=k, snippets=False),
        "BM25": lambda q, k: bm25.search(q, k=k, snippets=False),
        # запит як «мішок слів» (OR): та сама ключова половина, що в гібриді
        "BM25 OR": lambda q, k: bm25.search(bag_of_words(q), k=k, snippets=False),
    }
    if embeddings is not None:
        from findex.semantic import FastEmbedder, SemanticIndex

        sem = SemanticIndex.load(embeddings)
        if sem.num_docs != index.num_docs:
            raise SystemExit(f"{embeddings}: {sem.num_docs} документів, а в корпусі {index.num_docs}")
        dense = Searcher(index, BM25(), semantic=sem, embedder=FastEmbedder(sem.model))
        runs["semantic"] = lambda q, k: dense.search_page(q, k, snippets=False, mode="semantic").results
        runs["hybrid"] = lambda q, k: dense.search_page(q, k, snippets=False, mode="hybrid").results
    return runs


def main(
    path: Path, queries_path: Path, k: int, fmt: Format, suggest: bool, embeddings: Path | None = None
) -> dict[str, dict[str, float]]:
    """Друкує таблицю (markdown) і повертає середні: {"original"|"paraphrase"|"all": {система: P@k}}."""
    queries = json.loads(queries_path.read_text(encoding="utf-8"))
    runs = systems(path, fmt, embeddings)

    if suggest:
        for item in queries:
            print(f"\n### {item['query']}")
            for r in runs["BM25"](item["query"], k):
                print(f"- `{r.title}`")
        return {}

    groups: dict[str, list[dict[str, float]]] = {"original": [], "paraphrase": []}
    rows: list[str] = []
    for item in queries:
        relevant = {str(x) for x in item.get("relevant", [])}
        if not relevant:
            log.warning("пропущено (немає розмітки): %r", item["query"])
            continue
        kind = "paraphrase" if item.get("paraphrase") else "original"
        scores = {name: precision_at_k(run(item["query"], k), relevant, k) for name, run in runs.items()}
        groups[kind].append(scores)
        cells = " | ".join(f"{p:.2f}" for p in scores.values())
        rows.append(
            f"| `{item['query']}` | {'парафраза' if kind == 'paraphrase' else ''} | {len(relevant)} | {cells} |"
        )

    def mean(items: list[dict[str, float]]) -> dict[str, float]:
        return {n: sum(s[n] for s in items) / len(items) for n in runs} if items else {}

    means = {"original": mean(groups["original"]), "paraphrase": mean(groups["paraphrase"])}
    means["all"] = mean(groups["original"] + groups["paraphrase"])

    print("| Запит | тип | релевантних | " + " | ".join(f"P@{k} {n}" for n in runs) + " |")
    print("|---|---|---|" + "---|" * len(runs))
    print("\n".join(rows))
    labels = {"original": "оригінальні", "paraphrase": "парафрази", "all": "усі"}
    sizes = {"original": len(groups["original"]), "paraphrase": len(groups["paraphrase"])}
    sizes["all"] = sizes["original"] + sizes["paraphrase"]
    for key, m in means.items():
        if m:
            cells = " | ".join(f"**{v:.2f}**" for v in m.values())
            print(f"| **середнє: {labels[key]} ({sizes[key]} запитів)** | | | {cells} |")
    return means


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("queries", type=Path)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--format", choices=["pickle", "json"], default="pickle")
    ap.add_argument("--suggest", action="store_true")
    ap.add_argument(
        "--embeddings", type=Path, default=None, help="findex embed (.emb.npz) — колонки semantic/hybrid"
    )
    a = ap.parse_args()
    main(a.path, a.queries, a.k, a.format, a.suggest, a.embeddings)
