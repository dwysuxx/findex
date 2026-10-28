"""Приведение отрендеренного HTML со страницы MediaWiki к чистому тексту."""
from __future__ import annotations

from bs4 import BeautifulSoup

# Селекторы чисто оформительского/повторяющегося мусора, не несущего уникального
# содержания по конкретной странице (см. обсуждение в CLAUDE.md / чате):
#   .mw-editsection  — иконки-ссылки "[edit]" рядом с заголовками
#   .toc             — авто-оглавление, дублирует структуру заголовков
#   .page-tabber     — блок вкладок навигации, идентичен на всех страницах героев
#   .nowraplinks     — стандартный класс MediaWiki-навбоксов (списки ссылок внизу страницы)
#   script, style    — не текст вообще
#   sup.reference    — маркеры сносок вида "[1]"
NOISE_SELECTORS = [
    ".mw-editsection",
    ".toc",
    ".page-tabber",
    ".nowraplinks",
    "script",
    "style",
    "sup.reference",
]


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    root = soup.find("div", class_="mw-parser-output") or soup

    for selector in NOISE_SELECTORS:
        for tag in root.select(selector):
            tag.decompose()

    text = root.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)
