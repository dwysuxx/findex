"""Lab 08: pytest-benchmark для build_index і search. Окремо від tests/ — звичайний `pytest` їх не запускає.

    uv run pytest benchmarks --benchmark-only                                     # просто заміри
    uv run pytest benchmarks --benchmark-only --benchmark-storage=benchmarks/baseline \\
        --benchmark-compare --benchmark-compare-fail=median:100%                  # як у CI

У CI порівняння йде з baseline, знятим на такому самому раннері (ubuntu, Python 3.14). Поріг — 2×:
раннери GitHub різняться процесорами (±30 %), а регресії, від яких захищаємось, — у рази
(повернення сніпетів Lab 07 — це ×11, Python-скорер на великих постінгах — ×4).
"""

import json
from pathlib import Path

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from findex.engine import Searcher
from findex.index import Index, build_index
from findex.snippets import make_snippet, prepare

CORPUS = Path(__file__).resolve().parents[1] / "data" / "dota2wiki.jsonl"
FIXTURE_DOCS = 40  # фікстурний корпус: перші 40 статей (≈ 1 МБ) — секунди, а не хвилини
QUERY = "hero damage attack"


@pytest.fixture(scope="module")
def index() -> Index:
    return build_index(CORPUS, positions=True, limit=FIXTURE_DOCS)


def test_build_index(benchmark: BenchmarkFixture) -> None:
    ix = benchmark.pedantic(
        build_index, args=(CORPUS,), kwargs={"positions": True, "limit": FIXTURE_DOCS}, rounds=5
    )
    assert ix.num_docs == FIXTURE_DOCS


@pytest.mark.parametrize("backend", ["numpy", "python"])
def test_search_ranking(benchmark: BenchmarkFixture, index: Index, backend: str) -> None:
    searcher = Searcher(index, cache_size=0, backend=backend)  # pyright: ignore[reportArgumentType]
    results = benchmark(searcher.search, QUERY, 10, False)
    assert len(results) == 10


def test_search_with_snippets(benchmark: BenchmarkFixture, index: Index) -> None:
    with Searcher(index, cache_size=0, corpus_path=CORPUS) as searcher:
        results = benchmark(searcher.search, QUERY)
    assert all(r.snippet for r in results)


def test_snippet_one_document(benchmark: BenchmarkFixture) -> None:
    line = CORPUS.read_text("utf-8").splitlines()[13]  # pudge — довгий документ (≈ 26 тис. символів)
    doc = prepare(str(json.loads(line)["text"]).replace("\n", " "))
    snippet = benchmark(make_snippet, doc, ["meat", "hook", "pudge"])
    assert "**" in snippet
