"""Підсвітка для HTML. Спершу екрануємо текст, потім додаємо <mark> — тож текст документа
ніколи не потрапляє в розмітку сирим (у вікі-тексті трапляються і <, і &)."""

import re
from collections.abc import Iterable

from markupsafe import Markup, escape

from findex.tokenize import WORD, normalize

_MARK = re.compile(r"\*\*(.+?)\*\*")


def snippet_html(snippet: str) -> Markup:
    """**терм** з make_snippet -> <mark>терм</mark>; зірочки не є HTML, тож escape їх не чіпає."""
    return Markup(_MARK.sub(r"<mark>\1</mark>", str(escape(snippet))))


def highlight_html(text: str, terms: Iterable[str]) -> tuple[Markup, int]:
    """Увесь текст документа з <mark> на кожному слові, що нормалізується в один із термів,
    і кількість таких слів.

    Той самий WORD і normalize, що й у токенайзері: підсвічується рівно те, що знайшов індекс.
    """
    wanted = set(terms)
    if not wanted:
        return escape(text), 0
    out: list[str] = []
    cursor = marks = 0
    for m in WORD.finditer(text):
        if normalize(m.group()) in wanted:
            out.append(str(escape(text[cursor : m.start()])))
            out.append(f"<mark>{escape(m.group())}</mark>")
            cursor = m.end()
            marks += 1
    out.append(str(escape(text[cursor:])))
    return Markup("".join(out)), marks


def pretty_title(title: str) -> str:
    """anti_mage -> Anti Mage (id статті у вікі -> заголовок для людей)."""
    return title.replace("_", " ").strip().title() or title
