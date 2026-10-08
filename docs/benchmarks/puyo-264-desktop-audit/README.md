# PUYO-264 デスクトップ受け入れ証跡の監査

**G2 再実測は quality FAIL／G2 BLOCKED，モデル品質 PASS ではない．** 264 は再実測と固有 QA の証拠を確認して COMPLETE にするという親判断に従う．

2026-10-08，起点 `c812b3f8ad257645937fa6140412a6db6be29930`．新しい対局・G2 測定は実行せず，旧証拠の読取と軽量回帰を行った．

| A/C | 根拠 | 判定・限界 |
| --- | --- | --- |
| 通常速度と低速の違い | PR #158，Jira 10715／10764，開発文書の原因説明 | 初期公開入力は一致．通常速度では探索中の相手更新から stale→最短 fallback が発生．旧 raw は notebook 側 `/tmp/puyo-nextgen-20260924/` の参照だけで，Git にない．今回 raw の再検証はしていない． |
| 無目的な 3 列縦積み解消 | 同 source の 3 条件診断，9/27・9/28 人間 QA | 診断の fallback は全条件 0，最初の 6 採用手は `[2,12,0,14,16,19]`．通常/random の後続は相手/tick が分岐する．人間 QA は OK． |
| GTR・戦術切替 | 現在の GTR fixture，selector 回帰，保存済み ledger | GTR 進捗 0.875→1.0，build_template→build_main の activated receipt を確認．旧診断には cancel の実採用記録がある．fire_main の実採用を下記に補完． |
| 公開境界・14/15・replay | phase／scheduler の軽量回帰 | regression.log と boundary-regression.log．旧 witness 不在 assertion は現在の matcher 契約では成立せず，歴史的制限の要求だけを除去した． |
| 人間 QA・正式 G2 | Jira 10767，今回依頼者報告，PUYO-266 統合 G2 | 人間 QA は確認済み．30×2 の再測定は実施済みだが quality FAIL／G2 BLOCKED，G0/G1/threat/gap 未確認．264 の再実測条件は満たす．品質 PASS と正式 gate の完了判断は266の責務に残す． |

`ledger-audit.json` に保存済み統合 G2 の全 60 raw ファイル SHA-256 と戦術別件数を記録した．2366 選択は build_template 742，build_main 1576，fire_main 48．activated の candidate ID と requested／executed root の一致を照合した．seed 123／repeat 1 の index 30 は action 16 を実採用し，対応する実連鎖は 10．receipt を実 lock と混同せず，lock と最終 hash の整合は元の [統合 G2 検証](../puyo-266-safe-build/integrated-g2-20260927/README.md)を参照する．無脅威 cohort のため cancel／counter の包括的な能力合格とは扱わない．

検証コマンドは `regression.log` の 23 tests（GTR fixture，phase，SelectorTests）と `boundary-regression.log` の 5 tests（receipt/replay，private future，stale 再要求，新脅威再選択，同一組の重複消費）．Python は既存 native 対応環境 `/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python` を使用した．初回は root `.venv` の native 未導入で 1 ERROR，native 対応環境では旧 witness 不在 assertion が 1 FAIL となった．その assertion だけを除き，現在の完成・採用確認を再実行した．native の新規 build/install は行っていない．

参考：[PUYO-264](https://shhchan.atlassian.net/browse/PUYO-264)，[PR #158](https://github.com/shhchan/puyo-ai-dev-platform/pull/158)，[PUYO-268 の定型接続](../../development/puyo-268-template-integration.md)，[PUYO-266](https://shhchan.atlassian.net/browse/PUYO-266)．
