# PUYO-108: v1.7.x Model Design

## Purpose

v1.7.x では，現状の `manager_rule` を単純に拡張するのではなく，局面解析を入力として
戦術選択・戦術パラメータ・探索条件を学習で改善していく階層型モデルを目指す。

`manager_rule` は `RuleBasedManagerPolicy` による固定ルールの router であり，局面から
`build_large`，`punish`，`counter`，`fire_max`，`survival` などの固定 worker profile を
1つ選んで実行する。これは解釈可能な baseline として有用だが，次の制約がある。

- 強化学習で戦術選択が変化しない。
- 戦術カテゴリと戦術の中身が固定されている。
- 相手の本線以外の攻撃オプション，速攻，本線保持，発火点，スキなどを詳細に理解して反応する構造ではない。
- 相殺・催促・大連鎖構築の判断が，人間の期待する「大連鎖志向だがリスクに反応する」挙動になりにくい。

PUYO-108 の成果物は実装ではなく，後続チケットで v1.7.x として段階開発できる仕様・設計の土台である。

## Target Behavior

新モデルの基本人格は「大連鎖志向」を置く。ただし，相手の状況に応じて次のような切替ができる必要がある。

- 相手が本線以外に短期高火力の攻撃オプションを持っていると推定される場合は，いつ攻撃されても対応できる形を準備する。
- 相手の盤面にスキがある場合は，速攻・催促・リーサル狙いを選べる。
- incoming がある場合は，完全相殺，より大きい連鎖での返し，カウンター準備，生存優先を局面に応じて選ぶ。
- 安全な局面では本線火力を伸ばす。
- 自滅や発火点喪失を避ける。

この判断は「相手の特定攻撃には必ずこの行動」のような固定ルールではなく，局面解析器が出した特徴をもとに学習型 manager が選ぶ。

## Architecture

```text
own and opponent board / current pair / visible next / ojama queue
score and attack history / tick and deadline / previous plan state
        |
        v
State Analyzer
  - own chain and attack forecast
  - opponent chain and attack forecast
  - short tactical attack / sub-chain / main-chain / trigger estimates
  - incoming, deadline, counter feasibility
  - board danger, all-clear context, opponent vulnerability
        |
        v
Learned Strategy Manager
  - selects tactical intent or latent option
  - emits tactical parameters
  - emits search budget and objective weights
        |
        v
Parameterized Beam Workers
  - generate and evaluate candidate plans under manager conditions
  - return the first placement action and diagnostics
        |
        v
training signal
  - win/loss, chain size, attack/cancel outcome, survival, pressure result
```

### Observed Inputs

初稿では入力を `board / next / ojama / opponent board` と省略したが，実際には次を観測入力として扱う。

- 自分と相手の盤面。
- 自分と相手の current pair / visible NEXT。相手の NEXT が観測可能なルールでは相手分も含める。
- 自分と相手の pending / incoming おじゃま，落下期限，おじゃま queue。
- 自分と相手の得点，送信済み・相殺済み・受信済みおじゃま統計。
- 全消し状態，全消しボーナスの有無。
- 現在 tick / turn / 残り入力時間 / policy decision deadline。
- 直近の行動履歴，発火履歴，連鎖中かどうか。
- 前回 manager が選んだ intent / tactical parameters / plan とその成否。
- ルール・環境メタデータ。例: 盤面サイズ，色数，最大可視 NEXT，対戦/リアルタイムモード。

これらをすべて neural network に生で入れる必要はない。盤面 tensor，時系列特徴，State Analyzer の structured diagnostics に分け，
manager が使いやすい表現へ変換する。

### Terminology

- `short tactical attack`: 本線以外に短時間で撃てる攻撃オプション。2ダブはこの一例であり，専用扱いしない。
- `sub-chain threat`: 相手の副砲・サブ連鎖による脅威。本線とは別に評価する。
- `main-chain`: 相手または自分が最終的に伸ばしたい最大連鎖候補。
- `trigger`: 発火点。
- `context`: 全消し直後，incoming がある，発火点が高い，連鎖中，残り入力時間が短いなど，盤面単体だけでは判断しにくい状態。
- `opponent vulnerability`: 相手のスキ。発火点がない，中央が高い，受けが狭い，短期火力がない，おじゃま落下直前など，攻撃・催促が通りやすい度合い。

