# PUYO-269：入力順序と残余 GUI 同期経路

**状態：未完了．frame/input gate は未達．実人間の配置操作 QA は未実施．**

PUYO-273 の確定 head を通常 merge した合成 source の追加測定は [integrated/README.md](integrated/README.md) に保存した．以下の初回 A/B とは source が異なる．

## 条件と再現

Intel Core Ultra 7 258V，WSL2 Linux，CPython 3.12.3，pygame 2.6.1，DISPLAY `:0`，1120×780，seed 55，60 FPS 上限，速度 1.0，overlay off，最大 2400 tick．軽量 `first/random`，片側 `nextgen_tactic_manager/random`，両側 `nextgen_tactic_manager/nextgen_tactic_manager`，1P nextgen／2P human を別 process で直列測定した．軽量・片側・human は各 600 frame，両側は 360 frame．全 run の全 frame で match が活動中だった．human は 50 ms ごとの合成 key edge で，実人間の操作ではない．

固定 wheel の `_puyo_deep_chain_native.so` SHA-256 は `79b7d6f43305a27d169722ca014cfba09cd0e97932d49284896de63231bdb0d2`．旧 binary による fallback trace は含めない．全 run の `source_sha` は `7587d0bdfa166ec48135b1a6001951d54d7a0029`，`puyo_env` の未 commit 差分 hash は空値の SHA-256 `e3b0c442…`．計測 wrapper `eval/puyo_269_gui_probe.py` の SHA-256 は `1f425eea2d47d2e57f471a83027a22fe778cf8096380fc35f16baa7884e3eecd`，共通 probe `eval/puyo_273_gui_probe.py` は `8dc9a647bb9884fcccfbd3da1a60700cc8ff0109afa4700fe52f08be6d660990`．`--reference-269` は起点 `f53252bbd4f0c526a6a4ab3ea497eae96fce1e02` の `NextgenScheduler.accept/finish` を `git show` で読み込み，human の `TickInput.edges` を落として従来の release→press 処理を再現する．同じ source，config，wheel，host，frame 予算でこの切替だけを変えた．旧 source 全体を checkout した結果とは扱わない．旧 scheduler source の SHA-256 は `b4b1868e7184deb5f5480c97317174ec5dba4586195cc76f18bc0a0166050da2`．

```bash
DISPLAY=:0 /tmp/puyo271-final-venv/bin/python -m eval.puyo_269_gui_probe \
  --reference-269 --output /tmp/puyo269-before-one.json
DISPLAY=:0 /tmp/puyo271-final-venv/bin/python -m eval.puyo_269_gui_probe \
  --output /tmp/puyo269-after-one.json
# 両側：--opponent nextgen_tactic_manager --frames 360
# human：--opponent human，軽量：--policy first
# 計測器の比較：変更後へ --minimal を追加
python docs/benchmarks/puyo-269-followup/aggregate.py
```

`raw/` に 10 run の gzip 個票を保存した．`summary.json` は展開後 SHA-256，native/source/reference 条件，frame/input/tick の p50/p95/p99/max，関数区間，event ID，worker 資源を含む．`aggregate.py` で再集計できる．PID や wall clock は run ごとに異なる．

## frame/input と入力正確性

事前 gate は frame と input schedule の p95 ≤ 25 ms／p99 ≤ 50 ms．input schedule は予定投入時刻→event 処理．次表は p95/p99，単位 ms．

| 条件 | frame 旧→新 | input schedule 旧→新 | event 投入→状態 旧→新 | event 投入→描画 旧→新 |
| --- | --- | --- | --- | --- |
| 軽量 | 19.5/22.6 → 19.6/25.0 | 15.9/19.4 → 15.5/16.8 | 16.9/30.3 → 17.7/27.7 | 19.8/32.4 → 22.5/34.0 |
| 片側 | 26.4/65.6 → 29.5/64.8 | 40.2/61.7 → 34.4/55.7 | 41.1/85.6 → 27.0/56.0 | 62.7/133.5 → 52.4/87.6 |
| 両側 | 66.6/126.4 → 76.3/136.1 | 79.5/130.6 → 95.3/168.0 | 86.8/129.3 → 107.7/172.0 | 154.9/207.9 → 215.6/333.5 |
| 1P nextgen／2P human | 30.7/66.1 → 31.8/69.1 | 39.7/59.7 → 37.5/77.8 | 39.0/97.4 → 37.0/77.8 | 67.9/122.8 → 65.7/105.2 |

human trace の UI/simulator held 不一致は 63→0 tick．UI 解放済み，かつ同 tick に新規 press がない余分な横 fired は LEFT 7→0，RIGHT 6→0．回転 emitted/fired は旧 LEFT 22/22・RIGHT 22/22，新 LEFT 22/22・RIGHT 23/23 で一致する．event ID の重複は 0，各 run の未処理 1 件は終了直前に post された queue 残で，処理済み ID に内部欠落はない．同 tick press→release／release→press と 12 tick catch-up，押しっぱなし下＋横／回転は unit fixture でも確認した．合成列の結果は意図した配置ができることを証明しない．

## 同期費用と残余原因

`scheduler.finish` は選択候補の再検証と diagnostics の二重 JSON 化を避け，`accept` は executor 結果の二重 deepcopy を避けた．公開 request/batch validation，receipt，ledger，replay，stale/cancel は維持する．`finish` p50 は片側 9.8→6.9 ms，両側 11.5→6.2 ms．`accept` p50 は片側 20.3→17.5 ms，両側 21.8→19.9 ms．各 run の decision は 10–12 件程度で p95 は外れ値に敏感であり，片側 `accept` p95 は 41.6→62.2 ms と逆転した．**frame 全体の性能改善は確認できない．**

両側の update p95 は 45.2→59.8 ms，render p95 は 25.4→24.5 ms，catch-up p95 は 4→5 tick（最大 10→12 tick）．worker 2 件の sample CPU は各 0.87–0.92 core，親は 0.66 core，最大 RSS は親 266–268 MiB，worker 各 339–340 MiB．片側は親 0.60–0.63 core，worker 1.05–1.08 core．worker cleanup は全 run 成功，scheduler error/fallback/deadline miss は 0．stale は片側 4/4，両側 1+1/0+0，human 5/5 で，run の進行差として分けて扱う．CPU sample は 60 frame 間隔で process の生涯平均ではない．

計測器 full/minimal の変更後 1 run 比較では，軽量の elapsed 9.8/9.8 s・frame p95 19.6/19.3 ms，片側 11.13/11.14 s・29.5/32.9 ms．区間 wrapper と `/proc` sampling による差は run 間ばらつきから識別できない．minimal にも frame/event/tick の基本時計は残る．PUYO-273 の別 run は片側 38.0/76.2 ms，両側 98.7/156.8 ms だったが，本 A/B の旧側は 26.4/65.6 ms，66.6/126.4 ms と異なる．別日の raw を同条件ペアとして扱わない．

残余は worker 受領時の diagnostics 型復元・digest validation，GUI update の catch-up 集中，両側 render と worker の CPU 競合にまたがる．A/B で gate 未達なので PUYO-269 は In Progress，PR は draft を維持する．実人間が意図した配置へ操作できるかの再 QA も必要である．
