# PUYO-219 native evaluator hot-path profile

Decision: **PROFILE_COMPLETE_OPTIMIZATION_REQUIRED**.

The source-bound combined p95 is 2076.095 ms for exactly 600,000 operations. The unchanged gate is 820.625 ms.

## Stage decomposition

| Stage | Evaluator cycle share | ns/node | 600k projection | Budget |
| --- | ---: | ---: | ---: | ---: |
| `base_feature_component_extraction` | 21.420% | 724.637 | 434.782 ms | 165.864 ms |
| `placement_enumeration_trigger_qualification` | 35.144% | 1188.949 | 713.370 ms | 272.142 ms |
| `virtual_resolve_gravity` | 8.391% | 283.868 | 170.321 ms | 64.975 ms |
| `remaining_structure_scan` | 0.000% | 0.000 | 0.000 ms | 0.000 ms |
| `candidate_ranking_sha256` | 35.045% | 1185.586 | 711.352 ms | 271.372 ms |

The interval sampler attributes 99.950% of the profiled combined loop to named, non-overlapping stages.

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

The diagnostic-only max_added_puyos=1/2/3 medians are 906.499 / 1976.765 / 3392.512 ns/node. Production remains max_added_puyos=3.

Measured follow-up order: **PUYO-223 → PUYO-220 → PUYO-222 → PUYO-221**.

The proportional stage ledger sums exactly to the 774.353480 ms evaluator envelope. Each implementation must preserve all semantic, allocation, determinism, and 80/24-byte ABI gates.
