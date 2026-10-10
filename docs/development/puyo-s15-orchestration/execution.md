# Sprint 15 自律開発記録

対象は Sprint 15 (id 141) の PUYO-266／274／275／276／277／278 全 6 件．PR 作成・統合検証までを委任範囲とし，merge／tag／本学習は含めない．Sprint 14 stack は全件 merge 済み，起点は `7757f2312f34a1d79593f43ebd4f41921c61f0a6`．master は `3defcc2` (v1.7.2)．既存 worktree と変更は保持する．親記録はこのファイルと jira/ の受入条件 snapshot．

| Jira | 依存／待機条件 | worktree／branch | 起点／PR base | 担当範囲 | モデル／推論／理由 | 排他資源 | 子／状態／PR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 275 | なし | /home/sion2000114/workspaces/dev/puyo-s15-275 / PUYO-275/esports-tsumo-provider | 7757f23 / integration は 278 監査後確定 | 配ぷよ provider，core/env/CLI/開始設定，関連テスト | gpt-6-sol / medium / 明確な provider 追加 | launcher/UI の編集を 276 に先行，native build は要調整 | 起動準備 |
| 276 | 275 の GUI/CLI 編集完了後に実装 | /home/sion2000114/workspaces/dev/puyo-s15-276 / PUYO-276/automatic-qa-replay | 7757f23，実装時に 275 head へ更新 / 275 branch | QA 保存，launcher/UI/manifest/validator，関連テスト | gpt-6-sol / medium / 既存 replay の保存導線 | 当初は読取調査のみ，GUI 計測は排他 | 起動準備 |
| 266 | 275 の受入達成．当初は読取設計のみ | /home/sion2000114/workspaces/dev/puyo-s15-266 / PUYO-266/sprint15-single-quality | 調査用 7757f23，依存実装前に検証済み先行 head を親が取り込む / stack の先行 branch | 単独 gate，nextgen 構築品質 | gpt-6-astra / high / 探索・品質原因調査 | 重い探索と GUI/native build は排他 | /root/puyo266，読取設計 |
| 277 | 275 達成，266 と契約編集を直列化 | 専用 worktree を先行 head 確定後作成 | 未確定 / stack の先行 branch | 対戦 gate，response/selector | gpt-6-astra / high / deadline と採用整合 | 266 と shared_search/tactic_manager の編集は排他 | 待機 |
| 274 | 266／276／277 の結果 | /home/sion2000114/workspaces/dev/puyo-s15-274 / PUYO-274/sprint15-closeout | 7757f23，実装時に全先行 head を取り込む / 先行 branch | 親実行表，統合・人間 QA 手順，完了境界 | 継承モデル / 継承推論 / 親統合，後で子に専用範囲委任 | 全体記録は親所有，GUI QA は排他 | 親準備中 |
| 278 | release 実行は 274 達成後，監査は先行 | /home/sion2000114/workspaces/dev/puyo-s15-278 / PUYO-278/v173-release-audit | 7757f23 / 最終先行 branch | v1.7.3 release 監査 docs のみ | gpt-6-sol / medium / 履歴と文書監査 | branch 作成/共有設定は親のみ | 起動準備 |

host: Intel i7-14700F，28 logical CPU，15 GiB RAM (約 12 GiB available)．最大 3 子だが GUI/重い探索/native build は同時実行しない．子は再委任しない．各子が自分の Jira 更新を所有し，親はコメントを重複しない．人間 QA は実コマンド，操作，期待結果，証跡保存先を具体的に示す．

## 決定と初期検証

- 278 の初期監査を受け，親が `integration/puyo-228-v1-7-3` を `7757f2312f34a1d79593f43ebd4f41921c61f0a6` から作成・push・remote SHA read-back．275 の PR base はこの新統合先．旧 v1-8-0 は保持して使用停止，release 後の master から後続 version を計画する．
- 子 ID: `/root/puyo275`，`/root/puyo276`，`/root/puyo278`．275 は provider 実装と source 調査，276 は独立した新規保存 module/tests/docs のみ先行（既存 UI ファイルは 275 編集後），278 は release 監査 docs．
- 278 は初期監査 docs と 217 commit 一覧を `34f54c21839ea207ee9f53c7ef8ada0c2c3933b4` まで commit，clean のまま停止．release gate 未達で PR/COMPLETE/セッションコメントは保留，後で結果を渡して再開する．空いた枠で 266 の読取調査・設計だけ開始した．依存成果の受入前には正式実装・評価を開始しない．
- native を含む既存環境 `/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python` を read-only 利用可能．`NativeDeepChainBackend()` が成功し，native source の `6e2c140..7757f23` 差分は空．共有 install は禁止する．
- 起点 7757f23 で上記 Python から `-m unittest tests.test_nextgen_response_search tests.test_nextgen_adoption_replay tests.test_realtime_replay -q` を実行し，20 tests PASS (5.632 s)．
- 266 の新 provider 結果を見る前に `single-cohort-preregistration.json` へ 30 個の logical pattern ID と 2 repeat・閾値を固定．dataset identity と正確な source/config/native は 275 検証後の正式 init で固定し，正式評価前に変更しない．
- 274 の 2026-10-10 Jira 追補が旧 G2／cadence 条件に優先．Sprint 14 の人間操作・preview は受理済み，厳密 frame 25 ms 安定化は Sprint 16 課題．新 274 は単独／対戦／配ぷよ／replay の統合条件を所有する．
