# PUYO-198 deep-chain native profile

## Result

Smoke and intermediate profiles completed. The locked reference first decision recorded a latency lower bound of 10.000 seconds under supervision. This is performance evidence, not canonical quality evidence.

## Completed profiles

| Workload | Wall seconds | Expanded nodes | Evaluated nodes |
| --- | ---: | ---: | ---: |
| smoke | 21.364487 | 1100 | 1100 |
| intermediate | 68.945396 | 3234 | 3234 |

## Intermediate deterministic profile

Exclusive time is non-overlapping. Inclusive time and per-call/node costs remain available in `raw/intermediate_profile.json`.

| Group | Calls | Exclusive seconds | Share | us / expanded node |
| --- | ---: | ---: | ---: | ---: |
| bounded_quiescence | 232123591 | 62.798129 | 92.32% | 19418.098 |
| search_control | 78219 | 1.780957 | 2.62% | 550.698 |
| component_extraction | 946945 | 1.060997 | 1.56% | 328.076 |
| transition | 1722270 | 0.838288 | 1.23% | 259.211 |
| aggregation_serialization | 844917 | 0.682161 | 1.00% | 210.934 |
| digest | 513154 | 0.515279 | 0.76% | 159.332 |
| evaluator_orchestration | 9705 | 0.201984 | 0.30% | 62.457 |
| decision_orchestration | 233223 | 0.086116 | 0.13% | 26.628 |
| evaluator_score | 3235 | 0.021422 | 0.03% | 6.624 |
| candidate_generation | 3241 | 0.017574 | 0.03% | 5.434 |
| beam_prune | 3558 | 0.006729 | 0.01% | 2.081 |
| transposition_table | 3042 | 0.003221 | 0.00% | 0.996 |
| scenario_aggregation | 154 | 0.000857 | 0.00% | 0.265 |
| diagnostics | 22 | 0.000151 | 0.00% | 0.047 |
| scenario_generation | 120 | 0.000107 | 0.00% | 0.033 |

## Gate budget

| Category | Decision budget (s) | Canonical us / expanded node |
| --- | ---: | ---: |
| transition | 0.010596 | 0.017660 |
| evaluator | 0.810029 | 1.350048 |
| search | 0.029375 | 0.048958 |
| serialization | 0.020000 | n/a |
| aggregation | 0.030000 | n/a |

## Boundary decision

The measured native candidate share is 99.8627%. The prior reference lower bound requires at least 300.0x end-to-end.

Adopt one native call per decision covering transition through root aggregation. Per-node Python callbacks and Python object conversion are outside the accepted boundary.

The PUYO-189 single sampled stack is retained only as supporting prior evidence; all percentages above come from PUYO-198 statistical and deterministic profiles.
