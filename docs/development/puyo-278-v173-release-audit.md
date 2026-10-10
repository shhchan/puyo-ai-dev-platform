# PUYO-278 v1.7.3 release 監査

2026-10-10 の再監査．Sprint 15 の受け入れと統合 QA が未完了のため，release PR／merge／tag は保留する．この文書の SHA と状態は release 判断の直前に再取得する．

## 起点と対象履歴

| 対象 | 確認値 |
| --- | --- |
| `origin/master`／`v1.7.2^{}` | `3defcc2b8c6f42824102166aa7011527dfd4fb9e` |
| `origin/integration/puyo-228-v1-7-3` | `7757f2312f34a1d79593f43ebd4f41921c61f0a6` |
| 旧 `origin/integration/puyo-228-v1-8-0` | `7757f2312f34a1d79593f43ebd4f41921c61f0a6` |
| merge base (`master` と候補) | `3defcc2b8c6f42824102166aa7011527dfd4fb9e` |

親セッションが旧統合 branch の head を履歴保持で新しい v1.7.3 統合 branch に作成し，remote を read-back した．両 branch の tree は一致する．旧 v1.8.0 branch は保持して使用を停止する．削除・再作成・force-push はしない．v1.8.0 の後続統合は，v1.7.3 release 後の `master` を起点に親が決める．PUYO-279 は Sprint 16 の設計・実装 Task 起票を担当し，この release 候補へ runtime 変更を混ぜない．

`master..7757f23` は 217 commit，first-parent 6 merge である．全 SHA と subject は [候補 commit 一覧](puyo-278-v173-candidate-commits.txt)に固定した．first-parent は #139 `3de91c7`，#141 `605f2cf`，#146 `e7fd4e3`，#160 `c0c77d9`，#171 `732ed3d`，#182 `7757f23`．現時点の候補には Sprint 16 の実装差分はない．個々の 217 commit と Jira scope の最終突合は，Sprint 15 の全 PR を取り込んだ後に再実施する．

Sprint 14 の stack は PR #172，#173，#174，#175，#177，#178，#179，#180，#181，#182 の 10 件で，全件 `MERGED`，GitHub が返す merge commit は `7757f23`．`#176` は stack の参照番号であり，同番号の PR は存在しない．最下段 #172 の当時の base は旧 v1.8.0 統合 branch だった．Sprint 15 の PR は以下のように作成済みで，いずれも未 merge．統合 branch の候補 commit 一覧にはまだ入らない．

