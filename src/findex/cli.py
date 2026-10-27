"""findex — CLI на typer + rich.

    findex index data/dota2wiki.jsonl --out index.bin --positions
    findex index data/dota2wiki.jsonl -o index.bin --executor threads --workers 4
    findex search index.bin '"meat hook" OR rot' --k 5
    findex search index.bin pudge --json | jq .title
    findex stats index.bin
    findex -vv search index.bin pudge --repeat 2      # DEBUG: час @timed і cache HIT

stdout — лише дані (таблиця або JSON lines), усе діагностичне — logging у stderr.
Очікувані помилки (немає файлу, синтаксис запиту) — один рядок у stderr і код виходу 1.
"""

import json
import logging
import os
import pickle
import re
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from findex.engine import Searcher
from findex.explain import explain
from findex.index import Format, Index, guess_format, open_index
from findex.parallel import PartialIndex, build_parallel, choose_executor
from findex.query import QueryParseError, parse
from findex.ranking import BM25, Scorer, TfIdf
from findex.stats import index_stats
from findex.store import save

log = logging.getLogger(__name__)

# Звичайні псевдоніми, а не `type X = ...` (PEP 695): typer читає анотації під час виконання
# і поки не розгортає TypeAliasType. Значення ті самі, що й index.Format.
ScorerName = Literal["bm25", "tfidf"]
FormatName = Literal["pickle", "json"]
ExecutorChoice = Literal["auto", "serial", "threads", "processes"]
SCORERS: dict[ScorerName, type[BM25] | type[TfIdf]] = {"bm25": BM25, "tfidf": TfIdf}

console = Console()  # stdout: дані
err = Console(stderr=True)  # stderr: прогрес, логи, помилки

app = typer.Typer(
    name="findex",
    help="Маленький пошуковий рушій: інвертований індекс, BM25/TF-IDF, мова запитів.",
    no_args_is_help=True,
    add_completion=False,
)


def _version() -> str:
    try:
        return version("findex")
    except PackageNotFoundError:  # запуск із сирців без встановлення
        return "unknown"


def _print_version(value: bool) -> None:
    """is_eager: спрацьовує до перевірки «немає підкоманди», тож `findex --version` працює сам."""
    if value:
        console.print(f"findex {_version()}")
        raise typer.Exit


def _setup_logging(verbosity: int) -> None:
    """Логування налаштовується ОДИН раз — тут, у точці входу; бібліотечний код лише логує."""
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(name)s: %(message)s",
        handlers=[RichHandler(console=err, show_time=False, show_path=False)],
        force=True,  # повторний виклик (тести CliRunner) замінює хендлери, а не додає
    )


def _fail(message: str) -> NoReturn:
    err.print(f"[bold red]Помилка:[/] {escape(message)}")
    raise typer.Exit(code=1)


@contextmanager
def _loaded(path: Path, fmt: Format | None) -> Generator[Index]:
    """open_index + перетворення очікуваних помилок завантаження на один зрозумілий рядок.

    ExitStack дозволяє перехопити помилку саме входу в open_index, не загортаючи в try
    тіло with (інакше помилки з тіла теж видавались би за «битий індекс»).
    """
    if not path.is_file():
        _fail(f"файл індексу не знайдено: {path}")
    with ExitStack() as stack:
        try:
            index = stack.enter_context(open_index(path, fmt))
        except (OSError, EOFError, pickle.UnpicklingError, TypeError, ValueError, KeyError) as e:
            _fail(f"не вдалося прочитати індекс {path} ({type(e).__name__}): {e}")
        yield index


@app.callback()
def main(
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="-v: INFO, -vv: DEBUG (у stderr)")
    ] = 0,
    show_version: Annotated[
        bool,
        typer.Option("--version", callback=_print_version, is_eager=True, help="Показати версію і вийти"),
    ] = False,
) -> None:
    """Корінь CLI: налаштовує логування для всіх підкоманд."""
    _setup_logging(verbose)


