# PUYO-240 弱いscenario支持の順位調整試行

## 判断

No-Go。実10連鎖以上の達成は3/8→5/8、最大実連鎖平均は4.375→6.75へ改善した。
一方、40手全体の平均時間は20.578→21.689秒（+5.40%）。改善したseed130は両repeatで
17.24% / 19.91%、seed147は25.54% / 26.81%遅くなった。
追加node予算は与えていないが、変更後の軌跡で実探索量が増え、
ユーザーの「今より計算時間を悪化させない」条件を満たさない。
このPRは不採用の実験記録として保存し、merge・既定採用しない。

seed135の全40手では、安全条件を満たす継続代替を証明できず、
閾値2/6の試行は順位変更を適用しなかった。actionと実結果は現行版と一致した。
同seedの閾値3/6オフライン診断でも選択actionは変わらない。
この診断を3/6の閉ループ品質評価とは扱わず、3/6の実装・閉ループ試行は追加しない。

## 試行条件と変更範囲

比較元はPUYO-232/237/238統合済みの
`73ab4e8ce066555042f1a20e1b3b59be3a2a8968`。
候補の測定commitは`2e1fe2b`（実装`4576b46`＋固定evidence digest更新）。
両者を独立したclean checkout・venv・release wheelに固定した。
baseline wheel SHA-256は`6c5dde345ec65c6ce97f5b7a17a575a9060b4cfb260a9da383d72a6456a8fde6`、
候補は`e4956ac115bea4bd01e404f514e9baeca631eb3c2cc32def03e0adad3f381571`。

target10 / safe-no-threat / reference / native scenario-6 / depth16 /
width250 / scenarios6 / node cap600,000を固定した。evaluator設定、quiescence、
node budget、elapsed timeoutを使わない契約は変更していない。
seed123/126/130/133/135/138/147/151×2 repeatsを各40 placementsまたは正当なgame overまで、
同じhost上でbaseline→候補の順に交互・直列で実行する。
repeat2は決定論の確認であり、独立seed数は8のまま。

試行は支持数の最小値2/6に固定し、target支持1/6を対象とする。
全rootが6scenario評価済み、safe_build、record_and_stopの場合だけ検討する。
winningまたはforced-safetyがある場合は全順位を維持する。
有効なquiet代替は、全6scenarioで次をすべて満たすrootとした。

- 評価済み・探索完了・selected classがquietでquiet survivorがある。
- survivor評価値が有限でfatal floorより大きい。
- その評価値の根拠となったbest survivor自体が深さ16にある。
- reached depthも16で、深さ16のretained countが1以上ある。

代替がある場合に限り、weak targetのclass比較値を2.25、有効quietを2.5とする。
他のclass優先値とcoverage/support/value/dispersion/actionの厳密tie-breakは維持する。
非適用時はv2キーの値も変えず、適用時だけranking v3と試行ruleタグを返す。
火の分類、代表候補、planの生成処理は変更していない。

`quiet_support`はselected classの件数であり、生存保証には使用しない。
`survivor_evaluator_score`は過去深さを含むbest survivorの値なので、既に保持している
path長だけを`survivor_evaluator_depth`として追加伝達した。新しい評価・探索・特徴抽出はない。
root evidenceのrecord framingだけをv2へ上げ、既存u16予約領域を深さに使用する。
外側ABI/要求形式は据置。capabilitiesのresult schema digestを更新し、旧extensionと新adapterの
組合せを拒否する。新digestは次のUTF-8文字列のSHA-256。

```
puyo.native_long_horizon_records.v2:root-evidence-coverage-reserved-u16=survivor_evaluator_depth;guard-ranking-v3-min2of6
```

## 全root診断

過去rawの未選択rootを推測せず、現行releaseで8seedの全319decision・全rootを新規取得した。
厳密materializerを通過したnative順位、各rootの全scenario evidence、代表深さ、未加工requestを保存した。
この取得時の時間は性能比較の分母から除外した。

実際に選択が変わる競合はseed130/147の8手目で再現した。変更前までのactionと
そのdecisionのrequestはbyte単位で同じで、全rootの集約値・node counterも完全一致する。
seed130はtarget支持1/6のroot2から、全6scenarioで深さ16の非fatal継続を持つquiet root18へ、
seed147は同じくtarget支持1/6のroot15から有効quiet root0へ切り替わる。
その後の独立閉ループで、各々34手目・32手目に実11連鎖を発火した。
候補側はseed130/135/147の全120decisionを追加取得し、全rootの順位・安全根拠を残した。

seed135では、11〜12手目にweak targetとquiet rootが競合する。
深さ16のretained countと非fatalなbest値だけを見るとroot19を代替と誤認し得る。
しかし、11手目のroot19のbest survivor深さは`[15,16,10,15,10,12]`、
12手目は`[15,15,16,15,16,16]`であり、全scenarioの最終深さの非fatal値を証明できない。
36〜37手目のtarget支持1/6でも、条件を満たす代替を確認できなかった。

これは「探索集合に安全な継続候補が存在しない」という証明ではない。
保存するbest評価値と最終深さの生存記録が異なる候補を指し得るため、
今回の小規模な既存evidenceによる安全条件では採用を正当化できないという結果である。
最終深さの新しい評価集約や生存条件緩和へ試行を拡張しない。

