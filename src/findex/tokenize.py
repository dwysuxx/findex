import re
import unicodedata
from collections.abc import Iterator

# \w у Python не включає комбінуючі знаки (категорія M). А casefold() і NFC їх породжують:
# "İ".casefold() == "i" + U+0307, і без цього класу "İstanbul" розпадався б на ["i", "stanbul"].
# Знайшов Hypothesis (tests/test_properties.py::test_word_is_one_token), мінімальний приклад — "İA".
# Перше виправлення перелічувало кілька блоків вручну; Hypothesis знайшов наступний знак поза
# ними — "שׁA" (U+FB2A -> ש + U+05C1). Тому клас тепер будується з unicodedata: усі Mn/Mc/Me тієї
# версії Unicode, що й \w (≈ 2 500 символів, ≈ 20 мс на імпорт). Знаки є лише в площинах 0-1 і 14.
# Токен починається з \w: «осиротілий» знак на початку слова до токена не входить.


def _mark_class() -> str:
    r"""Діапазони всіх комбінуючих знаків як вміст класу regex: "\U00000300-\U0000036f..."."""
    planes = (range(0x20000), range(0xE0000, 0xE1000))
    marks = [c for r in planes for c in r if unicodedata.category(chr(c))[0] == "M"]
    ranges: list[tuple[int, int]] = []
    for c in marks:
        if ranges and ranges[-1][1] == c - 1:
            ranges[-1] = (ranges[-1][0], c)
        else:
            ranges.append((c, c))
    return "".join(rf"\U{a:08x}-\U{b:08x}" for a, b in ranges)  # escape-и розбирає вже re


_COMBINING = _mark_class()
WORD = re.compile(rf"\w[\w{_COMBINING}]*")


def normalize(text: str) -> str:
    """NFC -> casefold -> знову NFC: casefold може розкласти літеру на базу + знак."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def tokenize(text: str) -> Iterator[str]:
    """Нормалізований текст -> слова (WORD). Лінивий генератор."""
    for match in WORD.finditer(normalize(text)):
        yield match.group(0)


if __name__ == "__main__":
    from itertools import islice
    from pathlib import Path

    from findex.corpus import iter_documents

    doc = next(iter_documents(Path("data/dota2wiki.jsonl")))
    print(list(islice(tokenize(doc.text or ""), 20)))
