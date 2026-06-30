# PUYO-71 Experiment Suites

`train.experiment_suite` は seed、scenario、replicate の実験行列を同じ定義から再生成し、
run ごとの artifact と集約結果を保存する runner です。

## Suite Format

```yaml
name: versus-smoke-suite
trainer: versus_ppo
config: train/config/versus_long_smoke.yaml
output_dir: runs/experiment_suites/versus-smoke-suite
seeds: [1, 2, 3]
replicates: 1
max_parallel: 1
metrics: [global_step, episodes, mean_win_rate, mean_episode_score, mean_max_chain]
overrides:
  total_timesteps: 2048
  num_envs: 1
  num_steps: 32
  minibatch_size: 32
scenarios:
  - name: random
    overrides:
      opponent_policy: random
  - name: greedy
    overrides:
      opponent_policy: greedy
```

Run id は `<suite>-<scenario>-seed<seed>-rep<replicate>` で生成します。
同じ suite 定義なら matrix 順序と run id は同じです。

## Run

```bash
python3 -m train.experiment_suite --suite path/to/suite.yaml
```

完了済み run は `<output_dir>/runs/<run_id>/summary.json` を見て `skipped` として再利用します。
再実行する場合は `--force` を指定します。`--dry-run` は学習を実行せず matrix と manifest だけを書きます。

## Outputs

| path | 内容 |
|---|---|
| `<output_dir>/suite_manifest.json` | suite 定義、matrix、run record、集約結果 |
| `<output_dir>/runs.csv` | run id、scenario、seed、replicate、status、path |
| `<output_dir>/summary.json` | overall / scenario 別の mean、variance、stdev、95% CI、paired comparison |
| `<output_dir>/runs/<run_id>/` | 各 trainer の通常 artifact directory |

2 scenario の suite では、同じ `(seed, replicate)` 同士の paired 差分も `summary.json` に出力します。

## Seed Behavior

既存 trainer は config の `seed` を base seed として使い、環境 index や episode index から派生 seed を作ります。
suite runner は run ごとに `seed` を上書きするため、seed 別の artifact と集約結果を同じ形式で追跡できます。

`max_parallel > 1` は `ProcessPoolExecutor` で run 単位に並列化します。GPU や同一出力先を共有する設定では、
suite 定義側で `num_envs`、device、output_dir を明示して競合を避けてください。
