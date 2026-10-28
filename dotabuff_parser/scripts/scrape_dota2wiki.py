"""Скрейпер Dota 2 Wiki (dota2.fandom.com) в JSONL-корпус для поиска.

Герои (--doc-type hero) забираются не через обход категории, а напрямую по
официальному списку из OpenDota API — см. docstring fetch_opendota_hero_names()
для причины (вандализм и неполная категоризация на Fandom-вики).

Пример запуска (быстрая проверка на маленьком куске):
    py scripts/scrape_dota2wiki.py --doc-type hero --limit 5

Полный прогон:
    py scripts/scrape_dota2wiki.py --doc-type hero

Прочие категории (когда дойдём до Items/Abilities/Patches) идут через обычный
обход категории вики:
    py scripts/scrape_dota2wiki.py --category Items --doc-type item

Результат по умолчанию: data/dota2wiki.jsonl (append — повторный запуск
дописывает; дедупликацию по id при мердже нескольких прогонов делать отдельно).
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from clean_html import html_to_text  # noqa: E402
from mediawiki_client import MediaWikiClient, USER_AGENT  # noqa: E402

BASE_URL = "https://dota2.fandom.com"
OPENDOTA_HEROES_URL = "https://api.opendota.com/api/heroes"

INTERN_RE = re.compile(r"\|\s*intern\s*=\s*(\S+)")


def fetch_opendota_hero_names() -> dict[str, str]:
    """Внутреннее имя движка (npc_dota_hero_*) -> каноничное имя героя.

    Fandom-вики открыта для правок кем угодно. В Category:Heroes находились
    вандальные страницы (League of Legends "герои" Yasuo/Irelia, пустой
    "Ringmaster"), а легитимная страница Arc Warden в категорию вообще не
    попадает (использует другой шаблон инфобокса, без автокатегоризации).
    Поэтому категория как источник списка героев ненадёжна: используем
    официальный список настоящих героев из OpenDota как единственный source
    of truth и для перебора страниц по имени, и для сверки intern= на случай
    вандализма содержимого под правильным заголовком.
    """
    resp = requests.get(OPENDOTA_HEROES_URL, timeout=30, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return {h["name"]: h["localized_name"] for h in resp.json()}


def slugify(title: str) -> str:
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


def make_doc(title: str, doc_type: str, resolved_page_title: str, text: str) -> dict:
    return {
        "id": slugify(title),
        "title": title,
        "category": doc_type,
        "url": f"{BASE_URL}/wiki/{resolved_page_title.replace(' ', '_')}",
        "text": text,
        "retrieved_at": datetime.date.today().isoformat(),
    }


def scrape_heroes(client: MediaWikiClient, hero_names: dict[str, str], limit: int | None):
    count = 0
    for npc, localized_name in hero_names.items():
        if limit is not None and count >= limit:
            break
        try:
            # redirects=1 — часть героев (Legion Commander, Abaddon, ...) на
            # этой вики это редирект на страницу с искажённым заголовком, где
            # и лежит настоящий контент; API сам разруливает переход.
            parsed = client.parse_page(localized_name, props="wikitext|text", redirects=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [!] нет страницы для {localized_name!r}: {exc}", file=sys.stderr)
            continue

        intern_match = INTERN_RE.search(parsed["wikitext"])
        intern = intern_match.group(1).strip() if intern_match else None
        if intern != npc:
            print(
                f"  [x] {localized_name!r}: intern={intern!r}, ожидался {npc!r} — похоже на вандализм, пропуск",
                file=sys.stderr,
            )
            continue

        text = html_to_text(parsed["text"])
        yield make_doc(localized_name, "hero", parsed["resolved_title"], text)
        count += 1
        print(f"  [{count}] {localized_name}", file=sys.stderr)


def scrape_category(client: MediaWikiClient, category: str, doc_type: str, limit: int | None):
    count = 0
    for member in client.category_members(category):
        if limit is not None and count >= limit:
            break
        page_title = member["title"]
        try:
            parsed = client.parse_page(page_title, props="text")
            text = html_to_text(parsed["text"])
        except Exception as exc:  # noqa: BLE001
            print(f"  [!] пропуск {page_title!r}: {exc}", file=sys.stderr)
            continue

        yield make_doc(page_title, doc_type, page_title, text)
        count += 1
        print(f"  [{count}] {page_title}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--category", default=None, help="Категория вики (не нужно для --doc-type hero)")
    parser.add_argument("--doc-type", required=True, help="Значение поля category в документе, например hero")
    parser.add_argument("--limit", type=int, default=None, help="Максимум страниц (для быстрой проверки)")
    parser.add_argument("--out", default="data/dota2wiki.jsonl", help="Путь к выходному JSONL")
    parser.add_argument("--sleep", type=float, default=0.3, help="Пауза между запросами к API, сек")
    args = parser.parse_args()

    if args.doc_type != "hero" and not args.category:
        parser.error("--category обязателен для всех doc-type кроме hero")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    client = MediaWikiClient(BASE_URL, sleep_between_requests=args.sleep)

    with out_path.open("a", encoding="utf-8") as f:
        if args.doc_type == "hero":
            print("Сверяю с официальным списком героев OpenDota...", file=sys.stderr)
            hero_names = fetch_opendota_hero_names()
            print(f"  {len(hero_names)} настоящих героев в базе OpenDota", file=sys.stderr)
            docs = scrape_heroes(client, hero_names, args.limit)
        else:
            print(f"Категория: {args.category} -> {out_path}", file=sys.stderr)
            docs = scrape_category(client, args.category, args.doc_type, args.limit)

        for doc in docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    print("Готово.", file=sys.stderr)


if __name__ == "__main__":
    main()
