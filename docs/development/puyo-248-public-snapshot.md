# PUYO-248 公開対戦 snapshot と時間要約

`puyo_env.nextgen_public_snapshot` は `agents.nextgen_contracts` の `PublicSnapshot` を生成する境界である．探索・戦術選択・hidden row 復元は実装しない．

## 公開情報境界

`match.public_snapshot(player_id=0)` を最初の `step()` より前に呼ぶと，その対戦の公開イベント観測を有効にする．戻り値は凍結された値であり，simulator，seed，環境の hash，未公開ツモ列を含まない．`reset()` は observer と履歴を破棄するため，新 episode の開始時に再度呼ぶ．途中から有効にした場合は，それ以前のイベントを復元しない．取得済み snapshot は以後の対戦進行に影響されない．

盤面は上から下へ 14 行 × 6 列であり，先頭の hidden/ghost 2 行は常に `None` となる．残りは画面で確認できる 12 行をコピーする．色は `PUBLIC_CELL_TO_COLOR` の逆写像で変換し，Enum の数値を使用しない．`drop_tween` と `garbage` では engine が先に配置済みの着地点を漏らさないよう，盤面全体を `None` にする．公開履歴からの hidden/ghost 復元は未実装である．unknown を空セルへ置換して探索に渡してはならない．

組ぷよは存在する current と NEXT/NEXT2 のみをコピーする．current のない連鎖・落下中には NEXT/NEXT2 の 2 組となる．carry は `0 <= carry < match.target_score_per_ojama` を検証し，全消し状態は公開フラグをコピーする．adapter 自身は得点変換・ボーナス消費を行わない．

incoming packet は公開 source/created tick/arrival tick が同じものを 1 batch にまとめる．ID は `受信 player:送信 player:created tick:arrival tick` であり，部分相殺や最大 30 個落下後も残量の ID が変わらない．snapshot 内の packet は未落下の残量なので `landed_tick=None` である．到着時刻を実落下時刻へ置換しない．

## 公開イベントと後続チケット用 API

`match.public_timing_history()` は `PublicTimingHistory` を返す．これも凍結値であり，`to_dict()`/`from_dict()` で保存・検証できる．この API も observer を有効にする．

- `packets`: `PublicPacketEvent(event_id, player_id, tick, kind, packet_id, amount)`．`kind` は `arrival/cancel/drop`．packet ID を phase の response target と結合できる．arrival は相殺後・落下前に記録するため，同 tick の到着と全量落下も両方残る．全量相殺された packet の arrival は生成せず，cancel に残す．部分相殺後の arrival amount は残量である．
- `resolutions`: `PublicResolutionEvent(event_id, player_id, tick, chain_count, generated, canceled, outgoing)`．連鎖なしの配置解決も記録する．ID は `tick:player_id:resolution`．攻撃生成・同 tick 相殺後の authoritative 値を保持する．同時相殺は新 packet を作らないため，resolution の canceled に記録し，架空の packet cancel を作らない．
- snapshot の `events`: `PublicEvent` の kind/tick を記録する．placement は lock，clear は連鎖解決完了を表す．lock のみでは発火を証明しない．action と cells は本実装では未記録であり，hidden 復元の根拠には使えない．

player ID は snapshot 視点で入れ替えず，対戦の絶対 ID `0/1` を保つ．ID は episode 内で一意であり，trajectory では episode ID と結合する．packet イベントと resolution イベントは同 tick を共有できるため，配列順を game-rule の処理順とみなさない．必要な結合は tick・player ID・packet ID で行う．取得履歴は episode 全体を保持する．

## 時間契約と RL 境界

`TimingProfile.from_match(match, latency_mode=..., inference_latency_ticks=..., timeout_ticks=..., operation_cadence=TickInterval(...))` は runtime 設定・実効 attack delay・garbage 時間・変換閾値・操作 cadence・latency mode を保存する．`schema_version` は `puyo.nextgen.public_timing.v1`，`digest` は保存値全体の semantic SHA-256 である．`execution_context(reachable_mask, request_tick)` は PUYO-244 の `ExecutionContext` を生成する．configured/measured を切り替えると digest も変わる．この profile は scheduler を変更しない．

`TickInterval(lower, upper, source, provenance)` の両端は tick 単位である．両端 `None` は未推定，上端だけ `None` は有限上限なしを表す．source は `visible_exact/public_estimate/sampled_future`，provenance は公開 witness や推定手順を識別する必須文字列とする．有限の予測区間も実未来の保証ではない．cadence は配置完了間隔であり，未測定なら unknown のまま使う．

`derive_timing_summary(snapshot, profile, request_tick=..., opponent_attack_candidate=..., landing_deadline=..., response_witness=..., opponent_next_operation=...)` は以下を返す．

- 脅威: packet がなければ，公開探索で攻撃候補なしを確認したときだけ none，候補ありなら potential，未探索なら欠損．packet がある場合は `(deadline.lower - request_tick) // cadence.upper` を保守的な操作余裕とし，1 以下は immediate，2 以下は pressing，それ以上は potential．必要な値が未知なら欠損．
- deadline: 明示 witness がなければ `max(request tick, 最早 arrival)` を落下の下限とし，上限は unknown．到着済みでも次の配置境界を勝手に確定しない．全 packet の arrival が既知でない場合は下限も unknown．
- 対応: `ResponseWitness(fire_start, fire_complete, generated_ojama, required_ojama, known_prefix)` の火力が足り，完了上限が deadline 下限以下なら possible．既知不足・期限超過は impossible，区間重複や sampled future・partial 根拠は marginal，未評価は unknown．発火開始だけで期限内とは判定しない．必要火力と生成量は探索側の公開 evidence を入力し，累積 score や bonus を再変換しない．
- 先打ち損失: 自分の `(fire_start, fire_complete]` に完了する相手の追加配置回数の下限・上限．`opponent_next_operation` は同じ絶対時計上の公開推定であり，相手の連鎖・garbage 待機も反映して入力する．区間の両端と cadence が揃わなければ欠損となる．

戻り値の `feature_summaries()` だけを PUYO-244 の `build_features()` に渡す．登録済み threat/response/first_fire_loss の 10 特徴だけを出力し，tick・simulator・履歴・ID は actor に入れない．回数はここで正規化せず，registry で 0〜3 に clip して 3 で割る．その他の未評価特徴も `build_features()` が missing bit 付きで扱う．探索候補生成・勝利保証・実際の manager 接続は後続チケットの範囲である．

## 検証

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python -m unittest tests.test_nextgen_public_snapshot tests.test_nextgen_contracts \
  tests.test_realtime_versus tests.test_realtime_ai \
  tests.test_realtime_terminal tests.test_realtime_replay -q
```

専用 fixture は非公開 queue/seed/hidden 変更不変性，色変換，設定依存 carry，連鎖中の相手進行，同 tick 相殺，全消し一回消費，到着と実落下の差，同 tick 到着・全量落下，31/60/61 個の残 packet，reset，区間・欠損・feature allowlist を検証する．
