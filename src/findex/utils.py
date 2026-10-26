"""Маленькі спільні утиліти: декоратор @timed."""

import functools
import logging
import time
from collections.abc import Callable

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
