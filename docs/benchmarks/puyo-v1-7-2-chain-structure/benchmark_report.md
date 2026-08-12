# PUYO-173 structural chain evaluator ablation

- result: **PASS**
- feature version: `puyo.chain_structure_features.v1`
- weight version: `v1.0`
- corpus: 4 fixed / 4 tuning boards
- deterministic digest: `cb18afae1f03bff5c4960922d0a494025ca85e09fc57c3158b2adf03d2a74f52`
- observed node throughput: 290.27 evaluations/s

## Checks

- `deterministic_re_evaluation`: **PASS**
- `deterministic_corpus_repeat`: **PASS**
- `mirror_symmetry`: **PASS**
- `budget_bounds`: **PASS**
- `fixed_and_tuning_corpora`: **PASS**
- `extendable_above_unreachable`: **PASS**
- `evaluated_zero_is_explicit`: **PASS**

## Ablation rank changes

- `fixed-extendable-high` (growth-vs-dead-end): baseline 2 -> structure 1 (+1)
- `fixed-unreachable-high` (growth-vs-dead-end): baseline 1 -> structure 2 (-1)

The wall-clock profile is observational and excluded from the deterministic digest.
BuildPotential v2 remains the authoritative final-candidate diagnostic; this benchmark exercises only compact generic ordering features.

## Provenance

- git commit: `e9fd4e190a8e7bfcd76bb993c2a69acd5fbf6466`
- fixture: `tests/fixtures/chain_structure_cases.json`
- seed: `173`
- Ama analysis reference: `dea210bcd92965ae08fbc311f23565b0fab6dbbb`
- copied Ama source or numeric weights: none
