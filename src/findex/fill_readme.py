"""Вимірює все на вашому корпусі й заповнює таблиці в README.md.

    python -m findex.fill_readme data/dota2wiki.jsonl README.md

Підставляє результат між маркерами <!-- BEGIN:name --> ... <!-- END:name -->,
тому скрипт можна запускати повторно.
"""

import argparse
import re
import sys
import time
import tracemalloc
from pathlib import Path

from findex.bench_search import measure_us
from findex.index import DocMeta, Posting, build_index
from findex.memory_study import PlainPosting, SlotPosting, VARIANTS, measure
from findex.search import and_merge, and_set, get_doc_ids
from findex.store import load, save


def sec_index(root: Path):
    tracemalloc.start()
    t0 = time.perf_counter()
    idx = build_index(root)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return (
        "| Документів | Унікальних термінів | Час побудови | Пік пам'яті |\n|---|---|---|---|\n"
        f"| {len(idx.doc_lengths):,} | {len(idx.postings):,} | {elapsed:.2f} с | {peak / 2**20:.2f} МБ |"
    ), idx


def sec_bench(idx) -> str:
    p = idx.postings
    by_len = sorted(p, key=lambda t: len(p[t]), reverse=True)
    common, rare = by_len[:2], by_len[-2:]
    pairs = {
        "частий + частий": (common[0], common[1]),
        "рідкий + рідкий": (rare[0], rare[1]),
        "частий + рідкий": (common[0], rare[0]),
    }
    out = ["| Пара | Терміни | Довжини списків | merge (мкс) | set (мкс) | Швидше |", "|---|---|---|---|---|---|"]
    for name, (x, y) in pairs.items():
        a, b = get_doc_ids(p, x), get_doc_ids(p, y)
        assert and_merge(a, b) == and_set(a, b)
        tm, ts = measure_us(and_merge, a, b), measure_us(and_set, a, b)
        out.append(f"| {name} | {x}, {y} | {len(a)} і {len(b)} | {tm:.2f} | {ts:.2f} | {'merge' if tm < ts else 'set'} |")
    return "\n".join(out)


def sec_store(idx) -> str:
    out = ["| Формат | Розмір файлу, МБ | Save, с | Load, с |", "|---|---|---|---|"]
    for fmt, label, name in [("pickle", "pickle (`HIGHEST_PROTOCOL`)", "_tmp_index.bin"), ("json", "json", "_tmp_index.json")]:
        path = Path(name)
        t0 = time.perf_counter(); save(idx, path, fmt); ts = time.perf_counter() - t0
        t0 = time.perf_counter(); loaded = load(path, fmt); tl = time.perf_counter() - t0
        assert loaded.postings == idx.postings and loaded.doc_lengths == idx.doc_lengths
        out.append(f"| {label} | {path.stat().st_size / 2**20:.2f} | {ts:.3f} | {tl:.3f} |")
        path.unlink()
    return "\n".join(out)


def sec_memory(root: Path):
    rows = [measure(n, b, root, Path("_m4_tmp.bin")) for n, b in VARIANTS.items()]
    out = ["| Postings representation | Peak memory (build), МБ | Index file size, МБ | Load time, с |", "|---|---|---|---|"]
    for name, peak, size, load_t, _ in rows:
        out.append(f"| {name} | {peak:.2f} | {size:.2f} | {load_t:.4f} |")
    base = rows[0][1]
    out.append("")
    out.append("Пік пам'яті порівняно з plain dataclass: "
               + ", ".join(f"{n}: **{base / pk:.2f}×** менше" for n, pk, *_ in rows[1:])
               + ".")
    return "\n".join(out), rows[0][4], rows


def sec_sizes(rows, n_post: int) -> str:
    """N× рахуємо з розмірів об'єктів та ВИМІРЯНОЇ різниці plain проти slots.

    getsizeof(obj.__dict__) не використовуємо: у сучасному CPython __dict__ створюється
    ліниво, і такий виклик завищив би вартість звичайного екземпляра.
    """
    slot_obj = sys.getsizeof(SlotPosting(100_000, 7))
    extra_plain = (rows[0][1] - rows[1][1]) * 2**20 / n_post  # plain мінус slots, байт/постінг
    slots_total = slot_obj + 8
    plain_total = slots_total + extra_plain
    return (
        "| Складова (байт на постінг) | Plain dataclass | slots | `array('I')` |\n|---|---|---|---|\n"
        f"| об'єкт Posting | {slot_obj} | {slot_obj} | — |\n"
        f"| додаткове в plain (виміряно: різниця пікових пам'ятей / к-сть постінгів) | ≈ {extra_plain:.0f} | — | — |\n"
        "| `doc_id` (`int`, 28 Б) | спільний на документ | спільний на документ | 4 |\n"
        "| `tf` (`int`) | 0 (малі числа кешуються) | 0 (кешуються) | 4 |\n"
        "| вказівник у списку | 8 | 8 | — |\n"
        f"| **разом на постінг (приблизно)** | **≈ {plain_total:.0f}** | **≈ {slots_total}** | **8** |\n\n"
        f"Звідси N ≈ {plain_total:.0f}/8 ≈ **{plain_total / 8:.0f}×** (plain проти array) і "
        f"≈ {slots_total}/8 ≈ **{slots_total / 8:.0f}×** (slots проти array) для самих даних постінгів. "
        "Виміряний пік пам'яті (таблиця вище) показує менші співвідношення, бо в ньому є спільні витрати: "
        "рядки термінів, словник, `Counter`, текст поточного документа та накладні витрати двох масивів "
        "на кожен термін. На малому корпусі вони домінують; з ростом корпусу виграш `array` наближається до N."
    )


def fill(text: str, name: str, body: str) -> str:
    pat = re.compile(rf"(<!-- BEGIN:{name} -->\n).*?(\n<!-- END:{name} -->)", re.S)
    assert pat.search(text), f"немає маркерів {name}"
    return pat.sub(lambda m: m.group(1) + body + m.group(2), text)


def main(root: Path, readme: Path) -> None:
    text = readme.read_text(encoding="utf-8")
    index_tbl, idx = sec_index(root)
    text = fill(text, "index", index_tbl)
    text = fill(text, "bench", sec_bench(idx))
    text = fill(text, "store", sec_store(idx))
    mem_tbl, n_post, rows = sec_memory(root)
    text = fill(text, "memory", mem_tbl + f"\n\nУсього постінгів: {n_post:,}.")
    text = fill(text, "sizes", sec_sizes(rows, n_post))
    readme.write_text(text, encoding="utf-8")
    print(f"Готово: {readme}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    ap.add_argument("readme", type=Path, nargs="?", default=Path("README.md"))
    a = ap.parse_args()
    main(a.root, a.readme)