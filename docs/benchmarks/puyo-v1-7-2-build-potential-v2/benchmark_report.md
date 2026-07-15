# PUYO-166 BuildPotential v2 benchmark

- frozen source: `docs/benchmarks/puyo-v1-7-2-search-diagnostics/decision_records.json` (`460f5fb26890d50117107269c342002750ecc84e0ab2d263044fe923502222c6`)
- decisions/candidates: 1200/16806
- v2 feature coverage: 1.000
- overall evaluation: **PASS**
- frozen replay: **PASS** (0 issues)
- deterministic cache on/off replay: **PASS**
- bounded budget: **PASS**
- migration: **PASS**

This is observational evidence, not a quality gate. The bounded reference is not an oracle. In particular, `reference_candidate_value` includes the legacy board-shape heuristic, so negative correlations with the new danger/flexibility contract are reported as design differences rather than improvements or regressions.

## reference_predicted_max_chain

| feature | pairs | rho | tau-b | top1 | informative top1 | feature/target tie decisions |
|---|---:|---:|---:|---:|---:|---:|
| `v1_scalar` | 16806 | -0.0827 | -0.0698 | 0.8840 | 0.2057 (175) | 572/1087 |
| `v2_predicted_chain_potential` | 16806 | -0.1336 | -0.1132 | 0.9132 | 0.4057 (175) | 728/1087 |
| `v2_ignition_readiness` | 16806 | -0.0949 | -0.0890 | 0.9199 | 0.4514 (175) | 1130/1087 |
| `v2_alternative_robustness` | 16806 | -0.1283 | -0.1134 | 0.9107 | 0.3886 (175) | 800/1087 |
| `v2_continuation_flexibility` | 16806 | -0.0708 | -0.0657 | 0.9466 | 0.6343 (175) | 1051/1087 |
| `v2_danger_margin` | 16806 | 0.0391 | 0.0318 | 0.9541 | 0.6857 (175) | 941/1087 |
| `v2_composite` | 16806 | -0.1021 | -0.0829 | 0.9299 | 0.5200 (175) | 246/1087 |

## reference_candidate_value

| feature | pairs | rho | tau-b | top1 | informative top1 | feature/target tie decisions |
|---|---:|---:|---:|---:|---:|---:|
| `v1_scalar` | 16806 | 0.7862 | 0.7115 | 0.5167 | 0.5110 (1184) | 572/48 |
| `v2_predicted_chain_potential` | 16806 | 0.4203 | 0.3754 | 0.3990 | 0.3919 (1184) | 728/48 |
| `v2_ignition_readiness` | 16806 | -0.0016 | 0.0038 | 0.1795 | 0.1698 (1184) | 1130/48 |
| `v2_alternative_robustness` | 16806 | 0.2929 | 0.2446 | 0.1761 | 0.1664 (1184) | 800/48 |
| `v2_continuation_flexibility` | 16806 | -0.3091 | -0.2444 | 0.1628 | 0.1529 (1184) | 1051/48 |
| `v2_danger_margin` | 16806 | -0.5287 | -0.4134 | 0.1361 | 0.1258 (1184) | 941/48 |
| `v2_composite` | 16806 | 0.2442 | 0.1938 | 0.2988 | 0.2905 (1184) | 246/48 |

## Coverage and migration semantics

- evaluation status counts: `{"available": 11009, "budget_exhausted": 3150, "not_found": 2647}`
- field-less v1 positive values: `14525` -> `legacy_partial`
- field-less v1 zeros: `9530` -> `unknown` (not coerced to evaluated zero)
- board-backed recomputations: 2396/2396 exact matches

Ranking uses the lowest action index as the deterministic tie-break. Tie-aware top-set overlap and target/feature tie counts remain available in `benchmark_summary.json`.
