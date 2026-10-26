"""Property-based тести (Hypothesis): властивості, а не приклади."""

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from findex.corpus import Document
from findex.index import build_from_documents
from findex.query import And, Node, Not, Or, Phrase, Term, parse
from findex.ranking import BM25
from findex.search import and_merge, not_merge, or_merge
from findex.store import load, save
from findex.tokenize import tokenize

sorted_ids = st.lists(st.integers(min_value=0, max_value=200), unique=True).map(sorted)


# --- 1. оракул: швидкий merge збігається з очевидно правильною версією на множинах ---------
@given(sorted_ids, sorted_ids)
def test_merges_agree_with_set_oracle(a: list[int], b: list[int]) -> None:
    assert and_merge(a, b) == sorted(set(a) & set(b))
    assert or_merge(a, b) == sorted(set(a) | set(b))
    assert not_merge(a, b) == sorted(set(a) - set(b))


# --- 2. токенайзер: ідемпотентність і «кожен токен — нерухома точка» ------------------------
@given(st.text())
def test_tokenize_idempotent(text: str) -> None:
    tokens = list(tokenize(text))
    assert all(tokens)  # порожніх токенів не буває
    assert list(tokenize(" ".join(tokens))) == tokens


@given(st.text(alphabet=st.characters(categories=["Lu", "Ll", "Lt", "Lo"]), min_size=1))
def test_word_is_one_token(word: str) -> None:
    """Слово з самих літер — рівно один токен. Ця властивість знайшла баг: "İA" -> ["i", "a"]
    (casefold дає i + U+0307, а \\w не матчить комбінуючий знак)."""
    assert len(list(tokenize(word))) == 1


# --- 3. round trip: load(save(index)) == index для будь-якого корпусу ----------------------
texts = st.lists(st.text(alphabet=st.characters(categories=["L", "N", "Zs", "P"]), max_size=60), max_size=8)


@settings(max_examples=40, deadline=None)  # deadline=None: запис на диск на Windows буває повільним
@given(texts, st.booleans(), st.sampled_from(["pickle", "json"]))
def test_save_load_round_trip_property(docs: list[str], positions: bool, fmt) -> None:
    index = build_from_documents(
        (Document(f"d{i}", Path(f"c#{i}"), t) for i, t in enumerate(docs)), positions=positions
    )
    with tempfile.TemporaryDirectory() as tmp:  # не tmp_path: function-фікстура не скидається між прикладами
        path = Path(tmp) / "i"
        save(index, path, fmt)
        assert load(path, fmt) == index


# --- 4. парсер: str(tree) перепарситься в те саме дерево ----------------------------------
word = st.from_regex(r"[a-z0-9а-я]{1,8}", fullmatch=True)
leaves = word.map(Term) | st.lists(word, min_size=2, max_size=3).map(lambda ws: Phrase(tuple(ws)))
trees: st.SearchStrategy[Node] = st.recursive(
    leaves,
    lambda kids: st.builds(And, kids, kids) | st.builds(Or, kids, kids) | st.builds(Not, kids),
    max_leaves=12,
)


@given(trees)
def test_parse_render_round_trip(tree: Node) -> None:
    assert parse(str(tree)) == tree


# --- 5. BM25: монотонний за tf і обмежений idf·(k1+1) -------------------------------------
@given(
    st.integers(1, 1000),
    st.integers(1, 100),
    st.integers(1, 5000),
    st.floats(0.0, 1.0),
)
def test_bm25_monotone_and_bounded(tf: int, df: int, doc_len: int, b: float) -> None:
    bm, n, avg = BM25(b=b), 1000, 300.0
    s, s_next = bm.term_score(tf, df, doc_len, n, avg), bm.term_score(tf + 1, df, doc_len, n, avg)
    assert 0 < s < s_next <= bm.idf(df, n) * (bm.k1 + 1)
