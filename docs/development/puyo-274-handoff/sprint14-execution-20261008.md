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

## 親の組合せ QA とレビュー順

GitHub stack #176 は `integration/puyo-228-v1-8-0`（起点 `732ed3da17a1a6144a98c7995fbe9dfe2e45ea91`）へ #172→#173→#174→#175→#177→#179→#178→#180→#181→#182 の順で接続した．下段 4 PR の head/base は維持．新規後段は各先行 head を祖先に含める履歴保持 merge を通常 push し，PR base/head/draft/reviewer 未指定を再取得した．PR merge/release はしていない．

組合せ QA 実行時，一時 worktree `/home/sion2000114/workspaces/dev/puyo-s14-integration-qa` の同順 merge と最上段 `/home/sion2000114/workspaces/dev/puyo-s14-274` の tree hash はともに `42606f91d2e5fb55b19928d1f29b3f65a57f4cf5`．この記録の追記は docs のみ．結合状態で主要10 moduleの79 tests，GUI 4 moduleの53 tests，変更 Python の Ruff，統合差分 check が通過．PUYO-268 の raw 9 件／120 判断 verifier と PUYO-269 の 12 GUI trace aggregate も通過し，それぞれ reference 未完了と両側 frame 未達を保持した．これは保存 raw の整合確認であり，最終 stack 上の新しい人間 GUI run や正式 G2 PASS ではない．