### State Analyzer

State Analyzer はハードコードしてよい API 層として扱う。ここでは「判断」ではなく「観測の要約」を行う。

出力候補:

- 自分の即時火力，短期火力，本線見込み，発火可能性。
- 相手の即時火力，短期火力，本線見込み，発火可能性。
- 相手の攻撃オプション候補一覧。
  - 本線候補: 相手が最終的に大きく伸ばしそうな最大連鎖。
  - サブ連鎖/副砲候補: 本線とは別に短い時間で撃てる攻撃。
  - 即時発火候補: 1手以内に撃てる攻撃。
  - 短期発火候補: 2〜3手程度で撃てる攻撃。
  - 催促/速攻候補: 連鎖数は小さくても単位時間あたり火力が高い攻撃。
  - 対応困難候補: こちらの現在形から見ると相殺や受けが難しい攻撃。
- 相手が速攻中か，本線構築中か，発火点を持つかの推定。
- 相手盤面のスキ，窒息リスク，全消し状態。
- incoming 量，到達期限，相殺可能性，カウンター可能性。
- 自盤面の危険度，発火点の高さ，発火点喪失リスク。

重要な制約として，Analyzer は「相手が短期攻撃を持つなら counter する」のような行動ルールを持たない。行動判断は manager が学習する。
2ダブは「短期発火候補」かつ「単位時間あたり火力が高い副砲候補」の一例として扱う。

### Learned Strategy Manager

manager は設置手を直接決めるのではなく，戦術 intent と探索条件を出す。

初期段階では，人間が理解できる少数の戦術カテゴリを持つ。

- `build_main`: 本線を大きくする。
- `prepare_response`: 相手の攻撃に対応できる形を準備する。
- `counter_or_return`: incoming に対して相殺または上回る返しを狙う。
- `pressure`: 催促や短期攻撃で相手に発火を迫る。
- `lethal_attack`: 相手を倒せる短期火力を狙う。
- `all_clear`: 全消し局面の専用方針。
- `fire_main`: 本線発火を選ぶ。
- `survive`: 窒息や自滅を避ける。

ただし，これらは固定 worker 名ではなく，学習を安定させるための初期 schema として扱う。
各戦術の中身は次のような連続値・離散値パラメータで表現する。

- `target_chain`: 目標連鎖数。
- `target_attack`: 目標おじゃま量。
- `deadline_turns` / `deadline_ticks`: 期限。
- `danger_tolerance`: どの程度の盤面危険を許すか。
- `trigger_preservation_weight`: 発火点維持の重み。
- `harass_weight`: 催促の重み。
- `counter_margin`: 相殺をどれくらい上回るか。
- `search_depth` / `search_width` / `latency_budget_ms`: 探索予算。
- `chain_shape_weight` / `future_potential_weight`: 本線伸長・形の評価重み。

v1.7.x の段階開発では，まず明示カテゴリ + 学習パラメータで開始し，その後に latent option へ拡張する。
latent option 段階では，option の振る舞いをクラスタ分析や診断ログで後から命名する。

### Tactic Schema

後続のモデル更新で戦術そのものを動的に作成・進化させるには，戦術を固定名ではなく schema として表現する必要がある。
初期段階では人間が `build_main` などの `TacticSpec` を定義し，manager がそのパラメータを学習する。
次段階では `TacticSpec` の embedding や parameter distribution を学習し，似た戦術の分岐・統合・改良を可能にする。

```text
TacticSpec
  identity:
    id / name / version / optional human_label
  applicability:
    candidate conditions or learned embedding for suitable positions
  objective:
    target_chain / target_attack / deadline / counter_margin / pressure_goal
  constraints:
    danger_tolerance / trigger_preservation / max_latency / avoid_choke
  planner:
    beam_depth / beam_width / evaluation_weights / candidate_count
  termination:
    objective_achieved / timeout / danger_threshold / opponent_fired
  fallback:
    fallback tactic or safety behavior
  diagnostics:
    success_rate / mean_attack / mean_chain / response_success / usage_clusters
```

