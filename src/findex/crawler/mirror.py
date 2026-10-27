"""Локальне дзеркало корпусу: на ньому краулер можна ганяти скільки завгодно, нікого не навантажуючи.

    python -m findex.crawler.mirror --origins 5 --copies 2      # друкує "ready <url>" на кожен origin

Що це за сайт:
- `origins` серверів на 127.0.0.1 (різні порти = різні origin-и: robots.txt і ліміти ввічливості
  діють на scheme://host:port). Кожна стаття живе на одному з них, посилання ведуть між ними.
- Сторінка героя = стаття корпусу (Lab 01) як HTML; посилання — на героїв, згаданих у тексті,
  на наступну статтю і на ту саму статтю в наступній копії (copies > 1 дає > 200 сторінок).
- Затримка 50–150 мс на запит (детермінована від шляху) — модель мережі.
- Пастки на сторінках: `/private/...` (заборонено в robots.txt), `/loop/a` (redirect-цикл),
  `/missing/...` (404), `/files/....pdf` (не HTML), посилання на fandom.com (поза allowlist).
- Збої лише на ПЕРШИЙ запит до шляху (тож повтор їх лікує), детерміновано від crc32(шлях):
  ≈ 4 % — 503, ≈ 4 % — 429 з Retry-After: 1 (за crc32), і кожна 50-та стаття відповідає через
  `slow` секунд — довше за таймаут краулера (явно, бо 1 % від 248 сторінок міг випасти й нулем).
"""

import argparse
import html
import json
import re
import threading
import time
import zlib
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROBOTS_TXT = "User-agent: *\nDisallow: /private/\n"


@dataclass(frozen=True, slots=True)
class Article:
    id: str
    title: str
    text: str


@dataclass(slots=True)
class MirrorConfig:
    origins: int = 5
    copies: int = 2
    latency: tuple[float, float] = (0.05, 0.15)
    faults: bool = True
    slow: float = 3.0


def load_articles(corpus: Path) -> list[Article]:
    rows = (json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines() if line.strip())
    return [Article(str(r["id"]), str(r.get("title") or r["id"]), str(r["text"])) for r in rows]


def _h(s: str) -> int:
    return zlib.crc32(s.encode())


@dataclass(slots=True)
class Site:
    """Уся структура сайту — у пам'яті; сервери лише віддають готовий HTML."""

    config: MirrorConfig
    ports: list[int] = field(default_factory=list[int])
    pages: dict[tuple[int, str], str] = field(
        default_factory=dict[tuple[int, str], str]
    )  # (origin, path) -> HTML
    requests: Counter[tuple[int, str]] = field(default_factory=Counter[tuple[int, str]])
    lock: threading.Lock = field(default_factory=threading.Lock)
    slow_paths: set[str] = field(default_factory=set[str])

    def url(self, origin: int, path: str) -> str:
        return f"http://127.0.0.1:{self.ports[origin]}{path}"

    def build(self, articles: list[Article]) -> None:
        n, cfg = len(articles), self.config
        # Хто кого згадує: одна регулярка з усіх заголовків, межі слів, щоб "Io" не ловилось у "Lion".
        titles = sorted({a.title for a in articles}, key=len, reverse=True)
        mention = re.compile(r"\b(" + "|".join(map(re.escape, titles)) + r")\b")
        by_title = {a.title: i for i, a in enumerate(articles)}
        mentions = [
            sorted({by_title[m] for m in mention.findall(a.text) if by_title[m] != i})
            for i, a in enumerate(articles)
        ]

        def place(i: int, copy: int) -> tuple[int, str]:
            suffix = f"~{copy}" if copy else ""
            return (copy * n + i) % cfg.origins, f"/wiki/{articles[i].id}{suffix}"

        roots: dict[int, list[tuple[str, str]]] = {o: [] for o in range(cfg.origins)}
        for copy in range(cfg.copies):
            for i, art in enumerate(articles):
                o, path = place(i, copy)
                label = art.title + (f" (copy {copy})" if copy else "")
                if i % 50 == 25:
                    self.slow_paths.add(path)
                roots[o].append((self.url(o, path), label))
                links = [(self.url(*place(j, copy)), articles[j].title) for j in mentions[i]]
                links.append((self.url(*place((i + 1) % n, copy)), "Next article"))
                if copy + 1 < cfg.copies:
                    links.append((self.url(*place(i, copy + 1)), "Archived copy"))
                h = _h(path)
                links.append(
                    (f"https://dota2.fandom.com/wiki/{art.id}", "Original on Fandom")
                )  # поза allowlist
                links.append((self.url(o, f"/private/{art.id}"), "Editor notes"))  # robots.txt
                if h % 10 == 0:
                    links.append((self.url(o, "/loop/a"), "Old permalink"))  # redirect-цикл
                if h % 10 == 1:
                    links.append((self.url(o, f"/missing/{art.id}"), "Deleted page"))  # 404
                if h % 10 == 2:
                    links.append((self.url(o, f"/files/{art.id}.pdf"), "Printable PDF"))  # не HTML
                self.pages[(o, path)] = _render(label, art.text, links)
        for o in range(cfg.origins):
            other = [(self.url(p, "/"), f"Mirror {p}") for p in range(cfg.origins) if p != o]
            self.pages[(o, "/")] = _render(
                f"findex mirror {o}", "Dota 2 heroes (local mirror).", roots[o] + other
            )

    def latency(self, path: str) -> float:
        lo, hi = self.config.latency
        return lo + (_h(path + "#latency") % 1000) / 1000 * (hi - lo)

    def fault(self, origin: int, path: str) -> str | None:
        """Збій для ПЕРШОГО запиту до шляху: '503' | '429' | 'slow' | None."""
        if not self.config.faults or not path.startswith("/wiki/"):
            return None
        with self.lock:
            self.requests[(origin, path)] += 1
            first = self.requests[(origin, path)] == 1
        if not first:
            return None
        if path in self.slow_paths:
            return "slow"
        bucket = _h(path + "#fault") % 100
        return "503" if bucket < 4 else "429" if bucket < 8 else None


