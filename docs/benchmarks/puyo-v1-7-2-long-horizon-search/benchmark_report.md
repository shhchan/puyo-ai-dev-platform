# PUYO-174 Long-Horizon Expected-Chain Search

This validation uses PUYO-174 held-out seeds, not the canonical safe-build gate seeds.
Wall-clock time is observational; expanded-node counts are authoritative.

| stage | mean max chain | mean expected score | survival | dead-end | nodes | elapsed (s) |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 0.000 | 0.0 | 1.000 | 0.000 | 1034.0 | 0.1195 |
| compact_kernel | 0.000 | 0.0 | 1.000 | 0.000 | 1034.0 | 0.0893 |
| lightweight_evaluator | 0.000 | 0.0 | 1.000 | 0.000 | 1034.0 | 5.4400 |
| six_scenario | 0.000 | 0.0 | 1.000 | 0.000 | 2244.0 | 11.7082 |
| long_horizon | 1.667 | 733.3 | 1.000 | 0.000 | 5412.0 | 29.6338 |
| transposition_table | 1.667 | 733.3 | 1.000 | 0.000 | 5412.0 | 29.2639 |

## Determinism

- two-repeat digest match: True
- selected action stable: True

## Stop / Go

- capability improved over baseline: True
- 10-chain reachability visible in this validation: False
- verdict: STOP for evaluator/pruning diagnosis

The registered quality-d16 contract remains depth=16, width=250, scenarios=6; count-budget truncation is reported rather than hidden.
