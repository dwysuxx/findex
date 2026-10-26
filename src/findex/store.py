import json
import pickle
import time
from pathlib import Path
from typing import TypedDict

from findex.index import DocMeta, Format, Index, Posting, build_index
from findex.utils import timed


def save_pickle(index: Index, path: Path) -> None:
    with open(path, "wb") as f:
        pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_pickle(path: Path) -> Index:
    # SECURITY: pickle.load can execute arbitrary code. A pickle stream is a
    # small program for the unpickler: an object may define __reduce__ that
    # returns (os.system, ("rm -rf ~",)), and loading the file calls it.
    # So load ONLY files this program wrote itself, NEVER a file that came
    # from a network or an untrusted user. For untrusted data use json
    # (see load_json) or a purpose-built binary format.
    with open(path, "rb") as f:
        obj = pickle.load(f)  # Any: pickle не знає типу
    if not isinstance(obj, Index):
        raise TypeError(f"{path} містить {type(obj).__name__}, а не Index")
    return obj


class DocMetaJSON(TypedDict):
    title: str
    path: str


class IndexJSON(TypedDict):
    """Форма JSON-файлу. Постінг — плаский список [doc_id, tf, *positions].
    Ключі doc_lengths/doc_meta — рядки: JSON не має int-ключів."""

    postings: dict[str, list[list[int]]]
    has_positions: bool
    doc_lengths: dict[str, int]
    doc_meta: dict[str, DocMetaJSON]


def save_json(index: Index, path: Path) -> None:
    data: IndexJSON = {
        "postings": {
            term: [[p.doc_id, p.tf, *p.positions] for p in plist] for term, plist in index.postings.items()
        },
        "has_positions": index.has_positions,
        "doc_lengths": {str(k): v for k, v in index.doc_lengths.items()},
        "doc_meta": {str(k): {"title": m.title, "path": m.path} for k, m in index.doc_meta.items()},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_json(path: Path) -> Index:
    with open(path, encoding="utf-8") as f:
        data: IndexJSON = json.load(f)  # довіряємо формі, бо файл писали ми (save_json)

    postings = {
        term: [Posting(row[0], row[1], tuple(row[2:])) for row in plist]
        for term, plist in data["postings"].items()
    }
    doc_lengths = {int(k): v for k, v in data["doc_lengths"].items()}
    doc_meta = {int(k): DocMeta(v["title"], v["path"]) for k, v in data["doc_meta"].items()}

    return Index(
        postings=postings,
        doc_lengths=doc_lengths,
        doc_meta=doc_meta,
        has_positions=data["has_positions"],
    )


@timed
def save(index: Index, path: Path, fmt: Format = "pickle") -> None:
    if fmt == "pickle":
        save_pickle(index, path)
    elif fmt == "json":
        save_json(index, path)
    else:
        raise ValueError(f"Unknown format: {fmt}")


@timed
def load(path: Path, fmt: Format = "pickle") -> Index:
    if fmt == "pickle":
        return load_pickle(path)
    elif fmt == "json":
        return load_json(path)
    else:
        raise ValueError(f"Unknown format: {fmt}")


if __name__ == "__main__":
    import sys

    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/dota2wiki.jsonl")
    index = build_index(root)

    targets: list[tuple[Format, str]] = [("pickle", "index.bin"), ("json", "index.json")]
    for fmt, filename in targets:
        p = Path(filename)

        t0 = time.perf_counter()
        save(index, p, fmt=fmt)
        save_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        loaded = load(p, fmt=fmt)
        load_time = time.perf_counter() - t0

        size_mb = p.stat().st_size / (1024 * 1024)

        assert loaded.postings == index.postings
        assert loaded.doc_lengths == index.doc_lengths
        assert loaded.doc_meta == index.doc_meta

        print(f"=== {fmt.upper()} ===")
        print(f"Size: {size_mb:.2f} MB | Save: {save_time:.4f} s | Load: {load_time:.4f} s")
