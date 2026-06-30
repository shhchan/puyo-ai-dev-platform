# PUYO-56 実行計画

最終更新: 2026-06-18

## 目的

PUYO-56 / v1.4.0 は探索目標、探索制御、戦術表現、plan API、学習、評価を含む大きなエピックです。Codex CLI の 5h 制限とトークン消費を抑えるため、PUYO-56 関連作業は 1 セッション 1 Jira 子チケットに固定します。

## セッション原則

- 「PUYO-56 を実施」と依頼された場合でも、PUYO-74〜PUYO-79 を一括実行しない。
- 作業開始前に Jira を確認し、次に着手する 1 チケットと推奨 `model_reasoning_effort` をユーザーへ提示する。
- PUYO-56 の集約ブランチは `integration/puyo-56` とする。
- PUYO-56 子チケット（PUYO-74〜PUYO-79）の各作業 PR は、base branch を `integration/puyo-56` にする。
- 対象チケットのブランチは `integration/puyo-56` 起点で `PUYO-74/search-objective-schema` のように作成する。
- PUYO-56 全体の統合確認が完了したら、`integration/puyo-56` から `integration/puyo-53-58` へ PR を作成する。
- PUYO-56 子チケット PR を `integration/puyo-53-58` へ直接向けない。既に直接向いている PR は `integration/puyo-56` へ retarget する。
- Jira のステータス遷移、コメント、commit、PR は選択した 1 チケットだけを対象にする。
- 対象チケット完了後は次チケットへ進まず、次候補と推奨推論レベルを提示して停止する。
- 途中で時間やトークンが重くなった場合は、実装範囲を同一チケット内の未完了事項として残し、別チケットへ広げない。

## Codex CLI 推論レベル

この環境の `codex debug models` で、`gpt-5.5` は `low` / `medium` / `high` / `xhigh` をサポートしていることを確認済みです。PUYO-56 作業では既定値に頼らず、起動時に次のように明示します。

```bash
codex -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high"
```

`xhigh` は常用しません。複数回の失敗、広範囲な設計やり直し、依存関係の破綻調査が必要な場合だけ、ユーザーへ理由を示してから使います。

## 推奨順序と推論レベル

| 順序 | Jira | 推奨 | 理由 |
| --- | --- | --- | --- |
| 1 | PUYO-74: search objective schema | `high` | 後続全体の contract を決める基盤で、strategy workers、manager env、realtime observation、tests に影響するため。 |
| 2 | PUYO-75: 探索予算・評価重み・発火判断の学習最適化 | `high` | action space、mask / clamp、学習信号、checkpoint 復元にまたがるため。 |
| 3 | PUYO-77: N 手 plan API と再計画条件 | `medium` | DTO / adapter / replay diagnostics が中心で、PUYO-74・PUYO-75 の contract 後なら範囲を限定しやすいため。 |
| 4 | PUYO-76: 固定 profile に限定されない戦術表現 | `high` | option controller、latent strategy、fallback、評価比較を含むため。 |
| 5 | PUYO-78: curriculum・teacher・self-play 学習 | `high` | training pipeline、teacher data、lineage registry、rollback 条件まで扱うため。 |
| 6 | PUYO-79: benchmark と ablation | `medium` | 実装より evaluation suite、統計集計、report 整備が中心で、長時間実行は推論深度より実行管理の問題になるため。 |

## チケット別起動例

```bash
codex -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-74 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-56-execution-plan.md に従ってください。"
codex -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-75 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-56-execution-plan.md に従ってください。"
codex -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="medium" "PUYO-77 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-56-execution-plan.md に従ってください。"
codex -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-76 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-56-execution-plan.md に従ってください。"
codex -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="high" "PUYO-78 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-56-execution-plan.md に従ってください。"
codex -C /home/sion2000114/workspaces/dev/puyo_ai_dev_platform -c model_reasoning_effort="medium" "PUYO-79 を 1 チケットだけ実施してください。AGENTS.md と docs/development/puyo-56-execution-plan.md に従ってください。"
```

## 次回セッションの開始手順

1. `getAccessibleAtlassianResources` で Atlassian 接続を確認する。
2. JQL `parent = PUYO-56 ORDER BY key ASC` で PUYO-74〜PUYO-79 の最新ステータスを確認する。
3. `integration/puyo-56` を fetch / fast-forward し、これを起点に対象チケット用ブランチを切る。
4. 上の推奨順序から、未完了かつ依存が満たされた最初の 1 チケットだけを選ぶ。
5. 選んだチケットと推奨 `model_reasoning_effort` をユーザーへ提示する。
6. その 1 チケットの作業だけを実施し、PR base は `integration/puyo-56` にする。
7. 対象チケット完了後は次チケットへ進まない。
