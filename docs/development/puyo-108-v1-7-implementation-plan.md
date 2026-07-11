# PUYO-108: v1.7.0-v1.7.4 Playable Model Implementation Plan Draft

## 位置づけ

この文書は，`docs/development/puyo-108-v1-7-model-presentation.html` の設計内容を，
実際に動くぷよぷよ AI モデルのバージョンへ分割するためのドラフトである。

`v1.7.x` はぷよぷよ AI モデルのバージョンであり，各 version は「手を選択し，実際にプレイの様子を確認できるもの」とする。
内部基盤だけを作って終わる version は作らない。

Jira チケットは，この計画がレビュー・承認された後に起票する。
このドラフト時点では Jira 子チケットは作成しない。

## Versioning Rule

`x = 4` とし，`v1.7.0` から `v1.7.4` までの5段階で進める。

前回案に含めていた「戦術そのものを自動生成・進化させる latent tactic evolution」は，難易度が高く，`v1.7.x` の目的である
「強化学習を回してある程度強い playable model に到達する」と切り分けた方がよい。
そのため，戦術生成・戦術進化は `v1.8.x` の候補へ移す。

各 version の完了条件:

- `policy_type` または checkpoint として実行できる。
- `eval.realtime_versus_ui` で実際のプレイを確認できる。
- あるいは `python3 main.py` で起動するホーム画面から対象モデルを選んで確認できる。
- 補助的に `eval.versus_ui`，`eval.spectate`，replay artifact でも確認できる。
- `manager_rule` など既存 baseline と比較できる。
- その version で増えた診断情報が artifact として保存される。
- 次 version の開発開始前に，人間が挙動をレビューできる。

## Naming Policy

`v17` という表記は「version 17」に見えるため使わない。
コード識別子では `v1_7`，文書・metadata では `v1.7.x` と書く。

例:

- policy type: `v1_7_analyzer_manager`
- config: `train/config/v1_7_manager_bootstrap.yaml`
- artifact dir: `docs/benchmarks/puyo-v1-7-0-smoke/`
- metadata: `model_version: v1.7.0`

## Model Name

今回作るモデル名は `Adaptive Chain Manager` とする。

日本語では「状況に応じて連鎖方針を変える管理モデル」という意味で扱う。
v1.7.x の主目的が「大連鎖を基本にしつつ，相手の攻撃オプションや incoming に適応する」ことなので，この名前に固定する。

表記方針:

- UI 表示名: `Adaptive Chain Manager`
- metadata: `model_family: Adaptive Chain Manager`
- Python identifier: `adaptive_chain_manager`
- version: `model_version: v1.7.0` など，別 field で管理する
- policy type: `v1_7_analyzer_manager` など，version と役割を明示する

`Adaptice Chain Manager` は typo とみなし，正式名には使わない。

## 用語の説明

この計画では英語名を使うが，レビューしやすいように日本語の意味を併記する。

- Analyzer-driven playable baseline:
  - 日本語: 局面解析結果を使って手を選ぶ，学習前の動作確認用モデル。
  - 意味: State Analyzer が出した「相手攻撃オプション」「本線見込み」「incoming」などを使い，固定の簡易 scoring で tactic を選び，実際に beam worker に手を選ばせる。
  - 目的: 学習済み manager を作る前に，入力・診断・planner API・UI 表示が実戦経路で動くか確認する。
- Bootstrapped Learned Manager:
  - 日本語: 教師データで初期化した学習済み manager。
  - 意味: v1.7.0 の簡易 scoring を，behavior cloning / imitation で学習した neural manager に置き換える。
- Mixed-Opponent RL Manager:
  - 日本語: 複数種類の相手と戦わせて強化した manager。
  - 意味: 自己対戦だけに入る前に，既存 baseline・既存 checkpoint・人間由来モデルなどと戦わせて基本実力を底上げする。
- Self-Play Improved Manager:
  - 日本語: 自己対戦で強化した manager。
  - 意味: v1.7.2 で底上げしたモデルを，過去世代と戦わせながらさらに強くする。
- Promotion-Ready Champion:
  - 日本語: 昇格審査に出せる安定版候補。
  - 意味: 学習研究用 checkpoint ではなく，人間が GUI で確認し，promotion gate で比較し，registry に champion candidate として登録できる状態。

