| Target | Route | Concurrency | RPS | p50 (ms) | p95 | p99 | не-200 |
|---|---|---|---|---|---|---|---|
| local, 1 worker, `async def` | `/bench/search-async` | 50 | 19.2 | 2013.2 | 8379.6 | 21475.2 | 0 |
| local, 1 worker, `async def + to_thread` | `/bench/search-thread` | 50 | 18.4 | 2701.5 | 4000.2 | 4947.4 | 0 |
| local, 1 worker, `def` | `/search` | 50 | 17.7 | 2828.2 | 3937.1 | 5446.3 | 0 |
| local, 4 workers, `def` | `/search` | 50 | 70.3 | 637.9 | 1355.4 | 1528.4 | 0 |
