"""findex.web — FastAPI-застосунок (Lab 07).

`app` створюється ліниво, при першому зверненні (`uvicorn findex.web:app`), а не під час імпорту:
імпорт findex.web у тестах чи в CLI не повинен вимагати INDEX_PATH в оточенні.

Фабрика — у application.py, а не app.py: імпорт підмодуля findex.web.app записав би сам модуль в
атрибут `app` пакета, і `uvicorn findex.web:app` отримав би модуль замість застосунку.
"""

import functools
from typing import TYPE_CHECKING

from findex.web.application import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI


@functools.cache
def _default_app() -> "FastAPI":
    return create_app()


def __getattr__(name: str) -> object:  # PEP 562: атрибут модуля, що обчислюється на вимогу
    if name == "app":
        return _default_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
