# PUYO-274: 同 source の結合 GUI 再測定

最上段 source `32280fda0a4f980b9bbdf1d07b3cbf09bad7fbee` の初回 8 条件で minimal-two frame p95 が 25 ms gate を超えたため，CPU 重計測を並走させずに同じ 8 条件を逐次再測定した．[初回の条件・失敗・比較](../puyo-274-sprint14-bounded-20261009/README.md)に対する補足である．[集計と raw SHA](summary.json)，[圧縮 raw](raw/) を保存した．全 8 条件で frame／input p95 ≤ 25 ms，p99 ≤ 50 ms を通過し，最大 frame p95 は minimal-two の 24.45 ms．source file fingerprint，native binary，host，画面条件は一致，source diff は空，実 lock 不一致・scheduler error・timeout・fallback は 0，worker cleanup は全件成功．初回の 25.59 ms を取り消す結果ではなく，run 間の変動を示す．

```bash
cd /home/sion2000114/workspaces/dev/puyo-s14-274
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-274-sprint14-combined-20261008/verify.py \
  docs/benchmarks/puyo-274-sprint14-bounded-repeat-20261009
```
