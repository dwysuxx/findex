"""Lab 05: паралельна побудова індексу як map-reduce.

    plan_chunks   — батько: один прохід по байтах файлу, ділить рядки на N шматків
                    приблизно однакового розміру і НАПЕРЕД призначає кожному діапазон doc_id;
    build_partial — map: воркер сам читає свій байтовий діапазон і будує частковий індекс.
                    Через межу процесу туди йде лише Chunk (шлях + три числа), назад — постінги;
    merge         — reduce: батько склеює часткові індекси послідовно (це і є Amdahl-частина).

Один код для трьох виконавців: звичайний цикл, ThreadPoolExecutor, ProcessPoolExecutor.
build_partial — функція рівня модуля: під `spawn` воркер імпортує findex.parallel за іменем,
а лямбду чи вкладену функцію pickle передати не може.

Податок на передачу (виміряно, README Lab 05): pickle часткового індексу з positions коштує
0.33 с dump + 0.39 с load на 0.54 с побудови — пересилати дорожче, ніж рахувати, і load
платить батько, послідовно. Тому процеси за замовчуванням повертають «компактний» результат
(transport="compact"): пласкі списки int через marshal, а Posting збирає батько (_decode).
"""

import logging
import marshal
import os
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path
from typing import Literal, cast

from findex.corpus import Document, parse_line
from findex.index import DocMeta, Index, Posting, Postings, index_documents
from findex.utils import gc_paused, peak_rss_bytes

log = logging.getLogger(__name__)

type ExecutorName = Literal["serial", "threads", "processes"]
EXECUTORS: tuple[ExecutorName, ...] = ("serial", "threads", "processes")
type Transport = Literal["compact", "pickle"]


@dataclass(frozen=True, slots=True)
class Chunk:
    """Шматок корпусу: байтовий діапазон [start, end) і наперед призначені doc_id."""

    path: Path
    start: int
    end: int
    first_line: int  # номер першого рядка діапазону (з 1) — для попереджень про биті рядки
    first_id: int  # doc_id першого документа шматка
    slots: int  # непорожніх рядків у шматку = скільки doc_id зарезервовано


@dataclass(frozen=True, slots=True)
class WorkerStats:
    """Що виміряв воркер про себе. process_cpu/peak_rss — на весь процес, не лише на задачу."""

    pid: int
    seconds: float  # wall-час задачі
    task_cpu: float  # CPU цього потоку на задачу (thread_time)
    process_cpu: float  # CPU усього процесу від старту, включно з імпортами (process_time)
    peak_rss: int  # пік RSS процесу, байти


@dataclass(slots=True)
class PartialIndex:
    """Результат map-кроку: постінги одного шматка з глобальними doc_id."""

    chunk: Chunk
    postings: Postings
    doc_lengths: dict[int, int]
    doc_meta: dict[int, DocMeta]
    has_positions: bool
    stats: WorkerStats

    @property
    def num_docs(self) -> int:
        return len(self.doc_lengths)


