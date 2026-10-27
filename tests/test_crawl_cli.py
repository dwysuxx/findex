"""Lab 06: `findex crawl` наскрізно — проти локального дзеркала (localhost, без інтернету)."""

import json
import os
import subprocess
import sys
import warnings
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from findex.cli import app
from findex.crawler.mirror import Article, MirrorConfig, Site, serve

runner = CliRunner()

ARTICLES = [
    Article("axe", "Axe", "Axe hunts Pudge.\nCounter Helix spins."),
    Article("pudge", "Pudge", "Pudge throws Meat Hook at Axe."),
    Article("lina", "Lina", "Lina casts Laguna Blade."),
]


@pytest.fixture
def mirror() -> Generator[Site]:
    with serve(ARTICLES, MirrorConfig(origins=2, copies=2, latency=(0.0, 0.01), faults=False)) as site:
        yield site


@pytest.fixture
def mirror_process(tmp_path: Path) -> Generator[str]:
    """Дзеркало в ОКРЕМОМУ процесі -> URL першого origin.

    Для перевірки «цикл не блокувався» дзеркало не можна тримати в потоках того самого процесу:
    його 20 потоків-обробників по черзі тримають GIL, і потік циклу подій чекає своєї черги
    (convoy з Lab 05) — debug=True бачить це як «крок задачі тривав > 100 мс», хоча краулер
    нічого не блокував. Тест падав приблизно в 1 з 3 запусків.
    """
    corpus = tmp_path / "mirror.jsonl"
    corpus.write_text(
        "\n".join(json.dumps({"id": a.id, "title": a.title, "text": a.text}) for a in ARTICLES),
        encoding="utf-8",
    )
    cmd = [sys.executable, "-m", "findex.crawler.mirror", str(corpus), "--origins", "2", "--copies", "2"]
    cmd += ["--latency", "0", "0.01", "--no-faults"]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}
    )
    assert proc.stdout is not None
    try:
        while not (line := proc.stdout.readline()).startswith("ready"):
            assert line, "mirror died"
        yield line.split()[1]
    finally:
        proc.terminate()
        proc.wait()


def test_mirror_serves_site(mirror: Site) -> None:
    with httpx.Client(follow_redirects=False) as c:
        assert "Disallow: /private/" in c.get(mirror.url(0, "/robots.txt")).text
        assert c.get(mirror.url(0, "/loop/a")).headers["location"] == "/loop/b"
        assert c.get(mirror.url(0, "/nope")).status_code == 404
        root = c.get(mirror.url(0, "/")).text
    assert "/wiki/" in root and mirror.url(1, "/") in root


def test_mirror_faults_only_on_first_request() -> None:
    config = MirrorConfig(origins=1, copies=1, latency=(0.0, 0.0), slow=0.0)
    with serve([Article(f"h{i}", f"H{i}", "x") for i in range(60)], config) as site:
        faulty = [(o, p) for (o, p) in site.pages if p.startswith("/wiki/") and site.fault(o, p)]
        assert faulty  # детерміновано є збої
        assert all(site.fault(o, p) is None for o, p in faulty)  # другий запит — без збою


def test_crawl_cli_writes_jsonl_and_log(mirror_process: str, tmp_path: Path) -> None:
    out, log = tmp_path / "crawl.jsonl", tmp_path / "crawl.log"
    args = [
        "crawl",
        mirror_process,
        "-o",
        str(out),
        "--log",
        str(log),
        "--max-pages",
        "50",
        "--debug-loop",
    ]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    # Забутий await — це RuntimeWarning "coroutine ... was never awaited" (детерміновано).
    assert not [w for w in caught if "never awaited" in str(w.message)]
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    urls = {r["url"] for r in rows}
    assert len(urls) == len(rows)  # без дублів
    assert {r["title"] for r in rows} >= {"Axe", "Pudge", "Lina", "Axe (copy 1)"}
    assert all(set(r) >= {"id", "text", "url", "title", "retrieved_at"} for r in rows)
    journal = log.read_text(encoding="utf-8")
    assert "robots.txt disallows" in journal and "redirect loop" in journal  # 404 — у test_crawler.py
    # «Жоден крок циклу не довший за 100 мс» (debug=True) тут не перевіряємо: це залежить від
    # навантаження машини (CI), а не від коду. Перевірено бенчмарком в окремих процесах (README L6.7).

    index_out = tmp_path / "idx.bin"
    result = runner.invoke(app, ["index", str(out), "-o", str(index_out)])
    assert result.exit_code == 0, result.output
    hits = runner.invoke(app, ["search", str(index_out), "meat hook", "--json", "--no-snippets"])
    assert "/wiki/pudge" in json.loads(hits.stdout.splitlines()[0])["title"]  # pudge або копія pudge~1


def test_crawl_cli_rejects_bad_seed(tmp_path: Path) -> None:
    result = runner.invoke(app, ["crawl", "ftp://x.test/", "-o", str(tmp_path / "o.jsonl")])
    assert result.exit_code == 1 and "не http(s) URL" in result.output
