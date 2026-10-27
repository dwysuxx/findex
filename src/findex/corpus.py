import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Document:
    doc_id: str
    path: Path
    text: str | None


def parse_line(line: str, root: Path, line_number: int) -> Document | None:
    """Один рядок jsonl -> Document; порожній або битий рядок -> None (з попередженням у лог).

    Спільна для iter_documents і для воркерів паралельної побудови (findex.parallel):
    обидва шляхи мусять однаково вирішувати, який рядок — документ, інакше індекси розійдуться.
    """
    if not line.strip():
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        log.warning("Битый JSON в %s:%d — %s", root, line_number, e)
        return None
    try:
        doc_id = str(obj["id"])
        doc_text = str(obj["text"].replace("\n", " "))
    except KeyError as e:
        log.warning("Нет поля %s в %s:%d", e, root, line_number)
        return None
    return Document(doc_id=doc_id, path=Path(str(root) + "#" + doc_id), text=doc_text)


def iter_documents(root: Path) -> Iterator[Document]:
    """
    Read jsonl file for one line and yield Document objects.(LAZY ITERATOR)
    Args:
        root: Path
    Returns:
        Iterator[Document]: An iterator of Document objects
    """
    with root.open(encoding="utf-8", errors="replace") as f:
        for line_number, line in enumerate(f, start=1):
            doc = parse_line(line, root, line_number)
            if doc is not None:
                yield doc
