# PUYO-273：到達可能 mask の共有探索と GUI 再測定

**状態：未完了．PR は draft，Jira は In Progress．** mask 計算は短縮したが，片側・両側・human の frame/input gate が未達で，人間の変更後操作 QA も未実施である．

## 実装と整合性

`realtime_reachable_action_mask` が 22 action ごとに同じ BFS を始める処理を，`reachable_placement_actions` の 1 回の BFS に置き換えた．単一目標 planner と同じ transition・探索順・展開上限を使い，上限判定前の目標確認も維持する．状態をまたぐ cache は作らないため，mask cache hit は該当なし，各呼出しが現在の盤面・落下ペアに対する再探索となる．worker の search cache は別に集計した．

scheduler の公開 mask 契約，activation 時の authoritative 再検証，stale/cancel，native ABI は変更していない．空盤面・高い盤面・落下中の位置/回転/回転カウンタ・盤面変更・探索上限 0/8/50/2000 の比較で，従来の独立 BFS と mask が一致し，live state が変化しないことを確認した．既存の実配置・hidden row・公開 digest・replay hash・pause/step・独立進行・worker cleanup の回帰も通過した．

## 条件と証跡

事前条件は [conditions.json](conditions.json)．Intel Core Ultra 7 258V，WSL2 Linux，CPython 3.12.3，pygame 2.6.1，DISPLAY `:0`，1120×780，seed 55，60 FPS，速度 1.0，overlay off，最大 2400 tick．片側 nextgen/random と nextgen/human は 600 frame，両側は 360 frame．他の重量処理を止め，新しい process で直列に実行した．正式 run は全 frame で match が活動中だった．

正式測定 source は `049a02d`．before は同じ source/config/binary で計測器の `--reference-mask` が起点 `595dbed7851c4a995f1426103c1660fa8f0f0550` の mask 実装だけを復元する．after は共有 BFS を使用する．計測器への後続変更は終局後の無進行 tick を human ログに重複記録しない修正だけで，正式 run は全 frame 活動中なので測定対象に影響しない．再現は repository root で行う．

```bash
DISPLAY=:0 /tmp/puyo271-final-venv/bin/python -m eval.puyo_273_gui_probe \
  --reference-mask --output /tmp/puyo273-before-one.json
DISPLAY=:0 /tmp/puyo271-final-venv/bin/python -m eval.puyo_273_gui_probe \
  --output /tmp/puyo273-after-one.json
# 両側：--opponent nextgen_tactic_manager --frames 360
# human：--opponent human（50 ms ごとの合成 key edge，物理操作ではない）
# 軽量：--policy first，profiling overhead 比較：--minimal
```

検証済み native extension は source revision `9b64186087ea8b6e1219861ddbb534b39233966c`，SHA-256 `79b7d6f43305a27d169722ca014cfba09cd0e97932d49284896de63231bdb0d2`．全 run の native 証跡は raw/summary に含む．既定 Python の旧 native revision `3defcc2…` では全 decision が error fallback になったため，初回 run は棄却した．[manifest.json](manifest.json) にローカル棄却 trace の hash と fallback 件数を残し，正式集計に混ぜていない．

正式 9 run は [raw/](raw/) に gzip 保存し，[summary.json](summary.json) は各時刻の p50/p95/p99/max，worker cache hit/miss，CPU/RSS/thread/process，stale/deadline/fallback，入力件数を含む．以下は展開後 checksum と個票からの集計を再検証する．

```bash
python docs/benchmarks/puyo-273-reachable-mask/aggregate.py
```

## 固定 gate と結果

事前 gate は frame と input の p95 ≤ 25 ms，p99 ≤ 50 ms．input schedule は予定投入時刻→event 処理で，PUYO-269 の指標を維持した．別に実投入→event queue 消費，最初の simulation boundary，その後の描画完了を測った．状態境界まで到達しても，衝突や animation によって移動が成立するとは限らない．raw human tick の before/after・emitted/fired/held を合わせて確認する．

単位 ms．各欄は p95/p99．

| 条件 | frame before → after | input schedule before → after | event 投入→描画 before → after |
| --- | --- | --- | --- |
| nextgen/random，600 frame | 67.2/152.6 → 38.0/76.2 | 112.5/149.5 → 51.4/70.9 | 190.4/249.5 → 71.6/118.0 |
| nextgen/nextgen，360 frame | 240.1/390.1 → 98.7/156.8 | 278.7/382.4 → 116.6/173.8 | 417.9/499.1 → 228.4/300.9 |
| nextgen/human，600 frame | 72.0/160.5 → 41.0/82.6 | 110.9/155.8 → 54.5/104.6 | 181.4/242.6 → 76.9/161.4 |
| first/random，600 frame | 20.7/27.5（軽量基準） | 15.6/18.2 | 26.9/32.3 |

