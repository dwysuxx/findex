| Target | Route | Concurrency | RPS | p50 (ms) | p95 | p99 | не-200 |
|---|---|---|---|---|---|---|---|
| local, 1 worker, `def`, mode=keyword | `/search` | 50 | 206.4 | 237.7 | 303.5 | 353.2 | 0 |
| local, 4 workers, `def`, mode=keyword | `/search` | 50 | 410.4 | 113.8 | 209.0 | 245.3 | 0 |
| local, 1 worker, `def`, mode=semantic | `/search` | 50 | 306.4 | 151.9 | 284.2 | 368.2 | 0 |
| local, 1 worker, `def`, mode=hybrid | `/search` | 50 | 281.6 | 170.2 | 276.5 | 335.5 | 0 |
