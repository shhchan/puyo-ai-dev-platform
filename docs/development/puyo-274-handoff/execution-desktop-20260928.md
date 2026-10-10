# PUYO-274 デスクトップ再開実行表

窓口 PUYO-274，親 PUYO-228．Jira は In Progress．最新統合起点は `732ed3da17a1a6144a98c7995fbe9dfe2e45ea91`．#171 は 2026-09-28 に merge 済み，最終 head `0c90fd27d38f546dba80aebd25bc6bb0c694eee8` の native CI 2 件は SUCCESS．開始時の open PR は 0 件．旧 stack #167 は今回の起点として再使用しない．

環境：Linux x86_64/WSL，Intel Core i7-14700F，28 logical CPU，RAM 15 GiB，swap 4 GiB，DISPLAY=:0，Wayland=wayland-0，WSLg．CPython 3.12.3．[環境記録](desktop-environment-20260928.json) に依存 version と native 情報を保存．最初の requirements install は PyPI read timeout．各 worktree の新規 venv は既存 desktop runtime 依存を読み取り専用の `.pth` で参照し，native wheel は各 venv へ独立 install．pip check は全環境 PASS．native/build script/requirements-native は起点との差分なし．native revision の `-dirty` は親の未追跡実行表に由来するため，clean build と偽らず source hashes と wheel SHA を記録．ノート PC と同条件 A/B とは扱わない．

| Jira／担当 | worktree／branch | 起点／PR base | 担当範囲 | モデル／推論／理由 | 排他資源／状態 |
| --- | --- | --- | --- | --- | --- |
| PUYO-274／親 | /home/sion2000114/workspaces/dev/puyo-desktop-274 / PUYO-274/desktop-resume | 上記 SHA / integration/puyo-228-v1-8-0 | 実行表，環境整備，組合せ QA，PR/stack，窓口 Jira コメント | 現在の親設定を継承 | native build と heavy probe の割当，GUI の排他所有 |
| PUYO-266／生存 | /home/sion2000114/workspaces/dev/puyo-desktop-266 / PUYO-266/desktop-survival | 上記 SHA / integration/puyo-228-v1-8-0 | shared search，survival，selector，戦術の選択契約と回帰 | gpt-6-astra / high：生存・公開入力・実採用の原因分離 | 最終 #173，子停止・所有権返却済み |
| PUYO-274／preview | /home/sion2000114/workspaces/dev/puyo-desktop-preview-274 / PUYO-274/nextgen-plan-preview | 上記 SHA / integration/puyo-228-v1-8-0 | diagnostics の N=3 plan，表示契約，preview 回帰 | gpt-6-astra / high：adopted plan と未来 witness の契約分離 | 最終 #172，子停止．窓口コメントは親のみ |
| PUYO-269／cadence（273 gate も測定） | /home/sion2000114/workspaces/dev/puyo-desktop-269 / PUYO-269/desktop-cadence | 上記 SHA / integration/puyo-228-v1-8-0 | scheduler/realtime/GUI の同期経路，計測と入力回帰 | gpt-6-astra / high：frame/input/IPC を横断する原因究明 | 最終 #174 draft，子停止・所有権返却済み |

共有 agents/nextgen_tactic_manager.py は生存担当が選択契約，preview 担当が diagnostics のみを所有し，編集前に親へ通知する．scheduler/realtime/renderer は cadence 担当が所有する．preview 側で変更が必要な場合は親へ相談して直列化する．子は再委任しない．各自の限定 commit/push/PR まで，reviewer 未指定，merge/release なし．独立 PR の stack 化は全子停止後に親が履歴を保つ merge で実施する．

固定 gate：frame/input p95 ≤ 25 ms，p99 ≤ 50 ms．品質 FAIL/G2 BLOCKED，正式 G2 と残る品質 A/C は PUYO-266 が所有．本学習 256〜258 は開始しない．元 daa/random 人間 run の raw は未提供のため新規再現として記録する．実人間の配置 QA は機械入力で代替した扱いにしない．

## 開始確認

