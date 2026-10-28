"""Збирає корпус findex: герої (як є) + предмети без оглядових сторінок і дублів-варіантів.

    py scripts/scrape_dota2wiki.py --category Items --doc-type item --out data/items.jsonl
    git show 16768bc:data/dota2wiki.jsonl > data/heroes.jsonl   # корпус лише з героями (Lab 01–08)
    py scripts/build_corpus.py --heroes data/heroes.jsonl --items data/items.jsonl \
        --base data/items_base.json --out ../data/dota2wiki.jsonl

Герої лишаються першими й у тому самому порядку: doc_id 0..123 у findex не змінюються.

Що відкидається з Category:Items (звірено з OpenDota /api/constants/items):
* оглядові сторінки Items, Neutral Items, Recipe — списки, де згадано все підряд, тож вони
  виринали б угорі на будь-якому запиті про предмет;
* варіанти одного предмета (Bracer 1/2, Power Treads (Agility), Vambrace (Strength), ...) —
  майже однакові тексти, що займали б по три місця в top-5. Основні сторінки цих предметів у
  категорію не потрапляють (як Arc Warden у героях), тож вони завантажені окремо (--base).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

OVERVIEW = {"Items", "Neutral Items", "Recipe"}
VARIANT = re.compile(r"^(.+?) (?:\d+|\((?:Agility|Intelligence|Strength)\))$")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heroes", type=Path, required=True)
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True, help="JSON-список основних сторінок варіантів")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    hero_lines = [line for line in a.heroes.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(json.loads(line).get("category", "hero") != "hero" for line in hero_lines):
        raise SystemExit(f"{a.heroes} уже містить не лише героїв — передайте корпус лише з героями")
    hero_ids = {json.loads(line)["id"] for line in hero_lines}

    base = {d["title"]: d for d in json.loads(a.base.read_text(encoding="utf-8"))}
    items: list[dict] = []
    seen: set[str] = set()
    dropped: list[str] = []
    for line in a.items.read_text(encoding="utf-8").splitlines():
        doc = json.loads(line)
        title = doc["title"]
        m = VARIANT.match(title)
        if title in OVERVIEW:
            dropped.append(title)
            continue
        if m and m.group(1) in base:
            dropped.append(title)
            doc = base[m.group(1)]  # основна сторінка — на місці першого варіанта
        if doc["id"] in seen or doc["id"] in hero_ids:
            continue
        seen.add(doc["id"])
        items.append(doc)

    with a.out.open("w", encoding="utf-8", newline="\n") as f:
        for line in hero_lines:
            f.write(line + "\n")
        for doc in items:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"{len(hero_lines)} героїв + {len(items)} предметів -> {a.out}; відкинуто {len(dropped)}: {dropped}")


if __name__ == "__main__":
    main()
