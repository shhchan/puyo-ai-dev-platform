# PUYO-219 native evaluator hot-path profile

Decision: **PROFILE_COMPLETE_OPTIMIZATION_REQUIRED**.

The source-bound combined p95 is 12759.772 ms for exactly 600,000 operations. The unchanged gate is 820.625 ms.

## Stage decomposition

| Stage | Evaluator cycle share | ns/node | 600k projection | Budget |
| --- | ---: | ---: | ---: | ---: |
| `base_feature_component_extraction` | 5.780% | 1224.673 | 734.804 ms | 44.755 ms |
| `placement_enumeration_trigger_qualification` | 57.317% | 12144.976 | 7286.985 ms | 443.835 ms |
| `virtual_resolve_gravity` | 21.987% | 4658.808 | 2795.285 ms | 170.255 ms |
| `remaining_structure_scan` | 8.496% | 1800.213 | 1080.128 ms | 65.788 ms |
| `candidate_ranking_sha256` | 6.421% | 1360.498 | 816.299 ms | 49.719 ms |

The interval sampler attributes 99.985% of the profiled combined loop to named, non-overlapping stages.

## Exact call-count distribution

| Counter | p50/node | p95/node | max/node | exact 600k total |
| --- | ---: | ---: | ---: | ---: |
| `pattern_nodes` | 415 | 415 | 415 | 249,000,000 |
| `resolution_nodes` | 18 | 25 | 30 | 10,444,966 |
| `rank_comparison_calls` | 17 | 24 | 29 | 9,846,138 |
| `rank_tie_calls` | 1 | 10 | 13 | 1,028,918 |
| `sha256_calls` | 2 | 11 | 14 | 1,415,633 |

## Diagnostic depth A/B and follow-up

The diagnostic-only max_added_puyos=1/2/3 medians are 1868.005 / 6219.758 / 20980.162 ns/node. Production remains max_added_puyos=3.

Measured follow-up order: **PUYO-223 → PUYO-220 → PUYO-222 → PUYO-221**.

The proportional stage ledger sums exactly to the 774.353480 ms evaluator envelope. Each implementation must preserve all semantic, allocation, determinism, and 80/24-byte ABI gates.
