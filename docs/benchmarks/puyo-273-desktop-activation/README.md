# PUYO-273 desktop activation の局所 witness 再利用

2026-10-08 の Sprint 14 証跡．最終 source `ec2d3c0` は両側 input schedule の gate を通過したが，frame p95 が **25.1802222 ms** で固定 25 ms gate に未達．Jira は In Progress，PR は draft のままとする．初回 source `b7365ba` の input p99 55.71 ms という失敗も保存し，追加実装前後の測定を区別する．

## 変更と検証境界

mask が検証済み timed plan を呼出内の mapping へ返し，activation と fallback の同じ同期呼出だけで再利用する．次 tick／request にまたがる cache は持たない．prepare の公開 mask，activation 前の public stale 判定，board legality，live gravity deadline，held/repeat，first-lock の検証，最大 2 回の repair と探索予算は維持する．geometry edge の汎用 `copy.copy` を，dict を持つ game probe の同じ浅い snapshot へ置き換えた．scheduler，wire，native ABI，game tick，探索 quota は変更しない．

固定状態の [microbenchmark.json](microbenchmark.json) は，空盤面と seed 137/148 の既存 timing 反例で全 plan の入力列・root 等が旧実装と一致し，実 first-lock と source 不変を検証する．60 回交互測定の mask 中央値は 9.74→7.64，3.53→3.17，9.59→8.72 ms．GUI gate の代替には使わない．

## 初回 A/B 条件

[conditions.json](conditions.json) を測定前に保存した．Core i7-14700F／28 logical CPU，WSL2，CPython 3.12.3，pygame 2.6.1，DISPLAY `:0`，1120×780，60 FPS 上限，速度 1.0，seed 55，native `nextgen_safe_build`，overlay ON．native binary SHA-256 は `d7e6fff7c808323fb367c632e1c87dd03bbfd122c0854dc4b09466ae90c9c654` で 9/28 desktop と同一．親の排他枠で全 run を別 process／直列に実行した．

`probe.py --reference-plans` は同 source/config/binary 上で起点 `c812b3f` の planner を復元し，旧 mask が plan を破棄して activation で再探索する経路を再現する．baseline source/hash と probe 自体の hash を raw へ保存する．片側 before/after 各 600 frame，両側各 360 frame，変更後 human/light 各 600 frame，minimal one/light 各 600 frame の計 8 run．開始後の cold と warm を含む．非同期進行のため request 列は異なり，同一 decision corpus の品質比較ではない．

input schedule は予定投入 `expected_ns`→handler `handled_ns`，queue は実投入 `posted_ns`→handler，event→state/draw は実投入→最初の simulation boundary／その後の描画である．state boundary は意図した移動成立を保証しない．機械 gate は frame/input schedule の p95 ≤ 25 ms，p99 ≤ 50 ms を維持する．

| 条件 | frame p95/p99 ms | input schedule p95/p99 ms | event→draw p95/p99 ms |
| --- | --- | --- | --- |
| one before | 22.00/48.64 | 18.09/27.32 | 30.54/53.69 |
| one after | 22.59/42.38 | 19.74/39.53 | 33.41/48.59 |
| two before | 25.38/61.88 | 24.44/55.47 | 46.84/83.13 |
| two after | 22.25/49.60 | 18.98/55.71 | 39.83/61.04 |
| human after | 22.49/44.81 | 20.97/35.95 | 32.58/45.43 |
| light after | 19.57/21.49 | 19.58/21.18 | 24.08/34.89 |
| one minimal | 23.05/39.66 | 19.79/25.32 | 30.89/41.12 |
| light minimal | 19.61/20.95 | 19.66/21.39 | 20.71/34.46 |

two は frame gate に到達したが input p99 は未達．minimal も片側 gate を通過するが，1 run ずつなので profiling overhead と run 間のばらつきを厳密分離できない．event→draw は one/two/human の p95 が 25 ms を超え，schedule 通過を入力全体の上限保証にしない．

