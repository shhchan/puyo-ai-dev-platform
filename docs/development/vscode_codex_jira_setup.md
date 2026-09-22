# VSCode x Codex x Jira セットアップ手順（Jira Cloud / OAuth）

## 1. Atlassian 側の前提

対象の Atlassian Cloud site と Jira へのアクセス権、OAuth 2.1 のブラウザー認証が必要。組織の許可設定やネットワーク制限で接続できない場合は、管理者に確認する。詳細は [Atlassian の現行セットアップ案内](https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/) を参照する。

## 2. VSCode 側

1. 拡張 `Atlassian: Jira, Rovo Dev, Bitbucket` をインストールする。
2. 本リポジトリを開く（`.vscode/extensions.json` で推奨表示される）。
3. VSCode 拡張は画面から Jira を操作する場合に利用する。Codex による Jira 操作には次節の MCP v2 接続を使う。

## 3. Codex 側（Atlassian Rovo MCP v2）

Codex アプリ / ChatGPT でプラグインを使う場合は、旧 `Atlassian Rovo (Legacy)`（MCP v1）を無効化または削除し、プラグイン一覧から現行の `Atlassian Rovo`（MCP v2）を追加して認証する。旧接続で認証済みでも、v2 は別の OAuth resource なので再認証が必要。

Codex CLI でカスタム MCP サーバーを使う場合は、次の URL を登録する。既に `atlassian` という名前で v1 URL を登録している場合は、`codex mcp get atlassian` で内容を確認してから旧設定を削除し、同名で追加し直す。既に v2 URL なら追加し直す必要はない。`codex mcp remove` は対象の設定を削除するため、独自のヘッダーやタイムアウトを設定している場合は先に控えておく。

```bash
codex mcp get atlassian                # 既存設定がある場合のみ
codex mcp remove atlassian             # 旧 v1 設定がある場合のみ
codex mcp add atlassian --url https://mcp.atlassian.com/v2/mcp
codex mcp login atlassian
codex mcp list
```

OAuth の承認画面で、この Jira site に必要な権限を確認する。Codex アプリのプラグイン接続と CLI のカスタム MCP 設定は別の導線であり、利用する側で接続状態を確認する。

Codex セッション開始後は、`getAccessibleAtlassianResources` で `https://shhchan.atlassian.net`、cloudId `46424ed5-7d42-4bff-bc2a-da4c296f8b5b`、Jira へのアクセス可否を確認する。次に既知のチケットキーで `getJiraIssue` を実行し、データが取得できることを確認する。コメントには `addOrEditJiraIssueComment`、遷移候補の確認には `listJiraIssueTransitions`、遷移には `transitionJiraIssue` を使う。各 Jira API には cloudId を指定する。

Rovo Search が `403` / `The app is not installed on this instance` を返しても、cloudId を指定した Jira issue API は使える場合がある。まずチケットの直接取得を試す。

## 4. 再認証・トラブル時

- 認証が失効したら、利用中のプラグインの接続画面で再認証する。CLI のカスタム接続なら次を実行する:

```bash
codex mcp login atlassian
```

- 接続状態を確認:

```bash
codex mcp list
codex mcp get atlassian
```

- 認証に失敗する場合は、Legacy が有効になっていないか、`codex mcp get atlassian` の URL が v2 か、Jira site への権限があるかを確認する。CLI の再ログインが拒否される場合は `codex mcp logout atlassian` の後に再ログインする。

## 5. 運用開始チェック

1. Codex から既存の Jira チケットを取得し、site / cloudId と内容を確認する。
2. 実作業チケットでコメント追記と `In Progress` 遷移を確認する。
3. 作業完了時は `COMPLETE` まで遷移し、`完了` / `Done` への遷移は人間が行う。

詳細な運用ルールは以下を参照:

- `docs/development/codex_jira_operating_rules.md`
- [Atlassian: MCP v1 から v2 への移行](https://support.atlassian.com/atlassian-ai-gateway/docs/how-to-upgrade-from-atlassian-rovo-mcp-v1-to-atlassian-rovo-mcp-v2/)
- [Atlassian: 接続の検証とトラブルシューティング](https://support.atlassian.com/atlassian-ai-gateway/docs/troubleshoot-and-verify-your-setup/)
- [OpenAI Docs: Codex の MCP 設定](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
