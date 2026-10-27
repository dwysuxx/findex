import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findex.cli import app

runner = CliRunner()


@pytest.fixture(scope="module")
def built(tiny_corpus: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("cli") / "idx.bin"
    result = runner.invoke(app, ["index", str(tiny_corpus), "--out", str(out), "--positions"])
    assert result.exit_code == 0, result.output
    return out


def test_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert all(cmd in result.stdout for cmd in ("index", "search", "stats"))


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.stdout.startswith("findex ")


def test_index_reports_and_writes(built: Path) -> None:
    assert built.is_file() and built.stat().st_size > 0


def test_index_json_format_by_suffix(tiny_corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "idx.json"
    result = runner.invoke(app, ["index", str(tiny_corpus), "-o", str(out), "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8"))["doc_lengths"].keys() == {"0", "1"}
    assert "docs=2" in result.stdout


@pytest.mark.parametrize("executor", ["serial", "threads", "processes"])
def test_index_executors_write_same_file(
    built: Path, tiny_corpus: Path, tmp_path: Path, executor: str
) -> None:
    out = tmp_path / "idx.bin"
    args = ["index", str(tiny_corpus), "-o", str(out), "--positions", "--executor", executor, "-w", "2"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == built.read_bytes()


def test_search_table(built: Path) -> None:
    result = runner.invoke(app, ["search", str(built), "python java", "--k", "3"])
    assert result.exit_code == 0, result.output
    assert "py-java" in result.stdout and "score" in result.stdout


def test_search_json_lines_pipe_cleanly(built: Path) -> None:
    result = runner.invoke(app, ["-vv", "search", str(built), "python", "--json", "-k", "3"])
    assert result.exit_code == 0
    rows = [json.loads(line) for line in result.stdout.splitlines()]  # stdout — лише JSON
    assert [r["rank"] for r in rows] == [1, 2, 3]
    assert rows == sorted(rows, key=lambda r: -r["score"])
    assert "**python**" in rows[0]["snippet"].casefold()
    assert "DEBUG" in result.stderr and "cache=MISS" in result.stderr  # логи — у stderr


def test_search_cache_hit_in_log(built: Path) -> None:
    result = runner.invoke(app, ["-vv", "search", str(built), "python", "--repeat", "2", "--json"])
    assert "cache=HIT" in result.stderr


@pytest.mark.parametrize("scorer", ["bm25", "tfidf"])
def test_search_scorers_and_explain(built: Path, scorer: str) -> None:
    result = runner.invoke(app, ["search", str(built), "rareword OR java", "--scorer", scorer, "--explain"])
    assert result.exit_code == 0, result.output
    assert "OR" in result.stdout and "TERM rareword" in result.stdout
    assert ("idf" in result.stdout) if scorer == "bm25" else ("ln(" in result.stdout)


def test_search_phrase(built: Path) -> None:
    result = runner.invoke(app, ["search", str(built), '"event loop"', "--json", "--no-snippets"])
    titles = {json.loads(line)["title"] for line in result.stdout.splitlines()}
    assert titles == {"py-async", "java-loop"}


def test_search_no_results_is_not_an_error(built: Path) -> None:
    result = runner.invoke(app, ["search", str(built), "zzzz"])
    assert result.exit_code == 0 and "Нічого не знайдено" in result.stderr


def test_stats_table_and_json(built: Path) -> None:
    table = runner.invoke(app, ["stats", str(built), "--top", "3"])
    assert table.exit_code == 0 and "унікальних термів" in table.stdout
    data = json.loads(runner.invoke(app, ["stats", str(built), "--json"]).stdout)
    assert data["num_docs"] == 6 and data["has_positions"] is True
    assert data["top_terms"][0] == ["filler", 40]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["search", "missing.bin", "x"], "не знайдено"),
        (["stats", "missing.bin"], "не знайдено"),
        (["index", "missing.jsonl", "-o", "x.bin"], "корпус не знайдено"),
    ],
)
def test_expected_errors_exit_1_with_one_line(args: list[str], message: str) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert message in result.stderr and "Traceback" not in result.stderr
    assert result.stdout == ""


def test_bad_query_syntax(built: Path) -> None:
    result = runner.invoke(app, ["search", str(built), "(python"])
    assert result.exit_code == 1 and "дужки" in result.stderr


def test_corrupt_index_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not a pickle")
    result = runner.invoke(app, ["search", str(bad), "x"])
    assert result.exit_code == 1 and "не вдалося прочитати індекс" in result.stderr


def test_phrase_without_positions(tiny_corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "np.bin"
    runner.invoke(app, ["index", str(tiny_corpus), "-o", str(out)])
    result = runner.invoke(app, ["search", str(out), '"event loop"'])
    assert result.exit_code == 1 and "positions" in result.stderr


def test_invalid_option_value(built: Path) -> None:
    result = runner.invoke(app, ["search", str(built), "x", "--scorer", "nope"])
    assert result.exit_code == 2  # помилка використання — код click/typer
