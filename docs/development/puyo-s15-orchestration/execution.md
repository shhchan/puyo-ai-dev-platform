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

## Provider 受入後の実行

- 275 最終 head `51d53a8d5008fca3554c7b97502b6a31e637c749`，PR [#183](https://github.com/shhchan/puyo-ai-dev-platform/pull/183)，base `integration/puyo-228-v1-7-3`，ready，reviewer なし．Jira Complete，セッションコメント 10819．privacy 境界修正後 89 tests，外部 source audit，legacy/new replay を子が確認．親も source 専用 8 tests／Ruff F／diff check 成功．GitHub CI は進行中であり成功を未確認．親が継続追跡する．
- 276 へ親が最終 provider を通常 merge，起点 `8717839653edcd15a02d754d1e05418a50913c4f`．GUI/launcher/renderer と新 QA module の担当を保持．root read-only venv で 77 tests 成功．保存 OFF/ON 各 120 frame の GUI/native 実測排他枠を付与した．他の子は重い実行を待機する．
- 266／277 の worktree を親が `51d53a8` へ fast-forward，clean 確認後，同じ子を再開して gate 実装を開始．両 PR の当初 base は 275 branch，最終 stack では 276→266→277 の通常 merge による ancestry を親が全子停止後に構築する．共有 agents/runtime/core/UI は所有範囲に含めず，必要な修正は根拠つきで再割当する．
- 266 は新 eval 専用の単独 gate を担当．reference 入力も真の hidden/ghost にアクセスせず，public snapshot と既存 PublicInferenceTracker の公開履歴による推定だけを使う．unknown は BLOCKED．120 run の正式評価は manifest 固定と排他枠付与後に実行する．
- 277 は `00a8fe1` で 4 pattern × 7 case の fixture を初回評価前に固定．人工公開盤面／攻撃 script を既存 realtime engine に適用する eval wrapper を作る．配置・resolution・cancel/drop の実 trace，public witness の prepared/fireable 分類と replay を照合し，未観測や cutoff を PASS にしない．

- 276 の初回 random nextgen/human 120 frame 計測で replay 134 MiB／保存 23.1 s を発見．毎 tick の同一巨大 controller diagnostics 複製を新 decision 時の完全記録＋中間 tick summary に縮小し，QA replay の JSON を compact 化．旧 `--replay` の記録は保持．改善後 ON は 145 tick／replay 2.4 MiB／result 584 KiB／保存 0.137 s／RSS 157564 KiB，OFF は 143 tick／RSS 157568 KiB．frame p95 は OFF 22 ms／ON 21 ms，p99 は OFF 65 ms／ON 84 ms．tick 数／推論遅延が異なり，この短い計測は S14 cadence SLA の再合格や因果比較を意味しない．
- 276 の esports ID 34066 は 12 frame／27 tick 保存，原本別 path と bundle 移送後の validator 成功．native 使用 random 対局の module／wrapper SHA と，source mode／pattern／mapping／policy seed／hash を親が manifest で確認．測定 source は UI 実装中の dirty head 8717839 と記録され，最終 clean head の受入結果には読み替えない．GUI 排他枠を解除し，266 の native smoke／正式評価所要時間見積へ移した．

- 276 PR [#184](https://github.com/shhchan/puyo-ai-dev-platform/pull/184)，base 275，head `66206e6`，reviewer なし．79 tests 成功，Jira In Progress，comment 10820．人間操作 QA と長い対局測定を未完と分離した．docs/probe 準備 `92d100a` で長い実測を実施：random seed127 nextgen/human x1.0，50 ms key script，OFF 987 frame／1000 tick，ON 1008 frame／1000 tick．ON replay 14,845,041 bytes，保存 0.753 s，移送 validator 成功．frame p95/p99 OFF 21/45 ms，ON 18/30 ms．input schedule p95/p99 OFF 18.94/40.77 ms，ON 16.42/57.82 ms．ON input p99 は既存 50 ms 基準未達であり，S14 SLA の再合格や保存機能との因果関係は未証明．子が raw/probe/限界を docs/PR/既存コメントへ保存する．
- 266 gate 実装 `651449a`，専用 15＋既存 38＝53 tests 成功．pattern 0 の両 policy 各 3 placement の native smoke は全 lock／receipt／quota／tick hash 一致，incomplete window 以外の integrity 問題なし．nextgen 平均 decision 0.269 s，reference 0.291 s．raw 2 件と manifest は `6600314` で保存．正式 120 run は 90〜120 分見積．先に 276 長い測定と 277 formal を完了し，その後排他枠を付与する．
- 277 `7091afd`，専用 9 tests／Ruff 成功．manifest `/tmp/puyo277-formal-v1/manifest.json`，SHA `c047f98a23a8dfc91eb48a96cd82efe31177e6c78d8a74c2f94b46e9ec67cb63` を固定，56 条件の formal を 1 worker／各 thread 1／600 s alarm で開始．探索の partial/cutoff と，肯定的に証明された public root＋実 receipt／lock／resolution／cancel/drop の結果を分離する．全候補列挙の完了は Jira A/C が要求しておらず，未発見・unknown を成功や不可避にしない．旧 cutoff 一律 FAIL smoke は保持する．
