# PUYO-274: PUYO-266 発火順位修正後の結合 GUI 機械 QA

測定 source `3724eb9af7ad23fd730d0a2ed8e6c6668fc3367f` は，PUYO-266 の適格な `fire_main` 候補の順位修正を #178→#180→#181→#182 へ履歴保持で反映した最上段である．seed 55，速度 x1.0，1120×780，60 FPS，overlay ON，native／nextgen_safe_build で 8 条件を逐次測定した．`human` は合成入力であり実人間 QA の代用ではない．`minimal` でも frame／input の測定は残る．全 run の source file fingerprint，native binary，host，画面条件は一致し，測定時の source diff は空である．[集計と raw SHA](summary.json)，[圧縮 raw](raw/) を保存した．

| 条件 | frame p95／p99 ms | input p95／p99 ms |
| --- | ---: | ---: |
| one | 22.57／31.38 | 15.75／19.26 |
| two | 23.51／33.10 | 16.18／18.62 |
| human | 20.44／30.57 | 15.35／16.09 |
| light | 18.34／20.16 | 15.92／16.22 |
| repeat-two | 22.95／44.08 | 16.61／26.54 |
| minimal-two | 23.12／33.14 | 16.54／24.42 |
| minimal-one | 22.37／30.61 | 15.49／16.85 |
| minimal-light | 18.23／19.25 | 15.06／16.00 |

固定 gate の frame／input p95 ≤ 25 ms，p99 ≤ 50 ms は全件通過した．実 lock 不一致，scheduler error，timeout，fallback は各 run 0，worker cleanup は全件成功．最大 frame p95 は両側の 23.51 ms で余裕は約 1.49 ms．別 run／環境の安定保証ではない．PUYO-266 の正式 G2 と変更後の実人間 GUI QA は別の受け入れ条件である．

```bash
cd /home/sion2000114/workspaces/dev/puyo-s14-274
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-274-sprint14-combined-20261008/verify.py \
  docs/benchmarks/puyo-274-sprint14-eligible-fire-20261008
```
