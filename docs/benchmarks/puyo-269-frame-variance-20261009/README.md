# PUYO-269: frame gate の run 間変動と同期負荷

2026-10-09 の保存 raw 18 本を読み取り，追加の固定 two 条件を 1 run だけ計測した．**既存 minimal-two の frame p95 25.59096305 ms は失敗のまま保持する．安定 PASS も新修正固有の回帰も結論にしない．** 製品コードは変更していない．PR #181 は draft，PUYO-269/273 は In Progress，人間 GUI QA は依頼者の結果待ちである．

## 保存 raw の比較

参照は #182 の `docs/benchmarks/puyo-274-sprint14-bounded-20261009/` と `...bounded-repeat-20261009/`．ファイルごとの SHA-256，source，分位点，相関，25 ms 超の frame 番号を [summary.json](summary.json) に保存した．前版以外の source は `32280fda0a4f980b9bbdf1d07b3cbf09bad7fbee`，前版は `3724eb9`．同一 source の 2 組の 8 条件は初回 7/8，再測 8/8 だった．

| minimal-two | frame p95 ms | update p95 ms | render p95 ms | frame/update Pearson r | 25 ms 超/360 frame |
| --- | ---: | ---: | ---: | ---: | ---: |
| 初回 | 25.591 | 11.548 | 4.906 | 0.815 | 19 |
| 同 source 8 条件再測内 | 24.445 | 11.600 | 4.582 | 0.748 | 16 |
| 同 source 単独追加 | 23.297 | 9.631 | 5.110 | 0.821 | 14 |
| 前版単独比較 | 25.955 | 14.653 | 4.690 | 0.817 | 20 |

初回の 25 ms 超 frame では平均 update/render/待ち残差が 19.335/5.212/8.287 ms．同期 update の寄与が大きい．frame は draw 終了間隔なので，clock.tick の待ちと前後 frame の処理量にも左右される．相関は原因そのものの証明ではない．minimal の function/GC/process 個票は空であり，初回の特定 frame を GC や activation と断定できない．

instrumented two/repeat-two 4 本では，両者の prepare が同時に走ると約 15 ms，completion/activation が同時に走ると約 26 ms の同期処理になっていた．gen2 GC は 32–78 ms．例として初回 two の frame 175 は frame/update/GC が 51.60/38.92/35.01 ms，再測 two の frame 187 は 94.72/84.83/77.57 ms だった．cache hit の worker 時間は約 86–88 ms，miss は約 378–451 ms だが，hit の採用 frame も 26.8–49.0 ms になる．worker の短縮だけで UI 採用時の費用は消えない．

各 run の最初～frame 300 の process CPU 増分は，UI 約 0.49–0.51 core，worker 各約 1.02–1.11 core 相当．平均値からマシン全体の飽和とは言えない．host 全体・thread 別の短時間 CPU/run queue は保存されておらず，瞬間的な競合も否定できない．再測 minimal-two の frame 309 は待ち残差 35.245 ms，update 8.807 ms，render 2.869 ms．待ち側の外れ値はあるが，OS/WSL，GIL，その他の実行待ちを既存 raw だけで分離できない．

## 追加の wall/CPU 区間計測

[専用 probe](../../../eval/puyo_269_frame_diagnostic.py) は既存 minimal probe に，wall `perf_counter_ns` と当該 thread の CPU `thread_time_ns`，clock.tick，update/tick/render，prepare/accept/finish/activation，finish 内 Diagnostics.to_dict，scheduler/controller の deepcopy，GC を付加する．計測は seed 55，両側 nextgen_safe_build/native，360 frame，speed 1.0，overlay ON，1120×780，60 FPS，DISPLAY=:0，WSLg．元の gate p95 ≤ 25 ms／p99 ≤ 50 ms は変更しない．

source HEAD は `a29e31f470a81d31bb2b7cc53b3bd0b056358a33`．最上段 `32280fd` と `agents/puyo_env/eval/src` の既存 Python 全ファイルの fingerprint は一致し，native/config/host も参照 raw と一致した．追加 probe 自体は raw 内の source hash と区間 raw 内の SHA-256 で固定する．この run には wrapper と timer の allocation が増えており，**minimal gate 判定の代用にはしない**．

