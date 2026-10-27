"""Структуровані логи у stdout (12-factor, правило XI): один JSON-об'єкт на рядок.

request_id лежить у contextvar: middleware ставить його на початку запиту, і кожен запис, зроблений
під час цього запиту (у маршруті, у Searcher, навіть у пулі потоків — anyio копіює контекст),
отримує його автоматично через RequestIdFilter.
"""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# атрибути, які є в кожного LogRecord; решта — це extra={...} з виклику логера
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        out.update({k: v for k, v in vars(record).items() if k not in _STANDARD and v is not None})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def logging_config(level: str = "INFO", as_json: bool = True) -> dict[str, Any]:
    """dictConfig для uvicorn.run(log_config=...): uvicorn застосовує його в КОЖНОМУ процесі-воркері.

    Логи uvicorn (старт, зупинка) йдуть у той самий хендлер; uvicorn.access вимкнено — рядок доступу
    з request_id і часом пише наш middleware (findex.web.access).
    """
    text_fmt = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": RequestIdFilter}},
        "formatters": {
            "json": {"()": JsonFormatter},
            "text": {"format": text_fmt},
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "json" if as_json else "text",
                "filters": ["request_id"],
            }
        },
        "root": {"level": level, "handlers": ["stdout"]},
        "loggers": {
            "uvicorn": {"level": level, "handlers": [], "propagate": True},
            "uvicorn.error": {"level": level, "handlers": [], "propagate": True},
            "uvicorn.access": {"level": "WARNING", "handlers": [], "propagate": False},
        },
    }
