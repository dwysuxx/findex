"""M1 (Lab 03): індекс як об'єкт, що поводиться як Python.

Index — це Mapping[str, list[Posting]]: len(), in, [], for, repr() працюють
через dunder-методи. Для сумісності з Lab 02 лишаються атрибути
`postings`, `doc_lengths`, `doc_meta`.
"""

import time
import tracemalloc
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from findex.corpus import iter_documents
from findex.tokenize import tokenize
from findex.utils import timed


@dataclass(frozen=True, slots=True)
class Posting:
    doc_id: int
    tf: int
    positions: tuple[int, ...] = ()  # зміщення токенів; порожньо, якщо індекс без positions


@dataclass(frozen=True, slots=True)
class DocMeta:
    title: str
    path: str


class Index(Mapping[str, list[Posting]]):
    """Інвертований індекс: терм -> постінги, відсортовані за doc_id."""

    def __init__(
        self,
        postings: dict[str, list[Posting]],
        doc_lengths: dict[int, int],
        doc_meta: dict[int, DocMeta],
        has_positions: bool = False,
    ) -> None:
        # defaultdict тут небезпечний: index[term] для відсутнього терміна створював би запис
        self.postings = postings if type(postings) is dict else dict(postings)
        self.doc_lengths = doc_lengths
        self.doc_meta = doc_meta
        self.has_positions = has_positions
        self._closed = False

    # --- протокол Mapping: достатньо трьох методів, решта (get, keys, items...) безкоштовно
    def __getitem__(self, term: str) -> list[Posting]:
        return self.postings[term]  # KeyError, якщо терміна немає

    def __len__(self) -> int:
        return len(self.postings)

    def __iter__(self) -> Iterator[str]:
        return iter(self.postings)

    def __contains__(self, term: object) -> bool:
        return term in self.postings  # швидше за версію з Mapping (без створення списку)

    def __repr__(self) -> str:
        if self._closed:
            return "Index(closed)"
        extra = ", positions=True" if self.has_positions else ""
        return f"Index(terms={len(self):_}, docs={self.num_docs:_}{extra})"

    # --- обчислювані атрибути
    @property
    def num_docs(self) -> int:
        return len(self.doc_lengths)

    @cached_property
    def avg_doc_length(self) -> float:
        """Рахується один раз на індекс, а не на кожен оцінюваний документ."""
        if not self.doc_lengths:
            return 0.0
        return sum(self.doc_lengths.values()) / self.num_docs

    def doc_length(self, doc_id: int) -> int:
        return self.doc_lengths[doc_id]

    def df(self, term: str) -> int:
        """Document frequency: у скількох документах зустрічається терм."""
        return len(self.postings.get(term, ()))

    # --- життєвий цикл
    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Звільняє дані індексу. Викликається з open_index, навіть якщо блок кинув виняток."""
        self._closed = True
        self.postings = {}
        self.doc_lengths = {}
        self.doc_meta = {}
        self.__dict__.pop("avg_doc_length", None)


@contextmanager
def open_index(path: Path | str, fmt: str = "pickle"):
    """with open_index(p) as ix: ... — завантажує індекс і гарантовано закриває його.

    Усе до `yield` — це __enter__, усе після — __exit__; `finally` виконається
    і при return, і при винятку в тілі with.
    """
    from findex.store import load  # імпорт всередині: store імпортує index

    index = load(Path(path), fmt)
    try:
        yield index
    finally:
        index.close()


@timed
def build_index(root: Path, positions: bool = False) -> Index:
    """Будує індекс за один потоковий прохід. positions=True додає зміщення токенів
    (потрібні для фразових запитів; індекс стає помітно більшим)."""
    postings: dict[str, list[Posting]] = defaultdict(list)
    doc_lengths: dict[int, int] = {}
    doc_meta: dict[int, DocMeta] = {}

    for internal_id, doc in enumerate(iter_documents(root)):
        tokens = list(tokenize(doc.text or ""))
        doc_lengths[internal_id] = len(tokens)
        doc_meta[internal_id] = DocMeta(title=doc.doc_id, path=str(doc.path))

        if positions:
            by_term: dict[str, list[int]] = defaultdict(list)
            for offset, tok in enumerate(tokens):
                by_term[tok].append(offset)
            for term, offsets in by_term.items():
                postings[term].append(Posting(internal_id, len(offsets), tuple(offsets)))
        else:
            for term, tf in Counter(tokens).items():
                postings[term].append(Posting(internal_id, tf))

    return Index(postings, doc_lengths, doc_meta, has_positions=positions)


def load_or_build(path: Path | str, fmt: str = "pickle", positions: bool = False) -> Index:
    """.jsonl -> побудувати індекс; інакше завантажити збережений файл."""
    path = Path(path)
    if path.suffix == ".jsonl":
        return build_index(path, positions=positions)
    from findex.store import load

    return load(path, fmt)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    parser.add_argument("--positions", action="store_true", help="зберігати positions (для фраз)")
    parser.add_argument("--out", type=Path, default=None, help="зберегти індекс у файл")
    parser.add_argument("--format", choices=["pickle", "json"], default="pickle")
    args = parser.parse_args()

    # `python -m findex.index` запускає цей файл як __main__, і pickle запам'ятав би класи як
    # __main__.Index — такий файл не завантажиться з іншого модуля. Тому будуємо через
    # канонічний імпорт findex.index (там класи мають правильне ім'я модуля).
    from findex.index import build_index as canonical_build

    tracemalloc.start()
    t0 = time.perf_counter()
    idx = canonical_build(args.root, positions=args.positions)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(repr(idx))
    print(f"Час побудови: {elapsed:.2f} с")
    print(f"Пік пам'яті: {peak / (1024 * 1024):.2f} МБ")

    if args.out:
        from findex.store import save

        save(idx, args.out, args.format)
        print(f"Збережено: {args.out} ({args.out.stat().st_size / 2**20:.2f} МБ)")