## Version Summary

| version | playable model | 日本語での位置づけ | 主目的 | 目視確認 |
|---|---|---|---|---|
| `v1.7.0` | Analyzer-driven playable baseline | 局面解析で動く最小モデル | 入力・診断・tactic・beam API を実戦経路でつなぐ | realtime UI / main.py で analyzer と tactic 選択を確認 |
| `v1.7.1` | Bootstrapped learned manager | 初期学習済み manager | behavior cloning / imitation で deterministic scoring を置き換える | bootstrap checkpoint の対戦を確認 |
| `v1.7.2` | Mixed-opponent RL manager | 複数相手で底上げした manager | baseline 相手に学習して自滅・無反応を減らす | opponent 別の挙動と勝率を確認 |
| `v1.7.3` | Self-play improved manager | 自己対戦で鍛えた v1.7 主力モデル | ある程度強いモデルになるまで training を積む | 世代比較・lineage・崩壊検知を確認 |
| `v1.7.4` | Promotion-ready v1.7 champion | 昇格審査可能な安定版候補 | GUI QA / promotion gate / release runbook | champion candidate として確認 |

## Shared Model Contract

すべての version は次の contract を満たす。

### Runtime Interface

- `select_action(observation, info) -> action` を持つ。
- action は既存環境で合法手として実行できる。
- `reset()` で episode 状態を初期化できる。
- `current_profile_name` または同等の表示名を返せる。
- `tactical_diagnostics` に analyzer / tactic / planner の情報を返せる。

### Required Artifacts

- model metadata。
- policy type。
- model family。
- model version。
- analyzer schema version。
- tactic schema version。
- planner schema version。
- training config。
- benchmark summary。
- lineage metadata。
- GUI / replay QA notes。

### Required Visual QA

必須:

```bash
python3 -m eval.realtime_versus_ui --policy-a human --policy-b <policy_type> --seed 123
```

または:

```bash
python3 main.py
```

`main.py` から起動するホーム画面で対象モデルを選び，対戦または観戦できること。

GUI QA の完了条件:

- GUI 経由で対象モデルを選択できる。
- 対戦または観戦を開始できる。
- 勝敗が決まるところまで停止せずに動く。
- 勝敗決定後，結果を確認できる。
- GUI を正常終了できる。
- QA artifact に使用 model，checkpoint，seed，対戦相手，勝敗，確認者向けメモを残す。

補助確認:

```bash
python3 -m eval.versus_ui --policy-a human --policy-b <policy_type> --seed 123
python3 -m eval.spectate --policy-a <policy_type> --policy-b manager_rule --seed 123
```

realtime UI と `main.py` 経路は，最終的に人間が普段触る確認経路として扱う。
headless benchmark だけで完了にはしない。

### Required Lineage

各 version は model lineage で進化を追える必要がある。

- parent model / parent checkpoint。
- training dataset / scenario dataset。
- training config。
- git commit。
- benchmark result。
- promotion / rejection history。
- tactic schema version。
- analyzer schema version。
- GUI QA artifact。

lineage 表示では，単に checkpoint の一覧を出すだけでは不十分。
「どの version で何が変わったか」「前世代より何が良くなった/悪くなったか」「どの診断を見るべきか」を人間が追える表示にする。

## Lineage Graph Specification

v1.7.x では，モデル進化を「直線の履歴」ではなく，有向グラフとして扱う。
目的は，version 間の関係と，同一 version 内の学習分岐を情報欠損なく追えるようにすることである。

### Node Types

| node type | 日本語での意味 | 例 |
|---|---|---|
| `model_version` | 仕様上のモデル version | `v1.7.2` |
| `checkpoint` | 実体としての重みファイル | `runs/.../checkpoints/latest.pt` |
| `training_run` | 1回の学習実行 | `v1.7.2-mixed-seed1` |
| `dataset` | 学習・評価に使った dataset | scenario dataset / human log |
| `config` | 学習・評価 config | `train/config/v1_7_manager_mixed.yaml` |
| `evaluation` | benchmark / GUI QA / promotion の結果 | paired arena report |
| `tactic_schema` | tactic registry / schema version | `tactic-schema-v1` |
| `analyzer_schema` | analyzer schema version | `analyzer-schema-v1` |

