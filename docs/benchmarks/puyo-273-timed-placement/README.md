# PUYO-273：live clock と配置 root の一致

## 原因と所有範囲

PUYO-266 の 30 seed × 2 repeat の raw で，requested/executed action が一致していても，実際の lock が別 root になる 2 件を確認した．これは PUYO-273 の「公開 snapshot／mask と実配置の整合」の追加対応とし，候補品質と G2 の再評価は PUYO-266，速度条件の同一入力比較は PUYO-264，human 入力と GUI 同期診断の残余は PUYO-269 が所有する．

* seed 137，34 手目：tick 1800 に action 12=(x3,RIGHT) を採用．同 tick が自然落下期限だったため RIGHT 後に y12→11 へ降下し，次の回転で左へ kick した．旧入力は tick 1833 に (x2,y11,RIGHT) で lock し，予測 12 連鎖に対し実際は 1 連鎖だった．
* seed 148，21 手目：tick 1198 に action 18=(x4,LEFT) を採用．tick 1200 の DOWN と自然落下が同時に進み，後続の右移動が通らなくなった．旧入力は tick 1237 に (x3,y7,LEFT) で lock し，予測 0 連鎖に対し実際は 8 連鎖だった．

従来の BFS は (x,y,rotation,blocked-rotation count) の幾何状態だけを扱っていた．`execute_planned_placement` も GameState だけをコピーして realtime simulator を作り直すため，tick と次の重力期限が 0/300 に戻り，旧経路を誤って正しいと確認できていた．同じ raw 入力の replay hash が一致しても，candidate root と実 lock の一致を保証するものではなかった．

## 修正契約

1. 幾何 BFS で入力候補を共有生成し，最初の lock まで live clock の control branch と同じ処理で検証する．RealtimeHeadlessSimulator 由来では tick，次の gravity tick，held/repeat，接地回数・時間，補間・floor-kick grace を保持する．GameState/headless 由来だけが新しい時計から始まる．
2. 検証は authoritative の `_collect_fired_actions`，`GameState.update`，`_apply_gravity_if_due` と実 `lock_puyo` を使用する．snapshot hash の生成や chain animation は行わない．field は lock 直前に grid 行をコピーし，live game を変更しない．この control probe と実 simulator の tick ごとの一致を回帰する．
3. 自然落下・kick で幾何予測から外れた時だけ，最大 2 回経路を修復する．初回の共有 BFS に加え，修復は全 action 合計で `max_expanded_states`（既定 2000）展開に制限する．同じ呼出し内の不変盤面に限って幾何 transition と修復 BFS を共有し，時計に依存する実入力検証は省かない．呼出しをまたぐ cache は持たない．
4. 最初の lock の x/y/rotation が目標と一致しない，修復予算が尽きる，または検証中に lock を確認できない場合は `reachable=False`，`inputs=()` を返し，mask でも除外する．予算による保守的な root 除外はあり得る．完全な到達可能性を列挙したとの主張はしない．候補の戦術順位関数は変更していないが，許可 mask の変化により選択結果が変わる可能性はある．
5. active plan は現在の clock/held 状態から残りの入力列そのものを最初の lock まで検証する．次 tick の release を含めて検証し，新規 plan を作り直して誤った abort を起こさない．各 seed fixture の全 cursor で suffix を再検証し，release を飛ばすと異なる root になる held/repeat 条件も回帰する．
6. 実行 helper は realtime source を clone して時計を維持する．検証済み plan は実 lock 後の不要な待機を省き，押しっぱなしの入力があれば解放する．deadline test は固定 62 tick ではなく実計画長を境界にして，推論の 1 tick が deadline に加算されることを検証する．

`src/core/realtime.py`，scheduler，native ABI，PUYO-269 の human 入力 API は変更していない．

## 固定 fixture と再現

