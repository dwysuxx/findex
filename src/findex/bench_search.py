"""Бенчмарк: двухуказательное слияние (merge) против множеств (set) для AND.

Запуск из корня проекта:
    uv run python -m findex.bench_search data/dota2wiki.jsonl
"""

import argparse
import timeit
from pathlib import Path

from findex.index import build_index
from findex.search import and_merge, and_set, get_doc_ids

NUMBER = 20_000
REPEAT = 5


def measure_us(fn, a: list[int], b: list[int]) -> float:
    """Время одного вызова fn(a, b) в микросекундах."""
    times = timeit.repeat(lambda: fn(a, b), number=NUMBER, repeat=REPEAT)
    return min(times) / NUMBER * 1e6


def main(root: Path) -> None:
    postings = build_index(root).postings

    by_len = sorted(postings, key=lambda t: len(postings[t]), reverse=True)
    common = by_len[:2]  # два самых частых
    rare = by_len[-2:]  # два самых редких

    print(f"Частые термины: {common}, длины {[len(postings[t]) for t in common]}")
    print(f"Редкие термины: {rare}, длины {[len(postings[t]) for t in rare]}")
    print()

    pairs = {
        "частый + частый": (common[0], common[1]),
        "редкий + редкий": (rare[0], rare[1]),
        "частый + редкий": (common[0], rare[0]),
    }

    print("| Пара | Термины | Длины списков | merge (мкс) | set (мкс) | Быстрее |")
    print("|---|---|---|---|---|---|")
    for name, (x, y) in pairs.items():
        a = get_doc_ids(postings, x)
        b = get_doc_ids(postings, y)

        assert and_merge(a, b) == and_set(a, b)

        t_merge = measure_us(and_merge, a, b)
        t_set = measure_us(and_set, a, b)
        winner = "merge" if t_merge < t_set else "set"
        print(
            f"| {name} | {x}, {y} | {len(a)} и {len(b)} "
            f"| {t_merge:.2f} | {t_set:.2f} | {winner} |"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    args = parser.parse_args()
    main(args.root)
