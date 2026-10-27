"""Lab 06: асинхронний ввічливий краулер.

fetch.py   — один запит: timeout, повтори з backoff + jitter, Retry-After
robots.py  — RobotsCache: robots.txt по одному разу на origin
urls.py    — канонізація URL, розбір HTML (посилання, заголовок, текст)
crawl.py   — crawl(): frontier на asyncio.Queue, воркери в TaskGroup, async-генератор Page
mirror.py  — локальне дзеркало корпусу для бенчмарку й демо збоїв
"""

USER_AGENT = "findex-crawler/0.6 (+https://github.com/dwysuxx/findex; educational crawler, Lab 06)"
ROBOTS_AGENT = "findex-crawler"  # токен, який шукаємо в рядках User-agent: robots.txt
