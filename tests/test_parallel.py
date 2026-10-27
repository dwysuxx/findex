"""Lab 05: build_partial/merge і три виконавці дають побайтово той самий індекс, що й Lab 04."""

import itertools
import json
import os
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import REAL_CORPUS, TINY_DOCS, write_corpus
from findex.index import build_index
from findex.parallel import (
    AUTO_PROCESSES_MIN_BYTES,
    EXECUTORS,
    Chunk,
    build_parallel,
    build_partial,
    choose_executor,
    map_chunks,
    merge,
    plan_chunks,
)


def same_bytes(a: object, b: object) -> bool:
    """«Ідентичний індекс» = однаковий pickle: та сама структура, порядок термів і doc_id."""
    return pickle.dumps(a) == pickle.dumps(b)


@pytest.mark.parametrize("positions", [False, True])
def test_serial_is_merge_of_one_partial(tiny_corpus: Path, positions: bool) -> None:
    [chunk] = plan_chunks(tiny_corpus, 1)
    rebuilt = merge([build_partial(chunk, positions)])
    assert same_bytes(rebuilt, build_index(tiny_corpus, positions=positions))


@pytest.mark.parametrize("executor", EXECUTORS)
@pytest.mark.parametrize("workers", [1, 2, 4])
def test_executors_build_identical_index(tiny_corpus: Path, executor, workers: int) -> None:
    report = build_parallel(tiny_corpus, executor, workers, positions=True)
    per_worker = 4 if executor == "processes" else 1  # процеси: конвеєр, шматків більше за воркерів
    assert report.chunks == min(workers * per_worker, len(TINY_DOCS))
    assert same_bytes(report.index, build_index(tiny_corpus, positions=True))


@pytest.mark.parametrize("positions", [False, True])
def test_pickle_and_compact_transports_agree(tiny_corpus: Path, positions: bool) -> None:
    """Компактний marshal-транспорт декодується в ті самі Posting, що й pickle."""
    via_pickle = build_parallel(tiny_corpus, "processes", 2, positions=positions, transport="pickle")
    via_compact = build_parallel(tiny_corpus, "processes", 2, positions=positions, transport="compact")
    assert same_bytes(via_pickle.index, via_compact.index)
    assert same_bytes(via_compact.index, build_index(tiny_corpus, positions=positions))


def test_gc_paused_restores_state() -> None:
    import gc

    from findex.utils import gc_paused

    with gc_paused():
        assert not gc.isenabled()
        with gc_paused():
            pass
        assert not gc.isenabled()  # вкладений не вмикає GC раніше за зовнішній
    assert gc.isenabled()


@pytest.fixture
def messy_corpus(tmp_path: Path) -> Path:
    """Порожні рядки, битий JSON і запис без поля text — у різних шматках."""
    good = [json.dumps({"id": f"d{i}", "text": f"word{i} common text"}) for i in range(8)]
    lines = [good[0], "", good[1], "{broken", good[2], "   ", good[3], '{"id": "no-text"}', *good[4:]]
    path = tmp_path / "messy.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("workers", [1, 2, 3, 5, 10])
def test_bad_lines_renumbered_to_match_serial(messy_corpus: Path, workers: int) -> None:
    """Битий рядок займає зарезервований doc_id; merge мусить закрити «дірку», як це робить serial."""
    report = build_parallel(messy_corpus, "serial", workers, positions=True)
    assert same_bytes(report.index, build_index(messy_corpus, positions=True))
    assert sorted(report.index.doc_lengths) == list(range(8))


def test_plan_chunks_covers_every_line_once(messy_corpus: Path) -> None:
    chunks = plan_chunks(messy_corpus, 3)
    assert len(chunks) == 3
    assert [c.first_id for c in chunks] == [0, chunks[0].slots, chunks[0].slots + chunks[1].slots]
    assert sum(c.slots for c in chunks) == 10  # 8 коректних + 2 биті; порожні не рахуються
    assert all(a.end <= b.start for a, b in itertools.pairwise(chunks))


def test_plan_chunks_never_more_than_lines(tiny_corpus: Path) -> None:
    chunks = plan_chunks(tiny_corpus, 100)
    assert len(chunks) == len(TINY_DOCS)
    assert all(c.slots == 1 for c in chunks)