### Edge Types

| edge type | 日本語での意味 | 例 |
|---|---|---|
| `implements` | checkpoint が version を実装する | checkpoint -> `v1.7.2` |
| `derived_from` | checkpoint が別 checkpoint から派生した | `v1.7.2-B` -> `v1.7.2-A` |
| `trained_with` | training run が dataset/config を使った | run -> dataset |
| `produced` | training run が checkpoint を生成した | run -> checkpoint |
| `evaluated_by` | checkpoint が evaluation で評価された | checkpoint -> evaluation |
| `promoted_to` | checkpoint が champion / challenger 等に昇格した | checkpoint -> registry role |
| `uses_schema` | checkpoint が schema を使った | checkpoint -> tactic schema |
| `retargeted_from` | 別実験から引き継いだ | checkpoint -> previous experiment |
| `rejected_by` | evaluation により採用されなかった | checkpoint -> evaluation |

### Branching Semantics

同じ checkpoint から複数回 training を実行した場合，lineage は枝分かれとして表現する。

```text
v1.7.2-A
  -> training_run mixed-seed1 -> v1.7.2-B
  -> training_run mixed-seed2 -> v1.7.2-C
```

このとき，`v1.7.2-B` と `v1.7.2-C` はどちらも `v1.7.2-A` から派生した sibling checkpoint である。
どちらが強いか，どちらを次 version の親にするかは，evaluation node と promotion/rejection edge で表す。

### Required Lineage Metadata

各 checkpoint は最低限，次の metadata を持つ。

```yaml
model_family: Adaptive Chain Manager
model_version: v1.7.2
checkpoint_id: v1.7.2-B
parent_checkpoint_id: v1.7.2-A
training_run_id: mixed-seed1
git_commit: <commit_sha>
policy_type: v1_7_mixed_manager
analyzer_schema_version: analyzer-schema-v1
tactic_schema_version: tactic-schema-v1
planner_schema_version: planner-schema-v1
training_config_path: train/config/v1_7_manager_mixed.yaml
datasets:
  - scenario-dataset-v1
  - opponent-pool-v1
evaluations:
  - paired-arena-v1.7.2-B
  - gui-qa-v1.7.2-B
promotion_state: candidate | champion | rejected | archived
```

### Lineage View Requirements

lineage UI / report では，少なくとも次の view を持つ。

- Graph view:
  - checkpoint と version を node として表示する。
  - `derived_from`，`produced`，`evaluated_by` などの edge を表示する。
  - `v1.7.2-A -> v1.7.2-B` と `v1.7.2-A -> v1.7.2-C` のような分岐を見えるようにする。
- Version timeline:
  - `v1.7.0` から `v1.7.4` までを並べ，採用 checkpoint と rejected checkpoint を分けて表示する。
- Run detail:
  - 1つの training run について，親 checkpoint，config，dataset，seed，学習時間，生成 checkpoint を表示する。
- Evaluation comparison:
  - sibling checkpoint や前 version と，勝率，最大連鎖，相殺成功率，自滅率，tactic usage，latency を比較する。
- GUI QA evidence:
  - GUI で勝敗決定まで確認した seed，対戦相手，結果，メモを表示する。

### Lineage QA

各 version の完了時に，次を確認する。

- checkpoint から parent checkpoint へ辿れる。
- checkpoint から training config / dataset / git commit へ辿れる。
- checkpoint から benchmark / GUI QA artifact へ辿れる。
- 同一 parent から分岐した sibling checkpoint を比較できる。
- 採用された checkpoint と rejected checkpoint の理由を確認できる。
- model viewer または生成 markdown report で，人間が進化の流れを説明できる。

### Additional Hearing Needed

lineage 可視化については，実装前に次を追加ヒアリングする。

- graph view は GUI 内に直接表示したいか，HTML/Markdown report でもよいか。
- node の粒度は checkpoint 単位でよいか，training epoch / generation まで細かく見る必要があるか。
- edge label にどの metric 差分を表示したいか。
- rejected checkpoint をどの程度残すか。
- 長期学習で checkpoint が大量に出る場合，どの保持ルールにするか。
- GUI QA の証跡として screenshot / replay / JSON のどれを必須にするか。

