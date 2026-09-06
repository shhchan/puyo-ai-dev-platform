# PUYO-204 Deep-chain native baseline evaluation

Decision: **FAIL**

The baseline was not promoted. A FAIL result is an evaluation outcome, not evidence that the locked thresholds were changed.

## Coverage

- Expected runs: 60
- Executed canonical runs: 60
- Fully evaluated runs: 60
- Preflight timeout: False

## Metrics

- Mean maximum actual fire chain: 7.066667
- Premature fires: 50
- Game overs: 2
- Simulator parity mismatches: 34
- Private future leaks: 0
- Fallbacks: 0
- Scenario accounting failures: 0
- Decision p95 seconds: 0.783086
- Expanded nodes / native compute second: 1053581.707493
- Native serialization ratio: 0.001035
- Preflight latency lower bound seconds: 0.653068

## Gate results

- native_build: PASS
  - tracked_worktree_clean: PASS (actual=True, expected=True)
  - capabilities_available: PASS (actual=True, expected=True)
  - release_build: PASS (actual=True, expected=True)
  - source_revision_matches_commit: PASS (actual=True, expected=True)
  - python_abi_matches: PASS (actual=True, expected=True)
  - gil_detached: PASS (actual=True, expected=True)
  - configured_thread_mode_available: PASS (actual=True, expected=True)
  - configured_thread_mode_locked: PASS (actual=True, expected=True)
  - single_release_wheel_present: PASS (actual=True, expected=True)
- coverage: PASS
  - expected_run_identities_present: PASS (actual=60, expected=60)
  - canonical_runs_executed: PASS (actual=60, expected=60)
  - canonical_runs_fully_evaluated: PASS (actual=60, expected=60)
  - run_lineage_consistent: PASS (actual=60, expected=60)
  - canonical_native_build_per_run: PASS (actual=60, expected=60)
- quality: FAIL
  - coverage_complete: PASS (actual=60, expected=60)
  - mean_maximum_actual_fire_chain_count: FAIL (actual=7.066667, expected=>= 10.0)
  - premature_fire_count: FAIL (actual=50, expected=<= 0)
  - game_over_count: FAIL (actual=2, expected=<= 0)
  - fallback_count: PASS (actual=0, expected=0)
- simulator_parity: FAIL
  - coverage_complete: PASS (actual=60, expected=60)
  - simulator_parity_mismatch_count: FAIL (actual=34, expected=<= 0)
- future_isolation: PASS
  - seed_coverage: PASS (actual=30, expected=30)
  - private_future_leak_count: PASS (actual=0, expected=<= 0)
- determinism: PASS
  - complete_repeat_pairs: PASS (actual=30, expected=30)
  - matching_action_and_plan_digests: PASS (actual=30, expected=30)
  - cold_warm_and_one_thread_determinism: PASS (actual=True, expected=True)
- scenario_accounting: PASS
  - all_decisions_accounted_for: PASS (actual=2400, expected=2400)
  - missing_duplicate_or_unexpected_scenarios: PASS (actual=0, expected=0)
- performance: PASS
  - canonical_latency_coverage: PASS (actual=2400, expected=> 0 across 60 fully evaluated runs)
  - preflight_within_gate: PASS (actual=0.653068, expected=<= 1.0)
  - decision_p95_seconds: PASS (actual=0.783086, expected=<= 1.0)
- gui_human_qa: FAIL
  - automated_gui_contract: PASS (actual=passed, expected=passed)
  - dummy_gui_replay_contract: FAIL (actual=failed, expected=passed)
  - manual_gui_review: FAIL (actual=pending, expected=passed)

## Failure classification

- search: evaluated
- evaluator: evaluated
- transition: mismatch
- boundary: native_only
- flow: evaluated
- simulator: mismatch
- performance: evaluated

## Lineage and reproduction

- Evaluated commit: `47db7dd352ae21df0b2b4dd29169089e005c0f5e`
- Config checksum: `bef9b02039b218dd72e6500e74a2c4b6a780b4d55c2c22b7e1a891a35da2dd2d`
- Reference source: https://github.com/citrus610/ama/tree/dea210bcd92965ae08fbc311f23565b0fab6dbbb
- Canonical resume: `.venv/bin/python -m eval.deep_chain_builder_benchmark run --backend native --max-runs 1 --output-dir docs/benchmarks/puyo-204-deep-chain-native-baseline`
- Verification: `.venv/bin/python -m eval.deep_chain_builder_benchmark verify --output-dir docs/benchmarks/puyo-204-deep-chain-native-baseline`

## Human review

Review the run index, failure taxonomy, preflight evidence, latency hotspot evidence, and GUI QA state before deciding whether to open a separate search/evaluator/performance task. Do not promote this result to a formal version, tag, stable policy, or champion.
