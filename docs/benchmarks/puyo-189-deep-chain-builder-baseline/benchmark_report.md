# PUYO-189 Deep-chain baseline evaluation

Decision: **FAIL**

The baseline was not promoted. A FAIL result is an evaluation outcome, not evidence that the locked thresholds were changed.

## Coverage

- Expected runs: 60
- Executed canonical runs: 0
- Fully evaluated runs: 0
- Preflight timeout: True

## Metrics

- Mean maximum actual fire chain: not evaluable
- Premature fires: 0
- Game overs: 0
- Simulator parity mismatches: 0
- Private future leaks: 0
- Decision p95 seconds: not evaluable
- Preflight latency lower bound seconds: 300.000000

## Gate results

- coverage: FAIL
  - expected_run_identities_present: PASS (actual=60, expected=60)
  - canonical_runs_executed: FAIL (actual=0, expected=60)
  - canonical_runs_fully_evaluated: FAIL (actual=0, expected=60)
  - run_lineage_consistent: FAIL (actual=0, expected=60)
- quality: FAIL
  - coverage_complete: FAIL (actual=0, expected=60)
  - mean_maximum_actual_fire_chain_count: FAIL (actual=not evaluable, expected=>= 10.0)
  - premature_fire_count: PASS (actual=0, expected=<= 0)
  - game_over_count: PASS (actual=0, expected=<= 0)
  - fallback_count: PASS (actual=0, expected=0)
- simulator_parity: FAIL
  - coverage_complete: FAIL (actual=0, expected=60)
  - simulator_parity_mismatch_count: PASS (actual=0, expected=<= 0)
- future_isolation: PASS
  - seed_coverage: PASS (actual=30, expected=30)
  - private_future_leak_count: PASS (actual=0, expected=<= 0)
- determinism: FAIL
  - complete_repeat_pairs: FAIL (actual=0, expected=30)
  - matching_action_and_plan_digests: FAIL (actual=0, expected=30)
- performance: FAIL
  - canonical_latency_coverage: FAIL (actual=0, expected=> 0 across 60 fully evaluated runs)
  - preflight_within_gate: FAIL (actual=300.000000, expected=<= 1.0)
  - decision_p95_seconds: FAIL (actual=not evaluable, expected=<= 1.0)
- gui_human_qa: FAIL
  - automated_gui_contract: PASS (actual=passed, expected=passed)
  - manual_gui_review: FAIL (actual=pending, expected=passed)

## Failure classification

- search: not_evaluable
- evaluator: not_evaluable
- flow: not_evaluable
- simulator: not_evaluable
- performance: confirmed_fail

## Lineage and reproduction

- Evaluated commit: `76d0b2a31ac9ea0a1d64fac373ecc282dfa4fb67`
- Config checksum: `bef9b02039b218dd72e6500e74a2c4b6a780b4d55c2c22b7e1a891a35da2dd2d`
- Reference source: https://github.com/citrus610/ama/tree/dea210bcd92965ae08fbc311f23565b0fab6dbbb
- Canonical resume: `.venv/bin/python -m eval.deep_chain_builder_benchmark run --max-runs 1 --output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline`
- Verification: `.venv/bin/python -m eval.deep_chain_builder_benchmark verify --output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline`

## Human review

Review the run index, failure taxonomy, preflight evidence, latency hotspot evidence, and GUI QA state before deciding whether to open a separate search/evaluator/performance task. Do not promote this result to a formal version, tag, stable policy, or champion.
