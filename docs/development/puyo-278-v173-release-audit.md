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

Sprint 14 の stack は PR #172，#173，#174，#175，#177，#178，#179，#180，#181，#182 の 10 件で，全件 `MERGED`，GitHub が返す merge commit は `7757f23`．`#176` は stack の参照番号であり，同番号の PR は存在しない．最下段 #172 の当時の base は旧 v1.8.0 統合 branch だった．Sprint 15 の PR は以下のように作成済みで，いずれも未 merge．統合 branch の候補 commit 一覧にはまだ入らない．

| PR／Jira | base | head | 状態／CI |
| --- | --- | --- | --- |
| [#183／PUYO-275](https://github.com/shhchan/puyo-ai-dev-platform/pull/183) | `integration/puyo-228-v1-7-3` | `51d53a8d5008fca3554c7b97502b6a31e637c749` | OPEN，required `linux-cp312-release` 成功．Jira Complete． |
| [#184／PUYO-276](https://github.com/shhchan/puyo-ai-dev-platform/pull/184) | `PUYO-275/esports-tsumo-provider` | `30405d87f764bc51332bd0ec1d7bd8ca038fe3bf` | OPEN，required `linux-cp312-release` 成功．人間実画面 QA 待ち． |
| [#185／PUYO-277](https://github.com/shhchan/puyo-ai-dev-platform/pull/185) | `PUYO-276/automatic-qa-replay` | `06c0e1ae3783904790c8506265b1af9f41086d0f` | OPEN／ready，required `linux-cp312-release` 成功．Jira Complete． |
| [#186／PUYO-266](https://github.com/shhchan/puyo-ai-dev-platform/pull/186) | `PUYO-277/attack-response-gate` | `1deacb34d130ce9af0b696a3f012cd38cef3cb44` | OPEN／draft．直前の製品 head `21d6b16` は required CI 成功．docs-only 追補 head の CI は進行中．正式単独 gate は BLOCKED． |

PUYO-266 の head は公開 action mask 修正 `b6046e8` と追加調査を含む．同じ事前登録 120 identity の v3 は 14 final で停止し，未実行と未知分類を含む．正式 v4 は init も実行もしていない．追加の公開 prefix 読取監査は，到達不能 trajectory を単純に除くと正常 2259 の選択も変えることを示し，製品 filter を採用しなかった．PUYO-274 の確定 head `fd78cc56de362111d276086ee371283ccc95ce74` は親が本監査 branch へ履歴保持で取り込み，取り込み commit は `e76083198ea4fb1b896a638aedda6f6ad5c40793`．これは remote 統合 branch への merge ではない．PUYO-274 の独自 CI／QA 文書を含む stack 上の機械確認はあるが，統合受け入れは BLOCKED．これらの PR・SHA を release 候補の実績として数えず，後続の base／head／差分と取り込み順を最終監査で確認する．

親の GitHub push は，workflow ファイルを含む commit に対し OAuth token の `workflow` scope 不足で拒否された．この監査時の `gh auth status` も token scopes が `gist`／`read:org`／`repo` であり，`workflow` を含まない．親が認証を回復するまで PUYO-274 の push と後続 draft PR の remote CI を完了扱いにしない．認証の問題は QA gate 未達と別に記録する．

`master` の GitHub branch protection は PR 必須，`linux-cp312-release` の required check，管理者にも適用，force-push／削除禁止，会話 resolve 必須，approval 数 0 だった．追加 ruleset の branch API 応答は空配列．release 判断前に protection を再確認する．

## release gate と証拠

| Gate | 必要な証拠 | 現状 |
| --- | --- | --- |
| 配ぷよ source（PUYO-275） | 来歴・利用／再配布条件・別実装照合・checksum・128 手境界，provider と公開 current／NEXT／NEXT2 契約．30 pattern ID と条件を結果より前に固定する． | Complete，PR #183 は OPEN／CI 成功．原本 `haipuyo.txt` は 65,536 行・SHA-256 `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb`．別実装の照合済み．公式実対局による独立証明ではない． |
| 単独本線（PUYO-266） | 固定 30 pattern ID × 2 repeat × 2 policy の正式 120 run，40 手構築＋最大 6 手発火，平均最大実連鎖 ≥ 10，理由のない小発火 0，回避可能窒息 0．旧 legacy random の失敗 raw は別に保持する． | In Progress，draft PR #186．正式 v3 は 14／120 final で中断．nextgen pattern 6779 は最大 1 連鎖・不当小発火 1，reference pattern 4519 は最大 2 連鎖・窒息 unknown．修正後 targeted 4 run で nextgen 6779 は両 repeat とも最大 10 連鎖へ改善したが，reference 4519 の unknown は残る．targeted を正式合格へ数えず，v4 は未実施．BLOCKED． |
| 対戦（PUYO-277） | 予告おじゃま対応・独立小連鎖／副砲の発火を，単独 gate と分けて固定条件で評価する．hidden future を観測に渡さない． | Complete，PR #185 は ready／CI 成功．正式 v3 の元 56 条件は 52 PASS／4 回避不能除外／0 FAIL，追加 16 条件は全 PASS．全 72 replay の event／全 tick hash／最終 hash が一致した．これは bounded 公開 challenge の合格であり，全体 G2 や単独品質の合格ではない． |
| 人間 QA replay（PUYO-276） | GUI 実対局から自動保存された既存形式 replay，結果・設定・実行 identity・pattern ID・保存先，replay 再生一致． | In Progress，PR #184 は OPEN／CI 成功．修正後 dummy 環境の 1000 tick，OFF／ON 各 2 repeat は frame／input schedule の p95 ≤ 25 ms／p99 ≤ 50 ms を全回通過．旧 ON の input p99 57.82 ms 失敗 raw は保存．人間実画面操作は未実施． |
| 統合・人間 QA（PUYO-274） | 上記証拠の組合せ，非公開 future 漏れの否定，先読み表示・操作感，固定 GUI cadence と人間確認． | In Progress．`fd78cc56` の先行統合 head で原本付き 8 suite／102 tests，skip 0，Ruff／差分確認は成功．PUYO-266 の正式品質と PUYO-276 の人間実画面 QA が未達で，統合判定は BLOCKED．remote CI は workflow scope 不足による push 拒否で未確認． |
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
