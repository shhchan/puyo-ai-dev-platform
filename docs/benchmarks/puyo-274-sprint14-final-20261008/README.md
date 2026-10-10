# PUYO-274: Sprint 14 最終 stack の GUI 機械 QA

source `5180bc7e3225915c6e254b798a0330bb2b892381` は PUYO-266 の着弾済みおじゃま回復を #178→#180→#181→#182 へ履歴保持で反映した時点の最上段である．seed 55，速度 x1.0，1120×780，60 FPS，overlay ON，native／nextgen_safe_build の GUI probe を 8 条件で逐次実行した．`human` は合成入力であり，実人間 QA の代用ではない．`minimal` でも frame／input の測定は残る．全 run の source file fingerprint，native binary，host，画面条件は一致し，計測時の source diff は空である．[集計と raw SHA](summary.json)，[圧縮 raw](raw/) を保存した．

| 条件 | frame p95／p99 ms | input p95／p99 ms |
| --- | ---: | ---: |
| one | 21.97／30.26 | 16.04／25.70 |
| two | 23.63／35.38 | 16.44／27.81 |
| human | 20.62／30.05 | 15.71／18.45 |
| light | 17.90／19.20 | 15.13／15.91 |
| repeat-two | 24.41／35.48 | 15.66／16.66 |
| minimal-two | 23.40／31.15 | 15.42／17.72 |
| minimal-one | 20.37／30.55 | 15.22／16.88 |
| minimal-light | 18.45／19.17 | 15.42／16.12 |

固定 gate の frame／input p95 ≤ 25 ms，p99 ≤ 50 ms を全件通過した．実 lock 不一致，scheduler error，timeout，fallback は各 run 0，worker cleanup は全件成功．最大 frame p95 は repeat-two の 24.41 ms で余裕は約 0.59 ms に留まる．別の run／環境での安定を保証しない．PUYO-266 の正式 G2 と変更後の実人間 GUI QA は別の受け入れ条件である．

保存 raw の checksum，分位点，source／native／host 一致，lock／worker／gate は既存の verifier を新しい保存先へ指定して再集計できる．

```bash
cd /home/sion2000114/workspaces/dev/puyo-s14-274
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-274-sprint14-combined-20261008/verify.py \
  docs/benchmarks/puyo-274-sprint14-final-20261008
```
