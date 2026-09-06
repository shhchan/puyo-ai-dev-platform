# PUYO-231 target 固定探索予算比較

GUI の目標連鎖数は1〜19の整数。比較実験は target 6/8/10/12 の4条件に固定する。
既定値6、native ABIの整数表現、PUYO-204 canonical target6 と過去artifactは保持する。
既定値と品質基準10の整合は PUYO-232、探索改良の判断は PUYO-233 で扱う。

## 固定契約と証跡

- 全条件: reference depth16 / width250 / scenarios6 / max expanded nodes600,000。
- seed123〜152 × repeats1/2 × targets6/8/10/12 = 240 identities、各40 placements。
- safe/no-threat、elapsed timeoutなし、native scenario-6。各identityを新規プロセスで逐次実行する。
- 全条件で品質基準10。実発火1〜9は premature。内部target到達は別の指標。
- 固有seedの品質推定は事前指定したrepeat1を使用。repeat2は決定論検証に用い、独立seed数を増やさない。
- `experiment_manifest.json` を実行前に作成し、clean commit、release wheel、host、config checksum、全identityを固定する。
- 条件以外の設定は共通checksum、target込み設定は条件別checksumを持つ。再開時はcommit/build/host/configの混在を拒否する。
- PUYO-229の完全状態parity v2を使用。予測最大と実発火の差は別のfollowthrough診断として扱う。

rawは `target-NN/seed-NNN-repeat-NN.json.gz`。圧縮は可逆で、全decisionの盤面・選択理由・plan・
予測と実発火・phase timing・node・RSS・parity・scenario accountingを保持する。
`summary.json` はrawから再計算可能。未実行はpending、未完了は理由を残し、集計の分母から除外する。
全60runが完全評価されるまで各条件の品質・性能・決定論などをPASSにしない。
`verify` の成功は証跡の整合を意味し、品質合格を意味しない。

`diagnostic-target-NN.json` はseed123の同一初期盤面をcold/warmで測定し、private counterfactualも確認する。
閉ループtrajectoryの異なる盤面を含むlatencyとは分けてレビューする。
trajectoryのfirst decisionは各新規プロセスの初回探索、later decisionは異なる盤面を含むため、
同一盤面のcold/warm比とは解釈しない。RSSはLinuxのプロセスhigh-water mark（KiB）。

## 実行・再開・検証

測定に使う実装commitをcleanな状態でcheckoutし、release extensionをそのcommitからbuild/installする。
過去証跡を再実行する場合はmanifestの `build_provenance.evaluated_commit` を別worktreeでcheckoutし、
新しい空ディレクトリに出力する。既存証跡へのresumeは同一host/build/configだけで実行できる。

```bash
bash scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_target_ablation init
# 4コマンドは順に実行（それぞれ新規プロセス）
.venv/bin/python -m eval.deep_chain_target_ablation diagnostic --target 6
.venv/bin/python -m eval.deep_chain_target_ablation diagnostic --target 8
.venv/bin/python -m eval.deep_chain_target_ablation diagnostic --target 10
.venv/bin/python -m eval.deep_chain_target_ablation diagnostic --target 12
.venv/bin/python -m eval.deep_chain_target_ablation run
.venv/bin/python -m eval.deep_chain_target_ablation finalize
.venv/bin/python -m eval.deep_chain_target_ablation verify
```

既存rawは上書きせずスキップする。中断された未保存identityは最初から再実行する。
部分実行・条件別集計も可能。

```bash
.venv/bin/python -m eval.deep_chain_target_ablation run --target 10 --max-runs 2
.venv/bin/python -m eval.deep_chain_target_ablation finalize --target 10
.venv/bin/python -m eval.deep_chain_target_ablation verify --target 10
```

全コマンドは `--output-dir <new-directory>` を指定できる。
`paired_comparison.json` で同じseedの4条件・各repeatを比較する。seed135/138の個別診断は条件別summaryに含む。
forecast_followthroughのhorizon_complete=falseは観測区間の打切りで、実際の予測誤差と断定しない。

## GUI の人間確認

`python main.py` → 観戦 → policy `deep_chain_builder` → 目標連鎖数を左右操作で選ぶ。
1〜19を1ずつ選択でき、7も保持される。CLIの例:

```bash
.venv/bin/python -m eval.realtime_versus_ui \
  --policy-a deep_chain_builder --policy-b random \
  --deep-chain-profile reference --deep-chain-backend native \
  --deep-chain-target-chain 7 --seed 187 --speed 4
```

1・7・19で表示のtargetとplan objective、replayの値が一致することを確認する。
0・20・非整数はエラー。目標値を上げても到達を保証しない。
通常windowの目視確認とdummy SDLの自動確認は区別する。

## 検証コマンド

```bash
.venv/bin/python -m unittest tests.test_deep_chain_target_ablation tests.test_launcher \
  tests.test_realtime_versus_ui tests.test_deep_chain_builder_benchmark tests.test_deep_chain_builder
.venv/bin/python -m eval.deep_chain_target_ablation verify
```