## v1.7.0: Analyzer-driven Playable Baseline

### モデルとしての位置づけ

`v1.7.0` は，学習済み manager ではなく，State Analyzer と `TacticSpec` registry を使う playable baseline model とする。

日本語で言うと，「局面解析結果を使って手を選ぶ，学習前の動作確認用モデル」である。
目的は「Analyzer だけを作る」ことではない。
Analyzer の出力を使い，実際に tactic を選び，beam worker に手を選ばせ，realtime UI や `main.py` 経由でプレイ確認できるところまで作る。

この model は強さの最終候補ではなく，v1.7 系の最初の動く足場である。

### Behavior

- State Analyzer で相手攻撃オプション，本線，発火点，スキ，incoming，相殺可能性を診断する。
- 初期 `TacticSpec` registry から候補 tactic を出す。
- 学習ではなく deterministic scorer / heuristic arbitration で tactic を選ぶ。
- 選んだ tactic から parameterized beam worker に `PlannerRequest` を渡す。
- beam worker が手を選び，N-turn plan と診断を返す。

### Input

- 自分と相手の盤面。
- current pair / visible NEXT。
- pending / incoming おじゃま，deadline。
- 得点，送受信おじゃま統計。
- tick / turn / policy deadline。
- 前回 plan state。

### Output

- playable action。
- selected tactic。
- tactical parameters。
- planner request。
- visible N-turn plan。
- analyzer diagnostics。
- tactic selection reason。
- lineage metadata。

### Deliverables

- `policy_type`: `v1_7_analyzer_manager`。
- `model_family`: `Adaptive Chain Manager`。
- `agents/state_analyzer.py`。
- `agents/v1_7_tactics.py`。
- `agents/v1_7_analyzer_manager.py`。
- `train/config/v1_7_tactic_registry.yaml`。
- `eval/analyzer_scenarios.py`。
- `docs/benchmarks/puyo-v1-7-0-smoke/`。
- realtime UI / `main.py` launcher integration。
- model lineage entry。

### Acceptance Criteria

- `v1_7_analyzer_manager` が既存 arena / realtime versus UI / `main.py` 経路で手を選べる。
- `manager_rule` と1局以上対戦できる。
- analyzer が短期高火力サブ連鎖，本線，発火点，incoming，相殺可能性を artifact に出す。
- 2ダブは短期高火力サブ連鎖の一例として扱われ，専用 rule になっていない。
- UI / replay で「どの tactic を選んだか」と「なぜ選んだか」が確認できる。
- lineage で `manager_rule` との差分と v1.7.0 の診断項目が確認できる。

### Non-goals

- 強い学習済み model である必要はない。
- self-play は行わない。
- 戦術そのものの自動生成は行わない。

### Jira 化候補

- v1.7 analyzer schema and diagnostics。
- v1.7 initial TacticSpec registry。
- v1.7 parameterized beam request。
- v1.7 analyzer manager playable policy。
- v1.7 realtime UI / launcher diagnostics。
- v1.7 lineage entry。

## v1.7.1: Bootstrapped Learned Manager

### モデルとしての位置づけ

`v1.7.1` は，`v1.7.0` の deterministic scorer を学習済み manager に置き換える最初の version。
behavior cloning / imitation を使い，破綻しにくい初期 checkpoint を作る。

各 version で手を選べることを条件にしているため，この version でも checkpoint は必ず playable である。

### Behavior

- State Analyzer と `TacticSpec` registry は `v1.7.0` を引き継ぐ。
- shared encoder + tactic proposal/evaluator heads + final arbitration head を使う。
- manager が tactic と tactical parameters を出す。
- 上位 tactic は planner preview を使って評価できる。
- selected tactic から parameterized beam worker が手を選ぶ。

### Training Source

- `v1.7.0` analyzer-manager の行動ログ。
- scripted scenario。
- 強い beam search の preview outcome。
- 既存 checkpoint の対戦ログ。
- 可能なら人間ログ。

### Input

