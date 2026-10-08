# PUYO-269: desktop cadence の検証済み wire proof

親 PUYO-228，PR base `PUYO-273/desktop-mask-cadence`，起点 `0a364aa2a6d8b5a5ca0313c5aa6ca3369a21f222`．**両側 frame p95 と変更後の実人間 QA が未達／未実施のため，Jira は In Progress，PR は draft．** 旧 #175 の x1.0 人間操作報告を再 QA として扱わない．

## 変更と直接効果

reader は従来どおり `Diagnostics.from_dict` で schema／型／batch digest を検証する．検証の証明用に作っていた第 2 の完全な dictionary tree と Python 再帰比較を，親 process 内だけの `pickle.dumps(protocol=5)` による exact byte 比較へ置き換えた．pickle を読み込む処理や新しい IPC 契約はない．dict 順序・alias 関係の変更は保守的な再検証になる．nested mutation，bool/int/float，list/tuple，欠損・余分な field，selection 改変は proof を再使用しない．変異 object の `__reduce__` が ValueError/RuntimeError/TypeError を投げても proof を破棄し schema 拒否へ戻す．authoritative request／盤面／phase 照合，実 lock，receipt，ledger は維持する．

同一 payload の 100 回測定（[microbenchmark.json](microbenchmark.json)）では decode 中央値 **11.437→10.162 ms**，UI proof 中央値 **0.915→0.102 ms**．旧／新の canonical diagnostics SHA-256 は一致した．最終 two trace では reader decode p50 が 21.10→17.74 ms，accept p50 が 3.68→2.84 ms．ただし reader max は GC と重なり 59.75 ms，gen2 GC 最大は before 49.97 ms／after 44.96 ms．中間 run では逆転もあり，GC 改善や GUI 全体の安定改善は主張しない．

## 固定条件と最終結果

既存 desktop venv を read-only 使用．DISPLAY=:0，WSLg，seed 55，1120×780，60 FPS，speed 1.0，overlay on，nextgen_safe_build/native，one/human/light 600 frame，two 360 frame．native binary/source files/config/host は各 A/B 間で一致を assertion した．before は同じ source 内の `--reference-wire` で起点 scheduler のみ復元する．source HEAD は起点で，未コミット実装の全 Python source hash と diff hash を raw に保存．最終実装との source hash 一致も確認した．

固定 gate は frame interval と **入力の予定時刻→event 処理（input_schedule_ms）** の p95 ≤ 25 ms／p99 ≤ 50 ms．post→処理，状態反映，描画完了は別に保存し，schedule gate の通過を end-to-end gate の通過へ読み替えない．分位点の raw 再計算と A/B 同一条件は `aggregate.py` が検証する．

| 条件 | frame before p95/p99 ms | frame after p95/p99 ms | input before p95/p99 ms | input after p95/p99 ms |
| --- | ---: | ---: | ---: | ---: |
| one | 23.24/36.56 | 24.61/32.76 | 17.80/22.08 | 17.84/23.71 |
| two | 26.21/42.89 | 25.37/38.71 | 19.86/34.73 | 19.16/42.63 |
| human | 21.40/35.15 | 22.24/34.99 | 19.80/23.71 | 20.95/27.32 |
| light | 19.77/21.65 | 19.68/20.86 | 20.39/21.11 | 20.27/20.97 |

**最終 two の frame p95 25.37 ms は未達．** repeat は frame 27.42/36.05 ms／input 19.48/32.48 ms，minimal は frame 26.69/35.28 ms／input 20.67/29.56 ms で，同じ p95 未達を確認した．one/human/light と全 run の input は gate を通過した．minimal one/light の frame は 22.25/30.09／19.77/21.35 ms．追加の pass 選別再試行や閾値変更は行わない．

two の elapsed は通常 6.47 s／repeat 6.49 s／minimal 6.47 s．minimal は function span・process sample・GC callback を外すが frame/input/lock 計測は残る．完全な無計測 overhead とは解釈しない．

#180 の final two 25.18 ms 未達は別 run/source であり，ここから直接変更効果を算出しない．初回 exploratory before/after two 26.92→24.54 ms と，例外処理追加前の 12 run（intermediate/，全 gate 通過）も別に保持．**先行の pass より最終 source の未達結果を優先する．**

## p95 近傍と tail の原因

`summary.json` の `frames_over_25ms` に該当する全 frame の update/render/catch-up と関数 span を保存した．背景 reader span の frame は完了時のラベルで，壁時計区間は重なり得るため足し合わせない．入力 tail は timestamp による区間重複も保存した．

