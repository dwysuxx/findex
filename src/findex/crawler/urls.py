"""Канонізація URL і розбір HTML. Чисті функції без I/O — легко тестувати."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def canonicalize(url: str) -> str | None:
    """Одна адреса — один рядок, щоб `seen` не пропустив дубль.

    Схема й хост — у нижньому регістрі, порт за замовчуванням прибрано, фрагмент (#...)
    відкинуто, порожній шлях -> "/", кінцевий слеш у не-кореневому шляху прибрано,
    параметри запиту відсортовано. Не http(s) -> None.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port  # ValueError на сміттєвому порту
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or not parts.hostname:
        return None
    host = parts.hostname.lower()  # hostname уже без userinfo і порту
    if ":" in host:  # IPv6: hostname віддає адресу без квадратних дужок
        host = f"[{host}]"
    if port is not None and port != DEFAULT_PORTS[scheme]:
        host = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, host, path, query, ""))


def origin(url: str) -> str:
    """scheme://host[:port] — одиниця, для якої діють robots.txt і ліміти ввічливості."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def host_allowed(url: str, allowed_domains: frozenset[str]) -> bool:
    """Хост збігається з дозволеним доменом або є його піддоменом (docs.x.org для x.org)."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in allowed_domains)


@dataclass(slots=True)
class ParsedPage:
    title: str
    text: str
    links: list[str] = field(default_factory=list[str])


class _Extractor(HTMLParser):
    SKIP = frozenset({"script", "style", "noscript", "template"})

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "base":  # <base href> змінює, відносно чого розв'язувати посилання
            href = dict(attrs).get("href")
            if href:
                self.base_url = urljoin(self.base_url, href)
        elif tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("javascript:", "mailto:", "tel:")):
                self.links.append(urljoin(self.base_url, href))

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        (self.title_parts if self._in_title else self.text_parts).append(data)


def parse_html(html: str, base_url: str) -> ParsedPage:
    """HTML -> заголовок, видимий текст (пробіли згорнуто) і абсолютні посилання (без дублів).

    Це CPU-робота: crawl() викликає її через asyncio.to_thread, а не в корутині.
    """
    parser = _Extractor(base_url)
    parser.feed(html)
    parser.close()
    links: list[str] = []
    seen: set[str] = set()
    for link in parser.links:
        canon = canonicalize(link)
        if canon and canon not in seen:
            seen.add(canon)
            links.append(canon)
    title = " ".join("".join(parser.title_parts).split())
    text = " ".join(" ".join(parser.text_parts).split())
    return ParsedPage(title, text, links)
