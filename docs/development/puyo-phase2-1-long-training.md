# Phase 2.1 長時間学習運用メモ

PUYO-21 のフラット対戦 PPO ベースラインを、Phase 3 の HRL 比較対象として再現可能に残すための運用メモです。

## Artifact contract

`train.train_versus` は `log_dir/<run_id>/` に run 単位の成果物を出力します。`run_id` を config で空にすると `run_name-seed<seed>-<UTC timestamp>` が自動採番されます。

| path | 内容 |
|---|---|
| `config.yaml` | 入力 config と解決済み artifact path |
| `metadata.json` | run id、UTC 作成時刻、git commit、相手設定 |
| `metrics.csv` | `global_step,metric,value` 形式の学習メトリクス |
| `summary.json` | rolling window の score/win/max chain/ojama 集計 |
| `checkpoints/latest.pt` | 最終 checkpoint |
| `checkpoints/best.pt` | `best_checkpoint_metric` が改善した checkpoint |
| `checkpoints/step_<global_step>.pt` | `checkpoint_interval_updates` ごとの periodic checkpoint |

`metrics.csv` には以下を記録します。

- `episodic_return`
- `episodic_score`
- `episodic_opponent_score`
- `episodic_win`
- `episodic_length`
- `episodic_max_chain`
- `episodic_sent_ojama`
- `episodic_received_ojama`
- `loss_policy`
- `loss_value`
- `explained_variance`
- `SPS`

## Staged configs

| config | 目的 |
|---|---|
| `train/config/versus_long_smoke.yaml` | artifact 出力と設定読み込みの確認 |
| `train/config/versus_long_medium.yaml` | 長時間 run 前の中間確認 |
| `train/config/versus_long.yaml` | baseline 長時間学習 |
| `train/config/versus_long_quality.yaml` | PUYO-25 用の小さい報酬/ハイパーパラメータ改善案 |

実行例:

```bash
python3 -m train.train_versus --config train/config/versus_long_smoke.yaml
python3 -m train.train_versus --config train/config/versus_long_medium.yaml
python3 -m train.train_versus --config train/config/versus_long.yaml
python3 -m train.train_versus --config train/config/versus_long_quality.yaml
```

CPU/GPU を切り替える場合:

```bash
python3 -m train.train_versus --config train/config/versus_long.yaml --set device=cuda
python3 -m train.train_versus --config train/config/versus_long.yaml --set num_envs=4 --set num_steps=128
```

相手プールは `opponent_pool_path` で指定します。固定 baseline pool は `train/config/opponent_pool_baselines.json` です。過去 checkpoint を追加する場合は同じ JSON 形式で `policy_type: checkpoint` と `checkpoint_path` を持つ snapshot を追加します。

## Evaluation protocol

best checkpoint は第一基準を `mean_win_rate`、同率時は `mean_episode_score`、さらに同率時は `mean_max_chain` で選びます。Phase 3 ではこの checkpoint をフラット PPO baseline として扱います。

評価は同じ games/seed/max_steps で揃えます。

```bash
RUN_DIR=runs/versus_long/<run_id>
CKPT=$RUN_DIR/checkpoints/best.pt

python3 -m eval.arena --policy-a checkpoint --checkpoint-a "$CKPT" --policy-b random \
  --games 50 --seed 1001 --max-steps 500 \
  --csv "$RUN_DIR/arena_random_matches.csv" \
  --summary-csv "$RUN_DIR/arena_random_summary.csv" \
  --markdown "$RUN_DIR/arena_random.md" \
  --label baseline_vs_random

python3 -m eval.arena --policy-a checkpoint --checkpoint-a "$CKPT" --policy-b greedy \
  --games 50 --seed 1001 --max-steps 500 \
  --csv "$RUN_DIR/arena_greedy_matches.csv" \
  --summary-csv "$RUN_DIR/arena_greedy_summary.csv" \
  --markdown "$RUN_DIR/arena_greedy.md" \
  --label baseline_vs_greedy
```

過去 checkpoint との比較:

```bash
python3 -m eval.arena --policy-a checkpoint --checkpoint-a "$CKPT" \
  --policy-b checkpoint --checkpoint-b runs/versus_long/<previous_run_id>/checkpoints/best.pt \
  --games 50 --seed 1001 --max-steps 500 \
  --csv "$RUN_DIR/arena_previous_matches.csv" \
  --summary-csv "$RUN_DIR/arena_previous_summary.csv" \
  --markdown "$RUN_DIR/arena_previous.md" \
  --label baseline_vs_previous
```

`arena_*_matches.csv` の seed、winner、score、max chain を使い、Phase 4 の観戦確認用に以下を選びます。

- 勝ち局面: `winner=player_0` かつ score または sent ojama が高い seed
- 負け局面: `winner=player_1` かつ received ojama が高い seed
- 大連鎖局面: `max_chain_player_0` が最大の seed

代表局面の観戦:

```bash
python3 -m eval.spectate --policy-a checkpoint --checkpoint-a "$CKPT" \
  --policy-b greedy --seed <representative_seed> --max-steps 80
```

## PUYO-25 comparison

`versus_long_quality.yaml` は baseline から以下だけを小さく変えます。

- learning rate: `0.00025` から `0.0002`
- entropy coefficient: `0.01` から `0.02`
- score reward を下げ、attack/chain/win reward を上げる
- survival bonus を下げる

比較時は baseline と quality の `summary.json`、`arena_*_summary.csv` を同じ表に並べます。採用条件は greedy/random/previous の arena 評価で win rate または Elo delta が改善し、mean score と max chain が極端に悪化しないことです。

## Current branch status

このブランチは長時間 run そのものではなく、長時間 run を安全に実施するための metrics、artifact、config、arena report を整備します。実時間が必要な baseline/quality run は上記コマンドで同じブランチから継続できます。
