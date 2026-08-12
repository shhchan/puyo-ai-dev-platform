# PUYO-175 Worker Proposal v2 Benchmark

The fixed corpus uses K=8 and six canonical scenario slots. Timing is observational;
serialized and ranker digests neutralize wall-clock fields.

## Coverage and size

- candidate feature coverage mean: 0.9360
- scenario feature coverage mean: 1.0000
- serialized payload p50/p95: 172232 / 181200 bytes
- serialization p50/p95: 18.607 / 20.872 ms
- compatibility projection p50/p95: 1.857 / 2.194 ms
- v1 zero/missingness confusion count: 12

## Status counts

- `budget_exhausted`: 4
- `evaluated`: 20
- `legacy_missing`: 4
- `not_evaluated`: 4

## Checks

- `round_trip`: PASS
- `rank_zero_compatibility`: PASS
- `fixed_k8`: PASS
- `six_scenario_mask`: PASS
- `candidate_ids_preserved_in_v1_projection`: PASS
- `candidate_mask_preserved_in_v1_projection`: PASS
- `selected_action_preserved_in_v1_projection`: PASS
- `shared_cost_not_in_v2_candidate_features`: PASS
- `projection_is_explicitly_lossy`: PASS
- `status_masks_invalid_candidates`: PASS
- `deterministic_serialized_digest`: PASS
- `deterministic_ranker_input_digest`: PASS
- `all_four_missingness_statuses`: PASS
- `passed`: PASS

Reproduce with:

```bash
python -m eval.v1_7_worker_proposal_v2_benchmark run
python -m eval.v1_7_worker_proposal_v2_benchmark verify
```
