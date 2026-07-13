# PUYO-53 / PUYO-59 / PUYO-156: リアルタイム固定 tick 仕様

## 時間単位

realtime headless core は実時間 clock や sleep を参照せず、固定 tick の整数時刻だけで進む。

| 項目 | 定義 |
|---|---:|
| tick rate | `REALTIME_TICK_RATE = 60` ticks/sec |
| tick seconds | `REALTIME_TICK_SECONDS = 1 / REALTIME_TICK_RATE` |
| 重力 | `REALTIME_GRAVITY_INTERVAL_TICKS` |
| DAS 初回遅延 | `REALTIME_DAS_INITIAL_DELAY_TICKS` |
| DAS repeat | `REALTIME_DAS_REPEAT_INTERVAL_TICKS` |
| soft drop repeat | `REALTIME_SOFT_DROP_REPEAT_INTERVAL_TICKS` |
| 接地 lock | `LOCK_FRAME_LIMIT` ticks または `LOCK_CONTACT_LIMIT` contacts |
| 消去 flash | `REALTIME_VANISH_FLASH_TICKS` |
| 連鎖落下 tween | `REALTIME_CHAIN_DROP_TWEEN_TICKS` |
| 標準おじゃま遅延 | `REALTIME_ATTACK_DELAY_TICKS` |

秒単位の UI 定数は `src/core/constants.py` に残し、realtime 用コードと fixture は同ファイルの `REALTIME_*_TICKS` だけを参照する。

## 1 tick の処理順

`RealtimeHeadlessSimulator.step()` は tick N で次の順に処理する。

1. `TickInput.release` を適用して hold 状態と repeat 予約を解除する。
2. `TickInput.press` を適用し、hold 入力は即時発火し repeat 予約を作る。
3. 同 tick で repeat 時刻に達した hold 入力を発火する。
4. 左右同時 hold は横移動入力を相殺する。
5. `GameState.update()` へ発火入力を渡し、横移動、soft drop、回転、接地更新を適用する。
6. 重力 tick なら `GameState.step_gravity()` を適用し、落下した場合だけ接地更新する。
7. `animate` 中は control 入力を無視し、`GameState.advance_animation(tick_seconds)` だけを進める。
8. lock と resolution 完了を event として記録し、snapshot hash を生成する。

pause は `step()` を呼ばない状態、step は1回の `step()`、fast-forward は同じ `TickInput` mapping を使った複数回 `step()` と定義する。いずれも tick 列が同一なら結果 hash は同じになる。

## 状態と event

主要状態は既存 `GameState.state` をそのまま使う。

| 状態 | 意味 |
|---|---|
| `control` | 操作中ぷよが存在し、入力・重力・接地を受け付ける |
| `animate` | lock 後、落下・消去・連鎖演出を tick で進める |
| `ready` / `countdown` | UI 互換状態。headless realtime の標準初期化では即 spawn する |
| `gameover` | 窒息または不正状態で終了 |

event は次を持つ。

| event | 発生条件 | 主な payload |
|---|---|---|
| `lock` | `control -> animate` | axis x/y、rotation |
| `resolution_complete` | `animate -> control/gameover` | resolution score delta、attack score delta、chain end score、chain count、game over |

## 対戦 timing

`RealtimeVersusMatch` は2つの `RealtimeHeadlessSimulator` を同じ match tick で進める。各 tick は次の順で処理する。

1. 両 player の tick input をそれぞれの simulator に適用する。
2. 同 tick で完了した `resolution_complete` から攻撃量を確定する。
3. 生成攻撃を自分の incoming queue と早い着弾順に相殺する。
4. 両者の残り攻撃を同時相殺し、超過分を相手 queue に予約する。
5. 着弾 tick に達したおじゃまを pending にし、着弾可能な placement 境界に達した player の盤面へ最大 `max_ojama_drop` 個落とす。
6. 窒息と勝敗を判定する。

おじゃま生成には `resolution_complete.attack_score_delta` を使う。この値は今回と前回の連鎖終了時累積スコアの差で、連鎖がない resolution では0となり基準値も更新しない。`RealtimeVersusMatch` はこの差分に保持中の `score_carry` を一度だけ加え、70点単位の商と剰余へ分ける。これは turn-based の `VersusPuyoEnv` と同じ変換契約である。

片側が操作中、もう片側が連鎖演出中でも、それぞれの simulator は同じ tick 数だけ独立に進む。操作速度差、連鎖時間差、着弾 tick は queue と event の整数 tick で診断できる。

## Authoritative placement 境界

`arrival_tick` はおじゃまが着弾可能になる時刻であり、盤面へ即時挿入する時刻ではない。
到着済み packet は次の順序を満たす placement 境界まで queue に保持する。

1. 操作中ぷよを固定する。
2. 落下、消去、連鎖を最後まで解決する。
3. 生成攻撃を score carry からおじゃまへ変換する。
4. 到着済みを含む incoming queue と生成攻撃を相殺し、余剰を相手へ予約する。
5. game over でなければ、到着済み incoming を最大 `max_ojama_drop` 個だけ盤面へ落とす。
6. 次のぷよを操作可能にする。

