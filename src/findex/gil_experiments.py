"""Lab 05 «Prove it to yourself»: експерименти з GIL + гонка на спільному Counter.

    python -m findex.gil_experiments                 # усі по черзі
    python -m findex.gil_experiments cpu counter     # вибрані: cpu counter switch lambda gil dis race
    python3.13t -m findex.gil_experiments cpu race   # те саме на free-threaded збірці

Без залежностей, крім findex: запускається будь-яким інтерпретатором з PYTHONPATH=src.
"""

import dis
import sys
import sysconfig
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

N = 10**7
CORPUS = Path("data/dota2wiki.jsonl")


def burn(n: int = N) -> int:
    """CPU-bound на чистому Python: жодного I/O, GIL не відпускається добровільно."""
    return sum(i * i for i in range(n))


def _timed(label: str, fn: Callable[[], object]) -> float:
    t0 = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - t0
    print(f"  {label:<28} {elapsed:6.2f} s")
    return elapsed


def _two_threads(fn: Callable[[], object]) -> None:
    threads = [threading.Thread(target=fn) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def exp_cpu() -> None:
    """1. Один раз; двічі послідовно; двічі у двох потоках; двічі у двох процесах."""
    print("1. CPU-bound: sum(i*i for i in range(10**7))")
    _timed("once", burn)
    _timed("twice, sequential", lambda: (burn(), burn()))
    _timed("twice, 2 threads", lambda: _two_threads(burn))
    with ProcessPoolExecutor(2) as pool:
        pool.submit(int).result()  # пул уже запущено: міряємо роботу, а не spawn
        _timed("twice, 2 processes", lambda: list(pool.map(burn, [N, N])))


shared = 0


def _increment(times: int, lock: threading.Lock | None) -> None:
    global shared
    for _ in range(times):
        if lock is None:
            shared += 1
        else:
            with lock:
                shared += 1


def exp_counter() -> None:
    """2. Два потоки по 10**6 разів `shared += 1`: без Lock і з Lock."""
    global shared
    print("2. shared += 1, 2 threads x 1_000_000 (expected 2_000_000)")
    for lock in (None, threading.Lock()):
        results: list[int] = []
        for _ in range(5):
            shared = 0
            _two_threads(lambda lock=lock: _increment(10**6, lock))
            results.append(shared)
        lost = [2_000_000 - r for r in results]
        print(f"  {'with Lock' if lock else 'no lock':<10} 5 runs, lost updates: {lost}")


def exp_switch() -> None:
    """3. Інтервал перемикання 5 мс -> 0.1 мс: потоки віддають GIL у 50 разів частіше."""
    print(f"3. sys.getswitchinterval() = {sys.getswitchinterval()}")
    _timed("2 threads, 5 ms", lambda: _two_threads(burn))
    old = sys.getswitchinterval()
    sys.setswitchinterval(0.0001)
    try:
        _timed("2 threads, 0.1 ms", lambda: _two_threads(burn))
    finally:
        sys.setswitchinterval(old)


def exp_lambda() -> None:
    """4. Лямбду не можна передати в інший процес: її не можна запіклити за іменем."""
    print("4. ProcessPoolExecutor().submit(lambda: 42)")
    with ProcessPoolExecutor(1) as pool:
        try:
            pool.submit(lambda: 42).result()
        except Exception as e:
            print(f"  {type(e).__module__}.{type(e).__name__}: {e}")


def exp_gil() -> None:
    """5. Яка це збірка і чи ввімкнено GIL зараз."""
    is_enabled = getattr(sys, "_is_gil_enabled", None)
    print(f"5. {sys.version.splitlines()[0]}")
    print(f"  Py_GIL_DISABLED (free-threaded build): {bool(sysconfig.get_config_var('Py_GIL_DISABLED'))}")
    print(f"  sys._is_gil_enabled(): {is_enabled() if is_enabled else 'n/a (< 3.13)'}")


def _plus_one() -> None:
    global shared
    shared += 1


def exp_dis() -> None:
    """Рефлексія 8: байткоди `shared += 1` — читання, додавання і запис — це окремі інструкції."""
    print("dis: shared += 1")
    for ins in dis.get_instructions(_plus_one):
        if ins.opname not in ("RESUME", "RETURN_CONST", "RETURN_VALUE", "LOAD_CONST") or ins.argval == 1:
            print(f"  {ins.opname:<14} {ins.argrepr or ins.argval}")


def _shared_cf(token_chunks: list[list[str]], lock: threading.Lock | None) -> Counter[str]:
    """Частоти термів у колекції — ОДИН спільний Counter на всі потоки. Так робити не треба."""
    cf: Counter[str] = Counter()

    def work(tokens: list[str]) -> None:
        if lock is None:
            for tok in tokens:
                cf[tok] += 1  # прочитати -> додати 1 -> записати: між ними інший потік теж пише
        else:
            local = Counter(tokens)  # виправлення: свій стан у кожного потоку,
            with lock:  # а спільний чіпаємо один раз, під Lock
                cf.update(local)

    with ThreadPoolExecutor(len(token_chunks)) as pool:
        list(pool.map(work, token_chunks))
    return cf


def exp_race() -> None:
    """Гонка, яку ховає GIL: потоки рахують спільний Counter частот токенів корпусу."""
    from findex.corpus import iter_documents
    from findex.tokenize import tokenize

    tokens = [t for doc in iter_documents(CORPUS) for t in tokenize(doc.text or "")]
    chunks = [tokens[i::8] for i in range(8)]
    expected = len(tokens)
    print(f"race: shared Counter, 8 threads, {expected:_} tokens")
    for label, lock in (("no lock", None), ("local + Lock", threading.Lock())):
        lost = [expected - _shared_cf(chunks, lock).total() for _ in range(3)]
        print(f"  {label:<13} lost updates in 3 runs: {lost}")


EXPERIMENTS: dict[str, Callable[[], None]] = {
    "gil": exp_gil,
    "cpu": exp_cpu,
    "counter": exp_counter,
    "switch": exp_switch,
    "lambda": exp_lambda,
    "dis": exp_dis,
    "race": exp_race,
}


if __name__ == "__main__":  # під spawn процеси-воркери імпортують цей модуль заново
    for name in sys.argv[1:] or EXPERIMENTS:
        EXPERIMENTS[name]()
        print()
