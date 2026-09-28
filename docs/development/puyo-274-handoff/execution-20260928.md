# 2026-09-28 ノート PC 作業の引継ぎ実行表

ユーザーの今回の指示により，ローカル資料の commit 除外指定を置き換え，必要な資料を Git へ保存する．runtime 挙動の追加修正・GUI 占有・native build・正式 G2 は今回実施しない．新しい親セッションは Jira と GitHub の現在値を再確認する．

## 担当と排他資源

| Jira／作業 | worktree／branch | 起点 SHA／PR base | 担当範囲・契約 | モデル／推論／理由 | 排他資源・状態 |
| --- | --- | --- | --- | --- | --- |
| PUYO-274 資料移送 | `/home/sion2/workspaces/puyo-nextgen-20260928/274`／`PUYO-274/desktop-handoff` | `d3acb77d9542e66209618deb36eaae6d2bcaca9d`／`PUYO-266/gtr-asset-quality` | 子 `/root/handoff_audit`：AGENTS.md，ローカル依頼メモ，desktop handoff，旧実行表の保存，artifact inventory | `gpt-6-sol`／`medium`．移送範囲が限定され，runtime 設計変更がない | 親の manifest と本表は編集しない．build／GUI／benchmark を使用しない |
| PR／Jira／stack 整備 | 親 `/root` | 既存 stack #167，最下段 `integration/puyo-228-v1-8-0` | Jira 本文・コメント，review-manifest.json，層の祖先・差分確認，ready，gh-stack | 親の既存実行記録は `gpt-6-sol`／`high`．短い確認は親が実施 | 子の docs commit 中に同じ worktree を操作しない．merge／release／reviewer 指定なし |
| CI 失敗の読み取り | 子 `/root/puyo273`，元 worktree 273 | #164／#168／#169 の既存失敗 | CI log の原因と対策案のみ．子はコード・Jira を変更しない．親が実行契約を変えない lint 2 件を別 commit で修正 | 既存子の `gpt-6-astra`／`high` を継承．native/CI/PR 依存を確認 | GUI・CPU 実測・native build は使用しない |

統合ブランチの確認 SHA は `c0c77d944ff3ed276a4b44a40a779cd72bc0977a`．9 層すべての base SHA が head の祖先であることを確認．既存最上段 #170 は `d3acb77d9542e66209618deb36eaae6d2bcaca9d`．PR 一覧・変更ファイル・check は `review-manifest.json` の開始時 snapshot を参照し，最新値は GitHub から読み直す．

## 今回の人間 QA

- PUYO-264：OK．過去の速度差の仮説と source 別の機械診断は保持．
- PUYO-268：OK．元 seed123 GUI replay は未保存で，新規機械再現との同一性は未確定．
- PUYO-266：連鎖品質は暫定許容．ただし `--policy-a nextgen_tactic_manager --policy-b random --seed 59 --nextgen-templates daa --seed-a 55 --seed-b 59` の終盤で，上部に達しても生存用の小消しをせず自滅を目視．元 raw／速度／source/config SHA は未提供・未確認．旧 GTR seed55 の窒息とは別条件．
- PUYO-273：目視上，配置は特に問題なし．N=3 の future placement preview を `o` で表示する機能が見えないとの別報告．現在組の落下地点 ghost とは別．原因／回帰した層は未確定．
- PUYO-269：ノート PC で若干改善したがカクつきが残る．hardware 原因は未検証の仮説．人間の意図した配置と入力正確性の formal QA を代替しない．

264／268 の人間 QA は OK と記録する．既存 A/C に残る正式比較をこの報告だけで満たした扱いにせず，264／266／268／269／273 は In Progress を保持する．273 の frame/input gate も未達．PR をレビュー可能にすることと，Jira COMPLETE／G2 PASS は別の判断である．

## 今回と次回の境界

今回：引継ぎ資料の commit/push，差分・既存検証の確認，PR の ready，stack の読取・整理．既存ベンチマーク raw は tracked で，重複した `/tmp` raw を追加しない．元 root の未コミットファイルと `/tmp` の原本は消去しない．

次回：PUYO-274 を再開窓口として，窒息は PUYO-266 の品質責務，cadence は PUYO-269／273 の既存 gate を維持．先読み preview は原因調査後に編集所有先を確定．親が子と worktree，共有 API，CPU／GUI 排他を管理する．正式 G2 30 seed × 2 repeat と残る品質条件は PUYO-266 が所有し，quality FAIL／G2 BLOCKED の履歴を保持する．PUYO-256〜258 の本学習は開始しない．

## 移送対象の制約

root の `runs/` と `human_datasets/` は独立した歴史的実行／ユーザーデータで，cache とは異なる．今回の残課題を再開する必須証跡とは確認できず，PR に一括追加せず原本を保持する．詳細の対象・サイズ・代替参照は子の artifact inventory を参照．仮想環境・native binary・Rust target・秘密情報／Codex 設定と rollout は commit 対象外で，desktop で環境を作り直す．

## References

- [PUYO-274](https://shhchan.atlassian.net/browse/PUYO-274)
- [自律開発手順](../codex_autonomous_workflow.md)
- `execution-20260925.md`，`execution-20260927.md` は旧 host の原記録．旧絶対パスを desktop 上の存在保証として使わない．
- `../puyo-274-desktop-handoff.md` の再開手順を最新の入口にする．
