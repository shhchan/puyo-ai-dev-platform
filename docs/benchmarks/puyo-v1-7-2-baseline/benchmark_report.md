# v1.7.2 Pre-training Benchmark / Scenario QA

- evaluator: **PASS**
- training gate: **BLOCKED**
- v1.7.1 checkpoint: `/home/sion2000114/workspaces/dev/puyo_ai_dev_platform/runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/checkpoints/bootstrap.pt`
- existing champion: `/home/sion2000114/workspaces/dev/puyo_ai_dev_platform/runs/realtime_ppo/human-check-parent/checkpoints/latest.pt`

PUYO-132 の完了条件は evaluator と baseline artifact の成立です。training gate が BLOCKED の場合、PUYO-130 は開始しません。

## Safe-build

| policy | mean max chain | p50 | p90 | max | premature | trigger loss | game over | p95 ms | gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `v1_7_1` | 4.40 | 4.0 | 5.0 | 6 | 118 | 128/1028 | 0 | 1076.49 | FAIL |
| `forced_build_main` | 4.53 | 5.0 | 5.0 | 6 | 65 | 198/1092 | 0 | 1177.35 | FAIL |
| `worker_large` | 7.13 | 7.0 | 8.0 | 10 | 40 | 274/920 | 0 | 896.84 | FAIL |
| `standard_beam` | 7.40 | 7.0 | 10.0 | 10 | 37 | 259/893 | 0 | 2261.05 | FAIL |

## Paired arena

| opponent | matches | win rate | max chain | response rate | cancel rate | self-choke | decision ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| `manager_rule` | 40 | 0.675 | 4 | 0.894 | 0.081 | 0.000 | 162.56 |
| `standard_beam` | 40 | 0.775 | 4 | 0.908 | 0.006 | 0.000 | 160.12 |
| `worker_large` | 40 | 0.800 | 4 | 0.882 | 0.012 | 0.000 | 161.05 |
| `existing_checkpoint` | 40 | 1.000 | 4 | 0.827 | 1.000 | 0.000 | 180.98 |

## Fixed scenarios and GUI

- Analyzer: 24/24
- outcome scenarios: 3/6
- lifecycle/carry/cancel parity: PASS
- GUI attack profile: PASS

## Human-visible QA

`python3 main.py` から観戦を選び、1P に v1.7.1 checkpoint、2P に v1_7_analyzer_manager、seed 123、speed 1x を指定します。
`gui_qa_replay.json` は `python3 -m eval.model_viewer` で attack/carry/lifecycle diagnostics を確認できます。
