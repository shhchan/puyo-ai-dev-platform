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

## 2026-09-07 実測結果と引き継ぎ

240 identitiesをすべて実行し、226 runを完全評価した。14 runはnative検証器のエラーで未完了。
条件・閾値・候補選択を変更して補完せず、エラーを含むrawを保存した。
全条件に実発火1〜9連鎖があり、baseline採用は不可。PUYO-232の既定target整合と、
品質合格・baseline採用は別の判断となる。

| target | 完全評価 / 予定run | 完全評価した固有seed | 最大実連鎖平均 | 10以上達成 | premature（2 repeats） | game over（2 repeats） | 発火なし（固有seed） | 観測済みp95秒 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 6 | 54 / 60 | 27 | 6.407407 | 1 / 27 (3.70%) | 52 | 0 | 1 | 0.729155 |
| 8 | 58 / 60 | 29 | 9.000000 | 11 / 29 (37.93%) | 36 | 2 | 0 | 0.711893 |
| 10 | 58 / 60 | 29 | 9.000000 | 24 / 29 (82.76%) | 4 | 4 | 3 | 0.739339 |
| 12 | 56 / 60 | 28 | 9.178571 | 21 / 28 (75.00%) | 2 | 0 | 6 | 0.768754 |

品質推定と発火なしは完全評価できたrepeat1だけが分母。最大実連鎖の全run分布も未完了を除外する。
未完了の全軌跡結果はnullで、部分観測の最大値は `observed_maximum_actual_chain` に分けて保存する。
表のp95は観測済みの成功decisionに対する値で、全条件が未完了を含むため正式な性能PASSではない。
エラーdecisionのlatencyは `decision_error_latency_seconds`、各エラーの詳細はcoverageに残す。

全9,082実行actionで完全状態parity mismatch / fallback / 観測済みscenario accounting異常は0。
エラーで証跡のない14 decisionはscenario accountingの未検証件数として残し、PASSにはしない。
完全評価された113 seed/repeat組でaction・plan・trajectory digestが一致した。
エラーで終わる7組も部分digestは一致したが、完全軌跡のdeterminism合格数には含めていない。
全targetで30seedのprivate sentinel境界監査とseed123の実policy counterfactualが一致した。

### 同一seedでのtarget10との差

`paired_comparison.json` の `target10_paired_differences` は、比較する両条件で完全評価できた
repeat1の共通seedだけを使用する。欠損は条件に依存するため、全30seedの推定と同一視しない。

| 比較対象 | 共通seed数 | 除外seed | 最大実連鎖平均の差（10−比較対象） | 10以上達成率の差 | target10が上 / 同じ / 下 |
| --- | --- | --- | --- | --- | --- |
| 6 | 27 | 137, 141, 151 | +2.444444 | +77.78ポイント | 21 / 0 / 6 |
| 8 | 29 | 151 | 0.000000 | +44.83ポイント | 19 / 2 / 8 |
| 12 | 28 | 146, 151 | −0.214286 | +7.14ポイント | 5 / 7 / 16 |

target10はtarget8と最大実連鎖平均が同じだが、10以上達成率が高く、少数の大きな失敗が残る。
target12では発火なしが増える。目標値を大きくするだけで各seedが改善するとはいえない。

### 重点失敗と後続比較

- seed135: target6は34手目に8連鎖、target8は36手目に8連鎖。
  target10/12は40手まで発火なし。target10の36〜37手目はtarget発火を予測するscenarioが1/6、
  残り5/6はquietで、38手目以降は全scenarioがquietになった。
- seed138: 新しい完全状態契約では4条件とも40手を完了し、最大実連鎖6/8/10/12、game overなし。
  旧PUYO-204との間では状態・観測契約とそれに依存する軌跡が変わっているため、旧結果との差を
  target変更だけの効果とは解釈しない。旧artifactは保持している。
- seed126 target10: 26〜36手目のplanは10〜13連鎖を予測したが、40手目の実発火は2連鎖。
  最後の選択rootには全6scenarioでquiet survivorがない。seed130 target10も最大3連鎖。
- seed133: target8は40手目の1連鎖と同時にgame over、target10は39手目に未発火でgame over。
  seed147 target10も未発火でgame over。seed147 target12は1連鎖。

PUYO-232へは、target10の値の整合によって10未満を内部成功とする不一致は解消できる一方、
上記の品質・生存失敗は残ることを引き継ぐ。明示的なGUI targetを既定値で上書きしない。

PUYO-233では、(1) scenario支持数・分位と実発火の対応、(2) 高く積み上がる過程の非発火盤面評価、
(3) 発火タイミング・継続候補の枯渇を優先して比較する。seed135の支持数1/6だけを根拠に
rankingを変更せず、seed126のように支持数6/6でも実現しない場合も閉ループで検証する。
rawには選択rootの詳細を保存しているが、未選択rootの再ランキングには同じ予算で候補集合を再取得する必要がある。

