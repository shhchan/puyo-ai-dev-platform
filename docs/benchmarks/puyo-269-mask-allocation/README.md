# PUYO-269: immutable planner inputs と idle control

起点 `4056baa70de9b1d0ae897e2fcb1d07ae69db3441` は親が stack の先行変更を取り込んだ #181 head．以前の wire proof 計測とは source が異なるため，過去の frame 値との差を追加 planner の効果へ読み替えない．gate は frame/input schedule の p95 ≤ 25 ms／p99 ≤ 50 ms のまま固定する．

## 狭い変更と fixed-state 検証

`TickInput` は frozen dataclass で各 field は tuple．そのため，5 種類の静的 press/release pulse と idle input を planner 内で再利用する．正確に `RealtimeHeadlessSimulator` で，その idle input かつ held が空のときだけ，入力 collection を省略して空の fired を使う．この条件では入力 edge も押しっぱなしもないため，repeat deadline の更新も発火もない．`game.update`，ground-lock，gravity，tick の更新は毎回実行する．subclass，release，held が残る状態は従来の collection を通る．quota・探索順・repair・mask の真偽判定は変えない．

`experiment.py` は各 allocation 案を baseline source から個別生成し，100 回の実行順を回転する．clone 単独は実効差が小さいため採用しなかった．前回不採用の prefix 共有は再試行していない．

| 最終 production と baseline の 100 回交互 micro | before ms | after ms |
| --- | ---: | ---: |
| empty | 7.157 | 5.597 |
| seed 137 | 2.896 | 2.160 |
| seed 148 | 7.956 | 6.463 |

最終 source は `verify.json` に SHA-256 を保存．empty/137/148 × held 3 条件 × gravity 3 条件 × budget 3 条件の 81 条件で，全 22 root の入力列・到達可否・実 first-lock と source 不変が一致．budget を計測後に縮小していない．別の回帰で expired repeat，held LEFT/RIGHT/DOWN，release，gravity 境界を実 simulator と tick ごとに比較した．

## 固定 GUI 計画

`run.py` と `conditions.json` を実行前に固定した．seed 55，speed 1.0，1120×780，60 FPS，overlay on，nextgen_safe_build/native，one/human/light は 600 frame，two は 360 frame．既存 desktop venv を read-only で使用．

- `before-wire`：`--reference-wire --reference-allocation`．旧 wire proof と旧 planner．
- `before`：`--reference-allocation`．現 wire proof と旧 planner．
- `after`：現 wire proof と新 planner．
- after の two repeat，two/one/light minimal を追加し，計 16 run．

全段階を同じ source で実行し，旧 scheduler／planner の指定部分だけを評価用 switch で戻す．`aggregate.py` は source fingerprint/native/host/settings/frame 数の段階間一致と，raw からの分位点再計算を検証する．minimal は function span/process sample/GC callback を外すが，frame/input/lock の軽量計測は残る．

入力 gate は scheduled→handled であり，posted→handled→state→draw は別指標である．synthetic human は実人間 QA ではない．

## 最終 GUI 結果

全 16 run の固定 frame/input gate が通過した．旧段階も通過しており，frame 全体の安定改善や過去の別 source での未達解消をこの一組だけで証明したとは扱わない．

| 条件 | 旧 wire＋旧 planner frame p95/p99 ms | 現 wire＋旧 planner frame | 新 planner frame | 新 planner input schedule |
| --- | ---: | ---: | ---: | ---: |
| one | 23.43/35.73 | 19.65/30.19 | 19.90/30.02 | 15.30/16.29 |
| two | 21.07/46.85 | 23.94/33.89 | 23.13/29.98 | 17.12/19.44 |
| human | 20.18/30.27 | 19.49/30.01 | 19.46/34.50 | 15.66/20.89 |
| light | 18.11/19.32 | 18.23/19.76 | 17.91/19.17 | 15.04/16.36 |

新 planner の two repeat は frame 24.35/35.22 ms，input 16.05/17.80 ms．two minimal は frame 22.25/41.63 ms，input 16.55/31.14 ms．one/light minimal の frame は 19.70/28.11／17.95/19.17 ms，入力も通過した．two repeat の p95 余裕は **0.65 ms** に留まるため，恒常的な性能保証はしない．

two の mask p50 は 7.725→6.098 ms，prepare p50 は 9.028→7.533 ms．fixed-state の直接比較でも削減を確認できたため採用する．一方，実 GUI mask p95 は 8.932→9.673 ms で，非同期の進行により request の盤面・回数が異なる（10→11 回）．この run を同一盤面の探索品質比較に読み替えない．

two の elapsed は通常 5.948 s／repeat 5.950 s／minimal 5.986 s．minimal でも gate が通過したが，完全に計測器を除去した実行ではない．function，IPC，GC，cache hit/miss，cold/warm，25 ms 超の全 frame と input tail を summary/raw に保存した．GC・reader/GIL の最大遅延を解消したという主張はしない．

after-two の post→handled は 13.30/16.68 ms，post→state は 22.71/32.01 ms，post→draw は 28.05/37.53 ms．human の post→state は 19.90/32.90 ms，post→draw は 22.95/36.43 ms．update/render/catch-up とともに schedule gate とは別に報告する．

全 16 run の timeout/deadline/fallback/scheduler error は 0，worker cleanup は全件成功．予定 root と比較可能な実 lock の不一致はすべて 0．after の nextgen lock は one 6/6，two 8/8，human 6/6 が一致．human held mismatch／余分な横 fired は 0，左右回転 emitted/fired は 19/19，20/20．stale は one 2，two 1/2，human 2 で棄却された．内部 event ID 欠落・重複は 0，一部 run の終端 queue 末尾 1 件の未処理を別に保存している．

after-two の親／worker RSS 最大は 540.7/613.3/611.8 MiB，sampled CPU は 0.387/0.898/0.856 cores，thread 最大は各 35．before の RSS は 540.8/611.7/612.3 MiB，CPU は 0.385/0.899/0.881 cores．process sample 差分を run 全体時間で割った参考値であり，host 全体の飽和判定ではない．

**自動計測の固定 gate は通過したが，変更後の実人間 QA は未実施．PR draft／Jira In Progress を維持する．** source fingerprint/native/config/host の段階間一致，production source hash と raw の一致，checksum を検証した．


## 検証と再現

`unit-tests.txt` は 125 tests 成功．clock/held/release/first-lock，mask/repair，controller/reader，receipt/stale，pause/step，GUI/replay を含む．Ruff と PR 起点からの diff check を実施する．

```bash
PYTHONPATH=. /home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-269-mask-allocation/verify.py
PYTHONPATH=. /home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-269-mask-allocation/run.py
python docs/benchmarks/puyo-269-mask-allocation/aggregate.py
```

最終 stack branch での 1P nextgen／2P human，x1.0 の実人間 QA は親が案内する．それまでは draft／Jira In Progress を維持する．