- 引継ぎ manifest/inventory の JSON parse，8 GUI catalog，266 統合 G2/269 統合 GUI/273 timed 証跡ディレクトリの存在を確認．
- `scripts/build_deep_chain_native.sh` を CARGO_BUILD_JOBS=4 で実行し，locked release manylinux_2_28 CPython 3.12 wheel，ABI/schema check を通過．wheel SHA-256 は `b1c2baf386a2dd4cc32faefcd508e9156fd452bc695a774b2b24c7a6237cfa39`．
- `eval.realtime_versus_ui --help` が成功し，policy/seed-a/seed-b/nextgen-templates 引数を確認．launcher と実 GUI は最終組合せ QA でも成功．
- gh-stack v0.1.1 の version/help と stacks API を確認．旧 stack #167 は closed，全 10 PR は merge 済み．新しい PR の確定後だけ新 stack を作成する．
- preview の静的原因：nextgen diagnostics に top-level `plan` がなく，既存 overlay はそのキーだけを読む．273 回帰とする根拠は現時点でない．preview 専用生成は新探索なし，公開 3 組に一致する参考 continuation を表示し，実行 queue として扱わない．

## Desktop baseline と原因

PUYO-266 の新規 daa/random は step/measured 条件（共通 seed 59，policy seed 55/59）で，通常速度の元人間 run と同定しない．1567 tick/28 採用，28.22 s，fallback 0．終盤に random 由来おじゃま着弾があり，最終公開 root に小消し候補を検出できなかった．消去不能ケースを回避可能窒息改善の合格例には使わない．旧 G2 の公開反例では，未知 hidden 行への積み上げが非発火 survival witness になる点を調査する．unknown を fatal と偽らず，消去・落下後の生存証明を狭く修正する方針．

PUYO-269 の desktop baseline は計測拡張のみの source `3a86e81`，seed 55，4 条件を独立 process で逐次測定（cold/warm 混在）．frame p95/p99 (ms)：軽量 18.9/21.5，片側 26.7/53.5，両側 42.6/82.4，human 22.5/54.3．input：15.5/16.1，24.4/52.6，35.3/61.0，25.0/46.4．全 worker cleanup 成功．毎 frame の diagnostics.to_dict 再帰コピーが片側 render p99 35.5 ms/human 35.1 ms となり，描画の tail に寄与する．両側 update p99 72.8 ms も残るため，コピー削減だけで全 gate PASS を先取りしない．native/予算/入力は維持し，描画で必要な receipt 要約だけを読み取る狭い変更を比較する．

## 生存修正案の棄却と担当範囲の確定

未知 hidden 行への配置を一律 unknown とする試作は，126/128/132 の窒息を改善したが GTR 123 の最大連鎖を 10→3，premature を 0→2 に悪化させたため不採用．既存の正常な hidden 配置から大連鎖へ進む継続を壊す．試作 commit `d1060ba` は通常 revert `18c1051` で取り消し，共有履歴を書き換えない．before 10 run と棄却 after 7 run を保存後，所有 PGID 22276 のみ停止し，未実施の after 144/daa/persian と daa 再測定を成功扱いしない．

公開/完全盤面の offline 比較では，正常 GTR123/28 の hidden 欠落は 0．126/34，128/38，132/32，144/37 は自配置に由来する hidden 1/4/1/2 セルが公開推定から欠落し，NEXT action 0 が公開推定では到達可，完全盤面では到達不可．126/35 は公開投影だけでも後続到達不可．完全盤面は offline 原因診断に限り actor へ入力しない．既存 PublicVersusSnapshotAdapter は hidden を意図的に unknown とし，placement event の action/cells も未記録なので，既存履歴から確定復元できない．

PUYO-266 の今回の PR は，新規 daa 再現入口，公開反例・実 lock/replay，比較・棄却結果と必要な公開履歴契約を保存する．窒息 runtime 修正済みとは扱わない．採用済み自配置の根拠を保持する履歴契約，途中開始/未知/reset/stale と公開情報境界，固定 quota/latency，正常 55/123/124 と失敗 seed の回帰を次の独立した設計・実装単位として 266 に残す．追加チケットや本学習へ自動拡張しない．

## 子の最終成果（組合せ前）

