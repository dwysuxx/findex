"""Lab 08, гаряча точка №1: швидкий пошук збігів у сніпетах дає ТІ САМІ збіги, що Lab 03."""

import json
import random

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from conftest import REAL_CORPUS
from findex.snippets import _hits, _hits_slow, make_snippet  # pyright: ignore[reportPrivateUsage]
from findex.tokenize import tokenize

# Символи, на яких ламалися попередні версії швидкого шляху: розширення (ß, İ, ﬁ), стиснення
# (H + U+0331 -> ẖ), знак, що стає літерою (U+0345), і знаки, що ні з чим не складаються (U+FE0F).
TRICKY = st.sampled_from(list("aAhHßẞİıΣσςﬁ ̱́̇ͅ️‍-_1é▶"))


@settings(max_examples=600, deadline=None)
@given(
    text=st.text(alphabet=TRICKY | st.characters(codec="utf-8")),
    extra=st.lists(st.text(min_size=1, max_size=5), max_size=3),
    data=st.data(),
)
@example(text="Straße STRASSE strasse", extra=["strasse"], data=None)  # ß -> ss: повільний шлях
@example(text="İstanbul istanbul", extra=[], data=None)  # İ -> i + U+0307
@example(text="a ́abc abc", extra=["abc"], data=None)  # «осиротілий» знак перед словом
@example(text="ß H̱ ab ab", extra=["ab"], data=None)  # +1 і −1: довжина та сама, зміщення — ні
@example(text="▶️ abc a️bc", extra=["abc", "bc"], data=None)  # U+FE0F — частина токена
def test_fast_hits_equal_slow_hits(text: str, extra: list[str], data: st.DataObject | None) -> None:
    tokens = sorted(set(tokenize(text)))
    wanted = set(extra)
    if tokens and data is not None:
        wanted |= set(data.draw(st.lists(st.sampled_from(tokens), max_size=4)))
    elif tokens:
        wanted |= set(tokens)
    assert _hits(text, wanted) == _hits_slow(text, wanted)


@pytest.mark.slow
def test_fast_hits_equal_slow_hits_on_real_corpus() -> None:
    if not REAL_CORPUS.is_file():
        pytest.skip("немає data/dota2wiki.jsonl")
    rng = random.Random(8)
    for line in REAL_CORPUS.read_text("utf-8").splitlines():
        text = str(json.loads(line)["text"]).replace("\n", " ")
        vocab = sorted(set(tokenize(text)))
        for wanted in [{"hero", "damage", "attack"}, {"meat", "hook"}, set(rng.sample(vocab, 5)), {"ß", "ﬁ"}]:
            assert _hits(text, wanted) == _hits_slow(text, wanted)
            assert make_snippet(text, wanted)  # і сам сніпет будується


def test_snippet_same_as_lab03(monkeypatch: pytest.MonkeyPatch) -> None:
    import findex.snippets as sn

    text = "Pudge throws his Meat Hook. The hook drags. MEAT-HOOK again " * 5
    fast = [make_snippet(text, t, width=w) for t in (["meat", "hook"], ["again"], []) for w in (20, 80)]
    monkeypatch.setattr(sn, "_hits", sn._hits_slow)  # pyright: ignore[reportPrivateUsage]
    slow = [make_snippet(text, t, width=w) for t in (["meat", "hook"], ["again"], []) for w in (20, 80)]
    assert fast == slow
