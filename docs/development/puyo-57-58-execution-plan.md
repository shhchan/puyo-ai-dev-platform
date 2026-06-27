# PUYO-57 / PUYO-58 実行計画

最終更新: 2026-06-27

## 目的

PUYO-57 / v1.5.0 と PUYO-58 / v1.6.0 は UI、設定、plan 可視化、人間データ収集、学習、評価 gate を含む大きな連続作業です。Codex CLI のトークン消費と 1 セッションの作業範囲を抑えるため、PUYO-57 / PUYO-58 関連作業は 1 セッション 1 Jira 子チケットに固定します。ただし、PUYO-58 着手前の調整である PUYO-97 と関連タスク PUYO-98〜PUYO-101 は、共通の UI / realtime 基盤を連続して変更するため、1 セッション、1 ブランチ、1 PR で実施できる例外とします。PUYO-57 関連の PR は `integration/puyo-57` へ、PUYO-58 関連の PR は `integration/puyo-58` へ集約し、`integration/puyo-53-58` を PUYO-57 / PUYO-97 / PUYO-58 の最終統合先として扱います。

## Jira 確認時点の状態

- PUYO-56 / v1.4.0 は Jira 上で `Complete`。ユーザー報告では `integration/puyo-53-58` へ merge 済み。
- PUYO-57 の実装対象子チケット PUYO-80〜PUYO-84 は作業済み。`integration/puyo-57` から `integration/puyo-53-58` への統合 PR #33 は review 待ちで、まだ merge しない。
- PUYO-97 / PUYO-58 前の微調整は `進行中`。関連タスク PUYO-98〜PUYO-101 は `To Do` で、PUYO-97 が PUYO-58 を block している。
- PUYO-58 / v1.6.0 は `To Do`。PUYO-97 完了前には開始しない。
- PUYO-58 の実装対象子チケットは PUYO-85〜PUYO-89。
- PUYO-95 はこの実行計画を整備するための管理タスクであり、PUYO-57 の実装順序には含めない。

## セッション原則

- 「PUYO-57 を実施」「PUYO-58 へ進む」「PUYO-57/58 を順に進める」のような広い依頼でも、一括実行しない。
- 作業開始前に Jira を確認し、次に着手する 1 チケットと推奨 `model_reasoning_effort` をユーザーへ提示する。
- PUYO-57 関連の作業 PR は `integration/puyo-57` を base branch にし、PUYO-58 関連の作業 PR は `integration/puyo-58` を base branch にする。
- 対象チケットのブランチは、PUYO-57 なら `integration/puyo-57`、PUYO-58 なら `integration/puyo-58` 起点で `PUYO-80/unified-gui-launcher` のように作成する。
- Jira のステータス遷移、コメント、commit、PR は選択した 1 チケットだけを対象にする。
- 対象チケット完了後は同一セッションで次チケットへ進まず、次候補と推奨推論レベルを提示して停止する。
- PUYO-97 は例外として PUYO-98〜PUYO-101 を同一セッションで順に実施できる。ブランチは `integration/puyo-53-58` 起点の `PUYO-97/pre-puyo-58-adjustments`、PR base は `integration/puyo-53-58` とし、各 Jira のステータスと完了コメントは個別に管理する。
- PUYO-58 子チケットは、PUYO-57 統合 PR #33 と PUYO-97 実装 PR が `integration/puyo-53-58` へ merge され、同ブランチ上で統合 UI の目視 QA が完了してから開始する。
- Jira の実ステータス、依存リンク、ユーザー指定がこの文書と食い違う場合は Jira / ユーザー指定を優先し、差分を報告して更新案を提示する。
- 途中で時間やトークンが重くなった場合は、実装範囲を同一チケット内の未完了事項として残し、別チケットへ広げない。

## Codex CLI 推論レベル

この環境の `codex debug models` で、`gpt-5.5` は `low` / `medium` / `high` / `xhigh` をサポートしていることを確認済みです。PUYO-57 / PUYO-58 作業では既定値に頼らず、起動時に次のように明示します。

```bash
codex -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high"
```

`xhigh` は常用しません。複数回の失敗、広範囲な設計やり直し、依存関係の破綻調査が必要な場合だけ、ユーザーへ理由を示してから使います。

## 推奨順序と推論レベル

