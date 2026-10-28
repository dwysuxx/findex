"""Конфігурація з оточення (12-factor, правило III): INDEX_PATH, CORPUS_PATH, LOG_LEVEL, PORT...

Той самий образ у dev і prod відрізняється лише змінними оточення; у розробці — ще й файлом
.env (див. .env.example). FilePath перевіряє, що файл існує, ще під час Settings(): хибний
INDEX_PATH зупиняє процес на старті, до того як сервер відкрив сокет.
"""

from typing import Annotated, Literal

from pydantic import Field, FilePath, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    index_path: FilePath
    corpus_path: FilePath | None = None  # jsonl з текстами; None — шлях, записаний в індексі
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True  # JSON-рядки у stdout (платформа їх парсить); False — читабельний текст
    # Lab 08: ембединги (findex embed). None — семантичний і гібридний режими вимкнені (503)
    embeddings_path: FilePath | None = None
    # каталог моделі fastembed; у Docker модель запечена в образ, тож старт без мережі
    fastembed_cache_path: str | None = None
    cache_size: int = Field(256, ge=0)  # lru_cache запитів на один Searcher; 0 — вимкнено (бенчмарк)
    # CORS_ORIGINS=https://a.example,https://b.example — порожньо: CORS вимкнено (UI на тому ж origin)
    cors_origins: Annotated[list[str], NoDecode] = []

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """NoDecode вимикає розбір як JSON — список приходить рядком через кому."""
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value
