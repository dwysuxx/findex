"""Lab 08, M3: шматки, ембединги, best-chunk-per-doc, RRF, режими Searcher, API й CLI.

Справжня модель у тестах не вантажиться (≈ 90 МБ, мережа). HashEmbedder — детермінований
«мішок слів»: кожне слово -> вісь за хешем, тож косинус = частка спільних слів. Цього досить,
щоб перевірити все навколо моделі: нормування, шматки, злиття, сніпети, помилки.
"""

import json
import zlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from conftest import TINY_DOCS
from findex.engine import Searcher, SemanticUnavailable
from findex.index import Index
from findex.semantic import (
    SemanticIndex,
    Vectors,
    bag_of_words,
    build,
    chunk_spans,
    model_name,
    normalized,
    rrf,
    semantic_text,
)
from findex.tokenize import tokenize

DIM = 256


class HashEmbedder:
    model = "test/hash-bow"

    def embed(self, texts: Sequence[str]) -> Vectors:
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in tokenize(text):
                out[i, zlib.crc32(word.encode()) % DIM] += 1.0
        return out

    def embed_query(self, text: str) -> Vectors:
        return self.embed([text])[0]


def texts_of(index: Index) -> list[tuple[int, str]]:
    by_title = dict(TINY_DOCS)
    return [(d, by_title[index.doc_meta[d].title]) for d in range(index.num_docs)]


@pytest.fixture(scope="module")
def sem(tiny_index: Index) -> SemanticIndex:
    return build(texts_of(tiny_index), tiny_index.num_docs, HashEmbedder(), words=6, overlap=2)


def title(index: Index, doc_id: int) -> str:
    return index.doc_meta[doc_id].title


# --- шматки й вектори ----------------------------------------------------------------------------
def test_chunk_spans_cover_text_with_overlap() -> None:
    text = " ".join(f"w{i}" for i in range(10))
    spans = chunk_spans(text, words=4, overlap=1)
    pieces = [text[s:e].split() for s, e in spans]
    assert pieces == [["w0", "w1", "w2", "w3"], ["w3", "w4", "w5", "w6"], ["w6", "w7", "w8", "w9"]]
    assert chunk_spans("", 4, 1) == [] and chunk_spans("one", 4, 1) == [(0, 3)]
    assert len(chunk_spans(" ".join(["x"] * 300), 150, 30)) == 3  # 0-149, 120-269, 240-299


def test_normalized_unit_length_and_zero_safe() -> None:
    v = normalized(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v, axis=1), [1.0, 0.0])


def test_doc_score_is_best_chunk(sem: SemanticIndex) -> None:
    """np.maximum.at == цикл Python «максимум по шматках кожного документа»."""
    q = normalized(HashEmbedder().embed(["python loop"]))[0]
    sims = sem.vectors @ q
    expected = np.full(sem.num_docs, -np.inf)
    for chunk, doc in enumerate(sem.chunk_doc):
        expected[doc] = max(expected[doc], sims[chunk])
    assert np.array_equal(sem.doc_scores(q), expected)
    best = sem.best_chunk(q, int(np.argmax(expected)))
    assert best is not None and best[0] < best[1]


def test_save_load_roundtrip_without_pickle(sem: SemanticIndex, tmp_path: Path) -> None:
    sem.save(tmp_path / "e.npz")
    back = SemanticIndex.load(tmp_path / "e.npz")
    assert back.model == sem.model and back.num_docs == sem.num_docs
    for name in ("vectors", "chunk_doc", "chunk_span"):
        assert np.array_equal(getattr(back, name), getattr(sem, name))


# --- запит і злиття ------------------------------------------------------------------------------
def test_rrf_formula_and_ties() -> None:
    fused = dict(rrf([[1, 2, 3], [3, 1]], k=60))
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[3] == pytest.approx(1 / 63 + 1 / 61)
    assert fused[2] == pytest.approx(1 / 62)
    assert [d for d, _ in rrf([[5, 4], [4, 5]])] == [4, 5]  # рівні суми -> менший doc_id


