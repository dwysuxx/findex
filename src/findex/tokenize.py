import re
import unicodedata
from collections.abc import Iterator

# \w у Python не включає комбінуючі знаки (категорія M). А casefold() їх породжує:
# "İ".casefold() == "i" + U+0307, і без цього класу "İstanbul" розпадався б на ["i", "stanbul"].
# Знайшов Hypothesis (tests/test_properties.py::test_word_is_one_token), мінімальний приклад — "İA".
# Токен починається з \w: «осиротілий» знак на початку слова до токена не входить.
_COMBINING = "̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯"
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
