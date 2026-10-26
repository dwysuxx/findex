"""Stretch: explain() через functools.singledispatch.

Одна узагальнена функція, реалізація обирається за ТИПОМ першого аргументу:

    explain(parse('a OR b c'))                    # дерево запиту
    explain(BM25(), index, doc_id, ["a", "b"])    # чому документ отримав такий score, по термах

Як _explain API в Elasticsearch: рейтинг, який можна пояснити, можна й налагодити.
"""

import math
from functools import singledispatch
from types import FunctionType
from typing import Any

from findex.index import Index
from findex.query import And, Node, Not, Or, Phrase, Term
from findex.ranking import BM25, Scorer, TfIdf


@singledispatch
def explain(obj: object, *args: Any, **kwargs: Any) -> str:  # Any: кожна реалізація має свою сигнатуру
    """Базова реалізація: тип, для якого нічого не зареєстровано."""
    raise TypeError(f"explain() не вміє пояснювати {type(obj).__name__}")


# --- дерева запитів -------------------------------------------------------------------
def _children(node: Node) -> list[Node]:
    """Розгортає ланцюжки однакових операторів: And(And(a, b), c) -> [a, b, c]."""
    if isinstance(node, (And, Or)):
        out: list[Node] = []
        for child in (node.left, node.right):
            out.extend(_children(child) if type(child) is type(node) else [child])
        return out
    if isinstance(node, Not):
        return [node.operand]
    return []


def _tree(label: str, node: Node) -> str:
    lines = [label]
    kids = _children(node)
    for i, kid in enumerate(kids):
        last = i == len(kids) - 1
        first, *rest = explain(kid).splitlines()
        lines.append(("└── " if last else "├── ") + first)
        lines.extend(("    " if last else "│   ") + line for line in rest)
    return "\n".join(lines)


@explain.register
def _(node: Term) -> str:
    return f"TERM {node.text}"


@explain.register
def _(node: Phrase) -> str:
    return f'PHRASE "{" ".join(node.words)}"'


@explain.register
def _(node: And) -> str:
    return _tree("AND", node)


@explain.register
def _(node: Or) -> str:
    return _tree("OR", node)


@explain.register
def _(node: Not) -> str:
    return _tree("NOT", node)


# --- скорери --------------------------------------------------------------------------
def _header(scorer: object, index: Index, doc_id: int, total: float) -> str:
    title = index.doc_meta[doc_id].title if doc_id in index.doc_meta else "?"
    return f"{total:.3f} = {scorer!r} для doc {doc_id} ({title})"


def _posting(index: Index, term: str, doc_id: int):
    return next((p for p in index.get(term, ()) if p.doc_id == doc_id), None)


@explain.register
def _(scorer: BM25, index: Index, doc_id: int, terms: list[str]) -> str:
    n, avg, dl = index.num_docs, index.avg_doc_length, index.doc_length(doc_id)
    norm = 1.0 - scorer.b + scorer.b * (dl / avg if avg else 1.0)
    lines: list[str] = []
    total = 0.0
    for term in dict.fromkeys(terms):
        p = _posting(index, term, doc_id)
        if p is None:
            lines.append(f"  0.000 = {term!r}: немає в документі")
            continue
        df = index.df(term)
        idf = scorer.idf(df, n)
        tfn = p.tf * (scorer.k1 + 1.0) / (p.tf + scorer.k1 * norm)
        total += idf * tfn
        lines.append(
            f"  {idf * tfn:.3f} = {term!r}: idf {idf:.3f} (df={df}, N={n})"
            f" × tf_norm {tfn:.3f} (tf={p.tf}, k1={scorer.k1})"
        )
    lines.append(
        f"  довжина: dl={dl}, avgdl={avg:.1f}, b={scorer.b} → norm = 1 − b + b·dl/avgdl = {norm:.3f}"
    )
    return "\n".join([_header(scorer, index, doc_id, total), *lines])


@explain.register
def _(scorer: TfIdf, index: Index, doc_id: int, terms: list[str]) -> str:
    n = index.num_docs
    lines: list[str] = []
    total = 0.0
    for term in dict.fromkeys(terms):
        p = _posting(index, term, doc_id)
        if p is None:
            lines.append(f"  0.000 = {term!r}: немає в документі")
            continue
        df = index.df(term)
        tf_w, idf = 1.0 + math.log(p.tf), math.log(n / df)
        total += tf_w * idf
        lines.append(f"  {tf_w * idf:.3f} = {term!r}: (1 + ln {p.tf}) {tf_w:.3f} × ln({n}/{df}) {idf:.3f}")
    return "\n".join([_header(scorer, index, doc_id, total), *lines])


@explain.register(FunctionType)
def _explain_generic(scorer: Scorer, index: Index, doc_id: int, terms: list[str]) -> str:
    """Scorer-функція: внески по термах без розкладу формули (формули ми не знаємо).
    Реєструємо на FunctionType, а не на Callable: pyright не приймає ABC-спецформу в register()."""
    lines: list[str] = []
    total = 0.0
    for term in dict.fromkeys(terms):
        p = _posting(index, term, doc_id)
        s = scorer(term, p, index) if p is not None else 0.0
        total += s
        lines.append(f"  {s:.3f} = {term!r}" + ("" if p is not None else ": немає в документі"))
    name = getattr(scorer, "__name__", None) or repr(scorer)
    return "\n".join([f"{total:.3f} = {name} для doc {doc_id}", *lines])