def test_rrf_needs_no_score_normalisation() -> None:
    """Документ першим у ОБОХ списках перемагає документ, першим лише в одному — хоч би якими були
    сирі бали (BM25 = 20 чи косинус = 0.3, RRF їх не бачить)."""
    assert rrf([[7, 8], [7, 9]])[0][0] == 7


def test_query_rewrites() -> None:
    assert semantic_text('"meat hook" OR (rot AND NOT stun)') == "meat hook rot stun"
    assert bag_of_words("a fat butcher") == "a OR fat OR butcher"
    assert bag_of_words("pudge AND hook") == "pudge AND hook"  # явні оператори — як є
    assert model_name("all-MiniLM-L6-v2") == "sentence-transformers/all-MiniLM-L6-v2"


# --- Searcher ------------------------------------------------------------------------------------
def test_semantic_mode_finds_word_overlap(tiny_index: Index, sem: SemanticIndex, tiny_corpus: Path) -> None:
    s = Searcher(tiny_index, semantic=sem, embedder=HashEmbedder(), corpus_path=tiny_corpus)
    page = s.search_page("event loop java", k=3, mode="semantic")
    assert title(tiny_index, page.results[0].doc_id) == "java-loop"  # усі три слова
    assert page.total == tiny_index.num_docs  # кожен документ має шматки
    assert all(-1.0 <= r.score <= 1.0 for r in page.results)


def test_hybrid_mode_fuses_both_lists(tiny_index: Index, sem: SemanticIndex, tiny_corpus: Path) -> None:
    s = Searcher(tiny_index, semantic=sem, embedder=HashEmbedder(), corpus_path=tiny_corpus)
    hybrid = s.search_page("python async", k=10, mode="hybrid")
    keyword = [r.doc_id for r in s.search_page("python OR async", k=100, snippets=False).results]
    dense = [d for d, _ in sem.search(normalized(HashEmbedder().embed(["python async"]))[0], 100)]
    assert [(r.doc_id, r.score) for r in hybrid.results] == rrf([keyword, dense])[:10]


def test_semantic_without_embeddings_is_an_error(tiny_index: Index) -> None:
    with pytest.raises(SemanticUnavailable):
        Searcher(tiny_index).search_page("python", mode="semantic")
    assert Searcher(tiny_index).search_page("python").total == 4  # keyword працює як і раніше


def test_semantic_mode_ignores_query_syntax(tiny_index: Index, sem: SemanticIndex) -> None:
    """Битий для мови запитів рядок — нормальний запит природною мовою."""
    s = Searcher(tiny_index, semantic=sem, embedder=HashEmbedder())
    assert s.search_page('python "async (', snippets=False, mode="semantic").results


def test_semantic_snippet_comes_from_best_chunk(
    tiny_index: Index, sem: SemanticIndex, tiny_corpus: Path
) -> None:
    s = Searcher(tiny_index, semantic=sem, embedder=HashEmbedder(), corpus_path=tiny_corpus)
    top = s.search_page("tutorial filler", k=1, mode="semantic").results[0]
    assert title(tiny_index, top.doc_id) == "long"
    assert "**tutorial**" in top.snippet or "**filler**" in top.snippet


# --- API -----------------------------------------------------------------------------------------
@pytest.fixture
def semantic_client(tiny_index_file: Path, tiny_index: Index, sem: SemanticIndex) -> TestClient:
    from findex.web import create_app
    from findex.web.deps import get_index
    from findex.web.settings import Settings

    app = create_app(Settings(index_path=tiny_index_file, _env_file=None))  # type: ignore[call-arg]
    app.dependency_overrides[get_index] = lambda: tiny_index
    app.state.semantic, app.state.embedder = sem, HashEmbedder()
    return TestClient(app)


