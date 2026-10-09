# PUYO-274: 有限代替生存証明後の結合 GUI QA

測定 source は `32280fda0a4f980b9bbdf1d07b3cbf09bad7fbee`．PUYO-266 の代替 prefix 証明と診断器の逐次 JSON 保存を #178→#180→#181→#182 に積んだ最上段で，seed 55，速度 x1.0，1120×780，60 FPS，overlay ON，native／nextgen_safe_build の 8 条件を逐次測定した．`human` は合成入力であり実人間 QA の代用ではない．`minimal` でも frame／input の測定は残る．全 run の source file fingerprint，native binary，host，画面条件は一致し，source diff は空である．[初回集計](summary.json)，[初回 raw](raw/)，[同 source 再測定](../puyo-274-sprint14-bounded-repeat-20261009/README.md)を保存した．

| 条件 | 初回 frame p95／p99 ms | 初回 input p95／p99 ms | 再測 frame p95／p99 ms |
| --- | ---: | ---: | ---: |
| one | 20.55／32.30 | 16.31／23.23 | 21.89／31.71 |
| two | 20.80／38.89 | 16.35／20.75 | 22.66／36.64 |
| human | 20.84／30.47 | 17.40／18.72 | 21.29／35.68 |
| light | 18.62／19.47 | 16.20／17.38 | 18.45／20.29 |
| repeat-two | 23.15／35.57 | 16.00／23.54 | 23.25／40.84 |
| minimal-two | **25.59**／34.25 | 16.91／21.56 | 24.45／38.59 |
| minimal-one | 22.64／31.79 | 18.09／27.00 | 21.62／31.28 |
| minimal-light | 18.35／20.99 | 16.14／17.61 | 18.59／19.79 |

固定 gate は frame／input p95 ≤ 25 ms，p99 ≤ 50 ms．初回は minimal-two frame p95 `25.59096305 ms` のため **7／8 通過**，再測は **8／8 通過**．両組とも実 lock 不一致，scheduler error，timeout，fallback は各 run 0，worker cleanup は全件成功．同日，同一画面／native／host で minimal-two だけを追加測定すると新 source は 23.30 ms，前版 `3724eb9` は 25.96 ms だった．[比較 raw と SHA](comparison.json)を保存した．この幅から今回の失敗を新生存修正の性能回帰と断定できず，gate の安定達成も宣言しない．frame／input の機械結果は人間操作の受け入れ条件を代用しない．

初回の失敗を期待値として保持し，raw checksum，分位点，source／native／host，lock／worker を再検証する．再測の PASS は別ディレクトリの verifier で確認する．

```bash
cd /home/sion2000114/workspaces/dev/puyo-s14-274
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-274-sprint14-combined-20261008/verify.py \
  docs/benchmarks/puyo-274-sprint14-bounded-20261009 --allow-failed-gate
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-274-sprint14-combined-20261008/verify.py \
  docs/benchmarks/puyo-274-sprint14-bounded-repeat-20261009
```
