# PUYO-269 desktop cadence と採用済み preview の接続

2026-09-28，PUYO-274 の desktop 再開．ノート PC の値は比較対照に使わない．frame/input の固定 gate は p95 ≤ 25 ms，p99 ≤ 50 ms．合成キー操作は実人間が意図した配置をできることの確認を代替しない．

## 条件と証跡

Core i7-14700F，28 logical CPU，RAM 約 15 GiB，WSL2，DISPLAY `:0`，CPython 3.12.3，pygame 2.6.1，1120×780，60 FPS 上限，速度 1.0，seed 55，nextgen_safe_build/native，overlay OFF．軽量 `first/random`，片側 `nextgen_tactic_manager/random`，両側 `nextgen_tactic_manager/nextgen_tactic_manager`，1P nextgen／2P human を別 process で直列実行する．軽量・片側・human は各 600 frame，両側は 360 frame，最大 2400 tick．合成 human 入力は 50 ms 間隔の DOWN 押下＋左右／回転 key edge．探索 quota，game tick，catch-up 上限は変更していない．

native `.so` SHA-256 は `d7e6fff7c808323fb367c632e1c87dd03bbfd122c0854dc4b09466ae90c9c654`，wheel は [environment.json](environment.json) の `b1c2baf3…`．source revision は `732ed3d…-dirty` だが，build 時の差分は親の未追跡実行表のみで，native/build/requirements-native に差分なしと親が確認した．package `__init__.py` の hash と binary の hash を区別して保存する．

各 raw は full source file SHA-256，native capabilities，host/CPU/memory，設定，per-frame/event/function span，cache hit/miss，process CPU/RSS/thread，lock receipt，worker cleanup を含む．process の起動／ready 待ちは frame 時計の外で，開始後の cold と warm は混在する．summary は first accept 前後を別集計するが，gate は全 active frame を使う．非同期の進行により判断／公開 snapshot の時系列は run 間で変わり得る．固定 seed と設定の GUI A/B を，同じ decision corpus の品質比較と扱わない．

```bash
# 旧描画を再現（同じ source の切替）
DISPLAY=:0 SDL_AUDIODRIVER=dummy .venv/bin/python -m eval.puyo_269_gui_probe \
  --legacy-render --output /tmp/legacy-one.json
# pure decoder を UI 上へ戻す（同じ source の切替）
DISPLAY=:0 SDL_AUDIODRIVER=dummy .venv/bin/python -m eval.puyo_269_gui_probe \
  --ui-decode --output /tmp/ui-decode-one.json
# 変更後
DISPLAY=:0 SDL_AUDIODRIVER=dummy .venv/bin/python -m eval.puyo_269_gui_probe \
  --output /tmp/reader-one.json
# 軽量：--policy first，両側：--opponent nextgen_tactic_manager --frames 360
# human：--opponent human，profiling の比較：--minimal
.venv/bin/python docs/benchmarks/puyo-269-desktop/aggregate.py
.venv/bin/python docs/benchmarks/puyo-269-desktop/receipt_microbenchmark.py
```

`aggregate.py` は raw_samples から分位点を再計算し，保存値の完全一致を検証する．[summary.json](summary.json) は展開後 SHA-256 と source fingerprint を持つ．nested span の時間は重複するため単純加算して CPU 費用としない．IPC send は親の queue feeder thread，deserialize/decode は result reader thread であり，GUI thread の同期区間と区別する．

## 描画の診断コピー

baseline source `3a86e81` は runtime 未変更．4 条件の frame p95/p99 は順に 18.9/21.5，26.7/53.5，42.6/82.4，22.5/54.3 ms．desktop でも gate 未達を再現した．`runtime.diagnostics.to_dict()` が各 frame の receipt 表示で full batch を再帰コピーし，さらに `last_decision.to_json()` でも同じ batch をコピーしていた．片側のこの区間 p99 は 35.5 ms，render p99 は 37.7 ms．

source `18066db` の `--legacy-render`／変更後を比較した．preview 生成は両側とも未導入，GUI 接続は同じ source，overlay OFF．下表は p95/p99，単位 ms．

| 条件 | frame 旧→新 | input schedule 旧→新 | render 旧→新 |
| --- | --- | --- | --- |
| 軽量 | 18.78/21.16 → 18.81/20.29 | 15.56/15.94 → 15.40/16.13 | 2.12/2.40 → 2.19/2.41 |
| 片側 | 24.76/64.97 → 19.64/52.74 | 28.74/55.50 → 16.00/31.04 | 8.24/36.53 → 2.81/3.59 |
| 両側 | 36.70/87.39 → 26.65/52.65 | 45.41/83.77 → 28.30/39.05 | 13.17/39.77 → 4.32/5.76 |
| human | 20.01/57.49 → 19.52/54.55 | 17.41/40.75 → 18.52/68.28 | 7.91/36.48 → 2.99/3.67 |

receipt 要約は必要な scalar のみを返し，full diagnostics への可変参照を返さない．完全な replay/ledger serialization は変更しない．[同一保存 batch の 100 回比較](receipt-microbenchmark.json) は全 summary が一致し，片側の full copy／新 read の p50 は 5.09／0.0071 ms．したがって GUI の進行差だけに依存しないコピー費用の削減も確認した．

片側 full/minimal の frame は 19.64/52.74／19.42/53.51 ms，軽量は 18.81/20.29／18.82/20.32 ms．この 1 run ずつでは小さな計測 overhead を区別できないが，計測器を減らしても gate 未達が残った．変更後片側の 50 ms 超 8 frame はすべて update 側に集中し，scheduler accept，finish，activation mask，catch-up が残った．GC gen2 も一部 tail に重なる．