- `StateAnalysis`。
- `TacticSpec` registry。
- planner preview。
- scenario dataset。
- teacher labels または teacher outcomes。
- previous plan state。

### Output

- playable learned manager checkpoint。
- selected tactic。
- tactical parameters。
- manager decision diagnostics。
- BC / imitation metrics。
- lineage metadata。

### Deliverables

- `policy_type`: `v1_7_bootstrap_manager`。
- `agents/v1_7_strategy_manager.py`。
- `train/train_v1_7_manager.py`。
- `train/config/v1_7_manager_bootstrap.yaml`。
- bootstrapped checkpoint。
- BC metrics / confusion report。
- realtime UI / `main.py` QA artifact。
- lineage comparison against `v1.7.0`。

### Acceptance Criteria

- bootstrap checkpoint が arena / realtime versus UI / `main.py` 経路で手を選べる。
- `v1.7.0` と同等以上に自滅しにくい。
- 代表 scenario で tactic head の logit / value / risk が diagnostics に残る。
- `manager_rule`，`beam`，`v1.7.0` との smoke 比較ができる。
- lineage で `v1.7.0` から `v1.7.1` への変化が確認できる。

### Non-goals

- この段階で強い self-play champion を目指さない。
- 戦術そのものの自動生成は行わない。

### Jira 化候補

- v1.7 manager network。
- behavior cloning dataset builder。
- planner-preview training features。
- bootstrapped checkpoint save/load。
- v1.7 bootstrap GUI QA。
- v1.7 lineage comparison。

## v1.7.2: Mixed-Opponent RL Manager

### モデルとしての位置づけ

`v1.7.2` は，`v1.7.1` を実戦で改善する最初の強化学習 version。
自己対戦に入る前に，固定 baseline，既存 checkpoint，人間由来モデル，過去 checkpoint と対戦させ，基本実力を底上げする。

これは self-play の前に「最低限の対戦力」と「破綻しにくさ」を作る段階である。

### Behavior

- `v1.7.1` と同じ runtime interface を維持する。
- 対戦中に tactic selection と tactical parameters を学習済み policy で出す。
- reward は勝敗だけでなく，連鎖数，相殺，生存，催促，発火点維持，latency を含む。
- opponent pool により特定相手への過適合を避ける。

### Input

- `v1.7.1` checkpoint。
- opponent pool。
- reward config。
- scenario QA set。
- paired-side seed set。

### Output

- playable mixed-RL checkpoint。
- opponent 別勝率。
- tactic usage report。
- cancel / return / pressure / self-choke metrics。
- benchmark artifact。
- lineage metadata。

### Deliverables

- `policy_type`: `v1_7_mixed_manager`。
- `train/config/v1_7_manager_mixed.yaml`。
- opponent pool config。
- v1.7 benchmark suite。
- tactic usage analyzer。
- mixed training report。
- realtime UI / `main.py` QA artifact。
- lineage comparison against `v1.7.1`。

### Acceptance Criteria

- `manager_rule`，standard beam，worker baseline，既存 checkpoint，`v1.7.1` に対する比較結果が出る。
- realtime UI / `main.py` で `v1.7.2` の挙動を確認できる。
- 自滅率・deadline miss・latency が記録される。
- tactic 使用率が単一 tactic に崩壊していないか確認できる。
- 短期高火力攻撃への対応成功率が scenario QA と対戦ログで計測できる。
- lineage で mixed-opponent training による改善・悪化が確認できる。

### Non-goals

- 完全 self-play 最適化はまだ主目的にしない。
- 戦術そのものの自動生成は行わない。

### Jira 化候補

- v1.7 mixed opponent trainer。
- v1.7 opponent pool。
- v1.7 reward diagnostics。
- v1.7 benchmark suite。
- v1.7 tactic usage report。
- v1.7 lineage metrics。

## v1.7.3: Self-Play Trained Main Model

### モデルとしての位置づけ

`v1.7.3` は，`v1.7.x` の主力モデルを作る version。
`v1.7.2` で底上げした manager を self-play で十分に training し，ある程度強い playable model にする。

この段階で，`v1.7.x` として期待する強化学習ループを回せる状態に到達する。

### Behavior

