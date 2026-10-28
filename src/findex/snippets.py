"""Сніпети: вікно ±width символів навколо найкращого збігу з підсвіткою термів.

Працює й без positions: шукаємо слова в оригінальному тексті тим самим регулярним виразом і
нормалізацією, що й токенайзер, тому відображається оригінальний регістр.
"""

import contextlib
import functools
import json
import logging
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from findex.tokenize import _COMBINING  # pyright: ignore[reportPrivateUsage]  # той самий клас знаків, що у WORD
from findex.tokenize import WORD as _WORD
from findex.tokenize import normalize as _normalize

type Hit = tuple[int, int, str]  # (start, end, нормалізований терм) у координатах оригінального тексту

log = logging.getLogger(__name__)


class TextStore:
    """Ліниво читає текст документа з jsonl за байтовим зміщенням (корпус у пам'ять не вантажиться).

    Один дескриптор на всі читання: seek + readline — дві операції над спільною позицією файлу,
    тож без замка два потоки (веб-сервер, Lab 07) читали б рядки один одного.
    """

    def __init__(self, path: Path | str, prepared_cache: int = 512) -> None:
        self.path = Path(path)
        self._offsets: dict[str, int] | None = None
        self._fh: BinaryIO | None = None
        self._lock = threading.Lock()
        # Lab 08: підготовлений текст (prepare) кешується між запитами; lru_cache потокобезпечний
        self.prepared = functools.lru_cache(maxsize=prepared_cache)(self._prepared)

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

    def _prepared(self, doc_id: str) -> "Prepared | None":
        text = self.get(doc_id)
        return None if text is None else prepare(text)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def _hits_slow(text: str, wanted: set[str]) -> list[Hit]:
    """Lab 03: кожне слово тексту окремо через normalize (2×NFC + casefold) — ≈ 4 600 викликів
    на документ. Лишається для текстів, де нормалізація зсуває зміщення (див. prepare)."""
    hits = [(m.start(), m.end(), _normalize(m.group())) for m in _WORD.finditer(text)]
    return [h for h in hits if h[2] in wanted]


@functools.cache
def _shifting_chars() -> re.Pattern[str]:
    """Символи, через які normalize зсуває зміщення або межі токенів.

    * розширення: ß -> ss, ﬁ -> fi, İ -> i + U+0307 (довжина символу змінюється);
    * стиснення: знак, що буває другим у канонічній композиції. "H" + U+0331 у NFC лишається двома
      символами (великої готової літери немає), а після casefold "h" + U+0331 складається в один
      "ẖ" (U+1E96). Таких пар cased × знак — 55 тис. (перебір, README L8.3). Розширення й стиснення в
      одному тексті дають ту саму довжину, але зсунуті зміщення, тож порівняння довжин замало;
    * зміна категорії: U+0345 (знак) -> ι (літера) — межі токенів інші.
    Знаки, що ні з чим не складаються, безпечні: U+FE0F (variation selector після емодзі) є в усіх
    124 документах корпусу — перша версія цього списку відправляла на повільний шлях їх усі.
    Будується один раз (≈ 0.3 с) при першому сніпеті, а не при імпорті.
    """
    planes = [*range(0x20000), *range(0xE0000, 0xE1000)]
    composing = set[int]()
    for c in planes:
        parts = unicodedata.decomposition(chr(c)).split()
        if len(parts) == 2 and not parts[0].startswith("<"):  # канонічна пара (база, знак)
            composing.add(int(parts[1], 16))

    def is_mark(ch: str) -> bool:
        return unicodedata.category(ch)[0] == "M"

    bad = [
        c
        for c in planes
        if c in composing or len(n := _normalize(chr(c))) != 1 or is_mark(chr(c)) != is_mark(n)
    ]
    return re.compile("[" + "".join(re.escape(chr(c)) for c in bad) + "]")


@dataclass(frozen=True, slots=True)
class Prepared:
    """Текст, підготовлений для сніпетів ОДИН раз на документ (TextStore кешує його між запитами).
    norm — нормалізований текст, якщо його зміщення збігаються з оригіналом; інакше None."""

    text: str
    norm: str | None


def prepare(text: str) -> Prepared:
    norm = _normalize(text)  # той самий normalize, що й tokenize: ті самі токени, що в індексі
    aligned = (
        len(norm) == len(text)
        and unicodedata.is_normalized("NFC", text)
        and not _shifting_chars().search(text)
    )
    return Prepared(text, norm if aligned else None)


_LETTER = re.compile(r"\w")
_MARK = re.compile(f"[{_COMBINING}]")
_CONTINUES = re.compile(rf"[\w{_COMBINING}]")  # токен = літера, далі літери і комбінуючі знаки


def _is_token(norm: str, start: int, end: int) -> bool:
    """Чи norm[start:end] — ЦІЛИЙ токен: справа ніщо не продовжує слово, зліва — не частина слова.
    Знак зліва належить попередньому токену, лише якщо ланцюжок знаків упирається в літеру. Перша
    версія (lookbehind на «літеру або знак») помилялась — Hypothesis знайшов "a <U+0301>abc": знак
    після пробілу не входить у жоден токен, і "abc" — окремий токен."""
    if _CONTINUES.match(norm, end):
        return False
    i = start - 1
    while i >= 0 and _MARK.match(norm, i):
        i -= 1
    return i < 0 or not _LETTER.match(norm, i)


def _hits(doc: str | Prepared, wanted: Iterable[str]) -> list[Hit]:
    """Lab 08, гаряча точка №1. Lab 03 нормалізувала кожне з ≈ 4 600 слів документа на кожен запит.
    Тепер документ нормалізовано один раз (prepare), а терм шукається str.find — пошуком підрядка в C.
    Python бачить лише збіги (десятки, не тисячі) і перевіряє межі токена тільки в них.
    """
    doc = doc if isinstance(doc, Prepared) else prepare(doc)
    wanted = {w for w in wanted if _WORD.fullmatch(w)}  # не-токен (":", "a b") не збігається з жодним словом
    if not wanted:
        return []
    if doc.norm is None:
        return _hits_slow(doc.text, wanted)
    norm, hits = doc.norm, list[Hit]()
    for term in wanted:
        i = norm.find(term)
        while i != -1:
            if _is_token(norm, i, i + len(term)):
                hits.append((i, i + len(term), term))
            i = norm.find(term, i + 1)
    hits.sort()
    return hits


def make_snippet(
    text: str | Prepared,
    terms: Iterable[str],
    width: int = 80,
    mark: str = "**",
    fallback_at: int = 0,
    within: tuple[int, int] | None = None,
) -> str:
    """Вікно ~2*width символів із найбільшою кількістю РІЗНИХ термів запиту; збіги в **...**.
    Без збігів — текст із fallback_at (0 — початок; семантичний пошук передає найкращий шматок).
    within=(start, end) — брати лише збіги з цього проміжку (найкращого шматка)."""
    hits = _hits(text, terms)
    if within is not None:
        hits = [h for h in hits if within[0] <= h[0] and h[1] <= within[1]]
    text = text.text if isinstance(text, Prepared) else text

    if not hits:  # нічого підсвічувати (напр., запит лише з NOT або семантичний збіг без слів запиту)
        head = text[fallback_at : fallback_at + 2 * width].strip()
        return ("…" if fallback_at > 0 else "") + head + ("…" if len(text) > fallback_at + 2 * width else "")

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
