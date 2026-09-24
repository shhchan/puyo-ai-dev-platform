# PUYO-255 評価証跡

正式 G2 は **BLOCKED**，今回の smoke 能力は **FAIL**，historical only240 の能力も **FAIL** である．評価実装の完了・PR 作成は能力合格や学習開始の承認を意味しない．親セッションで PUYO-253 の GUI 接続後に 184 tests，Fn 不要の履歴キー追加後に 185 tests と通常ウィンドウの自動 smoke を確認し，統合時点の G0/G1 を **PASS** とした．この追補は [integration-qa.json](integration-qa.json) に記録した．依頼者による通常 GUI の確認では GTR 品質不良と Fn 依存キーの操作不能が報告され，キー修正後の人間再確認を含む受け入れ条件は未達である．

## 測定と分母

2026-09-24，Intel Core Ultra 7 258V，専用 venv，1 worker，OMP/OpenBLAS/MKL 各 1 thread．CPU 測定中は他の子の重いテストを停止した．nextgen_smoke は Python，main/template/response quota = 256/128/256，depth 4/width 4/scenarios 1．source/build/config の SHA と package version は各 manifest に固定している．

| 対象 | run/固有 seed | 最大実連鎖平均 | premature | game over | 判定 |
| --- | ---: | ---: | ---: | ---: | --- |
| historical only240 reference/native | 60/30 | 9.7 | 2/2,398 placements | 2/60 runs | 品質 FAIL |
| 新 runtime safe-build smoke | 2/1 | 2.0 | 28/80 placements | 0/2 runs | 観測条件の品質 FAIL |
| 正式 reference G2 | 2/60 診断 run，58 未実施 | reference 未校正 | 合格根拠なし | 合格根拠なし | BLOCKED |

新 runtime は seed 123 の repeat 1/2 を各 40 手完走し，semantic digest が一致した．wall 94.228/95.688 秒，合計 CPU 189.890 秒，peak RSS 73,848 KiB．60 identity 全体と欠落 58 件を report に残した．smoke で明確な能力反例が出たため，全 60 run(約 94 分の外挿)を追加しない判断を親セッションと記録した．旧 only240 の 60 raw は別 source/build/config の historical 入力であり，新 runtime の欠落を補完しない．

## 対戦・時間・学習費用

rule と search-only selector を seed 123 で side swap した．全て 600 tick 上限で打ち切られ，終局勝率の標本にはならない．表の policy latency は decision trace の実測 wall time．scheduler/engine/serialization を含む全体 wall time は各 report に別記する．

| 実行 | runs/観測 policy decisions | policy p50/p95 秒 | activation p50/p95 ticks | CPU 秒 | peak RSS KiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| 同期 configured | 2/34 | 2.045/2.186 | 0/0 | 79.411 | 72,328 |
| 同期 measured | 2/34 | 2.094/2.221 | 0/0 | 80.704 | 73,636 |
| 非同期 measured，実時間 tick | 2/8 | 2.207/2.368 | 117/119.7(activated 2 件のみ) | 22.221 | 53,148 |

同期 controller は計算中に match tick が進まないため，measured 指定だけでは実時間 latency の測定にならない．元 raw を保持し，別 manifest で共有 single-worker executor と実時間 tick clock を追加した．非同期では 12 requests 中 activated 2/stale 6，残り 4 は上限時点で pending．timeout は 0 だが，上限が未設定なので timeout 耐性の合格根拠ではない．stale は受信済みでも actor 学習対象から除外する．新対戦 profile の latency 合格閾値は未承認である．

| 外挿元 | rule 自 decision/秒 | 配線 2,000 件 | PPO 20,000 × 3 件 | long-run 1,000,000 × 5 件 |
| --- | ---: | ---: | ---: | ---: |
| 同期 configured の activated 16 件 | 0.2015 | 2.76 h | 82.73 h | 6,893.87 h |
| 非同期 measured の学習可能 activated 1 件 | 0.0450 | 12.33 h | 370.02 h | 30,835.18 h |