- self-play 世代ごとに checkpoint を作る。
- 最新世代だけでなく，過去世代・固定 baseline・`manager_rule` と比較する。
- policy が単一 tactic に崩壊していないかを監視する。
- 「勝っているが低連鎖・自滅増加・説明不能」という状態を reject できるようにする。
- `v1.7.3` 完了時点では，時間をかけてある程度強いモデルになるまで training を積む。

### Input

- `v1.7.2` mixed-RL checkpoint。
- self-play config。
- previous generation checkpoints。
- fixed baseline pool。
- promotion gate draft criteria。

### Output

- playable self-play trained checkpoint。
- generation benchmark。
- collapse report。
- strong v1.7 champion candidate。
- lineage artifact。

### Deliverables

- `policy_type`: `v1_7_selfplay_manager`。
- `train/config/v1_7_manager_selfplay.yaml`。
- self-play run artifact。
- lineage registry integration。
- collapse detection report。
- generation comparison report。
- realtime UI / `main.py` QA artifact。
- stronger long-run checkpoint。

### Acceptance Criteria

- `v1.7.3` checkpoint が arena / realtime versus UI / `main.py` 経路で手を選べる。
- self-play を十分に回した long-run checkpoint が保存される。
- 世代ごとに勝率，連鎖数，相殺成功率，自滅率，tactic usage が比較できる。
- self-play で一見勝率が上がっても，自滅・低連鎖・単一戦術崩壊を検知できる。
- 過去世代に対する regression が可視化される。
- lineage で `v1.7.0` から `v1.7.3` までの進化が人間に分かる。

### Non-goals

- 戦術そのものを生成・進化させる latent tactic evolution は `v1.8.x` へ持ち越す。
- production promotion はまだ行わない。

### Jira 化候補

- v1.7 self-play trainer。
- v1.7 generation lineage。
- v1.7 collapse detector。
- v1.7 self-play benchmark report。
- v1.7 generation model viewer。
- v1.7 long-run training。

## v1.7.4: Promotion-Ready v1.7 Champion

### モデルとしての位置づけ

`v1.7.4` は，`v1.7.3` で作った主力モデルを，レビュー・QA・昇格審査に出せる状態へ整える version。

英語で hardening / promotion と書いていたものは，日本語では「安定化と昇格準備」である。
意味は，学習をさらに大きく変えることではなく，次を満たす状態に固めること。

- GUI で人間が代表局面を確認できる。
- benchmark と promotion gate で既存モデルと比較できる。
- model registry で champion / challenger / previous stable を追跡できる。
- 問題があれば rollback できる。
- release runbook に確認手順と判断理由が残っている。

### Behavior

- `v1.7.3` から安定した checkpoint を champion candidate として選ぶ。
- GUI / replay QA で代表局面を確認する。
- promotion gate で `manager_rule`，beam，worker baseline，既存 stable model と比較する。
- model viewer で analyzer / tactic / planner / lineage diagnostics を見られるようにする。

### Input

- `v1.7.3` champion candidate。
- benchmark suite。
- GUI scenario QA set。
- promotion criteria。
- model registry。

### Output

- playable promotion-ready checkpoint。
- promotion evaluation artifact。
- model registry update。
- GUI QA report。
- release runbook。
- final v1.7.x design / implementation summary。

### Deliverables

- `policy_type`: `v1_7_champion` または promoted checkpoint metadata。
- `train/config/v1_7_promotion_gate.yaml`。
- `eval/v1_7_promotion_gate.py` または既存 gate 拡張。
- model viewer diagnostics。
- GUI/replay scenario launcher。
- improved lineage view。
- `docs/development/puyo-v1-7-release-runbook.md`。

### Acceptance Criteria

- 人間が realtime UI / `main.py` / replay で「大連鎖志向だが，相手リスクに反応している」と確認できる。
- benchmark artifact に平均最大連鎖数，相殺成功率，短期攻撃対応成功率，自滅率，latency が含まれる。
- champion / challenger / previous stable を registry で追跡できる。
- rollback 可能な checkpoint artifact になっている。
- lineage view で `v1.7.0` から `v1.7.4` までの進化，勝率，連鎖数，相殺，自滅，tactic 使用率が確認できる。
- Open Questions に対する最終判断が release runbook に残る。

