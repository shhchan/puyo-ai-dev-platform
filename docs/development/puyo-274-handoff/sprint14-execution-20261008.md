# PUYO Sprint 14 実行表（2026-10-08）

Jira 接続：cloudId 46424ed5-7d42-4bff-bc2a-da4c296f8b5b．JQL `project = PUYO AND sprint = 'PUYO Sprint 14' AND status = 'In Progress'` は 264/266/268/269/273/274 の 6 件．親エピックは全件 228．関連リンクは getJiraIssue evidence で確認．既存 Complete の 190/239/270 と Sprint 14 の他 Complete を再実装しない．

起点：integration/puyo-228-v1-8-0 = 732ed3da17a1a6144a98c7995fbe9dfe2e45ea91，stack #176 の #172→#173→#174(draft)→#175 head c812b3f8ad257645937fa6140412a6db6be29930．後続は #175 に積み，既存 PR/head を書き換えない．全 worktree の起点 c812b3f．root は PUYO-262 のまま clean．親の新しい作業 branch は PUYO-274/sprint14-closeout（同じ c812b3f 起点），専用 worktree /home/sion2000114/workspaces/dev/puyo-s14-274．

| Jira | 依存／待機 | worktree／branch | 担当範囲・PR base | モデル・推論 | 資源 |
|---|---|---|---|---|---|
| 266 | 先行なし，品質 FAIL/G2 BLOCKED | /home/sion2000114/workspaces/dev/puyo-s14-266 / PUYO-266/public-survival-history | 公開自配置履歴，hidden 推定，survival の固定反例，seed127/58 softmax1.0，#175 | gpt-6-astra/high：公開/private 境界と生存・正常構築の競合 | 探索 heavy は親の許可まで禁止 |
| 273 | 266 と scheduler/realtime_ai 共有の可能性，初期は調査のみ | /home/sion2000114/workspaces/dev/puyo-s14-273 / PUYO-273/desktop-mask-cadence | 両側 GUI gate の同期 mask/finish 原因，独立変更の確定まで read-only，#175 | gpt-6-astra/high：live clock と IPC，実配置安全性 | WSLg/GUI heavy は親の許可後 |
| 264 | G2 検証待ち，266 に依存する完了境界 | /home/sion2000114/workspaces/dev/puyo-s14-264 / PUYO-264/desktop-capability-qa | 既存証拠と A/C の read-only 監査，264 の残差分類．必要な小さな証跡のみ独立変更を相談，#175 | gpt-6.1-sol/medium：既存 artifact の再計算と契約監査 | 軽量 read-only のみ |
| 268 | 266 の shared search と所有競合 | 未作成 | 264 終了後に担当 worktree を親が作成し，定型配色/将来完成/3定型比較 | 複雑度確定後選択 | 直列 |
| 269 | 273 Blocks，GUI 共有 | 未作成 | 273 の確定 head 後に worktree を親が作成し，残余 cadence／人間 QA | 複雑度確定後選択 | WSLg 排他 |
| 274 | 266/269/273 と QA 連動 | /home/sion2000114/workspaces/dev/puyo-s14-274 / PUYO-274/sprint14-closeout | 親が人間 QA，子成果・stack・組合せ QA，Jira 1 comment を管理 | 親設定継承 | 全重作業の排他割当 |

ユーザー人間 QA：puyo-desktop-274 の 1P nextgen/2P human，o で 3 手表示切替・現在組 ghost と区別 OK，移動/回転は意図位置へ置けそうな程度．seed127，1P policy seed58，daa，softmax1.0 の human 全消しによる大量攻撃後に 1P 窒息．速度 x1.0，元 replay／結果 JSON は未保存（2026-10-08 に依頼者回答）．選択 mode/temp の既定は argmax/0.2 と source で確認，報告値は明示指定の別条件．再現と原因帰属を混ぜない．

G2 定義：設計 docs/development/puyo-234-next-generation-model-design.md §17，平均最大実連鎖≥10，premature0，game over0，全60run正当終了，repeat digest一致，必須脅威fixture全成功，既知解候補gap0．既存合成結果 8.8667/6/10 で FAIL．正式 G2 は266が所有，256〜258本学習は開始しない．

PR ごとにレビュー可能な差分と検証，Jira コメントを記録．親は子停止後に履歴保持 merge で新 PR を stack に組み込む．GitHub PR merge/release はしない．AC 未達は COMPLETE にしない．

## 編集・資源割当追補

- 266 は eval/nextgen_realtime_diagnostic.py，専用 tests，puyo_env/nextgen_public_snapshot.py と agents/生存選択範囲を所有．native build と seed127/58 初期逐次診断を先行割当．速度 x1.0，元 replay なし，human 実入力不明．
- 273 は保存 raw の読取解析後，puyo_env/action_planner.py と puyo_env/realtime_ai.py の同期 mask/activation に限定して編集所有．GUI 重計測は 266 の先行枠終了後．nextgen_scheduler.py と公開 wire は現時点で変更しない．
- 264 は docs/development/puyo-264-realtime-template.md，docs/benchmarks/puyo-264-desktop-audit/，tests/test_nextgen_realtime_diagnostic.py の legacy starvation 期待 1 件だけを所有．後続 matcher により witness `(0,)` が現在妥当と確認する前提で，本来の完成→build_main 実採用 receipt を残す．266 は同 test file を編集しない．heavy/G2 は割当なし．

## 実行更新（2026-10-08）

