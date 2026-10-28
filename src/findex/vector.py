"""Lab 08, M2: постінги як масиви NumPy і ранжування без Python-циклу по документах.

Розкладка CSR (compressed sparse row), як у scipy.sparse: усі постінги лежать підряд у двох
масивах, а терм — це зріз [offsets[t], offsets[t+1]):

    terms   = {"pudge": 0, "hook": 1, ...}                    # терм -> рядок
    offsets = int64[V+1]     [0, 4, 9, ...]
    doc_ids = int32[P]       [13, 40, 77, 101,  2, 13, ...]   # усередині рядка — за зростанням
    tfs     = int32[P]       [ 96,  1,  2,   1,  5,  3, ...]
    doc_len = int32[N]

Зріз — це view (без копії), тож `postings(term)` коштує O(1). int32 замість int64 удвічі
зменшує байти, а суцільні масиви читаються з кешу процесора потоком.

Дерево запиту рахується масками (bool[N]) замість множин: Term — `mask[ids] = True`, And — `&`,
Or — `|`, Not — `~`. Ті самі оператори, що Node перевантажує в query.py, але над масивами.
Фрази потребують positions — вони обчислюються через Index і перетворюються на маску.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from functools import singledispatch
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt

from findex.index import Index
from findex.query import And, Node, Not, Or, Term

type IntArray = npt.NDArray[np.int32]
type FloatArray = npt.NDArray[np.float64]
type BoolArray = npt.NDArray[np.bool_]

_EMPTY = np.empty(0, dtype=np.int32)


@dataclass(frozen=True, slots=True, eq=False)
class VectorIndex:
    """Постінги Index у CSR-масивах (без positions) + довжини документів."""

    terms: dict[str, int]
    offsets: npt.NDArray[np.int64]
    doc_ids: IntArray
    tfs: IntArray
    doc_len: IntArray
    avg_doc_length: float  # саме той float, що й Index.avg_doc_length (див. from_index)
    index: Index  # для фраз (positions) і doc_meta

    @classmethod
    def from_index(cls, index: Index) -> "VectorIndex":
        """Один прохід по постінгах. doc_id мають бути 0..N-1 підряд (так їх видає index_documents)."""
        n = index.num_docs
        if sorted(index.doc_lengths) != list(range(n)):
            raise ValueError("VectorIndex потребує doc_id 0..N-1 підряд")
        terms = {term: row for row, term in enumerate(index.postings)}
        lengths = np.fromiter((len(p) for p in index.postings.values()), dtype=np.int64, count=len(terms))
        offsets = np.zeros(len(terms) + 1, dtype=np.int64)
        np.cumsum(lengths, out=offsets[1:])
        total = int(offsets[-1])
        plists = index.postings.values()
        doc_ids = np.fromiter((p.doc_id for pl in plists for p in pl), dtype=np.int32, count=total)
        tfs = np.fromiter((p.tf for pl in plists for p in pl), dtype=np.int32, count=total)
        doc_len = np.fromiter((index.doc_lengths[d] for d in range(n)), dtype=np.int32, count=n)
        # avg — з Index, а не doc_len.mean(): інший порядок підсумовування дав би інший останній біт,
        # і «ідентичне ранжування» з Lab 03 трималося б лише приблизно.
        return cls(terms, offsets, doc_ids, tfs, doc_len, index.avg_doc_length, index)

    @property
    def num_docs(self) -> int:
        return len(self.doc_len)

    def postings(self, term: str) -> tuple[IntArray, IntArray]:
        """(doc_ids, tfs) терма — view у спільні масиви; відсутній терм — порожні масиви."""
        row = self.terms.get(term)
        if row is None:
            return _EMPTY, _EMPTY
        lo, hi = int(self.offsets[row]), int(self.offsets[row + 1])
        return self.doc_ids[lo:hi], self.tfs[lo:hi]

    def df(self, term: str) -> int:
        row = self.terms.get(term)
        return 0 if row is None else int(self.offsets[row + 1] - self.offsets[row])

    # --- збереження: np.save + memmap (Lab 02 Stretch). Ранжування цього не потребує, це заміри.
    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("offsets", "doc_ids", "tfs", "doc_len"):
            np.save(directory / f"{name}.npy", getattr(self, name))

    @staticmethod
    def load_arrays(directory: Path, mmap: bool = True) -> dict[str, npt.NDArray[np.generic]]:
        """mmap=True: файли відображаються в пам'ять, сторінки читаються з диска при доступі."""
        mode = "r" if mmap else None
        return {
            name: np.load(directory / f"{name}.npy", mmap_mode=mode)
            for name in ("offsets", "doc_ids", "tfs", "doc_len")
        }


