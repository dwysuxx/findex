# findex — пошуковий рушій по Dota 2 wiki

Корпус: `data/dota2wiki.jsonl`, 124 статті про героїв. Індекс з positions:
`Index(terms=16_106, docs=124, positions=True)`, середня довжина документа 4220 токенів.

- [Lab 03 — Ranking and the Object Model](#lab-03--ranking-and-the-object-model)
- [Lab 02 — The Inverted Index](#lab-02--the-inverted-index)

---

# Lab 03 — Ranking and the Object Model

`Index` поводиться як вбудований тип Python, пошук ранжує результати (TF-IDF / BM25),
є мова запитів з дужками й фразами, сніпети з підсвіткою, кеш запитів і оцінка precision@5.

## L3.1 Структура

| Файл | Призначення |
|---|---|
| `findex/index.py` | **M1**: `Index(Mapping[str, list[Posting]])`, `num_docs`, `avg_doc_length` (`cached_property`), `doc_length()`, `df()`, контекстний менеджер `open_index`, `build_index(positions=...)` |
| `findex/ranking.py` | **M2**: протокол `Scorer`, `TfIdf`, `BM25(k1=1.5, b=0.75)`, `SearchResult(order=True)`, `rank()` з `heapq.nlargest` |
| `findex/query.py` | **M3**: лексер + рекурсивний спуск → дерево `Term` / `Phrase` / `And` / `Or` / `Not`, оператори `&` `\|` `~`, `evaluate(index)` |
| `findex/utils.py` | **M4**: декоратор `@timed` (з `functools.wraps`) |
| `findex/engine.py` | **M4**: `Searcher` — розбір → збіги → ранжування (з `lru_cache(maxsize=256)`) → сніпети; CLI |
| `findex/snippets.py` | **M4**: вікно ±80 символів навколо найкращого збігу, підсвітка `**терм**` |
| `findex/sanity_checks.py` | три sanity-перевірки ранжування (вивід — markdown нижче) |
| `findex/evaluate.py` | precision@5 для TF-IDF і BM25 за `tests/eval_queries.json` |
| `tests/test_lab03_*.py` | тести dunder-методів, скорерів, парсера (рівність дерев), кешу |

## L3.2 Запуск

```bash
# індекс з positions (потрібні для фраз)
uv run python -m findex.index data/dota2wiki.jsonl --positions --out index.bin

# пошук: -v вмикає DEBUG-логи @timed і cache HIT/MISS
uv run python -m findex.engine index.bin 'silence NOT silencer' -k 3 --repeat 2 -v
uv run python -m findex.engine index.bin '"meat hook" OR (rot AND dismember)' --scorer tfidf

uv run python -m findex.sanity_checks index.bin
uv run python -m findex.evaluate data/dota2wiki.jsonl tests/eval_queries.json
uv run pytest -q          # 42 passed
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
- [ ] Stretch (`ResultList` як `Sequence`, `singledispatch` `explain`) — не виконано

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