`control` 中は arrival tick を過ぎても盤面を変更しない。現在ぷよを回転・移動して固定を
遅らせた場合、おじゃまの実着弾も同じ placement 境界まで遅れる。現在ぷよで連鎖を開始
できた場合、その攻撃による相殺を着弾より先に適用する。

1回の境界で落とせる量を超えた到着済み packet は queue に残す。残量は次 tick ではなく、
次のぷよの placement 境界で同じ順序を再適用する。packet の `arrival_tick`、`created_tick`、
`source_agent` は相殺、着弾、replay のすべてで保持する。

この順序は turn-based `VersusPuyoEnv` の「1 placement、連鎖解決、相殺、着弾」と同じ
game-rule contract である。realtime 固有なのは各 placement の途中に入力 tick と policy
deadline が存在する点だけで、game-rule event の順序は変更しない。

## Policy decision scheduler

GUI と headless arena は次の decision state machine を共有する。

`requested -> completed -> scheduled -> activated`

結果が期限を超えた場合は `timed_out -> fallback`、盤面が変わった場合は
`stale -> rejected` とする。render loop、wall-clock policy 計算、match tick を同じ clock と
して扱わない。

latency mode は実行前に次のどちらかを明示する。

| mode | activation tick | 用途 |
|---|---|---|
| `configured` | `request_tick + inference_latency_ticks` | 固定 seed benchmark、headless regression、再現 replay |
| `measured` | `max(completion_tick, request_tick + inference_latency_ticks)` | interactive GUI、実機負荷、cadence QA |

`configured` mode では policy の wall-clock elapsed は性能診断にだけ使い、match tick と
activation tick を変えない。`measured` mode では worker response を simulation loop が
受領した match tick を `completion_tick` とする。どちらの mode でも
`request_tick + timeout_ticks` を過ぎた結果は activation せず、fallback を使用する。

Python policy の探索は pygame event/render loop と実行境界を分離する。interactive GUI
では policy を専用 process に常駐させ、main process は request と response の受け渡し、
fixed-tick simulation、描画だけを担当する。headless arena も同じ scheduler state machine
を使い、同期呼び出しだけの特別な deadline semantics を持たない。

## Decision replay diagnostics

各 decision record と replay tick は次を保存する。

| field | 意味 |
|---|---|
| `latency_mode` | `configured` または `measured` |
| `request_tick` | observation と action mask を確定した match tick |
| `completion_tick` | scheduler が policy result を受領した match tick |
| `scheduled_activation_tick` | latency profile から算出した最短 activation tick |
| `activation_tick` | action plan を実際に有効化した match tick |
| `elapsed_seconds` | policy worker 内の wall-clock 推論時間 |
| `timeout_tick` | decision を無効にする match tick |
| `outcome` | `activated`、`fallback`、`timed_out`、`stale` のいずれか |
| `fallback_reason` | fallback または rejection の machine-readable 理由 |

deterministic replay は input と snapshot hash に加えて、上記 tick、latency mode、outcome の
一致を検証する。GUI QA は tick-limit 到達だけを成功条件にせず、scenario ごとの placement、
decision、attack/outcome、timeout、idle/cadence 条件を満たした場合だけ PASS とする。

## v1.0.0 互換境界

既存の `HeadlessPuyoSimulator` と `VersusPuyoEnv` は設置単位・turn synchronous API として維持する。

* 学習済み policy、action mask、placement-level golden score は従来 API を使える。
* realtime mode は `src/core/realtime.py`、`puyo_env/realtime_versus.py`、`puyo_env/action_planner.py` を使う。
* `attack_delay_steps` は turn 単位の互換設定、`REALTIME_ATTACK_DELAY_TICKS` は tick 単位の新設定として分離する。
* placement action から realtime input へ移行する場合は `plan_placement_action()` を経由し、到達不能時は `reachable=False` を明示する。

## Golden fixture

replay fixture は seed、tick 数、tick input、期待 hash を JSON で保持する。runner は `src/core/replay.py` の `assert_replay_matches_fixture()` を使う。

## 全消し runtime diagnostics

PUYO-151 以降、turn-based / realtime の runtime info と realtime match replay は
`puyo.all_clear_diagnostics.v1` を共通契約として使う。player ごとのフィールドは次の4つである。

| field | 意味 |
|---|---|
| `board_empty` | hidden row を含む現在盤面が空である |
| `all_clear_achieved` | 直前の連鎖解決で全消しが成立した |
| `all_clear_bonus_pending` | 次の連鎖で使う全消しボーナスを保持している |
| `all_clear_bonus_consumed` | 直前の連鎖解決で保持ボーナスを消費した |

runtime info は own field を上記名、相手 field を `opponent_` prefix 付きで公開する。
replay tick は `all_clear_diagnostics.schema_version` と `players.player_0/player_1` に同じ値を保存し、
replay 時に snapshot hash と併せて一致を検証する。この変更は既存
`puyo-realtime-match-v1` への additive diagnostics であり、入力と hash の形式は変更しない。
