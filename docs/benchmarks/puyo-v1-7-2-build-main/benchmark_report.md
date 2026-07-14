# PUYO-157 build_main benchmark

- result: **BLOCKED: no configuration passed all quality gates**
- adopted budget: `{'depth': 3, 'width': 24, 'probe_width': 8}`
- PUYO-130: **not started** (PUYO-158 and PUYO-129 remain incomplete)

| config | mean max chain | premature | game over | p95 ms | gate |
|---|---:|---:|---:|---:|---|
| `d6-w24-p8` | 5.53 | 18 | 1 | 804.65 | FAIL |
| `d6-w24-p16` | 3.63 | 19 | 0 | 896.86 | FAIL |
| `d6-w32-p8` | 5.73 | 16 | 1 | 979.25 | FAIL |
| `d6-w32-p16` | 7.33 | 15 | 1 | 1111.53 | FAIL |
| `d6-w48-p8` | 6.03 | 20 | 1 | 1379.77 | FAIL |
| `d6-w48-p16` | 7.53 | 9 | 0 | 1475.81 | FAIL |
| `d8-w24-p8` | 5.73 | 17 | 1 | 1111.92 | FAIL |
| `d8-w24-p16` | 4.10 | 20 | 0 | 1236.89 | FAIL |
| `d8-w32-p8` | 5.17 | 20 | 2 | 1395.10 | FAIL |
| `d8-w32-p16` | 6.10 | 19 | 1 | 1522.89 | FAIL |
| `d8-w48-p8` | 3.33 | 18 | 4 | 1932.41 | FAIL |
| `d8-w48-p16` | 6.40 | 8 | 3 | 2100.12 | FAIL |
| `d10-w24-p8` | 6.43 | 12 | 1 | 1413.01 | FAIL |
| `d10-w24-p16` | 4.30 | 14 | 2 | 1559.25 | FAIL |
| `d10-w32-p8` | 4.90 | 22 | 0 | 1747.14 | FAIL |
| `d10-w32-p16` | 4.97 | 15 | 2 | 1928.32 | FAIL |
| `d10-w48-p8` | 4.57 | 22 | 2 | 2511.15 | FAIL |
| `d10-w48-p16` | 5.33 | 17 | 3 | 2657.31 | FAIL |

Gate: 30 seeds × 40 moves, mean maximum chain >= 10, premature 1-9 chain fires = 0, and early game-over = 0.
