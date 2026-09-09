# PUYO-241 既存危険度重みの単独試行

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

## References

- https://shhchan.atlassian.net/browse/PUYO-241
- https://shhchan.atlassian.net/browse/PUYO-233
- https://shhchan.atlassian.net/browse/PUYO-236