[`tests/fixtures/puyo_273_timed_placement.json`](../../../tests/fixtures/puyo_273_timed_placement.json) は 2 run の対象 tick 直前の盤面，全落下ペア情報，clock/counter と旧入力を保存する．元 raw の hash，semantic digest，公開 snapshot digest を併記した．生成元は `/tmp/puyo266-native-g2-20260927/seed-{137,148}-repeat-1.json.gz`．`SafeNoThreatMatch(seed)` に `semantic.inputs` を対象 row の request tick 直前まで再生して取得した．native 探索をやり直す必要はない．

```bash
python -m unittest tests.test_timed_placement_planner -v
```

同じ盤面・tick・自然落下期限で，旧入力が誤 root と 1/8 連鎖を再現すること，修正入力が action 12/18 の root と予測 12/0 連鎖を実現し，配置・連鎖後の field も直接配置 simulator と一致することを確認する．seed 137 の修正列は RIGHT→ROTATE_RIGHT→ROTATE_RIGHT→RIGHT→ROTATE_LEFT，seed 148 は DOWN×3→ROTATE_LEFT→RIGHT．fixture で mask が許可した全 root について実 simulator の最初の lock を照合する．自然落下期限だけを変えた同一盤面では必要な入力列が変わることも検証する．

G2 全体の変更後 replay や再評価はこの fixture 成功と同一視しない．PUYO-266 の旧 60 run を新実装の結果として扱わない．

## GUI 再測定

計測条件と結果は conditions.json，summary.json，raw/ と manifest.json に保存する．初回の共有 BFS の証跡は [別ディレクトリ](../puyo-273-reachable-mask/README.md) に保持する．ルート直下の 8 trace は `79bbe41` 時点の中間測定として保持する．`suffix/` の最終 8 trace は source `c987ee9` に固定し，before では `f53252b` の幾何 planner と active-plan abort 判定を復元し，after では live-clock 検証・修復 transition 共有・残入力検証を実行する．同じ計測器とその他の source を両側で使用する．

```bash
DISPLAY=:0 /tmp/puyo271-final-venv/bin/python -m eval.puyo_273_gui_probe \
  --geometric-reference --output /tmp/puyo273-before-timed.json
DISPLAY=:0 /tmp/puyo271-final-venv/bin/python -m eval.puyo_273_gui_probe \
  --output /tmp/puyo273-after-timed.json
# 両側：--opponent nextgen_tactic_manager --frames 360
# human：--opponent human，軽量基準：--policy first
# 関数 profiler を外す比較：--minimal
python docs/benchmarks/puyo-273-reachable-mask/aggregate.py \
  --directory docs/benchmarks/puyo-273-timed-placement
```

両 run は同じ検証済み native SHA-256 `79b7d6f43305a27d169722ca014cfba09cd0e97932d49284896de63231bdb0d2`，seed 55，DISPLAY=:0，1120×780，60 FPS，速度 1.0．GUI と CPU-heavy 枠を PUYO-266 と直列に予約した．input の定義，IPC enqueue のみの計測，CPU sampling の限界は初回の資料と同じである．lock receipt は event の実 x/y/rotation と実行中 plan の root を比較し，人間に予定配置を仮定しない．

初回の時間対応版 `f723f81` は片側/両側 mask p95 が 44.2/50.1 ms となったため，同じ状態からの修復 BFS と transition の重複を除いた．初回 trace は `/tmp/puyo273-timed/` に保持し，中間比較 `/tmp/puyo273-timed/final/` と混ぜない．

### 最終結果（`suffix/`，source `c987ee9`）

値は p95/p99，単位は ms．各条件 1 run で，worker の実時間完了により進行 tick・候補盤面は一致しない．したがって分布差は測定値として扱い，同一入力の性能差を厳密推定したとはしない．

