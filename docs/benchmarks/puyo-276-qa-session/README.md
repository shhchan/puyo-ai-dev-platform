# PUYO-276 長い対局の保存・入力計測

2026-10-10，clean source `92d100adf7c89d13b88d9e3a39d008f62b02bf1e`，native SHA-256 `35735a1a6bce0a45a64a46453cd4302f34099fb626ef4fc226a61c32bb2c52ca`，CPython 3.12／既存 read-only native venv，SDL dummy video driver で測定した．config は random 配ぷよ，seed 127，1P `nextgen_tactic_manager`，2P `human`，速度 1.0，1000 tick 上限である．50 ms 間隔の同一キー列（W を保持し，A/E/D/Q を順に押下・解放）を合成投入した．これは人間の実画面操作 QA ではない．

`probe.py` は既存 `eval/puyo_273_gui_probe.py` と同じ予定投入 `expected_ns`→event 取得 `handled_ns` を `input_schedule_ms` として記録し，実投入→event 取得，実投入→最初の simulation boundary，実投入→描画完了も保存する．最初の boundary は意図した移動成立を保証しない．frame と input schedule の既存 gate は p95 ≤ 25 ms，p99 ≤ 50 ms．各 mode は別 process で直列実行し，worker 停止を確認した．

| 指標 | 保存 OFF | 保存 ON |
| --- | ---: | ---: |
| frame / tick | 987 / 1000 | 1008 / 1000 |
| 対局経過 | 16.641 秒 | 16.945 秒 |
| frame p95 / p99（runtime `clock.tick`） | 21 / 45 ms | 18 / 30 ms |
| input schedule p95 / p99（325 event） | 18.94 / 40.77 ms | 16.42 / 57.82 ms |
| input→state p95 / p99 | 22.11 / 46.58 ms | 23.97 / 60.60 ms |
| input→draw p95 / p99 | 28.11 / 59.42 ms | 26.96 / 78.65 ms |
| 対局中 sampled parent RSS 最大 | 96,344 KiB | 119,384 KiB |
| 対局中 sampled worker RSS 最大 | 171,824 KiB | 169,736 KiB |
| process `ru_maxrss` | 567,564 KiB | 567,564 KiB |
| replay / result / manifest | なし | 14,845,041 / 624,592 / 3,804 bytes |
| 保存処理 | なし | 0.753 秒 |

ON の input schedule p99 は固定 50 ms gate 未達．frame の観測値だけをもって総合 gate PASS としない．両 mode は seed と scripted 入力を揃えたが，worker 処理と frame 数は一致せず，各 1 run のため差を保存 ON/OFF の因果効果と断定しない．`ru_maxrss` は起動から終了までの process peak，60 frame ごとの RSS は対局中の sampled 値で，保存時瞬間 peak の内訳は分離していない．

ON の session ID は `502fbfb5fa194aabb6c92d95ad6b87a7`．manifest は source dirty `false`，対戦 seed 127，policy seed 127/10127，速度 1.0，1000 tick，最終 hash `5854b7f10a7105db78880b22a35f0efa3f56c1d4b4c794ebc2e89f27458a5e39`，config SHA-256 `b66960e00f8055dd1257ceda1832753ab396a4a1eec3bd050e5b91078ca4a499` を記録する．[OFF raw](raw/off.json)，[ON raw](raw/on.json)，[session bundle](raw/on-session.tar.gz)，[validator 出力](raw/validation.txt)を保存した．bundle 内の replay/result/manifest の hash は manifest 自身で確認できる．

次のコマンドで保存済み bundle を別 path に展開し，入力・各 tick hash と最終 hash を validator で再検証できる．終了コード 0 と `QA session valid:` を期待する．

```bash
mkdir -p /tmp/puyo-276-review
tar -xzf docs/benchmarks/puyo-276-qa-session/raw/on-session.tar.gz -C /tmp/puyo-276-review
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.qa_session /tmp/puyo-276-review/502fbfb5fa194aabb6c92d95ad6b87a7
```

測定コマンドは source `92d100a` の `probe.py` をリポジトリ root から実行した．同時に複数 process を起動しない．出力先を変えると config digest は変わる．

```bash
PYTHONPATH=. SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy /home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python docs/benchmarks/puyo-276-qa-session/probe.py --mode off --output /tmp/puyo-276-qa-long/off --frames 1100
PYTHONPATH=. SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy /home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python docs/benchmarks/puyo-276-qa-session/probe.py --mode on --output /tmp/puyo-276-qa-long/on --frames 1100
```

## input p99 未達の調査

旧 ON raw の input schedule tail には event ID 8／159／167 の 78.02／86.27／104.76 ms があり，同じ ID の OFF は 43.20／33.96／44.65 ms だった．保存 replay には tick 49／477／502 付近で 0.63 MB 前後の完全な nextgen decision が含まれる．raw に旧 serialization 呼出時刻はないため，この近接だけでは遅延の直接原因とは確定できない．

