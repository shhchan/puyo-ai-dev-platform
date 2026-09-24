# 次世代 decision trajectory 契約

`train.nextgen_trajectory.write_nextgen_run` は，実際の decision 時点で得た `Diagnostics` を `DecisionRecord` に入れた `EpisodeRecord` 列から，`puyo.nextgen.trajectory.v1` を生成する．探索器・実行器・trainer は実装しない．合成 fixture による独立 producer として利用できる．

各 episode は `episodes/<episode_id>/` に圧縮 JSONL を保存する．`trajectory.jsonl.gz` は自 decision ごとに 1 行，`events.jsonl.gz` は scheduler 試行，`evidence.jsonl.gz` は候補 batch，`public_replay.jsonl.gz` は request と公開 snapshot を持つ．`public_history.json` は PUYO-248 の公開 timing history を任意で保存する．`private_reproduction.json` は隠れ seed 等の再現情報，`oracle.json` は事後評価ラベル専用で，学習入力とは物理的に分ける．`summary.json` に complete / incomplete / truncated，勝敗・終了理由，実績，decision / 欠損 / partial 件数を明示する．時間制限・切断には勝者を設定できない．

ledger の主キーは `run_id/episode_id/player_id/decision_id` である．`identity` は request ID，snapshot / batch digest，decision schema hash を含む．`policy_input` は `PolicyFeatures.to_dict()` の実値をそのまま保存し，順序つき値・欠損 bit・6 戦術 mask・registry hash 以外を受け付けない．`decision` は selector と候補，rule / RL 出力，phase と切替情報を保存する．`evidence_ref` は候補根拠 sidecar の相対 path・SHA-256・同じ主キーを参照する．`execution.receipt` は要求 action と実行 action，outcome と各 tick を保持する．`transition` は次の同一 player decision，match tick 差，reward 成分，episode summary 参照，actor / value 適格性を保持する．

`iter_actor_samples(run_dir)` は，まず `validate_nextgen_run` で全 artifact の存在・byte 数・SHA-256 と schema，主キー・参照・receipt の結合を検証してから，`(policy_input, selected_tactic_id)` だけを返す．`ExecutionReceipt.outcome == "activated"` の行だけが actor 対象である．stale / timeout / fallback と receipt 欠損は除外理由を記録し，reward・遷移は ledger に残す．`Candidate.fallback` は batch 内の決定論的 root を示し，scheduler 介入を表す receipt の `fallback` outcome とは別である．value 更新可否は ledger に独立して記録する．

manifest は既存 `train.artifacts.write_artifact_manifest` を使う．`extra.nextgen` は schema，episode 一覧，source / native / checkpoint / opponent，独立 environment / search / selector seed，探索 quota，timing schema / digest / latency mode，OS / CPU / thread 数と p50 / p95，dataset split，gate report を記録する．`config_resolved.yaml`，feature / tactic / template schema snapshot，gate report と全 episode ファイルを閉じた後で SHA-256・byte 数を記録し，最後に manifest を書く．この `config_resolved.yaml` は JSON 形式の YAML 1.2 文書である．各 episode は train / validation / test の 1 集合だけに属する．古い schema は暗黙移行しない．

fixture 検証は次で実行する．

```bash
python -m unittest tests.test_nextgen_trajectory
```
