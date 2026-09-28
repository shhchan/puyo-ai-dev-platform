> **2026-09-28 移送時点の優先指示**：ユーザーが本メモの commit/push を明示的に依頼したため，以下の過去の「commit しない」「起票・調査後に停止する」記述は現在の指示を制限しない．本文は過去の記録として保持する．最新のレビュー状態・残課題・デスクトップ再開は [PUYO-274 引継ぎ](puyo-274-desktop-handoff.md) と [09-27 実行表](puyo-274-handoff/execution-20260927.md) を参照し，Jira/PR を再取得する．今回のノート PC 作業は資料保存とレビュー整備までとし，runtime 修正・追加 GUI/正式 G2 はデスクトップへ引き継ぐ．

# PUYO Sprint 12：Codex への一括依頼文

確認日：2026-09-24（JST）．ローカル用メモ，commit 対象外．各回の「コピペ用依頼文」だけをコピーして依頼できる．

## 対象と分け方

Jira の `project = PUYO AND sprint = 106 AND status = "To Do"` で取得した **PUYO-244〜258 の 15 件**が対象．すべて親は [PUYO-228](https://shhchan.atlassian.net/browse/PUYO-228)．Sprint 内の PUYO-190・239・262 は確認時点で `Complete` のため対象外．本文・受け入れ条件・実際の `Blocks` リンクを確認して整理した．

| 回 | まとまり | 対象 | この回の確認地点 |
| --- | --- | --- | --- |
| 1 | 契約・定型・公開情報・記録の基盤 | PUYO-244・245・246・247・248・252（6 件） | 3 定型の形状と成立例，14 手の phase，公開情報と記録形式をレビューできる |
| 2 | 探索・対戦経路・可視化・能力評価 | PUYO-249・250・251・253・254・255（6 件） | rule 対戦を GUI で確認し，候補能力と学習開始可否を判断できる |
| 3 | 学習コード・段階実験・採否 | PUYO-256・257・258（3 件） | 学習コードを検証し，ゲートと資源条件を満たす範囲で実験・採否まで進める |

件数や推定工数を均等にする区切りではなく，人間が成果を確認しやすい境界を選んだ．特に第 2 回の探索実装と第 3 回の実験は重い．**3 回の依頼で全チケットを委任できるが，3 回で必ず全受け入れ条件を満たせるという見積りではない**．PUYO-258 は能力ゲート，実測した計算予算，人間の判断によって続行範囲が変わる．

## 共通の進め方

- 統合先は既存の `integration/puyo-228-v1-8-0` とする．現在の自律開発手順の例と GitHub 上の既存ブランチに合わせた指定である．確認時の head は `605f2cf6185624a892b60f543ba6f87ea163c1fc`，open PR は 0 件．実行時には最新の状態を再確認する．
- 各チケットと古い設計書には `integration/puyo-113-v1-7-2`，Sprint 未設定などの起票時の記述が残っている．以下の依頼文で統合先を明示し，Sprint・状態は Jira の現在のフィールドを参照する．このメモの作成では Jira や既存文書を変更していない．
- 基本は **第 1 回のレビュー・必要な人間確認・統合 → 第 2 回 → 同様の確認・統合 → 第 3 回**．各回に 1 つの stack を作る．PR の merge は人間が行うか，別途明示的に依頼する．
- 先行グループが未統合なら，次の依頼は先行成果の所在と未達条件を確認するところから始める．この依頼文では，未統合の別 stack へ自動的に積み増す運用は指定していない．
- 並列可否は以下を初期方針とし，実際の編集ファイル・契約・計算資源から親が確定する．レビュー用の stack 順は，Jira の実装依存とは別のもの．並列実装した PR は，自律開発手順どおり履歴と差分を整えてから `gh stack link` で取りまとめる．
- 各回の成果報告には，stack/PR 一覧，組合せ QA，人間向けの起動・確認手順，未達条件を含める．通常画面の人間 QA が未実施なら，該当 A/C は未達として残す．

## 第 1 回：契約・定型・公開情報・記録の基盤

| チケット | 実装内容と確認点 | Jira 上の直接の前提 |
| --- | --- | --- |
| [PUYO-244](https://shhchan.atlassian.net/browse/PUYO-244) | 6 戦術と request/batch/features/selection/diagnostics の型・検証・fixture．不正値と旧 schema を拒否する | なし |
| [PUYO-245](https://shhchan.atlassian.net/browse/PUYO-245) | 底面基準の定型 matcher と argmax/softmax 選択．色関係・seed 再現を確認する | PUYO-244 |
| [PUYO-246](https://shhchan.atlassian.net/browse/PUYO-246) | GTR・だぁ積み・ペルシャ式の config，形状図，14 手以内の成立 fixture | PUYO-245 |
| [PUYO-247](https://shhchan.atlassian.net/browse/PUYO-247) | 14 decision 上限と応答後の定型再選択．同じ組の replan を重複消費しない | PUYO-245 |
| [PUYO-248](https://shhchan.atlassian.net/browse/PUYO-248) | 公開対戦 snapshot と timing 要約．非公開 seed/queue の漏洩や到着と着地の混同を防ぐ | PUYO-244 |
| [PUYO-252](https://shhchan.atlassian.net/browse/PUYO-252) | 実入力・選択・実行・報酬を結ぶ trajectory と manifest．破損・欠損参照を拒否する | PUYO-244・248 |

**順序と並列候補：** PUYO-244 を先行し，契約確定後に PUYO-245 と 248 を並列候補とする．245 後に 246・247，248 後に 252 を進められる．ただし 247 と 248 は `puyo_env/realtime_ai.py` の変更が重なる可能性があるため，247 の runtime 接続は 248 後を基本にする．246 と 247 は config/fixture と phase controller の編集範囲を分けられれば並列にできる．

stack のレビュー順の例：`244 → 245 → 248 → 246 → 247 → 252`．全件を最初から直列実装する指定ではない．

この回は fixture/mock による基盤確認まで．本番の共有探索と対戦 policy の接続は第 2 回に含める．人間は 246 の形状図・色条件・固定ツモでの成立例を確認し，レビュー結果を残す．形状レビュー待ちでも，独立した基盤の作業は続行できる．

### コピペ用依頼文

```text
PUYO-244，PUYO-245，PUYO-246，PUYO-247，PUYO-248，PUYO-252 を実施してください．
統合先は既存の integration/puyo-228-v1-8-0 です．チケット本文に残る旧統合先より，この指定を優先してください．

AGENTS.md と docs/development/codex_autonomous_workflow.md に従い，チケット別の子セッションと専用 worktree で，実装・必要な検証・Jira 更新・PR 作成まで進めてください．
244 の契約確定後に 245 と 248 を並列候補とし，245 後に 246・247，248 後に 252 を進めてください．247 と 248 の runtime 接続は原則直列とし，その他も同じファイル・契約へ影響する場合は親が順序を調整してください．

最後に親が各 PR の差分と組合せ QA を確認し，github/gh-stack で取りまとめてください．stack の最下段は上記統合ブランチ，後続は直前のチケットブランチを base にしてください．
レビュー順，stack/PR URL，検証結果，246 の形状図・成立例を含む人間の確認手順と残課題を提示してください．人間確認を含む A/C が未達なら，該当チケットを COMPLETE にしないでください．
reviewer は未指定とし，PR の merge・release は行わないでください．対象外チケットの実装へは範囲を広げず，このローカル依頼メモは commit に含めないでください．
```

## 第 2 回：探索・対戦経路・可視化・能力評価

| チケット | 実装内容と確認点 | Jira 上の直接の前提 |
| --- | --- | --- |
| [PUYO-249](https://shhchan.atlassian.net/browse/PUYO-249) | 固定総予算の共有探索 batch，6 戦術候補，要約・mask・順位．Python/native parity と合法 fallback | PUYO-244・245・248 |
| [PUYO-250](https://shhchan.atlassian.net/browse/PUYO-250) | 期限内相殺，最初のおじゃま落下後の一手 counter，短期攻撃候補．候補 coverage と費用を確認する | PUYO-248・249 |
| [PUYO-251](https://shhchan.atlassian.net/browse/PUYO-251) | rule selector と scheduler の実行経路．requested/executed と replay の整合性 | PUYO-247・249・250 |
| [PUYO-253](https://shhchan.atlassian.net/browse/PUYO-253) | 戦術・土台・遷移履歴・開始設定の GUI．ledger と表示一致，通常画面の人間 QA | PUYO-246・251・252 |
| [PUYO-254](https://shhchan.atlassian.net/browse/PUYO-254) | 採用・不採用双方の lineage と採用経路．明示した採用記録から表示する | PUYO-252 |
| [PUYO-255](https://shhchan.atlassian.net/browse/PUYO-255) | G0〜G4 の判定器と G1/G2 測定．候補不足・候補順位・戦術選択の失敗を分ける | PUYO-246・251・252 |

**順序と並列候補：** 中心は `249 → 250 → 251` の直列．254 は第 1 回の 252 が揃えばこの経路と並列に進められる．253 と 254 は `src/ui/model_viewer.py` と関連テストを共有するため，原則 `254 → 253` とする．251 後は 253 と 255 の実装を並列候補にできるが，G1 の最終確認には rule→ledger→GUI の接続済み成果が必要．評価計測中に同じ CPU/GPU を消費する重い検証は重ねない．

stack のレビュー順の例：`249 → 250 → 251 → 254 → 253 → 255`．254 の実装は先行可能でも，レビュー時にはこの位置へ積める．

ここで人間が実際の対戦画面・設定・履歴・lineage を確認できる．255 は判定器と必要な測定が正しく完成すれば，結果が FAIL/BLOCKED でもチケット成果は完了し得る．**255 の COMPLETE は G2 PASS を意味しない**．only240 の採用と既存の品質未達を両方引き継ぐ．

### コピペ用依頼文

```text
PUYO-249，PUYO-250，PUYO-251，PUYO-253，PUYO-254，PUYO-255 を実施してください．
統合先は既存の integration/puyo-228-v1-8-0 です．チケット本文に残る旧統合先より，この指定を優先してください．
前提となる PUYO-244・245・246・247・248・252 の成果と受け入れ条件を確認し，統合済みの最新 SHA から開始してください．前提未達の場合は依存作業を待機させ，独立した対象チケットは進めてください．

AGENTS.md と docs/development/codex_autonomous_workflow.md に従い，チケット別の子セッションと専用 worktree で，実装・必要な検証・Jira 更新・PR 作成まで進めてください．
249 → 250 → 251 は直列，254 はその経路と並列候補です．253 は 251 後，かつ model_viewer.py の競合を避けるため原則 254 後に進めてください．251 後の 253 と 255 は編集範囲と資源が独立する範囲で並列にし，G1 の最終確認は GUI 接続後に行ってください．

255 は能力・失敗分類・latency・学習費用を実測し，G2 の PASS/FAIL/BLOCKED と根拠を提示してください．only240 の採用や接続 smoke を品質合格に読み替えないでください．
最後に親が差分と組合せ QA を確認し，github/gh-stack で取りまとめてください．stack の最下段は上記統合ブランチ，後続は直前のチケットブランチを base にしてください．
レビュー順，stack/PR URL，検証結果，人間が通常 GUI・設定・履歴・lineage を確認する手順と残課題を提示してください．人間 QA が未実施なら該当 A/C を未達として残し，COMPLETE にしないでください．
reviewer は未指定とし，PR の merge・release は行わないでください．対象外チケットの実装へは範囲を広げず，このローカル依頼メモは commit に含めないでください．
```

## 第 3 回：学習コード・段階実験・採否

| チケット | 実装内容と確認点 | Jira 上の直接の前提 |
| --- | --- | --- |
| [PUYO-256](https://shhchan.atlassian.net/browse/PUYO-256) | 要約特徴から 6 戦術を出す bootstrap trainer，masked CE，checkpoint 互換検査 | PUYO-251・252 |
| [PUYO-257](https://shhchan.atlassian.net/browse/PUYO-257) | realtime PPO，variable-dt GAE，報酬 ledger，curriculum，resume と開始ゲート | PUYO-255・256 |
| [PUYO-258](https://shhchan.atlassian.net/browse/PUYO-258) | 段階学習，paired 評価，通常 GUI QA，採用/不採用/保留と lineage の記録 | PUYO-253・254・255・257 |

**順序：** `256 → 257 → 258` が基本．256 と 257 は actor/checkpoint と `agents/manager_ppo.py` を共有するため，実装は直列にする．258 の実験計画・資源見積りの準備は先行できるが，学習実行の前倒しとは区別する．stack のレビュー順も同じ．

第 2 回で G2 未達でも，256・257 のコードと許容された小規模の配線検証は進められる．258 は前提成果を確認後，ゲート未達の原因と再開条件を記録する．診断だけで全 A/C を満たしたことにはせず，未達条件が残る間は COMPLETE にしない．

258 の実行は，bootstrap pilot → 短期 PPO → G3 → 長時間学習 → paired 評価/G4 の順．[設計書 §17〜19](puyo-234-next-generation-model-design.md#17-テスト評価昇格ゲート) と 258 に従い，長時間学習の機械・CPU/thread・壁時計上限，G4 の数値条件は実測に基づく人間判断を記録してから進める．設計書の学習量は初期上限案であり，自動実行の予約ではない．

このため第 3 回の途中で資源条件や最終 GUI 確認の返答が必要になる場合がある．同じ依頼の続きとして条件・結果を伝えて再開できる．G2/G3 の原因改良が別チケットを要する場合は，追加の作業依頼が必要になり得る．

### コピペ用依頼文

```text
PUYO-256，PUYO-257，PUYO-258 を実施してください．
統合先は既存の integration/puyo-228-v1-8-0 です．チケット本文に残る旧統合先より，この指定を優先してください．
前のグループの成果・人間 QA・統合状態と，PUYO-255 の実際の gate artifact を確認して開始してください．

AGENTS.md と docs/development/codex_autonomous_workflow.md に従い，チケット別の子セッションと専用 worktree で進めてください．実装は 256 → 257 → 258 の順にし，258 の実験計画と資源見積りは先行して準備して構いません．
256・257 は実装・必要な検証・Jira 更新・PR 作成まで進めてください．G2 未達でも許容された配線 smoke は実施できますが，本学習の開始条件は満たしたと扱わないでください．
258 は設計書の段階ゲートに従って進め，長時間学習の前には実測 throughput に基づく機械・CPU/thread・時間上限の案を，G4 評価前には数値条件の案を提示し，記録済みの人間判断があれば引き継いでください．判断が未確定の実行だけを待機させ，準備可能な作業は完了してください．ゲート未達なら原因と再開条件を残し，対象外の改善実装には広げないでください．

最後に親が差分と組合せ QA を確認し，作成できた PR を github/gh-stack で取りまとめてください．stack の最下段は上記統合ブランチ，後続は直前のチケットブランチを base にしてください．
レビュー順，stack/PR URL，全 seed を含む実験結果，採否または保留理由，通常 GUI での人間の確認手順を提示してください．未実施の実験や人間 QA を明記し，未達 A/C があるチケットは COMPLETE にしないでください．
reviewer は未指定とし，PR の merge・正式採用・release は別途の人間判断に従ってください．このローカル依頼メモは commit に含めないでください．
```

## 参照先

- [AGENTS.md](../../AGENTS.md)
- [複数チケットの自律開発手順](codex_autonomous_workflow.md)：worktree，子への委任，`gh stack link`，履歴と組合せ QA の扱い．
- [次世代モデル設計](puyo-234-next-generation-model-design.md)：契約・機能・ゲートの詳細．統合先と Sprint の古い記述は上記の指定に読み替える．
- [次世代モデル実装バックログ](puyo-234-next-generation-backlog.md)：今回の Jira `Blocks` と一致する直接依存表．
- [PUYO-234](https://shhchan.atlassian.net/browse/PUYO-234)：全対象に関連する完了済み設計チケット．

各依頼時点の Jira と既存 PR を再確認し，このメモ以降の完了・変更を反映する．このファイルの作成時点では，対象 15 件の実装・ステータス変更・commit・push は行っていない．


## 2026-09-25 追記：連鎖構築・GUI 応答・窒息回避の再計画（初期起票）

今回の依頼では，チケット分割 → 別サブセッションで Ama 調査 → 追加起票とこのメモの更新までを行い，そこで停止する．改善実装・学習実行・PR 作成・merge は次回以降の依頼で扱う．初期起票と Ama 調査の両方を **gpt-6-astra / high 以上**へ委任する指定であり，初期起票子は `gpt-6-astra / high` を使用した．調査子は親が別途起動する．モデル指定の理由は，探索・定型・生存・実行性能を横断する設計判断が必要なためである．

前節の「第 1〜3 回」は作成当時の記録として保持する．2026-09-25 の Jira 確認では PUYO-244〜255・263・265 が `Complete`，PUYO-264・266 が `In Progress`，PUYO-256〜258 が `To Do` である．PR #160 までが既存の `integration/puyo-228-v1-8-0` へ merge 済みで，確認したリモート head の短縮 SHA は `c0c77d9`．後続作業時は最新 SHA を固定し直す．**旧第 1・2 回の完了項目を再実装せず，第 3 回の本学習より先に今回の探索・安全性の残差を評価する．** PUYO-266 の正式 G2（30 seed × 2 repeat）は未実施であり，PR merge や固定 3 seed の結果を G2 PASS としない．正式 G2 の実行・受入責務は 266 に残す．

### 初期起票した 5 件

全件が親 PUYO-228 の独立 Task，優先度 `High`，未割り当て．初期起票直後は全件 `To Do` であり，267 のみ調査子が着手時に `In Progress` へ移す．Task の作成 schema に独立した What / Why / How / A/C / References フィールドはないことを確認し，description 内の各節を埋めた．

| チケット | 成果と範囲 | 配置 | 初期の依存・関連 |
| --- | --- | --- | --- |
| [PUYO-267](https://shhchan.atlassian.net/browse/PUYO-267) | Ama の固定参照と現行実装を比較し，改善案を追加起票する．旧 PUYO-171・185 の解析，native 化，nextgen 接続との差分を調べる | Sprint 12（106） | 268・270・271 を Blocks．269，171，185，233，265，266 と Relates |
| [PUYO-268](https://shhchan.atlassian.net/browse/PUYO-268) | ペルシャ式の横 3/L 字反例を起点に，定型の形・色割当・将来完成可能性を保つ大連鎖探索を実装する | 優先バックログ，次回 Sprint 候補 | 267 後．245・246・263・266，270・271 と Relates |
| [PUYO-269](https://shhchan.atlassian.net/browse/PUYO-269) | GUI frame/input cadence と worker/IPC を測って原因を分離し，測定に基づく改善を行う．大きな対策は追加 Task へ分割する | 優先バックログ，次回 Sprint 候補 | 267 の結論待ちは必須でない．161・162・264・265・267・271 と Relates |
| [PUYO-270](https://shhchan.atlassian.net/browse/PUYO-270) | 窒息を避ける実行可能な候補を確保・優先し，必要な単発消しを生存例外として扱う | 優先バックログ，次回 Sprint 候補 | 267 後．250・251・239・266，268・271 と Relates |
| [PUYO-271](https://shhchan.atlassian.net/browse/PUYO-271) | 新しい反例 corpus・baseline の固定と改良後の比較証跡を整備する．正式 G2 実行は 266 が引き続き担当 | 優先バックログ，次回 Sprint 候補 | 267 後．268・269・270・255・264・266 と Relates |

ペルシャ式の報告は，底から y=1，左から x=1 として同色の `(1,1),(2,1),(3,1)` を目指すところ，`(1,1),(2,1),(1,2)` の L 字ができ，`(3,1)` の補充で意図せず 4 消しとなる事例である．正確な seed/profile は未提供のため，座標を固定した反例から原因を確かめる．L 字一般や単発消し一般を禁止する仕様にはしない．将来の戦術選択は RL 化する方針を保ち，現時点の生存例外を許容する．

### 暫定対応順序と依頼の区切り

1. **今回：PUYO-267 の調査と追加起票まで．** 初期 268〜271 を再取得し，Ama 調査で得た具体的な探索・評価・高速化案を既存 Task の具体化または独立 Task にする．実際の Jira リンクを read-back し，この追記の後に確定した対応順序を追記して停止する．
2. **次回候補 A：再現条件と baseline の固定．** PUYO-271 の前半で反例 corpus を固定し，PUYO-269 の第 1 段階で通常 GUI cadence を測る．両者の重い実測は CPU を共有するため直列にする．これは 271 全件完了を待つ指定ではなく，変更前に測定条件を受け渡す区切りである．
3. **次回候補 B：生存と定型探索．** 初期案は `PUYO-270 → PUYO-268`．生存例外を先に固め，定型保持制約を接続する．shared search/selector/native 契約の競合を避けて直列を基本とする．Ama 由来の追加 Task は 267 が依存を確定してこの間へ挿入する．269 の GUI 固有修正は独立性を確認できれば並列候補だが，backend/IPC/diagnostics 契約を変える場合は直列にする．
4. **次回候補 C：統合比較と既存品質ゲート．** 対象改善の統合後に PUYO-271 の後半比較を行い，PUYO-264・266 の未達 A/C と正式 G2 を再評価する．271 の前半/後半に双方向 Blocks を張って循環させない．人間 GUI QA と品質条件を満たす前に G2 PASS／学習 Go としない．
5. **その後：既存 PUYO-256〜258．** コード検証と本学習の開始条件を区別し，上記改善・測定結果を踏まえて前節第 3 回の依頼範囲を見直す．今回の追加起票だけで学習を開始しない．

現時点の Jira の強制前提は `267 → 268 / 270 / 271` の 3 本である．`270 → 268` は共有ファイルの実装順の初期案であり，追加調査によって変わり得るため今は Relates としている．269 は原因によって別の対策 Task を起票し，その時点で依存関係とこのメモを更新する．PUYO-264/266 は未達を保持し，新規 Task と循環する完了待ちを作らない．

### 今回の Ama 調査子への依頼文

```text
PUYO-267 を gpt-6-astra high 以上の別サブセッションで実施してください．
対象は Ama の再調査，現行 deep_chain_builder／nextgen_safe_build との差分整理，必要な追加の実装・検証 Task の Jira 起票，依存関係とこのローカル依頼メモの更新までです．
PUYO-171・185 の旧 pinned 調査と，PUYO-264〜266 の最新成果・未達条件，初期 PUYO-268〜271 を先に確認して重複を避けてください．
調査結果から追加起票すること，既存初期 Task を具体化すること，追加分を含む対応順序・並列可否・次回依頼文をこのファイルの末尾へ追記することを成果に含めてください．
baseline は deep_chain_builder とし，ペルシャ式の形と色配置・将来完成可能性，連鎖の候補生成と順位，窒息回避，探索速度と GUI cadence を分けて根拠を示してください．
実装や本学習へ進まず，調査と起票が揃った時点で停止して報告してください．このファイルの既存部分を保持し，git add／commit に含めないでください．
```


## 2026-09-25 追記：PUYO-267 調査結果と確定した実施順序

PUYO-267 は `gpt-6-astra / high` の調査子が実施した．成果は source 比較，PUYO-268〜271 の具体化，新規 [PUYO-272](https://shhchan.atlassian.net/browse/PUYO-272) の起票，依存関係と本メモの更新である．製品コード・学習・PR・merge は実施していない．前の「暫定対応順序」は以下で具体化する．

### 固定した参照と比較の限界

- Ama：2026-09-25 に `https://github.com/citrus610/ama.git` を新規取得．main HEAD は `dea210bcd92965ae08fbc311f23565b0fab6dbbb`，commit 日時は 2025-08-02T17:27:24Z，件名 v2.0.1．**PUYO-171/185 の旧 pinned commit と同一で，git diff は空**だった．Ama 側の新機能が増えたという前提は置かない．ローカル読取 clone は `/tmp/puyo-267-ama-20260925`．
- 現行側：`c0c77d944ff3ed276a4b44a40a779cd72bc0977a`，worktree `/home/sion2/workspaces/puyo-nextgen-20260925/267`，branch `PUYO-267/ama-current-diff`．baseline は `deep_chain_builder reference/native/target10`．`nextgen_safe_build` も既に depth16/width250/6 future/shared600000 で，追加 template128/response256 は独立 quota．
- Ama は [MIT](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/LICENSE)，Copyright (c) 2023 citrus610．既存 native/NOTICE と LICENSE-AMA-MIT は scalar pop-mask 派生の帰属を保持している．派生コードを追加する場合も出典と適用範囲を追記する．今回コードのコピーはない．
- Ama の同条件 build/対戦/速度測定は未実施．公開ソースによる機能比較と，既存ローカル測定の再読を区別する．「Ama の方が強い・速い」の倍率や勝率は未証明．CPU を占有する実験は行わず，合成盤面の軽量確認だけを行った．
- 両者の通常ツモは 4 色だが色 ID 順が異なる．Ama は 13 行の色盤面＋14 行目の占有 bit，現行は visible12＋hidden1＋ghost1 の色付き状態である．到達可能性，おじゃま位置，発火/終了/攻撃時刻，既知ツモの個数を同値確認せずに transition や重みを移植しない．Steam client は公開されていないため UI 実装比較はできない．

### ソースから確認した差分と採否

以下の Ama リンクは全て固定 SHA．現行の行番号は上記 `c0c77d9` に属する．

| 観点 | Ama の一次ソース | 現行側の確認結果・判断 |
| --- | --- | --- |
| bitfield/SIMD | [core/fieldbit.cpp](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/core/fieldbit.cpp#L103)，[field.h](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/core/field.h#L9)，makefile:12 | 現行 compact.rs は 3 bit-slice/u128 と scalar pop-mask，chain_structure.rs:1212/1294/2938 等には CPU 検出付き BMI2/PEXT 最適化がある．「現行は全て Python」ではない．Ama の SSE 前提全移植は見送り．GUI 計測前に SIMD を必須タスク化しない． |
| 候補生成・解決 | [core/move.cpp:7](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/core/move.cpp#L7) は高さ/row14 に基づく最大22配置と同色組の対称削減．beam.cpp:25–29 は pop 後に中央高さで死判定 | 現行 compact と authoritative simulator の設置/消去/ghost/終了 semantics，さらに realtime reachable mask がある．Ama の配置 ID や中央高さ閾値をそのまま適用しない．生存不足は PUYO-270 で公開既知手・実到達・消去後を確認する． |
| beam / best-first | [beam.cpp:43](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/beam/beam.cpp#L43)，[layer.cpp:19](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/beam/layer.cpp#L19)，[dfs/build.cpp:101](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/dfs/build.cpp#L101) | 主経路は層ごとの heap bounded beam と既知キュー DFS．README の best-first を独立した global best-first engine が常時動く意味には採用しない．現行も diverse/root survivor beam を既に持つ．探索方式の全面置換は不要． |
| TT | [table.cpp:50](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/beam/table.cpp#L50) は短い hash と value/age 置換．layer clear で age を進める | 現行 long_horizon.rs:1022–1060 は完全 SearchStateKey を比較する collision-safe table．Ama の短い fingerprint を根拠に既存 parity を緩めない．永続 subtree/TT 再利用は未証明で見送り，既存 exact request cache は再実装しない． |
| 複数未来・集約 | [beam.cpp:267](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/beam/beam.cpp#L267) は固定6補完，6 thread，root ごとの最大 score の合計．[search.cpp:32](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/search.cpp#L32) では既知 queue>2 のとき単一 beam 経路 | 現行 reference/safe_build は legacy-fixed-six と6 scenario，ただし class/support/coverage/continuation の順位，target10 と only240 guard を持つ．同じ d/w/scenario 数でも同じアルゴリズムではない．単純な期待 score 集約への逆戻り，旧 PUYO-240 の重複起票はしない． |
| quiescence/評価 | [quiet.cpp:9](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/beam/quiet.cpp#L9)，[eval.cpp:38](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/beam/eval.cpp#L38) | Ama は同一列・同色を最大3個追加して発火点を調べ，chain/trigger height/key/extension space/残連結/shape/tear/waste を評価．現行は複数列の bounded quiescence，trigger protection/damage，fatal floor 等を既に持ち，weights も異なる．過去 PUYO-241 の危険重み倍増は退行を伴い不採用，242 も既定不採用．根拠なしの重み移植/予算増はせず，まず不足している定型制約をつなぐ． |
| 定型 | [eval.cpp:17](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/beam/eval.cpp#L17)，[form.cpp:10](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/beam/form.cpp#L10)，[form.h](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/beam/form.h) | Ama は各 node で GTR/FRON/SGTR の最大 soft score を加算する．固定された1定型/binding の保持保証ではなく，ペルシャ実装も確認できない．現行は phase 固定 binding を持つが長期 backend へ渡さず，最初の進捗 witness と局所 tail 点を使う．**node 内へ制約を通す考え方を PUYO-272，形状/採用経路の修正を 268 に採用**する． |
| 窒息・防御 | [dfs/build.cpp:64](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/dfs/build.cpp#L64)，[ai.cpp:564](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/ai.cpp#L564)，gaze.cpp | Ama は死/dead-end を除外し，防御時の desperate return を持つ．現行 selector:101–117 の小攻撃は相手への攻撃条件であり，無予告の自己窒息救済を明示していない．fatal_rate は展開死亡頻度で生存確率ではない．**PUYO-270 の bounded survival witness を優先**し，将来 RL の通常戦術判断を固定状態機械へ置換しない． |
| 実行・GUI | [search.cpp:20/74](https://github.com/citrus610/ama/blob/dea210bcd92965ae08fbc311f23565b0fab6dbbb/ai/search/search.cpp#L20)，現行 realtime_ai.py:865/911/978，realtime_versus_ui.py:1178–1185/1708–1720，long_horizon.rs:1655–1682 | 現行は process executor，6 native worker，snapshot validation/cache を持つ．GUI 側 copy/validation/render，最大12 tick catch-up，speculative work/quota rerun は原因候補．実 frame trace は未測定なので断定しない．**PUYO-269 で測定して支配的経路を修正**する． |

現行ソースの固定リンク：[template matcher](https://github.com/shhchan/puyo-ai-dev-platform/blob/c0c77d944ff3ed276a4b44a40a779cd72bc0977a/agents/template_catalog.py#L963)，[shared backend request](https://github.com/shhchan/puyo-ai-dev-platform/blob/c0c77d944ff3ed276a4b44a40a779cd72bc0977a/agents/nextgen_shared_search.py#L385)，[rule selector](https://github.com/shhchan/puyo-ai-dev-platform/blob/c0c77d944ff3ed276a4b44a40a779cd72bc0977a/agents/nextgen_tactic_manager.py#L101)，[native scenario execution](https://github.com/shhchan/puyo-ai-dev-platform/blob/c0c77d944ff3ed276a4b44a40a779cd72bc0977a/native/deep_chain_native/src/long_horizon.rs#L1655)．

### 合成反例と既存実測の扱い

`train/config/nextgen_templates.yaml:43` のペルシャは `AAACCC / .BCB.. / ..BB..`，empty_cells は空．`.` は matcher:599–644 で無制約となる．A=1/B=2/C=3 とした以下の合成盤面を既存 matcher と GameState で確認した．

| 補充前の A の座標（1始まり，底から） | 静的 satisfied / conflicts | current(A,B) を x=3 に縦 UP で置いた後 |
| --- | --- | --- |
| (1,1),(2,1) | 2 / 0 | chain0，底面の A 横3が残る |
| (1,1),(2,1),(1,2) | 2 / 0 | chain1，A が4連結で消えて B だけ残る |

実行証跡は `/tmp/puyo-267-persian-check.json`．元の user seed/profile/receipt は未提供であり，その GUI 対局を完全再現した結果ではない．初回の補助スクリプトでは Direction 型と wire/internal 行向きの取り違えを修正し，上表は底基準で確認し直して assertions が成功した最終値．製品修正はしていない．再現は次の短いコードで確認できる（対象 checkout の依存がある Python を使う）．

```python
from agents.template_catalog import load_template_catalog, _conditions, _evaluate, _game, _wire
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, Direction
catalog = load_template_catalog("train/config/nextgen_templates.yaml")
variant = next(t for t in catalog.templates if t.id == "persian").variants[0]
for points in ([(1, 1), (2, 1)], [(1, 1), (2, 1), (1, 2)]):
    board = [[0] * GRID_WIDTH for _ in range(GRID_HEIGHT)]
    for x, y in points:
        board[y - 1][x - 1] = 1  # internal board is bottom-up
    satisfied, conflicts = _evaluate(board, _conditions(variant, "identity"), {"A": 1, "B": 2, "C": 3})
    game = _game(board, (1, 2))
    result = game.place_current_pair_and_resolve(2, Direction.UP, spawn_next=False)
    print(len(satisfied), conflicts, result["chain_count"], _wire(game)[:3])
```

性能の新規計測はしていない．既存 PUYO-265 の native policy p50/p95=0.232/0.366 s は旧 d4/w4/scenario1 の18サンプルであり，現行 safe_build の frame cadence ではない．PUYO-266 paired-final は source `851b02fc9ea7e0c4afdaa260ebfc727b316ac023`，seed55/123/124 各1 run，nextgen 最大実連鎖12/10/10，baseline1/10/10，decision p95=0.865/0.651 s である．この3 seed の結果と before-cache の2 repeat を区別し，今回 c0c77d9 の新規測定や正式 G2 の代替にはしない．詳細は既存 `docs/benchmarks/puyo-266-safe-build/`．

### Jira の確定範囲・リンク

新規実装 Task は **PUYO-272 の1件**．268〜271 は既存 description を保持して根拠・境界・追加 A/C を追記した．全実装 Task は親 PUYO-228，未割り当て，High，To Do，Sprint 未設定の優先バックログ（次回 Sprint 候補）を read-back した．267 のみ Sprint12 の調査タスクとして着手した．

| Task | 確定した所有範囲 | 論理上の Blocks |
| --- | --- | --- |
| [PUYO-270](https://shhchan.atlassian.net/browse/PUYO-270) | 無予告の自己窒息も含む生存候補・優先理由・receipt，6戦術/将来 RL との安全境界 | 267 → 270 → 268 |
| [PUYO-272](https://shhchan.atlassian.net/browse/PUYO-272) | optional selected-template request/result，Python/native 内の制約評価・複数整合 root・TT/cache/schema/ABI parity．合成制約で単独検証 | 267 → 272 → 268 |
| [PUYO-268](https://shhchan.atlassian.net/browse/PUYO-268) | ペルシャ等の catalog 保持条件，最初の witness 偏りの修正，272 長期 root 順位と270生存例外を phase/candidate/実採用へ統合 | 267・270・272 が前提 |
| [PUYO-269](https://shhchan.atlassian.net/browse/PUYO-269) | GUI cadence 実測，原因に対応する改善．大規模 native/IPC 対策が必要なら証拠付きで追加分割 | 267とは Relates．探索側の全完了待ちではない |
| [PUYO-271](https://shhchan.atlassian.net/browse/PUYO-271) | 前半：変更前 corpus/baseline 固定．後半：各成果の統合比較 | 267が前提．268/269/270/272とは Relates＋部分成果引渡し |

272 には 270/271/245/265/266 への Relates を追加した．270→268 は単なる作業順ではなく，268 が生存例外契約を消費するための依存．270 と 272 の間に Blocks はない．既存の270–268 Relatesは関連範囲を示す履歴として併存し，削除必須の誤リンクはない．新しい G2 実行 Task は作成しておらず，正式 G2 30 seed×2 repeat と未達受入は **PUYO-266**，通常 GUI の残確認は **PUYO-264/266** に残る．

### 確定した進め方と並列可否

1. **今回の終点：267 の調査・起票・メモ更新で停止．** 268〜272 は開始しない．
2. **次回の最初：271前半と269第1段階．** 反例/corpus/seed/config/host を変更前に固定し，GUI trace を採る．ファイルの調査・設計は並列可，CPU 負荷のある測定は直列．271 は前半だけで Complete にしない．
3. **実装は原則 270 → 272 → 268．** 270と272は論理的には独立だが，candidate/backend/safety 共有契約の変更を直列にレビューする．272 は合成制約で完結し，268 が production catalog と policy に接続する．269 の純 GUI 修正は競合がないと親が確認した範囲だけ並列可．native/IPC/scheduler 共通変更がある場合は直列．
4. **271後半の統合比較 → 264/266 の残 A/C・正式 G2．** 比較は全seed/失敗/未実施と品質・decision latency・frame cadence を分離する．G2 を新チケットへ移さない．人間 GUI QA 未実施や品質未達を残したまま Complete/G2 PASS にしない．
5. **256→257→258 の学習系はその後に再判断．** 許容される実装/配線 smoke と本学習の開始条件は分ける．今回の調査や272追加だけで学習 Go としない．将来の通常戦術判断は RL 化し，270 の生存例外を安全境界として保つ．

### 次回のコピペ用依頼文

```text
PUYO-268，PUYO-269，PUYO-270，PUYO-271，PUYO-272 を対象に，今回確定した計画に従って実装・必要な検証・PR 作成まで進めてください．統合先は既存 integration/puyo-228-v1-8-0 です．最初に Jira 本文と依存関係，既存 PR，最新の統合 SHA，AGENTS.md，codex_autonomous_workflow.md を確認してください．

チケット別の子セッションと専用 worktree を使い，親が共有契約・CPU・native build の順序を管理してください．まず PUYO-271 前半で合成ペルシャ横3/L字・窒息・3定型の反例 corpus と deep_chain_builder reference/native/target10 baseline を固定し，PUYO-269 第1段階で GUI frame/input/event/simulation/IPC/worker の区間測定を行ってください．重い実測は並走させないでください．271 前半だけで Complete にせず，部分成果を後続へ渡してください．

探索側は原則 270 → 272 → 268 の順にしてください．270 は到達可能な生存候補と必要な単発消しの理由契約，272 は選択定型を保持する Python/native 長期探索基盤，268 は catalog/phase/候補順位/実採用への接続を担当します．272 の backend と268の接続を重複実装せず，270の生存例外を定型保持より優先してください．単なる L 字一般・小連鎖一般の禁止や，全 '.' の空セル化では対処しないでください．269 の修正は測定で支配的と分かった原因に限定し，共有 API に触れなければ独立作業を並列化できます．

271 後半で改善後を同じ公開入力・node予算・host条件で比較してください．品質，候補不足/順位誤り，premature/生存例外，decision p50/p95，frame cadence を分け，全seedと未完了理由を保存してください．Ama の優位性は同条件未実測であり，既存速度数値を現行GUI測定へ流用しないでください．正式 G2 30seed×2repeat の所有先は PUYO-266 のままです．今回の比較を G2 PASS に読み替えず，264/266 の残 A/C への対応と次の実行手順を報告してください．本依頼に 256〜258 の学習実行は含めません．

最後に親が差分・組合せ QA・依存関係を確認し，作成できた PR を github/gh-stack とレビュー一覧で取りまとめてください．最下段は上記統合ブランチ，後続は確認した直前ブランチを base にしてください．reviewer は未指定，merge/release は行わず，人間GUI QA や未達 A/C を明示してください．このローカル依頼メモは既存部分を保持し，git add/commit に含めないでください．
```

調査完了記録：PUYO-267 のセッションコメントは [10750](https://shhchan.atlassian.net/browse/PUYO-267?focusedCommentId=10750)．調査 A/C を満たしたため `Complete` へ遷移し，`完了/Done` には遷移していない．268〜272 は未着手 `To Do` を維持し，今回の作業をここで停止する．

## 2026-09-25 追記：PUYO-269 の GUI 実測から分割した後続

今回の実装一括依頼では PUYO-268〜272 を対象とする．PUYO-271 前半の変更前 corpus は [draft PR #162](https://github.com/shhchan/puyo-ai-dev-platform/pull/162) に固定し，PUYO-269 の GUI 計測をその後に直列実行した．PUYO-269 の [draft PR #161](https://github.com/shhchan/puyo-ai-dev-platform/pull/161) は，ライブ表示で未使用の full replay/diagnostics を毎 tick 生成していた費用を削減する．seed 55，実 `DISPLAY=:0`，片側 nextgen の frame p95 は 184.5 → 58.8 ms，入力 p95 は 243.6 → 76.6 ms となったが，測定前に固定した 25 ms gate は未達である．人間 GUI QA も未実施のため，269 は `In Progress` のままとする．

残る UI thread の同期 `nextgen_authoritative_action_mask` 計算は 20 呼出で p50/p95=75.5/81.0 ms と観測された．共有 scheduler／到達可能性契約の対策として [PUYO-273](https://shhchan.atlassian.net/browse/PUYO-273) を独立 Task に起票し，`273 Blocks 269` を確認した．PUYO-273 は今回指定された 5 チケットの実装対象に自動追加しない．269 の完了条件を満たすには，273 の対策，同条件の cadence 再測定，人間 GUI QA が必要である．269 の条件・集計・元出力 11 本は [証跡](https://github.com/shhchan/puyo-ai-dev-platform/tree/PUYO-269/gui-cadence/docs/benchmarks/puyo-269-gui-cadence)に保存し，`python docs/benchmarks/puyo-269-gui-cadence/aggregate.py` で整合確認した．元計測に per-frame 個票はないため，percentile を独立再計算できない限界も記録した．

次回以降の順序は，今回の `271 前半 → 269 第 1 段階 → 270 → 272 → 268 → 271 後半` を維持し，GUI cadence の未達は **273 → 269 残 A/C** として別経路で追う．273 が共通 scheduler／planner を変更するため，272／268 と同じ契約へ触れる場合は，親が編集範囲・PR base・CPU 測定順を再確認してから着手する．正式 G2 の所有先は引き続き PUYO-266 である．

## 2026-09-25 追記：PUYO-268〜272 の実装と統合比較

統合先 `integration/puyo-228-v1-8-0` の SHA は作業前後とも `c0c77d944ff3ed276a4b44a40a779cd72bc0977a`．専用 worktree から作成した 6 PR を，履歴書換えなしの通常 merge と `github/gh-stack link --base integration/puyo-228-v1-8-0` で **stack #167** に登録した．レビュー順は [#162：271 前半 corpus](https://github.com/shhchan/puyo-ai-dev-platform/pull/162) → [#161：269 GUI 部分改善](https://github.com/shhchan/puyo-ai-dev-platform/pull/161) → [#163：270 生存例外](https://github.com/shhchan/puyo-ai-dev-platform/pull/163) → [#164：272 定型長期探索 backend](https://github.com/shhchan/puyo-ai-dev-platform/pull/164) → [#165：268 production 接続](https://github.com/shhchan/puyo-ai-dev-platform/pull/165) → [#166：271 後半比較](https://github.com/shhchan/puyo-ai-dev-platform/pull/166)．全 PR は open，reviewer 未指定，auto-merge なし．merge／release は実施していない．

271 後半の [比較証跡](https://github.com/shhchan/puyo-ai-dev-platform/tree/PUYO-271/integrated-comparison/docs/benchmarks/puyo-271-regression/after)は，同一 host・公開ツモ生成器・target 10／depth 16／width 250／6 scenario・nextgen shared 600000／template 128／response 256 で seed 55/123/124 を各 1 回，最大 40 実設置で比較した．変更前→変更後の nextgen 最大実連鎖は **12/10/10 → 0/10/11**．seed 55 は GTR を 12 実設置で完成したが，その後発火できず 35 実設置で窒息した．初回 14 手内の GTR 成立は 2/3．モデル品質の改善や G2 PASS は未確認ではなく **未達が観測された**．272 の制約単体比較でも合成盤面の最大探索連鎖が 8→6／7→4 に下がる場合がある．

GUI の最終実測は同じ `DISPLAY=:0`／seed 55／60 FPS 上限で片側 600 frame と両側 360 frame を直列に測り，frame／input 個票を保存した．片側 frame/input p95 は 62.5/67.4 ms，両側は 151.2/208.9 ms で，事前固定の p95 ≤ 25 ms／p99 ≤ 50 ms gate は未達．人間の GUI 操作・視認 QA は未実施．PUYO-269 は draft／In Progress，PUYO-273 が Blocks．PUYO-268 も一般品質未達のため draft／In Progress．PUYO-270，272，271 は各担当 A/C を満たして Jira Complete とし，Done にはしていない．

次の実行順は **PUYO-266 の完成後 `build_main`・理由なき早消し・窒息の修正と同条件再測定 → PUYO-273 の UI thread 到達性処理対策 → PUYO-269 の cadence 再測定と人間 GUI QA → PUYO-264 の通常速度／`n` 低速ステップ差と 3 列目縦積みの確認**．PUYO-268 は 266 の品質修正後に一般品質 A/C を再判定する．正式 G2 30 seed×2 repeat は PUYO-266 が所有し，今回の 3 seed×1 の結果を代用しない．PUYO-256〜258 の学習実行は今回行っていない．親の before／after manifest 再検証と最終 head の組合せ 81 tests は成功した．このローカルメモは未追跡のまま保持し，どの PR にも含めていない．

## 2026-09-27 追記：人間 GUI QA 後の新セッション用依頼文

新しいセッションを UI／起動時に `gpt-6-sol`／`high` に設定し，以下のブロックだけをそのまま貼る．過去の Jira 状態，PR head，測定値は記録時点の値なので，実行時に再取得する．この追記自体は起票・実装・正式 G2 の実行を意味しない．

### コピペ用依頼文

~~~text
このセッションの親モデル／推論が `gpt-6-sol`／`high` として選択されていることを，利用可能な表示・設定から確認し，確認できない場合は実際の設定と制約を記録してください．

PUYO-264・266・268・269 の人間 GUI QA を踏まえ，残課題の調査とチケット起票から始め，対象となった改善チケットの実装・検証・PR 作成まで連続して進めてください．起票後に停止しないでください．あなたは親オーケストレーターです．AGENTS.md と docs/development/codex_autonomous_workflow.md に従い，親は調査結果・チケット境界・依存・worktree・排他資源・PR stack・組合せ QA を管理し，重い調査・実装・計測はチケット別の子セッションに委任してください．子へは必要な文脈と参照先だけを渡し，再帰委任はさせないでください．利用可能なモデルと推論レベルを確認し，明確な実装は標準コードモデル／medium，複数層の原因究明や設計判断は高能力モデル／high を初期候補として，実際の選択と理由を実行表に記録してください．安易に low へ落とさず，親が短く確認できることまで毎回委任して文脈転送を増やさないでください．

最初に Jira 接続を確認し，PUYO-264・266・268・269・270・271・272・273 と親 PUYO-228 の本文，A/C，現在の状態，Sprint，親・Blocks・Relates，既存コメントと PR を読み直してください．2026-09-25 のメモでは 264/266/268/269 は In Progress，273 は To Do，270/271/272 は Complete でしたが，現在値を優先してください．git status，worktree，integration/puyo-228-v1-8-0 の最新 SHA，open PR，既存 stack #167（#162→#161→#163→#164→#165→#166）の base/head と差分を確認してください．既存 PR や未コミットのユーザー変更を上書きしないでください．統合先は integration/puyo-228-v1-8-0 であり，master へ直接向けません．

今回の人間観察を条件付きの証拠として記録してください．PUYO-264 では縦置きの繰り返しは観測されず，通常速度とゆっくり n を押す／0.25x で配置方針は基本的に同じでした．random 相手のおじゃまで盤面と選択手が変わった点は，速度による tick 数・思考時刻・ツモ回数の差もあり得るため，直ちに速度依存バグとは断定しないでください．PUYO-266 では無理由の土台破壊も，発火不能のまま窒息する場面も今回は観測されませんでした．相手の単発消しやおじゃまへの応答で土台を崩す場面はありました．これらの観測と既存の機械評価で出た失敗を，条件・実装 SHA・観測範囲を分けて保持してください．

PUYO-268 の人間観察では gtr/daa/persian とも概ね定型土台を組めました．PLAYER2=human，速度 0.25x で，gtr は 10 連鎖→セカンド 10 連鎖→8 連鎖，daa は 12 連鎖→だぁ積み下 2 段の L 字 4 個を作り始めてから約 6 連鎖，persian は 13 連鎖→13 連鎖→13 連鎖→6 連鎖を観測しました．persian と random 相手の 0.25x でも，おじゃまを受けつつ 11 連鎖の本線を確認しました．persian と random 相手の通常速度では，初回の相手の 5 連結単発消しに対して「相殺」を選び，大きく土台を崩して攻撃し，相手自滅で終了しました．相手 human／random と 0.25x／通常速度を混ぜて集計せず，連鎖の観測を正式 G2 PASS や全条件での品質合格に読み替えないでください．

改善候補として，gtr の seed 123 で通常の GTR としては珍しい膨らんだ地形へ積極的に向かった理由を，局面・配置列・候補順位・選択戦術・定型進捗・発火見通しから再現・調査してください．別の観察では，セカンド 10 連鎖が GTR 折り返しを経由しない逆発火となり，構築した GTR 部分が消えませんでした．このセカンドの seed は報告から確定できないため，seed 123 の地形事象と同一 run と決めつけないでください．有効な逆発火を一律禁止せず，構築資産の利用・残存形・本線評価を確認し，望ましくない膨らみや未使用土台を抑える狭い改善を設計してください．観測 seed だけへの特例や根拠のない重み調整を避けてください．daa 初回本線が左側 L 字 2 個にとどまったテンプレート形状の細部は，今回の改善対象から除外してください．

PUYO-269 の人間 GUI QA では軽量基準は滑らかで tick 表示も飛びませんでした．片側 nextgen は AI の操作直前付近でカクつき tick が飛び，両側 nextgen は両プレイヤーでより頻繁に起きました．1P nextgen 対 2P human では，キー入力は受け取られていそうでも，押しっぱなしの下入力と横・回転を組み合わせた操作で意図した場所へ置けず，操作感が非常に悪いとの報告です．既存 PUYO-273 の UI thread 同期到達可能性処理と 269 の責務を先に確認し，実 GUI の frame/input/tick とイベント区間を再測定して原因を分けてください．軽量・片側・両側・human 操作を別条件とし，遅延分布，入力から反映までの時間，欠落／過剰入力の有無，tick catch-up を調べてください．入力の正しさを見た目だけで断定しないでください．既存の p95 ≤ 25 ms／p99 ≤ 50 ms gate と人間操作の A/C を維持し，未達なら 269 を COMPLETE にしないでください．

起票前に各懸念が既存 264/266/268/269/273 の A/C で処理できるか調べ，重複を避けてください．既存チケットで担える範囲は本文・A/C・コメントへ追記し，新規 Task は独立して調査・実装・検証できる未所有の改善単位に絞って起票してください．特に GTR 地形と逆発火が同じ原因か別原因かを調査して適切に分けてください．GUI は 273 と 269 を優先し，大きな独立原因が実測で判明した場合だけ追加 Task にしてください．新規 Task には再現条件，調査事項，実装する契約・変更範囲，観測可能な A/C，回帰 seed と反例，性能予算，参照証跡，親 PUYO-228，優先度・Sprint 判断，Blocks/Relates を具体的に記入して read-back してください．新規と既存の完了境界を明示してください．今回の QA に基づく既存チケットの状態更新は満たした条件だけ行い，未実施の正式測定や GUI 操作確認が残るものは In Progress のままにしてください．

起票した対象と既存の残作業は，チケット別の専用 worktree・ブランチと子セッションで依存順に連続実行してください．親が対象・起点 SHA・PR base・共有ファイル／API・排他資源を表に固定し，依存する子は先行 PR の検証済み head を起点とします．GTR の探索・選択契約と 266 の品質修正，273 の scheduler／到達可能性処理と 269 の GUI 計測は重なる可能性があるため，調査結果に応じて直列化してください．GUI を占有する確認と CPU の重い探索・native build・正式 G2 を並走させないでください．各子は担当 Jira の進行中遷移，調査・実装・最小十分な検証，明示したファイルだけの commit・push，指定 base の PR と 1 セッション 1 コメントを担当します．未達 A/C が残れば draft／In Progress とし，COMPLETE／Done や品質合格を装わないでください．

再測定は seed 55/123/124 の既存 baseline，gtr seed 123 の地形，別途観測した GTR セカンド，3 定型，human/random 相手，通常速度/0.25x／低速 n を，条件を混ぜずに比較してください．同じ公開ツモ・盤面・予算・host・source/config SHA で前後を比較し，AI の判断差と相手のおじゃま／tick 数による状態差を分離してください．候補不足・候補順位・実採用・発火・生存・定型部の利用・decision latency・GUI cadence を必要な範囲で記録してください．機械評価で既知の seed 55 窒息と初回 GTR 成立 2/3，今回の GUI 観察での窒息非観測は両方保持し，片方で他方を消さないでください．正式 G2 の 30 seed × 2 repeat と残る品質 A/C の所有先は PUYO-266 のままです．3 seed の比較や今回の GUI 連鎖数だけでは G2 PASS にしないでください．PUYO-256〜258 の本学習は G2 等の開始条件が満たされるまで開始しないでください．

親は各 PR の head・base・差分と組合せ QA を確認し，レビュー可能な複数 PR を github/gh-stack で整理してください．最下段は integration/puyo-228-v1-8-0，後続は実際の直前チケットブランチを base にし，履歴を書き換えず，各層の差分を確認してください．既存 stack #167 を無条件に変更・積み増しせず，状態と依存を確認して適切な stack を決めてください．reviewer は指定せず，PR の merge／release は行わないでください．最後に起票・更新した Jira，実行表，PR/stack URL，変更ファイル，個別・組合せ検証，GUI 再現手順，未達 A/C，正式 G2 の状態と人間に必要な確認を一括提示してください．このローカルメモは git add/commit/PR に含めないでください．
~~~
