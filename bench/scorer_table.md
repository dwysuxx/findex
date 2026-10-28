| Corpus | Query | Docs matched | Postings scored | Lab 3 scorer (ms) | NumPy scorer (ms) | Speedup |
|---|---|---|---|---|---|---|
| dota2wiki.jsonl (124 docs) | common term: `abilities` | 124 | 124 | 0.198 ± 0.026 | 0.050 ± 0.009 | **4.0×** |
| dota2wiki.jsonl (124 docs) | rare term: `zones` | 2 | 2 | 0.005 ± 0.001 | 0.036 ± 0.001 | **0.1×** |
| dota2wiki.jsonl (124 docs) | 3-term query: `hero damage attack` | 124 | 372 | 0.439 ± 0.058 | 0.101 ± 0.012 | **4.3×** |
| dota2wiki_x16.jsonl (1984 docs) | common term: `abilities` | 1984 | 1984 | 2.918 ± 0.643 | 0.108 ± 0.016 | **26.9×** |
| dota2wiki_x16.jsonl (1984 docs) | rare term: `zones` | 32 | 32 | 0.086 ± 0.052 | 0.079 ± 0.034 | **1.1×** |
| dota2wiki_x16.jsonl (1984 docs) | 3-term query: `hero damage attack` | 1984 | 5952 | 14.246 ± 6.000 | 0.298 ± 0.128 | **47.9×** |