非同期の集計は両 side 合計で rule 1 件・相手 1 件の学習可能 activation にすぎず，極めて不安定な予備外挿である．stale を含む receipt 4 件/22.201 秒 = 0.1802 件/秒も report に保持するが，これを学習可能 sample 速度へ読み替えない．optimizer/gradient/checkpoint は未計測．profile・hardware・worker 数を変えた性能予測や長時間学習の実行許可ではない．

## fixture と gate

- 必須 response coverage: 8/8，候補 gap 0．template fixture: 6/6(positive/negative を含む)．
- 独立した公開 1-ply 相殺 reference: candidate gap 0/1，候補順位失敗 0/1，戦術選択失敗 0/1．全 root を実 engine で列挙した sidecar と，実 runtime の六戦術 batch を別ファイルに保持する．全体の known-solution corpus を事前登録した証拠ではないため，正式 G2 の known-solution 条件は保留する．
- 測定時点の report では G0: native parity 再実行待ち，G1: GUI/ledger 組合せ QA 待ちで BLOCKED．統合後の追補では native を含む 185 tests が全件 PASS，120 tick の通常ウィンドウ自動 smoke で ledger/replay/viewer の同一 7 件，request-1 の activation tick 46・戦術・requested/executed action の一致を確認し，G0/G1 は PASS．Fn 不要の `J`／`K`／`L` 履歴操作と viewer の `J`／`K`／`U`／`I` も通常ウィンドウで自動確認した．人間 QA を G1 の自動検証と混同しない．G2 は能力反例・reference 未校正・58 run 欠落などで BLOCKED．G3/G4 は前段未達で BLOCKED．
- 依頼者設定に近い seed 55，GTR のみ，argmax，rule，`nextgen_smoke` の安全局面再現では，1 手目は `build_template`，2 手目の候補が `unknown` となって `build_main` に切り替わり，8 手目まで戻らなかった．元の GUI run は replay/ledger 未保存であり，相手も再現時の無脅威設定とは異なる．これは実対戦の厳密な再生ではないが，報告された挙動を説明する独立した証拠である．
- 110 tests 実行，109 件成功，native 1 件 skip．追加の最終 gate/非同期 clock 回帰 10 tests 成功．ruff 成功．広い suite の後の変更は gate 集計・計測 harness とそのテストだけで，ゲーム・探索・scheduler 本体は変更していない．

## 証跡と検証

[report.json](report.json) と [async-report.json](async-report.json) が機械可読な判定・分母・測定の正本．[validation.json](validation.json) は全 artifact の SHA，測定 source と分析 source，archive 内の全ファイル SHA を保持する．

[evidence.tar.gz](evidence.tar.gz) は `sync/` と `async/` の manifest，8 個の新しい gzip raw，全公開 ledger/候補/replay，fixture，sidecar，historical 60 identity/元 raw SHA，QA log を含む．旧 only240 の 16 MB の raw は複製せず，元 path/PR #132 と SHA を historical report に記録する．raw に非公開 seed metadata を含めても，policy の公開 `Diagnostics` に混ぜない．

```bash
mkdir -p /tmp/puyo-255-evidence
tar -xzf docs/benchmarks/puyo-255-gates/evidence.tar.gz -C /tmp/puyo-255-evidence
.venv/bin/python -m eval.nextgen_gate_benchmark --output /tmp/puyo-255-evidence/sync finalize
.venv/bin/python -m eval.nextgen_gate_benchmark --output /tmp/puyo-255-evidence/async finalize
```

再集計では分析 source の metadata だけが現在の checkout を指す．元の manifest と raw は変更しない．公開参照/fixture と historical 集計は [collect_evidence.py](collect_evidence.py) で再生成できる．旧 raw の場所を変える場合は `--historical-dir`，出力先は `--output` を指定する．新規測定は [実行手順](../../development/puyo-255-evaluation-gates.md) を参照する．非同期実時間測定には `init --realtime-clock` を使い，別の出力 directory と事前 manifest を作成する．