mask p95 は片側 130.3→7.4 ms，両側 124.8→9.1 ms．全正式 run の scheduler error/fallback/timeout/deadline miss は 0．探索の無効化や tick の間引きは行っていない．非同期の elapsed tick と snapshot 列は前後で一致しないため，固定状態の速度比較ではない．

片側 after の worker cache hit は 5 件，判断時間中央値 193.5 ms，miss は 6 件・684.0 ms．両側は hit 2 件・171.7 ms，miss 10 件・922.4 ms．mask の共有探索とは独立した worker 内再利用である．片側の親/worker 最大 RSS は 266/340 MiB，thread は 12/15，process は 2 件．両側は親＋worker 2 件で 3 process．全 worker は shutdown 後 `/proc` から消失した．CPU は 60 frame ごとの sample の差分であり，起動・終了を含む全 lifetime ではない．

`--minimal` は関数 wrapper と `/proc` sampling を外した比較で，frame/event/tick の基本時計は残す．軽量 full/minimal はともに 9.81 s，frame p95 は 20.7/20.6 ms．片側 full/minimal は 12.00/11.99 s，frame p95 は 38.0/38.6 ms．この各 1 run の差では profiling overhead をばらつきから識別できない．完全な無計測との差ではない．IPC は `submit_policy` の enqueue 呼出し時間だけで，queue feeder の serialize と受信 deserialize の内訳は未計測である．

## PUYO-269 へ引き継ぐ同期経路

mask を短縮しても，片側の `scheduler.accept` p95 は 55.7 ms，`finish` は 27.4 ms，`_complete_decision` は 60.6 ms，activation は 42.0 ms．両側 after は simulation p95 15.2 ms，render p95 30.3 ms，catch-up p95 は 6 tick（最大 12 tick）．GUI 同期処理と結果受領の残余原因として PUYO-269 で扱う．

具体的な呼出し点は `NextgenScheduler.accept` の `Diagnostics.from_dict`，`selection.validate_batch`，payload の `deepcopy`，および `finish` の batch validation・dataclass `replace` に伴う再検証・`diagnostics.to_dict` である．別の 360 frame run ではこの 2 関数だけを cProfile し，[scheduler-profile.json](scheduler-profile.json) と [計測スクリプト](profile_scheduler.py.txt) に保存した．計 1.013 s の計測対象のうち `_json_value` の自己時間 0.170 s，`_typed` 0.075 s，`deepcopy` 0.076 s が記録された．再帰を含む cumulative time は加算できず，profile run の時間は gate に使用しない．大きな diagnostics の型復元・JSON 化・digest 検証の重複を確認し，公開結果の検証を保ったまま UI thread の仕事を減らすことが次の対策候補となる．

## PUYO-269 へ引き継ぐ human 入力

50 ms edge の固定列で，下を保持しながら左右移動・左右回転を交互に入力した．before は 303 件投入/302 件処理，after は 247/246 件．各 run 最後の 1 件は停止直前の queue 残で，処理された ID に欠落や重複はない．回転の emitted/fired は before 左30/30・右30/30，after 左24/24・右25/25 で一致する．ただし配置の正しさまで証明するものではない．

UI 側で解放済み，かつその tick に新規 press がない横入力発火は，before 左32/右27 回，after 左5/右15 回．simulator に UI 解放済み hold が残った tick は 290→109 件．合成入力でも過剰反復が残り，人間が難しいと報告した操作を正常と判定できない．

最小再現は以下で，出力は `ui=[] / simulator=['RIGHT']` となる．`RealtimeHumanController` が同 tick の press/release を別配列にし，`RealtimeHeadlessSimulator._collect_fired_actions` が release→press 順で適用することで，再度押下状態になる．この順序の問題を PUYO-269 に引き継ぐ．

```python
from eval.realtime_versus_ui import RealtimeHumanController
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.constants import Action
match = RealtimeVersusMatch(seed=55)
human = RealtimeHumanController('player_1')
human.key_down(Action.RIGHT)
human.key_up(Action.RIGHT)
simulator = match.player_states['player_1'].simulator
simulator.step(human.next_input(match))
print('ui=', [a.name for a in human._held],
      '/ simulator=', [a.name for a in simulator.held_actions])
```

## 回帰と未達

`/tmp/puyo271-final-venv/bin/python -m unittest` で，`test_reachable_mask_batch`，`test_action_planner`，`test_realtime_ai`，`test_realtime_replay`，`test_nextgen_tactic_manager.SchedulerTests` と GUI の pause/step・独立進行・stale・human 下押しの既存 4 test を実行し，46 件成功（29.94 s）．この検証は性能値として使用していない．

残る A/C は固定 frame/input gate，変更後の実際の人間操作 QA，入力過剰反復，IPC serialize/deserialize の個別計測である．軽量基準にも投入→描画 p95 が 25 ms を超えるため，event 処理と描画までの定義を分けて再 QA する．PR 作成は A/C 完了を意味しない．
