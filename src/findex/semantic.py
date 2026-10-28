"""Lab 08, M3: семантичний пошук (ембединги) і гібрид з BM25 через Reciprocal Rank Fusion.

    findex embed index.bin --model all-MiniLM-L6-v2        # -> index.emb.npz
    findex search index.bin "hero who can't be seen" --mode semantic

Документ ріжеться на шматки (chunks) по ~150 слів з перекриттям 30: MiniLM бачить лише 256
токенів, а один вектор на 4 000 слів — «каша» з усіх тем статті. Вектори L2-нормовані один раз,
тож косинус = скалярний добуток, і запит проти всіх шматків — одне множення матриці на вектор:

    sims = E @ q          # E: float32[n_chunks, 384], q: float32[384] -> float32[n_chunks]

Оцінка документа — його найкращий шматок (np.maximum.at). Brute force: 3.5 тис. шматків — це
частки мілісекунди; ANN-індекс (HNSW) потрібен від ~10⁶ векторів.

Модель — sentence-transformers/all-MiniLM-L6-v2 через fastembed (ONNX Runtime, без PyTorch):
та сама модель і ті самі ваги, але ~100 МБ залежностей замість ~700, тож вона влазить у
безкоштовний інстанс Render (512 МБ). Корпус англомовний — мультимовна модель не потрібна.
"""

import hashlib
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt

from findex.tokenize import WORD, tokenize

log = logging.getLogger(__name__)

type Vectors = npt.NDArray[np.float32]
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"  # README L8.6: на нашій оцінці краща за all-MiniLM-L6-v2
RRF_K = 60  # стандартна константа RRF (Cormack et al., 2009; так само в Elasticsearch)


class Embedder(Protocol):
    """Що завгодно, що перетворює тексти на вектори. Тести підставляють детермінований фейк."""

    model: str

    def embed(self, texts: Sequence[str]) -> Vectors: ...  # (n, d) — шматки документів

    def embed_query(self, text: str) -> Vectors: ...  # (d,) — запит; асиметричні моделі додають інструкцію


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_corpus(sem: "SemanticIndex", corpus: Path) -> None:
    """Вектори прив'язані до doc_id, а doc_id — до порядку рядків корпусу. Інший корпус з тією ж
    кількістю документів дав би правдоподібні, але чужі результати — тому порівнюємо відбиток."""
    if sem.corpus_sha256 and sem.corpus_sha256 != file_sha256(corpus):
        raise ValueError(f"ембединги пораховано для іншого корпусу, ніж {corpus}: перерахуйте findex embed")


def default_path(index_path: Path) -> Path:
    """index.bin -> index.emb.npz: ембединги лежать поруч з індексом, для якого пораховані."""
    return index_path.with_suffix(".emb.npz")


def model_name(name: str) -> str:
    """'all-MiniLM-L6-v2' -> 'sentence-transformers/all-MiniLM-L6-v2' (ім'я з завдання -> ім'я fastembed)."""
    return name if "/" in name else f"sentence-transformers/{name}"


class FastEmbedder:
    """fastembed.TextEmbedding з ледачим імпортом: без семантичного режиму onnxruntime не вантажиться."""

    def __init__(
        self, model: str = DEFAULT_MODEL, cache_dir: Path | str | None = None, threads: int | None = None
    ):
        from fastembed import TextEmbedding

        self.model = model_name(model)
        self._m = TextEmbedding(self.model, cache_dir=str(cache_dir) if cache_dir else None, threads=threads)

    def embed(self, texts: Sequence[str]) -> Vectors:
        return np.asarray(list(self._m.embed(list(texts), batch_size=32)), dtype=np.float32)

    def embed_query(self, text: str) -> Vectors:
        """bge навчено на парах (інструкція + запит, абзац): запит отримує префікс «Represent this
        sentence for searching relevant passages: », документи — ні. Для MiniLM це те саме, що embed."""
        return np.asarray(next(iter(self._m.query_embed(text))), dtype=np.float32)


# --- шматки --------------------------------------------------------------------------------------
def chunk_spans(text: str, words: int = 150, overlap: int = 30) -> list[tuple[int, int]]:
    """(start, end) шматків у символах: по `words` слів, кожен наступний починається на
    `words - overlap` слів пізніше. Межі — по словах, тож шматок не ріже слово навпіл."""
    spans = [(m.start(), m.end()) for m in WORD.finditer(text)]
    if not spans:
        return []
    step = max(1, words - overlap)
    out: list[tuple[int, int]] = []
    for i in range(0, len(spans), step):
        last = min(i + words, len(spans)) - 1
        out.append((spans[i][0], spans[last][1]))
        if last == len(spans) - 1:
            break
    return out


def normalized(v: npt.NDArray[np.floating]) -> Vectors:
    """Одиничні вектори: тоді cos(a, b) = a·b. Нульовий вектор лишається нульовим."""
    v32 = np.asarray(v, dtype=np.float32)
    norms = np.linalg.norm(v32, axis=-1, keepdims=True)
    return (v32 / np.where(norms == 0, 1, norms)).astype(np.float32, copy=False)


