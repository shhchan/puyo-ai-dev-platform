# PUYO-219 native evaluator hot-path profile

Decision: **PROFILE_COMPLETE_OPTIMIZATION_REQUIRED**.

The source-bound combined p95 is 1918.176 ms for exactly 600,000 operations. The unchanged gate is 820.625 ms.

## Stage decomposition

| Stage | Evaluator cycle share | ns/node | 600k projection | Budget |
| --- | ---: | ---: | ---: | ---: |
| `base_feature_component_extraction` | 20.618% | 643.255 | 385.953 ms | 159.658 ms |
| `placement_enumeration_trigger_qualification` | 35.281% | 1100.723 | 660.434 ms | 273.203 ms |
| `virtual_resolve_gravity` | 8.182% | 255.271 | 153.163 ms | 63.359 ms |
| `remaining_structure_scan` | 0.000% | 0.000 | 0.000 ms | 0.000 ms |
| `candidate_ranking_sha256` | 35.918% | 1120.592 | 672.355 ms | 278.134 ms |

The interval sampler attributes 99.959% of the profiled combined loop to named, non-overlapping stages.

## Exact call-count distribution

| Counter | p50/node | p95/node | max/node | exact 600k total |
| --- | ---: | ---: | ---: | ---: |
| `pattern_nodes` | 415 | 415 | 415 | 249,000,000 |
| `executed_pattern_probes` | 18 | 25 | 30 | 10,444,966 |
| `resolution_nodes` | 18 | 25 | 30 | 10,444,966 |
| `rank_comparison_calls` | 17 | 24 | 29 | 9,846,138 |
| `rank_tie_calls` | 1 | 10 | 13 | 1,028,918 |
| `sha256_calls` | 2 | 11 | 14 | 1,415,633 |

## Diagnostic depth A/B and follow-up

The diagnostic-only max_added_puyos=1/2/3 medians are 820.236 / 1857.385 / 3173.570 ns/node. Production remains max_added_puyos=3.

Measured follow-up order: **PUYO-223 → PUYO-220 → PUYO-222 → PUYO-221**.

The proportional stage ledger sums exactly to the 774.353480 ms evaluator envelope. Each implementation must preserve all semantic, allocation, determinism, and 80/24-byte ABI gates.
