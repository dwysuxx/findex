# pyright: basic
# ^ matplotlib типізований частково; strict тонув би в reportUnknown* на кожному виклику.
"""Lab 05: графік speedup vs workers з bench/results.jsonl (+ bench/io_results.json) -> bench/speedup.png.

    python -m findex.bench_plot

matplotlib — лише dev-залежність: потрібен для картинки в README, не для findex.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.ticker import ScalarFormatter

from findex.bench_parallel import GIL_PYTHON, load_rows, summarize

OUT = Path("bench/speedup.png")
IO = Path("bench/io_results.json")

# Категоріальна палітра у фіксованому порядку; колір іде за серією, а не за рангом.
SERIES: list[tuple[str, str, str, str]] = [  # (python, executor, підпис, колір)
    (GIL_PYTHON, "threads", f"threads, {GIL_PYTHON} (GIL)", "#2a78d6"),
    (GIL_PYTHON, "processes", f"processes, {GIL_PYTHON}", "#eb6834"),
    ("3.13t", "threads", "threads, 3.13t (no GIL)", "#1baf7a"),
    ("3.14t", "threads", "threads, 3.14t (no GIL)", "#eda100"),
]
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _style(ax: Axes, title: str, max_x: int) -> None:
    ax.plot([1, max_x], [1, max_x], ls="--", lw=1.2, color=MUTED, label="ideal (linear)")
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    ax.set_xlabel("workers", color=MUTED)
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 2, 4, 8] if max_x <= 8 else [1, 2, 4, 8, 16, 32, 64])
    ax.get_xaxis().set_major_formatter(ScalarFormatter())
    ax.grid(True, color=GRID, lw=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED)


def main() -> None:
    summary = summarize(load_rows())
    corpora = sorted({k[0] for k in summary}, key=len)
    panels = len(corpora) + (1 if IO.is_file() else 0)
    fig, axes = plt.subplots(1, panels, figsize=(5.2 * panels, 4.4), sharey=False)
    axes = list(axes) if panels > 1 else [axes]

    for ax, corpus in zip(axes, corpora, strict=False):
        mb = next(r["corpus_mb"] for r in load_rows() if r["corpus"] == corpus)
        _style(ax, f"CPU-bound: index {corpus} ({mb:.0f} MB)", 8)
        placed: list[float] = []  # y підписів на цій панелі — щоб близькі не накладались
        for python, executor, label, color in SERIES:
            pts = sorted(
                (k[3], v["speedup"]) for k, v in summary.items() if k[:3] == (corpus, python, executor)
            )
            if len(pts) < 2:
                continue
            xs, ys = zip(*pts, strict=True)
            ax.plot(xs, ys, marker="o", ms=6, lw=2, color=color, label=label, mec="white", mew=1.5)
            dy = -8 if any(abs(ys[-1] - y) < 0.3 for y in placed) else 0
            placed.append(ys[-1])
            ax.annotate(f"{ys[-1]:.1f}×", (xs[-1], ys[-1]), xytext=(6, dy), textcoords="offset points",
                        va="center", fontsize=9, color=INK)  # fmt: skip
        ax.set_ylabel(f"speedup vs serial ({GIL_PYTHON})", color=MUTED)
        ax.set_ylim(0, 8.5)

    if IO.is_file():
        ax = axes[-1]
        data = json.loads(IO.read_text(encoding="utf-8"))
        rows = data["rows"]
        _style(
            ax,
            f"I/O-bound: {data['docs']} HTTP GETs, {data['latency'] * 1000:.0f} ms each",
            rows[-1]["workers"],
        )
        xs = [r["workers"] for r in rows]
        ys = [rows[0]["wall"] / r["wall"] for r in rows]
        ax.plot(xs, ys, marker="o", ms=6, lw=2, color=SERIES[0][3], label=SERIES[0][2], mec="white", mew=1.5)
        ax.annotate(f"{ys[-1]:.0f}×", (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=9, color=INK)  # fmt: skip
        ax.set_ylabel("speedup vs 1 thread", color=MUTED)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False, fontsize=9)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    OUT.parent.mkdir(exist_ok=True)
    fig.savefig(OUT, dpi=130, facecolor="white")
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
