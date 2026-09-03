# PUYO-219 native evaluator hot-path profile

Decision: **PROFILE_COMPLETE_OPTIMIZATION_REQUIRED**.

The source-bound combined p95 is 1591.629 ms for exactly 600,000 operations. The unchanged gate is 820.625 ms.

## Stage decomposition

| Stage | Evaluator cycle share | ns/node | 600k projection | Budget |
| --- | ---: | ---: | ---: | ---: |
| `base_feature_component_extraction` | 27.671% | 712.700 | 427.620 ms | 214.273 ms |
| `placement_enumeration_trigger_qualification` | 43.346% | 1116.406 | 669.844 ms | 335.648 ms |
| `virtual_resolve_gravity` | 10.471% | 269.687 | 161.812 ms | 81.082 ms |
| `remaining_structure_scan` | 0.000% | 0.000 | 0.000 ms | 0.000 ms |
| `candidate_ranking_sha256` | 18.512% | 476.802 | 286.081 ms | 143.351 ms |

The interval sampler attributes 99.900% of the profiled combined loop to named, non-overlapping stages.

## Exact call-count distribution

| Counter | p50/node | p95/node | max/node | exact 600k total |
| --- | ---: | ---: | ---: | ---: |
| `pattern_nodes` | 415 | 415 | 415 | 249,000,000 |
| `executed_pattern_probes` | 18 | 25 | 30 | 10,444,966 |
| `resolution_nodes` | 18 | 25 | 30 | 10,444,966 |
| `rank_comparison_calls` | 17 | 24 | 29 | 9,846,138 |
| `rank_tie_calls` | 1 | 10 | 13 | 1,028,918 |
| `sha256_calls` | 2 | 11 | 14 | 1,383,996 |

## Diagnostic depth A/B and follow-up

The diagnostic-only max_added_puyos=1/2/3 medians are 949.058 / 1651.073 / 2631.272 ns/node. Production remains max_added_puyos=3.

Measured follow-up order: **PUYO-223 → PUYO-220 → PUYO-222 → PUYO-221**.

The proportional stage ledger sums exactly to the 774.353480 ms evaluator envelope. Each implementation must preserve all semantic, allocation, determinism, and 80/24-byte ABI gates.
