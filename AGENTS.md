# AGENTS 運用ルール（puyo_ai_dev_platform）

このリポジトリでは、Codex は以下を既定動作とする。

## 1. Jira 起票の既定動作

- 対象: 非自明な実装依頼（調査・実装・テストを伴う依頼）
- Codex は作業開始前に Jira を機能単位で分割して起票する。
- チケットは独立タスクとして作成し、作成後に `進行中` へ遷移する。
- 依存管理が必要な場合は親管理チケットへ `Relates` で紐付ける。

## 2. 実装時の Jira 更新

- 作業開始時は、対象チケットのステータスを `進行中` へ遷移する。
- Jira 操作では、まず `atlassian` MCP の `getAccessibleAtlassianResources` で接続確認する。
- 本リポジトリの Jira site は `https://shhchan.atlassian.net`、cloudId は `46424ed5-7d42-4bff-bc2a-da4c296f8b5b`。
- Rovo Search が 403 を返す場合でも、cloudId を指定した Jira issue API（取得・コメント・遷移・起票）を優先して試す。
- Jira コメントは「1作業セッションにつき1件/チケット」を原則とする。
- コメントには以下を含める。
  - 実施内容の要約
  - 変更ファイルまたは変更範囲
  - テスト結果
  - 残課題（あれば）
- 作業完了時は、対象チケットのステータスを `COMPLETE` へ遷移する。
- `完了` ステータスへの遷移は人間が行うため、Codex は実行しない。

## 3. Git / GitHub 運用

- 作業開始前に対象チケット用のブランチを切る。
- ブランチ名は原則 `PUYO-13/xxx-yyy-zzz` の形式にする。
  - 先頭は Jira チケット番号。
  - 後続は作業内容を表すケバブケース。
- 既存の未コミット変更がある場合は、ユーザー変更を混ぜないようにステージ対象を明示的に限定する。
- 作業単位ごとに適宜 commit する。
- コミットメッセージは英語で端的に書く。
- 作業がすべて完了したら PR を作成してレビュー依頼を出す。
- PR タイトルは `[PUYO-13] タイトル（日本語）` の形式にする。
- PR description は日本語で、以下のフォーマットを使う。

```markdown
## What

## Why

## QA

## References
```

### PUYO-53〜PUYO-58 の統合ブランチ運用

- PUYO-53〜PUYO-58 は `master` へ直接 merge しない。
- 統合検証用ブランチは `integration/puyo-53-58` とする。
- PUYO-53〜PUYO-58 の各作業 PR は、base branch を `integration/puyo-53-58` にする。
- PUYO-53 の既存 PR #17 は、必要に応じて `integration/puyo-53-58` へ取り込んで検証する。
- PUYO-54〜PUYO-58 の作業開始時は、原則として `integration/puyo-53-58` を起点に `PUYO-54/...` 形式の作業ブランチを切る。
- `master` への PR は、PUYO-53〜PUYO-58 を統合ブランチ上で目視確認・QA してから `integration/puyo-53-58` から作成する。
- 統合ブランチ上での QA では、ユニットテストだけでなく、画面または実行ログで目に見える動作確認を行う。

### PUYO-56 の単一チケット実行

- PUYO-56 / v1.4.0 関連の実装依頼では、開始前に `docs/development/puyo-56-execution-plan.md` を確認する。
- PUYO-56 の集約ブランチは `integration/puyo-56` とする。
- PUYO-56 子チケット（PUYO-74〜PUYO-79）の各作業 PR は、base branch を `integration/puyo-56` にする。
- PUYO-56 子チケットの作業ブランチは、原則として `integration/puyo-56` を起点に `PUYO-74/...` 形式で切る。
- PUYO-56 全体の統合確認が完了したら、`integration/puyo-56` から `integration/puyo-53-58` へ PR を作成する。
- PUYO-56 子チケット PR を `integration/puyo-53-58` へ直接向けない。既に直接向いている PR は `integration/puyo-56` へ retarget する。
- 「PUYO-56 を実施」のような広い依頼でも、1 セッションで実施する Jira 子チケットは 1 件だけに限定する。
- 対象チケットの作業開始前に、選択する Jira チケットと Codex CLI の `model_reasoning_effort` をユーザーへ提示する。
- 対象チケット完了後は、同一セッションで次の PUYO-56 子チケットへ進まない。次に実施すべきチケットと推奨推論レベルだけを提示して停止する。
- 推奨順序・推論レベル・起動例は `docs/development/puyo-56-execution-plan.md` を正とし、Jira の実ステータスや依存関係に差分がある場合は Jira を優先して更新案を提示する。

## 4. 例外と優先順位

- 軽微な質問・相談・説明依頼のみの場合は Jira を自動起票しない。
- ユーザーから明示的な運用指定がある場合は、その指示を本ルールより優先する。
- 権限/認証エラーは隠さず報告する（必要に応じて `codex mcp login atlassian` を案内）。