| 順序 | Jira | 推奨 | 理由 |
| --- | --- | --- | --- |
| 1 | PUYO-80: 主要機能へアクセスできる共通 GUI ランチャー | `high` | 後続 UI の画面遷移、controller / view / service 境界、既存 UI 維持を決める基盤のため。 |
| 2 | PUYO-81: policy・checkpoint・config・seed 選択 | `high` | artifact registry、config validation、preset 永続化、CLI equivalent まで影響するため。 |
| 3 | PUYO-82: AI の N 手 plan ghost overlay | `medium` | PUYO-77 plan API 後なら renderer / diagnostics の局所変更に抑えやすいが、目視 QA は必須のため。 |
| 4 | PUYO-83: replay・diagnostics・lineage model viewer | `medium` | 既存 replay / lineage registry を使う viewer と query が中心で、基盤 contract は既にあるため。 |
| 5 | PUYO-84: 統合 UI の回帰テスト・操作性検証・手順 | `medium` | 実装より test、smoke、docs、manual QA 整備が中心のため。 |
| 6 | PUYO-100: launcher・画面遷移・設定操作の安定化 | `high` | 起動、終了、focus、edit mode を先に安定させ、後続の対戦 UI QA を信頼できる状態にするため。 |
| 7 | PUYO-98: AI v.s. AI の非同期進行・ghost 描画 | `high` | simulation、policy decision、render cadence の分離が PUYO-99 と PUYO-101 の共通基盤になるため。 |
| 8 | PUYO-99: Human v.s. AI の独立進行・soft drop | `high` | PUYO-98 の scheduler を Human input と両側の animation 中操作へ展開するため。 |
| 9 | PUYO-101: simulator animation の拡張 | `high` | 時間分離後の共通 animation pipeline に settle、chain、ojama motion を追加するため。 |
| 10 | PUYO-86: 人間対戦 trajectory schema・検証・replay | `high` | PUYO-58 の dataset contract を決め、PUYO-85 の writer と PUYO-87 の sampler に影響するため。 |
| 11 | PUYO-85: 人間対戦データ収集 ON / OFF 制御 | `high` | 統合 UI、match controller、dataset writer、OFF 時の非保存保証をまたぐため。 |
| 12 | PUYO-87: 人間対戦データ由来の派生モデル学習 | `high` | training job、dataset sampler、lineage、active model 保護に影響するため。 |
| 13 | PUYO-88: challenger 自動評価・昇格・rollback gate | `high` | registry role、evaluation pipeline、promotion / rollback の atomicity を扱うため。 |
| 14 | PUYO-89: 監査・削除・安全性テスト | `high` | 収集、学習、評価、昇格を横断する最終 QA と fault test のため。 |

PUYO-97 の4タスクは上表の順序で同一セッション内に連続実施します。途中で未解決の基盤障害が発生した場合は後続タスクへ進まず、完了済みタスクだけを記録して停止します。PUYO-58 は Jira key 順では PUYO-85 が先ですが、dataset writer と training の手戻りを避けるため、schema / replay contract である PUYO-86 を先に実施します。

## PUYO-97 実装方針

### PUYO-100: launcher・画面遷移・設定操作

- ウィンドウ生成前の重い import、font discovery、artifact scan、viewer data load を計測し、遅延初期化または event pump を維持する loading state へ移す。
- launcher と子画面の lifecycle を統一し、`Esc`、window close、`Ctrl-C` で bounded time 内に終了させる。
- hover highlight、click selection、keyboard focus、Enter 後の edit mode を分離する。2 次元 focus navigation、文字列候補 popup、数値 spinner と 0.5 秒後の key repeat acceleration を追加し、時間値は定数で管理する。

### PUYO-98: AI v.s. AI の描画

- simulation tick、policy decision、render frame を分離し、重い `manager_rule` / `beam` 推論中も相手側と animation を一定 cadence で進める。非同期結果は対象 state/tick と照合し、stale result を適用しない。
- active pair の着地点 ghost と N 手 plan overlay を独立した描画レイヤーにし、active ghost は通常ぷよの半径の 1/2 で常時描画する。

### PUYO-99: Human v.s. AI の描画

- Human と AI の controller/tick を独立させ、Human の lock 待ちや片側の chain animation が他方を停止させない。
- Human の `w` を即時 lock の hard drop ではなく時間ベースの soft drop とし、active ghost を controller state から毎 frame 再計算する。

### PUYO-101: simulator animation

- simulation の確定状態と visual-only animation state を分離し、elapsed time ベースの settle squash/bounce、chain flash/vanish/drop、ojama fall を実装する。
- duration は名前付き定数に集約し、異なる render delta でも終端 state と replay determinism を維持する。

