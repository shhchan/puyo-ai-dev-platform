# Sprint 15 単独 gate の native smoke

測定 source は `651449a`．preregistered pattern0 の先頭3配置を両 policy で実行した．正式120runの一部として数えず，品質は DIAGNOSTIC_ONLY．両policyとも最大実連鎖0で，40手checkpointは未到達である．

| Policy | 判断平均／p95 | 測定・保存・再生合計 | gzip raw | 実 lock／全 tick hash |
| --- | --- | --- | --- | --- |
| nextgen_tactic_manager | 0.268810／0.277629 s | 2.109692 s | 107640 byte | 3配置一致／全一致 |
| deep_chain_builder | 0.291152／0.305954 s | 2.962540 s | 141577 byte | 3配置一致／全一致 |

両policyの integrity 問題は予定した `incomplete_window` のみ．scheduler error，fallback，stale，quota超過，rootと実lockの不一致，replay hash不一致はない．source／native build／host／設定／provider identity は manifest に固定し，前後一致を確認した．reference は公開履歴の推定を入力とした．

最大5520resolutionへの線形外挿は4666.454秒（約78分），raw gzip229279640byte（約229MB）．序盤だけの小標本なので終盤の探索・分類・長いreplayの費用は保証しない．fresh processの起動と再開用progress保存も含め，正式枠は90～120分を見込む．46resolved placements／30000tick/runの上限，閾値，cohortをこの見積のために変更しない．

専用tests15件と，既存safe-build／public snapshot／public inference／inference wire／旧gateの38件が成功した（計53件，重複計上なし）．Ruff と diff check が成功．正式品質と PUYO-266 COMPLETE，次世代全体G2 PASS，本学習開始は未達のまま．

再実行には測定sourceのclean checkoutとmanifest記録のnative環境を使い，親のCPU排他枠を取得する．repository rootから実行し，新しい出力先を指定する．

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1 \
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python \
  -m eval.nextgen_single_quality_gate smoke \
  --source /tmp/puyo275-haipuyo.txt --output /tmp/puyo266-single-smoke-recheck
```

原本 `haipuyo.txt` は含めない．公開者推定sourceのchecksumと色正規化は [PUYO-275の説明](../../../development/puyo-275-tsumo-source.md)を参照する．`checksums.json` は保存したmanifest／report／raw／logのSHA-256．正式測定前の性能確認なので，このREADMEの保存commitと測定sourceの違いを区別する．