- PUYO-264 は 28 tests・Ruff・差分検査に成功し，head `a3396b6d10e6f9678fa44dac1ff2e5d2ece8147d`，base `PUYO-274/desktop-resume` の [PR #177](https://github.com/shhchan/puyo-ai-dev-platform/pull/177) を作成．Jira `Complete`，コメント 10809．正式 G2 は失敗のまま PUYO-266 が所有．
- PUYO-268 は `/home/sion2000114/workspaces/dev/puyo-s14-268`，branch `PUYO-268/sprint14-template-quality`，起点 PUYO-264 head `a3396b6d10e6f9678fa44dac1ff2e5d2ece8147d`，PR base `PUYO-264/desktop-capability-qa` で開始．`gpt-6-astra/high` を選択：reference 比較の公開入力境界と品質 A/C 判定が複雑なため．shared agents/native は 266 と競合するので編集せず，先に専用検証器・証跡を作る．重い比較は GUI 計測と直列．
- PUYO-273 は mask/activation の timed witness 再利用を `puyo_env/action_planner.py` と `puyo_env/realtime_ai.py` に限定して実装し，暫定 commit `b7365ba`．114 件の GUI／planner 回帰が成功．事前固定した seed55/x1.0/1120×780/60FPS/overlay ON の同条件 GUI before/after と profiler overhead を，266 の初回 heavy 枠解放後に測る．受け入れ gate は frame/input p95≤25 ms／p99≤50 ms のまま．
- PUYO-266 は seed127/58，softmax1.0，速度 x1.0 の公開入力による新規再現を先行実行中．元 human 入力と replay は未保存なので，同一対局の再現とは扱わない．初回 heavy 終了後，WSLg 枠を 273 に渡す．
- PUYO-269 は PUYO-273 の確定 head／gate 結果を待ち，専用 worktree を親が作成して子へ委任する．PUYO-274 は全依存と組合せ QA を確認してから判定する．

## 実行更新（PUYO-268／273 確定後）

- PUYO-268 は head `d5656843b7511e3f93b2e6dc950c9b705c136ab5`，base `PUYO-264/desktop-capability-qa` の [PR #179](https://github.com/shhchan/puyo-ai-dev-platform/pull/179) を作成し，43 tests・120 判断の同一公開入力/予算照合に成功．Jira `Complete`，コメント 10810．reference seed123 は 30 手後に mask 外 root を選び未完了として保存し，PUYO-266 G2 FAIL と区別．
- PUYO-273 は head `0a364aa2a6d8b5a5ca0313c5aa6ca3369a21f222`，base `PUYO-274/desktop-resume` の draft [PR #180](https://github.com/shhchan/puyo-ai-dev-platform/pull/180) を作成．115 回帰，固定 8+8 GUI run を保存．最終両側 frame p95 `25.1802222 ms` で事前 gate `≤25 ms` 未達，input p95/p99 `20.74/34.75 ms` は通過．Jira In Progress，コメント 10811．残る prepare 同期 mask・decode/GIL・GC を PUYO-269 へ引継ぐ．
- PUYO-269 は `/home/sion2000114/workspaces/dev/puyo-s14-269`，branch `PUYO-269/desktop-cadence-finish`，起点 PUYO-273 確定 head `0a364aa2a6d8b5a5ca0313c5aa6ca3369a21f222`，PR base `PUYO-273/desktop-mask-cadence` で子 `/root/s14_269` を起動．`gpt-6-astra/high`：UI／scheduler／GIL/GC の支配原因と公開 mask 保証の設計判断が複雑なため．所有は `puyo_env/nextgen_scheduler.py`，`eval/puyo_269_gui_probe.py`，専用 tests/docs；`realtime_ai.py` は親と事前調整．WSLg/重計測の排他枠を割当．
- PUYO-266 は head `0e7a707` 以降の draft [PR #178](https://github.com/shhchan/puyo-ai-dev-platform/pull/178) を作成．新規 seed127 全消し対局で変更後も窒息し，正常 GTR55/123/124 は 10/10/11 連鎖・非窒息，失敗126/128は窒息が残る．128 node 枠内の全候補 control BFS は正常123/28の候補を cutoff へ退行させたため不採用とし，証明可能な満杯14段壁の候補排除だけを runtime に残す．Jira は In Progress／G2 FAIL のまま判定する．

## 子セッション停止時点の判定

- PUYO-266 は確定 head `f3f40f62438bc7c3fa37fb7a33f65e9cb14bd7f1`，draft #178，Jira In Progress／コメント10812．変更後 normal127 でも窒息，GTR126/128 の窒息も残る．後続 control 証明だけでは正常123の128 node枠と hidden row12/13 の両問題を解けず，未採用案の数値と offline 完全盤面を診断専用として保存した．正式 G2 を PASS としない．
- PUYO-269 は確定 head `251b62d1c2cda145a5d912580af98305efdba4a0`，base `PUYO-273/desktop-mask-cadence` の draft [PR #181](https://github.com/shhchan/puyo-ai-dev-platform/pull/181)，Jira In Progress／コメント10813．同一 source の診断 proof 中央値 0.915→0.102 ms，ただし最終両側 frame p95 `25.37 ms`，repeat `27.42 ms`，minimal `26.69 ms` で事前 gate `≤25 ms` 未達．全入力，片側，human，軽量 frame は通過．実人間再 QA は未実施．prefix共有案は固定 micro で悪化し不採用．
- PUYO-273 は #180 の `25.1802222 ms` 未達を保持し，PUYO-269 と組合せた最終 source でも両側 frame gate が未達なので Jira In Progress のまま．
- PUYO-274 の先読み表示と操作感に関する 2026-10-08 人間 QA は確認済み．窒息，両側 frame，変更後の人間再 QA，正式 G2 の条件が残るため Jira In Progress のまま．
