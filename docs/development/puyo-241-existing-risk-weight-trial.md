# PUYO-241 既存危険度重みの単独試行

## 結論: No-Go、既定設定を維持

既存 `danger_ratio` 減点だけを2倍にした単一条件は採用しない。
実10連鎖達成は3/8 seedから6/8へ増えたが、既存成功seed138/151が退行し、
40手全体の平均時間も20.643秒から23.428秒へ13.49%増えた。
共用YAML・本番Python/Rust・探索予算・ABIは変更していない。追加重み条件は試さず、
このPRは独立試行とNo-Go証跡を保存する。PUYO-236の最終60runやGUI再QAは実施していない。

| 指標 | 基準版 | danger2 |
| --- | ---: | ---: |
| 実発火10以上（独立seed） | 3/8 | 6/8 |
| 最大実連鎖の平均（独立seed） | 4.375 | 9.25 |
| premature発火数（両repeat合計） | 4 | 6 |
| game over数（両repeat合計） | 4 | 0 |
| 未発火seed | 3/8 | 0/8 |
| 完全評価run / decision数 | 16 / 638 | 16 / 640 |

両repeatのaction・plan・trajectory・全root request receiptsが一致し、以下の実発火は
2回とも再現した。`なし`は未発火。seed133基準版は39手で正当なgame overとなる。

| seed | 基準版の実発火 | danger2の実発火 | 個別結果 | 全体時間変化 repeat1 / 2 |
| --- | --- | --- | --- | ---: |
| 123 | 39手目10連鎖 | 30手目10連鎖 | 成功維持、発火が早まる | +30.11% / +24.34% |
| 126 | 40手目2連鎖 | 36手目10連鎖 | premature解消 | +14.25% / +16.13% |
| 130 | 40手目3連鎖 | 35手目11連鎖 | premature解消 | +13.67% / +13.67% |
| 133 | なし、game over | 40手目7連鎖 | game over解消、premature追加、target10未達 | +9.59% / +7.99% |
| 135 | なし | 32手目10連鎖 | 未発火解消 | +30.99% / +30.94% |
| 138 | 34手目10連鎖 | 35手目12連鎖、37手目1連鎖 | 既存成功へpremature追加 | −4.34% / −3.50% |
| 147 | なし、game over | 31手目12連鎖 | game over解消 | +42.26% / +41.71% |
| 151 | 36手目10連鎖 | 40手目2連鎖 | 既存成功がtarget10未達へ退行 | −13.38% / −13.04% |

seed138は最大値だけなら10→12だが、追加1連鎖が`premature_fire`となる。
seed151は最大10→2で明確に悪化する。平均値・達成seed数の改善でこれらを相殺せず、
小連鎖での生存をtarget10成功として数えない。

## 時間・実計算量

| 指標 | 基準版 | danger2 |
| --- | ---: | ---: |
| decision p50（秒） | 0.548680 | 0.585914 |
| decision p95（秒） | 0.702194 | 0.712275 |
| trajectory平均（秒、各16run） | 20.642783 | 23.428270 |
| trajectory p50 / p95（秒） | 19.607532 / 24.845714 | 23.810028 / 26.876536 |
| native search p50 / p95（秒） | 0.317424 / 0.451803 | 0.345247 / 0.444673 |
| materialization p50 / p95（秒） | 0.154835 / 0.208859 | 0.166731 / 0.216811 |
| Python flow p50 / p95（秒） | 0.059395 / 0.078489 | 0.061923 / 0.083309 |
| peak RSS（KiB、最大） | 170,804 | 170,804 |
| 実expanded nodes（全16run合計） | 209,126,138 | 234,704,674 |
| 実evaluated nodes（全16run合計） | 193,467,044 | 222,437,928 |

p95は両群とも1秒未満だが、時間非悪化の条件は満たさない。seed123/126/130/133/135/147で
両repeatともtrajectory時間が増え、expanded/evaluated nodesも増える。
node capは600,000のままで、観測された1decisionの最大expandedは両群465,036。
1秒までの余裕を追加計算の許可には使っていない。

seed123の増加を同じ手数窓で分けると、31〜40手目のdecision時間は
1.005 / 1.092秒から6.087 / 6.288秒へ増える。同区間のexpandedは675,216から4,428,416、
evaluatedは585,949から4,394,687へ増える。30手目までのdecision時間は
15.562 / 16.977秒に対して15.754 / 16.486秒であり、このseedの差の多くは
早い発火後のtrajectory変化に伴う実探索量増加で説明できる。