`RealtimeControllerDiagnostics.to_dict()` の旧処理は `asdict(self)` で decision を深くコピーした後，`last_decision.to_json()` で再度深くコピーしていた．保存 replay の 629,555 byte の実 decision を復元し，旧相当関数と新関数を交互に 30 回ずつ呼ぶ軽い測定では，旧中央値／p95 が 13.17／39.34 ms，新関数が 6.52／6.77 ms だった．値の同一性は確認したが，この microprofile は GUI gate の代替ではない．[個票](raw/decision-copy-profile.json)を保存した．新しい長時間 probe は実対局中の `to_dict()` 呼出時刻・時間を測り，入力 event の tail と照合する．

## 単一コピー後の 2 repeat 再計測

修正後の clean source は `0995e2374be405a1ae894c8f2c9054a1aad69c3a`，native SHA-256 は前回と同じ `35735a1a6bce0a45a64a46453cd4302f34099fb626ef4fc226a61c32bb2c52ca`．事前に OFF/ON 各 2 repeat と既存 p95 ≤ 25 ms／p99 ≤ 50 ms gate を固定した．1 worker・1 feeder thread とし，同じ random seed 127，1P nextgen／2P human，速度 1.0，50 ms の同一キー列，1000 tick 上限で順に実行した．出力 root と非同期 worker の進行は repeat ごとに異なるため，config digest・最終 hash・frame／event 数は同一とは限らない．

| 指標 | OFF 1 | ON 1 | OFF 2 | ON 2 |
| --- | ---: | ---: | ---: | ---: |
| frame / tick | 1038 / 1000 | 1006 / 1000 | 1022 / 1000 | 1028 / 1000 |
| frame p95 / p99 ms | 19 / 27 | 19 / 32 | 18 / 25 | 17 / 31 |
| input schedule p95 / p99 ms | 15.91 / 34.51 | 16.60 / 48.46 | 16.56 / 25.97 | 15.67 / 25.51 |
| input→draw p95 / p99 ms | 23.62 / 65.70 | 30.57 / 56.54 | 32.41 / 46.33 | 31.93 / 46.44 |
| 対局中 sampled parent RSS 最大 KiB | 93,492 | 119,648 | 93,532 | 111,784 |
| 対局中 sampled worker RSS 最大 KiB | 170,876 | 171,396 | 171,536 | 167,460 |
| process `ru_maxrss` KiB | 626,244 | 626,244 | 626,244 | 626,244 |
| replay bytes | なし | 15,266,876 | なし | 12,210,531 |
| 保存処理秒 | なし | 0.712 | なし | 0.627 |
| decision `to_dict()` p95 ms | 1.33（n=1） | 9.13（n=18） | 1.44（n=1） | 5.82（n=17） |

4 run の frame と input schedule は今回の固定 gate の範囲内だった．ただし旧 ON 1 run の input p99 57.82 ms 未達は上記 raw のとおり保持する．今回の各 mode 2 repeat だけで広い環境の SLA や ON/OFF 因果差の合格を宣言しない．ON 1 の input schedule 最大 76.88 ms と input→draw p99 56.54 ms も残り，p99 gate 内の観測と個々の遅い操作を区別する．

ON 1 の遅い event ID 8／151 は decision `to_dict()` の 15.18／8.07 ms 呼出区間と重なる．ON 2 の最大 event ID 8 も 14.74 ms の呼出区間と重なる．一方で ON 1 の event ID 323 は 59.13 ms 遅延のうち `to_dict()` が 1.38 ms にすぎず，他の処理や scheduling も影響する．単一コピー化が input tail 全体の唯一の原因だったとは断定しない．

[OFF 1](raw/after-off-1.json)，[ON 1](raw/after-on-1.json)，[OFF 2](raw/after-off-2.json)，[ON 2](raw/after-on-2.json) に時刻・RSS・serializer 呼出個票を保存した．ON の[bundle 1](raw/after-on-1-session.tar.gz)と[bundle 2](raw/after-on-2-session.tar.gz)はそれぞれ session ID `8a2de3a7f3624d04bb33bd0a8904d1ca` と `54264fd71f8747288a0a304325f60049` を含む．両 manifest は source dirty `false`，policy seed 127/10127，1000 tick，native hash を記録する．[別 path 展開後の validator 出力](raw/after-validation.txt)で両方の再検証に成功した．

```bash
mkdir -p /tmp/puyo-276-after-review
tar -xzf docs/benchmarks/puyo-276-qa-session/raw/after-on-1-session.tar.gz -C /tmp/puyo-276-after-review
tar -xzf docs/benchmarks/puyo-276-qa-session/raw/after-on-2-session.tar.gz -C /tmp/puyo-276-after-review
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.qa_session /tmp/puyo-276-after-review/8a2de3a7f3624d04bb33bd0a8904d1ca
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.qa_session /tmp/puyo-276-after-review/54264fd71f8747288a0a304325f60049
```
