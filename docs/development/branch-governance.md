# ブランチ保護とモデルバージョンリリース運用

## 決定

このリポジトリでは、厳密な Git Flow ではなく、**モデルバージョンごとの短期統合ブランチを使う軽量 Git Flow**を採用する。

Git Flow は `main` と恒久的な `develop` を中心に、feature / release / hotfix を扱うリリース型のワークフローである。一方、Git Flow は現在ではレガシーとされ、長命なブランチは CI/CD を重くしやすい。ここでは `develop` を常設せず、各モデルバージョンのエピックを統合単位にする。

参考:

- [Atlassian: Gitflow Workflow](https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow)
- [GitHub: About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

## ブランチ構成

```text
master                         # 完了・リリース済みバージョンのみ
└─ integration/puyo-103-v1-7-0 # PUYO-103 / v1.7.0 の統合先
   ├─ PUYO-118/state-analyzer-schema
   ├─ PUYO-119/manager-baseline
   └─ PUYO-120/version-qa
```

- `master`: 完了済みモデルバージョンだけを含む安定ブランチ。直接変更しない。
- `integration/puyo-<epic-key>-v<version>`: 1 つのモデルバージョンだけの統合ブランチ。例: `integration/puyo-103-v1-7-0`、`integration/puyo-114-v1-7-1`。
- `PUYO-<task-key>/<description>`: 個別 Jira タスク用の短命な作業ブランチ。
- `hotfix/PUYO-<task-key>/<description>`: `master` のリリース済み内容を緊急修正する場合だけ使う短命なブランチ。

## 通常の開発フロー

1. モデルバージョンのエピックを開始するとき、最新の `origin/master` から対応する `integration/...` を 1 本作成する。
2. 個別タスクはその `integration/...` から作業ブランチを切る。
3. 個別タスクの PR は対応する `integration/...` を base にする。レビューとテストを通したら統合ブランチへマージする。
4. エピック内の全タスクが統合され、統合 QA・リリース判断・Jira の受け入れ条件が完了したら、`integration/...` から `master` への release PR を 1 件作る。
5. release PR をマージした commit をタグ付けして、必要なら GitHub Release を公開する。
6. 次の連続モデルバージョンの統合ブランチは、release PR がマージされた後の `master` から作る。これにより v1.7.1 は v1.7.0 のリリース済み内容を確実に含む。
7. release PR のマージ後、統合ブランチは削除する。長命な `develop` ブランチは作らない。

複数バージョンを並行して進める必要があり、後続バージョンが前バージョンに依存する場合は、後続の統合ブランチを先行バージョンの統合ブランチから切ってよい。ただし、先行バージョンの release PR をマージした直後に `master` を取り込む PR を作り、依存関係とマージ順を Jira に明記する。

## 緊急修正

`master` のリリース済み内容を直ちに直す必要がある場合だけ、最新の `origin/master` から `hotfix/PUYO-<task-key>/<description>` を作る。hotfix は PR 経由で `master` に取り込む。

修正が進行中のモデルバージョンにも必要な場合は、hotfix の release PR マージ後に `master` を対象の `integration/...` へ取り込む同期 PR を作る。個別作業ブランチへ直接 cherry-pick しない。

## `master` の保護

Git そのものはローカルで `master` に commit を作ることを禁止できない。誤操作の影響を確実に止める防壁は、ホスティング側で直接 push を拒否する branch protection / ruleset である。

`master` は GitHub の branch protection または ruleset で次を設定する。

- `master` を対象に **pull request 必須**を有効化する。
- 直接 push の例外・管理者 bypass を許可しない。
- force-push とブランチ削除を禁止する。
- CI が整備された後は required status checks を有効化する。
- PR 会話の resolve を必須にする。
- 独立したレビュー担当者がいる場合は 1 approval を必須にする。単独開発中は PR 必須を維持し、review approval 数は 0 とする。

Codex は server-side protection の有無にかかわらず `master` を checkout・commit・push しない。ローカル Git hook で commit を止めることもできるが、hook は無効化できるため server-side protection の代替にはしない。
