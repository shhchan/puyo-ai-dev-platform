# VSCode x Codex x Jira セットアップ手順（Jira Cloud / OAuth）

## 1. Atlassian 管理設定（管理者）

1. `Atlassian Administration > Apps > AI settings > Rovo MCP server` を開く。
2. OAuth 2.1 を有効にする。
3. `Allow Atlassian supported domains` を有効にする。
4. IP allowlist を使っている場合、開発端末の IP を許可する。
5. 必要なら Atlassian MCP App のインストール制約を解除する。

## 2. VSCode 側

1. 拡張 `Atlassian: Jira, Rovo Dev, Bitbucket` をインストールする。
2. 本リポジトリを開く（`.vscode/extensions.json` で推奨表示される）。
3. Jira チケット起票は VSCode 拡張経由で実施する。

## 3. Codex 側（実施済み）

この環境では以下を実施済みです。

```bash
codex mcp add atlassian --url https://mcp.atlassian.com/v1/mcp
codex mcp login atlassian --scopes read:jira-work,write:jira-work,read:me,read:account
codex mcp list
```

確認結果（要点）:

- MCP サーバー名: `atlassian`
- URL: `https://mcp.atlassian.com/v1/mcp`
- 認証: `OAuth`

## 4. 再認証・トラブル時

- OAuth を再実行:

```bash
codex mcp login atlassian
```

- 接続状態を確認:

```bash
codex mcp list
codex mcp get atlassian
```

- 設定削除:

```bash
codex mcp remove atlassian
```

## 5. 運用開始チェック

1. VSCode 拡張でテストチケットを 1 件作成する。
2. Codex にチケットキーを渡し、内容取得できることを確認する。
3. Codex でコメント追記を確認する。
4. 作業開始時の `In Progress` 遷移を確認する。
5. `Done` は明示指示時のみ遷移することを確認する。

詳細な運用ルールは以下を参照:

- `docs/development/codex_jira_operating_rules.md`
