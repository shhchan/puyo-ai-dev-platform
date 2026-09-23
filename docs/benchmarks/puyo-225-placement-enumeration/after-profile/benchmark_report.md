# PUYO-219 native evaluator hot-path profile

Decision: **PROFILE_COMPLETE_OPTIMIZATION_REQUIRED**.

The source-bound combined p95 is 1256.651 ms for exactly 600,000 operations. The unchanged gate is 820.625 ms.

## Stage decomposition

| Stage | Evaluator cycle share | ns/node | 600k projection | Budget |
| --- | ---: | ---: | ---: | ---: |
| `base_feature_component_extraction` | 29.611% | 597.344 | 358.406 ms | 229.294 ms |
| `placement_enumeration_trigger_qualification` | 36.763% | 741.629 | 444.978 ms | 284.679 ms |
| `virtual_resolve_gravity` | 14.207% | 286.606 | 171.964 ms | 110.016 ms |
| `remaining_structure_scan` | 0.000% | 0.000 | 0.000 ms | 0.000 ms |
| `candidate_ranking_sha256` | 19.418% | 391.719 | 235.032 ms | 150.364 ms |

The interval sampler attributes 99.913% of the profiled combined loop to named, non-overlapping stages.

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
| `resolution_group_comparisons` | 6 | 11 | 14 | 3,712,540 |
| `resolution_groups` | 6 | 10 | 12 | 3,720,736 |
| `precomputed_resolution_groups` | 4 | 6 | 7 | 2,165,627 |
| `precomputed_candidate_hits` | 12 | 22 | 29 | 7,419,181 |
| `resolution_cache_hits` | 2 | 7 | 11 | 1,470,676 |

## Diagnostic depth A/B and follow-up

The diagnostic-only max_added_puyos=1/2/3 medians are 751.726 / 1307.087 / 2084.073 ns/node. Production remains max_added_puyos=3.

Measured follow-up order: **PUYO-223 → PUYO-220 → PUYO-222 → PUYO-221**.

The proportional stage ledger sums exactly to the 774.353480 ms evaluator envelope. Each implementation must preserve all semantic, allocation, determinism, and 80/24-byte ABI gates.
