"""Спільні фікстури: крихітний корпус на диску й індекси з нього."""

import json
from pathlib import Path

import pytest

from findex.index import Index, build_index

TINY_DOCS = {
    "py-async": "python async await event loop python",
    "py-java": "python java",
    "java-loop": "java event loop",
    "long": "async python tutorial " + "filler " * 40,
    "rare": "rareword python",
    "uk": "Пошук «Привіт, світе» і ще раз привіт",
}

REAL_CORPUS = Path(__file__).resolve().parents[1] / "data" / "dota2wiki.jsonl"


def write_corpus(path: Path, docs: dict[str, str]) -> Path:
    path.write_text(
        "\n".join(json.dumps({"id": k, "text": v}, ensure_ascii=False) for k, v in docs.items()),
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="session")
def tiny_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_corpus(tmp_path_factory.mktemp("corpus") / "tiny.jsonl", TINY_DOCS)


@pytest.fixture(scope="session")
def tiny_index(tiny_corpus: Path) -> Index:
    """Будується один раз на сесію. НЕ змінювати в тестах (і не викликати close()):
    його бачать усі тести, тож мутація в одному зламала б інші — для цього є fresh_index."""
    return build_index(tiny_corpus, positions=True)


@pytest.fixture
def fresh_index(tiny_corpus: Path) -> Index:
    """Свіжий індекс на кожен тест — для тестів, що закривають або змінюють індекс."""
    return build_index(tiny_corpus, positions=True)


@pytest.fixture(scope="session")
def tiny_index_file(tiny_index: Index, tmp_path_factory: pytest.TempPathFactory) -> Path:
    from findex.store import save

    path = tmp_path_factory.mktemp("idx") / "tiny.bin"
    save(tiny_index, path)
    return path
