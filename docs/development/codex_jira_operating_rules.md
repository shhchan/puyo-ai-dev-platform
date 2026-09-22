# Codex x Jira 運用ルール

このドキュメントは、本リポジトリでの Jira 連携時の Codex 行動ルールを定義します。

## 基本方針

- Jira は現行の Atlassian Rovo MCP v2 接続経由で操作する。Codex のプラグイン利用時は `Atlassian Rovo` を選び、`Atlassian Rovo (Legacy)` は使わない。
- Codex は参照・コメント・更新・遷移・起票を実行可能とする。
- 非自明な実装依頼では、`AGENTS.md` に従って Codex が機能単位で起票する。
- Jira / GitHub / Git の操作に失敗した場合は、理由を隠さずユーザーに報告する。

## Jira 接続情報

- Jira site: `https://shhchan.atlassian.net`
- cloudId: `46424ed5-7d42-4bff-bc2a-da4c296f8b5b`
- Codex CLI でカスタム MCP 接続を使う場合のサーバー名: `atlassian`
- カスタム MCP URL: `https://mcp.atlassian.com/v2/mcp`
- v1 からの移行時は旧接続を削除または無効化し、v2 で再認証する（OAuth resource が異なるため旧認証は引き継がれない）。手順は [セットアップ文書](vscode_codex_jira_setup.md) を参照する。

## Jira 操作手順

1. まず `getAccessibleAtlassianResources` で Jira site、cloudId、Jira へのアクセス可否を確認する。
2. `PUYO-12` のようなチケットキーが分かっている場合は、Rovo Search に頼らず `getJiraIssue` を使う。
3. コメントは `addOrEditJiraIssueComment` を使う。
4. ステータス遷移は `listJiraIssueTransitions` で遷移先と transition id を確認してから `transitionJiraIssue` を使う。
5. Rovo Search が `403` / `The app is not installed on this instance` を返しても、cloudId 指定の Jira issue API が使える場合があるため直接 API を試す。
6. 直接 API も認証エラーになる場合は、`codex mcp login atlassian` を案内する。

## 遷移ルール

- チケット指定で作業開始指示があった場合、可能なら `進行中` / `In Progress` へ遷移する。
- 作業が完了した場合、対象チケットを `COMPLETE` へ遷移する。
- `完了` / `Done` への遷移は人間が行うため、Codex は実行しない。
- 遷移失敗時は、失敗理由（権限/ワークフロー制約など）をユーザーに返す。

## コメント運用

- Jira コメントは「1作業セッションにつき1件/チケット」を原則とする。
- コメントには以下を含める。
  - 実施内容の要約
  - 変更ファイルまたは変更範囲
  - テスト結果
  - 残課題（あれば）
- コメントは作業完了時、またはセッション終了前に投稿する。
- 複数チケットにまたがる場合は、チケットごとに対応内容が分かるようにコメントする。

## 起票ルール

- 非自明な実装依頼では、Codex が機能単位で独立タスクを起票する。
- 軽微な質問・相談・説明依頼のみの場合は自動起票しない。
- ユーザーが起票方法を明示した場合は、その指定に従う。

## Git ブランチ / コミット運用

- 作業開始前に対象チケット用のブランチを作成する。
- ブランチ名は原則 `PUYO-13/xxx-yyy-zzz` の形式にする。
  - `PUYO-13` は対象 Jira チケット番号に置き換える。
  - `xxx-yyy-zzz` は作業内容を表す英語のケバブケースにする。
- 既に未コミット変更がある場合は、作業前に `git status` を確認する。
- ユーザーや別作業の変更が存在する場合は、commit 時に対象ファイルを明示してステージし、無関係な変更を混ぜない。
- 作業単位ごとに適宜 commit する。
- コミットメッセージは英語で端的に書く。

## Pull Request 運用

- 作業がすべて完了したら、ブランチを push して PR を作成する。
- PR 作成時に reviewer / review request は指定しない。
- PR タイトルは `[PUYO-13] タイトル（日本語）` の形式にする。
- PR description は日本語で、以下のフォーマットを使う。

```markdown
## What

## Why

## QA

## References
```

- `QA` には実行したテスト、未実行の場合はその理由を書く。
- `References` には関連 Jira チケット、親チケット、関連ドキュメントを記載する。

## 安全ルール

- 権限エラーや認証エラーは握りつぶさず、そのまま報告する。
- OAuth 期限切れ時は再ログイン手順（`codex mcp login atlassian`）を案内する。
