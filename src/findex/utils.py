"""Маленькі спільні утиліти: декоратор @timed, пік RSS процесу, пауза GC."""

import functools
import gc
import logging
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager

log = logging.getLogger(__name__)


def timed[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Логує (DEBUG) час виконання функції в мілісекундах.

    `functools.wraps` копіює __name__, __doc__, __wrapped__ на обгортку; без нього
    `search.__name__` став би "wrapper", а help(search) і тести зламались би.
    ParamSpec `**P` зберігає сигнатуру: pyright перевіряє аргументи декорованої функції.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - t0
            log.debug("%s took %.2f ms", fn.__qualname__, elapsed * 1000)

    return wrapper


def peak_rss_bytes() -> int:
    """Пік resident set size поточного процесу в байтах (Windows: PeakWorkingSetSize).

    tracemalloc бачить лише алокації Python; RSS — уся пам'ять процесу, включно з
    інтерпретатором і буферами, тобто те, що реально платить машина за кожен воркер.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [  # PROCESS_MEMORY_COUNTERS з psapi.h
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return 0
        return int(counters.PeakWorkingSetSize)

    import resource

    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maxrss if sys.platform == "darwin" else maxrss * 1024  # Linux рахує в КБ, macOS — у байтах


@contextmanager
def gc_paused() -> Generator[None]:
    """Вимкнути циклічний GC на час блоку (вкладені виклики безпечні: вмикає лише зовнішній).

    Побудова індексу створює мільйони об'єктів і жодного циклу — їх звільняє підрахунок посилань.
    А циклічний GC, рахуючи нові алокації, раз у раз обходить усю купу, що росте: на корпусі ×16
    це 20% послідовної побудови і половина декодування в батька (README, Lab 05).
    """
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
