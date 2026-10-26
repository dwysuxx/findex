# findex — пошуковий рушій по Dota 2 wiki

[![CI](https://github.com/dwysuxx/findex/actions/workflows/ci.yml/badge.svg)](https://github.com/dwysuxx/findex/actions/workflows/ci.yml)

Корпус: `data/dota2wiki.jsonl`, 124 статті про героїв. Індекс з positions:
`Index(terms=16_106, docs=124, positions=True)`, середня довжина документа 4220 токенів.

- [Lab 04 — Typing, Testing, Packaging, CLI](#lab-04--typing-testing-packaging-cli)
- [Lab 03 — Ranking and the Object Model](#lab-03--ranking-and-the-object-model)
- [Lab 02 — The Inverted Index](#lab-02--the-inverted-index)

---

# Lab 04 — Typing, Testing, Packaging, CLI

`findex` — встановлюваний пакет з командою `findex`: pyright strict без помилок, 118 тестів
(з них 6 Hypothesis-властивостей), CLI на typer + rich, логування в stderr, CI на GitHub Actions.

## L4.1 Встановлення

```bash
# з релізу на GitHub (wheel, прикріплений до v0.4.0)
uv tool install https://github.com/dwysuxx/findex/releases/download/v0.4.0/findex-0.4.0-py3-none-any.whl

# або з локального wheel / сирців
uv build                                   # -> dist/findex-0.4.0-py3-none-any.whl і .tar.gz
uv tool install dist/findex-0.4.0-py3-none-any.whl
uv tool install .                          # прямо з репозиторію

findex --version                           # findex 0.4.0

# розробка: середовище з lockfile (uv.lock закомічено)
uv sync
```

## L4.2 CLI

```bash
findex index data/dota2wiki.jsonl --out index.bin --positions     # rich progress bar у stderr
findex index data/dota2wiki.jsonl -o small.json --limit 20        # формат за розширенням
findex search index.bin '"meat hook" OR rot' --k 5                # таблиця з підсвіченими сніпетами
findex search index.bin pudge --scorer tfidf --explain            # + дерево запиту і розклад score
findex search index.bin pudge --json | jq -r .title               # JSON lines у stdout
findex stats index.bin --top 10                                   # числа Lab 01, гарно
findex stats index.bin --json
findex -v  search ...     # INFO   (розмір кешу)
findex -vv search ...     # DEBUG  (@timed, cache HIT/MISS)
python -m findex ...      # те саме без встановлення
```

```
$ findex search index.bin '"meat hook" OR rot' -k 2
                       "meat hook" OR rot — bm25, top-2
┌───┬────────┬───────────────┬────────────────────────────────────────────────┐
│ # │  score │ документ      │ сніпет                                         │
├───┼────────┼───────────────┼────────────────────────────────────────────────┤
│ 1 │ 26.013 │ pudge         │ …throw of Pudge's signature Meat Hook strikes  │
│   │        │               │ fear into his foes. Disoriented, his foe       │
│   │        │               │ stumbles, gagging on a sudden stench of Rot .  │
│   │        │               │ As it looks up at the…                         │
├───┼────────┼───────────────┼────────────────────────────────────────────────┤
│ 2 │  3.787 │ dragon_knight │ …and frail, its wings tattered, its few        │
│   │        │               │ remaining scales stricken with scale-rot, its  │
│   │        │               │ fangs ground to nubs, and its fire-gouts no    │
│   │        │               │ more threatening than a…                       │
└───┴────────┴───────────────┴────────────────────────────────────────────────┘

$ findex search index.bin pudge -k 2 --json --no-snippets
{"rank": 1, "doc_id": 13, "title": "pudge", "score": 8.141226}
{"rank": 2, "doc_id": 91, "title": "slark", "score": 3.465375}
```

(У терміналі збіги в сніпетах підсвічені жовтим.)

**Правила виводу.** У stdout ідуть лише дані (таблиця або JSON). Прогрес-бар, логи й помилки
пишуться в stderr. Очікувані помилки дають один рядок і код виходу 1, без traceback:

```
$ findex search nope.bin x            ; echo $?
Помилка: файл індексу не знайдено: nope.bin
1
$ findex search index.bin '(pudge'    ; echo $?
Помилка: помилка в запиті '(pudge': немає закриваючої дужки
1
$ findex search README.md pudge
Помилка: не вдалося прочитати індекс README.md (UnpicklingError): invalid load key, '#'.
$ findex search small.bin '"meat hook"'      # індекс без --positions
Помилка: Фразові запити потребують індексу з positions: findex index <корпус> --positions --out index.bin
```

Неправильне значення опції (`--scorer nope`) — помилка використання typer/click, код 2.

```
$ findex -vv search index.bin pudge --repeat 2 -k 1 --no-snippets >/dev/null
DEBUG    findex.utils: load took 284.84 ms
DEBUG    findex.utils: Searcher._rank_ids took 0.07 ms
DEBUG    findex.engine: query='pudge' k=1 cache=MISS
DEBUG    findex.utils: Searcher.search took 1.33 ms
DEBUG    findex.engine: query='pudge' k=1 cache=HIT
DEBUG    findex.utils: Searcher.search took 0.60 ms
INFO     findex.cli: cache: CacheInfo(hits=1, misses=1, maxsize=256, currsize=1)
```

## L4.3 M1 — типізація (pyright strict)

`[tool.pyright] typeCheckingMode = "strict"`, `include = ["src"]`: **0 errors**. Що зроблено:

- `Scorer` — `typing.Protocol`. `BM25`, `TfIdf` і звичайна функція з тією самою сигнатурою
  задовольняють його без успадкування.
- Генератори повертають `Iterator[...]` (`tokenize`, `iter_documents`), а приймають `Iterable[...]`
  (`build_from_documents`, `count_tokens`). `@contextmanager`-функції повертають `Generator[Index]`.
- `Literal`: `Format = Literal["pickle", "json"]`, `Engine = Literal["merge", "set"]`,
  `ScorerName = Literal["bm25", "tfidf"]`. typer за тими самими анотаціями будує вибір значень
  і перевіряє його.
- PEP 695: `def timed[**P, R](fn: Callable[P, R]) -> Callable[P, R]` зберігає сигнатуру
  декорованої функції. `build_objects[P: (PlainPosting, SlotPosting)]`, псевдоніми `type Postings = ...`.
- `TypedDict` описує форму JSON-файлу індексу (`IndexJSON`, `DocMetaJSON`). `@overload` на
  `ResultList.__getitem__`: `int -> SearchResult`, `slice -> ResultList`.
- Звуження (narrowing): `load_pickle` перевіряє `isinstance(obj, Index)` (pickle повертає `Any`),
  `TextStore` присвоює `self._offsets = self._build()` замість `# type: ignore`.

Усі винятки з перевірки, кожен з причиною в коді:

| Де | Що | Чому |
|---|---|---|
| `engine.py` | `pyright: ignore[reportPrivateUsage]` на `functools._CacheInfo` | у типу `cache_info()` немає публічного імені |
| `index.py`, `ranking.py` | `type: ignore[assignment]` на `__hash__ = None` | змінний контейнер з `__eq__` не хешується (як `list`); typeshed чекає метод |
| `explain.py` | `*args: Any` у базовій `@singledispatch`-функції | кожна зареєстрована реалізація має свою сигнатуру |
| `cli.py` | `Literal` через звичайне присвоєння, а не `type X = ...` | typer читає анотації під час виконання і не розгортає `TypeAliasType` |

## L4.4 M2 — тести

`uv run pytest`: **118 passed** (≈ 4.5 с). `uv run pytest -m "not slow"`: 114 тестів за ≈ 2 с.

| Файл | Що перевіряє |
|---|---|
| `tests/conftest.py` | фікстури: крихітний корпус на диску (`scope="session"`), `tiny_index` (лише читати), `fresh_index` (на кожен тест), збережений файл індексу |
| `test_tokenize.py` | параметризована таблиця на 19 випадків: кирилиця, `ß→ss`, NFD→NFC, лігатура `ﬁ`, CJK, emoji, `İ` |
| `test_corpus.py` | биті рядки jsonl пропускаються з WARNING і номером рядка (`caplog`); `count_tokens` = `index_stats` |
| `test_merge_and_store.py` | merge-алгоритми проти set-версій, булевий пошук Lab 02 на обох движках, round trip pickle/json × positions, інваріанти індексу |
| `test_lab03_*.py` | dunder-методи, `open_index` при винятку, скорери й три sanity-перевірки, парсер (рівність дерев), кеш, `@timed` + `wraps`, `ResultList`, `explain` |
| `test_properties.py` | **6 Hypothesis-властивостей** (нижче) |
| `test_cli.py` | `typer.testing.CliRunner`: усі підкоманди, `--json` (stdout містить лише JSON, логи у stderr), cache HIT у лозі, коди виходу 1 і 2 |
| `test_real_corpus.py` | `@pytest.mark.slow`: справжній корпус — три sanity-перевірки, P@5 (BM25 ≥ TF-IDF ≥ 0.4), скрипти звітів для README |

**Hypothesis-властивості:**

1. **Оракул:** `and_merge / or_merge / not_merge` дорівнюють `sorted(set(a) & | - set(b))` для будь-яких відсортованих списків.
2. **Ідемпотентність:** `tokenize(" ".join(tokenize(t))) == tokenize(t)`, порожніх токенів немає.
3. **Слово — один токен:** рядок із самих літер дає рівно один токен.
4. **Round trip:** `load(save(index)) == index` для довільного корпусу, обох форматів, з positions і без.
5. **Парсер:** `parse(str(tree)) == tree` для випадкових дерев `Term/Phrase/And/Or/Not`.
6. **BM25:** score строго зростає за tf і не перевищує `idf·(k1+1)` за будь-якого `b ∈ [0, 1]`.

**Що знайшов Hypothesis.** Властивість 3 одразу впала, і Hypothesis звів приклад до
**`"İA"` → `["i", "a"]`**. `"İ".casefold()` дає `"i" + U+0307` (комбінуючу крапку), а `\w` у Python
комбінуючі знаки не матчить. Тому `İstanbul` у старому токенайзері розпадався на `i` і `stanbul`,
а `ǰ` втрачав гачек. Виправлено: після `casefold` ще раз NFC, а слово — це
`\w[\w<комбінуючі знаки>]*`. Нормалізацію спільно використовують токенайзер і сніпети. Регресійні
приклади додано в `test_tokenize.py`. Ідемпотентність (властивість 2) цього бага не ловила: вона
виконувалась і для поламаного токенайзера.

### Покриття

`uv run pytest --cov=findex` (branch coverage, блоки `if __name__ == "__main__":` виключено):
**79 %** загалом.

| Модуль | Покриття | | Модуль | Покриття |
|---|---|---|---|---|
| `tokenize`, `corpus`, `engine`, `utils` | 100 % | | `search` (Lab 02) | 85 % |
| `explain`, `cli`, `query`, `sanity_checks` | 97–98 % | | `evaluate` | 75 % |
| `store` | 96 % | | `stats` | 61 % |
| `index`, `ranking` | 93 % | | `bench_search`, `memory_study`, `fill_readme`, `eager_stats` | 0 % |
| `snippets` | 88 % | | | |

**Що не покрито і чому:**

- `bench_search`, `memory_study`, `fill_readme`, `eager_stats` — дослідницькі скрипти Lab 01–02
  (бенчмарки, вимірювання пам'яті, заповнення таблиць README). Вони міряють час і пам'ять на
  реальному корпусі й нічого не повертають. Тест на них перевіряв би лише «не впало» і робив би
  набір повільним і нестабільним. Pyright strict їх перевіряє.
- `stats.main`, `evaluate --suggest` — друк для людини поверх уже протестованих чистих функцій
  (`count_tokens`, `index_stats`, `precision_at_k`).
- Решта пропусків — гілки обробки рідкісних помилок (зіпсований рядок у `TextStore`, порожній
  індекс у `avg_doc_length`) і рядки старого парсера Lab 02 з неповним запитом (`a AND`).

## L4.5 M3 — пакет

`pyproject.toml` (PEP 621): метадані, `dependencies = [rich, typer]`, група `dev` (pytest,
pytest-cov, hypothesis, pyright, ruff), `[project.scripts] findex = "findex.cli:app"`,
налаштування `[tool.pyright]`, `[tool.pytest.ini_options]` (маркер `slow`, `--strict-markers`),
`[tool.coverage]` і `[tool.ruff]`. Backend — `uv_build`, `src/`-layout, `py.typed`. Версія `0.4.0`
(semver). `uv.lock` закомічено.

## L4.6 M4 — логування, CI, реліз

- **Логування.** У кожному модулі `log = logging.getLogger(__name__)`. Налаштовується один раз,
  у `cli.main` (`RichHandler` → stderr; `-v` → INFO, `-vv` → DEBUG). Повідомлення — у лінивому
  `%`-форматі. `@timed` логує на DEBUG. `print` лишився тільки там, де він і є виводом:
  дані CLI та markdown-звіти скриптів (`sanity_checks`, `evaluate`, бенчмарки).
- **CI** (`.github/workflows/ci.yml`, на push і PR):
  - job `lint`: `uv sync --locked` → `ruff check` → `ruff format --check` → `pyright`;
  - job `test`: матриця Ubuntu/Windows × Python 3.12/3.14, `pytest --cov` (з `slow`-тестами);
  - smoke-тест зібраного wheel через `uvx --from dist/*.whl findex ...`.
- **Реліз:** тег `v0.4.0`, wheel і sdist прикріплено до GitHub Release.

## L4.7 Рефлексія (коротко)

1. Під час виконання анотації ніхто не перевіряє. Їх читають інструменти: pyright/mypy (до запуску,
   в CI), IDE (автодоповнення). Деякі бібліотеки читають їх під час виконання: dataclasses
   (поля), typer (`k: int = 10` → опція `--k` з валідацією), FastAPI/Pydantic.
2. Звуження: після перевірки тип змінної уточнюється. `def f(x: str | None): return x.upper()`:
   pyright скаже `"upper" is not a known attribute of "None"`. Після `if x is None: return ""`
   тип `x` уже `str`. У коді так зроблено з `pickle.load` (`isinstance`) і з `TextStore._offsets`.
3. Приймати `Iterable` (потрібен лише цикл, підійде список, генератор, `islice`), повертати
   `Iterator` (точно: генератор можна передати в `next()`, але лише один раз).
4. Протокол — структурний тип. Pyright порівнює члени: якщо в класу є `__call__(term: str,
   posting: Posting, index: Index) -> float` із сумісними типами, клас — `Scorer`. Успадкування
   не потрібне.
5. Scope визначає, як часто створюється фікстура (function / module / session). Якщо один тест
   змінить або закриє session-фікстуру `tiny_index`, тести після нього побачать зіпсований індекс.
   Результат залежатиме від порядку тестів. Тому є окрема `fresh_index` на кожен тест.
6. «Для будь-яких відсортованих `a`, `b`: `and_merge(a, b) == sorted(set(a) & set(b))`». Коли
   властивість падає, Hypothesis зменшує контрприклад до мінімального: замість рядка з 40 символів
   — `"İA"`, по якому одразу видно причину.
7. `src/`-layout: `import findex` у тестах бере встановлений пакет, а не теку поруч. Плоска
   структура дозволяє тестам проходити на сирцях, навіть якщо в wheel чогось бракує (файл не
   увійшов у пакет, забута залежність).
8. `[project.scripts]` створює виконуваний файл у `bin/` (на Windows — `Scripts\findex.exe`):
   маленьку обгортку, яка імпортує `findex.cli` і викликає `app()`. Запис про нього — в
   `entry_points.txt` у `.dist-info`.
9. `log.info("n=%d", n)` форматує рядок лише тоді, коли рівень увімкнено. f-рядок обчислюється
   завжди, навіть якщо повідомлення відкинуть. Крім того, шаблон лишається однаковим, і
   агрегатори логів можуть групувати за ним.

## L4.8 Статус

- [x] `pyproject.toml` (PEP 621): залежності, група `dev`, `[project.scripts]`, налаштування інструментів; `uv.lock` закомічено
- [x] pyright strict: 0 помилок; кожен ignore з причиною
- [x] `Scorer` — `typing.Protocol`; генератори типізовано через `Iterator`/`Iterable`
- [x] 118 тестів, 6 Hypothesis-властивостей, `conftest.py`, маркер `slow`, покриття в README
- [x] `findex index|search|stats` на typer + rich; `--json`; `-v/-vv`; ненульові коди виходу
- [x] `logging` усюди; налаштування в `cli.py`; діагностика у stderr
- [x] CI workflow і бейдж; wheel на релізі `v0.4.0`
- [x] Git-тег `lab-04`
- [ ] Stretch (shell completion, `findex config`, free-threaded матриця, TestPyPI) — не виконано

---

# Lab 03 — Ranking and the Object Model

`Index` поводиться як вбудований тип Python, пошук ранжує результати (TF-IDF / BM25),
є мова запитів з дужками й фразами, сніпети з підсвіткою, кеш запитів і оцінка precision@5.

## L3.1 Структура

| Файл | Призначення |
|---|---|
| `findex/index.py` | **M1**: `Index(Mapping[str, list[Posting]])`, `num_docs`, `avg_doc_length` (`cached_property`), `doc_length()`, `df()`, контекстний менеджер `open_index`, `build_index(positions=...)` |
| `findex/ranking.py` | **M2**: протокол `Scorer`, `TfIdf`, `BM25(k1=1.5, b=0.75)`, `SearchResult(order=True)`, `rank()` з `heapq.nlargest`; **Stretch**: `ResultList(Sequence)` |
| `findex/query.py` | **M3**: лексер + рекурсивний спуск → дерево `Term` / `Phrase` / `And` / `Or` / `Not`, оператори `&` `\|` `~`, `evaluate(index)` |
| `findex/utils.py` | **M4**: декоратор `@timed` (з `functools.wraps`) |
| `findex/engine.py` | **M4**: `Searcher` — розбір → збіги → ранжування (з `lru_cache(maxsize=256)`) → сніпети |
| `findex/snippets.py` | **M4**: вікно ±80 символів навколо найкращого збігу, підсвітка `**терм**` |
| `findex/sanity_checks.py` | три sanity-перевірки ранжування (вивід — markdown нижче) |
| `findex/evaluate.py` | precision@5 для TF-IDF і BM25 за `tests/eval_queries.json` |
| `findex/explain.py` | **Stretch**: `explain()` на `singledispatch` — дерево запиту і розклад score по термах |
| `tests/test_lab03_*.py` | тести dunder-методів, скорерів, парсера (рівність дерев), кешу, `ResultList` і `explain` |

## L3.2 Запуск

```bash
# індекс з positions (потрібні для фраз)
uv run python -m findex.index data/dota2wiki.jsonl --positions --out index.bin

# пошук (з Lab 04 — через CLI `findex`): -vv вмикає DEBUG-логи @timed і cache HIT/MISS
uv run findex -vv search index.bin 'silence NOT silencer' -k 3 --repeat 2
uv run findex search index.bin '"meat hook" OR (rot AND dismember)' --scorer tfidf

uv run python -m findex.sanity_checks index.bin
uv run python -m findex.evaluate data/dota2wiki.jsonl tests/eval_queries.json
# дерево запиту + розклад score кожного результату
uv run findex search index.bin 'meat hook' -k 1 --scorer tfidf --explain
```

## L3.3 M1 — `Index` як об'єкт

`Index` успадковує `collections.abc.Mapping`: достатньо реалізувати `__getitem__`, `__len__`,
`__iter__`, а `keys()`, `items()`, `get()`, `__eq__` дає ABC. `__contains__` перевизначено
для швидкості (пряма перевірка в dict).

```python
>>> with open_index("index.bin") as ix:
...     print(len(ix), "pudge" in ix, "xyzzy" in ix, ix.df("hook"), ix.num_docs, ix.avg_doc_length)
...     p = ix["meat"][0]; print(p.doc_id, p.tf, p.positions[:3])
...     print(repr(ix), next(iter(ix)))
...     ix["xyzzy"]
16106 True False 3 124 4219.9
13 26 (230, 518, 642)
Index(terms=16_106, docs=124, positions=True) anti
KeyError: 'xyzzy'
>>> ix                      # після виходу з with
Index(closed)
```

`open_index` написано через `@contextmanager`: `load` до `yield`, `index.close()` у `finally`,
тож індекс звільняється і при `return`, і при винятку (тест
`test_open_index_closes_even_on_exception`). Код Lab 02 (`findex.search`) працює через той самий
об'єкт.

## L3.4 M2 — ранжування і sanity-перевірки

`Scorer` — `typing.Protocol` з `__call__(term, posting, index) -> float`. `TfIdf` і `BM25` нічого
не успадковують; підходить і звичайна функція з такою сигнатурою.

- TF-IDF: `(1 + ln tf) · ln(N / df)`
- BM25: `idf · tf·(k1+1) / (tf + k1·(1 − b + b·dl/avgdl))`, `idf = ln(1 + (N − df + 0.5)/(df + 0.5))`

Вивід `python -m findex.sanity_checks index.bin`:

### Перевірка 1: рідкісний терм важить більше за поширений

| Терм | df | idf (BM25) | внесок за tf=1 у документі середньої довжини |
|---|---|---|---|
| рідкісний: `ascetic` | 1 | 4.423 | 4.423 |
| поширений: `grandmaster` | 123 | 0.012 | 0.012 |

Запит `ascetic OR grandmaster`, top-5:

| # | doc | score | tf(`ascetic`) | tf(`grandmaster`) |
|---|---|---|---|---|
| 1 | anti_mage | 6.695 | 2 | 1 |
| 2 | pugna | 0.023 | 0 | 5 |
| 3 | lion | 0.018 | 0 | 2 |
| 4 | silencer | 0.016 | 0 | 2 |
| 5 | sven | 0.015 | 0 | 1 |

Pugna містить `grandmaster` 5 разів, але це майже нічого не дає: слово є в 123 з 124 документів.

### Перевірка 2: насичення tf у BM25

Терм `thrown` (df=12), документ середньої довжини (4220 токенів).

| tf | BM25 | приріст за одне входження | TF-IDF (1+ln tf)·idf |
|---|---|---|---|
| 1 | 2.303 | +2.303 | 2.335 |
| 2 | 3.289 | +0.987 | 3.954 |
| 3 | 3.838 | +0.548 | 4.901 |
| 5 | 4.428 | +0.295 | 6.094 |
| 10 | 5.006 | +0.116 | 7.713 |
| 20 | 5.355 | +0.035 | 9.332 |
| 21 | 5.373 | +0.018 | 9.445 |
| 50 | 5.589 | +0.007 | 11.471 |

Межа при tf→∞: idf·(k1+1) = 5.756. 21-ше входження додає 0.018 (0.8 % від першого),
а TF-IDF з логарифмом росте без межі.

### Перевірка 3: короткий документ з одним збігом перемагає довгий з одним збігом

Терм `date`, tf=1 в обох документах:

| документ | довжина (токенів) | BM25 (b=0.75) | BM25 (b=0, без нормалізації) |
|---|---|---|---|
| короткий: sven | 2584 | 0.015 | 0.012 |
| довгий: invoker | 8990 | 0.008 | 0.012 |

При b=0 довжина документа ігнорується, і оцінки однакові.

## L3.5 M3 — мова запитів

```
or_expr  -> and_expr ('OR' and_expr)*
and_expr -> not_expr (['AND'] not_expr)*     # неявний AND
not_expr -> 'NOT' not_expr | atom
atom     -> WORD | "PHRASE" | '(' or_expr ')'
```

```python
>>> parse('a OR b c')
Or(left=Term(text='a'), right=And(left=Term(text='b'), right=Term(text='c')))
>>> parse('a OR b c') == Term('a') | (Term('b') & Term('c'))
True
```

Тести парсера порівнюють дерева (`tests/test_lab03_query.py`), а не рядки. Фраза перевіряється
двома вказівниками по positions: позиція `p` першого слова, `p+1` другого і т.д. Ранжування
застосовується до множини, яку повернув `evaluate`; терми під `NOT` у скор не входять.

## L3.6 M4 — `@timed`, кеш, сніпети

`@timed` стоїть на `build_index`, `load`, `save`, `Searcher.search`, `Searcher._rank_ids`.
`Searcher` створює `lru_cache(maxsize=256)` для кожного екземпляра над шляхом
«запит → id результатів» (`Index` не hashable, тому в ключі лише `(query, k)`).

Лог повторного запиту (`--repeat 2 -v`):

```
DEBUG findex.utils: load took 1368.76 ms
DEBUG findex.utils: Searcher._rank_ids took 0.58 ms
DEBUG __main__: query='silence NOT silencer' k=3 cache=MISS
DEBUG findex.utils: Searcher.search took 67.19 ms
DEBUG __main__: query='silence NOT silencer' k=3 cache=HIT
DEBUG findex.utils: Searcher.search took 44.80 ms
Кеш: CacheInfo(hits=1, misses=1, maxsize=256, currsize=1)
```

На HIT `_rank_ids` не викликається (рядка про нього немає). Решта часу `search` — читання
тексту з jsonl і побудова сніпетів, вони не кешуються.

Сніпети:

```
[44]   1.883  templar_assassin
    …Slow Duration: 5 Min **Silence** Duration: 1.5 Max **Silence** Duration: 3.5 0.5 0 Aghanim's Shard Upgrade: When activated, traps now **silence** affected enemies.…
[13]  60.997  pudge
    …bonuses 25 1.8x **Dismember** Damage/Heal -4s **Meat** **Hook** Cooldown 20 +0.75s **Dismember** Duration +140 **Meat** **Hook** Damage 15 8% Spell Lifesteal 16% **Rot** Slow 10 +5…
```

## L3.7 Оцінка: precision@5

10 запитів у `tests/eval_queries.json`. Релевантних героїв розмічено за здібностями з гри, а не
за видачею системи (напр. `remnant` → Storm/Ember/Earth/Void Spirit). Знаменник завжди 5, тому
для запитів з 1–2 релевантними максимум 0.20 / 0.40.

| Запит | релевантних | P@5 TF-IDF | P@5 BM25 |
|---|---|---|---|
| `remnant` | 4 | 0.80 | 0.80 |
| `illusion OR illusions` | 5 | 0.40 | 0.40 |
| `silence` | 5 | 0.40 | 0.60 |
| `ice OR frost` | 6 | 0.60 | 0.60 |
| `poison` | 3 | 0.60 | 0.60 |
| `"meat hook"` | 1 | 0.20 | 0.20 |
| `global` | 5 | 0.60 | 0.80 |
| `invisibility OR invisible` | 6 | 0.80 | 0.80 |
| `"mana burn" OR "mana break"` | 2 | 0.40 | 0.40 |
| `blink` | 2 | 0.40 | 0.40 |
| **середнє (10 запитів)** | | **0.52** | **0.56** |

BM25 трохи краще: на `silence` і `global` нормалізація за довжиною підняла коротші сторінки
(Night Stalker, Spectre) над довгими, де слово згадується мимохідь. На більшості запитів
результати однакові: корпус малий, а документи схожої довжини. Типові помилки обох скорерів —
сторінки, де терм є в описах предметів або контрпіків (`illusion` в Anti-Mage через Mana Break,
`blink` у героїв, що купують Blink Dagger). Без стемінгу `illusion` і `illusions` — різні терми.

## L3.8 Рефлексія (коротко)

1. `"python" in index` → `type(index).__contains__(index, "python")` → `bool`; якщо `__contains__`
   немає, Python пробує `__iter__`, потім `__getitem__`. `len(index)` → `type(index).__len__`,
   результат перевіряється (має бути `int >= 0`). Built-in шукає метод на типі, для C-типів має
   швидкий шлях і робить перевірки, яких немає в прямому `index.__len__()`.
2. Протокол — набір методів, який описує поведінку. `typing.Protocol` перевіряється структурно:
   успадковувати не треба, перевіряє тайпчекер (або `isinstance` з `@runtime_checkable`). ABC
   потребує успадкування або `register` і дає готові mixin-методи (як `Mapping` для `Index`).
3. `@timed` — у `findex/utils.py`. Без `functools.wraps` у функції було б `__name__ == "wrapper"`,
   не було б docstring і `__wrapped__`, тож `help()`, логи й тести показували б не те.
4. Декоратор з аргументами — на рівень більше: `retry(times)` повертає `decorator(fn)`, який
   повертає `wrapper(*a, **kw)` з циклом `for _ in range(times): try: return fn(...)`.
5. До `yield` виконується `__enter__`, після — `__exit__`. Виняток з тіла `with` кидається
   всередині генератора в точці `yield`, тож спрацьовує `finally` (або `except`).
6. Насичення tf (`k1`: чим менше, тим швидше насичення) і нормалізація за довжиною (`b`: частка
   впливу `dl/avgdl`). При `b = 0` довжина ігнорується (перевірка 3).
7. `nlargest(k, …)` — купа розміру k, O(n log k); `sorted(...)[:k]` — O(n log n) і повна копія.
8. `OR` має нижчий пріоритет: `or_expr` спочатку викликає `and_expr`, який «з'їдає» `b c` до
   `OR`. Тому `a OR b c` = `Or(a, And(b, c))`.

## L3.9 Статус

- [x] `Index`: `__len__`, `__contains__`, `__getitem__`, `__iter__`, `__repr__`; `num_docs`,
      `avg_doc_length` (`cached_property`), `doc_length`, `df`
- [x] `open_index` — контекстний менеджер, звільняє ресурси при винятках
- [x] протокол `Scorer`; `TfIdf` і `BM25`; `heapq.nlargest` для top-k
- [x] три sanity-перевірки на корпусі (L3.4)
- [x] рекурсивний спуск; `Term`/`Phrase`/`And`/`Or`/`Not` з `&`, `|`, `~`; тести рівності дерев
- [x] `@timed` з `functools.wraps` і `lru_cache`; cache HIT видно в лозі
- [x] сніпети з підсвіткою
- [x] таблиця precision@5 (10 запитів) для TF-IDF і BM25
- [x] git-тег `lab-03`
- [x] Stretch: `ResultList` як `Sequence` зі зрізами, `explain` на `singledispatch` (L3.10)

## L3.10 Stretch — `ResultList` і `explain`

**`ResultList(collections.abc.Sequence)`** — `Searcher.search` повертає її замість `list`.
Реалізовано лише `__getitem__` і `__len__`; `__contains__`, `__iter__`, `__reversed__`, `index()`,
`count()` дає ABC. `__getitem__` зі `slice` повертає новий `ResultList`, тож `results[:5]`
лишається видачею, а не стає звичайним списком.

```python
>>> res = searcher.search("silence", k=10)
>>> type(res[:5]).__name__, len(res[:5]), res[0] in res
('ResultList', 5, True)
>>> print(res[:2])
[73]   1.978  silencer
[44]   1.883  templar_assassin
```

**`explain`** — одна функція з `@functools.singledispatch`; реалізація обирається за типом
першого аргументу. Для `Term`/`Phrase`/`And`/`Or`/`Not` друкується дерево (ланцюжки однакових
операторів розгортаються). Для `BM25` і `TfIdf` виводиться розклад score по термах, як у
`_explain` в Elasticsearch. Будь-який інший `Scorer` (навіть звичайна функція) пояснюється
через запасну реалізацію: внесок кожного терма без розкладу формули. Тест перевіряє, що сума
внесків дорівнює score з пошуку.

```
$ findex search index.bin 'pudge OR (meat hook) NOT "blink dagger" OR rot' -k 2 --explain   # вивід Lab 03
OR
├── TERM pudge
├── AND
│   ├── TERM meat
│   ├── TERM hook
│   └── NOT
│       └── PHRASE "blink dagger"
└── TERM rot
  [13]  34.154  pudge
      34.154 = BM25(k1=1.5, b=0.75) для doc 13 (pudge)
        8.141 = 'pudge': idf 3.324 (df=4, N=124) × tf_norm 2.449 (tf=77, k1=1.5)
        9.212 = 'meat': idf 3.912 (df=2, N=124) × tf_norm 2.355 (tf=26, k1=1.5)
        8.632 = 'hook': idf 3.576 (df=3, N=124) × tf_norm 2.414 (tf=45, k1=1.5)
        8.169 = 'rot': idf 3.576 (df=3, N=124) × tf_norm 2.285 (tf=17, k1=1.5)
        довжина: dl=4602, avgdl=4219.9, b=0.75 → norm = 1 − b + b·dl/avgdl = 1.068
  [47]   3.787  dragon_knight
      3.787 = BM25(k1=1.5, b=0.75) для doc 47 (dragon_knight)
        0.000 = 'pudge': немає в документі
        0.000 = 'meat': немає в документі
        0.000 = 'hook': немає в документі
        3.787 = 'rot': idf 3.576 (df=3, N=124) × tf_norm 1.059 (tf=1, k1=1.5)
        довжина: dl=3697, avgdl=4219.9, b=0.75 → norm = 1 − b + b·dl/avgdl = 0.907
```

Розклад показує, чому Pudge має tf=77 для `pudge`, але лише 8.1 бала: насичення BM25 обмежує
tf_norm значенням k1+1 = 2.5.

---

# Lab 02 — The Inverted Index

Інвертований індекс для пошукового рушія: побудова за один потоковий прохід,
булевий пошук (AND / OR / NOT), збереження на диск у двох форматах і
дослідження пам'яті трьох представлень постінгів.

> **Як заповнювати.** Таблиці між маркерами `<!-- BEGIN:... -->` заповнює скрипт:
> `python -m findex.fill_readme data/dota2wiki.jsonl README.md`.
> Після цього лишається лише переглянути два короткі місця з `TODO` у текстових
> висновках (розділи 4 і 6) і прибрати цей блок.

---

## 1. Структура проєкту

| Файл | Призначення |
|---|---|
| `findex/corpus.py` | ледачий читач jsonl → `Document(doc_id, path, text)` |
| `findex/tokenize.py` | нормалізація NFC, `casefold`, токени `\w+` |
| `findex/index.py` | **M1**: `Posting`, `DocMeta`, `Index`, `build_index` |
| `findex/search.py` | **M2**: merge- та set-реалізації AND/OR/NOT, CLI |
| `findex/bench_search.py` | бенчмарк merge проти set |
| `findex/store.py` | **M3**: `save`/`load` у форматах pickle та json |
| `findex/memory_study.py` | **M4**: порівняння трьох представлень постінгів |
| `findex/stats.py`, `findex/eager_stats.py` | статистика корпусу з Lab 01 (ліниво проти жадібно) |

## 2. Запуск

```bash
# M1: побудова індексу (час + пік пам'яті)
python -m findex.index data/dota2wiki.jsonl

# M3: збереження/завантаження у pickle та json, розмір і час
python -m findex.store data/dota2wiki.jsonl

# M2: пошук із збереженого файлу
python -m findex.search index.bin "hero AND dota"
python -m findex.search index.bin "hero OR dota NOT item" --engine set
python -m findex.search index.json "hero" --format json

# бенчмарк merge проти set
python -m findex.bench_search data/dota2wiki.jsonl

# M4: таблиця пам'яті
python -m findex.memory_study data/dota2wiki.jsonl
```

Усі команди, що будують або завантажують індекс, друкують час і пік пам'яті.

## 3. M1 — побудова індексу

```python
@dataclass(frozen=True, slots=True)
class Posting:  doc_id: int; tf: int
@dataclass(frozen=True, slots=True)
class DocMeta:  title: str; path: str
@dataclass(slots=True)
class Index:
    postings: dict[str, list[Posting]]   # term -> постінги, відсортовані за doc_id
    doc_lengths: dict[int, int]          # doc_id -> кількість токенів
    doc_meta: dict[int, DocMeta]         # doc_id -> заголовок/шлях
```

Ключові рішення:

- **Один потоковий прохід.** `iter_documents` — ледачий ітератор: корпус не
  завантажується в пам'ять, росте лише індекс.
- **`defaultdict(list)`** для постінгів: `postings[term].append(...)` без
  перевірки `if term not in ...`.
- **`Counter` на документ.** Токени документа підраховуються одним `Counter`,
  потім по кожному унікальному терміну додається один `Posting(doc_id, tf)`.
- **Сортованість безкоштовна.** `doc_id` — це лічильник `enumerate`, що
  зростає, тому кожен список постінгів автоматично відсортований за `doc_id`.
  Окреме сортування не потрібне, а саме воно робить можливим merge.
- **`frozen=True, slots=True`.** `frozen` робить об'єкти незмінними й
  hashable (`__hash__` генерується з полів, узгоджено з `__eq__`), тож
  `Posting` можна класти в `set` чи використовувати як ключ. `slots` прибирає
  `__dict__` з кожного екземпляра (див. M4).
- Внутрішній `doc_id` — ціле число (`enumerate`), а рядковий id із корпусу
  зберігається в `DocMeta`.
- **Positions** (необов'язково) не реалізовані; це обмеження описано в
  розділі 8.

Результат побудови на корпусі:

<!-- BEGIN:index -->
| Документів | Унікальних термінів | Час побудови | Пік пам'яті |
|---|---|---|---|
| 124 | 16,106 | 1.36 с | 10.75 МБ |
<!-- END:index -->

## 4. M2 — булевий пошук

Запит розбирається зліва направо: `term term` означає неявний AND, також
підтримуються `AND`, `OR`, `NOT` (унарний на початку й бінарний `X NOT Y`,
а також `AND NOT`). Повноцінний парсер із пріоритетами з'явиться в Lab 3.

Дві реалізації з однаковим інтерфейсом, вибір через `--engine {merge,set}`:

- **merge**: два вказівники по відсортованих спискам. AND просувається,
  коли значення рівні, і просуває менший; OR виводить усе по одному разу;
  NOT пропускає збіги. Складність `O(len(a) + len(b))`, без хешування, працює
  потоково.
- **set**: `sorted(set(a) & set(b))` тощо. Побудова множин `O(n + m)`
  плюс `sorted` результату `O(k log k)`.

`NOT` на початку запиту обчислюється як доповнення до `range(len(doc_lengths))`.

### Бенчмарк merge проти set (AND)

Два найчастіші й два найрідші терміни, `timeit` (мін. із повторів), результат
перевіряється `assert and_merge(a, b) == and_set(a, b)`:

<!-- BEGIN:bench -->
| Пара | Терміни | Довжини списків | merge (мкс) | set (мкс) | Швидше |
|---|---|---|---|---|---|
| частий + частий | 1, 6 | 124 і 124 | 14.12 | 6.49 | set |
| рідкий + рідкий | scrying, mournful | 1 і 1 | 0.24 | 0.41 | merge |
| частий + рідкий | 1, scrying | 124 і 1 | 11.95 | 1.98 | set |
<!-- END:bench -->

**Аналіз.** Асимптотично обидва підходи лінійні, тож різницю визначає
константа. `set(a) & set(b)` виконується повністю в C, а merge — цикл на
чистому Python з індексацією та порівняннями, тому на списках
пам'ять set зазвичай швидший. Merge виграє не в мікробенчмарку, а в
архітектурі: він не виділяє хеш-таблиць (менше пам'яті), працює потоково по
стислих постінгах, дозволяє пропускати елементи (skip pointers) при дуже
різних довжинах списків і природно розширюється на фразові запити (Lab 3).
TODO: підтвердьте або спростуйте це власними цифрами з таблиці вище.

## 5. M3 — збереження та завантаження

`save(index, path, fmt)` / `load(path, fmt)`, формати `pickle` і `json`.
Для кожного формату перевіряється, що після завантаження `postings`,
`doc_lengths`, `doc_meta` дорівнюють оригіналу.

<!-- BEGIN:store -->
| Формат | Розмір файлу, МБ | Save, с | Load, с |
|---|---|---|---|
| pickle (`HIGHEST_PROTOCOL`) | 2.13 | 0.259 | 0.262 |
| json | 1.48 | 0.317 | 0.181 |
<!-- END:store -->

Особливості json: кортежі стають списками, а цілі ключі словників —
рядками, тому при завантаженні `doc_lengths` і `doc_meta` явно перетворюються
назад через `int(k)`; `Posting` відновлюється зі списків `[doc_id, tf]`.

### Безпека pickle

`pickle.load` **не просто читає дані — він виконує інструкції** потоку pickle.
Об'єкт може визначити метод `__reduce__`, що повертає, наприклад,
`(os.system, ("<команда>",))`, і ця команда виконається під час `pickle.load`.
Тому завантаження pickle-файлу, отриманого з мережі або від недовіреного
джерела, дорівнює віддаленому виконанню коду (RCE). У цьому проєкті pickle
використовується лише для власного індексного файлу, який створила ця ж
програма. Для недовірених даних слід використовувати json або власний
бінарний формат. Той самий попереджувальний коментар є в `store.py`.

## 6. M4 — дослідження пам'яті

Один і той самий корпус, три представлення постінгів, пік пам'яті при побудові
міряється `tracemalloc` (для кожного варіанта — окрема сесія), розмір файлу —
pickle, час — `pickle.load`.

<!-- BEGIN:memory -->
| Postings representation | Peak memory (build), МБ | Index file size, МБ | Load time, с |
|---|---|---|---|
| `list[Posting]`, plain `@dataclass` | 15.82 | 2.66 | 0.1049 |
| `list[Posting]`, `slots=True` | 10.48 | 2.12 | 0.2716 |
| `array('I')` pairs (doc_ids, tfs) | 6.71 | 1.79 | 0.0303 |

Пік пам'яті порівняно з plain dataclass: `list[Posting]`, `slots=True`: **1.51×** менше, `array('I')` pairs (doc_ids, tfs): **2.36×** менше.

Усього постінгів: 141,060.
<!-- END:memory -->

### Куди пішли байти

**Plain `@dataclass`.** Кожен постінг — окремий Python-об'єкт із власним
`__dict__`. На одну пару `(doc_id, tf)`, що містить 8 корисних байтів,
припадає: сам об'єкт, словник атрибутів, два об'єкти `int` по 28 байтів
(малі числа кешуються інтерпретатором, великі `doc_id` — ні) та 8-байтовий
вказівник у списку. Це кілька сотень байтів замість восьми. Саме `__dict__` на
кожен екземпляр і є тим «розкошем», який дозволяє додавати довільні
атрибути в будь-який момент і який нам тут не потрібен.

**`slots=True`.** `__dict__` прибирається: атрибути лежать у фіксованих
слотах C-рівня, розмір екземпляра дорівнює розміру його полів. Це дає
помітне скорочення пікової пам'яті, але об'єкт, два `int`-и й вказівник у
списку нікуди не зникають: структура «багато дрібних об'єктів» лишається.
Ціна: не можна додавати атрибути, не оголошені в slots, а множинне наслідування
складніше. TODO: опишіть, як змінився час завантаження — розпікловування
frozen+slots-класу відновлює поля через `object.__setattr__`, тож
завантаження може бути повільнішим, ніж у plain-варіанта.

**`array('I')`.** Об'єктів немає зовсім: `doc_id` і `tf` лежать як сирі
беззнакові 4-байтові цілі в двох суцільних буферах на термін, тобто 4 + 4 = 8
байтів на постінг плюс невеликі накладні витрати на сам масив. Pickle такого
масиву — майже пряме копіювання буфера, тому файл найменший, а завантаження
найшвидше. Ціна — втрата зручності об'єктів `Posting`: доступ іде через дві
паралельні послідовності, а значення обмежені діапазоном `uint32`.

**Різниця у N разів.** N = (розмір одного постінга в рядку 1) / 8 байтів.
Розрахунок (заповніть із виводу `memory_study`, який друкує розміри `int`,
`Posting` та `__dict__`):

<!-- BEGIN:sizes -->
| Складова (байт на постінг) | Plain dataclass | slots | `array('I')` |
|---|---|---|---|
| об'єкт Posting | 48 | 48 | — |
| додаткове в plain (виміряно: різниця пікових пам'ятей / к-сть постінгів) | ≈ 40 | — | — |
| `doc_id` (`int`, 28 Б) | спільний на документ | спільний на документ | 4 |
| `tf` (`int`) | 0 (малі числа кешуються) | 0 (кешуються) | 4 |
| вказівник у списку | 8 | 8 | — |
| **разом на постінг (приблизно)** | **≈ 96** | **≈ 56** | **8** |

Звідси N ≈ 96/8 ≈ **12×** (plain проти array) і ≈ 56/8 ≈ **7×** (slots проти array) для самих даних постінгів. Виміряний пік пам'яті (таблиця вище) показує менші співвідношення, бо в ньому є спільні витрати: рядки термінів, словник, `Counter`, текст поточного документа та накладні витрати двох масивів на кожен термін. На малому корпусі вони домінують; з ростом корпусу виграш `array` наближається до N.
<!-- END:sizes -->

## 7. Контракт хешування та `frozen`

- Якщо `a == b`, то `hash(a) == hash(b)` — завжди. Порушення «губить» ключі у
  dict/set, бо пошук іде в неправильний слот.
- Клас, у якому визначено `__eq__`, але не `__hash__`, отримує
  `__hash__ = None` і стає unhashable.
- `@dataclass(frozen=True)` генерує `__eq__` та `__hash__` з одних і тих самих
  полів, тож контракт виконується автоматично. Тому `Posting` і `DocMeta`
  можна класти в `set`:

```python
>>> len({Posting(1, 2), Posting(1, 2)})
1
```

- `hash("abc")` рандомізується між запусками процесу (захист від
  hash-flooding), тому сирі хеші рядків не можна зберігати на диск, а
  порядок ітерації `set` між запусками не гарантований. Наш індекс
  зберігає самі терміни, а не їх хеші.

## 8. Статус виконання

- [x] Індекс будується за один прохід по ліниво читаному корпусу, постінги
      відсортовані за `doc_id`
- [x] `Posting` / `DocMeta` — `@dataclass(frozen=True, slots=True)`, hashable
- [x] AND / OR / NOT через merge; set-альтернатива `--engine set`;
      бенчмарк у розділі 4
- [x] Збереження/завантаження у двох форматах (pickle, json), виміряні
      розмір і час; небезпека pickle задокументована в коді й тут
- [x] Таблиця пам'яті з трьома рядками та поясненням (розділ 6)
- [x] `python -m findex.index` і `python -m findex.search` працюють зі
      збереженого файлу
- [ ] Опціональний прапорець positions — не реалізований
- [ ] Git-тег `lab-02`

**Відомі обмеження**

- `DocMeta.title` зараз дорівнює рядковому id із корпусу, бо `Document` не
  читає поле `title` з jsonl.
- Парсер запиту — простий зліва направо, без дужок і пріоритетів операторів;
  багатослівний терм (через пробіл) не підтримується. Повноцінний парсер — Lab 3.
- Формат збереженого файлу не записується в сам файл: його треба передавати
  через `--format` при пошуку.
- `NOT` на початку запиту будує `list(range(N))` на весь корпус, що
  прийнятно для лаби, але не масштабується.
- Pickle не можна завантажувати з недовірених джерел (розділ 5).

## 9. Stretch (не виконано)

Бінарний mmap-формат (JSON-заголовок `term → (offset, count)` + суцільні
`array('I')` буфери), завантаження через `mmap` за константний час і gap
encoding (дельти `doc_id`).