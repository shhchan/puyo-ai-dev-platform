# Model Lineage Report

- runs: 0
- checkpoints: 0
- edges: 14
- issues: 0

## Version Timeline

| version | model family | policy | state | git commit | decision |
|---|---|---|---|---|---|
| `manager_rule` | RuleBasedManagerPolicy | `manager_rule` | baseline | `35e8f659737e0da34bdbe37ec1613eda95a4a527` | Retained as the interpretable rule-based comparison baseline for v1.7.x. |
| `v1.7.0` | Adaptive Chain Manager | `v1_7_analyzer_manager` | candidate | `d9b5e16c5406b14fefa208eda56f430358c2ce57` | Analyzer, tactic registry, and planner contracts pass headless QA; playable policy and GUI QA remain pending in downstream tickets. |

## Runs

| run | trainer/scenario | seed | key metrics |
|---|---|---:|---|

## Checkpoints

| checkpoint | role | path |
|---|---|---|

## Evaluations

| evaluation | kind | status | metrics | path | compatibility |
|---|---|---|---|---|---|
| `Legacy Analyzer report before PR #49` | legacy_analyzer_benchmark | rejected | - | `-` | regenerate_required |
| `v1.7.0 Analyzer scenario benchmark` | headless_benchmark | passed | failed=0, pass_rate=1.0, passed=24, scenarios=24 | `docs/benchmarks/puyo-v1-7-0-smoke/analyzer_report.json` | native |
| `v1.7.0 GUI QA` | gui_qa | pending | - | `-` | pending |

## Inputs And Artifacts

| input | type | version | path | sha256 |
|---|---|---|---|---|
| `v1.7.0 TacticSpec registry` | config | `-` | `train/config/v1_7_tactic_registry.yaml` | `4e2ffda9766825d4124e7d07efdacb92de7944a0610c53d7ac336ea12e903548` |
| `v1.7.0 Analyzer scenarios` | dataset | `puyo.state_analyzer.scenarios.v1` | `eval/scenarios/v1_7_analyzer.json` | `479aa9634bba5df5efe453199675a5ed06a530cd85b315f104a0f2584342fff2` |

## Schemas

| schema | type | version | compatibility |
|---|---|---|---|
| `State Analyzer diagnostics v1` | analyzer_schema | `puyo.state_analyzer.diagnostics.v1` | native |
| `State Analyzer input v1` | analyzer_schema | `puyo.state_analyzer.input.v1` | native |
| `All-clear diagnostics v1` | diagnostics_schema | `puyo.all_clear_diagnostics.v1` | native |
| `Lifecycle feature contract v1` | feature_schema | `puyo.state_analyzer.input.v1#lifecycle-features` | native |
| `TacticSpec schema v1` | tactic_schema | `tactic-schema-v1` | native |

## Graph Edges

| source | relationship | target | reason |
|---|---|---|---|
| `evaluation:legacy-analyzer-pre-pr49` | `rejected_by` | `evaluation:v1.7.0-analyzer-scenarios` | Legacy output lacks Analyzer, all-clear diagnostics, and lifecycle feature schema snapshots. |
| `evaluation:v1.7.0-analyzer-scenarios` | `derived_from` | `evaluation:legacy-analyzer-pre-pr49` | Regenerated after PR #49 with lifecycle and carry contracts. |
| `model_version:manager_rule` | `promoted_to` | `registry_role:baseline` | Retained as the stable, interpretable comparison policy. |
| `model_version:v1.7.0` | `derived_from` | `model_version:manager_rule` | Replaces fixed routing with Analyzer-driven tactics while retaining manager_rule as a comparison baseline. |
| `model_version:v1.7.0` | `evaluated_by` | `evaluation:v1.7.0-analyzer-scenarios` | - |
| `model_version:v1.7.0` | `evaluated_by` | `evaluation:v1.7.0-gui-qa` | Reserved for PUYO-122 realtime UI and main.py evidence. |
| `model_version:v1.7.0` | `promoted_to` | `registry_role:candidate` | Headless Analyzer QA passed; champion promotion remains blocked on playable and GUI QA evidence. |
| `model_version:v1.7.0` | `trained_with` | `config:v1.7.0-tactic-registry` | Deterministic baseline consumes this registry as its versioned tactical configuration. |
| `model_version:v1.7.0` | `trained_with` | `dataset:v1.7.0-analyzer-scenarios` | The pre-training baseline is validated against the fixed scenario dataset. |
| `model_version:v1.7.0` | `uses_schema` | `analyzer_schema:diagnostics-v1` | - |
| `model_version:v1.7.0` | `uses_schema` | `analyzer_schema:input-v1` | - |
| `model_version:v1.7.0` | `uses_schema` | `diagnostics_schema:all-clear-v1` | - |
| `model_version:v1.7.0` | `uses_schema` | `feature_schema:lifecycle-v1` | - |
| `model_version:v1.7.0` | `uses_schema` | `tactic_schema:tactic-schema-v1` | - |

## Promotion And Rejection Decisions

- `model_version:manager_rule` promoted_to `registry_role:baseline`: Retained as the stable, interpretable comparison policy.
- `model_version:v1.7.0` promoted_to `registry_role:candidate`: Headless Analyzer QA passed; champion promotion remains blocked on playable and GUI QA evidence.
- `evaluation:legacy-analyzer-pre-pr49` rejected_by `evaluation:v1.7.0-analyzer-scenarios`: Legacy output lacks Analyzer, all-clear diagnostics, and lifecycle feature schema snapshots.