`all-root-diagnosis.json`は11/12手目だけでなく、崩れ始めから終盤までの比較項目、
各scenarioの不成立理由、事前固定した2/6・3/6のオフライン順位を再計算可能な形で残す。
支持6/6でも失敗するseed126と、成功seed123/138/151も同じ測定条件に含める。

## 閉ループ・性能・検証

全32runが完了し、各版638decision。両repeatの実結果は一致した。

| 指標 | 現行 | 候補2/6 |
| --- | ---: | ---: |
| 実10連鎖以上のseed数 | 3/8 | 5/8 |
| 最大実連鎖平均（repeat1） | 4.375 | 6.750 |
| premature（両repeat） | 4 | 2 |
| game over（両repeat） | 4 | 2 |
| 未発火seed数（repeat1） | 3 | 2 |
| decision p50 / p95秒 | 0.546535 / 0.695302 | 0.562599 / 0.699104 |
| 40手全体の平均秒 | 20.577655 | 21.688632 |
| 最大RSS KiB | 167,480 | 166,984 |
| 実expanded nodes合計 | 209,126,138 | 220,892,004 |
| 実evaluated nodes合計 | 193,467,044 | 205,947,448 |

seed別の最大実連鎖は123:10→10、126:2→2、130:3→11、133:0→0、
135:0→0、138:10→10、147:0→11、151:10→10。既存成功seedの悪化はない。
seed130のprematureがなくなり、seed147のgame overがなくなった。残る未達も欠損補完せず保持した。

| 改善seed | 40手時間 repeat1秒 | repeat2秒 | 実expanded nodes（各repeat） |
| --- | --- | --- | --- |
| 130 | 19.494→22.854（+17.24%） | 19.385→23.243（+19.91%） | 12,480,545→14,821,856 |
| 147 | 19.196→24.099（+25.54%） | 19.043→24.148（+26.81%） | 12,559,213→16,100,835 |

固定したseed130・8手目のrequestを各版6fresh processes×cold/warmで反復した。
全12samplesでroot順位のPython/native一致を確認し、各版内のdigestは一致。
現行action2→候補action18を再現したが、探索counterは各版完全一致した。
backend＋materialization平均は0.613094→0.608036秒で、比較関数自体の明確な時間増は観測しなかった。
初期盤面123/135とseed135の11/12手目requestも各版3processで反復し、
固定盤面の揺らぎと、異なる盤面が続く閉ループの時間差を分けた。
固定盤面が同等でも、閉ループ時間の再現性ある増加を容認する根拠にはしない。

phaseのp95秒はnative search 0.453214→0.448213、aggregation 0.000226→0.000228、
serialization 0.001486→0.001383、Python materialization 0.212561→0.213320。
各phaseのp50/p95/平均・全体時間・RSSは`comparison.json`とrawに保持する。
全体p95は両者1秒以下だが、これだけで時間非悪化や採用の合格にはしない。

全1,276decisionで全root厳密順位検証と完全状態parityが通り、fallback / scenario accounting異常は0。
各版8組のaction・plan・trajectory repeat digestが一致した。
8seedのprivate sentinel監査と実policy counterfactualも通過した。
最大expanded nodesは両者465,036でcap600,000以下。各decisionのscenario jobsは6、rerunは0。

focused Pythonは71対象中、追加evidenceによる固定digestの期待値1件を更新し、その1件の再実行で成功した。
残る70件は同じ探索実装で成功。新境界テストではfatal・過去深さ・未評価・代替なし・winning/forced保護を検証し、
nativeの不正survivor depthを拒否する回帰テストも追加した。
Rustは48 tests成功（既定ignored 2件）、fmt / clippy / 変更Pythonのruffが成功した。
`summarize.py`はmanifest/identity/raw/品質集計/予算/全状態parity/scenario accounting/repeat digestと
固定requestを再検証し、入力ファイルchecksumを記録する。

## 再現と証跡

`docs/benchmarks/puyo-240-weak-scenario-support/`に全raw・manifest・比較JSON・実行scriptを保存する。
測定中の原本は`/home/sion2/workspaces/puyo-small-quality-20260909/puyo-240-evidence/`。
測定commitから独立したclean checkoutとrelease wheelを作り、そのcheckoutをcwdとして
`PYTHONPATH=.`を設定して`trial.py worker <新規出力先> --seed 123 --repeat 1`を実行する。
各manifestはcommit/build/host/configを固定し、不一致のresumeと既存run上書きを拒否する。
`trial.py fixed`は初期盤面123/135と保存済みseed135の11/12手目requestを反復する。
`analyze_roots.py`と`summarize.py`はrawから診断・比較を再計算する。

通常GUIの人間確認、PUYO-236の30seeds×2 repeats、release、masterへの統合は実施していない。
局所試行の結果をquality floor10、平均最大実連鎖10以上、premature/game over等0、
p95 1秒以下という最終gateの合格へ読み替えない。

## References

- [PUYO-240](https://shhchan.atlassian.net/browse/PUYO-240)
- [PUYO-233](https://shhchan.atlassian.net/browse/PUYO-233)
- [PUYO-236](https://shhchan.atlassian.net/browse/PUYO-236)
- [PUYO-237](puyo-237-aggregate-ranking.md) / [PUYO-238](puyo-238-evaluator-candidate-parity.md)
