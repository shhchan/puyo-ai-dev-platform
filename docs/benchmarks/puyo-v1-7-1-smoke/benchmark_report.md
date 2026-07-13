# v1.7.1 Bootstrap Benchmark / GUI QA / Lineage

- result: **PASS**
- checkpoint: `runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/checkpoints/bootstrap.pt`
- checkpoint sha256: `932730916ee4a17e0ea41babbe0638c332c3efe365264b81682456d70b5ef60b`
- seeds: 123–142 (paired sides)

## Hard Gates

| gate | result | actual | expected |
|---|---|---|---|
| `checkpoint_valid` | PASS | `[]` | no validation errors |
| `analyzer_scenarios` | PASS | `{"failed": 0, "pass_rate": 1.0, "passed": 24, "scenarios": 24}` | 24/24 passed |
| `initial_empty_false_positives` | PASS | `{"gui": 0, "headless": 0}` | 0 |
| `all_clear_bonus_double_consumptions` | PASS | `{"gui": 0, "headless": 0}` | 0 |
| `minimum_win` | PASS | `87` | >= 1 across all formal matches |
| `minimum_chain` | PASS | `6` | >= 1 |
| `self_choke_vs_v1_7_0` | PASS | `{"v1_7_0": 0.025, "v1_7_1": 0.0}` | v1.7.1 <= v1.7.0 |
| `gui_completed` | PASS | `{"completed": true, "interrupted": false, "scores": {"player_0": 51, "player_1": 54}, "termination_reason": "tick_limit", "ticks": 600, "winner": "player_1"}` | completed without interruption |
| `gui_decision_activated` | PASS | `4` | >= 1 |
| `replay_verified` | PASS | `"d93fdbef51eb0e15f3eef2548bb6d4c803c598d421e5272af5737c7a29ccc358"` | all ticks and final hash match |
| `lineage_valid` | PASS | `[]` | 0 issues |

## Paired Results

| opponent | matches | wins | losses | draws | score rate | max chain | self-choke |
|---|---:|---:|---:|---:|---:|---:|---:|
| `manager_rule` | 40 | 27 | 13 | 0 | 0.675 | 4 | 0 |
| `beam` | 40 | 31 | 9 | 0 | 0.775 | 4 | 0 |
| `v1_7_analyzer_manager` | 40 | 29 | 11 | 0 | 0.725 | 6 | 0 |

## Human-visible QA

Run `python3 main.py`, choose 観戦, and use the checkpoint above as 1P `v1_7_bootstrap_manager` against 2P `v1_7_analyzer_manager` at seed 123 and speed 1x.
Open `gui_qa_replay.json` with `python3 -m eval.model_viewer` to inspect tick diagnostics and lineage.
