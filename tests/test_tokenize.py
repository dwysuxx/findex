from findex.tokenize import tokenize


def test_basic_case():
    assert list(tokenize("Hello World")) == ["hello", "world"]


def test_empty_string():
    assert list(tokenize("")) == []


def test_mixed_case():
    assert list(tokenize("STRASSE Hello")) == ["strasse", "hello"]


def test_cyrillic():
    assert list(tokenize("Привет Мир")) == ["привет", "мир"]


def test_punctuation_only():
    assert list(tokenize("!!! ??? ,,,")) == []


def test_hyphenated_word_splits():
    assert list(tokenize("Anti-Mage")) == ["anti", "mage"]


def test_decimal_number_splits():
    assert list(tokenize("19 + 1.6")) == ["19", "1", "6"]