### Non-goals

- `v1.8.x` で扱う latent tactic evolution は含めない。
- v1.7.x 後の大規模再設計は含めない。

### Jira 化候補

- v1.7 promotion gate。
- v1.7 model viewer diagnostics。
- v1.7 GUI scenario QA。
- v1.7 release runbook。
- v1.7 champion registry integration。
- v1.7 lineage view improvement。

## v1.8.x へ持ち越す候補

`v1.8.x` では，`v1.7.x` の強い playable model を土台に，より柔軟な戦術進化を扱う。
引き継ぎメモは `docs/development/puyo-108-v1-8-planning-notes.md` に保存する。

候補:

- latent tactic / latent option。
- tactic clustering。
- tactic schema の分岐・統合・改良。
- 新 tactic 候補の自動生成。
- tactic ablation suite。
- 説明可能な latent tactic diagnostics。

`v1.7.x` では，これらを前提にしすぎず，まずは `TacticSpec` と学習済み manager を使って強いモデルを作る。

## 合意済みレビュー回答

現時点でユーザー合意済みの方針:

- `v1.7.0` は「局面解析結果を使って手を選ぶ，学習前の動作確認用モデル」とする。
- `v1.7.1` で初めて学習済み manager にする。
- `v1.7.2` で self-play 前に mixed opponent training を行い，基本実力を底上げする。
- `v1.7.3` で self-play を十分に回し，v1.7 系の主力モデルを作る。
- `v1.7.3` の training が非常に長時間になる場合は，可能な範囲で training し，処理最適化や追加 long-run を後続タスク化できるようにする。
- latent tactic evolution は `v1.8.x` に持ち越す。
- `v1.7.4` は `v1.7.3` の基本構造を変えず，より安定して強くなるモデルへ調整し，昇格準備する。
- Required Visual QA は realtime UI / `main.py` 経路を必須とし，GUI で対象モデル選択，対戦/観戦開始，勝敗決定，GUI 正常終了までを確認する。
- lineage の見せ方改善は `v1.7.x` の横断タスクとして含める。
- モデル名は `Adaptive Chain Manager` とする。

## 横断タスク

各 version に必ず含める横断作業:

- playable policy registration。
- realtime UI / `main.py` launcher integration。
- schema versioning。
- artifact manifest。
- checkpoint save/load。
- deterministic seed / paired-side evaluation。
- latency and deadline tracking。
- UI/replay diagnostics。
- model lineage metadata。
- model lineage viewer improvement。
- documentation。
- backward compatibility with `manager_rule` and existing worker baselines。

## レビュー観点

このドラフトでは，次の点をレビューしてほしい。

- `v1.7.0` を「局面解析結果を使って手を選ぶ，学習前の動作確認用モデル」とする方針でよいか。
- `v1.7.1` で初めて学習済み manager にする順番でよいか。
- `v1.7.2` で self-play 前に mixed opponent training を行い，基本実力を底上げする方針でよいか。
- `v1.7.3` で self-play を十分に回し，v1.7 系の主力モデルを作る方針でよいか。
- latent tactic evolution を `v1.8.x` に持ち越す方針でよいか。
- `v1.7.4` を「安定化・昇格準備」用の playable model release として分けるべきか。
- Required Visual QA を realtime UI / `main.py` 経路必須にしたことで十分か。
- lineage の見せ方改善を v1.7.x の横断タスクとして含めることで十分か。
- モデル名は `Adaptive Chain Manager` でよいか，別候補がよいか。

## 承認後の Jira 起票方針

この計画が承認されたら，PUYO-103 配下にバージョン別または機能別の Jira チケットを起票する。

起票単位の案:

- 各 version を親チケットにする。
- 各 version の Deliverables / Jira 化候補を子チケットにする。
- すべての version 親チケットに「playable policy / checkpoint / realtime UI or main.py QA」を A/C として入れる。
- lineage metadata と lineage view の確認を各 version の A/C に入れる。
- 依存関係は `Relates` または Jira の親子関係で表現する。
- 各 version 完了後に次 version の scope を再評価できるよう，すべてを一括で固定しすぎない。
