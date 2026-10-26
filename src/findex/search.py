import argparse
import time
import tracemalloc
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from findex.index import Posting
from findex.store import load
from findex.tokenize import tokenize

type Postings = Mapping[str, Sequence[Posting]]
type MergeFn = Callable[[list[int], list[int]], list[int]]
type Engine = Literal["merge", "set"]


def get_doc_ids(postings: Postings, term: str) -> list[int]:
    return [p.doc_id for p in postings.get(term, [])]


def and_merge(a: list[int], b: list[int]) -> list[int]:
    result: list[int] = []
    i, j = 0, 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            result.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            i += 1
        else:
            j += 1
    return result


def or_merge(a: list[int], b: list[int]) -> list[int]:
    result: list[int] = []
    i, j = 0, 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            result.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            result.append(a[i])
            i += 1
        else:
            result.append(b[j])
            j += 1
    result.extend(a[i:])
    result.extend(b[j:])
    return result


def not_merge(a: list[int], b: list[int]) -> list[int]:
    result: list[int] = []
    i, j = 0, 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        elif a[i] < b[j]:
            result.append(a[i])
            i += 1
        else:
            j += 1
    result.extend(a[i:])
    return result


def and_set(a: list[int], b: list[int]) -> list[int]:
    return sorted(set(a) & set(b))


def or_set(a: list[int], b: list[int]) -> list[int]:
    return sorted(set(a) | set(b))


def not_set(a: list[int], b: list[int]) -> list[int]:
    return sorted(set(a) - set(b))


def term_ids(postings: Postings, word: str, and_fn: MergeFn) -> list[int]:
    toks = list(tokenize(word))
    if not toks:
        return []
    result = get_doc_ids(postings, toks[0])
    for t in toks[1:]:
        result = and_fn(result, get_doc_ids(postings, t))
    return result


def search(
    query: str, postings: Postings, doc_lengths: Mapping[int, int], engine: Engine = "merge"
) -> list[int]:
    tokens = query.split()
    if not tokens:
        return []

    fns: tuple[MergeFn, MergeFn, MergeFn] = (
        (and_set, or_set, not_set) if engine == "set" else (and_merge, or_merge, not_merge)
    )
    and_fn, or_fn, not_fn = fns

    all_docs = list(range(len(doc_lengths)))

    idx = 0
    if tokens[0].upper() == "NOT":
        if len(tokens) < 2:
            return []
        current_result = not_fn(all_docs, term_ids(postings, tokens[1], and_fn))
        idx = 2
    else:
        current_result = term_ids(postings, tokens[0], and_fn)
        idx = 1

    while idx < len(tokens):
        op = tokens[idx].upper()

        if op in ("AND", "OR"):
            idx += 1
            if idx >= len(tokens):
                break

            is_not = False
            if tokens[idx].upper() == "NOT":
                is_not = True
                idx += 1
                if idx >= len(tokens):
                    break

            next_ids = term_ids(postings, tokens[idx], and_fn)
            if is_not:
                next_ids = not_fn(all_docs, next_ids)

            if op == "AND":
                current_result = and_fn(current_result, next_ids)
            else:
                current_result = or_fn(current_result, next_ids)
            idx += 1

        elif op == "NOT":
            idx += 1
            if idx >= len(tokens):
                break
            current_result = not_fn(current_result, term_ids(postings, tokens[idx], and_fn))
            idx += 1

        else:
            current_result = and_fn(current_result, term_ids(postings, tokens[idx], and_fn))
            idx += 1

    return current_result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("index_path", type=Path, nargs="?", default=Path("index.bin"))
    parser.add_argument("query", type=str, nargs="?", default="dota AND hero")
    parser.add_argument("--engine", choices=["merge", "set"], default="merge")
    parser.add_argument("--format", choices=["pickle", "json"], default="pickle")
    args = parser.parse_args()

    tracemalloc.start()
    t0 = time.perf_counter()
    index = load(args.index_path, args.format)
    load_time = time.perf_counter() - t0

    t1 = time.perf_counter()
    matched_ids = search(args.query, index.postings, index.doc_lengths, engine=args.engine)
    search_time = time.perf_counter() - t1

    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"Знайдено: {len(matched_ids)}")
    for doc_id in matched_ids[:10]:
        meta = index.doc_meta[doc_id]
        print(f"  - [{doc_id}] {meta.title} ({meta.path})")

    print(f"Час завантаження: {load_time:.4f} с")
    print(f"Час пошуку: {search_time * 1000:.4f} мс")
    print(f"Пік пам'яті: {peak / (1024 * 1024):.2f} МБ")