| PR／Jira | base | head | 状態／CI |
| --- | --- | --- | --- |
| [#183／PUYO-275](https://github.com/shhchan/puyo-ai-dev-platform/pull/183) | `integration/puyo-228-v1-7-3` | `51d53a8d5008fca3554c7b97502b6a31e637c749` | OPEN，required `linux-cp312-release` 成功．Jira Complete． |
| [#184／PUYO-276](https://github.com/shhchan/puyo-ai-dev-platform/pull/184) | `PUYO-275/esports-tsumo-provider` | `30405d87f764bc51332bd0ec1d7bd8ca038fe3bf` | OPEN，required `linux-cp312-release` 成功．人間実画面 QA 待ち． |
| [#185／PUYO-277](https://github.com/shhchan/puyo-ai-dev-platform/pull/185) | `PUYO-275/esports-tsumo-provider` | `cb2ecde34f012eff39724c788df44ffa2aaf19f2` | OPEN／draft，確認時に CI 結果なし．正式 72 条件を実行中． |

PUYO-266 は `be21c99b7c9821b68e235822f95f9d06ba7c889a` で #184 の head を取り込み済み．正式 120 run は未開始．PUYO-274 の独立 QA 手順文書 head は `0f4b48a8ff266b0762b1e83abf210de54a3170f2` だが，実統合 QA は未実施．これらの PR・SHA を統合候補の実績として数えず，後続の base／head／差分と取り込み順を最終監査で確認する．

`master` の GitHub branch protection は PR 必須，`linux-cp312-release` の required check，管理者にも適用，force-push／削除禁止，会話 resolve 必須，approval 数 0 だった．追加 ruleset の branch API 応答は空配列．release 判断前に protection を再確認する．

## release gate と証拠

| Gate | 必要な証拠 | 現状 |
| --- | --- | --- |
| 配ぷよ source（PUYO-275） | 来歴・利用／再配布条件・別実装照合・checksum・128 手境界，provider と公開 current／NEXT／NEXT2 契約．30 pattern ID と条件を結果より前に固定する． | Complete，PR #183 は OPEN／CI 成功．原本 `haipuyo.txt` は 65,536 行・SHA-256 `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb`．別実装の照合済み．公式実対局による独立証明ではない． |
| 単独本線（PUYO-266） | 固定 30 pattern ID × 2 repeat × 2 policy の正式 120 run，40 手構築＋最大 6 手発火，平均最大実連鎖 ≥ 10，理由のない小発火 0，回避可能窒息 0．旧 legacy random の失敗 raw は別に保持する． | In Progress．runner と事前登録は用意され，head `be21c99` は #184 を取り込み済み．正式 120 run 未開始，約 90～120 分の見積は実測完了を意味しない． |
| 対戦（PUYO-277） | 予告おじゃま対応・独立小連鎖／副砲の発火を，単独 gate と分けて固定条件で評価する．hidden future を観測に渡さない． | In Progress．draft PR #185．旧 v1 の 56 条件監査は 47 PASS／5 FAIL／4 回避不能除外．修正後の 56＋追加 16＝72 条件の正式実行中で，受入未判定． |
| 人間 QA replay（PUYO-276） | GUI 実対局から自動保存された既存形式 replay，結果・設定・実行 identity・pattern ID・保存先，replay 再生一致． | In Progress，PR #184 は OPEN／CI 成功．修正後 dummy 環境の 1000 tick，OFF／ON 各 2 repeat は frame／input schedule の p95 ≤ 25 ms／p99 ≤ 50 ms を全回通過．旧 ON の input p99 57.82 ms 失敗 raw は保存．人間実画面操作は未実施． |
| 統合・人間 QA（PUYO-274） | 上記証拠の組合せ，非公開 future 漏れの否定，先読み表示・操作感，固定 GUI cadence と人間確認． | In Progress．独立 CI／手順文書を準備した head `0f4b48a` はあるが，Sprint 15 の実統合 QA は未実施．Sprint 14 人間 GUI QA の実用受け入れは記録済み． |
| release 監査（PUYO-278） | 対象 Jira と全 PR／SHA／差分の突合，統合 branch head に対する CI・回帰・人間 QA，release 判断． | In Progress．この文書は先行監査のみ． |

旧 G2 の停止相手・攻撃抑止，legacy random 30 seed × 2 repeat は平均最大実連鎖 8.8667，premature 6，窒息 10 で FAIL．新しい単独／対戦 gate の結果へ読み替えない．どちらかが未達なら「次世代モデル全体の G2 PASS」や PUYO-256～258 の本学習開始を宣言しない．Sprint 14 の GUI 実用受け入れは固定 8 条件 × 2 回の frame p95 ≤ 26 ms／p99 ≤ 50 ms，入力 p95 ≤ 25 ms／p99 ≤ 50 ms で受理済み，PUYO-269／273 は Complete．旧 25 ms frame gate の 1 run 未達は証拠として保持し，厳密 25 ms の安定化を Sprint 16 設計へ送る．旧 gate や元 seed 対局の再現を今回の PUYO-274 再完了条件へ混ぜない．

PUYO-275 の再配布ライセンスは確認されていないため，原本は repository に同梱しない．検証者が同じ SHA-256 の原本をローカルに置いて明示指定する運用であり，追加の人間承認 blocker は設定しない．この監査では `/home/sion2000114/.cache/puyo-s15/haipuyo.txt` を read-only で `sha256sum` し，上記の値と一致した．同系の別実装との照合を公式ゲームの実測と呼ばない．128 手越えと 2P 配布規則も公式実測では未照合である．

## 最終監査と release 手順

1. 親が `git fetch origin --tags` した直後に，`git ls-remote origin` で `master`／v1.7.3 統合 branch／`v1.7.2`／`v1.7.3` を読み，`v1.7.3` の不存在と `master` の起点を確認する．各 Sprint 15 PR の base／head／merge 状態と Jira A/C，Blocks を再取得する．未 merge・未受入が一つでもあれば停止する．
2. `git merge-base origin/master origin/integration/puyo-228-v1-7-3`，`git log --first-parent origin/master..origin/integration/puyo-228-v1-7-3`，`git log --format='%H %s' --reverse origin/master..origin/integration/puyo-228-v1-7-3`，`git diff --stat`／`--name-status` で ancestry・全 commit・差分を確定する．候補に無関係な commit，Sprint 16 runtime，未確認の変更があれば停止する．この文書の候補一覧を更新する．
3. 統合 branch の確定 head に対して required CI `linux-cp312-release`，変更に応じた全自動回帰，単独／対戦 gate の固定 raw と verifier，人間 GUI replay と cadence を確認する．コマンド，exit code，artifact，dataset version／checksum，pattern ID，head SHA，実行 host を release PR の `QA` に記す．旧 SHA の PASS を新 head の PASS としない．
4. 全 gate と release 差分監査が成立したら，委任された範囲として `head=integration/puyo-228-v1-7-3`，`base=master` の release PR を作り，What／Why／QA／References に対象 Jira，PR 一覧，SHA，既知の制限を書く．review request は指定しない．required check と会話 resolve を確認する．merge は人間の release 判断後に PR 経由で行い，`master` へ直接 push しない．
5. merge 後に remote `master` と PR の merge commit を照合し，`v1.7.3` が remote に未存在であることを再確認する．tag は **release PR が merge された `master` commit** にだけ付け，SHA／tag の dereference を read-back する．既存 tag は移動しない．その後に v1.8.0 の起点を新 `master` に固定する．

人間が最終判断時に確認する証跡は，この監査，PUYO-275／266／277／276／274 の Jira A/C と PR，各 gate の raw／verifier，release PR の head/base／CI，`master` と tag の remote SHA である．現時点では release PR・tag・GitHub Release を作成しない．
