# PUYO-219 native evaluator hot-path profile

Decision: **PROFILE_COMPLETE_OPTIMIZATION_REQUIRED**.

The source-bound combined p95 is 4961.052 ms for exactly 600,000 operations. The unchanged gate is 820.625 ms.

## Stage decomposition

| Stage | Evaluator cycle share | ns/node | 600k projection | Budget |
| --- | ---: | ---: | ---: | ---: |
| `base_feature_component_extraction` | 14.192% | 1162.550 | 697.530 ms | 109.900 ms |
| `placement_enumeration_trigger_qualification` | 8.705% | 713.070 | 427.842 ms | 67.409 ms |
| `virtual_resolve_gravity` | 45.973% | 3765.812 | 2259.487 ms | 355.996 ms |
| `remaining_structure_scan` | 17.722% | 1451.683 | 871.010 ms | 137.233 ms |
| `candidate_ranking_sha256` | 13.407% | 1098.186 | 658.912 ms | 103.816 ms |

The interval sampler attributes 99.968% of the profiled combined loop to named, non-overlapping stages.

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

The diagnostic-only max_added_puyos=1/2/3 medians are 1548.295 / 3618.052 / 8175.388 ns/node. Production remains max_added_puyos=3.

Measured follow-up order: **PUYO-220 → PUYO-223 → PUYO-222 → PUYO-221**.

The proportional stage ledger sums exactly to the 774.353480 ms evaluator envelope. Each implementation must preserve all semantic, allocation, determinism, and 80/24-byte ABI gates.
