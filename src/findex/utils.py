"""Маленькі спільні утиліти: декоратор @timed."""

import functools
import logging
import time

log = logging.getLogger(__name__)


def timed(fn):
    """Логує (DEBUG) час виконання функції в мілісекундах.

    `functools.wraps` копіює __name__, __doc__, __wrapped__ на обгортку; без нього
    `search.__name__` став би "wrapper", а help(search) і тести зламались би.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - t0
            log.debug("%s took %.2f ms", fn.__qualname__, elapsed * 1000)

    return wrapper
