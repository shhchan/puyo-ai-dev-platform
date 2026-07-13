# Model Lineage Report

- runs: 1
- checkpoints: 1
- edges: 8
- issues: 0

## Version Timeline

| version | model family | policy | state | git commit | decision |
|---|---|---|---|---|---|
| `v1.7.0` | Adaptive Chain Manager | `v1_7_analyzer_manager` | previous_stable | `b1c8460863f42170ef5ff65c4c05c4ad96c4d471` | PUYO-128 comparison baseline |
| `v1.7.1` | Adaptive Chain Manager | `v1_7_bootstrap_manager` | candidate | `b1c8460863f42170ef5ff65c4c05c4ad96c4d471` | PUYO-128 benchmark hard gates passed |

## Runs

| run | trainer/scenario | seed | key metrics |
|---|---|---:|---|
| `puyo-128-bootstrap-round-1-seed1129` | v1_7_manager_bootstrap | 1129 | global_step=16040 |

## Checkpoints

| checkpoint | role | path |
|---|---|---|
| `puyo-128-bootstrap-round-1-seed1129:bootstrap` |  | `None` |

## Evaluations

| evaluation | kind | status | metrics | path | compatibility |
|---|---|---|---|---|---|
| `PUYO-128 bootstrap benchmark and GUI QA` | paired_benchmark_gui_replay | passed | - | `docs/benchmarks/puyo-v1-7-1-smoke/benchmark_summary.json` | native |

## Inputs And Artifacts

| input | type | version | path | sha256 |
|---|---|---|---|---|
| `bootstrap dataset 9b167e778fe7` | dataset | `-` | `-` | `8e0d417db12257588754930790086fc38f1fcd82f896fc7b826717e45c98a3fa` |

## Schemas

| schema | type | version | compatibility |
|---|---|---|---|
| `v1.7.0 structured analyzer input` | feature_schema | `puyo.state_analyzer.input.v1` | native |
| `v1.7.1 learned manager feature contract` | feature_schema | `puyo.v1_7_strategy_manager.features.v1` | retrain_required |

## Graph Edges

| source | relationship | target | reason |
|---|---|---|---|
| `checkpoint:932730916ee4a17e0ea41babbe0638c332c3efe365264b81682456d70b5ef60b` | `evaluated_by` | `evaluation:PUYO-128` | - |
| `checkpoint:932730916ee4a17e0ea41babbe0638c332c3efe365264b81682456d70b5ef60b` | `promoted_to` | `registry_role:candidate` | PUYO-128 benchmark hard gates passed |
| `model_version:v1.7.0` | `uses_schema` | `feature_schema:v1.7.0` | - |
| `model_version:v1.7.1` | `derived_from` | `model_version:v1.7.0` | - |
| `model_version:v1.7.1` | `implements` | `checkpoint:932730916ee4a17e0ea41babbe0638c332c3efe365264b81682456d70b5ef60b` | - |
| `model_version:v1.7.1` | `uses_schema` | `feature_schema:v1.7.1` | - |
| `training_run:puyo-128-bootstrap-round-1-seed1129` | `produced` | `checkpoint:932730916ee4a17e0ea41babbe0638c332c3efe365264b81682456d70b5ef60b` | - |
| `training_run:puyo-128-bootstrap-round-1-seed1129` | `trained_with` | `dataset:9b167e778fe72dc0691628988d1ea68a8a97762ca23f0f4c8aa630e33afad55e` | - |

## Promotion And Rejection Decisions

- `checkpoint:932730916ee4a17e0ea41babbe0638c332c3efe365264b81682456d70b5ef60b` promoted_to `registry_role:candidate`: PUYO-128 benchmark hard gates passed