@app.command("index")
def index_cmd(
    corpus: Annotated[Path, typer.Argument(help="Корпус .jsonl (поля id, text)")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Куди зберегти індекс")],
    positions: Annotated[bool, typer.Option(help="Зберігати позиції (потрібні для фраз)")] = False,
    limit: Annotated[int | None, typer.Option(min=1, help="Індексувати лише перші N документів")] = None,
    fmt: Annotated[
        FormatName | None, typer.Option("--format", help="За замовч. — за розширенням --out")
    ] = None,
    executor: Annotated[
        ExecutorChoice,
        typer.Option(help="auto: без GIL — threads; з GIL — processes від 4 МБ, інакше serial"),
    ] = "auto",
    workers: Annotated[
        int | None, typer.Option("--workers", "-w", min=1, help="Скільки воркерів (за замовч. — усі ядра)")
    ] = None,
) -> None:
    """Побудувати індекс із корпусу (з прогрес-баром) і зберегти його."""
    if not corpus.is_file():
        _fail(f"корпус не знайдено: {corpus}")
    with corpus.open("rb") as f:
        total = sum(1 for line in f if line.strip())
    if limit is not None:
        total = min(total, limit)
    chosen = choose_executor(corpus.stat().st_size) if executor == "auto" else executor
    workers = workers or os.cpu_count() or 1

    progress = Progress(
        TextColumn(f"[bold blue]індексація ({chosen} x{workers})"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=err,
    )
    with progress:
        task = progress.add_task("index", total=total)

        def done(part: PartialIndex) -> None:  # викликається в головному потоці, по шматку
            progress.advance(task, part.chunk.slots)

        # Виняток воркера летить звідси з оригінальним traceback — це баг, а не «очікувана помилка».
        report = build_parallel(corpus, chosen, workers, positions=positions, limit=limit, on_done=done)
    index = report.index

    if index.num_docs == 0:
        _fail(f"у {corpus} немає жодного коректного документа")
    try:
        save(index, out, fmt or guess_format(out))
    except OSError as e:
        _fail(f"не вдалося записати {out}: {e}")
    console.print(f"{index!r} → {out} ({out.stat().st_size / 2**20:.2f} МБ)")


_MARK = re.compile(r"\*\*(.+?)\*\*")


def _highlight(snippet: str) -> str:
    """**терм** із make_snippet -> rich-розмітка; решту тексту екрануємо від [квадратних] дужок."""
    return _MARK.sub(r"[bold yellow]\1[/]", escape(snippet))


@app.command()
def search(
    index_path: Annotated[Path, typer.Argument(help="Збережений індекс")],
    query: Annotated[str, typer.Argument(help='Запит: AND/OR/NOT, дужки, "фрази"')],
    k: Annotated[int, typer.Option("--k", "-k", min=1, help="Скільки результатів")] = 10,
    scorer: Annotated[ScorerName, typer.Option(help="Функція ранжування")] = "bm25",
    as_json: Annotated[bool, typer.Option("--json", help="JSON lines у stdout (для jq)")] = False,
    snippets: Annotated[bool, typer.Option(help="Сніпети з підсвіткою")] = True,
    corpus: Annotated[
        Path | None, typer.Option(help="jsonl для сніпетів (за замовч. — зі шляху в індексі)")
    ] = None,
    show_explain: Annotated[bool, typer.Option("--explain", help="Дерево запиту і розклад score")] = False,
    repeat: Annotated[int, typer.Option(min=1, hidden=True, help="Повторити запит (демо кешу)")] = 1,
    fmt: Annotated[FormatName | None, typer.Option("--format")] = None,
) -> None:
    """Ранжований пошук: таблиця з підсвіченими сніпетами або --json."""
    try:
        tree = parse(query)
    except QueryParseError as e:
        _fail(f"помилка в запиті {query!r}: {e}")

    with _loaded(index_path, fmt) as index:
        sc: Scorer = SCORERS[scorer]()
        with Searcher(index, sc, corpus_path=corpus) as searcher:
            try:
                results = searcher.search(query, k=k, snippets=snippets)
                for _ in range(repeat - 1):
                    results = searcher.search(query, k=k, snippets=snippets)
            except ValueError as e:  # напр. фраза в індексі без positions
                _fail(str(e))
            log.info("cache: %s", searcher.cache_info())
            explanations = [searcher.explain(query, r.doc_id) for r in results] if show_explain else []

    if as_json:
        for rank, r in enumerate(results, 1):
            row = {"rank": rank, "doc_id": r.doc_id, "title": r.title, "score": round(r.score, 6)}
            if snippets:
                row["snippet"] = r.snippet
            print(json.dumps(row, ensure_ascii=False))  # print, не rich: без переносів і розмітки
        return

    if show_explain:
        console.print(escape(explain(tree)), highlight=False)
    if not results:
        err.print(f"Нічого не знайдено за запитом [bold]{escape(query)}[/]")
        return
    table = Table(title=f"{escape(query)} — {scorer}, top-{k}", show_lines=snippets)
    table.add_column("#", justify="right", style="dim")
    table.add_column("score", justify="right", style="cyan")
    table.add_column("документ", style="bold")
    if snippets:
        table.add_column("сніпет", ratio=1)
    for rank, r in enumerate(results, 1):
        cells = [str(rank), f"{r.score:.3f}", escape(r.title)]
        if snippets:
            cells.append(_highlight(r.snippet))
        table.add_row(*cells)
    console.print(table)
    for text in explanations:
        console.print(escape(text), highlight=False)


@app.command()
def stats(
    index_path: Annotated[Path, typer.Argument(help="Збережений індекс")],
    top: Annotated[int, typer.Option(min=1, help="Скільки найчастіших термів показати")] = 20,
    as_json: Annotated[bool, typer.Option("--json", help="Один JSON-об'єкт у stdout")] = False,
    fmt: Annotated[FormatName | None, typer.Option("--format")] = None,
) -> None:
    """Числа Lab 01 (документи, токени, унікальні терми, найчастіші) з індексу."""
    with _loaded(index_path, fmt) as index:
        s = index_stats(index, top)
        has_positions = index.has_positions

    if as_json:
        print(
            json.dumps(
                {
                    "num_docs": s.num_docs,
                    "total_tokens": s.total_tokens,
                    "unique_terms": s.unique_terms,
                    "avg_doc_length": round(s.avg_doc_length, 2),
                    "has_positions": has_positions,
                    "top_terms": s.top_terms,
                },
                ensure_ascii=False,
            )
        )
        return

    summary = Table(title=f"findex stats — {escape(str(index_path))}", show_header=False)
    summary.add_column(style="bold")
    summary.add_column(justify="right", style="cyan")
    summary.add_row("документів", f"{s.num_docs:,}")
    summary.add_row("токенів усього", f"{s.total_tokens:,}")
    summary.add_row("унікальних термів", f"{s.unique_terms:,}")
    summary.add_row("середня довжина документа", f"{s.avg_doc_length:,.1f}")
    summary.add_row("positions", "так" if has_positions else "ні")
    console.print(summary)

    terms = Table(title=f"top-{top} термів")
    terms.add_column("#", justify="right", style="dim")
    terms.add_column("терм", style="bold")
    terms.add_column("входжень", justify="right", style="cyan")
    terms.add_column("частка", justify="right")
    for i, (term, n) in enumerate(s.top_terms, 1):
        terms.add_row(str(i), escape(term), f"{n:,}", f"{n / s.total_tokens:.2%}")
    console.print(terms)


if __name__ == "__main__":
    app()
