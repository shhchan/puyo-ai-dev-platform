# PUYO Sprint 15／16 の評価・リリース計画

2026-10-10 の依頼者判断を記録する．Sprint 14 の PUYO-269／273 は実用上の人間 GUI QA を受け入れ，固定 8 条件 × 2 回の frame p95 ≤ 26 ms／p99 ≤ 50 ms，入力 p95 ≤ 25 ms／p99 ≤ 50 ms で Complete とした．旧 25 ms gate は 15／16 run の通過に留まり，minimal-two 初回の 25.59 ms を消さない．厳密 25 ms の安定化は Sprint 16 の設計課題へ残す．PUYO-266／274 は未完了のまま Sprint 15 へ移した．

## 評価の分離

| 評価 | 目的と合否の境界 | Jira |
| --- | --- | --- |
| とこぷよ相当の単独評価 | 相手のおじゃまがない場で本線を構築・発火できるか．ぷよぷよ eスポーツ・通ルール 4 色の配ぷよを使用し，事前登録した 30 pattern ID × 2 repeat を評価する．40 手構築＋最大 6 手発火を基本窓とし，平均最大実連鎖 ≥ 10，理由のない小発火 0，回避可能な窒息 0 を目標とする． | PUYO-275 → PUYO-266 |
| 対戦評価 | 予告おじゃまへの対応と，独立小連鎖またはキーぷよ未設置の本線部分を使った副砲を検証する．相手の攻撃で本線が中断され得るので，対戦中の平均最大実連鎖 ≥ 10 を合否に使わない． | PUYO-275 → PUYO-277 |
| 統合・人間 QA | 単独／対戦の証跡，配ぷよ方式と pattern ID，GUI replay，先読み表示・操作感を統合確認する． | PUYO-266／276／277 → PUYO-274 |

旧 G2 の 30 seed × 2 repeat は**停止相手・攻撃抑止**で測定されていた．したがって旧平均最大実連鎖 8.8667，premature 6，窒息 10 は，相手からのおじゃまだけでは説明できない．旧評価は完全ランダム配ぷよでの単独に近い失敗証拠として保持し，新しい eスポーツ配ぷよ・明示的なとこぷよ gate の達成にも，新しい対戦 gate の失敗にも読み替えない．どちらかが未達なら「次世代モデル全体の G2 PASS」や PUYO-256～258 の本学習開始を宣言しない．全 65,536 pattern を品質評価する必要はない．選ぶ pattern ID と評価条件は結果を見る前に固定する．

## 配ぷよ・リプレイの前提

現行 `src/core/tsumo.py` は各ぷよ色を独立抽選する．[配ぷよ公開者の一次資料](https://puyo-camp.jp/posts/86154)は四色通対戦の 65,536 行，1 行 128 手／256 個のぷよを示しているが，公開者自身が公式仕様との完全一致を保証していない．PUYO-275 はデータの来歴・利用条件・代表照合・checksum と，軸／子・色・128 手境界を確認してから採用する．既存の完全ランダムを互換選択肢として残し，将来のタイトル別 provider を追加できる境界を作る．pattern ID と未公開 future は AI の観測に渡さず，公開 current／NEXT／NEXT2 を守る．

現行 GUI は `--replay` と launcher の `replay_path` を明示すれば診断 replay を保存できるが，`auto` では保存しない．PUYO-276 は新しい replay engine を重複実装せず，人間 QA から既存形式へ自動保存し，結果・設定・実行 identity と保存先を共有しやすくする．元の seed 59／127 の人間対局は replay がなく，同じ seed を新しい配ぷよ方式の同一事象と扱えない．旧 seed corpus は legacy random の回帰として保存し，新評価は pattern ID と replay で追う．

## Sprint と依存

| Sprint | Jira | 着手・検証の単位 |
| --- | --- | --- |
| 15 | PUYO-275 | 完全ランダム／eスポーツ通の選択と配ぷよ source 検証．PUYO-266／277 に先行． |
| 15 | PUYO-266 | とこぷよでの本線品質．旧失敗 seed は別 corpus とし，新 pattern で評価し直す． |
| 15 | PUYO-277 | 攻撃対応・副砲の対戦 gate．本線構築 gate と独立に判定． |
| 15 | PUYO-276 | 人間 QA replay の自動保存・報告導線．既存 replay 契約を再利用． |
| 15 | PUYO-274 | 上記の統合 QA と未解決条件の追跡．既存 PR #182 は Sprint 14 部分成果としてレビュー可能にする． |
| 15 | PUYO-278 | v1.7.3 の release 境界・統合 branch 整合・release PR／tag の判断． |
| 16 | PUYO-279 | v1.8.0 のシミュレータ／GUI リファクタリングの**設計・実装 Task 起票のみ**．製品コードは変更しない． |

PUYO-275 Blocks PUYO-266／277，PUYO-266／277／276 Blocks PUYO-274，PUYO-274 Blocks PUYO-278．PUYO-279 は PUYO-278 と Relates で紐付けた．後続・未着手の新規 Task は To Do に置く．

## バージョン境界

現在の `master` は v1.7.2，レビュー中の stack #176 は `integration/puyo-228-v1-8-0` を最下段 base とする．依頼者の新しい順序は，強化学習前の状態を **v1.7.3** として release し，その後にシミュレータ／GUI のリファクタリングを **v1.8.0** で行い，さらに後段で本学習を進めるものである．既存 branch 名と新しい version 境界が異なるため，PUYO-278 は stack のレビュー・取り込み後に履歴を保持した v1.7.3 用統合 branch と release PR の具体的な SHA／差分を確定する．`master` への直接 push，既存共有履歴の force-push，release PR の無断 merge，release 前 tag は行わない．

Sprint 16 の PUYO-279 は，現行の同期 prepare／finish，deepcopy，GC と GUI 入力・描画を測定根拠に，責務境界，互換性，性能 gate，移行順序，rollback，独立実装 Task を設計する．v1.7.3 release 前に v1.8.0 の runtime 変更を混ぜない．