| 区間（入れ子を含む） | 回数 | wall 合計 ms | thread CPU 合計 ms |
| --- | ---: | ---: | ---: |
| update | 360 | 1852.392 | 1794.698 |
| prepare | 10 | 82.996 | 80.568 |
| accept | 10 | 33.409 | 26.926 |
| finish | 10 | 49.887 | 43.488 |
| finish 内 Diagnostics.to_dict | 10 | 14.997 | 14.667 |
| activation | 10 | 84.598 | 77.436 |
| activation 直下の deepcopy | 18 | 27.240 | 26.642 |
| tick 直下の deepcopy | 2158 | 887.763 | 863.454 |
| reader decode | 10 | 151.486 | 107.095 |
| clock.tick | 360 | 2931.171 | 10.728 |

区間は入れ子なので加算しない．deepcopy は 2 module の参照だけ差し替え，再帰コピー全体に timer を挿入しない．activation の deepcopy には配置証明用と診断 detach 用が含まれ，すべてを除去可能な診断コピーと扱わない．tick の deepcopy も既存の安全な snapshot 境界であり，表からコピー削除を正当化しない．

- frame 126/339 の gen2 GC は wall 32.459/32.549 ms，CPU 31.730/31.832 ms，collected/uncollectable は 0．約 32 ms の実 CPU 走査が frame 48.381/49.256 ms に重なった．GC 起動時点は計測 allocation でも変わるため，初回失敗 frame と同一視しない．
- frame 303 の update は wall/CPU 30.008/29.086 ms．両者の completion/activation が同じ tick で走っており，同期製品処理の費用を確認できる．
- frame 28 は accept の wall/CPU 8.094/2.864 ms，finish は 9.977/4.468 ms．reader decode 区間と重なる約 5 ms の実行待ちがあり，GIL 競合と整合するが，OS スケジューリング等との厳密な切り分けは未実施．wall−thread CPU を OS/WSL 遅延と決めつけない．
- 診断 run の frame p95/p99 は 21.818/42.167 ms，input schedule は 16.550/30.329 ms．実 lock 8 件の不一致 0，scheduler error/timeout/fallback 0，worker 2 件 cleanup 成功．stale retry は両者各 1 件．失敗 raw を取り消す結果にはしない．

## 改善判断と停止条件

finish 内 to_dict は中央値 1.481 ms/採用．検証済み wire の再利用で縮小し得るが，canonical な型の正規化，受理後 mutation，controller/record/ledger の detach 契約を保持する必要がある．コピー全廃で費用が消えるとも仮定しない．1.5 ms/採用だけでは prepare，両者同時採用，GC，reader 待ちを含む 25 ms 超を安定解消できる根拠がないため，このセッションでは製品修正を停止した．GC 無効化・閾値変更，tick 間引き，gate 緩和は行わない．

次は代表的な受理 payload の固定 fixture で，canonical wire 構築と診断コピーを個別に profile し，mutation/型/receipt/ledger 契約を守る案に限って CPU/allocation の改善量を測る．契約維持を証明できない，または改善量が同期ピークの残余を説明できない場合は製品へ採用しない．製品案が得られた場合だけ親へ設計と回帰範囲を提示し，排他枠で同一 source/native/host/config の minimal-two と既存固定 8 条件を再測定する．現状の人間 QA と安定 gate 未達は PUYO-269/273/274 の残課題として保持する．

## 再計算と実行手順

[圧縮 GUI raw](diagnostic-raw.json.gz)，[圧縮区間 raw](diagnostic-intervals.json.gz)，[集計](summary.json)，[再計算器](analyze.py) を保存した．再計算器は GUI を起動せず，checksum，区間件数，分位点，source/native/host/config，lock/cleanup を検査する．参照 root を省略すれば本ディレクトリの診断証跡のみ検査できる．

```bash
python3 -m unittest tests.test_puyo_269_frame_diagnostic -v
python3 docs/benchmarks/puyo-269-frame-variance-20261009/analyze.py
python3 docs/benchmarks/puyo-269-frame-variance-20261009/analyze.py \
  --reference-root /home/sion2000114/workspaces/dev/puyo-s14-274/docs/benchmarks
```

再計測は別途 heavy 枠を確保し，新しい出力先を用意して行う．保存 raw を上書きしない．

```bash
DISPLAY=:0 SDL_AUDIODRIVER=dummy PYTHONPATH=. \
  /home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  -m eval.puyo_269_frame_diagnostic \
  --output /tmp/puyo269-new-diagnostic-raw.json \
  --diagnostic-output /tmp/puyo269-new-diagnostic-intervals.json
```

検証：時計分離・cross-frame・入れ子例外復旧・GC 記録の 3 tests，py_compile，保存 raw 再計算，`git diff --check`．製品コード無変更のため既存 125 tests は再実行していない．Ruff は既存 desktop venv と PATH に未導入で未実施，共有環境への install は行っていない．