@dataclass(frozen=True, slots=True, eq=False)
class SemanticIndex:
    vectors: Vectors  # float32[n, d], L2-нормовані
    chunk_doc: npt.NDArray[np.int32]  # шматок -> doc_id
    chunk_span: npt.NDArray[np.int32]  # шматок -> (start, end) у тексті документа
    num_docs: int
    model: str
    corpus_sha256: str = ""  # відбиток корпусу: ембединги від іншого корпусу не вантажаться

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1])

    @property
    def docs_with_chunks(self) -> int:
        return len(np.unique(self.chunk_doc))

    def save(self, path: Path) -> None:
        np.savez(  # без стиснення: float-и стискаються погано, а розпакування — зайвий час на старті
            path,
            vectors=self.vectors,
            chunk_doc=self.chunk_doc,
            chunk_span=self.chunk_span,
            num_docs=np.int64(self.num_docs),
            model=np.str_(self.model),
            corpus_sha256=np.str_(self.corpus_sha256),
        )

    @classmethod
    def load(cls, path: Path) -> "SemanticIndex":
        with np.load(path, allow_pickle=False) as z:  # allow_pickle=False: файл не може виконати код
            sha = str(z["corpus_sha256"]) if "corpus_sha256" in z.files else ""
            return cls(
                z["vectors"], z["chunk_doc"], z["chunk_span"], int(z["num_docs"]), str(z["model"]), sha
            )

    def doc_scores(self, q: Vectors) -> npt.NDArray[np.float64]:
        """Оцінка кожного документа = косинус його найкращого шматка; документ без шматків — -inf."""
        sims = self.vectors @ q  # (n,) — усі косинуси одним викликом BLAS
        best = np.full(self.num_docs, -np.inf, dtype=np.float64)
        steps = np.diff(self.chunk_doc, prepend=-1)
        if not len(steps) or steps.min() >= 0:  # неспадний chunk_doc (усередині документа крок 0)
            # Шматки документа йдуть підряд (так їх пише build): максимум по сегментах — reduceat.
            starts = np.flatnonzero(steps)
            best[self.chunk_doc[starts]] = np.maximum.reduceat(sims, starts) if len(starts) else []
        else:
            # Довільний порядок: .at, бо doc_id повторюються (best[ids] = ... лишив би останнє значення).
            # Він небуферизований і в 38 разів повільніший за reduceat (363 проти 9 мкс, L8.6).
            np.maximum.at(best, self.chunk_doc, sims)
        return best

    def best_chunk(self, q: Vectors, doc_id: int) -> tuple[int, int] | None:
        """(start, end) найкращого шматка документа — для сніпета, коли ключових слів у ньому немає."""
        rows = np.flatnonzero(self.chunk_doc == doc_id)
        if not len(rows):
            return None
        start, end = self.chunk_span[rows[int(np.argmax(self.vectors[rows] @ q))]]
        return int(start), int(end)

    def search(self, q: Vectors, k: int) -> list[tuple[int, float]]:
        from findex.vector import top_k

        scores = self.doc_scores(q)
        return top_k(scores, np.flatnonzero(np.isfinite(scores)).astype(np.int32), k)


def build(
    texts: Iterable[tuple[int, str]],
    num_docs: int,
    embedder: Embedder,
    *,
    words: int = 150,
    overlap: int = 30,
    batch: int = 256,
    on_progress: Callable[[int], None] | None = None,
) -> SemanticIndex:
    """(doc_id, текст) -> шматки -> вектори. on_progress(n) — скільки шматків щойно пораховано."""
    docs: list[int] = []
    spans: list[tuple[int, int]] = []
    pieces: list[str] = []
    for doc_id, text in texts:
        for s, e in chunk_spans(text, words, overlap):
            docs.append(doc_id)
            spans.append((s, e))
            pieces.append(text[s:e])
    parts: list[Vectors] = []
    for i in range(0, len(pieces), batch):
        parts.append(embedder.embed(pieces[i : i + batch]))
        if on_progress:
            on_progress(len(pieces[i : i + batch]))
    vectors = normalized(np.concatenate(parts)) if parts else np.empty((0, 0), dtype=np.float32)
    log.info("embedded %d chunks of %d docs with %s", len(pieces), num_docs, embedder.model)
    return SemanticIndex(
        vectors,
        np.asarray(docs, dtype=np.int32),
        np.asarray(spans, dtype=np.int32).reshape(-1, 2),
        num_docs,
        embedder.model,
    )


# --- запит і злиття -------------------------------------------------------------------------------
_OPERATORS = re.compile(r'\b(?:AND|OR|NOT)\b|["()]')


def semantic_text(query: str) -> str:
    """Мова запитів -> звичайний текст для моделі: оператори й лапки моделі нічого не кажуть.
    (NOT при цьому губиться: «stun NOT slow» стає «stun slow» — семантика заперечень не вміє.)"""
    return " ".join(_OPERATORS.sub(" ", query).split())


def bag_of_words(query: str) -> str:
    """Ключова половина гібриду. Явні оператори чи фрази — запит як є; звичайна фраза природною
    мовою — OR усіх слів, тобто BM25 по «мішку слів», як у гібридних системах (Elasticsearch,
    Vespa). Неявний AND мови запитів на такому запиті майже завжди дає порожньо."""
    if _OPERATORS.search(query):
        return query
    words = list(dict.fromkeys(tokenize(query)))
    return " OR ".join(words) if words else query


def rrf(rankings: Iterable[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: score(d) = Σ 1 / (k + rank_i(d)), rank з 1. Ваги — лише від місць,
    тож BM25 (0..20) і косинус (0..1) не треба зводити до однієї шкали."""
    fused: defaultdict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, doc in enumerate(ranking, start=1):
            fused[doc] += 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