def test_api_modes(semantic_client: TestClient) -> None:
    for mode in ("keyword", "semantic", "hybrid"):
        body = semantic_client.get("/search", params={"q": "python async", "mode": mode}).json()
        assert body["mode"] == mode and body["results"], mode
    assert semantic_client.get("/search", params={"q": "x", "mode": "vector"}).status_code == 422


def test_api_semantic_unavailable_is_503(tiny_index_file: Path, tiny_index: Index) -> None:
    from findex.web import create_app
    from findex.web.deps import get_index
    from findex.web.settings import Settings

    app = create_app(Settings(index_path=tiny_index_file, _env_file=None))  # type: ignore[call-arg]
    app.dependency_overrides[get_index] = lambda: tiny_index
    client = TestClient(app)
    r = client.get("/search", params={"q": "python", "mode": "semantic"})
    assert r.status_code == 503 and "findex embed" in r.json()["detail"]
    assert client.get("/search", params={"q": "python"}).status_code == 200


def test_ui_shows_mode_toggle_only_with_embeddings(semantic_client: TestClient) -> None:
    html = semantic_client.get("/").text
    assert 'name="mode" value="semantic"' in html
    page = semantic_client.get("/", params={"q": "python async", "mode": "hybrid"}).text
    assert "гібрид" in page and 'value="hybrid" checked' in page


# --- CLI -----------------------------------------------------------------------------------------
def test_cli_embed_then_search(
    tiny_index_file: Path, tiny_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import findex.semantic

    monkeypatch.setattr(findex.semantic, "FastEmbedder", lambda model, *a, **k: HashEmbedder())  # pyright: ignore[reportUnknownLambdaType]
    from findex.cli import app

    out = tmp_path / "tiny.emb.npz"
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "embed",
            str(tiny_index_file),
            "-o",
            str(out),
            "--corpus",
            str(tiny_corpus),
            "--words",
            "6",
            "--overlap",
            "2",
        ],
    )
    assert r.exit_code == 0, r.output
    assert out.is_file() and SemanticIndex.load(out).model == HashEmbedder.model

    r = runner.invoke(
        app,
        [
            "search",
            str(tiny_index_file),
            "event loop java",
            "--mode",
            "semantic",
            "--embeddings",
            str(out),
            "--json",
            "-k",
            "1",
        ],
    )
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout.splitlines()[0])["title"] == "java-loop"

    r = runner.invoke(
        app,
        [
            "search",
            str(tiny_index_file),
            "python",
            "--mode",
            "semantic",
            "--embeddings",
            str(tmp_path / "nope.npz"),
        ],
    )
    assert r.exit_code == 1 and "findex embed" in r.output


def test_embeddings_from_another_corpus_are_rejected(
    sem: SemanticIndex, tiny_corpus: Path, tmp_path: Path
) -> None:
    from dataclasses import replace

    from findex.semantic import check_corpus, file_sha256

    stamped = replace(sem, corpus_sha256=file_sha256(tiny_corpus))
    check_corpus(stamped, tiny_corpus)  # той самий корпус — тихо
    other = tmp_path / "other.jsonl"
    other.write_text(tiny_corpus.read_text("utf-8").replace("python", "pyth0n"), "utf-8")
    with pytest.raises(ValueError, match="іншого корпусу"):
        check_corpus(stamped, other)
    check_corpus(sem, other)  # старий файл без відбитка — не блокуємо (лише num_docs)


def test_doc_scores_unsorted_chunks_fall_back_to_ufunc_at(sem: SemanticIndex) -> None:
    """reduceat — для шматків підряд (як пише build); перемішані — через np.maximum.at, той самий результат."""
    from dataclasses import replace

    q = normalized(HashEmbedder().embed(["java event loop"]))[0]
    perm = np.random.default_rng(0).permutation(len(sem.chunk_doc))
    shuffled = replace(
        sem, vectors=sem.vectors[perm], chunk_doc=sem.chunk_doc[perm], chunk_span=sem.chunk_span[perm]
    )
    assert np.array_equal(shuffled.doc_scores(q), sem.doc_scores(q))