def plan_chunks(corpus: Path, n: int, limit: int | None = None) -> list[Chunk]:
    """Ділить непорожні рядки корпусу (перші `limit`) на ≤ n шматків, збалансованих за байтами.

    Документи бувають у десятки разів довші за інші, тому ділимо за розміром, а не за кількістю.
    Байтовий прохід без розбору JSON — дешевий (≈ швидкість читання диска).
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    lines: list[tuple[int, int, int]] = []  # (start, end, line_number) непорожніх рядків
    offset = 0
    with corpus.open("rb") as f:
        for line_number, raw in enumerate(f, start=1):
            if raw.strip():
                if limit is not None and len(lines) >= limit:
                    break
                lines.append((offset, offset + len(raw), line_number))
            offset += len(raw)
    if not lines:
        return []

    n = min(n, len(lines))
    base = lines[0][0]
    total = lines[-1][1] - base
    chunks: list[Chunk] = []
    first = 0  # індекс першого рядка поточного шматка в `lines`
    for i, (_, end, _) in enumerate(lines):
        remaining_lines = len(lines) - i - 1
        remaining_chunks = n - len(chunks) - 1
        enough_bytes = end - base >= total * (len(chunks) + 1) / n
        if i == len(lines) - 1 or (
            remaining_chunks > 0 and (enough_bytes or remaining_lines == remaining_chunks)
        ):
            chunks.append(
                Chunk(corpus, lines[first][0], end, lines[first][2], first_id=first, slots=i - first + 1)
            )
            first = i + 1
    return chunks


def _chunk_documents(chunk: Chunk) -> Iterator[Document]:
    with chunk.path.open("rb") as f:
        f.seek(chunk.start)
        data = f.read(chunk.end - chunk.start)
    # Розбиваємо байти, а не текст: у UTF-8 байт \n не трапляється всередині багатобайтового символу.
    for line_number, raw in enumerate(data.split(b"\n"), start=chunk.first_line):
        doc = parse_line(raw.decode("utf-8", errors="replace"), chunk.path, line_number)
        if doc is not None:
            yield doc


def build_partial(chunk: Chunk, positions: bool = False) -> PartialIndex:
    """Map-крок: частковий індекс одного шматка. Без спільного стану — лише локальні змінні."""
    t0, cpu0 = time.perf_counter(), time.thread_time()
    with gc_paused():
        postings, doc_lengths, doc_meta = index_documents(
            _chunk_documents(chunk), positions=positions, first_id=chunk.first_id
        )
    stats = WorkerStats(
        pid=os.getpid(),
        seconds=time.perf_counter() - t0,
        task_cpu=time.thread_time() - cpu0,
        process_cpu=time.process_time(),
        peak_rss=peak_rss_bytes(),
    )
    return PartialIndex(chunk, postings, doc_lengths, doc_meta, positions, stats)


type _Wire = tuple[
    bool,  # has_positions
    list[tuple[str, list[int], list[int], list[int]]],  # (term, doc_ids, tfs, усі positions підряд)
    list[tuple[int, int, str, str]],  # (doc_id, length, title, path)
    tuple[int, float, float, float, int],  # WorkerStats
]


def build_partial_compact(chunk: Chunk, positions: bool = False) -> bytes:
    """build_partial для процесів: результат як marshal-байти лише з int/str/list/tuple.

    marshal не викликає __reduce__ для кожного Posting і не тягне ім'я класу — у ~5 разів
    дешевше за pickle на обох кінцях. Кодування — частина задачі, тож його CPU у task_cpu.
    """
    t0, cpu0 = time.perf_counter(), time.thread_time()
    part = build_partial(chunk, positions)
    terms = [
        (term, [p.doc_id for p in pl], [p.tf for p in pl], [x for p in pl for x in p.positions])
        for term, pl in part.postings.items()
    ]
    docs = [(d, n, part.doc_meta[d].title, part.doc_meta[d].path) for d, n in part.doc_lengths.items()]
    stats = (
        os.getpid(),
        time.perf_counter() - t0,
        time.thread_time() - cpu0,
        time.process_time(),
        peak_rss_bytes(),
    )
    wire: _Wire = (positions, terms, docs, stats)
    return marshal.dumps(wire)


def _decode(chunk: Chunk, blob: bytes) -> PartialIndex:
    """Батьківський бік компактного транспорту: байти -> PartialIndex з об'єктами Posting."""
    has_positions, terms, docs, (pid, seconds, task_cpu, process_cpu, rss) = cast(_Wire, marshal.loads(blob))
    postings: Postings = {}
    for term, ids, tfs, flat in terms:
        if has_positions:
            it = iter(flat)
            postings[term] = [Posting(d, tf, tuple(islice(it, tf))) for d, tf in zip(ids, tfs, strict=True)]
        else:
            postings[term] = [Posting(d, tf) for d, tf in zip(ids, tfs, strict=True)]
    return PartialIndex(
        chunk,
        postings,
        {d: n for d, n, _, _ in docs},
        {d: DocMeta(title, path) for d, _, title, path in docs},
        has_positions,
        WorkerStats(pid, seconds, task_cpu, process_cpu, rss),
    )


