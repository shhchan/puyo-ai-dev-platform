# PUYO-269：通常 GUI の cadence 計測

PR #161 の live 履歴 payload 軽量化について，同一 host で採取した計測器出力を保存する．元出力 11 本は [`raw/`](raw/) に lossless gzip で収録し，[`manifest.json`](manifest.json) に圧縮前後の SHA-256 と byte 数を記録した．[`summary.json`](summary.json) は元出力の主要欄を集めたものである．`ledger` などの詳細は圧縮元出力に残す．

## 固定条件と測定方法

測定前の宣言は [`conditions.json`](conditions.json) に保存した．Intel Core Ultra 7 258V，CPython 3.12.3，pygame 2.6.1，同じ DISPLAY `:0`（`xdpyinfo` 接続確認），1120×780，seed 55，60 FPS 上限，速度 1.0，overlay off，最大 2400 tick，replay 保存なし．`first` 対 `random`，`deep_chain_builder` reference/native 対 `random`，`nextgen_safe_build`/native 対 `random` は各 600 frame，両側 nextgen は各 360 frame を，ほかの重い評価を止めて直列実行した．各 run は新規 process で開始した．両側のみ計測前宣言の 600 frame から 360 frame に短縮したため，その gate を事前に再宣言したものとして扱わない．

[`probe.py.txt`](probe.py.txt) は計測器の最終版で，cache 専用 2 run に使った．主要 8 run と `mask-after` の診断 1 run はこの直前版を使い，違いは `_complete_decision` から `cache_samples` を追加採取する処理の有無だけである．変更前は baseline SHA `c0c77d944ff3ed276a4b44a40a779cd72bc0977a` の `_build_replay_tick` を `--legacy` で読み込み，変更後は PR head `0be88b9c6d9afe1ddda8bb4df8aa350bb2433f4b` の実装を呼んだ．計測時の `HEAD` は baseline SHA だった．計測器の `--legacy` は現在の `HEAD` を読む実装なので，後からこの branch でそのまま実行すると baseline を指さない．以下の検証は保存済み出力だけを対象とし，重量計測を再実行しない．

frame interval は描画完了間隔，input age は別 thread が 50 ms 間隔で投入した無作用 F12 key の予定時刻から GUI event 処理までの時間である．event，update，render は同じ loop の区間時計，simulation tick は `advance_tick`，worker の CPU/RSS/thread は 60 frame ごとの `/proc` sampling である．CPU 秒は測定 host の `CLK_TCK=100` で，最初と最後の sample の差分であり，起動・終了全体ではない．policy 判断時間は worker が返した経過時間で，cache hit/miss は `search.shared_reuse.hit` に従う．input age は thread の起床・投入の遅れも含み，物理 keyboard の人間 QA ではない．

計測前 gate は frame と input の p95 ≤ 25 ms，p99 ≤ 50 ms（60 Hz の 16.67 ms を基準）．`eval/realtime_gui_qa.py` の既存 gate は意思決定の進行を判定し，frame cadence の閾値を持たないため，この gate は PUYO-269 用に測定前宣言した．軽量 policy の無計測 CLI 600 frame は wall 11.21 s，計測器ありは 11.03 s で，この条件では計測上乗せを識別できなかった．この差は別 process 間のばらつきも含み，負の overhead とは解釈しない．

## 結果

単位は ms．行内は変更前 → 変更後．

| 条件 | frame p50 | frame p95 | frame p99 | frame max | input p95 | input p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| first／random，600 frame | 16.1 → 16.1 | 19.4 → 19.0 | 23.2 → 22.9 | 62.7 → 59.2 | 15.7 → 15.4 | 16.9 → 16.5 |
| deep reference/native／random，600 frame | 16.1 → 16.2 | 20.1 → 20.6 | 62.7 → 60.7 | 161.7 → 110.8 | 26.1 → 31.0 | 75.5 → 61.5 |
| nextgen safe/native／random，600 frame | 17.0 → 16.4 | 184.5 → 58.8 | 301.1 → 121.2 | 431.8 → 208.3 | 243.6 → 76.6 | 337.2 → 113.5 |
| nextgen safe/native 両側，360 frame | 123.3 → 20.5 | 609.5 → 190.4 | 808.6 → 370.3 | 1043.4 → 558.8 | 548.1 → 270.4 | 728.0 → 410.4 |

片側 nextgen の event p95 は 0.10 → 0.09 ms，update p95 は 169.9 → 42.0 ms，render p95 は 11.5 → 10.3 ms．変更前の `_build_replay_tick` は 1328 tick で合計 7.24 s，変更後は 864 tick で 0.26 s．GUI の update が支配的で，描画や探索結果待ちだけでは説明できない．変更前の 12 tick catch-up は frame p95，変更後は p99 で観測された．

片側 nextgen の decision request／activated／stale は 22／9／13 → 14／5／9，timeout／deadline miss／fallback は両方 0．親 GUI process／worker の最大 RSS は 275／351 MiB → 272／351 MiB，最大 thread 数は 12／15 → 12／15，process 数は各 2．サンプリング区間 CPU は親 10.8 s・worker 15.2 s／wall 22.9 s → 親 9.2 s・worker 13.9 s／wall 13.2 s．両側は親と worker 2 件で計 3 process．worker PID は終了後に `/proc` から消えた．

cache だけを記録した別の片側 360 frame run は，変更前 hit 5 件が 0.16–0.19 s，miss 5 件が 0.85–0.90 s，変更後 hit 4 件が 0.17–0.19 s，miss 4 件が 0.83–0.89 s の worker 判断時間だった．cache hit 時も GUI の同期負荷は残る．変更後の別の細分 trace [`mask-after.json.gz`](raw/mask-after.json.gz) では `nextgen_authoritative_action_mask` 20 呼出の p50/p95 が 75.5／81.0 ms で，`NextgenScheduler.prepare` と activation が UI thread 上で呼んでいる．共有 scheduler／到達性探索は [PUYO-273](https://shhchan.atlassian.net/browse/PUYO-273) の担当とし，PUYO-269 を未完了に残す．

**解釈の限界：**固定したのは seed／設定／host／frame 数であり，非同期 policy と可変 frame 時間により同じ公開 snapshot 列を再生した比較ではない．両側変更前は 360 frame の途中で 2400 tick 上限へ到達し，変更後は 1220 tick だった．元出力は stage 別の件数・p50/p95/p99/max と 60 frame 間隔の process sample を保持するが，per-frame 個票を保持しない．そのため percentile の独立再計算や入力 spike と同一 frame の結合はできない．IPC send／deserialize の区間時間も個別には採取していない．replay 保存を有効にした GUI の改善と，人間が実際に操作した GUI QA は未検証である．

## 保存済み証跡の検証

repository root から次を実行する．重量 GUI や native 探索は起動しない．

```bash
python docs/benchmarks/puyo-269-gui-cadence/aggregate.py
```

これは `manifest.json` にある元出力 11 本と条件／計測器の checksum・byte 数を照合し，gzip の展開前後を検査して，主要集計値が `summary.json` と一致することを確認する．`summary.json` を再生成する場合だけ `--write` を指定する．集計の percentile は profiler が元出力に記録した値を照合するもので，個票から再計算するものではない．
