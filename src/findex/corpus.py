import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class Document:
    doc_id: str
    path: Path
    text: str | None

def iter_documents(root: Path) -> Iterator[Document]:
    with root.open(encoding="utf-8", errors='replace') as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                log.warning("Битый JSON в %s:%d — %s", root, line_number, e)
                continue
            try:
                doc_id = str(obj["id"])
                doc_text = str(obj["text"].replace("\n", " "))
            except KeyError as e:
                log.warning("Нет поля %s в %s:%d", e, root, line_number)
                continue

            yield Document(doc_id=doc_id, path=Path(str(root) + "#" + doc_id), text=doc_text)