## 結果受信側での純粋な型復元

`PolicyProcessExecutor` の既存 result reader thread で `Diagnostics.from_dict` と detached canonical wire の生成を行う．新しい process/thread は作らない．private handoff は immutable な型付き値と同じ wire 全体の型／値一致を UI accept 時に確認し，nested mutation があれば通常 parse へ戻る．`True == 1`／`1 == 1.0` の Python 同値で厳密な schema 型検証を迂回しない．普通の dict と invalid payload は既存の error/outcome 経路を通る．request identity/public/execution/phase/selected root と authoritative 到達性の検証は UI 側に残す．

decode 中の Future を登録に残して shutdown から cancel 可能にし，timeout/reset の cancel と完了が競合しても旧結果を再採用せず reader を継続する．固定 A/B は summary の `ui_decode` で識別する．

中間 source `78edae7` は controller の通常 dict 化で private proof を落とし，UI 再 parse が残っていた．`intermediate-*` の 9 run はこの二重復元を観測した不採用の証拠で，最終性能改善に数えない．source `22cece2` で private handoff の shallow copy を保持し，実 process→controller 経由で `from_dict` が reader 1 回／UI 0 回となる回帰を追加した．bool→int／int→float の同値型変更は proof を失効させ，従来の型拒否を維持した．

最終 source `22cece2`，描画コピーなし，overlay OFF の UI decode→reader A/B は次のとおり．各 1 run，p95/p99，単位 ms．

| 条件 | frame UI→reader | input schedule UI→reader | event 投入→状態 reader | event 投入→描画 reader |
| --- | --- | --- | --- | --- |
| 軽量 | 18.93/20.51 → 18.84/19.78 | 14.93/16.15 → 15.73/16.11 | 18.21/31.29 | 19.95/33.06 |
| 片側 | 19.80/52.33 → 23.01/42.65 | 15.70/17.61 → 16.27/24.73 | 22.87/29.00 | 25.92/36.01 |
| 両側 | 25.77/55.09 → 24.93/51.39 | 30.63/68.00 → 16.43/44.39 | 31.08/78.65 | 35.80/82.80 |
| human | 19.41/51.56 → 19.88/40.44 | 16.19/42.12 → 16.17/28.33 | 29.81/34.11 | 32.04/40.03 |

**軽量・片側・human は frame/input schedule の固定 gate に到達，両側の frame p99 51.39 ms は未達．PUYO-269/273 の完了としない．** event→state→draw は別指標として残し，schedule gate 達成を全操作応答の上限保証に読み替えない．無計測寄りの reader/minimal-one は frame 19.84/43.51 ms，input 16.31/30.91 ms で，同じ片側 gate を通過した．新 reader は GIL を共有しており，pure decode を別 process にした結果ではない．

片側 scheduler accept p50/p95 は 3.63/7.73 ms，reader decode は 17.49/38.28 ms．この 11 decision の受領では型復元を背景 thread に移せた．両側の 50 ms 超 4 frame はすべて update 側で，activation mask，finish，背景 decode が重なる．frame 106 は update 70.17 ms／GC 30.44 ms，frame 26/205/290 は update 49.05/43.73/48.04 ms，finish 11.03/10.70/10.94 ms，mask 15.60/9.36/13.86 ms．GC，decode と main の実行待ちを含む壁時計区間なので，nested 値の合計で因果寄与を断定しない．catch-up p95/max は軽量 1/2，片側 1/3，両側 1/5，human 1/3 tick．大きな残余は同期 activation と Python object の処理／GC にあり，raw の frame 対応表を残す．

最終 reader の sampled CPU は親 0.265–0.381 core，worker 0.684–0.902 core，最大 RSS は親 542 MiB／worker 617 MiB，thread 数は親最大 35／worker 最大 35．60 frame 間隔の process sample による値で，瞬間 CPU 競合は否定できないが，全 CPU 飽和を支配原因とする証拠は得られなかった．探索や thread 数の削減，GC 無効化，game tick 間引きは行っていない．

最終 4 条件の fallback/timeout/deadline miss はすべて 0，scheduler error は空，worker cleanup はすべて成功．stale は片側 4，両側 2/1，human 2 で棄却を維持した．AI の予定 root と実 lock は片側 6/6，両側 8/8，human 条件 6/6 が一致し，human 自身の 10 lock は比較外．human held 不一致 0，余分な横 fired 0，左右回転の emitted/fired は各 20/20．処理済み event ID の重複／内部欠落は 0，両側のみ終了時 queue に末尾 1 件が残り，他条件は全件処理した．

## Preview と回帰の境界

preview 生成自体は PUYO-274/#172 の担当範囲で，この branch へは取り込んでいない．GUI は `plan_id`，request identity/digest，candidate/root/first action，adopted receipt，現在の decision/active root を照合する．自盤面・公開組・phase・incoming・score/all-clear・own placement が変われば抑止する．相手側だけの独立進行では消さない．`o` の ON/OFF，表示理由，参考（次手保証なし）ラベルを追加した．`--overlay` で preview 状態変更を trace できる．実生成との合成 QA は親が所有する．

初期 GUI 48 test と reader 段階の回帰に加え，最終 source で次の **76 test が成功（44.527 s）**．[実行ログ](unit-tests.txt) を保存した．Ruff は既存 `E402` を除外した対象ファイルで成功した．`git diff --check` も成功．実人間の配置 QA は未実施であり，機械測定や合成入力を人間 A/C の完了としない．

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m unittest \
  tests.test_nextgen_reader_decode tests.test_nextgen_tactic_manager.SchedulerTests \
  tests.test_nextgen_preview_gui tests.test_nextgen_gui tests.test_realtime_versus_ui -q
```
