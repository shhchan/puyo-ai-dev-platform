# PUYO-236 safe-build target10 品質・性能評価

品質の分母は事前指定の repeat 1（最大30固有seed）。repeat 2 は決定論検証用。
欠損・未完了を0やPASSに補完しない。全条件で品質基準10、p95上限1.0秒。

| target | 完全評価run/60 | 固有seed | 最大実連鎖平均 | 10以上/固有seed | premature（2 repeats） | game over（2 repeats） | p95秒 | 品質 | 性能 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 6 | 6 | 2.5 | 1/6 | 2 | 2 | 0.762711 | FAIL / 未完了 | FAIL / 未完了 |

詳細は条件別 summary.json、比較は paired_comparison.json、raw は target-NN/*.json.gz。
same_root_diagnostic は同一初期盤面の cold/warm 診断。trajectory の phase/RSS/node 分布とは分離する。
forecast_followthrough は元planの予測最大と、再計画を伴う同じplacement区間の実発火最大との差。horizon_complete=false は観測打切り。
欠損は条件別 coverage の reason を参照。GUIの通常画面の目視確認は別途人間が行う。