def _shift(part: PartialIndex, delta: int) -> PartialIndex:
    """Перенумерувати doc_id на delta (лише коли попередні шматки мали биті рядки)."""
    return replace(
        part,
        postings={t: [replace(p, doc_id=p.doc_id + delta) for p in pl] for t, pl in part.postings.items()},
        doc_lengths={d + delta: n for d, n in part.doc_lengths.items()},
        doc_meta={d + delta: m for d, m in part.doc_meta.items()},
    )


def merge(partials: Iterable[PartialIndex]) -> Index:
    """Reduce-крок: часткові індекси -> один Index.

    doc_id призначено шматкам наперед і впорядковано, тож «сортоване злиття» постінгів зводиться
    до конкатенації в порядку шматків. Порядок термів у dict — порядок першої появи, як у
    послідовній побудові, тому результат ідентичний build_index (аж до байтів pickle).

    merge забирає списки постінгів у власність: після нього partials не використовувати.
    """
    parts = sorted(partials, key=lambda p: p.chunk.first_id)  # as_completed повертає будь-як
    if len({p.has_positions for p in parts}) > 1:
        raise ValueError("partials disagree on has_positions")

    postings: Postings = {}
    doc_lengths: dict[int, int] = {}
    doc_meta: dict[int, DocMeta] = {}
    next_id = 0
    for part in parts:
        if part.chunk.first_id != next_id:
            # У попередньому шматку був битий рядок: зарезервований doc_id лишився б «діркою».
            log.info(
                "chunk at line %d: renumbering doc_ids by %+d",
                part.chunk.first_line,
                next_id - part.chunk.first_id,
            )
            part = _shift(part, next_id - part.chunk.first_id)
        for term, plist in part.postings.items():
            existing = postings.get(term)
            if existing is None:
                postings[term] = plist  # без копії: список часткового індексу стає нашим
            else:
                existing.extend(plist)
        doc_lengths.update(part.doc_lengths)
        doc_meta.update(part.doc_meta)
        next_id += part.num_docs
    has_positions = parts[0].has_positions if parts else False
    return Index(postings, doc_lengths, doc_meta, has_positions=has_positions)


AUTO_PROCESSES_MIN_BYTES = 4 * 2**20  # беззбитковість: 3.1 МБ — нічия (1.00×), 6.3 МБ — 1.3×


def gil_enabled() -> bool:
    """False лише на free-threaded збірці (3.13t/3.14t), де GIL справді вимкнено під час виконання."""
    is_enabled = getattr(sys, "_is_gil_enabled", None)  # з'явилась у 3.13
    return True if is_enabled is None else bool(is_enabled())


def choose_executor(corpus_bytes: int, gil: bool | None = None) -> ExecutorName:
    """`--executor auto`, за бенчмарком (README, Lab 05).

    Без GIL потоки виграють завжди: паралельні, як процеси, але без пересилання результатів
    (3.14t, ×8: 5.0× проти 2.1× у процесів). З GIL потоки марні, а процеси окупаються, лише коли
    робота довша за запуск пулу і декодування в батька: на 3.1 МБ — нічия, з 6 МБ — виграш,
    на 50 МБ — 1.9×.
    """
    if not (gil_enabled() if gil is None else gil):
        return "threads"
    return "processes" if corpus_bytes >= AUTO_PROCESSES_MIN_BYTES else "serial"


def _make_pool(executor: ExecutorName, workers: int) -> Executor | None:
    match executor:
        case "serial":
            return None
        case "threads":
            return ThreadPoolExecutor(max_workers=workers, thread_name_prefix="findex")
        case "processes":
            return ProcessPoolExecutor(max_workers=workers)


