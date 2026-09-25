# PUYO-272 選択定型 backend の比較証跡

2026-09-25 に同一 host で直列測定した．元の PUYO-271 corpus は変更していない．[契約](../../development/puyo-272-selected-template-backend.md)，[共通 quota raw](common-quota.json.gz)，[native production quota raw](native-production-quota.json.gz)，[検証記録](verification.txt) を参照．

## 固定条件

- CPU：Intel(R) Core(TM) Ultra 7 258V．Python 3.12，Rust 1.98 release，native ABI 1．
- 共通設定：target10/d16/w250/scenario6，legacy-fixed-six，decision seed=123，公開 current=(RED,BLUE)，NEXT=(GREEN,YELLOW)，NEXT2=(BLUE,GREEN)．未公開入力は渡さない．
- 主比較は quota512，Python と native oracle-1 の各 1 thread．oracle-1 は 6 scenario を順次実行する mode であり，scenario 数を減らさない．予算を先頭 scenario が消費した場合，後続の未評価分も cutoff として残す．
- 別系列は native scenario-6 の 6 worker，quota600000．主比較の時間と混ぜない．
- 各組は専用 subprocess 内で warmup 1 回，測定 3 回．p50/p95 は並べた 3 値の線形補間（p95=0.1×中間+0.9×最大）．標本が少ないため探索的な数値である．
- RSS は warmup を含む process high-water の Linux KiB．constraint ns は判定本体とタイマー費用の合計．native では scenario ごとの時間を加算した値であり，全体 wall time の内訳には直接足せない．
- 最終計測 source/native provenance は実装 commit `9b64186087ea8b6e1219861ddbb534b39233966c`．raw manifest に Python/Rust 対象ファイル SHA-256，Python AST SHA-256，native binary SHA-256，全 capabilities，公開 state/pairs，測定 script SHA-256 を固定した．最終計測からコードは変更していない．scenario ID 追加前の中間値は `intermediate-without-scenario-id/` に分離し，合算しない．

## 共通 quota の結果

時間の単位は ms，p50/p95 の順．Python/native の全 root 証拠・plan・semantic digest・counters は全 8 比較組で一致した．各組の 3 repeat も決定論を保持した．

| 局面 | 制約 | Python p50/p95 | native p50/p95 | expanded/evaluated | checks |
| --- | --- | ---: | ---: | ---: | ---: |
| flat | なし | 3459.2/3468.9 | 64.9/67.4 | 512/512 | 0 |
| flat | あり | 2627.6/2671.5 | 55.6/60.3 | 512/394 | 512 |
| l | なし | 3628.3/3713.3 | 62.6/64.4 | 512/512 | 0 |
| l | あり | 2521.5/2555.4 | 47.4/53.7 | 512/369 | 512 |
| all_failed | なし | 3562.4/3630.3 | 65.4/66.3 | 512/512 | 0 |
| all_failed | あり | 21.7/22.8 | 4.8/6.0 | 132/0 | 132 |
| unproven | なし | 3571.3/3626.7 | 70.9/72.0 | 512/512 | 0 |
| unproven | あり | 3584.7/3648.2 | 67.2/69.7 | 512/512 | 512 |

初期矛盾による全失敗を除き，共通 quota では探索が打ち切られている．flat は整合 root 19，公開完成 root 3 を保持した．L 字は整合 root 12 を保持し，土台を消す root 7 を含む 10 root を違反として除いた．all_failed は初期必須セルの固定色が既存盤面と矛盾し，全 22 root が violated，整合 root 0 となる．unproven は高い必須セルの公開完成を証明できず，全 22 root を cutoff として保存した．

## native production quota の別系列

| 局面 | 制約なし p50/p95 ms | 制約あり p50/p95 ms | 整合 root | 最大探索連鎖 なし→あり | 制約判定 p50 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| flat | 448.8/504.8 | 447.6/450.4 | 19 | 8→6 | 93.8 |
| l | 431.1/440.6 | 316.3/371.4 | 12 | 7→4 | 94.1 |
| all_failed | 459.6/506.6 | 5.9/6.2 | 0 | 8→0 | 0.0 |
| unproven | 453.3/459.5 | 468.2/495.3 | 22 | 8→8 | 93.1 |

この系列は最大約 46.5 万 expanded nodes，最大 RSS 約 123 MiB．生存する枝がある scenario は depth16 まで進み，全失敗の枝は早期終了した．node quota の打切りはなかった．beam 探索なので未証明を完成不能と解釈しない．flat は公開完成 3 root，sampled だけの完成 10 root，L 字の整合 12 root は全て unknown．unproven は 8 root に sampled 完成例があるが，公開完成はなく全 22 root が unknown のままである．

**拘束すると flat の最大探索連鎖は 8→6，L 字は 7→4 に下がった．** 土台保持の条件を満たすことと連鎖品質の向上は別の条件である．この合成制約・単一 seed の backend 測定は実ゲームの勝率／最大実連鎖，GUI 応答，正式 G2 の PASS を示さない．PUYO-268 の phase に応じた保持範囲・解除判断と，PUYO-271/266 の統合・品質評価を残す．

## 再実行

専用 release native を入れた Python で，repository root から実行する．出力先は新しい path にし，既存証跡を上書きしない．

```bash
python docs/benchmarks/puyo-272-template-search/measure.py --output /tmp/puyo272-common.json
python docs/benchmarks/puyo-272-template-search/measure.py --full-native --output /tmp/puyo272-full.json
```

最終検証は Python 120 tests 成功，Rust 48 passed・2 既存 manual profile ignored．実行コマンドとプロセス識別子は verification.txt に保存した．
