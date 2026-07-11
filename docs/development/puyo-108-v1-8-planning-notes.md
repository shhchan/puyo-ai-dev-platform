# PUYO-108 Follow-up: v1.8.x Planning Notes

## 目的

このメモは，`v1.7.x` の開発完了後に，別の Codex セッションで `v1.8.x` の計画を立てるための引き継ぎ資料である。

`v1.7.x` では，`Adaptive Chain Manager` を playable model として育てる。
具体的には，State Analyzer，`TacticSpec`，学習済み manager，mixed-opponent training，self-play，lineage 可視化，
promotion-ready checkpoint までを扱う。

`v1.8.x` では，`v1.7.x` で得た強い playable model を土台に，「戦術そのものを柔軟に生成・進化させる」方向を検討する。

## v1.8.x に持ち越した理由

戦術そのものの生成・進化は難易度が高い。
`v1.7.x` に同時に入れると，次の問題が起きやすい。

- playable model としての強さが上がらない原因が，manager 学習なのか，tactic schema なのか，latent option なのか切り分けにくい。
- 説明不能な tactic が増え，GUI QA や promotion 判定が難しくなる。
- self-play の安定化と tactic evolution の不安定性が重なり，学習崩壊の原因追跡が難しくなる。
- lineage graph が複雑になりすぎ，v1.7.x でまず整えるべき lineage 基盤が固まらない。

そのため，`v1.7.x` では tactic を明示 schema として扱い，まず強い `Adaptive Chain Manager` を作る。
`v1.8.x` はその上で戦術進化を扱う。

## v1.8.x 計画開始時に読むべき資料

次の順番で読むとよい。

1. `docs/development/puyo-108-v1-7-model-presentation.html`
   - v1.7.x の全体アーキテクチャ。
2. `docs/development/puyo-108-v1-7-implementation-plan.md`
   - v1.7.x の version 分割，lineage graph specification，v1.8.x へ持ち越した理由。
3. v1.7.x の release runbook
   - 実際にどの checkpoint が champion candidate になったか。
4. v1.7.x の lineage graph/report
   - どのモデルがどの学習 run から生まれ，どの評価で採用/棄却されたか。
5. v1.7.x の tactic usage report
   - どの tactic がどの局面で使われ，どの tactic が弱かったか。

## v1.8.x で検討する候補

### Latent Tactic / Latent Option

明示された `TacticSpec` だけでなく，モデルが latent vector で戦術のバリエーションを表現する。

検討事項:

- latent option は何個から始めるか。
- 各 option に終了条件を持たせるか。
- latent option を人間が後から命名できる artifact をどう作るか。
- 説明不能な latent option を promotion から除外する基準。

### Tactic Clustering

v1.7.x の tactic usage logs をもとに，局面・行動・成功率で tactic をクラスタリングする。

検討事項:

- cluster の入力 feature。
- cluster を新 tactic 候補として採用する条件。
- 既存 tactic と新 tactic の重複判定。
- 人間が cluster の意味を確認する UI / report。

### Tactic Schema Evolution

`TacticSpec` 自体を分岐・統合・改良できるようにする。

検討事項:

- `TacticSpec` の versioning。
- tactic の parent / child lineage。
- tactic の deprecate / disable / fallback。
- tactic schema migration。

### Learned Planner Ranking

beam worker が生成した候補手列を，学習済み value model で順位付けする。

検討事項:

- ranking model を manager と一体化するか，独立 model にするか。
- candidate plan の feature。
- human / beam / self-play outcome を教師にするか。
- latency budget とのバランス。

### Tactic Ablation Suite

各 tactic を無効化したときの性能差を測る。

検討事項:

- tactic ごとの寄与度。
- tactic が有効な局面/無効な局面。
- redundant tactic の統合。
- dangerous tactic の quarantine。

## v1.8.x 開始時のヒアリング項目

Codex は，v1.8.x 計画開始時にユーザーへ最低限次を確認する。

1. `v1.7.x` のどの挙動に最も不満が残ったか。
2. 戦術進化をどの程度自動化したいか。
3. latent tactic は人間に説明可能であることをどこまで必須にするか。
4. tactic を増やす方向と，既存 tactic の parameter を強くする方向のどちらを優先するか。
5. planner ranking を v1.8.x 初期に含めるか。
6. training cost が大きくなる場合，どの範囲まで許容するか。
7. lineage graph に tactic lineage も統合するか。

## Jira 化方針

`v1.7.x` の仕様設計が合意され，Jira チケット化するときには，`v1.8.x planning notes` も Jira から参照できるようにする。

候補:

- PUYO-103 配下または次期エピック配下に「v1.8.x 戦術進化計画メモ」チケットを作る。
- チケット本文にこのファイルへの参照を入れる。
- `v1.7.x` 完了後に，このチケットを起点として別 Codex セッションでヒアリングを再開する。

このメモは `v1.8.x` の仕様を確定するものではない。
あくまで，`v1.7.x` で意図的に見送った内容と，次に考えるべき論点を欠損させないための引き継ぎである。