## 残余 tail

after-two の input event 72 は frame 203 で処理され，予定→投入 19.99 ms＋queue 43.27 ms＝schedule 63.26 ms．この待ち区間の MainThread では frame 202 の mask 2 件が計 17.29 ms，finish が計 10.90 ms，これらを含む activation が計 34.54 ms だった．reader decode も 28.33 ms 重なる．event 104 は frame 290 で処理され，予定→投入 5.32 ms＋queue 82.20 ms＝87.52 ms．同区間内の mask は 24.34 ms，finish は 44.27 ms，うち GC は 34.96 ms．GUI event pump はこの update の後の frame まで実行されない．nested span と別 thread の壁時計は重複するため加算して因果寄与としない．

全 run で timeout/deadline miss/fallback と scheduler error は 0，worker cleanup は全件成功．after-two の予定 root と実 lock は 8/8 一致．全条件の比較可能な lock に不一致なし．human held mismatch／余分な横 fired は 0，左右回転 emitted/fired は 21/21 と 22/22．処理 ID 重複と内部欠落なし，一部 run には終了時 queue 末尾 1 件が残った．

## 選択 root の再証明

追加 source `ec2d3c0` は，completion で作った選択 root の witness を activation の現在状態で再証明する．request／public／phase の accept と stale 判定を従来の位置に残し，stale または fallback では短縮経路に入らない．選択 action の同一性と現在の authoritative board の配置合法性を確認した上で，元の全 pulse/release を現在の gravity deadline／held/repeat／接地状態を保持した detached simulator で実行し，最初の lock の x/y/rotation が予定 root と一致した場合だけ採用する．時計を初期化せず，幾何到達性だけでは許可しない．失敗時は全 root mask を再計算し，上記の呼出内 witness を使用する．prepare の公開 mask と探索 budget は変更しない．

新規 proof の回帰は，137/148 の旧誤入力，同じ board で gravity deadline が変わった場合，board 占有変更，異なる選択 action，held RIGHT の予定 release 欠落を拒否する．source の非変更も確認する．既存の masked-root 注入テストは proof の拒否も注入して full-mask fallback を通す．実時計／board の拒否は別の固定 fixture で検証する．66＋GUI 49＝115 tests 成功（[final-unit-tests.txt](final-unit-tests.txt)，[final-gui-unit-tests.txt](final-gui-unit-tests.txt)）．

追加 A/B の `--reference-activation` は `b7365ba` の `_activate_nextgen` を復元する．両者とも改善済み planner を使い，選択 root proof の効果を分離する．条件は測定前の [final-conditions.json](final-conditions.json) に保存する．初回の 8 run を置き換えず `final-*` として全 run を追記する．集計の `input_tails` は schedule 50 ms 超の全 event と重複 wall span を保持する．

## 最終 A/B と PUYO-269 への引継ぎ

各条件 1 run，前記と同じ host／DISPLAY／seed／frame 数／overlay ON／native SHA．one と two の before/after で全 runtime source file hash，設定，native SHA が一致した．下表の before は `b7365ba` の activation，after は `ec2d3c0` の選択 root proof である．

| 条件 | frame p95/p99 ms | input schedule p95/p99 ms | event→draw p95/p99 ms |
| --- | --- | --- | --- |
| one before | 24.07/39.70 | 20.66/55.95 | 30.04/39.87 |
| one after | 21.11/33.22 | 20.47/25.81 | 27.27/45.70 |
| two before | 26.74/50.82 | 20.64/46.49 | 35.43/57.08 |
| two after | **25.18/38.52** | 20.74/34.75 | 36.47/50.41 |
| human after | 21.84/38.22 | 21.25/38.98 | 32.28/40.95 |
| light after | 19.72/21.07 | 17.98/20.31 | 21.74/34.19 |
| one minimal | 22.95/35.07 | 20.26/24.51 | 27.64/35.83 |
| light minimal | 19.71/21.07 | 19.95/21.28 | 21.61/29.46 |

