# PUYO-219 native evaluator hot-path profile

Decision: **PROFILE_COMPLETE_OPTIMIZATION_REQUIRED**.

The source-bound combined p95 is 995.521 ms for exactly 600,000 operations. The unchanged gate is 820.625 ms.

## Stage decomposition

| Stage | Evaluator cycle share | ns/node | 600k projection | Budget |
| --- | ---: | ---: | ---: | ---: |
| `base_feature_component_extraction` | 8.458% | 133.812 | 80.287 ms | 65.495 ms |
| `placement_enumeration_trigger_qualification` | 50.613% | 800.744 | 480.446 ms | 391.926 ms |
| `virtual_resolve_gravity` | 16.639% | 263.251 | 157.950 ms | 128.848 ms |
| `remaining_structure_scan` | 0.000% | 0.000 | 0.000 ms | 0.000 ms |
| `candidate_ranking_sha256` | 24.289% | 384.276 | 230.566 ms | 188.085 ms |

The interval sampler attributes 99.868% of the profiled combined loop to named, non-overlapping stages.

## Exact call-count distribution

| Counter | p50/node | p95/node | max/node | exact 600k total |
| --- | ---: | ---: | ---: | ---: |
| `pattern_nodes` | 415 | 415 | 415 | 249,000,000 |
| `executed_pattern_probes` | 18 | 25 | 30 | 10,444,966 |
| `resolution_nodes` | 18 | 25 | 30 | 10,444,966 |
| `rank_comparison_calls` | 17 | 24 | 29 | 9,846,138 |
| `rank_tie_calls` | 1 | 10 | 13 | 1,028,918 |
| `sha256_calls` | 2 | 11 | 14 | 1,383,996 |
| `single_component_frontiers` | 2 | 3 | 4 | 914,042 |
| `multi_component_frontiers` | 2 | 3 | 4 | 1,001,974 |
| `frontier_state_visits` | 36 | 64 | 81 | 19,467,680 |
| `qualified_candidates` | 18 | 25 | 30 | 10,444,966 |
| `resolution_group_comparisons` | 8 | 14 | 22 | 4,398,109 |
| `resolution_groups` | 6 | 10 | 12 | 3,720,736 |
| `precomputed_resolution_groups` | 4 | 6 | 7 | 2,165,627 |
| `precomputed_candidate_hits` | 12 | 22 | 29 | 7,419,181 |
| `resolution_cache_hits` | 2 | 7 | 11 | 1,470,676 |

## Diagnostic depth A/B and follow-up

The diagnostic-only max_added_puyos=1/2/3 medians are 324.341 / 872.510 / 1641.533 ns/node. Production remains max_added_puyos=3.

Measured follow-up order: **PUYO-223 → PUYO-220 → PUYO-222 → PUYO-221**.

The proportional stage ledger sums exactly to the 774.353480 ms evaluator envelope. Each implementation must preserve all semantic, allocation, determinism, and 80/24-byte ABI gates.