### PUYO-97 完了条件

- PUYO-100、PUYO-98、PUYO-99、PUYO-101 をそれぞれ `進行中` から `Complete` へ遷移し、各チケットに変更範囲、自動テスト、Human-visible check、残課題を1コメントで記録する。
- 全自動テストと下記の目視 QA を通し、`PUYO-97/pre-puyo-58-adjustments` から `integration/puyo-53-58` への PR を作成する。
- 4タスクの完了後に PUYO-97 を `Complete` へ遷移する。人間が行う `完了` 遷移と PR merge は実行しない。

## チケット別起動例

```bash
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-80 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-81 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="medium" "PUYO-82 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="medium" "PUYO-83 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="medium" "PUYO-84 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-97 関連の PUYO-100、PUYO-98、PUYO-99、PUYO-101 をこの順に実施してください。PUYO-97 に限り1セッションで一括実行し、AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-86 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-85 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-87 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-88 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-89 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
```

## 次回セッションの開始手順

1. `getAccessibleAtlassianResources` で Atlassian 接続を確認する。
2. JQL `project = PUYO AND key in (PUYO-57, PUYO-58, PUYO-97, PUYO-98, PUYO-99, PUYO-100, PUYO-101, PUYO-85, PUYO-86, PUYO-87, PUYO-88, PUYO-89) ORDER BY key ASC` で最新ステータスと依存を確認する。
3. PUYO-57 統合 PR #33 の review / merge と `integration/puyo-53-58` 上の目視 QA が完了していることを確認する。
4. PUYO-97 が未完了なら `integration/puyo-53-58` を fetch / fast-forward し、`PUYO-97/pre-puyo-58-adjustments` を作成する。対象4タスクと `high` をユーザーへ提示し、上記順序で一括実行する。
5. PUYO-97 が完了するまでは PUYO-58 を開始しない。
6. PUYO-97 完了後は上の推奨順序から未完了かつ依存が満たされた PUYO-58 の最初の1チケットだけを選ぶ。
7. 選んだチケットと推奨 `model_reasoning_effort` をユーザーへ提示し、PR base を `integration/puyo-58` として実施する。
8. PUYO-58 の対象チケット完了後は次チケットへ進まない。

## 成果物チェック提示ルール

- 各チケット完了時の最終報告、Jira コメント、PR description の `QA` には、人間が確認するための `Human-visible check` を必ず含める。
- GUI で確認できる内容は、実際に画面が開くコマンド、確認する画面状態、操作、期待される見た目を提示する。
- GUI が使えない環境向けに、可能な限り `SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy` の smoke command または JSON / Markdown / CSV artifact の確認コマンドも併記する。
- GUI で確認できない backend / schema / training / audit 内容は、その旨を明記し、CUI で確認できる command、生成 artifact、期待されるログまたはファイルを提示する。
- チケット実装で新しい entry point、test file、artifact path を追加した場合は、この表の仮コマンドより実装後の実コマンドを優先して提示する。
- `docs/benchmarks/` に永続 artifact を置く場合は、チケットの受け入れ条件に必要なものだけを commit 対象にする。単なる一時確認は `/tmp` または作業用 run directory に出す。

## チケット別チェック方針