`applicability` は「この局面なら必ず使う」という rule ではなく，候補に出しやすい局面特徴または learned embedding として扱う。
最終判断は manager の policy / value によって行う。

### Manager Structure

初期実装では，完全に独立した複数 sub agent ではなく「一人の manager + 戦術別 proposal/evaluator head」を採用する。
独立 sub agent を複数作ると，学習同期，credit assignment，診断が複雑になるためである。
一方で，構造としては sub agent 的に解釈できるようにする。

```text
shared encoder
  -> build_main proposal/evaluator head
  -> response proposal/evaluator head
  -> pressure proposal/evaluator head
  -> fire_main proposal/evaluator head
  -> survive proposal/evaluator head
  -> final arbitration head
```

各 head は「自分の戦術を使うなら，この条件でこのくらい価値がある」という proposal を出す。
final arbitration head が proposal 間の比較を行い，最終的な intent / option / tactical parameters を選ぶ。
将来的に必要になった場合は，この構造を mixture-of-experts や独立 sub agent へ拡張する。

### Strategy Valuation

各戦術の評価値は固定式で決めない。State Analyzer の特徴，`TacticSpec`，planner preview を入力として，
学習済み policy / value network が評価する。

初期案では二段階にする。

1. 軽量評価:
   - State Analyzer の特徴量と各 `TacticSpec` の embedding を入力する。
   - 各戦術の logit，expected value，risk を network が出す。
   - ここで数個の候補戦術に絞る。
2. planner-preview 評価:
   - 上位候補だけ beam worker で短く preview する。
   - 予測連鎖，予測おじゃま，期限達成，危険度，発火点維持，候補数などを得る。
   - manager が preview outcome と analyzer feature を使い，最終 value を出す。

診断上は `learned_value`，`predicted_attack`，`deadline_success`，`danger_risk`，`trigger_preservation`，
`latency_cost` のように分解して保存する。ただし，それらの重みは人間が固定するのではなく，学習対象とする。

### Parameterized Beam Workers

beam search は候補生成・具体手探索の worker として残す。

manager は「どの worker を使うか」だけでなく，worker に渡す条件を出す。

例:

- 3手以内に 12 個以上のおじゃまを返す。
- 発火点を残しながら，6連鎖以上を狙う。
- 1手以内に安全度を上げる。
- 低遅延で相手の短期攻撃に備える。

worker は盤面と manager 条件をもとに候補手列を探索し，1手目を返す。診断として，候補手列，予測火力，
発火点維持，危険度，期限達成可否，相殺達成可否を保存する。

将来的には，beam が広く候補手列を生成し，学習済み value model が候補を順位付けする構成も検討する。
ただし，初期段階では manager が探索条件と評価重みを出す方式を優先する。

## Training Plan

最初から self-play のみで始めると学習が不安定になりやすい。そのため，段階的に学習を進める。

### Stage 1: Analyzer and Scenario Dataset

Stage 1 は初期対戦相手を作る段階ではない。モデルが学習で使う観測 API と評価用シナリオを整える段階である。

成果物:

- State Analyzer の入出力 schema。
- 相手攻撃オプション，本線見込み，短期火力，スキ，incoming，相殺可能性などの診断値。
- 固定盤面・固定 NEXT・固定おじゃま状態の scenario dataset。
- 各 scenario に対する analyzer 出力の期待値，または sanity check。
- GUI / replay / CUI で analyzer が何を見ているか確認できる artifact。

代表シナリオには，短期高火力のサブ連鎖，速攻，本線保持，発火点，incoming，相殺，カウンター，全消しを含める。
各シナリオでは「必ずこの intent を選ぶ」という固定教師ではなく，観測特徴と結果評価を記録する。
Stage 1 の目的は，学習前でも局面を観測・説明できる状態を作ることである。

### Stage 2: Bootstrapped Manager

Stage 2 では，Stage 1 の Analyzer と scenario dataset を使って初期 manager を作る。
ここで初めて policy に近いものができる。ただし，これは強い self-play model ではなく，
Stage 3 以降の対戦学習に乗せるための破綻しにくい初期 policy である。

