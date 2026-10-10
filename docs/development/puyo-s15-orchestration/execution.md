# Sprint 15 自律開発記録

対象は Sprint 15 (id 141) の PUYO-266／274／275／276／277／278 全 6 件．PR 作成・統合検証までを委任範囲とし，merge／tag／本学習は含めない．Sprint 14 stack は全件 merge 済み，起点は `7757f2312f34a1d79593f43ebd4f41921c61f0a6`．master は `3defcc2` (v1.7.2)．既存 worktree と変更は保持する．親記録はこのファイルと jira/ の受入条件 snapshot．

| Jira | 依存／待機条件 | worktree／branch | 起点／PR base | 担当範囲 | モデル／推論／理由 | 排他資源 | 子／状態／PR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 275 | なし | /home/sion2000114/workspaces/dev/puyo-s15-275 / PUYO-275/esports-tsumo-provider | 7757f23 / integration は 278 監査後確定 | 配ぷよ provider，core/env/CLI/開始設定，関連テスト | gpt-6-sol / medium / 明確な provider 追加 | launcher/UI の編集を 276 に先行，native build は要調整 | 起動準備 |
| 276 | 275 の GUI/CLI 編集完了後に実装 | /home/sion2000114/workspaces/dev/puyo-s15-276 / PUYO-276/automatic-qa-replay | 7757f23，実装時に 275 head へ更新 / 275 branch | QA 保存，launcher/UI/manifest/validator，関連テスト | gpt-6-sol / medium / 既存 replay の保存導線 | 当初は読取調査のみ，GUI 計測は排他 | 起動準備 |
| 266 | 275 の受入達成．当初は読取設計のみ | /home/sion2000114/workspaces/dev/puyo-s15-266 / PUYO-266/sprint15-single-quality | 調査用 7757f23，依存実装前に検証済み先行 head を親が取り込む / stack の先行 branch | 単独 gate，nextgen 構築品質 | gpt-6-astra / high / 探索・品質原因調査 | 重い探索と GUI/native build は排他 | /root/puyo266，読取設計 |
| 277 | 275 達成，266 と契約編集を直列化 | /home/sion2000114/workspaces/dev/puyo-s15-277 / PUYO-277/attack-response-gate | 調査用 7757f23，実装前に先行確定 head を取り込む / stack の先行 branch | 対戦 gate，独立 eval sidecar を優先 | gpt-6-astra / high / deadline と採用整合 | 266 と shared_search/tactic_manager の編集は排他 | /root/puyo277，読取設計完了・clean 待機 |
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
- 276 の独立保存 module/tests/docs は `fd573573f69f92f4e7a3bafe089f28987151dedc` まで commit，6 tests PASS．親 review で別 PC/path へ移動した bundle を validator が拒否する問題を修正済み．既存 UI 接続は 275 編集後に実施する．
- 266 の読取調査で public PURPLE pair を long_horizon_search／deep_chain_builder が拒否する既存契約欠陥を発見．これは provider 追加前にも独立検証できるので，parent が `agents/long_horizon_search.py` と `agents/deep_chain_builder.py` の public color 境界・関連 tests/docs だけを 266 へ許可した．275 に取込必要を通知．非公開 provider palette/future は policy へ渡さず，legacy random の挙動を維持する．正式単独 gate は依存受入待ちのまま．
- public wire の ID 5 は OJAMA だったため，275 は原本 4 色の全単射による既存 RGBY への符号化を採用した．RGBY は保持，紫は当該行で欠けた色へ写す．原本との同色関係・軸子順・連鎖構造・周期を保持し，色対応は replay に記録する．既存 wire/native を変えない．266 の試験修正は撤去，調査差分・test・設計を `/tmp/puyo266-public-color-investigation.patch`，`/tmp/puyo266-public-color-investigation-test.py`，`/tmp/puyo266-single-gate-design.md` に退避し，7757f23 clean に戻した．修正 PR は作らない．
- 275 は `6b9d3aaada510776dff62169e97701cebfa53b14`，PR #183（draft，base v1.7.3 統合，reviewer なし）まで到達．原本 `/tmp/puyo275-haipuyo.txt`．別作者 puyop API の ID 0/34066/65535 全 256 文字，weakflour 全 ID の先頭 24 文字を照合．公式ゲームの独立実測とは区別する．49 tests と legacy replay，30 tick replay hash 一致を報告．
- 276 の clean `47fb772877f33532cb5fa368b0b8a49e285b42c6` へ，親が 275 の head を通常 merge (`5fa8501`) し，既存 launcher/UI/renderer 接続を許可．PR base は 275 branch．
- 275 の追加 review で legacy policy info の simulator から原本全列・pattern ID へ到達できる経路を確認．275 に core/tsumo と puyo_env の info 境界だけを再割当し，新方式で public snapshot 化する修正を進行中．UI は 276 所有のまま．privacy 未達で 275 COMPLETE と後続正式 gate を保留する．
- 277 は read-only で既存 response/survival coverage を確認し，新 eval wrapper・副砲分類 sidecar・人工盤面/攻撃 script を freeze する設計を報告．共有 agents の変更をせず gate 専用 replay wrapper を優先する．実装は 275 受入後．
