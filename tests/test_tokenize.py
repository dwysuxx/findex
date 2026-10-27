import pytest

from findex.tokenize import tokenize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello World", ["hello", "world"]),
        ("", []),
        ("   \n\t ", []),
        ("!!! ??? ,,,", []),
        ("STRASSE Hello", ["strasse", "hello"]),
        ("Straße", ["strasse"]),  # casefold, а не lower: ß -> ss
        ("Привет Мир", ["привет", "мир"]),
        ("Ґанок, ЇЖАК і є", ["ґанок", "їжак", "і", "є"]),
        ("Anti-Mage", ["anti", "mage"]),
        ("19 + 1.6", ["19", "1", "6"]),
        ("snake_case word", ["snake_case", "word"]),
        ("été", ["été"]),  # NFD -> NFC: «e + наголос» склеюється в одну літеру
        ("ﬁle", ["file"]),  # лігатура ﬁ: casefold розкладає її на f + i
        ("日本語 テキスト", ["日本語", "テキスト"]),
        ("emoji 🙂 here", ["emoji", "here"]),
        # регресія з Hypothesis: casefold("İ") = "i" + U+0307; знак лишається в токені
        ("İstanbul", ["i̇stanbul"]),
        ("İA", ["i̇a"]),
        # друга регресія: U+FB2A -> ש + U+05C1, знак поза блоками, що були перелічені вручну
        ("שׁA", ["שׁa"]),
        ("किताब", ["किताब"]),  # деванагарі: किताब — одне слово
        ("ǰ", ["ǰ"]),  # casefold розкладає, NFC після нього складає назад
        ("́abc", ["abc"]),  # осиротілий знак на початку не стає токеном
    ],
)
def test_tokenize(text: str, expected: list[str]) -> None:
    assert list(tokenize(text)) == expected


def test_tokenize_is_lazy() -> None:
    it = tokenize("a b c")
    assert next(it) == "a"  # генератор, а не список