入力候補:

- scripted scenario。
- 既存 baseline。
- 強い beam search の出力。
- 可能なら人間ログ。
- 既存 checkpoint。
- 手作業で作った局面評価。

behavior cloning は初期化として使うが，最終方針を固定しない。
初期 manager は明示カテゴリ + parameterized objective を出す。
目的は，最初から自滅する，まったく相殺しない，本線を伸ばせない，といった状態を避けることである。

### Stage 3: Mixed Opponent Training

- 固定 baseline，既存 checkpoint，人間由来モデル，過去世代モデルと対戦させる。
- 勝敗だけでなく，連鎖数，相殺，生存，催促成功，発火点維持を報酬・診断に入れる。
- opponent pool を使い，特定相手への過適合を避ける。

### Stage 4: Self-Play Improvement

- ある程度使える水準に達してから self-play を主軸にする。
- 過去世代との対戦を残し，戦術崩壊や自滅増加を検知する。
- latent option や option 数の増加を段階的に試す。

### Stage 5: Latent Strategy Evolution

- 明示カテゴリを超えて，schema 化された option を増やす。
- option ごとに使用局面，成功率，平均火力，対応成功率を記録する。
- 人間向けには診断ログから「催促型」「防御構築型」「本線伸長型」のように後付けで解釈する。

## Evaluation

v1.7.x の評価は，勝率だけではなく，目標行動ができているかを測る。

### Quantitative Metrics

- 平均最大連鎖数。
- 本線最大火力。初期目標は平均10連鎖級，将来的な理想は平均14連鎖級。
- 短期高火力のサブ連鎖・速攻への対応成功率。
- incoming に対する相殺成功率。
- incoming を上回って返せた割合。
- 催促で相手の発火を誘発できた回数。
- 発火点喪失率。
- 自滅率・窒息率。
- `manager_rule`，`beam`，`worker_large`，既存 checkpoint への勝率。
- 判断時間，deadline miss，stale decision 率。

### Scenario QA

GUI または replay で，人間が次を確認できる代表シナリオを用意する。

- 安全局面で大連鎖構築を継続する。
- 相手が本線以外に短期高火力の攻撃オプションを持つ局面で，防御または相殺準備に切り替わる。
- 相手にスキがある局面で，催促またはリーサル狙いを選ぶ。
- incoming に対して完全相殺，より大きい返し，生存優先を局面ごとに選び分ける。
- 全消し局面で専用方針を取る。
- 発火点を潰して自滅しない。

### Human-Facing Diagnostics

UI / replay / benchmark artifact には次を残す。

- Analyzer 出力。
- manager が選んだ intent / option。
- manager が出した tactical parameters。
- worker が見つけた候補手列。
- 選択理由の診断値。
- 期限達成，相殺達成，火力達成，危険度の結果。

「大連鎖志向だが，相手リスクに反応している」と人間が説明できることを合格条件の一部にする。

## Development Roadmap

v1.7.x は一度に完成させず，次の単位でチケット化する。

1. State Analyzer schema と診断 artifact を作る。
2. 短期高火力のサブ連鎖・速攻・本線・発火点・incoming の代表シナリオ dataset を作る。
3. parameterized objective を受け取れる beam worker API を整える。
4. 明示カテゴリ + 連続パラメータを出す manager policy を実装する。
5. bootstrapping 用の teacher / imitation pipeline を作る。
6. mixed opponent training と opponent pool を整える。
7. self-play 強化学習に移行する。
8. latent option 化と option 診断を追加する。
9. promotion gate / benchmark / GUI QA を v1.7.x 用に拡張する。

## Open Questions

- 初期戦術カテゴリを上記8個で始めるか，さらに絞るか。
- Analyzer の短期攻撃オプション・本線・発火点推定をどの精度で最初に実装するか。
- 人間ログを使う場合，どの形式で収集・正規化するか。
- reward における勝敗，連鎖数，相殺，催促，自滅防止の重みをどう段階調整するか。
- value model による beam candidate ranking を v1.7.x 初期に含めるか，後続に回すか。
