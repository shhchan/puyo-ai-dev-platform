# PUYO-278 v1.7.3 release 監査

2026-10-11 の再監査．Sprint 15 の受け入れと統合 QA が未完了のため，release PR／merge／tag は保留する．この文書の SHA と状態は release 判断の直前に再取得する．

## 起点と対象履歴

| 対象 | 確認値 |
| --- | --- |
| `origin/master`／`v1.7.2^{}` | `3defcc2b8c6f42824102166aa7011527dfd4fb9e` |
| `origin/integration/puyo-228-v1-7-3` | `7757f2312f34a1d79593f43ebd4f41921c61f0a6` |
| 旧 `origin/integration/puyo-228-v1-8-0` | `7757f2312f34a1d79593f43ebd4f41921c61f0a6` |
| merge base (`master` と候補) | `3defcc2b8c6f42824102166aa7011527dfd4fb9e` |

親セッションが旧統合 branch の head を履歴保持で新しい v1.7.3 統合 branch に作成し，remote を read-back した．両 branch の tree は一致する．旧 v1.8.0 branch は保持して使用を停止する．削除・再作成・force-push はしない．v1.8.0 の後続統合は，v1.7.3 release 後の `master` を起点に親が決める．PUYO-279 は Sprint 16 の設計・実装 Task 起票を担当し，この release 候補へ runtime 変更を混ぜない．

`master..7757f23` は 217 commit，first-parent 6 merge である．全 SHA と subject は [候補 commit 一覧](puyo-278-v173-candidate-commits.txt)に固定した．first-parent は #139 `3de91c7`，#141 `605f2cf`，#146 `e7fd4e3`，#160 `c0c77d9`，#171 `732ed3d`，#182 `7757f23`．現時点の remote 統合 branch はまだこの 217 commit の境界であり，Sprint 15 PR は一件も merge されていない．個々の commit と Jira scope の最終突合は，Sprint 15 の全 PR を取り込んだ後に再実施する．

Sprint 14 の stack は PR #172，#173，#174，#175，#177，#178，#179，#180，#181，#182 の 10 件で，全件 `MERGED`，GitHub が返す merge commit は `7757f23`．`#176` は stack の参照番号であり，同番号の PR は存在しない．最下段 #172 の当時の base は旧 v1.8.0 統合 branch だった．Sprint 15 の [stack #189](https://github.com/shhchan/puyo-ai-dev-platform/stacks/189) は v1.7.3 統合 branch を起点に #183→#184→#185→#186→#187→#188 の 6 層で接続済み．全 PR は OPEN，reviewer 未指定，未 merge．統合 branch の候補 commit 一覧にはまだ入らない．

