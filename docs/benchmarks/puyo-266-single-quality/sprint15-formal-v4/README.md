# PUYO-266 Sprint 15 正式 v4 評価

2026-10-11 に，事前登録した 30 pattern × 2 repeat × 2 policy の単独環境 120 run を完走し，`finalize` と全 tick の独立 `verify` を実行した．測定ソースは `b5c988140462beed05a3b5d913c72a066a8b6290`．このディレクトリへの証拠保存 commit は測定後に作成したもので，測定条件を変更していない．

| policy | 完了 | repeat1 の平均最大実連鎖 | 不当な小発火 | 小発火 unknown | 死亡 | 窒息 unknown | 判定 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `nextgen_tactic_manager` | 60/60 | 9.6667 | 2 | 8 | 0 | 0 | FAIL |
| `deep_chain_builder` | 60/60 | 9.1 | 0 | 0 | 10 | 10 | FAIL |

小発火と死亡の件数は両 repeat の合計である．nextgen の不当な小発火 2 件は同じ pattern 29377 の repeat1／2 で，再現性のある 1 事例である．nextgen の窒息は，この事前登録単独 cohort では 0/60．これは他の配ぷよや対戦時のおじゃまへの一般的な安全保証ではない．repeat 間の semantic digest 不一致は 0．`failed_workers`，欠損，integrity issue，source 変更はいずれも 0．

受け入れ条件の平均最大実連鎖 10 以上に nextgen は届かず，理由のない小発火も 0 になっていない．nextgen の小発火 unknown 8 件と reference の窒息 unknown 10 件も比較 gate を妨げる．閾値や cohort を測定後に変更せず，単独品質は `FAIL`，G2 は `BLOCKED`，本学習は未許可とする．PUYO-266，その受け入れに依存する PUYO-274／278 は `In Progress` を維持する．

`manifest.json` と `report.json` は正式出力をそのまま保存した．原本配ぷよの SHA256 は `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb`．manifest の semantic SHA256 は `054e2dd9379b667d186c2ea0e2a7f82bcc11684b4fe2f2c639591cb0a74d38a0`，ファイル自体の SHA256 は `2e1093c3e2b6db29855cff785676138793a99f0814ade56838273add9b0e0a19`．report の SHA256 は `f11c56c54fff0d2253502f38bc9377d042467852de30d2297c2dbfd94a7e1632`．`checksums.json` に保存した 11 原物のサイズと SHA256 を記録した．

Git には原因追跡に必要な repeat1 の raw 9 件を選んで保存した．nextgen は低連鎖の 11299／18078／42936 と不当小発火の 29377，reference は窒息した 4519／11299／22598／29377／42936．`report.json` の `artifacts` は全 120 件の hash を持つ．全 raw 120 件（約 276 MB），runner log，終了コードは測定 worktree の `runs/puyo-266-formal-v4/` と `runs/puyo-266-control-v4/` に保持する．Git にない 111 raw の完全再検証には，このローカル原物が必要である．

検証では関連 56 unittest が成功し，両 policy の 3 手 smoke を通した後に正式 120 run を実行した．`finalize` 後の `verify` は policy を再実行せず，全 120 件の tick／state hash と分類を独立再生し，同じ report を得た．具体的な実行コマンドと公開情報・分類の契約は [単独品質 gate](../../../development/puyo-266-sprint15-single-gate.md) に記載した．

次の修正では，pattern 29377 の小発火時点で公開 quiet root がなぜ選択されなかったか，低連鎖 3 pattern の構築／発火判断がなぜ平均を下げたかを調べる．unknown は証明不足のまま成功扱いせず，窒息が出た reference の 5 pattern も別に診断する．修正後は新しい manifest で同じ事前登録 120 identity を再測定する．
