"""Залежності для Depends: звідки маршрут бере налаштування, індекс і Searcher-и.

Маршрути не знають про app.state — лише про ці функції. Тому тест підміняє індекс одним рядком:
    app.dependency_overrides[get_index] = lambda: tiny_index

Залежності — `async def`, хоч і нічого не чекають: синхронну залежність FastAPI віддає в пул
потоків (як і def-маршрут), а це перехід туди й назад на кожну з них у кожному запиті. Тут роботи
на мікросекунди, тож дешевше виконати її просто в циклі подій.
"""

import threading
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from findex.engine import Searcher
from findex.index import Index
from findex.ranking import BM25, TfIdf
from findex.semantic import Embedder, SemanticIndex
from findex.snippets import TextStore
from findex.vector import VectorIndex
from findex.web.models import ScorerName
from findex.web.settings import Settings

SCORERS: dict[ScorerName, type[BM25] | type[TfIdf]] = {"bm25": BM25, "tfidf": TfIdf}


async def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_index(request: Request) -> Index:
    """Індекс, завантажений у lifespan. Немає (або вже закритий) — 503, а не AttributeError."""
    index: Index | None = getattr(request.app.state, "index", None)
    if index is None or index.closed:
        raise HTTPException(status_code=503, detail="index is not loaded")
    return index


class SearchEngine:
    """Searcher на кожен scorer (у кожного свій lru_cache) + сховище текстів для /docs/{id}.

    Живе стільки ж, скільки індекс: Searcher-и створюються ліниво й переживають запити, інакше
    кеш запитів скидався б щоразу.
    """

    def __init__(
        self,
        index: Index,
        settings: Settings,
        semantic: SemanticIndex | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.index = index
        self.semantic = semantic
        self.embedder = embedder
        self._corpus = settings.corpus_path
        self._cache_size = settings.cache_size
        self._searchers: dict[ScorerName, Searcher] = {}
        self._vector: VectorIndex | None = None  # Lab 08: CSR-масиви — одні на обидва scorer-и
        self._texts: dict[str, TextStore] = {}
        self._lock = threading.Lock()  # def-маршрути — у пулі потоків: створюємо під замком

    def searcher(self, scorer: ScorerName) -> Searcher:
        with self._lock:
            s = self._searchers.get(scorer)
            if s is None:
                if self._vector is None:
                    self._vector = VectorIndex.from_index(self.index)
                s = Searcher(
                    self.index,
                    SCORERS[scorer](),
                    cache_size=self._cache_size,
                    corpus_path=self._corpus,
                    vector=self._vector,
                    semantic=self.semantic,
                    embedder=self.embedder,
                )
                self._searchers[scorer] = s
            return s

    def text(self, doc_id: int) -> str | None:
        meta = self.index.doc_meta[doc_id]
        corpus = str(self._corpus) if self._corpus else meta.path.rpartition("#")[0]
        with self._lock:
            store = self._texts.get(corpus)
            if store is None:
                store = self._texts[corpus] = TextStore(Path(corpus))
        return store.get(meta.title)

    def close(self) -> None:
        with self._lock:
            for s in self._searchers.values():
                s.close()
            for t in self._texts.values():
                t.close()
            self._searchers.clear()
            self._texts.clear()


async def get_engine(
    request: Request,
    index: Annotated[Index, Depends(get_index)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SearchEngine:
    """Один SearchEngine на індекс. Якщо індекс підмінили (тест), рушій перебудовується під нього."""
    state = request.app.state
    with state.engine_lock:
        engine: SearchEngine | None = getattr(state, "engine", None)
        if engine is None or engine.index is not index:
            if engine is not None:
                engine.close()
            engine = state.engine = SearchEngine(
                index, settings, getattr(state, "semantic", None), getattr(state, "embedder", None)
            )
        return engine


SettingsDep = Annotated[Settings, Depends(get_settings)]
IndexDep = Annotated[Index, Depends(get_index)]
EngineDep = Annotated[SearchEngine, Depends(get_engine)]
