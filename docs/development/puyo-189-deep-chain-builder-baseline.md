# PUYO-189 deep-chain baseline evaluation

## Purpose

`deep_chain_builder` の品質、性能、決定論、authoritative simulator parity、private future
isolation、GUI 表示を別々に判定し、experimental baseline として受け入れられるかを決めます。
品質未達や実行未完了を成功値へ補完せず、FAIL の場合も原因分類と再現情報を残します。

## Locked contract

設定の正本は `train/config/deep_chain_builder.yaml` です。

| Dimension | Locked value |
| --- | ---: |
| profile | `reference` |
| depth / width / scenarios | 16 / 250 / 6 |
| maximum expanded nodes | 600,000 |
| seeds | 123–152 |
| repeats | 2 per seed |
| placements | 40 per run |
| mean maximum actual fire | at least 10 chains |
| premature fire / game over | 0 / 0 |
| parity mismatch / private future leak | 0 / 0 |
| repeat action / plan digest | identical |
| one-decision p95 | at most 1.0 second |

Prediction-only chain values and evaluator scores do not count as actual fire. The canonical runner does
not stop search on elapsed time: the reference profile's deterministic expanded-node budget remains
authoritative, while wall-clock time is observational evidence.

## Commands

Run one pending identity and persist it immediately:

```bash
.venv/bin/python -m eval.deep_chain_builder_benchmark run \
  --backend native \
  --max-runs 1 \
  --output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline
```

Repeat the command to resume, or omit `--max-runs` to process every pending identity. Completed run files
under `runs/` are not overwritten.

Run a bounded, non-canonical first-decision latency diagnosis:

```bash
.venv/bin/python -m eval.deep_chain_builder_benchmark preflight \
  --backend native \
  --timeout-seconds 5 \
  --output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline
```

The supervised process may be terminated by this diagnostic. Its result proves only a latency lower
bound and is never counted as an action, trajectory, actual fire, parity sample, or completed canonical
run.

Generate or refresh the aggregate evidence and verify it:

```bash
.venv/bin/python -m eval.deep_chain_builder_benchmark finalize \
  --output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline
.venv/bin/python -m eval.deep_chain_builder_benchmark verify \
  --output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline
```

## Evidence layout

| Artifact | Purpose |
| --- | --- |
| `runs/seed-NNN-repeat-NN.json` | authoritative actions, boards, actual fires, parity, plan and trace evidence for one run |
| `run_index.json` | all 60 predeclared identities, including an explicit reason for every pending identity |
| `benchmark_summary.json` | aggregate metrics, independent gates, failure taxonomy, and baseline decision |
| `future_isolation.json` | per-seed counterfactual private-sentinel boundary audit |
| `preflight.json` | non-canonical first-decision latency diagnostic |
| `gui_qa.json` | automated contract result and separate normal-window human review state |
| `lineage.json` | evaluated commit, config checksum, source attribution, reproduction, and promotion constraints |
| `benchmark_manifest.json` | size and SHA-256 checksum of every evidence artifact |
| `benchmark_report.md` | concise human review report |

`finalize` lists every planned run even when canonical execution is incomplete. Presence in
`run_index.json` means the identity is accounted for, not that it was executed. Coverage, determinism,
quality, parity, and performance gates remain FAIL until their required samples exist.

## GUI QA

Automated contracts are checked with:

```bash
.venv/bin/python -m unittest \
  tests.test_deep_chain_builder_benchmark \
  tests.test_deep_chain_builder \
  tests.test_deep_chain_builder_smoke \
  tests.test_realtime_versus_ui \
  tests.test_launcher
```

The normal-window review still requires a person to start `python main.py` with the `smoke` profile and
confirm all of the following:

1. step 1 ghost and actual selected placement agree;
2. selection reason and decision trace timings are visible;
3. replan replaces the previous plan's ghosts;
4. `O` toggles only the plan overlay and does not change the selected action or plan ID.

Record the human result, then rerun `finalize` so the manifest covers the updated QA artifact:

```bash
.venv/bin/python -m eval.deep_chain_builder_benchmark record-gui-qa \
  --automated-passed \
  --automated-command "<exact test command>" \
  --manual-status passed \
  --reviewer "<reviewer>" \
  --notes "<observations>"
.venv/bin/python -m eval.deep_chain_builder_benchmark finalize
```

## Acceptance and follow-up

Only an all-PASS result may set `accepted_as_experimental_baseline=true`. This workflow never creates a
formal model version, Git tag, stable/champion promotion, or corrective Jira task. On FAIL, a person first
reviews the saved `search` / `evaluator` / `flow` / `simulator` / `performance` classification and decides
whether a separately scoped task is warranted.

## Recorded result (2026-08-20)

Evaluated implementation commit: `76d0b2a31ac9ea0a1d64fac373ecc282dfa4fb67`.

The seed 123 reference preflight did not complete its first decision within 300 seconds. This is more than
300 times the locked one-decision limit, so the performance gate is a confirmed FAIL. A separate 30-second
sampled stack was inside `RunLongRangeSearchStep`, specifically `ChainStructureEvaluator.evaluate` /
`bounded_quiescence`; `performance_hotspot.json` records the complete sampled path without claiming it is a
statistical profile.

The canonical 60-run quality execution was not launched after that result. It therefore has no actual-fire,
parity, repeat-determinism, node-count, cache-hit, or flow-timing samples; those values are represented as
`null` / not evaluable rather than zero-success evidence. `run_index.json` still accounts for all 60 locked
identities and marks each one `not_executed` with the preflight reason. Consequently coverage, quality,
parity, determinism, and performance all FAIL.

The counterfactual visible-boundary audit covered all 30 seeds and found zero private-future leaks. The 77
focused automated tests passed. Dummy SDL additionally produced `puyo.gui_qa.v1` and
`puyo-realtime-match-v1`; player 0's selected action matched plan step 1, the trace contained all seven flow
steps, and plan overlay was enabled. A normal-window review of ghost appearance, replan replacement, and
the `O` toggle remains pending, so GUI human QA also FAILs.

Final decision: **FAIL — do not accept or promote `deep_chain_builder` as an experimental baseline.** A
person must review the performance/search-evaluator evidence before deciding whether to create a corrective
task. No version, tag, stable/champion promotion, or corrective Jira issue was created.

## Attribution

The design uses the public source pinned at
[`dea210bcd92965ae08fbc311f23565b0fab6dbbb`](https://github.com/citrus610/ama/tree/dea210bcd92965ae08fbc311f23565b0fab6dbbb)
as architectural input under the MIT License, Copyright (c) 2023 citrus610. No reference implementation
code is copied into this repository.
