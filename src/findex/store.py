import json
import pickle
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from findex.index import Index, Posting, DocMeta, build_index


def save_pickle(index: Index, path: Path) -> None:
    with open(path, "wb") as f:
        pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_pickle(path: Path) -> Index:
    with open(path, "rb") as f:
        return pickle.load(f)


def save_json(index: Index, path: Path) -> None:
    data = {
        "postings": {
            term: [[p.doc_id, p.tf] for p in plist]
            for term, plist in index.postings.items()
        },
        "doc_lengths": index.doc_lengths,
        "doc_meta": {
            doc_id: asdict(meta)
            for doc_id, meta in index.doc_meta.items()
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_json(path: Path) -> Index:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    postings = defaultdict(list)
    for term, plist in data["postings"].items():
        postings[term] = [Posting(doc_id, tf) for doc_id, tf in plist]

    doc_lengths = {int(k): v for k, v in data["doc_lengths"].items()}
    doc_meta = {int(k): DocMeta(**v) for k, v in data["doc_meta"].items()}

    return Index(postings=postings, doc_lengths=doc_lengths, doc_meta=doc_meta)


def save(index: Index, path: Path, fmt: str = "pickle") -> None:
    if fmt == "pickle":
        save_pickle(index, path)
    elif fmt == "json":
        save_json(index, path)
    else:
        raise ValueError(f"Unknown format: {fmt}")


def load(path: Path, fmt: str = "pickle") -> Index:
    if fmt == "pickle":
        return load_pickle(path)
    elif fmt == "json":
        return load_json(path)
    else:
        raise ValueError(f"Unknown format: {fmt}")


if __name__ == "__main__":
    root = Path("data/dota2wiki.jsonl")
    index = build_index(root)

    for fmt, filename in [("pickle", "index.bin"), ("json", "index.json")]:
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