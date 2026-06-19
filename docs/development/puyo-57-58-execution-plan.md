# PUYO-57 / PUYO-58 実行計画

最終更新: 2026-06-19

## 目的

PUYO-57 / v1.5.0 と PUYO-58 / v1.6.0 は UI、設定、plan 可視化、人間データ収集、学習、評価 gate を含む大きな連続作業です。Codex CLI のトークン消費と 1 セッションの作業範囲を抑えるため、PUYO-57 / PUYO-58 関連作業は 1 セッション 1 Jira 子チケットに固定します。PUYO-57 関連の PR は `integration/puyo-57` へ、PUYO-58 関連の PR は `integration/puyo-58` へ集約し、両エピックの作業が一巡したタイミングで `integration/puyo-53-58` を PUYO-57 / PUYO-58 の塊をまとめる最終統合先として扱います。

## Jira 確認時点の状態

- PUYO-56 / v1.4.0 は Jira 上で `Complete`。ユーザー報告では `integration/puyo-53-58` へ merge 済み。
- PUYO-57 / v1.5.0 は `To Do`。PUYO-56 に block され、PUYO-58 を block している。
- PUYO-58 / v1.6.0 は `To Do`。PUYO-57 に block されている。
- PUYO-57 の実装対象子チケットは PUYO-80〜PUYO-84。
- PUYO-58 の実装対象子チケットは PUYO-85〜PUYO-89。
- PUYO-95 はこの実行計画を整備するための管理タスクであり、PUYO-57 の実装順序には含めない。

## セッション原則

- 「PUYO-57 を実施」「PUYO-58 へ進む」「PUYO-57/58 を順に進める」のような広い依頼でも、一括実行しない。
- 作業開始前に Jira を確認し、次に着手する 1 チケットと推奨 `model_reasoning_effort` をユーザーへ提示する。
- PUYO-57 関連の作業 PR は `integration/puyo-57` を base branch にし、PUYO-58 関連の作業 PR は `integration/puyo-58` を base branch にする。
- 対象チケットのブランチは、PUYO-57 なら `integration/puyo-57`、PUYO-58 なら `integration/puyo-58` 起点で `PUYO-80/unified-gui-launcher` のように作成する。
- Jira のステータス遷移、コメント、commit、PR は選択した 1 チケットだけを対象にする。
- 対象チケット完了後は同一セッションで次チケットへ進まず、次候補と推奨推論レベルを提示して停止する。
- PUYO-58 子チケットは、PUYO-57 の子チケットが完了し、`integration/puyo-53-58` 上で統合 UI の目視 QA が可能になってから開始する。
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
| 6 | PUYO-86: 人間対戦 trajectory schema・検証・replay | `high` | PUYO-58 の dataset contract を決め、PUYO-85 の writer と PUYO-87 の sampler に影響するため。 |
| 7 | PUYO-85: 人間対戦データ収集 ON / OFF 制御 | `high` | 統合 UI、match controller、dataset writer、OFF 時の非保存保証をまたぐため。 |
| 8 | PUYO-87: 人間対戦データ由来の派生モデル学習 | `high` | training job、dataset sampler、lineage、active model 保護に影響するため。 |
| 9 | PUYO-88: challenger 自動評価・昇格・rollback gate | `high` | registry role、evaluation pipeline、promotion / rollback の atomicity を扱うため。 |
| 10 | PUYO-89: 監査・削除・安全性テスト | `high` | 収集、学習、評価、昇格を横断する最終 QA と fault test のため。 |

PUYO-58 は Jira key 順では PUYO-85 が先ですが、dataset writer と training の手戻りを避けるため、schema / replay contract である PUYO-86 を先に実施します。

## チケット別起動例

```bash
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-80 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-81 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="medium" "PUYO-82 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="medium" "PUYO-83 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="medium" "PUYO-84 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-86 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-85 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-87 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-88 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
codex --dangerously-bypass-approvals-and-sandbox -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-89 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-57-58-execution-plan.md に従ってください。"
```

## 次回セッションの開始手順

1. `getAccessibleAtlassianResources` で Atlassian 接続を確認する。
2. JQL `project = PUYO AND (key in (PUYO-57, PUYO-58) OR parent in (PUYO-57, PUYO-58)) ORDER BY parent ASC, key ASC` で PUYO-57 / PUYO-58 と子チケットの最新ステータスを確認する。
3. PUYO-95 は計画整備タスクとして除外し、PUYO-80〜PUYO-89 だけを実装対象候補にする。
4. `integration/puyo-53-58` を fetch / fast-forward し、必要に応じて `integration/puyo-57` / `integration/puyo-58` へ反映する。
5. 上の推奨順序から、未完了かつ依存が満たされた最初の 1 チケットだけを選ぶ。
6. 選んだチケットと推奨 `model_reasoning_effort` をユーザーへ提示する。
7. その 1 チケットの作業だけを実施し、PR base は `integration/puyo-57` または `integration/puyo-58` にする。
8. 対象チケット完了後は次チケットへ進まない。

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
| PUYO-86 | GUI で直接確認できる主対象ではない。viewer 連携を追加した場合だけ `python3 main.py` から replay / dataset viewer を目視する。 | trajectory schema validation、replay determinism、quarantine、index rebuild を CUI test と generated JSON で確認する。最低限 `python3 -m unittest tests.test_realtime_replay tests.test_lineage -q` に、追加した human dataset tests を含める。 |
| PUYO-85 | `python3 main.py` で human match collection の ON / OFF、保存先、停止操作、状態表示を確認する。OFF session では保存されないことを画面とファイル一覧で確認する。 | `python3 -m unittest discover -s tests -q` と、ON / OFF session の dataset directory / audit log の差分を示す command を提示する。 |
| PUYO-87 | GUI job controls を実装した場合は `python3 main.py` の training 画面で dataset selection、start / stop / cancel、active model 非変更を確認する。GUI がない場合はその旨を明記する。 | smoke training は `python3 -m train.train_realtime --config train/config/realtime_smoke.yaml --set run_id=puyo-87-smoke` を基準に、実装した human dataset sampler の指定を加えた実コマンドを提示する。 |
| PUYO-88 | `python3 main.py` の model / training / evaluation status 画面で champion、challenger、previous stable、promotion / rejection / rollback を確認する。GUI がない場合はその旨を明記する。 | fixed seed arena、promotion criteria、registry role 更新、rollback 再実行を CUI で確認する。既存基盤は `python3 -m eval.realtime_arena --policy-a first --policy-b random --games 1 --seed 58 --max-ticks 180 --paired-sides` と `python3 -m train.lineage --root runs --output /tmp/puyo-88-lineage.json --markdown /tmp/puyo-88-lineage.md` を使う。 |
| PUYO-89 | audit / deletion / safety view を追加した場合は `python3 main.py` で collection、dataset、derived model、promotion の追跡と削除前確認を目視する。GUI がない場合はその旨を明記する。 | `python3 -m unittest discover -s tests -q` に fault injection / deletion / rollback safety tests を含め、監査 Markdown または JSON report の生成 command を提示する。 |
