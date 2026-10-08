# PUYO-266: seed 127 の全消し攻撃と生存 witness

**品質 FAIL/G2 BLOCKED を維持する．元の人間対局の再現ではない．**

報告条件は 1P nextgen，2P human，速度 x1.0，match seed 127，1P policy seed 58，daa，selection softmax，temperature 1.0 である．softmax/1.0 は既定 argmax/0.2 と異なる．元 replay と正確な human 入力列，元 source/config/native SHA は未取得であり，新しい固定入力 fixture と区別する．

## 新しい再現機能と入力

診断 CLI に selection mode/temperature/speed と `--human-inputs` を追加した．normal は指定速度の最大 tick rate で進め，step は worker 完了待ちの別条件と明記する．fixture の schema/seed/重複 tick/不正 edge を検証し，原文と SHA-256 を report に保存する．replay は実際に投入した両 player の入力を保持する．

`human-inputs.json` は seed 127 の公開初期ツモ `(1,1),(1,1),(3,2)` から作った合成入力である．`make_fixture.py` の greedy は入力作成時だけ使い，測定中の 2P には固定済み tick edges を投入する．生成器の simulator は nextgen policy へ渡さない．全消しは 2 手目，ボーナス消費は 12 手目となる．

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1 \
.venv/bin/python -m eval.nextgen_realtime_diagnostic \
  --mode normal --seed 127 --seed-a 58 --templates daa \
  --selection-mode softmax --temperature 1.0 --speed 1.0 --opponent human \
  --human-inputs docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/human-inputs.json \
  --placements 60 --max-ticks 3500 --profile nextgen_safe_build \
  --output /tmp/puyo266-human127-new
```

## 変更前の新規測定

1565 tick，32.794 s，23 採用，5 stale，fallback 0．1P が窒息した．全 tick hash/攻撃/全消しと最終 hash の再生一致を確認し，23 実 lock の root 不一致は 0．source の測定中変更なし．`before/report.json.gz` に source 各 file SHA/config，ledger に候補固定順位/selection/receipt，audit に実 lock/clear を保存した．`before/replay.json.gz` は反復 UI/controller diagnostics を除いた入力 replay で，除外 key/元 SHA/サイズは `originals.json` に記録した．元 `/tmp/puyo266-human127-normal` は保持している．

| 公開/実 event | tick | 結果 |
| --- | ---: | --- |
| 2P 2 手目の全消し | 143 | bonus pending，送信 0 |
| 2P 次回消去で bonus 消費 | 690 | 2100 score bonus，生成/送信 32 |
| 1P おじゃま落下 | 724 / 806 | 30 + 2 |
| 1P 最後の判断 | 1532 activation | 即時消去なし，後に窒息 |

最後の判断 28 に公開/実盤面いずれも即時合法消去はない．これを「最後の小消しを無視」と表現しない．一方，判断 26 では公開/実盤面とも root 7/8/10 の到達可能な 4 連鎖があり，即時解決では非 fatal だった．選択は root 3 の非発火 `survival_safe_nonfire` だった．

判断 26 の witness `[3,0,11]` は，root 3 後の列 1 が満杯となり，次 root 0 に公開/実盤面とも幾何到達できない．判断 27 は missing hidden 4 セルが原因で，公開投影だけが次 root 0 を可到達とした．offline 完全盤面を同じ有限 probe に渡しても，26 は別の `[3,11,15]` を返す．この 3 手目 15 も，直前の高さ `[12,14,11,13,12,12]` では fresh-spawn geometry が返す `[7,9]` に含まれない．hidden の欠落だけを直しても，後続操作の不足は残る．完全盤面は offline 診断だけで使用した．

## 採用した狭い修正と固定回帰

完全に埋まった 14 段列を横断する後続配置だけを probe から除外した．root mask と既存 quota は維持する．正常 hidden 継続の一律抑制はない．別 sidecar として，公開 current pair と実 lock action の履歴も追加した．[契約と残設計](../../../development/puyo-266-public-placement-history.md)を参照．

`compare_probes.py` は旧保存物の公開入力を固定して，従来 probe と修正 probe を比較する．383 判断で survival 128/response 256 を超えない．これは対局全体の新しい品質測定ではない．

| identity | 判断数 | 保存された採用 root の証拠の変化 |
| --- | ---: | --- |
| GTR 55 / 123 / 124 | 各 40 | なし．123/28 の `[1,3,8]` を維持 |
| GTR 126 | 36 | 35 判断目 `[3,0,11]` → `[3,12,11]`，witness のまま |
| GTR 128 | 39 | 37 判断目 `[3,0,11]` → `[3,8,0]`，witness のまま |
| GTR 132 / 135 / 144 | 33 / 37 / 38 | なし |
| daa / persian 55 | 各 40 | なし |

127/26 は `[3,0,11]` → `[3,11,15]` となり，非発火 root 3 の witness が残る．この修正だけで必要な小消しを選ぶことは立証していない．

## 採用しなかった control 探索

全後続 root に公開 geometry BFS を追加し，control-state 展開を placement/drop と同じ 128 枠へ課金した試作は，正常 GTR 123/28 で cutoff を起こした．14 テスト中 5 件が失敗したため，runtime へは採用していない．patch/JSON/失敗ログを保存し，正常ケースの期待値は変更していない．

| 入力 | placement / control | 結果 |
| --- | --- | --- |
| 127/26 | 25 / 103 | root 3 と消去 7/8/10 が cutoff，別 root 11 は witness |
| 123/28 | 23 / 105 | 保護対象 root 1 も cutoff，正当な継続を失う |

## 再検証

```bash
python -m unittest tests.test_nextgen_public_placement_history \
  tests.test_nextgen_public_snapshot tests.test_nextgen_survival \
  tests.test_nextgen_survival_walls tests.test_nextgen_realtime_diagnostic_inputs \
  tests.test_nextgen_adoption_replay tests.test_nextgen_safe_build
PYTHONPATH=. python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/compare_probes.py
python -m eval.nextgen_realtime_audit \
  --report docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/before/report.json.gz \
  --replay docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/before/replay.json.gz \
  --output /tmp/puyo266-human-audit.json
```

正式 G2，修正済みの品質宣言，元 human run の同定，人間 GUI QA は未完了である．

現在の関連単体・receipt・safe-build 回帰は 43 件成功，Ruff と diff check も成功した．既存 response fixture 8 件は candidate gap 0 だった．その専用 quota は 2000/4000 であり，通常 profile の response 256 や正式 G2 の全条件の代用にしない．
