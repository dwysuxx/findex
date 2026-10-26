import json

import pytest

from findex.index import build_index
from findex.query import And, Not, Or, Phrase, QueryParseError, Term, parse

DOCS = [
    "python async await event loop python",
    "python java",
    "java event loop",
    "loop event python",
]


@pytest.fixture
def index(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text("\n".join(json.dumps({"id": i, "text": t}) for i, t in enumerate(DOCS)), encoding="utf-8")
    return build_index(p, positions=True)


# --- тесты на деревьях, а не на строках ----------------------------------------------
def test_implicit_and_binds_tighter_than_or():
    assert parse("a OR b c") == Or(Term("a"), And(Term("b"), Term("c")))
    assert parse("a OR b c") != And(Or(Term("a"), Term("b")), Term("c"))


def test_implicit_and_equals_explicit_and():
    assert parse("a b") == parse("a AND b") == And(Term("a"), Term("b"))


def test_parentheses_override_precedence():
    assert parse("(a OR b) c") == And(Or(Term("a"), Term("b")), Term("c"))


def test_not_and_phrase():
    assert parse("a AND NOT b") == And(Term("a"), Not(Term("b")))
    assert parse("NOT a") == Not(Term("a"))
    assert parse('x "event loop"') == And(Term("x"), Phrase(("event", "loop")))


def test_terms_are_normalised_like_the_index():
    assert parse("Python") == Term("python")


def test_operator_overloads_build_the_same_tree():
    built = Term("python") & (Term("async") | Term("await")) & ~Term("java")
    assert built == parse("python AND (async OR await) NOT java")


def test_lowercase_or_is_a_word():
    assert parse("a or b") == And(And(Term("a"), Term("or")), Term("b"))


@pytest.mark.parametrize("bad", ["", "   ", "(a", "a)", '"abc', "a AND", "AND a", "a NOT", "()"])
def test_parse_errors(bad):
    with pytest.raises(QueryParseError):
        parse(bad)


# --- evaluate ------------------------------------------------------------------------
def test_evaluate_boolean(index):
    assert parse("python").evaluate(index) == {0, 1, 3}
    assert parse("python java").evaluate(index) == {1}
    assert parse("java OR async").evaluate(index) == {0, 1, 2}
    assert parse("python NOT java").evaluate(index) == {0, 3}
    assert parse("NOT python").evaluate(index) == {2}
    assert parse("(java OR async) NOT python").evaluate(index) == {2}


def test_phrase_requires_adjacent_order(index):
    assert parse('"event loop"').evaluate(index) == {0, 2}  # док 3 содержит «loop event» — не фраза
    assert parse('"loop event"').evaluate(index) == {3}
    assert parse('"event python"').evaluate(index) == {3}  # «loop event python»
    assert parse('"python event"').evaluate(index) == set()  # слова є, але не поруч у такому порядку


def test_phrase_needs_positions(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text(json.dumps({"id": 0, "text": "a b"}), encoding="utf-8")
    with pytest.raises(ValueError, match="positions"):
        parse('"a b"').evaluate(build_index(p))


def test_node_terms_exclude_negated():
    assert parse("a NOT b OR c").terms() == ["a", "c"]