### 未完了の原因と再現証跡

| 後続Task | 条件と発生decision（1始まり） | 未完了run数 | 検証エラー |
| --- | --- | --- | --- |
| [PUYO-237](https://shhchan.atlassian.net/browse/PUYO-237) | target6 seed137: 2、target6 seed141: 1、全target seed151: 1 | 12 | native aggregate ranking differs from Python |
| [PUYO-238](https://shhchan.atlassian.net/browse/PUYO-238) | target12 seed146: 22 | 2 | native fixed-width tie-break selected a different best candidate |

いずれも両repeatで再現。PUYO-237/238はPUYO-184配下の独立Task、High、未割り当て、To Doの
バックログとし、PUYO-231/233にRelatesで結んだ。検証器を無効化する修正は本実験に入れていない。

`error_diagnostics/` は本測定終了後の診断であり、品質・性能の分母には含めない。
seed137/141/151の再現では、Python側の集約値・分散等をnativeと同じ逐次加算で再計算すると
nativeの全root順位に一致した。seed137/151には約1.16e−10の加算結果差があり、同点扱いが変わる。
3ケースとも選択初手は同じだが、全root順位の契約違反として検証器が拒否している。

seed146は、nativeのscalar bestと、候補一覧の同じcanonical signatureに対応する候補で
trigger protectionが2/3と1/3になっていた。候補一覧からPythonが選ぶbestは別のBLUE候補（1/2）。
座標の正規化で同一signatureになる候補の属性保持・選択の整合が原因候補であり、PUYO-238で確定・修正する。

再現時は対象commitのrelease buildを使い、新しい診断出力先を指定する。

```bash
PYTHONPATH=. .venv/bin/python \
  docs/benchmarks/puyo-231-target-ablation/error_diagnostics/reproduce.py \
  /tmp/puyo-231-error-reproduction
```

### 性能内訳と測定条件

| target | native search p95秒 | aggregation p95秒 | serialization p95秒 | Python materialization p95秒 | backend total p95秒 | プロセス最大RSS KiB |
| --- | --- | --- | --- | --- | --- | --- |
| 6 | 0.489905 | 0.000148 | 0.000940 | 0.200607 | 0.654038 | 351804 |
| 8 | 0.474428 | 0.000143 | 0.000746 | 0.201157 | 0.647711 | 351388 |
| 10 | 0.472316 | 0.000181 | 0.000942 | 0.219318 | 0.669684 | 350432 |
| 12 | 0.489667 | 0.000151 | 0.000750 | 0.235605 | 0.703983 | 350420 |

各phaseのp95を足してtotal p95にはしない。decision totalにはbackend外のflow処理も含む。
node上限600,000に対し、観測された1 decisionの最大expanded nodesは全条件465,036。
上表は異なる軌跡を含み、同一盤面のtarget変更コストだけを表すものではない。

seed123の同一初期盤面では、全条件のexpanded nodesが465,036で一致。
cold / warm秒はtarget6: 0.4751 / 0.4589、8: 0.5022 / 0.4769、
10: 0.5050 / 0.4666、12: 0.5183 / 0.4832。
coldはprovenance確認後の最初の探索であり、Python起動・extension import時間は含まない。
trajectoryのdecision計時にはpolicy生成時間も含まない。

本測定のcommitは `195b41415324bb27643c59fbef8d8799d59a6296`。
release wheel SHA-256は `1443deb0ff2bdd86f3b629575f310a7f8abb1bdf1fc6db49d34dcd6e640b0d66`。
元workspaceの未追跡資料がbuild scriptのdirty判定に入るため、同commitの専用clean worktree
`/tmp/puyo-231-release-build` でwheelを作成し、既存venvにinstallしてから比較を開始した。
全runのcommit/build/host/configをmanifestで固定している。測定後の集計処理の更新は
未完了のnull表示・共通seed差分・証跡検証に限定し、測定raw・探索処理は変えていない。
rawの再現は測定commit、今回の集計・verifyは本PRの集計実装を使う。

### GUI QAと検証結果

`gui/gui_qa.json` に全6ケースのコマンド、値の一致、適用decision数、replay checksumを保存した。
native/referenceのtarget1/7/19はそれぞれ9/10/10 decision、Python/smokeはそれぞれ1 decisionを適用。
全ケースでtargetがmetadata、policy、search、backend設定、plan、replayに一致し、fallbackは0。
探索前のreplayにはplanが空の初期診断があるため、初期targetと生成済みplanを区別して検証した。
これらはtarget伝播のQAであり、backend間の品質・速度比較ではない。通常window目視はpending。

focused suiteは98 tests成功。その後のchecksum欠落検出テスト追加を含め、集計の対象テストも成功。
全体・各targetのfinalize/verifyで、rawからの再計算、checksum、manifestとidentityの整合を確認する。
`README.md` とPUYO-203のGUI説明も現在の1〜19に更新した。
