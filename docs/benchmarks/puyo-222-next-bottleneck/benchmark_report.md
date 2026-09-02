# PUYO-222 next evaluator bottleneck verification

Decision: **PASS_FOLLOW_UP_REQUIRED**.

The largest unimproved baseline stage was `base_feature_component_extraction`. Its sampled cycles per node fell from 5415.460 to 2225.326 (58.908% reduction).
Combined transition-plus-evaluator p95 fell from 3244.640 ms to 1918.176 ms (40.882% reduction).

## Gates

- fixture / transition / selected-child mismatches: 0 / 0 / 0
- component-metadata exact comparisons: 512, mismatches: 0
- response SHA-256 and all logical counter distributions: unchanged
- normal hot-path allocations: 0; child/result ABI: 80/24 bytes
- production max_added_puyos remains 3

## Residual budget

The combined p95 remains 1097.551 ms above the 820.625 ms gate. The next unimproved stage is `candidate_ranking_sha256`; follow-up PUYO-224 is required.