| after-two frame | frame ms | update ms | render ms | 内容 |
| --- | ---: | ---: | ---: | --- |
| 181 | 25.37 | 13.23 | 2.53 | mask 8.40／prepare 9.82 ms，catch-up 1 |
| 215 | 25.28 | 21.01 | 3.88 | accept 2.85／finish 4.48 ms，reader 22.10 ms，catch-up 2 |
| 83 | 26.95 | 13.67 | 3.42 | mask 7.91／prepare 9.66 ms，catch-up 1 |
| 116 | 52.82 | 11.77 | 3.27 | 背景 reader 57.85 ms と gen2 33.84 ms，catch-up 3 |
| 303 | 70.97 | 50.07 | 3.10 | gen2 44.96 ms，catch-up 1 |

最終 two の prepare p50/p95/max は 9.82/17.54/24.03 ms，mask は 8.41/15.90/22.72 ms．mask のない frame でも decode/GIL・finish・GC と 2–3 tick の catch-up が重なる．accept の約 0.8 ms 短縮だけでは同期 mask と待ち／描画の合計に 25 ms の余裕を作れていない．最小 profile の未達も，追加計測器だけが原因ではないことを示す．個々の span から未計測の CPU scheduling 原因までは断定しない．

after-two の post→handled p95/p99 は 13.44/17.02 ms，post→state は 22.85/44.39 ms，post→draw は 31.35/56.42 ms．human の post→state は 29.90/36.73 ms，post→draw は 33.36/40.32 ms．simulation 境界・描画待ちと最大遅延が残る．

## 正確性と process

全最終 run の timeout/deadline/fallback/scheduler error は 0，worker cleanup は成功．after の AI 予定 root と実 lock は one 6/6，two 7/7，human 6/6 が一致（比較対象の不一致はすべて 0）．stale は raw/summary に記録し棄却されている．human held mismatch と余分な横 fired は 0，左右回転 emitted/fired は {'ROTATE_LEFT': [21, 21], 'ROTATE_RIGHT': [21, 21]}．処理済み event ID の内部欠落／重複は 0．一部 run 終端に未処理 queue 末尾 1 件を明示している．

最終 after-two の親／worker RSS 最大は 540.6/612.5/612.3 MiB，sampled CPU は 0.357/0.834/0.813 cores，thread 最大は各 35．before は 541.2/612.2/612.1 MiB，0.360/0.824/0.798 cores．CPU は process sample 差分を run 全体時間で割った参考値で，host 全体の飽和判定ではない．全条件・IPC・cache hit/miss は raw/summary 参照．

## 採用しなかった mask 案

同一 mask 呼出の同じ入力 prefix だけに authoritative control probe を共有する案を試した．全 root の到達可否／入力列／first-lock と元 simulator 不変は empty/seed137/seed148 で一致したが，状態 clone と保持が割高だった．

| 案 | empty before→after ms | seed137 | seed148 |
| --- | ---: | ---: | ---: |
| 全 tick prefix | 7.80→12.39 | 3.15→4.34 | 8.67→11.42 |
| idle lock tail を除外 | 7.61→9.50 | 3.13→3.16 | 8.61→9.16 |

各 30 回交互測定は `rejected-*-microbenchmark.json`，再現 patch は `rejected-*.patch`．**action_planner.py は起点へ戻し，製品には採用していない．** 別 checkout に対象 patch を適用し，`PYTHONPATH=. <python> docs/benchmarks/puyo-269-cadence-finish/planner_microbenchmark.py` で再現できる．失敗した最適化を正式 GUI 値へ混ぜない．現時点で追加の狭い変更の安全性と性能効果を裏付ける証拠はない．

## 回帰と再実行

[unit-tests.txt](unit-tests.txt) は 123 tests 成功（schema mutation／reader cancellation・cleanup／timed mask／gravity・held・repeat／receipt／stale／pause・step／独立進行／replay hash／GUI）．その後の例外処理追加を含む最終 [final-wire-tests.txt](final-wire-tests.txt) は 3 tests 成功．Ruff と `git diff --check` も成功．

```bash
PYTHONPATH=. /home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-269-cadence-finish/run.py
python docs/benchmarks/puyo-269-cadence-finish/aggregate.py
```

`run.py` は最終 12 run を再実行する．synthetic human と実人間操作は別であり，最終 stack branch で 1P nextgen／2P human，x1.0 の意図した横移動・回転・配置を再 QA する必要がある．同期 mask，decode/GIL，GC の tail と両側 frame gate を未解決として残す．
