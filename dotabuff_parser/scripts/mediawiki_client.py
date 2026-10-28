"""Небольшой клиент для MediaWiki API (используется Fandom-вики, в т.ч. dota2.fandom.com).

Общая логика вынесена сюда, чтобы её можно было переиспользовать для любых
других MediaWiki-сайтов (Liquipedia и т.п.) — меняется только BASE_URL.
"""
from __future__ import annotations

import time
from typing import Iterator

import requests

USER_AGENT = "corpus-research-bot/0.1 (student project; contact: ivan.sidorenko373@gmail.com)"


class MediaWikiClient:
    def __init__(self, base_url: str, sleep_between_requests: float = 0.3, max_retries: int = 5):
        self.api_url = f"{base_url.rstrip('/')}/api.php"
        self.sleep = sleep_between_requests
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _get(self, params: dict) -> dict:
        params = {**params, "format": "json"}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(self.api_url, params=params, timeout=30)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                time.sleep(self.sleep)
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                backoff = min(2 ** attempt, 30)
                time.sleep(backoff)
        raise RuntimeError(f"MediaWiki API request failed after {self.max_retries} retries: {last_exc}")

    def category_members(self, category: str, namespace: int = 0) -> Iterator[dict]:
        """Yield {'pageid', 'title'} for every non-redirect page in a category."""
        cont: dict = {}
        while True:
            params = {
                "action": "query",
                "generator": "categorymembers",
                "gcmtitle": f"Category:{category}",
                "gcmlimit": 500,
                "gcmnamespace": namespace,
                "prop": "info",
                **cont,
            }
            data = self._get(params)
            pages = data.get("query", {}).get("pages", {})
            for page in pages.values():
                if "redirect" in page:
                    continue
                yield {"pageid": page["pageid"], "title": page["title"]}
            if "continue" in data:
                cont = data["continue"]
            else:
                break

    def parse_page(self, title: str, props: str = "wikitext|text", redirects: bool = False) -> dict:
        """Return dict with requested props plus 'resolved_title' (post-redirect).

        redirects=True auto-follows a #REDIRECT page to its target's content —
        needed because on this wiki some canonical hero names are just
        redirects to a page with a differently-worded title.
        """
        params = {"action": "parse", "page": title, "prop": props}
        if redirects:
            params["redirects"] = 1
        data = self._get(params)
        parsed = data.get("parse")
        if not parsed:
            raise ValueError(f"Page not found or unparsable: {title!r} ({data.get('error')})")
        result = {"resolved_title": parsed.get("title", title)}
        if "wikitext" in parsed:
            result["wikitext"] = parsed["wikitext"]["*"]
        if "text" in parsed:
            result["text"] = parsed["text"]["*"]
        return result
