"""M3: мова запитів. Рекурсивний спуск -> дерево вузлів, які перевантажують &, |, ~.

Граматика (AND, OR, NOT — лише ВЕЛИКИМИ літерами, як у Lucene; «or» малими — звичайне слово):

    or_expr  -> and_expr ('OR' and_expr)*
    and_expr -> not_expr (['AND'] not_expr)*        # неявний AND між сусідніми термінами
    not_expr -> 'NOT' not_expr | atom
    atom     -> WORD | "PHRASE" | '(' or_expr ')'
"""

import re
from dataclasses import dataclass
from functools import reduce

from findex.index import Index
from findex.tokenize import tokenize


class QueryParseError(ValueError):
    pass


class Node:
    """Базовий вузол. Оператори будують дерево, тож запит можна скласти без рядка:
    Term('python') & (Term('async') | Term('await')) & ~Term('java')"""

    def __and__(self, other: object) -> "Node":
        return And(self, other) if isinstance(other, Node) else NotImplemented

    def __or__(self, other: object) -> "Node":
        return Or(self, other) if isinstance(other, Node) else NotImplemented

    def __invert__(self) -> "Node":
        return Not(self)

    def evaluate(self, index: Index) -> set[int]:
        raise NotImplementedError

    def terms(self) -> list[str]:
        """Терми, що беруть участь в оцінці (під NOT — жодних)."""
        raise NotImplementedError

    def __str__(self) -> str:
        """Рядок запиту, який parse() перетворить назад на ТЕ САМЕ дерево (дужки — явно)."""
        raise NotImplementedError


@dataclass(frozen=True)
class Term(Node):
    text: str

    def evaluate(self, index: Index) -> set[int]:
        return {p.doc_id for p in index.get(self.text, ())}

    def terms(self) -> list[str]:
        return [self.text]

    def __str__(self) -> str:
        return self.text


def _follow(a: list[int] | tuple[int, ...], b: list[int] | tuple[int, ...]) -> list[int]:
    """Елементи q з b, для яких q-1 є в a (обидва відсортовані). Два вказівники."""
    out: list[int] = []
    i = j = 0
    while i < len(a) and j < len(b):
        target = a[i] + 1
        if b[j] == target:
            out.append(b[j])
            i += 1
            j += 1
        elif b[j] < target:
            j += 1
        else:
            i += 1
    return out


def has_phrase(position_lists: list[tuple[int, ...]]) -> bool:
    """Чи існує p, що p+i є в position_lists[i] для всіх i."""
    ends = list(position_lists[0])
    for nxt in position_lists[1:]:
        ends = _follow(ends, nxt)
        if not ends:
            return False
    return bool(ends)


@dataclass(frozen=True)
class Phrase(Node):
    words: tuple[str, ...]

    def evaluate(self, index: Index) -> set[int]:
        if not index.has_positions:
            raise ValueError(
                "Фразові запити потребують індексу з positions: "
                "findex index <корпус> --positions --out index.bin"
            )
        per_word = [{p.doc_id: p.positions for p in index.get(w, ())} for w in self.words]
        if any(not d for d in per_word):
            return set()
        candidates = set(per_word[0]).intersection(*per_word[1:])
        return {doc for doc in candidates if has_phrase([d[doc] for d in per_word])}

    def terms(self) -> list[str]:
        return list(self.words)

    def __str__(self) -> str:
        return '"' + " ".join(self.words) + '"'


@dataclass(frozen=True)
class And(Node):
    left: Node
    right: Node

    def evaluate(self, index: Index) -> set[int]:
        return self.left.evaluate(index) & self.right.evaluate(index)

    def terms(self) -> list[str]:
        return self.left.terms() + self.right.terms()

    def __str__(self) -> str:
        return f"({self.left} AND {self.right})"


@dataclass(frozen=True)
class Or(Node):
    left: Node
    right: Node

    def evaluate(self, index: Index) -> set[int]:
        return self.left.evaluate(index) | self.right.evaluate(index)

    def terms(self) -> list[str]:
        return self.left.terms() + self.right.terms()

    def __str__(self) -> str:
        return f"({self.left} OR {self.right})"


@dataclass(frozen=True)
class Not(Node):
    operand: Node

    def evaluate(self, index: Index) -> set[int]:
        return set(range(index.num_docs)) - self.operand.evaluate(index)

    def terms(self) -> list[str]:
        return []

    def __str__(self) -> str:
        return f"NOT {self.operand}"


# --- лексер ---------------------------------------------------------------------------
_TOKEN = re.compile(r'"([^"]*)"|(\()|(\))|([^\s()"]+)')
_KEYWORDS = {"AND", "OR", "NOT"}


def _lex(query: str) -> list[tuple[str, str]]:
    if query.count('"') % 2:
        raise QueryParseError("непарні лапки у запиті")
    tokens: list[tuple[str, str]] = []
    for m in _TOKEN.finditer(query):
        phrase, lp, rp, word = m.groups()
        if phrase is not None:
            tokens.append(("PHRASE", phrase))
        elif lp:
            tokens.append(("LP", "("))
        elif rp:
            tokens.append(("RP", ")"))
        elif word in _KEYWORDS:
            tokens.append(("KW", word))
        else:
            tokens.append(("WORD", word))
    return tokens


# --- парсер ---------------------------------------------------------------------------
class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _is_kw(self, word: str) -> bool:
        return self._peek() == ("KW", word)

    def parse(self) -> Node:
        if not self.tokens:
            raise QueryParseError("порожній запит")
        node = self.or_expr()
        if self._peek() is not None:
            raise QueryParseError("зайва закриваюча дужка")
        return node

    def or_expr(self) -> Node:
        node = self.and_expr()
        while self._is_kw("OR"):
            self.pos += 1
            node = Or(node, self.and_expr())
        return node

    def and_expr(self) -> Node:
        node = self.not_expr()
        while True:
            tok = self._peek()
            if tok is None or tok[0] == "RP" or self._is_kw("OR"):
                return node
            if self._is_kw("AND"):
                self.pos += 1
            node = And(node, self.not_expr())

    def not_expr(self) -> Node:
        if self._is_kw("NOT"):
            self.pos += 1
            return Not(self.not_expr())
        return self.atom()

    def atom(self) -> Node:
        tok = self._peek()
        if tok is None:
            raise QueryParseError("запит обірвався: очікувався терм")
        kind, value = tok
        if kind == "LP":
            self.pos += 1
            node = self.or_expr()
            if self._peek() != ("RP", ")"):
                raise QueryParseError("немає закриваючої дужки")
            self.pos += 1
            return node
        if kind == "KW":
            raise QueryParseError(f"неочікуваний оператор {value}")
        if kind == "RP":
            raise QueryParseError("неочікувана закриваюча дужка")
        self.pos += 1
        words = tuple(tokenize(value))  # та сама нормалізація, що й при індексації
        if not words:
            raise QueryParseError(f"у {value!r} немає символів для пошуку")
        if kind == "PHRASE" and len(words) > 1:
            return Phrase(words)
        # слово, що розпалось на кілька токенів (don't -> don, t), — це AND по токенах
        return reduce(And, (Term(w) for w in words))


def parse(query: str) -> Node:
    """'a OR b c' -> Or(Term('a'), And(Term('b'), Term('c')))"""
    return _Parser(_lex(query)).parse()
