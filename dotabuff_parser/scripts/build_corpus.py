"""Збирає корпус findex: герої (як є) + предмети без оглядових сторінок і дублів-варіантів
+ інші категорії (--extra): механіки, руни.

    py scripts/scrape_dota2wiki.py --category Items --doc-type item --out data/items.jsonl
    git show 16768bc:data/dota2wiki.jsonl > data/heroes.jsonl   # корпус лише з героями (Lab 01–08)
    py scripts/scrape_dota2wiki.py --category Mechanics --doc-type mechanic --out data/mechanics.jsonl
    py scripts/scrape_dota2wiki.py --category Runes --doc-type rune --out data/runes.jsonl
    py scripts/build_corpus.py --heroes data/heroes.jsonl --items data/items.jsonl \
        --base data/items_base.json --extra data/mechanics.jsonl data/runes.jsonl \
        --out ../data/dota2wiki.jsonl

Порядок: герої, предмети, --extra — тож doc_id героїв (0..123) і предметів (124..374) не
змінюються, коли додаються нові категорії.

Що відкидається з Category:Items (звірено з OpenDota /api/constants/items):
* оглядові сторінки Items, Neutral Items, Recipe — списки, де згадано все підряд, тож вони
  виринали б угорі на будь-якому запиті про предмет;
* варіанти одного предмета (Bracer 1/2, Power Treads (Agility), Vambrace (Strength), ...) —
  майже однакові тексти, що займали б по три місця в top-5. Основні сторінки цих предметів у
  категорію не потрапляють (як Arc Warden у героях), тож вони завантажені окремо (--base).

Що відкидається з --extra (EXTRA_DROP): оглядова Mechanics; Charisma — вандалізм, такого
атрибута в грі немає; Armor/Old Mechanics — механіка до 7.31. Bounty Rune є і в Mechanics,
і в Runes — другу копію відкидає дедуплікація за id.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

OVERVIEW = {"Items", "Neutral Items", "Recipe"}
EXTRA_DROP = {"Mechanics", "Charisma", "Armor/Old Mechanics"}
VARIANT = re.compile(r"^(.+?) (?:\d+|\((?:Agility|Intelligence|Strength)\))$")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heroes", type=Path, required=True)
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True, help="JSON-список основних сторінок варіантів")
    ap.add_argument("--extra", type=Path, nargs="*", default=[], help="JSONL інших категорій")
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

    for path in a.extra:
        for line in path.read_text(encoding="utf-8").splitlines():
            doc = json.loads(line)
            if doc["title"] in EXTRA_DROP:
                dropped.append(doc["title"])
                continue
            if doc["id"] in seen or doc["id"] in hero_ids:
                continue
            seen.add(doc["id"])
            items.append(doc)

    with a.out.open("w", encoding="utf-8", newline="\n") as f:
        for line in hero_lines:
            f.write(line + "\n")
        for doc in items:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"{len(hero_lines)} героїв + {len(items)} інших документів -> {a.out}; відкинуто {len(dropped)}: {dropped}")


if __name__ == "__main__":
    main()
