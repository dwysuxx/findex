"""Сніпети: вікно ±width символів навколо найкращого збігу з підсвіткою термів.

Працює й без positions: шукаємо слова в оригінальному тексті тим самим регулярним виразом і
нормалізацією, що й токенайзер, тому відображається оригінальний регістр.
"""

import contextlib
import json
import logging
import threading
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from findex.tokenize import WORD as _WORD
from findex.tokenize import normalize as _normalize

log = logging.getLogger(__name__)


class TextStore:
    """Ліниво читає текст документа з jsonl за байтовим зміщенням (корпус у пам'ять не вантажиться).

    Один дескриптор на всі читання: seek + readline — дві операції над спільною позицією файлу,
    тож без замка два потоки (веб-сервер, Lab 07) читали б рядки один одного.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._offsets: dict[str, int] | None = None
        self._fh: BinaryIO | None = None
        self._lock = threading.Lock()

    def _build(self) -> dict[str, int]:
        offsets: dict[str, int] = {}
        pos = 0
        with self.path.open("rb") as f:
            for line in f:
                if line.strip():
                    with contextlib.suppress(ValueError, KeyError):  # биті рядки пропускає й corpus.py
                        offsets[str(json.loads(line.decode("utf-8", "replace"))["id"])] = pos
                pos += len(line)
        return offsets

    def get(self, doc_id: str) -> str | None:
        try:
            with self._lock:
                if self._offsets is None:
                    self._offsets = self._build()  # тепер pyright знає, що це dict, без ignore
                offset = self._offsets.get(doc_id)
                if offset is None:
                    return None
                if self._fh is None:
                    self._fh = self.path.open("rb")
                self._fh.seek(offset)
                line = self._fh.readline()
            obj = json.loads(line.decode("utf-8", "replace"))  # розбір — уже поза замком
            return str(obj["text"]).replace("\n", " ")  # так само, як у corpus.py
        except (OSError, ValueError, KeyError) as e:
            log.warning("Не вдалося прочитати текст %s з %s: %s", doc_id, self.path, e)
            return None

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def make_snippet(text: str, terms: Iterable[str], width: int = 80, mark: str = "**") -> str:
    """Вікно ~2*width символів із найбільшою кількістю РІЗНИХ термів запиту; збіги в **...**."""
    wanted = set(terms)
    hits = [(m.start(), m.end(), _normalize(m.group())) for m in _WORD.finditer(text)]
    hits = [h for h in hits if h[2] in wanted]

    if not hits:  # нічого підсвічувати (напр., запит лише з NOT): початок документа
        head = text[: 2 * width].strip()
        return head + ("…" if len(text) > 2 * width else "")

    # ковзне вікно по збігах: максимум різних термів, при рівності — найраніше
    window: Counter[str] = Counter()
    j = 0
    best_i, best_j, best_distinct = 0, 1, 0
    for i, (start, _, term) in enumerate(hits):
        while j < len(hits) and hits[j][0] - start <= 2 * width:
            window[hits[j][2]] += 1
            j += 1
        if len(window) > best_distinct:
            best_i, best_j, best_distinct = i, j, len(window)
        window[term] -= 1
        if window[term] == 0:
            del window[term]

    first_start, last_end = hits[best_i][0], hits[best_j - 1][1]
    center = (first_start + last_end) // 2
    lo, hi = max(0, center - width), min(len(text), center + width)
    if lo > 0:  # не різати слово посередині
        sp = text.find(" ", lo, first_start)
        if sp != -1:
            lo = sp + 1
    if hi < len(text):
        sp = text.rfind(" ", last_end, hi)
        if sp != -1:
            hi = sp

    out: list[str] = []
    cursor = lo
    for start, end, _ in hits:
        if start >= lo and end <= hi:
            out.append(text[cursor:start])
            out.append(f"{mark}{text[start:end]}{mark}")
            cursor = end
    out.append(text[cursor:hi])
    return ("…" if lo > 0 else "") + "".join(out) + ("…" if hi < len(text) else "")