two の frame p95 を丸めて 25 ms 通過としない．one/light の full/minimal は機械 gate を維持するが，両側の profiling overhead をこの片側結果から推定しない．event→draw は one/two/human の p95 と two の p99 が閾値を超え，PUYO-269 に残る応答遅延として引き継ぐ．

after-two の root proof は 8 回，p50/p95/max は 0.717/1.073/1.082 ms．全 mask 10 回はすべて prepare 10 回に対応し，activation の全 root 再探索は 0 回だった．prepare mask の p50/p95/max は 8.283/14.051/15.272 ms，prepare 全体は 9.950/15.546/16.683 ms．同 frame の 2 人分 prepare で mask 計 21.56 ms／prepare 計 25.00 ms の frame 1 が残る．frame 291 も mask 15.27 ms／prepare 16.68 ms／frame 25.78 ms だった．

GC は frame 138 の MainThread に約 38.45 ms，frame 298 の背景 reader に約 38.12 ms の gen2 回収が重なった（同 frame の全 GC 計 39.31 ms）．後者の reader decode は 58.57 ms，frame は 94.34 ms で，event 106 の input schedule は 53.81 ms（投入遅れ 6.86＋queue 46.95 ms）．全体の p99 通過を最大遅延の解消とは扱わない．Python reader の GIL 共有と GC，prepare の同期 mask が次の対策対象である．GC 無効化や tick 間引きは行っていない．

最終全 run の timeout/deadline/fallback/scheduler error は 0，worker cleanup は全件成功．nextgen の予定 root と実 lock は one 6/6，two 8/8，human の AI 6/6 が一致．stale 棄却は one 2，two 各 1，human 2．human held mismatch／余分な横 fired は 0，左右回転 emitted/fired はともに 21/21．処理済み ID の内部欠落／重複はなく，一部 run の終了時に queue 末尾 1 件が残る．

**残課題は PUYO-269 の prepare 同期 mask／decode・GIL・GC の改善と，その確定 head の組合せ QA での PUYO-273 gate 再判定である．本 PR では追加の未測定最適化を入れない．変更後の実人間 QA は未実施で，既存の人間操作報告を今回の再 QA に読み替えない．**

## 回帰と再実行

timed placement／reachable mask／planner／realtime／scheduler／survival receipt／reader／replay の 65 tests と GUI の 49 tests が成功（[unit-tests.txt](unit-tests.txt)，[gui-unit-tests.txt](gui-unit-tests.txt)）．初回は native 未導入の root venv で 1 件が環境エラーとなり，既存 desktop venv で再実行した．Ruff は変更ファイルで成功．timed-placement test の既存 F841 は起点同ファイルでも再現し，その 1 件のみ除外した．probe の既存 E402 も従来どおり除外した．`git diff --check` 成功．歴史再現 `--reference-mask`／`--geometric-reference` の dummy 3 frame smoke が成功し，正式性能値には混ぜていない．

```bash
PYTHONPATH=. DISPLAY=:0 SDL_AUDIODRIVER=dummy \
  /home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-273-desktop-activation/probe.py \
  --overlay --reference-plans --output /tmp/before-one.json
# after は --reference-plans を外す．two は --opponent nextgen_tactic_manager --frames 360．
# human は --opponent human，light は --policy first，overhead 比較は --minimal．
python docs/benchmarks/puyo-273-desktop-activation/aggregate.py
```

raw の全分位点を再計算し保存値との完全一致を検証する．source file hash，native hash，process CPU/RSS/thread，IPC，cache hit/miss，stale，lock と cleanup は raw/summary に残す．ユーザーの既存人間 QA は x1.0 の移動/回転と preview 切替が概ね良好だったが，この変更後の人間 QA や元の human 窒息 run の再現とは扱わない．
