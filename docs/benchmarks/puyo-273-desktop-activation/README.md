# PUYO-273 desktop activation の局所 witness 再利用

2026-10-08 の Sprint 14 証跡．source `b7365ba` の初回 A/B は，両側の input schedule p99 が 55.71 ms のため未達．Jira は In Progress のままとする．旧失敗を再試行で置き換えない．

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
