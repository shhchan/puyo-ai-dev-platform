# PUYO-167 diverse beam candidate benchmark

- paired decisions: 31
- deterministic replay: **PASS**
- quality gate: **PASS**
- frozen source: `docs/benchmarks/puyo-v1-7-2-search-diagnostics/decision_records.json` (`460f5fb26890d50117107269c342002750ecc84e0ab2d263044fe923502222c6`)

## Quality / cost comparison

| config | root coverage | path coverage | long-chain coverage | max chain | nodes | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| `scalar-d6-w48-p16-k16` | 1.000 | 0.129 | 1.000 | 1.677 | 3306.8 | 1829.19 | 3240.47 |
| `diverse-d6-w48-p16-k16` | 1.000 | 0.129 | 1.000 | 2.032 | 3278.5 | 1718.84 | 4577.74 |
| `scalar-d8-w64-p32-k16` | 1.000 | 0.161 | 1.000 | 4.387 | 6124.8 | 3222.56 | 6073.08 |

## Seed-level explanation

| seed | decisions | root delta | path delta | max-chain delta | explanation |
|---:|---:|---:|---:|---:|---|
| 123 | 3 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 124 | 1 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 125 | 1 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 126 | 3 | +0.000 | +0.333 | +0.000 | `improved_coverage_or_candidate_quality` |
| 127 | 2 | +0.000 | +0.500 | +0.000 | `improved_coverage_or_candidate_quality` |
| 128 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 129 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 130 | 1 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 131 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 132 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 133 | 2 | +0.000 | -0.500 | +0.000 | `coverage_regression` |
| 134 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 135 | 1 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 136 | 3 | +0.000 | -0.333 | +0.000 | `coverage_regression` |
| 137 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 138 | 1 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 139 | 1 | +0.000 | +0.000 | +1.000 | `improved_coverage_or_candidate_quality` |
| 140 | 3 | +0.000 | +0.000 | +3.333 | `improved_coverage_or_candidate_quality` |
| 141 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 142 | 1 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 143 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 144 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 145 | 4 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 146 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 147 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 148 | 2 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 149 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 150 | 0 | +0.000 | +0.000 | +0.000 | `no_reference_long_chain_opportunity` |
| 151 | 1 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |
| 152 | 1 | +0.000 | +0.000 | +0.000 | `unchanged_on_bounded_sample` |

## Gate

- deterministic: **PASS**
- reference_action_coverage_non_regression: **PASS**
- long_chain_action_coverage_non_regression: **PASS**
- max_candidate_chain_non_regression: **PASS**
- no_illegal_candidates: **PASS**
- no_game_over_candidates: **PASS**

Wall-clock latency is observational and excluded from the deterministic digest. Expanded-node counts are the deterministic runtime-budget proxy. A failed gate is persisted unchanged in the artifact.