- preview：`/root/preview_274`，PR [#172](https://github.com/shhchan/puyo-ai-dev-platform/pull/172)，head `98c84c1fc05dfce67b1d0f894e51fee961f62569`．生成/manager 33 tests，Ruff/diff check PASS．作業停止・clean 確認済み．
- 生存：`/root/survival_266`，PR [#173](https://github.com/shhchan/puyo-ai-dev-platform/pull/173)，head `78b2c297e5cafb55b71fc1a2c8ec1a9d850b6fcc`．17 run の replay/実 lock と新規 daa 全 1567 tick が一致．最終 14 tests 中 13 PASS，legacy starvation 期待 1 件は起点でも同じ FAIL（`test_fixed_gtr_fixture_completes_then_rule_switches_to_build_main`，witness 空の期待に対して `(0,)`）．期待を都合よく変えない．CLI 既定/override，Ruff/diff check，51 artifact SHA を確認．Jira コメント 10774，In Progress．作業停止・clean 確認済み．
- cadence：`/root/cadence_269`，純粋な型復元を既存 result-reader thread へ移し，UI の request/public/execution/phase/authoritative root 再検証を維持．private proof を controller で失う中間版は不採用として別保存．最終 `22cece2` は実 controller の復元 thread/count，decode 中 cancel，nested mutation，bool/int/float 型変更，stale，process/reader cleanup を回帰．新 thread/process，wire/schema/native，tick catch-up の変更なし．

最終 cadence は同 source `22cece2` で UI 復元/reader 復元を切り替えた A/B，overlay OFF，各条件の独立 fresh process，native/config/seed 55 固定．最終 reader の frame/input schedule は次の通り．

| 条件 | frame p95/p99 (ms) | input schedule p95/p99 (ms) | frame/schedule gate |
| --- | --- | --- | --- |
| 軽量 | 18.84/19.78 | 15.73/16.11 | PASS |
| 片側 nextgen | 23.01/42.65 | 16.27/24.73 | PASS |
| 両側 nextgen | 24.93/51.39 | 16.43/44.39 | frame p99 FAIL |
| nextgen/human（合成入力） | 19.88/40.44 | 16.17/28.33 | frame/schedule PASS，人間意図 QA 未実施 |

minimal-one は frame 19.84/43.51，input 16.31/30.91．全 worker cleanup 成功，fallback/timeout/deadline 0，合成 human held 不一致/余分横 0，回転 20/20．予定 AI lock 一致は one 6/6，two 8/8，human 6/6．片側 accept p50/p95 は 3.63/7.73 ms．両側 >50 ms の 4 frame は update，mask/finish と背景復元の重なり，1 frame は gen2 GC 30.4 ms も重なる．都合の良い反復で gate PASS を選ばず，両側未達と実人間 QA を保持する．

input schedule は予定イベント→handler の時間で，event→state→draw 全体の PASS ではない．cadence reader の event→state/draw p95/p99 は two 31.08/78.65，35.80/82.80 ms，human 29.81/34.11，32.04/40.03 ms と未達を含む．

## 親の組合せ確認と新 stack

全子停止後に #172 の head を #173 へ通常 merge `25bdae7c291fa1445d8150cbedfc473d01126d34`，その head を #174 へ通常 merge `d6ca1b94baa9cd226f7ff79b7b6600e7c11db511`．親 #175 の起点は後者．共有履歴を保持し，層ごとの net diff は各担当範囲のみ．GitHub の PR merge／release はしない．新 stack #176 は #172→#173→#174→#175，最下段のみ integration/puyo-228-v1-8-0，後続は直前 branch．#174 は未達条件により draft，reviewer 全件未指定．

回帰 118 tests／46 subtests PASS，既存起点 FAIL 1 件は別途記録．Ruff（既存 E402 除外）／diff check／main.py 起動成功．実 overlay ON の 4 条件，receipt/lock，o OFF/ON の tick/action/receipt 不変と画面を [組合せ QA](../../benchmarks/puyo-274-desktop-combined/README.md) に保存．two の input schedule p99 68.75 ms／event→draw 37.07/79.82 ms，human event→draw p95 29.19 ms が未達．単独 overlay OFF の two frame p99 未達も保持．

窒息は未修正，正式 G2 は再実行せず，人間 QA も未完了．274/266/269/273 は In Progress，品質 FAIL／G2 BLOCKED，本学習 256〜258 は開始しない．資料・先読み・同期負荷削減・回帰はレビュー可能で，残条件を COMPLETE に読み替えない．

GitHub stack #176 の API read-back で，順序・base・head と reviewer 空を確認．Jira の本セッションコメントは 274=10776，273=10777，266=10774 編集，269=10775 編集．全件 In Progress．親の組合せ実測 source は `d6ca1b9`，最初の証跡 commit は `fcf3627`．#174 の remote native CI run 36380462019 は確認時点で実行中（PASS を先取りしない）．#172/#173/#175 は workflow の path 条件に該当する check が未起動．
