"""Lab 05 bonus: I/O-bound контрольний бенчмарк — тут потоки масштабуються.

    python -m findex.bench_io                     # -> bench/io_results.json + таблиця
    python -m findex.bench_io --latency 0.05 --workers 1 2 4 8 16 32 64

Локальний HTTP-сервер (окремий процес) віддає документи корпусу із затримкою `latency` на запит —
модель мережі без мережі: результат відтворюваний і нікого не навантажує. Клієнт качає всі
документи через ThreadPoolExecutor. Поки потік чекає на recv(), GIL відпущено, і решта потоків працює.
"""

import argparse
import json
import multiprocessing as mp
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing.queues import Queue
from pathlib import Path

OUT = Path("bench/io_results.json")


def serve(corpus: Path, latency: float, port_out: "Queue[int]") -> None:
    docs = [line.encode() for line in corpus.read_text(encoding="utf-8").splitlines() if line.strip()]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            time.sleep(latency)  # «мережа»
            body = docs[int(self.path.rsplit("/", 1)[-1])]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # без шуму в stderr
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.request_queue_size = 128
    port_out.put(server.server_address[1])
    server.serve_forever()


def fetch(url: str) -> int:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return len(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path, nargs="?", default=Path("data/dota2wiki.jsonl"))
    parser.add_argument("--latency", type=float, default=0.05)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8, 16, 32, 64])
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    port_q: Queue[int] = mp.Queue()
    server = mp.Process(target=serve, args=(args.corpus, args.latency, port_q), daemon=True)
    server.start()
    try:
        port = port_q.get(timeout=30)
        n_docs = sum(1 for line in args.corpus.open(encoding="utf-8") if line.strip())
        urls = [f"http://127.0.0.1:{port}/doc/{i}" for i in range(n_docs)]
        fetch(urls[0])  # прогрів

        rows: list[dict[str, float]] = []
        for workers in args.workers:
            walls: list[float] = []
            total = 0
            for _ in range(args.runs):
                t0 = time.perf_counter()
                with ThreadPoolExecutor(workers) as pool:
                    total = sum(pool.map(fetch, urls))
                walls.append(time.perf_counter() - t0)
            rows.append({"workers": workers, "wall": statistics.median(walls), "bytes": total})
    finally:
        server.terminate()

    base = rows[0]["wall"]
    print(f"{n_docs} GET-запитів, затримка {args.latency * 1000:.0f} мс; теоретичний мінімум серійно: "
          f"{n_docs * args.latency:.1f} с\n")  # fmt: skip
    print("| Потоків | Wall, с (медіана) | Speedup |")
    print("|---|---|---|")
    for r in rows:
        print(f"| {r['workers']:.0f} | {r['wall']:.2f} | {base / r['wall']:.1f}× |")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(
        json.dumps({"latency": args.latency, "docs": n_docs, "rows": rows}, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
