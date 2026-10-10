# Reference 公開 action mask 回帰

PUYO-266 formal v2 の pattern2259／reference 両 repeat が 35 手後に同じ illegal action 例外となった．測定 HEAD は `7fa83c3274f9ed4fc465f4708a6c465c6e503f8b`．元の progress は同 worktree の `runs/puyo-266-formal-v2/` に保全する．

保存済み progress の全 tick/hash を policy 無実行で再生し，tick1789 の公開盤面・履歴推定・到達 mask を復元した．同じ depth16／width250／6 scenarios／quota600000 の 1 decision で，修正前は action19，修正後は公開 mask 内の action9 を選択した．全探索順位は不変．修正後の action9 だけを実 controller に渡し，lock の一致と解決までの 525 tick を再生検証した．11 連鎖を確認したが，この targeted 結果は正式品質 PASS を意味しない．

- `reference-2259-before-fix.json`: 元順位・誤選択・公開到達 action・profile．
- `reference-2259-after-fix.json`: 修正選択・同じ順位・実 lock/resolution・全追加 tick の入力と state hash．
- `tests/fixtures/nextgen_single_reference_2259_public.json`: 公開 state と推定，公開 mask の回帰 fixture．非公開 future/provider を policy へ渡さない．

選択 contract の runtime input 依存と，候補全不許可時の fail closed を専用 unit で確認する．
