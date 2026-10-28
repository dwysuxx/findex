| Query | Docs matched | Lab 07: ranking (ms) | Lab 08: ranking (ms) | Lab 07: + snippets (ms) | Lab 08: + snippets (ms) | Speedup (whole request) |
|---|---|---|---|---|---|---|
| `hero damage attack` | 124 | 0.447 | 0.134 | 44.16 | 3.93 | **11.2×** |
| `pudge` | 4 | 0.022 | 0.027 | 15.88 | 0.32 | **49.7×** |
| `"meat hook"` | 1 | 0.030 | 0.034 | 4.37 | 0.25 | **17.2×** |
| `illusion OR illusions` | 118 | 0.279 | 0.109 | 38.57 | 1.71 | **22.6×** |
| `stun OR slow OR silence` | 120 | 0.343 | 0.147 | 37.57 | 1.54 | **24.4×** |