| PR／Jira | base | head | 状態／CI |
| --- | --- | --- | --- |
| [#183／PUYO-275](https://github.com/shhchan/puyo-ai-dev-platform/pull/183) | `integration/puyo-228-v1-7-3` | `2e338b6216bb463e173b2be4cf3d814bb6fa0d37` | OPEN，lint 修正後 head の required CI 成功．Jira Complete． |
| [#184／PUYO-276](https://github.com/shhchan/puyo-ai-dev-platform/pull/184) | `PUYO-275/esports-tsumo-provider` | `6d71dc41829bf51d15fd6411b45600fc5a4b3752` | OPEN／ready，required CI 成功．人間実画面 QA 3 件と保存 OFF の自動確認が成功し，Jira Complete． |
| [#185／PUYO-277](https://github.com/shhchan/puyo-ai-dev-platform/pull/185) | `PUYO-276/automatic-qa-replay` | `555880c69064177a9fcbf4d4b4ad780adb21e768` | OPEN／ready，head 固有の check は未報告．Jira Complete． |
| [#186／PUYO-266](https://github.com/shhchan/puyo-ai-dev-platform/pull/186) | `PUYO-277/attack-response-gate` | `b5c988140462beed05a3b5d913c72a066a8b6290` | OPEN／draft，required CI 成功．正式単独 gate は BLOCKED． |
| [#187／PUYO-274](https://github.com/shhchan/puyo-ai-dev-platform/pull/187) | `PUYO-266/sprint15-single-quality` | `63bf51f3632a2053745079c22cc1d32b343b676f` | OPEN／draft，製品 source head `3613989` の required CI は 2 run 成功．文書追補 head の CI は実行中．統合 QA は PUYO-266 のため BLOCKED． |
| [#188／PUYO-278](https://github.com/shhchan/puyo-ai-dev-platform/pull/188) | `PUYO-274/sprint15-closeout` | 更新前 `9008bdcc47e8589736901fe3acb02c96d56ce7fe` | OPEN／draft，更新前 head の required CI は成功．本監査更新後に head を再取得する． |

PUYO-266 の head は公開 action mask 修正 `b6046e8` と証拠専用 commit `b5c9881` を含む．同じ事前登録 120 identity の v3 は 14 final で停止し，未実行と未知分類を含む．正式 v4 は init も実行もしていない．reference 4519 は targeted の両 repeat でも 42 手死亡・最大 2 連鎖・窒息 unknown のまま．公開 prefix の隔離診断で 29 手目だけを代替 root へ変更すると 34 手死亡・最大 1 連鎖，毎決定の診断 filter では 34 手死亡・最大 0 連鎖だった．公開到達不能な予測を除いても品質は改善せず，未知 future の仮定と代替解・quiet 継続の証明が残るため，製品 filter は採用しなかった．これらの別 policy 診断を正式 120 run の合格に数えない．

PUYO-274 の製品 source head `361398981f3a9083dbf63b79a9993f8548ec0fb7` は親が本監査 branch へ履歴保持で取り込み，取り込み commit は `a390c52f12ba4f0c960b75644a3fa9b1a2c54b6a`．人間 QA 証拠を追補した head `63bf51f3632a2053745079c22cc1d32b343b676f` も取り込んだ．これらは remote 統合 branch への merge ではない．初回 #187 CI は Ruff 0.16.0 の I001／B023 で失敗した．担当別の lint 修正を積み上げた #187 の製品 source では，同じ Ruff 0.16.0 と原本付き 8 suite／102 tests（skip 0）がローカル成功し，required CI 2 run も成功した．以降の取り込みは証拠のみで製品コードを変更していない．文書追補 head の remote CI は実行中であり，PUYO-266 の品質未達で統合受け入れは BLOCKED．これらの PR・SHA を release 候補の実績として数えず，後続の base／head／差分と取り込み順を最終監査で確認する．

workflow ファイルを含む先行 push は OAuth token の `workflow` scope 不足で一度拒否された．ユーザーの再認証後，`gh auth status` は `workflow` scope を確認でき，親は #187／#188 を push・draft PR 作成済み．認証 blocker は解消した．更新後 head について required CI の終了結果を別途 read-back する．旧 head の成功を新 head の成功としない．CI と PUYO-266／274 の品質 gate 未達は別に扱う．

`master` の GitHub branch protection は PR 必須，`linux-cp312-release` の required check，管理者にも適用，force-push／削除禁止，会話 resolve 必須，approval 数 0 だった．追加 ruleset の branch API 応答は空配列．release 判断前に protection を再確認する．

## release gate と証拠

| Gate | 必要な証拠 | 現状 |
| --- | --- | --- |
| 配ぷよ source（PUYO-275） | 来歴・利用／再配布条件・別実装照合・checksum・128 手境界，provider と公開 current／NEXT／NEXT2 契約．30 pattern ID と条件を結果より前に固定する． | Complete，PR #183 は OPEN．原本 `haipuyo.txt` は 65,536 行・SHA-256 `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb`．別実装の照合済み．公式実対局による独立証明ではない． |
| 単独本線（PUYO-266） | 固定 30 pattern ID × 2 repeat × 2 policy の正式 120 run，40 手構築＋最大 6 手発火，平均最大実連鎖 ≥ 10，理由のない小発火 0，回避可能窒息 0．旧 legacy random の失敗 raw は別に保持する． | In Progress，draft PR #186．正式 v3 は 14／120 final で中断．nextgen pattern 6779 は最大 1 連鎖・不当小発火 1，reference pattern 4519 は最大 2 連鎖・窒息 unknown．targeted で nextgen 6779 は両 repeat とも最大 10 連鎖へ改善したが，reference 4519 は改善せず，追加の公開 prefix filter 診断も不採用．targeted と隔離診断を正式合格へ数えず，v4 は未実施．BLOCKED． |
| 対戦（PUYO-277） | 予告おじゃま対応・独立小連鎖／副砲の発火を，単独 gate と分けて固定条件で評価する．hidden future を観測に渡さない． | Complete，PR #185 は ready．正式 v3 の元 56 条件は 52 PASS／4 回避不能除外／0 FAIL，追加 16 条件は全 PASS．全 72 replay の event／全 tick hash／最終 hash が一致した．これは bounded 公開 challenge の合格であり，全体 G2 や単独品質の合格ではない． |
| 人間 QA replay（PUYO-276） | GUI 実対局から自動保存された既存形式 replay，結果・設定・実行 identity・pattern ID・保存先，replay 再生一致． | Complete，PR #184 は OPEN．修正後 dummy 環境の 1000 tick，OFF／ON 各 2 repeat は frame／input schedule の p95 ≤ 25 ms／p99 ≤ 50 ms を全回通過．旧 ON の input p99 57.82 ms 失敗 raw は保存．人間実画面 3 件の bundle 検証と操作／先読み確認が成功．保存 OFF は dummy 起動で保存先非作成を確認． |
| 統合・人間 QA（PUYO-274） | 上記証拠の組合せ，非公開 future 漏れの否定，先読み表示・操作感，固定 GUI cadence と人間確認． | In Progress，draft PR #187．原本付き 8 suite／102 tests，skip 0，Ruff／差分確認は成功．人間実画面 QA は確認済み．PUYO-266 の正式品質未達で統合判定は BLOCKED．文書追補 head の remote CI は実行中． |
| release 監査（PUYO-278） | 対象 Jira と全 PR／SHA／差分の突合，統合 branch head に対する CI・回帰・人間 QA，release 判断． | In Progress，draft PR #188．stack と branch 境界は監査済み．先行 gate 未達のため release PR／tag は保留． |

人間 QA の 3 件は同一 source `361398981f3a9083dbf63b79a9993f8548ec0fb7` で，session ID は `f54e176489a148d78515ea7a31cf533b`（通常），`abb6fc23d5984d24821b3c8ad546f485`（配ぷよ指定・通常），`ae8b6b865c7a4c3b9742935e455df51c`（Esc 中断）．各 bundle の隔離コピーを `python -m eval.qa_session` で検証し，frame p95/p99 は順に 18/37，17/35，17/30 ms．依頼者が 2P 操作と `O` 2 回の先読み切替を確認した．詳細な replay checksum と実行 identity は [統合 QA 記録](puyo-274-sprint15-integration-qa.md)を参照する．

旧 G2 の停止相手・攻撃抑止，legacy random 30 seed × 2 repeat は平均最大実連鎖 8.8667，premature 6，窒息 10 で FAIL．新しい単独／対戦 gate の結果へ読み替えない．どちらかが未達なら「次世代モデル全体の G2 PASS」や PUYO-256～258 の本学習開始を宣言しない．Sprint 14 の GUI 実用受け入れは固定 8 条件 × 2 回の frame p95 ≤ 26 ms／p99 ≤ 50 ms，入力 p95 ≤ 25 ms／p99 ≤ 50 ms で受理済み，PUYO-269／273 は Complete．旧 25 ms frame gate の 1 run 未達は証拠として保持し，厳密 25 ms の安定化を Sprint 16 設計へ送る．旧 gate や元 seed 対局の再現を今回の PUYO-274 再完了条件へ混ぜない．

PUYO-275 の再配布ライセンスは確認されていないため，原本は repository に同梱しない．検証者が同じ SHA-256 の原本をローカルに置いて明示指定する運用であり，追加の人間承認 blocker は設定しない．この監査では `/home/sion2000114/.cache/puyo-s15/haipuyo.txt` を read-only で `sha256sum` し，上記の値と一致した．同系の別実装との照合を公式ゲームの実測と呼ばない．128 手越えと 2P 配布規則も公式実測では未照合である．

## 最終監査と release 手順

1. 親が `git fetch origin --tags` した直後に，`git ls-remote origin` で `master`／v1.7.3 統合 branch／`v1.7.2`／`v1.7.3` を読み，`v1.7.3` の不存在と `master` の起点を確認する．各 Sprint 15 PR の base／head／merge 状態と Jira A/C，Blocks を再取得する．未 merge・未受入が一つでもあれば停止する．
2. `git merge-base origin/master origin/integration/puyo-228-v1-7-3`，`git log --first-parent origin/master..origin/integration/puyo-228-v1-7-3`，`git log --format='%H %s' --reverse origin/master..origin/integration/puyo-228-v1-7-3`，`git diff --stat`／`--name-status` で ancestry・全 commit・差分を確定する．候補に無関係な commit，Sprint 16 runtime，未確認の変更があれば停止する．この文書の候補一覧を更新する．
3. 統合 branch の確定 head に対して required CI `linux-cp312-release`，変更に応じた全自動回帰，単独／対戦 gate の固定 raw と verifier，人間 GUI replay と cadence を確認する．コマンド，exit code，artifact，dataset version／checksum，pattern ID，head SHA，実行 host を release PR の `QA` に記す．旧 SHA の PASS を新 head の PASS としない．
4. 全 gate と release 差分監査が成立したら，委任された範囲として `head=integration/puyo-228-v1-7-3`，`base=master` の release PR を作り，What／Why／QA／References に対象 Jira，PR 一覧，SHA，既知の制限を書く．review request は指定しない．required check と会話 resolve を確認する．merge は人間の release 判断後に PR 経由で行い，`master` へ直接 push しない．
5. merge 後に remote `master` と PR の merge commit を照合し，`v1.7.3` が remote に未存在であることを再確認する．tag は **release PR が merge された `master` commit** にだけ付け，SHA／tag の dereference を read-back する．既存 tag は移動しない．その後に v1.8.0 の起点を新 `master` に固定する．

人間が最終判断時に確認する証跡は，この監査，PUYO-275／266／277／276／274 の Jira A/C と PR，各 gate の raw／verifier，release PR の head/base／CI，`master` と tag の remote SHA である．現時点では release PR・tag・GitHub Release を作成しない．
