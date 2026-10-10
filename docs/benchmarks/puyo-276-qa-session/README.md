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
