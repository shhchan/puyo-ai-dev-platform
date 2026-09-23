# PUYO-224 canonical candidate-ranking verification

Decision: **PASS_FOLLOW_UP_REQUIRED**.

Candidate-ranking cycles per node fell from the canonical PUYO-222 value 3876.663 to 1599.501 (58.740% reduction).
On the same-session paired parent, combined transition-plus-evaluator p95 fell from 2076.095 ms to 1591.629 ms (23.335% reduction).
The historical PUYO-222 combined p95 is recorded separately as 1918.176 ms; the performance gate uses the paired build to exclude run-to-run host drift.

## Gates

- fixture / transition / selected-child mismatches: 0 / 0 / 0
- canonical bytes, full digest ordering, and protection oracle: 0 mismatches
- response SHA-256 and logical budget/rank counters: unchanged
- physical SHA-256 compressions: did not increase
- normal hot-path allocations: 0; child/result ABI: 80/24 bytes
- production max_added_puyos remains 3

## Residual budget

The combined p95 remains 771.004 ms above the 820.625 ms gate. The next stage is `placement_enumeration_trigger_qualification`; follow-up PUYO-225 is required.
