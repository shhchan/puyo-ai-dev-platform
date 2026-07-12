# PUYO-45: 対戦攻撃・相殺タイミング仕様

## joint step の phase

`VersusPuyoEnv` は各 joint step を次の順序で決定論的に処理する。

1. 両プレイヤーの合法手を検証する。
2. 両プレイヤーの設置と連鎖を独立に解決し、攻撃量を確定する。
3. 各プレイヤーの攻撃を、自分に届く予定の `ScheduledAttack` と早い着弾順に相殺する。
4. 両者に残った同時攻撃を小さい方の量だけ相殺する。
5. 超過攻撃を相手の着弾 queue へ追加する。
6. step を進め、着弾時刻に達した未相殺おじゃまを最大30個落下させる。
7. 窒息、勝敗、報酬、次 observation/info を確定する。

## おじゃま生成の得点基準

おじゃま生成へ渡す得点差分は、設置前後の差ではなく「今回の連鎖終了時累積スコア - 前回の連鎖終了時累積スコア」とする。初回の基準値は0で、連鎖が発生しなかった設置では基準値を更新しない。このため、soft drop など連鎖間に加算された得点も次の連鎖終了時に一度だけ含まれる。

確定した差分と前回の `score_carry` を加算してから70点単位へ変換し、商を生成おじゃま数、剰余を次回の `score_carry` とする。累積スコア全体と carry を直接加算してはならない。core は `last_chain_end_score` と `last_chain_score_delta`、headless result は `attack_score_delta` としてこの契約を公開する。

標準の `attack_delay_steps=1` では、step N で確定した攻撃は step N+1 の observation で予告される。防御側は step N+1 の行動と連鎖で相殺でき、その行動後に残量が落下する。

## 同時攻撃と相殺

攻撃計算は player 順に state を更新せず、両者の生成量を集めてから同時解決する。これにより player 0 / player 1 の処理順による差をなくす。

* 全相殺: 生成攻撃が既存 incoming 以下なら outgoing は0。
* 部分相殺: incoming の残量だけ queue に残る。
* 相殺超過: incoming を0にし、超過分を outgoing とする。
* 同時発火: 両者の既存 incoming 相殺後の生成量を相互に相殺し、差分だけ送る。
* 複数 packet: `arrival_step` の早い packet から相殺・落下する。

## info と集計

各 player の `info` は次を公開する。

* `pending_ojama` / `incoming_ojama`: 全 packet の合計。
* `incoming_turns`: 最短着弾までの残り joint step。
* `incoming_arrival_step`: 最短の絶対着弾 step。
* `incoming_attack_packets`: amount、arrival、source、created step。
* `generated_ojama_total`: 生成した総攻撃量。
* `canceled_ojama_total`: incoming と同時攻撃を相殺した総量。
* `sent_ojama_total`: 相殺後に相手へ予約した総量。
* `received_ojama_total`: 実際に盤面へ落下した総量。

## 既知の簡略化

この環境は設置単位の headless 対戦であり、実ゲームのフレーム単位の連鎖アニメーション時間、マージンタイム、複数回に分かれる細かな予告更新は表現しない。連鎖の長さにかかわらず joint step 終了時に攻撃量が確定し、標準では1回の応答手を与える。

学習上は「相手の発火を確認してから counter を選ぶ」判断を可能にする一方、連鎖時間差を利用する先打ち・後打ちの厳密な再現ではない。必要になった場合は `ScheduledAttack` に chain phase 時刻を追加し、現在の golden test を維持したまま細分化する。

## v1.1.0 realtime core との境界

この文書の `VersusPuyoEnv` は v1.0.0 互換の turn-synchronous API として維持する。v1.1.0 の fixed tick 進行は [puyo-realtime-timing.md](puyo-realtime-timing.md) を正本とし、次の責務分離にする。

| API | 時間単位 | 用途 |
|---|---|---|
| `HeadlessPuyoSimulator` | 1 placement | 既存 RL、golden score、action mask |
| `VersusPuyoEnv` | 1 joint placement step | 既存 self-play / arena |
| `RealtimeHeadlessSimulator` | 1 fixed tick | 入力列、落下、lock、連鎖演出、replay |
| `RealtimeVersusMatch` | 1 match tick | 独立進行、tick 単位攻撃 queue、着弾診断 |

`attack_delay_steps` は turn API 専用に残す。realtime では `REALTIME_ATTACK_DELAY_TICKS` と `RealtimeVersusMatch.attack_delay_ticks` を使い、同一 tick 内の攻撃生成・相殺・着弾順序は realtime 仕様に従う。