def map_chunks(
    chunks: list[Chunk],
    executor: ExecutorName = "processes",
    workers: int = 1,
    positions: bool = False,
    on_done: Callable[[PartialIndex], None] | None = None,
    transport: Transport = "compact",
) -> list[PartialIndex]:
    """Виконати build_partial для кожного шматка обраним виконавцем; порядок результатів довільний.

    Виняток воркера піднімається тут же, з оригінальним traceback (для процесів — у __cause__),
    а ще не розпочаті задачі скасовуються — ніякого зависання на решті шматків.
    transport стосується лише процесів: потоки ділять пам'ять, їм нічого не треба пересилати.
    """
    pool = _make_pool(executor, workers)
    results: list[PartialIndex] = []
    if pool is None:
        for chunk in chunks:
            results.append(build_partial(chunk, positions))
            if on_done:
                on_done(results[-1])
        return results

    compact = executor == "processes" and transport == "compact"
    with pool:
        task: Callable[[Chunk, bool], PartialIndex | bytes] = (
            build_partial_compact if compact else build_partial
        )
        futures: dict[Future[PartialIndex | bytes], Chunk] = {
            pool.submit(task, c, positions): c for c in chunks
        }
        try:
            for fut in as_completed(futures):
                result = fut.result()  # re-raise винятку воркера
                # Декодуємо, поки воркери рахують наступні шматки: для цього шматків більше, ніж воркерів.
                results.append(_decode(futures[fut], result) if isinstance(result, bytes) else result)
                if on_done:
                    on_done(results[-1])
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    return results


CHUNKS_PER_PROCESS = 4


@dataclass(frozen=True, slots=True)
class BuildReport:
    """Індекс плюс розклад часу побудови — для CLI (-v) і бенчмарку."""

    index: Index
    executor: ExecutorName
    workers: int
    chunks: int
    plan_s: float
    map_s: float
    merge_s: float
    worker_stats: tuple[WorkerStats, ...]

    @property
    def child_cpu(self) -> float:
        """CPU процесів-воркерів (інтерпретатор, імпорти, задачі); для serial/threads — 0."""
        me = os.getpid()
        per_pid: dict[int, float] = {}
        for s in self.worker_stats:
            if s.pid != me:
                per_pid[s.pid] = max(per_pid.get(s.pid, 0.0), s.process_cpu)
        return sum(per_pid.values())

    @property
    def child_peak_rss(self) -> int:
        """Сума піків RSS процесів-воркерів, байти; для serial/threads — 0."""
        me = os.getpid()
        per_pid = {s.pid: s.peak_rss for s in self.worker_stats if s.pid != me}
        return sum(per_pid.values())


def build_parallel(
    corpus: Path,
    executor: ExecutorName = "processes",
    workers: int | None = None,
    positions: bool = False,
    limit: int | None = None,
    on_done: Callable[[PartialIndex], None] | None = None,
    transport: Transport = "compact",
    chunks_per_worker: int | None = None,
) -> BuildReport:
    """plan_chunks -> map_chunks -> merge. workers=None -> os.cpu_count().

    Шматків = workers × chunks_per_worker; за замовчуванням 1, для процесів — CHUNKS_PER_PROCESS:
    батько декодує готові шматки, поки воркери ще зайняті рештою (конвеєр замість «усі разом
    закінчили — тепер батько працює сам»).

    limit рахує непорожні рядки (а не коректні документи, як build_index): без розбору JSON
    батько не знає, який рядок битий. На корпусі без битих рядків це одне й те саме.
    """
    workers = workers or os.cpu_count() or 1
    t0 = time.perf_counter()
    if chunks_per_worker is None:
        chunks_per_worker = CHUNKS_PER_PROCESS if executor == "processes" else 1
    chunks = plan_chunks(corpus, workers * chunks_per_worker, limit)
    t1 = time.perf_counter()
    with gc_paused():  # процес-глобально: покриває і потоки, і декодування результатів процесів
        partials = map_chunks(chunks, executor, workers, positions, on_done, transport)
        t2 = time.perf_counter()
        stats = tuple(p.stats for p in partials)
        index = merge(partials) if partials else Index({}, {}, {}, has_positions=positions)
        t3 = time.perf_counter()
    log.info(
        "%s x%d: plan %.3fs, map %.3fs, merge %.3fs, %d chunks",
        executor, workers, t1 - t0, t2 - t1, t3 - t2, len(chunks),
    )  # fmt: skip
    return BuildReport(index, executor, workers, len(chunks), t1 - t0, t2 - t1, t3 - t2, stats)