def _render(title: str, text: str, links: list[tuple[str, str]]) -> str:
    paras = "\n".join(f"<p>{html.escape(line)}</p>" for line in text.splitlines() if line.strip())
    items = "\n".join(f'<li><a href="{html.escape(u)}">{html.escape(t)}</a></li>' for u, t in links)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        f"<style>body{{font-family:sans-serif}}</style><script>var tracking = 'not text';</script></head>"
        f"<body><h1>{html.escape(title)}</h1>\n{paras}\n<ul>\n{items}\n</ul></body></html>"
    )


def _handler(site: Site, origin: int) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive: клієнт перевикористовує з'єднання
        server_version = "findex-mirror/1.0"

        def _send(
            self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8", **headers: str
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers.items():
                self.send_header(k.replace("_", "-"), v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            time.sleep(site.latency(path))  # «мережа»: потік сервера спить, клієнт чекає
            match site.fault(origin, path):
                case "503":
                    return self._send(503, b"temporarily unavailable", "text/plain")
                case "429":
                    return self._send(429, b"slow down", "text/plain", Retry_After="1")
                case "slow":
                    time.sleep(site.config.slow)
                case _:
                    pass
            if path == "/robots.txt":
                self._send(200, ROBOTS_TXT.encode(), "text/plain")
            elif path == "/loop/a":
                self._send(302, b"", Location="/loop/b")
            elif path == "/loop/b":
                self._send(302, b"", Location="/loop/a")
            elif path.startswith("/files/"):
                self._send(200, b"%PDF-1.4 fake", "application/pdf")
            elif (page := site.pages.get((origin, path))) is not None:
                self._send(200, page.encode())
            else:
                self._send(404, b"<h1>Not found</h1>")

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def handle_error(self, request: object, client_address: object) -> None:
        pass  # клієнт закрив з'єднання після таймауту — для дзеркала це норма


@contextmanager
def serve(articles: list[Article], config: MirrorConfig | None = None) -> Generator[Site]:
    """Підняти дзеркало у фонових потоках цього процесу (тести, експерименти)."""
    site = Site(config or MirrorConfig())
    servers = [_QuietServer(("127.0.0.1", 0), BaseHTTPRequestHandler) for _ in range(site.config.origins)]
    site.ports = [s.server_address[1] for s in servers]
    site.build(articles)
    for o, server in enumerate(servers):
        server.RequestHandlerClass = _handler(site, o)
        threading.Thread(target=server.serve_forever, daemon=True, name=f"mirror-{o}").start()
    try:
        yield site
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("corpus", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    parser.add_argument("--origins", type=int, default=5)
    parser.add_argument("--copies", type=int, default=2)
    parser.add_argument("--latency", type=float, nargs=2, default=(0.05, 0.15), metavar=("LO", "HI"))
    parser.add_argument("--no-faults", dest="faults", action="store_false")
    parser.add_argument("--slow", type=float, default=3.0, help="затримка «повільного» збою, с")
    args = parser.parse_args()
    config = MirrorConfig(args.origins, args.copies, tuple(args.latency), args.faults, args.slow)
    with serve(load_articles(args.corpus), config) as site:
        html_pages = sum(1 for (_, p) in site.pages if p.startswith("/wiki/"))
        print(f"{html_pages} article pages on {config.origins} origins", flush=True)
        for o in range(config.origins):
            print(f"ready {site.url(o, '/')}", flush=True)
        with suppress(KeyboardInterrupt):
            threading.Event().wait()


if __name__ == "__main__":
    main()