| 条件 | frame before → after | input schedule before → after | input→描画 before → after | mask p95 before → after | 比較可能 lock before → after（不一致数） |
| --- | --- | --- | --- | --- | --- |
| 片側 nextgen，600 frame | 31.8/62.6 → 35.1/77.0 | 37.8/70.3 → 45.1/67.9 | 71.2/105.4 → 72.5/98.4 | 4.9 → 25.9 | 15(0) → 16(0) |
| 両側 nextgen，360 frame | 66.4/109.2 → 89.6/145.1 | 63.5/110.8 → 91.7/141.4 | 125.1/177.1 → 169.8/224.4 | 6.6 → 23.9 | 8(1) → 10(0) |
| nextgen/human，600 frame | 35.0/77.4 → 33.1/82.2 | 41.0/77.3 → 39.0/62.8 | 68.9/122.5 → 79.9/108.9 | 6.4 → 32.6 | AI 5(0) → 6(0) |

幾何参照の両側 run では player_1 の tick 344 に予定 (x0,y1,DOWN) が実 (x1,y1,UP) で lock した．最終 after の計 32 件の比較可能 AI lock は一致した．人間の lock に予定 root を仮定せず，この件数に含めない．全最終 run の fallback/unreachable/replan/timeout/deadline miss は 0，scheduler error は空，worker cleanup 成功．stale decision は after 片側 6，両側 1/0，human 3 件で，棄却を無効化していない．中間 `79bbe41` は after 片側で nextgen fallback 1 と random fallback 2，replan 5/3 件があり，最終版の結果で上書きしない．

**固定 frame/input gate p95 ≤ 25 ms，p99 ≤ 50 ms は未達**．時間を含む検証による mask コスト増を隠さず残す．片側 after の scheduler accept/finish p95 は 62.1/20.7 ms，両側は 46.6/38.1 ms．両側 simulation/render p95 は 15.3/25.2 ms，frame 当たり tick catch-up p95 は 5.1．PUYO-269 の同期処理と組み合わせた検証が必要で，この PR は draft，Jira は In Progress を維持する．

軽量 first/random の frame は 19.7/25.3 ms，input schedule は 15.9/23.3 ms，input→描画は 31.9/35.3 ms．profiler と /proc sampling を外した片側は frame 53.2/91.5 ms，input schedule 59.1/115.0 ms，input→描画 108.0/179.4 ms．後者も gate 未達なので計測器だけを原因にはできない．ただし async worker による進行・配置が異なる単発比較であり，差分を profiler 自体の overhead と断定しない．完全無計測との差は未測定である．

human の before は 226 件投入/225 件処理，after は 227/226 件．各末尾 1 件は停止時 queue 残で，それ以外に ID 欠落・重複はない．UI 解放後かつ新規 press なしの横発火は before 左 6/右 7，after 左 0/右 0 回，held 不一致 tick は 61→0．回転 emitted/fired は両 run とも左 22/22，右 23/23．今回 after で過剰反復が観測されなかったことを入力 API の修正・合格とは扱わない．同 tick press/release の最小再現と中間 after-human の不一致 124 tick・余分な横発火左 16/右 6 は残っており，PUYO-269 が所有する．実際の人間操作 QA は未実施である．

### 回帰・証跡検証

planner/mask/realtime AI/replay/scheduler，GUI pause/step，独立進行，stale reject，human 下押しを含む **56 tests 成功（23.780 s）**．fixture は同じ public board/timing で旧・新入力の root と field を比較する．GUI のイベント処理件数や replay hash 一致だけで配置正しさを判断しない．

```bash
python docs/benchmarks/puyo-273-reachable-mask/aggregate.py
python docs/benchmarks/puyo-273-reachable-mask/aggregate.py --directory docs/benchmarks/puyo-273-timed-placement
python docs/benchmarks/puyo-273-reachable-mask/aggregate.py --directory docs/benchmarks/puyo-273-timed-placement/suffix
```

各 archive の native/source/host/settings と raw SHA-256 は manifest・conditions・raw に保存する．最初の追加実装 `f723f81` の集計は `initial_timed_summary.json`，中間 `79bbe41` の 8 raw は直下，最終 `c987ee9` の 8 raw は `suffix/`．元の共有 BFS 9 raw は既存ディレクトリに保持した．PUYO-266 G2 全件の再評価，PUYO-264 の同一入力性能比較，PUYO-269 の人間 QA は本証跡を代替とせず，各 Task で継続する．
