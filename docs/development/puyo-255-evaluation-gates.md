# PUYO-255: 次世代モデルの評価ゲート

`eval.nextgen_gates` は Jira の完了状態と独立した G0〜G4 の判定器である．G2 の未達は `BLOCKED` として保存し，採用済み only240 や接続 smoke を能力合格へ読み替えない．

## 固定条件と再実行

`eval.nextgen_gate_benchmark init` は seed 123〜152，repeat 1/2，40 placements，品質閾値，profile，source/build/config の SHA を測定前に凍結する．同じ manifest/run の上書きは拒否する．各 safe run を新しい process から実行する．

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  .venv/bin/python -m eval.nextgen_gate_benchmark --output runs/new-gate-run init
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  .venv/bin/python -m eval.nextgen_gate_benchmark --output runs/new-gate-run safe --seed 123 --repeat 1
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  .venv/bin/python -m eval.nextgen_gate_benchmark --output runs/new-gate-run paired
.venv/bin/python -m eval.nextgen_gate_benchmark --output runs/new-gate-run finalize
```

`safe` は公開情報だけを受ける rule policy と既存 realtime scheduler を通す．脅威なしの単独構築を再現するため，相手を countdown の空盤面に固定し，攻撃 packet を抑止する専用評価環境である．ターン制の historical only240 と同条件の時間比較ではない．全 identity を宣言するが，未実行・tick 上限・重複・repeat digest 不一致を明示し，完走した一部だけで 60 run 合格を出さない．品質の平均は repeat 1 の固有 seed を分母とし，premature/game over は全 repeat を数える．

現在の nextgen profile は Python smoke(main 256/template 128/response 256，depth 4/width 4/scenarios 1)である．reference 配分と新対戦 timing は未校正であり，60 run が全て成功しても reference G2 合格へ昇格しない．既存 safe-build の p95 <= 1 秒は別の性能判定に保持する．

## 診断と情報境界

候補不足，候補順位失敗，戦術選択失敗は異なる機会数を分母にする．`diagnose_selection` は全候補入り `Diagnostics` と，独立に確認した公開参照 root action の sidecar を使う．

- 候補不足: 参照戦術に適切な root がない．
- 候補順位失敗: 適切な root が同戦術内にあるが，固定順位の `best_id` が外れる．
- 戦術選択失敗: 適切な `best_id` を持つ戦術があるが，selector が別戦術を選ぶ．

これらは単一 decision の参照解に対する診断であり，閉ループ勝率に対する因果 regret ではない．古い raw に未選択候補がなければ順位失敗を unknown にする．hidden future を使う oracle は `hidden_future_oracle` と明示した別ファイルだけに保存し，公開 reference と集計しない．runtime に oracle/private field を混入すると既存の厳格な `Diagnostics` schema が拒否する．

## 接続，対戦，費用

G1 は rule→ledger→GUI の一致を要求する．headless fixture だけでは `gui_ledger` を true にできない．G0/G1 の必須証跡が欠ければ `BLOCKED`，明示的な反例があれば `FAIL` とする．G2 は G0/G1 に加え，60 run，平均最大実連鎖 >= 10，premature/game over 0，repeat 一致，必須脅威 fixture，公開既知解 gap 0 を要求する．G3/G4 は前段 gate が未達なら `BLOCKED` とし，後続の boolean 証跡だけで迂回させない．

対戦 preflight は同じ batch/profile の rule と search-only selector(fire_main/build_main の固定順位)を side swap する．template 候補生成自体は両者で有効であり，template off ablation ではない．bootstrap/RL checkpoint，reward ablation，template off は未実施として行列へ保存する．configured/measured を別集計し，同期 controller の measured 指定では tick が進まない制約を明示する．`init --realtime-clock` は別 manifest で single-worker 非同期推論中も実時間 tick を進める．tick 上限終了を正当な終局・勝利へ読み替えない．1 paired seed は G4 の 100 paired seeds/相手層と 5 training seeds を満たさない．G4 の profile 別 latency/timeout 閾値は人間による事前確定が必要である．

実測には policy step 別 wall time，p50/p95，process CPU 秒，peak RSS，self/opponent decision 数，rollout 全体の wall time を含める．学習費用は実測 self decisions/秒から配線 2,000，短期 PPO 20,000 × 3，long-run 1,000,000 × 5 を外挿する．optimizer・gradient・checkpoint 費用を測っていないため，実際の学習時間は保証しない．worker 数に比例する高速化や，p95 を平均とする計算は行わない．

測定の機械可読な結果と SHA は [評価証跡](../benchmarks/puyo-255-gates/README.md) を参照する．

非同期の stale receipt は actor sample に含めない．receipt throughput と activated sample throughput を別々に保存する．実時間の activation delay は activated receipt だけで測り，stale を含む completion delay と混同しない．