| レビュー順 | Jira／PR | 判定と担当範囲 |
| --- | --- | --- |
| 1–4 | 既存 #172→#173→#174→#175 | 既存 preview・生存・cadence・引継ぎ．#174 は draft． |
| 5 | PUYO-264／[#177](https://github.com/shhchan/puyo-ai-dev-platform/pull/177) | Complete．通常速度の定型/rule 能力を証拠で確認． |
| 6 | PUYO-268／[#179](https://github.com/shhchan/puyo-ai-dev-platform/pull/179) | Complete．同一公開入力・固定予算の reference 比較，失敗 run も保存． |
| 7 | PUYO-266／[#178](https://github.com/shhchan/puyo-ai-dev-platform/pull/178) | draft／In Progress．seed127 の窒息と G2 FAIL が残る． |
| 8 | PUYO-273／[#180](https://github.com/shhchan/puyo-ai-dev-platform/pull/180) | draft／In Progress．両側 frame p95 が25.1802222 ms． |
| 9 | PUYO-269／[#181](https://github.com/shhchan/puyo-ai-dev-platform/pull/181) | draft／In Progress．最終両側 frame p95 が25.37 ms，変更後の実人間 QA 未実施． |
| 10 | PUYO-274／[#182](https://github.com/shhchan/puyo-ai-dev-platform/pull/182) | draft／In Progress．引継ぎ・組合せ証跡，依存 gate 未達． |

変更後の実人間 GUI QA は最上段 worktree で実施する．既存 desktop venv の native wheel を読み取り専用で使用する．launcher で 1P=`nextgen_tactic_manager`，2P=`human`，速度 x1.0 を選び，`o` の 3 手先読み，現在組 ghost との区別，押しっぱなし下＋横／回転での意図した配置を確認する．速度と対戦/policy seed を結果に併記し，元 seed127 run と同一視しない．

```bash
cd /home/sion2000114/workspaces/dev/puyo-s14-274
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python main.py
```

## 最新の GUI 機械 QA（2026-10-08，PUYO-269 追加修正後）

上記「子セッション停止時点」の両側 frame gate 未達は，PUYO-269 の追加修正前の値である．追加修正の head は `19da026dc6bd8ee358df2155552ab9b07a96908f`．`TickInput` の固定 pulse／idle を再利用し，held が空の idle tick で不要な入力 edge 集計を省いた．実際の simulator subclass や入力 edge がある tick の処理は従来通りとする．81 条件×22 root の一致，125 tests，Ruff に成功した．

固定 source／設定の最終 16 GUI run では，frame／input の p95 ≤ 25 ms，p99 ≤ 50 ms を全条件で通過した．両側 frame p95／p99 は 23.13／29.98 ms，再計測 24.35／35.22 ms，minimal 22.25／41.63 ms．最小 p95 余裕は 0.65 ms であり，別環境の安定保証ではない．証跡は `docs/benchmarks/puyo-269-mask-allocation/` に保存した．追加 head を PUYO-274 最上段へ履歴保持 merge した head は `ecad2500e74aeb4ad33c60a8be2371c67cf97706`．ここで主要 80 tests，GUI 53 tests，Ruff，差分検査，保存 raw verifier が通過した．

PUYO-269／273 は修正後の実人間 GUI QA が未確認のため draft／In Progress を維持する．PUYO-266 の公開盤面に基づく生存修正は継続中であり，正式 G2 PASS も未確認．この段落の機械 QA は PUYO-266 の次の runtime 変更を含まないため，最終 head で再検証する．

## 最終結合更新（PUYO-266 の公開推定後）

PUYO-266 の [draft PR #178](https://github.com/shhchan/puyo-ai-dev-platform/pull/178) は head `15aed37b005d9c8410a395511cdfbc178ff2caf6`．公開の実 lock 履歴と visible snapshot から自分の hidden 2 行だけを確定できるときに推定し，request.v2 へ episode を照合して bind した．旧 request.v1 の wire/digest を保ち，native／actor 入力へ推定 hidden セルを渡さない．後続 control と追加 1 配置の有限証明を survival 128／response 256 の中で課金し，証明不能・打切りは unknown として従来経路へ戻す．

保存 133 判断の公開推定と offline 実盤面の不一致 0，関連 136 tests，legacy 383 判断の evidence 比較は成功．新規固定 native 5 対局では正常 GTR 55／123／124 が最大実連鎖 10／10／11，小発火 0，非窒息，旧入力列・最終 hash と一致した．126 は旧 36 配置の窒息から 40 配置生存へ変わったが，小発火 2／最大 1 連鎖．128 は 39 配置で窒息が残る．元の人間対局とは異なる固定 2P 入力による normal127／policy58／daa／softmax1.0／x1.0 でも，1P は 30 lock 後に窒息した．全 34 request の推定と offline 実盤面，30 実 lock，全 tick／最終 hash は一致した．追加の cache／公開 known plan／段階化の案は 128／127 を直せず正常 seed を退行させるため棄却した．正式 G2 は既存の FAIL／BLOCKED を維持し，再計測 60 run で PASS とは宣言しない．Jira 266 は In Progress，コメント 10812 を同一セッション内で編集済み．

親が #178 の確定 head を #180→#181→#182 へ履歴保持 merge した．GitHub PR merge／release／reviewer 指定／force-push はしていない．最上段で PUYO-266 を含む 23 module／229 tests，変更 Python の Ruff，統合差分検査，PUYO-268 の raw 9 件／120 判断 verifier が成功．結合 GUI 測定 source `cd299d7c2e25cf10c1130fb94ba0bc3f134c31ce` で，#269 の変更後 8 条件を新たに逐次測定し，frame／input p95 ≤ 25 ms／p99 ≤ 50 ms を全件通過した．両側 frame p95／p99 は 22.65／44.86 ms，再計測 24.04／34.18 ms，minimal 23.87／31.77 ms．全 8 run の source file fingerprint／native／host は一致し，実 lock 不一致，scheduler error，timeout，fallback は各 0，cleanup は全件成功．[raw／集計](../../benchmarks/puyo-274-sprint14-combined-20261008/README.md)を保存して再集計 verifier が通過した．最大 p95 余裕は約 0.96 ms．この機械結果は人間 GUI QA の代替ではない．

PUYO-264／268 は Complete．PUYO-266 は窒息と G2 品質が未達．PUYO-269／273 は最終 head の機械 gate は通過したが，修正後の人間 GUI QA が未確認．PUYO-274 は依存する生存・正式品質・人間 QA が未達．後 4 件は draft PR／Jira In Progress を維持する．依頼者は最上段での人間 GUI QA を実施して結果を報告すると回答済みで，完了前にこの最上段の起動先を案内する．