# --- вагові функції: ті самі формули, що в ranking.py, але над масивом постінгів ---------------
class BatchScorer(Protocol):
    """Scorer, що вміє оцінити всі постінги терма одним виразом. Його мають TfIdf і BM25."""

    def batch(
        self, tfs: IntArray, doc_len: IntArray, df: int, num_docs: int, avg_len: float
    ) -> FloatArray: ...


def supports_batch(scorer: object) -> bool:
    return callable(getattr(scorer, "batch", None))


# --- дерево запиту -> маска --------------------------------------------------------------------
@singledispatch
def mask(node: Node, vx: VectorIndex) -> BoolArray:
    """Загальний випадок (Phrase і будь-який майбутній вузол): множина з Index -> маска."""
    out = np.zeros(vx.num_docs, dtype=np.bool_)
    out[np.fromiter(node.evaluate(vx.index), dtype=np.int64)] = True
    return out


@mask.register
def _(node: Term, vx: VectorIndex) -> BoolArray:
    out = np.zeros(vx.num_docs, dtype=np.bool_)
    out[vx.postings(node.text)[0]] = True  # fancy indexing: усі doc_id терма за один виклик
    return out


@mask.register
def _(node: And, vx: VectorIndex) -> BoolArray:
    return mask(node.left, vx) & mask(node.right, vx)


@mask.register
def _(node: Or, vx: VectorIndex) -> BoolArray:
    return mask(node.left, vx) | mask(node.right, vx)


@mask.register
def _(node: Not, vx: VectorIndex) -> BoolArray:
    return ~mask(node.operand, vx)


# --- ранжування ---------------------------------------------------------------------------------
def score_all(vx: VectorIndex, terms: Iterable[str], scorer: BatchScorer) -> FloatArray:
    """scores[d] = Σ по термах вага(терм, d) для всіх N документів.

    Порядок додавання — той самий, що в ranking.rank (терми по черзі), тож суми побітово рівні.
    doc_id усередині терма унікальні, тому `scores[ids] += w` коректне; з повторами потрібен
    був би np.add.at (буферизоване += записало б лише останнє значення).
    """
    scores = np.zeros(vx.num_docs, dtype=np.float64)
    for term in dict.fromkeys(terms):
        ids, tfs = vx.postings(term)
        if len(ids):
            scores[ids] += scorer.batch(tfs, vx.doc_len[ids], len(ids), vx.num_docs, vx.avg_doc_length)
    return scores


def top_k(scores: FloatArray, candidates: IntArray, k: int) -> list[tuple[int, float]]:
    """Top-k кандидатів за (score ↓, doc_id ↑) — те саме правило, що heapq.nlargest у Lab 03.

    np.argpartition — O(n): ставить k-й елемент на своє місце, а більші — перед ним, без сортування
    решти. Сортуємо лише k обраних. Але серед рівних на межі argpartition обирає довільно, тож
    межу добираємо явно: усі > порогу + найменші doc_id серед == порогу.
    """
    s = scores[candidates]
    if k < len(s):
        threshold = s[np.argpartition(-s, k - 1)[k - 1]]  # k-те найбільше значення
        above = np.flatnonzero(s > threshold)
        tied = np.flatnonzero(s == threshold)[: k - len(above)]  # candidates зростають => менші doc_id
        keep = np.concatenate([above, tied])
    else:
        keep = np.arange(len(s))
    order = keep[np.lexsort((candidates[keep], -s[keep]))]  # остання клавіша — головна
    return list(zip(candidates[order].tolist(), s[order].tolist(), strict=True))


def rank(vx: VectorIndex, matched: BoolArray, terms: Iterable[str], scorer: BatchScorer, k: int = 10):
    """Аналог ranking.rank: matched — маска замість множини."""
    candidates = np.flatnonzero(matched).astype(np.int32)
    return top_k(score_all(vx, terms, scorer), candidates, k), len(candidates)
