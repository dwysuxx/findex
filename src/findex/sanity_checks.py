"""Три sanity-перевірки BM25 на вашому корпусі (виводить markdown для README).

python -m findex.sanity_checks data/dota2wiki.jsonl        # побудує індекс
python -m findex.sanity_checks index.bin                   # або завантажить збережений
"""

import argparse
from pathlib import Path

from findex.engine import Searcher
from findex.index import Format, Index, Posting, load_or_build
from findex.ranking import BM25, TfIdf


def check_rare_vs_common(index: Index, bm25: BM25) -> None:
    n = index.num_docs
    by_df = sorted(index, key=index.df)
    # беремо слово, а не число: «1022» теж рідкісний, але нічого не пояснює
    rare = next(t for t in by_df if t.isalpha() and len(t) > 3)
    # «поширений», але не в кожному документі (інакше idf у TF-IDF = 0)
    common = next(t for t in reversed(by_df) if index.df(t) < n)
    avg = index.avg_doc_length

    print("## Перевірка 1: рідкісний терм важить більше за поширений\n")
    print("| Терм | df | idf (BM25) | внесок за tf=1 у документі середньої довжини |")
    print("|---|---|---|---|")
    for label, t in (("рідкісний", rare), ("поширений", common)):
        df = index.df(t)
        print(
            f"| {label}: `{t}` | {df} | {bm25.idf(df, n):.3f} | {bm25.term_score(1, df, avg, n, avg):.3f} |"
        )

    with Searcher(index, bm25) as s:
        results = s.search(f"{rare} OR {common}", k=5, snippets=False)

    def tf(term: str, doc: int) -> int:
        return next((p.tf for p in index[term] if p.doc_id == doc), 0)

    print(f"\nЗапит `{rare} OR {common}`, top-5:\n")
    print(f"| # | doc | score | tf(`{rare}`) | tf(`{common}`) |")
    print("|---|---|---|---|---|")
    for rank_, r in enumerate(results, 1):
        print(f"| {rank_} | {r.title} | {r.score:.3f} | {tf(rare, r.doc_id)} | {tf(common, r.doc_id)} |")
    print()


def check_saturation(index: Index, bm25: BM25) -> None:
    n, avg = index.num_docs, index.avg_doc_length
    target = max(2, n // 10)  # терм, що є приблизно в 10% документів: idf помітний, але не екстремальний
    t = min((x for x in index if index.df(x) < n), key=lambda x: abs(index.df(x) - target))
    df = index.df(t)
    tfidf = TfIdf()
    print("## Перевірка 2: насичення tf у BM25\n")
    print(f"Терм `{t}` (df={df}), документ середньої довжини ({avg:.0f} токенів).\n")
    print("| tf | BM25 | приріст за одне входження (від попереднього рядка) | TF-IDF (1+ln tf)·idf |")
    print("|---|---|---|---|")
    prev, prev_tf = 0.0, 0
    for tf in (1, 2, 3, 5, 10, 20, 21, 50):
        bm = bm25.term_score(tf, df, avg, n, avg)
        ti = tfidf(t, Posting(0, tf), index)
        print(f"| {tf} | {bm:.3f} | {(bm - prev) / (tf - prev_tf):+.3f} | {ti:.3f} |")
        prev, prev_tf = bm, tf
    limit = bm25.idf(df, n) * (bm25.k1 + 1)
    print(
        f"\nГраниця при tf→∞: idf·(k1+1) = {limit:.3f}. Приріст від 20-го до 21-го входження майже нульовий.\n"
    )


def check_length_norm(index: Index, bm25: BM25) -> None:
    best = None  # терм, де документи з tf=1 найбільше різняться за довжиною
    for term, plist in index.items():
        ones = [p for p in plist if p.tf == 1]
        if len(ones) < 2:
            continue
        short = min(ones, key=lambda p: index.doc_length(p.doc_id))
        long_ = max(ones, key=lambda p: index.doc_length(p.doc_id))
        ratio = index.doc_length(long_.doc_id) / max(1, index.doc_length(short.doc_id))
        if best is None or ratio > best[0]:
            best = (ratio, term, short, long_)
    print("## Перевірка 3: короткий документ з одним збігом перемагає довгий з одним збігом\n")
    if best is None:
        print("Не знайшлося терма з tf=1 у двох документах.\n")
        return
    _, term, short, long_ = best
    print(f"Терм `{term}`, tf=1 в обох документах:\n")
    print("| документ | довжина (токенів) | BM25 (b=0.75) | BM25 (b=0, без нормалізації) |")
    print("|---|---|---|---|")
    flat = BM25(k1=bm25.k1, b=0.0)
    for label, p in (("короткий", short), ("довгий", long_)):
        print(
            f"| {label}: {index.doc_meta[p.doc_id].title} | {index.doc_length(p.doc_id)} "
            f"| {bm25(term, p, index):.3f} | {flat(term, p, index):.3f} |"
        )
    print("\nПри b=0 довжина документа ігнорується — оцінки рівні.\n")


def main(path: Path, fmt: Format) -> None:
    index = load_or_build(path, fmt)
    bm25 = BM25()
    print(f"{index!r}, avg_doc_length={index.avg_doc_length:.1f}\n")
    check_rare_vs_common(index, bm25)
    check_saturation(index, bm25)
    check_length_norm(index, bm25)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    ap.add_argument("--format", choices=["pickle", "json"], default="pickle")
    a = ap.parse_args()
    main(a.path, a.format)
