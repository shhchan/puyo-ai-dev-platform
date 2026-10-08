# PUYO-274: Sprint 14 最上段の結合 GUI 機械 QA

測定 source は `cd299d7c2e25cf10c1130fb94ba0bc3f134c31ce`（#178→#180→#181→#182 の履歴保持 merge 後）．既存の PUYO-269 固定 16 run のうち，変更後に相当する 8 条件を，PUYO-266 の request.v2／公開推定を含む最上段で新たに逐次実行した．元の 16 run は [PUYO-269 証拠](../puyo-269-mask-allocation/README.md) のまま保持する．

seed 55，速度 x1.0，1120×780，60 FPS，overlay ON，native／nextgen_safe_build．one／human／light は 600 frame，two は 360 frame．`human` は合成入力であり，人間 GUI QA の代用ではない．`minimal` は一部の計測を外した条件で，frame／input の測定は残る．8 run の source file fingerprint，native binary，host，画面条件は一致し，source diff は空だった．[集計と raw SHA](summary.json)，[raw](raw/) を保存した．

| 条件 | frame p95／p99 ms | input p95／p99 ms |
| --- | ---: | ---: |
| one | 22.37／34.05 | 15.76／17.74 |
| two | 22.65／44.86 | 15.83／16.42 |
| human | 21.30／30.22 | 16.21／29.12 |
| light | 18.69／20.25 | 15.43／15.93 |
| repeat-two | 24.04／34.18 | 16.38／34.41 |
| minimal-two | 23.87／31.77 | 15.66／28.32 |
| minimal-one | 22.35／32.24 | 16.09／21.57 |
| minimal-light | 18.13／19.38 | 15.22／16.12 |

固定 gate の p95 ≤ 25 ms／p99 ≤ 50 ms は frame／input とも全 8 run で通過した．確認可能な実 lock の不一致，scheduler error，timeout，fallback は各 run 0，worker cleanup は全件成功．最大 frame p95 は repeat-two の 24.04 ms で余裕は約 0.96 ms に留まり，別 run／環境での安定保証ではない．人間操作の意図位置への配置とカクつきは，この変更後の最上段で別途 QA する．PUYO-266 の seed 127／128 窒息と正式 G2 FAIL は本計測で解消した扱いにしない．

同条件の単独再実行例：

```bash
cd /home/sion2000114/workspaces/dev/puyo-s14-274
PYTHONPATH=. DISPLAY=:0 SDL_AUDIODRIVER=dummy \
  /home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  eval/puyo_269_gui_probe.py --overlay --opponent nextgen_tactic_manager \
  --frames 360 --output /tmp/puyo274-combined-two.json
```

保存した 8 raw の checksum，分位点，source／native／host 一致，lock／worker／gate は次で再集計できる．

```bash
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-274-sprint14-combined-20261008/verify.py
```
