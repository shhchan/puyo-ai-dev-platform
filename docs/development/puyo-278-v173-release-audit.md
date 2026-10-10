# PUYO-278 v1.7.3 release 監査

2026-10-10 時点の先行監査．Sprint 15 の受け入れと統合 QA が未完了のため，release PR／merge／tag は保留する．この文書の SHA と状態は release 判断の直前に再取得する．

## 起点と対象履歴

| 対象 | 確認値 |
| --- | --- |
| `origin/master`／`v1.7.2^{}` | `3defcc2b8c6f42824102166aa7011527dfd4fb9e` |
| `origin/integration/puyo-228-v1-7-3` | `7757f2312f34a1d79593f43ebd4f41921c61f0a6` |
| 旧 `origin/integration/puyo-228-v1-8-0` | `7757f2312f34a1d79593f43ebd4f41921c61f0a6` |
| merge base (`master` と候補) | `3defcc2b8c6f42824102166aa7011527dfd4fb9e` |

親セッションが旧統合 branch の head を履歴保持で新しい v1.7.3 統合 branch に作成し，remote を read-back した．両 branch の tree は一致する．旧 v1.8.0 branch は保持して使用を停止する．削除・再作成・force-push はしない．v1.8.0 の後続統合は，v1.7.3 release 後の `master` を起点に親が決める．PUYO-279 は Sprint 16 の設計・実装 Task 起票を担当し，この release 候補へ runtime 変更を混ぜない．

`master..7757f23` は 217 commit，first-parent 6 merge である．全 SHA と subject は [候補 commit 一覧](puyo-278-v173-candidate-commits.txt)に固定した．first-parent は #139 `3de91c7`，#141 `605f2cf`，#146 `e7fd4e3`，#160 `c0c77d9`，#171 `732ed3d`，#182 `7757f23`．現時点の候補には Sprint 16 の実装差分はない．個々の 217 commit と Jira scope の最終突合は，Sprint 15 の全 PR を取り込んだ後に再実施する．

Sprint 14 の stack は PR #172，#173，#174，#175，#177，#178，#179，#180，#181，#182 の 10 件で，全件 `MERGED`，GitHub が返す merge commit は `7757f23`．`#176` は stack の参照番号であり，同番号の PR は存在しない．最下段 #172 の当時の base は旧 v1.8.0 統合 branch だった．新しい Sprint 15 最下段 PR の base は v1.7.3 統合 branch に変更済みであり，各 PR の base と head は最終監査で再取得する．

`master` の GitHub branch protection は PR 必須，`linux-cp312-release` の required check，管理者にも適用，force-push／削除禁止，会話 resolve 必須，approval 数 0 だった．追加 ruleset の branch API 応答は空配列．release 判断前に protection を再確認する．

## release gate と証拠

| Gate | 必要な証拠 | 現状 |
| --- | --- | --- |
| 配ぷよ source（PUYO-275） | 来歴・利用／再配布条件・独立照合・checksum・128 手境界，provider と公開 current／NEXT／NEXT2 契約．30 pattern ID と条件を結果より前に固定する． | In Progress．source の再配布許諾と独立照合が難航．正式新 gate は停止し得る． |
| 単独本線（PUYO-266） | 固定 30 pattern ID × 2 repeat，40 手構築＋最大 6 手発火，平均最大実連鎖 ≥ 10，理由のない小発火 0，回避可能窒息 0．旧 legacy random の失敗 raw は別に保持する． | In Progress．新配ぷよでの正式評価待ち． |
| 対戦（PUYO-277） | 予告おじゃま対応・独立小連鎖／副砲の発火を，単独 gate と分けて固定条件で評価する．hidden future を観測に渡さない． | To Do． |
| 人間 QA replay（PUYO-276） | GUI 実対局から自動保存された既存形式 replay，結果・設定・実行 identity・pattern ID・保存先，replay 再生一致． | To Do．旧 seed 59／127 の元対局 replay はない． |
| 統合・人間 QA（PUYO-274） | 上記証拠の組合せ，非公開 future 漏れの否定，先読み表示・操作感，固定 GUI cadence と人間確認． | In Progress．Sprint 14 人間 GUI QA の実用受け入れは記録済み．Sprint 15 条件は未達． |
| release 監査（PUYO-278） | 対象 Jira と全 PR／SHA／差分の突合，統合 branch head に対する CI・回帰・人間 QA，release 判断． | In Progress．この文書は先行監査のみ． |

旧 G2 の停止相手・攻撃抑止，legacy random 30 seed × 2 repeat は平均最大実連鎖 8.8667，premature 6，窒息 10 で FAIL．新しい単独／対戦 gate の結果へ読み替えない．どちらかが未達なら「次世代モデル全体の G2 PASS」や PUYO-256～258 の本学習開始を宣言しない．Sprint 14 の GUI 実用受け入れは固定 8 条件 × 2 回の frame p95 ≤ 26 ms／p99 ≤ 50 ms，入力 p95 ≤ 25 ms／p99 ≤ 50 ms で受理済み，PUYO-269／273 は Complete．旧 25 ms frame gate の 1 run 未達は証拠として保持し，厳密 25 ms の安定化を Sprint 16 設計へ送る．旧 gate や元 seed 対局の再現を今回の PUYO-274 再完了条件へ混ぜない．

## 最終監査と release 手順

1. 親が `git fetch origin --tags` した直後に，`git ls-remote origin` で `master`／v1.7.3 統合 branch／`v1.7.2`／`v1.7.3` を読み，`v1.7.3` の不存在と `master` の起点を確認する．各 Sprint 15 PR の base／head／merge 状態と Jira A/C，Blocks を再取得する．未 merge・未受入が一つでもあれば停止する．
2. `git merge-base origin/master origin/integration/puyo-228-v1-7-3`，`git log --first-parent origin/master..origin/integration/puyo-228-v1-7-3`，`git log --format='%H %s' --reverse origin/master..origin/integration/puyo-228-v1-7-3`，`git diff --stat`／`--name-status` で ancestry・全 commit・差分を確定する．候補に無関係な commit，Sprint 16 runtime，未確認の変更があれば停止する．この文書の候補一覧を更新する．
3. 統合 branch の確定 head に対して required CI `linux-cp312-release`，変更に応じた全自動回帰，単独／対戦 gate の固定 raw と verifier，人間 GUI replay と cadence を確認する．コマンド，exit code，artifact，dataset version／checksum，pattern ID，head SHA，実行 host を release PR の `QA` に記す．旧 SHA の PASS を新 head の PASS としない．
4. 全 gate が満たされ人間の release 判断が記録されたら，`head=integration/puyo-228-v1-7-3`，`base=master` の release PR を作り，What／Why／QA／References に対象 Jira，PR 一覧，SHA，既知の制限を書く．review request は指定しない．required check と会話 resolve を確認する．merge は人間の指示後に PR 経由で行い，`master` へ直接 push しない．
5. merge 後に remote `master` と PR の merge commit を照合し，`v1.7.3` が remote に未存在であることを再確認する．tag は **release PR が merge された `master` commit** にだけ付け，SHA／tag の dereference を read-back する．既存 tag は移動しない．その後に v1.8.0 の起点を新 `master` に固定する．

人間が最終判断時に確認する証跡は，この監査，PUYO-275／266／277／276／274 の Jira A/C と PR，各 gate の raw／verifier，release PR の head/base／CI，`master` と tag の remote SHA である．現時点では release PR・tag・GitHub Release を作成しない．