固定盤面は各群4盤面×3 processes×2 calls。cold/warm/privateの初期盤面確認は別に保存する。

| 固定盤面 | 平均秒 基準版 → danger2 | 比 | expanded 基準版 → danger2 |
| --- | ---: | ---: | ---: |
| seed123 / 30手目 | 0.289162 → 0.271437 | 0.93870 | 164,647 → 164,621 |
| seed126 / 30手目 | 0.209729 → 0.221325 | 1.05529 | 163,700 → 164,276 |
| seed130 / 25手目 | 0.345009 → 0.357940 | 1.03748 | 341,829 → 341,673 |
| seed133 / 25手目 | 0.392524 → 0.386224 | 0.98395 | 350,474 → 350,366 |

固定盤面では小さな増減が混在し、この少数反復だけで一般的な時間非悪化は保証しない。
各盤面のp50/p95、native search/materialization、process別平均、node数、全root順位は
`comparison.json` と `fixed-*.json` に保存した。閉ループの大きな反復安定の増加と
既存成功seedの退行があるため、追加測定で採用扱いへ変更する理由はない。

## QA・証跡

関連37 tests成功、最終のnative未導入環境向けskip整備後に試行固有3 testsも再成功。
調整重みでの全fixture Python/native評価一致、fatal floor不変、実native requestへの設定伝播を確認した。
32runは全件完全評価。全1,278 decisionで既存の厳密全root materializerを通り、
candidate/plan整合、完全状態parity、scenario accounting、実効evaluator checksumを検証した。
parity mismatch / fallback / accounting failure / forced-safety選択は0。
16組のrepeat action/plan/trajectory・全root receiptsが一致し、private counterfactualと
8seedのfuture isolation auditも成功した。固定盤面のnative/Python digestとnodesは群内反復で一致した。

証跡は `docs/benchmarks/puyo-241-existing-risk-weight-trial/`。
圧縮archiveには32run raw、2manifest、固定盤面6ファイル、request入力、事前診断、比較結果、test logを保存する。
実行hostはIntel Core Ultra 7 258V / 8 logical CPUs、WSL2 Linux、CPython3.12.3、native scenario-6。
runtimeのsource/buildとrunner/configを分離し、native sourceやwheelを変更していない。

人間はこの表とseed138/151のrawの実発火・selected classを照合し、
seed123の時間窓・実nodes、全件のchecksum/再集計結果を確認できる。
No-Goのため本番への適用・GUIでの変更後QA・PUYO-236の最終60runは行わない。
実発火10以上、平均最大実連鎖≥10、premature/game over等0、p95≤1秒の最終gateは維持する。

## 事前固定（2026-09-09）

測定する条件は既定値と `danger2` の2群だけとする。候補は
`danger_ratio=-20000 → -40000` の1特徴1条件で、識別用のweight version以外は
評価器設定を変えない。`hidden_row_puyo=-1500`、potential chain reward、fatal判定、
quiescence、発火class、root/scenario集約は維持する。

共有YAMLは変更せず、既存 `DeepChainBuildFlow` / `RunLongRangeSearchStep` の
`evaluator_config` に試行スクリプトから注入する。本番コード変更はない。
source/buildは両群ともclean commit `73ab4e8ce066555042f1a20e1b3b59be3a2a8968`、
release wheel SHA256 `6c5dde345ec65c6ce97f5b7a17a575a9060b4cfb260a9da383d72a6456a8fde6`。
runner Git commit、effective config全項目、semantic checksumを別々に記録し、
全decisionのrequest・backend diagnosticsのevaluator checksumも一致確認する。

事前診断は保存済み基準版の319盤面と6,315即時合法子盤面を再評価した。
hidden rowを持つ48盤面と858子盤面は全件fatal floorに達しており、その観測範囲では
hidden row重みはtotalに影響しない。深い全beamでも無効とは一般化しない。
seed126の30手目は潜在連鎖加点497,785に対して危険度減点−16,166.7、
seed130の25手目は497,540に対して−15,000。fatal前の順位へ作用する可能性を
単一の2倍条件で検証する。保存済み枝刈り件数とquota coverageも参照するが、
捨てられた深い候補の個別スコアは記録されておらず、原因を確定する証拠ではない。

target10 / safe-no-threat / reference / native scenario-6 / depth16 / width250 /
max expanded nodes600,000。seed123/126/130/133/135/138/147/151 ×2 repeatsを
40 placementsまたは正当なgame overまで、同じhostで基準版・候補版を交互に直列実行する。
各identityは新規process。repeat2は決定論確認であり独立seedには数えない。
同一盤面反復はseed123の30手目、126の30手目、130の25手目、133の25手目で
各群3 processes ×2 calls。初期盤面のcold/warm/private counterfactualも確認する。