def test_plan_chunks_limit(tiny_corpus: Path) -> None:
    assert sum(c.slots for c in plan_chunks(tiny_corpus, 2, limit=3)) == 3
    assert build_parallel(tiny_corpus, "serial", 2, limit=3).index.num_docs == 3


def test_plan_chunks_rejects_zero(tiny_corpus: Path) -> None:
    with pytest.raises(ValueError):
        plan_chunks(tiny_corpus, 0)


def test_empty_corpus(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n\n", encoding="utf-8")
    assert plan_chunks(empty, 4) == []
    report = build_parallel(empty, "processes", 4, positions=True)
    assert report.index.num_docs == 0 and report.index.has_positions


def test_merge_rejects_mixed_positions(tiny_corpus: Path) -> None:
    a, b = plan_chunks(tiny_corpus, 2)
    with pytest.raises(ValueError, match="has_positions"):
        merge([build_partial(a, True), build_partial(b, False)])


def test_merge_order_independent(tiny_corpus: Path) -> None:
    """as_completed повертає шматки в довільному порядку — merge сортує їх сам."""
    chunks = plan_chunks(tiny_corpus, 3)
    shuffled = merge([build_partial(c, True) for c in reversed(chunks)])
    assert same_bytes(shuffled, build_index(tiny_corpus, positions=True))


@pytest.mark.parametrize("executor", EXECUTORS)
def test_worker_exception_surfaces(tiny_corpus: Path, tmp_path: Path, executor) -> None:
    """Виняток у воркері — не зависання і не тихий пропуск, а той самий виняток у батька."""
    chunks = [*plan_chunks(tiny_corpus, 2), Chunk(tmp_path / "gone.jsonl", 0, 10, 1, first_id=6, slots=1)]
    with pytest.raises(FileNotFoundError) as info:
        map_chunks(chunks, executor, workers=2)
    if executor == "processes":
        # traceback з дочірнього процесу прикріплено як __cause__ (_RemoteTraceback)
        assert "build_partial" in str(info.value.__cause__)


def test_worker_stats(tiny_corpus: Path) -> None:
    report = build_parallel(tiny_corpus, "threads", 2)
    assert len(report.worker_stats) == 2
    assert report.child_cpu == 0 and report.child_peak_rss == 0  # потоки — той самий процес
    assert report.plan_s >= 0 and report.map_s > 0 and report.merge_s >= 0


def test_choose_executor() -> None:
    assert choose_executor(3 * 2**20, gil=True) == "serial"
    assert choose_executor(AUTO_PROCESSES_MIN_BYTES, gil=True) == "processes"
    assert choose_executor(3 * 2**20, gil=False) == "threads"  # free-threaded: потоки завжди
    assert choose_executor(AUTO_PROCESSES_MIN_BYTES, gil=False) == "threads"


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    texts=st.lists(st.text(alphabet="ab cd\nαβ", max_size=30), min_size=0, max_size=12),
    n=st.integers(min_value=1, max_value=6),
    positions=st.booleans(),
)
def test_any_split_equals_serial(texts: list[str], n: int, positions: bool) -> None:
    """Властивість: для будь-якого корпусу і будь-якої кількості шматків merge == build_index."""
    with tempfile.TemporaryDirectory() as d:
        corpus = write_corpus(Path(d) / "c.jsonl", {f"id{i}": t for i, t in enumerate(texts)})
        report = build_parallel(corpus, "serial", n, positions=positions)
        assert same_bytes(report.index, build_index(corpus, positions=positions))


@pytest.mark.slow
@pytest.mark.skipif(not REAL_CORPUS.is_file(), reason="немає data/dota2wiki.jsonl")
@pytest.mark.parametrize("executor", EXECUTORS)
def test_real_corpus_identical(executor) -> None:
    report = build_parallel(REAL_CORPUS, executor, 4, positions=True)
    assert same_bytes(report.index, build_index(REAL_CORPUS, positions=True))


@pytest.mark.slow
def test_python_m_findex_is_spawn_safe(tiny_corpus: Path, tmp_path: Path) -> None:
    """`python -m findex` з процесами: воркери (spawn) не запускають CLI повторно."""
    out = tmp_path / "i.bin"
    cmd = [sys.executable, "-m", "findex", "index", str(tiny_corpus), "-o", str(out)]
    result = subprocess.run(
        [*cmd, "--executor", "processes", "-w", "3"],
        capture_output=True,
        timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert pickle.loads(out.read_bytes()) == build_index(tiny_corpus)