| Jira | GUI 確認 | CUI / artifact 確認 |
| --- | --- | --- |
| PUYO-80 | `python3 main.py` で launcher を開き、home / play / spectate / arena / training / models へ移動できることを確認する。既存機能の退行確認は `python3 -m eval.versus_ui --policy-a human --policy-b greedy --seed 57 --start-paused` を使う。 | `SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -m unittest tests.test_versus_ui tests.test_realtime_versus_ui -q` |
| PUYO-81 | `python3 main.py` で policy / checkpoint / config / seed / speed を GUI から選ぶ。既存 UI 経路では `python3 -m eval.versus_ui --policy-a beam --policy-b greedy --seed 57 --seed-a 101 --seed-b 202 --beam-depth-a 8 --beam-depth-b 10 --start-paused` で設定反映を目視する。 | `python3 -m unittest tests.test_versus_ui tests.test_checkpoint_loading tests.test_lineage -q` |
| PUYO-82 | `python3 -m eval.realtime_versus_ui --policy-a beam --policy-b random --seed 57 --max-ticks 600 --beam-depth 3 --beam-width 8 --start-paused` で plan ghost、順序、replan 表示、overlay OFF を目視する。 | `SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -m eval.realtime_versus_ui --policy-a beam --policy-b random --seed 57 --max-ticks 180 --beam-depth 3 --beam-width 8 --max-frames 8 --result-json /tmp/puyo-82-plan-overlay-smoke.json` |
| PUYO-83 | `python3 main.py` で model / replay / lineage viewer を開き、replay seek、diagnostics timeline、checkpoint lineage 遷移を確認する。standalone viewer CLI を追加した場合はその実コマンドを優先する。 | `python3 -m train.lineage --root docs/benchmarks --output /tmp/puyo-lineage.json --markdown /tmp/puyo-lineage.md` と `python3 -m unittest tests.test_lineage tests.test_realtime_replay -q` |
| PUYO-84 | `python3 main.py` と `python3 -m eval.realtime_versus_ui --policy-a first --policy-b random --seed 57 --max-ticks 600 --start-paused` で manual QA checklist を実施する。 | `python3 -m unittest discover -s tests -q` と dummy video smoke の JSON 出力を確認する。 |
| PUYO-100 | cold start の `python3 main.py` で数秒以内に window または loading state が表示されること、全画面への遷移、`Esc` / x / `Ctrl-C` 終了、hover / click / 2次元 focus / edit mode / popup / spinner を確認する。 | launcher tests で startup/transition/exit timeout、focus navigation、filtering、0.5秒後の numeric acceleration を検証し、`SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy` の smoke を実行する。 |
| PUYO-98 | AI v.s. AI を `manager_rule` / `beam` で実行し、片側の思考中も相手側と animation が滑らかに進むこと、半径1/2の active ghost と N 手 plan ghost を識別できることを確認する。 | delayed policy による cadence、stale decision rejection、ghost layer を deterministic test で検証し、dummy video smoke の result JSON を保存する。 |
| PUYO-99 | Human v.s. AI で Human を放置しても AI が進むこと、双方が相手の chain animation 中に操作できること、`w` 長押しが段階的 soft drop になること、ghost が消えないことを確認する。 | Human 未操作中の AI tick、両側 animation 中入力、soft-drop repeat/lock、ghost persistence を controller test で検証する。 |
| PUYO-101 | 通常設置の「ぷよん」、複数連鎖、おじゃま落下を目視し、速度と形が一貫すること、animation 中も両側の操作が継続することを確認する。 | animation timeline 境界、異なる frame delta での同一終端 state、replay determinism を test し、dummy video smoke を実行する。 |
| PUYO-86 | GUI で直接確認できる主対象ではない。viewer 連携を追加した場合だけ `python3 main.py` から replay / dataset viewer を目視する。 | trajectory schema validation、replay determinism、quarantine、index rebuild を CUI test と generated JSON で確認する。最低限 `python3 -m unittest tests.test_realtime_replay tests.test_lineage -q` に、追加した human dataset tests を含める。 |
| PUYO-85 | `python3 main.py` で human match collection の ON / OFF、保存先、停止操作、状態表示を確認する。OFF session では保存されないことを画面とファイル一覧で確認する。 | `python3 -m unittest discover -s tests -q` と、ON / OFF session の dataset directory / audit log の差分を示す command を提示する。 |
| PUYO-87 | GUI job controls を実装した場合は `python3 main.py` の training 画面で dataset selection、start / stop / cancel、active model 非変更を確認する。GUI がない場合はその旨を明記する。 | smoke training は `python3 -m train.train_realtime --config train/config/realtime_smoke.yaml --set run_id=puyo-87-smoke` を基準に、実装した human dataset sampler の指定を加えた実コマンドを提示する。 |
| PUYO-88 | `python3 main.py` の model / training / evaluation status 画面で champion、challenger、previous stable、promotion / rejection / rollback を確認する。GUI がない場合はその旨を明記する。 | fixed seed arena、promotion criteria、registry role 更新、rollback 再実行を CUI で確認する。既存基盤は `python3 -m eval.realtime_arena --policy-a first --policy-b random --games 1 --seed 58 --max-ticks 180 --paired-sides` と `python3 -m train.lineage --root runs --output /tmp/puyo-88-lineage.json --markdown /tmp/puyo-88-lineage.md` を使う。 |
| PUYO-89 | audit / deletion / safety view を追加した場合は `python3 main.py` で collection、dataset、derived model、promotion の追跡と削除前確認を目視する。GUI がない場合はその旨を明記する。 | `python3 -m unittest discover -s tests -q` に fault injection / deletion / rollback safety tests を含め、監査 Markdown または JSON report の生成 command を提示する。 |