全root/native-Python順位・候補/plan整合、完全状態parity、scenario accounting、
repeat action/plan/trajectory digest、fallback/private leakage 0を維持する。
実発火10以上・平均最大実連鎖・premature・game over・未発火・既存成功seedを分け、
decision p50/p95・phase・trajectory全体時間・RSS・実nodesを比較する。
品質または再現性ある時間悪化はNo-Go。p95≤1秒だけでは採用しない。
最大3条件は上限であり、追加試行は必須にしない。PUYO-236の最終60runとGUI QAは別途であり、
局所試行のGoをbaseline合格として扱わない。

## 診断の読み方

手数は1始まり。以下は変更前の実trajectory上の盤面であり、将来の全beam候補を
再現した比較ではない。potentialの大きさだけで重みの誤りを断定せず、再探索で判断する。

| seed / 手数 | 潜在連鎖加点 | danger減点 | hidden減点 | 盤面totalのfatal | 選択rootのfatal survivor数 |
| --- | ---: | ---: | ---: | --- | ---: |
| 126 / 30 | 497,785 | −16,166.7 | 0 | なし | 0/6 |
| 126 / 35 | 589,186 | −17,083.3 | 0 | なし | 6/6 |
| 126 / 37 | 0 | −17,666.7 | −3,000 | −1e12 | 6/6 |
| 130 / 25 | 497,540 | −15,000 | 0 | なし | 0/6 |
| 133 / 25 | 361,574 | −14,666.7 | 0 | なし | 0/6 |
| 133 / 30 | 0 | −17,666.7 | −1,500 | −1e12 | 6/6 |
| 147 / 35 | 45,002 | −17,083.3 | 0 | なし | 6/6 |

seed126の30手目は80,036 nodesが枝刈りされ、35手目は7,054、37手目は0まで減る。
seed133の25手目は188,029、30手目は23,973。探索候補の枯渇は保存済み
`search_counters` とscenario別 `survivor_coverage` に残るが、これだけで「危険度重みが
安全な候補を落とした」とは言えない。`survivor_evaluator_score` は履歴上の生存スコアであり、
全depthで安全に継続できる保証にも用いない。fatal判定後は重みの増減に関係なくtotalが−1e12になる。

## 設定・buildの由来

共通search/backend/evaluator YAMLのfile checksumと、注入するevaluator全項目のsemantic
checksumをmanifestで別記する。各閉ループdecisionは既存backend diagnosticsとrequest receiptで
実効evaluator checksumを検証する。`NativeDecisionRequest.config_digest` は既存backendの
`request.search_config_sha256` に由来し、evaluator checksumではない。固定盤面では検索設定を
保つためこの値を保持し、元request集合の`inputs_sha256`と、注入後の
`evaluator_config_sha256`を別々に保存・検証する。

試行runnerはcommit `1eadf7bf9e00f1ccade13acfe730b29724888860` に固定した。
後続の証跡・報告commitは実行されたソースやwheelのrevisionを変更しない。
Pythonの依存関係追加やnative wheelの再build・差替えはしていない。

再実行には指定した基準commitのclean checkoutと同じrelease wheelが必要。
`PYTHONPATH`をそのcheckoutに向け、そこにあるPythonから試行scriptを起動する。
出力先は未使用の場所にする。`puyo241_run_all.py`は保存済み全root診断から
事前固定した4盤面のrequestを抽出する。診断の元ファイル名とSHA256は証跡に保存する。

```bash
PYTHONPATH=/path/to/clean-baseline /path/to/clean-baseline/.venv/bin/python \
  /path/to/trial/eval/puyo241_run_all.py \
  /path/to/clean-baseline /path/to/all-root-diagnostics /path/to/new-evidence
PYTHONPATH=/path/to/clean-baseline /path/to/clean-baseline/.venv/bin/python \
  /path/to/trial/eval/puyo241_summarize.py /path/to/new-evidence
```

共有YAMLを変更するコマンドは不要。全root比較は既存の厳密materializerを通り、
全runにranked root actionsとdigest・request receiptsを残す。完了後の再集計は測定hostでなくても行える。

## References

- https://shhchan.atlassian.net/browse/PUYO-241
- https://shhchan.atlassian.net/browse/PUYO-233
- https://shhchan.atlassian.net/browse/PUYO-236
