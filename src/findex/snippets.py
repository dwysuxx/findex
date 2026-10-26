"""Сніпети: вікно ±width символів навколо найкращого збігу з підсвіткою термів.

Працює й без positions: шукаємо слова в оригінальному тексті тим самим \\w+ та
нормалізацією (NFC + casefold), що й токенайзер, тому відображається оригінальний регістр.
"""

import json
import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)
_WORD = re.compile(r"\w+")


def _normalize(word: str) -> str:
    return unicodedata.normalize("NFC", word).casefold()


class TextStore:
    """Ліниво читає текст документа з jsonl за байтовим зміщенням (корпус у пам'ять не вантажиться)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._offsets: dict[str, int] | None = None
        self._fh = None

    def _build(self) -> None:
        offsets: dict[str, int] = {}
        pos = 0
        with self.path.open("rb") as f:
            for line in f:
                if line.strip():
                    try:
                        offsets[str(json.loads(line.decode("utf-8", "replace"))["id"])] = pos
                    except (ValueError, KeyError):
                        pass
                pos += len(line)
        self._offsets = offsets

    def get(self, doc_id: str) -> str | None:
        try:
            if self._offsets is None:
                self._build()
            offset = self._offsets.get(doc_id)  # type: ignore[union-attr]
            if offset is None:
                return None
            if self._fh is None:
                self._fh = self.path.open("rb")
            self._fh.seek(offset)
            obj = json.loads(self._fh.readline().decode("utf-8", "replace"))
            return str(obj["text"]).replace("\n", " ")  # так само, як у corpus.py
        except (OSError, ValueError, KeyError) as e:
            log.warning("Не вдалося прочитати текст %s з %s: %s", doc_id, self.path, e)
            return None

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def make_snippet(text: str, terms: set[str] | list[str], width: int = 80, mark: str = "**") -> str:
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

    out, cursor = [], lo
    for start, end, _ in hits:
        if start >= lo and end <= hi:
            out.append(text[cursor:start])
            out.append(f"{mark}{text[start:end]}{mark}")
            cursor = end
    out.append(text[cursor:hi])
    return ("…" if lo > 0 else "") + "".join(out) + ("…" if hi < len(text) else "")
