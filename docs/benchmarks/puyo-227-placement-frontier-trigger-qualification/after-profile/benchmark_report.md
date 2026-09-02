# PUYO-219 native evaluator hot-path profile

Decision: **PROFILE_COMPLETE_OPTIMIZATION_REQUIRED**.

The source-bound combined p95 is 810.385 ms for exactly 600,000 operations. The unchanged gate is 820.625 ms.

## Stage decomposition

| Stage | Evaluator cycle share | ns/node | 600k projection | Budget |
| --- | ---: | ---: | ---: | ---: |
| `base_feature_component_extraction` | 15.305% | 194.917 | 116.950 ms | 118.518 ms |
| `placement_enumeration_trigger_qualification` | 17.821% | 226.957 | 136.174 ms | 137.999 ms |
| `virtual_resolve_gravity` | 26.554% | 338.175 | 202.905 ms | 205.624 ms |
| `remaining_structure_scan` | 0.000% | 0.000 | 0.000 ms | 0.000 ms |
| `candidate_ranking_sha256` | 40.319% | 513.473 | 308.084 ms | 312.212 ms |

The interval sampler attributes 99.844% of the profiled combined loop to named, non-overlapping stages.

## Exact call-count distribution

| Counter | p50/node | p95/node | max/node | exact 600k total |
| --- | ---: | ---: | ---: | ---: |
| `pattern_nodes` | 415 | 415 | 415 | 249,000,000 |
| `executed_pattern_probes` | 18 | 25 | 30 | 10,444,966 |
| `resolution_nodes` | 18 | 25 | 30 | 10,444,966 |
| `rank_comparison_calls` | 17 | 24 | 29 | 9,846,138 |
| `rank_tie_calls` | 1 | 10 | 13 | 1,028,918 |
| `sha256_calls` | 2 | 11 | 14 | 1,383,996 |
| `single_component_frontiers` | 0 | 0 | 0 | 9,373 |
| `multi_component_frontiers` | 0 | 0 | 0 | 8,203 |
| `frontier_state_visits` | 0 | 0 | 0 | 154,686 |
| `qualified_candidates` | 0 | 0 | 0 | 89,054 |
| `resolution_group_comparisons` | 0 | 0 | 0 | 41,013 |
| `resolution_groups` | 0 | 0 | 0 | 36,325 |
| `precomputed_resolution_groups` | 0 | 0 | 0 | 17,577 |
| `precomputed_candidate_hits` | 12 | 22 | 29 | 7,419,181 |
| `resolution_cache_hits` | 2 | 7 | 11 | 1,470,676 |

## Diagnostic depth A/B and follow-up

The diagnostic-only max_added_puyos=1/2/3 medians are 271.181 / 668.669 / 1297.547 ns/node. Production remains max_added_puyos=3.

Measured follow-up order: **PUYO-220 → PUYO-223 → PUYO-222 → PUYO-221**.

The proportional stage ledger sums exactly to the 774.353480 ms evaluator envelope. Each implementation must preserve all semantic, allocation, determinism, and 80/24-byte ABI gates.
