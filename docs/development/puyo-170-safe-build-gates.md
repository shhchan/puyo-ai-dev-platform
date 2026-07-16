# PUYO-170 safe-build two-stage gate

## Purpose

v1.7.2 の safe-build 判定は、学習前の candidate generator と学習後の selected policy を
同じ `training_gate_passed` で扱わない。PUYO-170 では次の二つを独立 schema として固定する。

| Gate | 責務 | 次工程 |
| --- | --- | --- |
| Training capability gate | K-best candidate 集合に大連鎖へ到達できる安全な手が含まれるか | PUYO-130 long-run training の開始可否 |
| Promotion gate | 学習済み policy が候補から安全な手を実際に選ぶか | PUYO-133 の registry 登録可否 |

bounded reference や evaluation-only selector の結果を learned ranker の性能として扱ってはならない。

## Training capability gate

入力 schema は `puyo.v1_7_training_capability_gate_input.v1`、結果 schema は
`puyo.v1_7_training_capability_gate_result.v1` である。固定 seed を最低30 game、各40手で実行し、
各 decision で PUYO-169 の `puyo.worker_proposal_batch.v1` を保存する。

評価時だけ、proposal 内で大連鎖への到達可能性と安全性が最も高い candidate を選び、同じ状態を
PUYO-165 の bounded reference budget でも探索する。reference は oracle ではなく、現行候補に
不足する root action / path を分類するための診断上限である。

artifact には次を残す。

- 実際に辿った path の game ごとの最大連鎖数
- proposal 内の best-reachable chain と bounded-reference chain
- bounded-reference root/path coverage
- premature fire の `avoidable` / `candidate_limited` / `none` 分類
- no-fire candidate gap と forced game-over candidate gap
- proposal/reference の観測 wall-clock latency
- seed、config、commit、schema、candidate ID/mask/preview

gate は固定 suite の完走、平均最大連鎖10以上、avoidable candidate gap 0、forced game-over gap 0、
`build_main` registry の p95 latency budget 内、latency を除いた独立再生の一致をすべて要求する。
複数構成が pass した場合は proposal p95 latency が最小の構成を選び、同値なら
depth、width、probe width、K の順で小さい構成を選ぶ。pass 構成がなければ
`puyo_130_long_run.status` は明示的に `BLOCKED` となる。

## Promotion gate

入力 schema は `puyo.v1_7_promotion_gate_input.v1`、結果 schema は
`puyo.v1_7_promotion_gate_result.v1` である。PUYO-130 後の `post_training_candidate` だけを対象にし、
evaluation-only selector ではなく learned policy が選択した action を測る。

promotion には次の全条件を要求する。

- version-controlled な学習 seed が5件以上
- selected policy の30 game x 40手で平均最大連鎖10以上
- selected policy の premature fire 0、40手未満の game over 0
- PUYO-158 後の threat/outcome scenario が6/6
- parent node、training run、git commit を含む lineage
- GUI/replay QA の pass evidence

pretraining reference checkpoint は selected-policy 数値が条件を満たしても
`PENDING_POST_TRAINING` のままであり、registry 登録資格を持たない。

## Canonical evidence

統合ブランチの canonical artifact は PUYO-158、PUYO-129、PUYO-169 の merge commit をすべて
含む HEAD から生成する。checkpoint binary は `runs/` に置き、Git には追加しない。

```bash
python3 -m train.migrate_v1_7_1_checkpoint \
  runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/checkpoints/bootstrap.pt \
  runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/checkpoints/bootstrap-v1-7-2-migrated.pt \
  --force

python3 -m eval.v1_7_safe_build_gates run \
  --workers 12 --repetitions 2 \
  --promotion-checkpoint \
    runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/checkpoints/bootstrap-v1-7-2-migrated.pt \
  --checkpoint-role pretraining_reference

python3 -m eval.v1_7_safe_build_gates verify
```

`verify` は schema、canonical integration commit、artifact hash、gate 間の責務分離を検証する。
CI で開始・昇格条件そのものを要求する場合だけ、それぞれ
`--require-capability-gate`、`--require-promotion-gate` を追加する。

既定出力先 `docs/benchmarks/puyo-v1-7-2-safe-build-gates/` には gate input/result、
decision ごとの K-best candidate、seed 集計、決定論 digest、selected-policy game、outcome scenario、
checkpoint/config/commit/schema を含む manifest と Markdown report を保存する。pass/fail のどちらでも
同じ証跡を残し、fail を evaluator の失敗や昇格成功に読み替えない。
