# PUYO-246 定型 catalog の形状確認

`train/config/nextgen_templates.yaml` は出典図から読んだ未発火の土台 core を固定する．図は左端 `x=0`，底面 `y=0` であり，行は上から表示する．`A` などの同一記号は同色を表す．`.` は判定対象外である．表の色は参考図を読むための凡例であり，実際の色 ID は各対局の binding で決まる．どの形も連鎖尾の完成や任意ツモでの構築成功を表さない．

| template / variant | 対象図と読み取り | 色条件 |
| --- | --- | --- |
| `gtr/left_supported_core` | [GTR の図](https://puyo-euphonic.com/puyo-gtr) の左折り返し．右列の持ち上がった緑 `B` の下に，安定盤面に必要な支持色 `C` を追加した．`C` は原図の GTR core そのものではない． | `A≠B`，`B≠C`．`A=C` を許す． |
| `daa/two_interlocking_l` | [だぁ積みの図](https://puyo-euphonic.com/puyo-daa) の左側の交互に噛み合う 2 個の L 字．[KENROU 氏の解説](https://puyo.bondo.link/posts/133892) は L 字 2 個の時点でだぁ積みと扱えると説明する．4 個の L 字を備える土台全体ではない． | `A≠B`． |
| `persian/central_step_flat_tail` | [ペルシャ式の図](https://puyo-euphonic.com/puyo-persian) の右側 3 列が平らな土台と，中央の段差をまたぐ青の core． | `A≠B`，`A≠C`，`B≠C`． |

以下は config から転記したセル図である．GTR は `A=赤`，`B=緑`，`C=赤または緑以外の任意色`，だぁ積みは `A=赤`，`B=緑`，ペルシャ式は `A=赤`，`B=青`，`C=緑` と読む．GTR の固定 fixture では `A=C=1`，`B=2` を実際に照合する．

```text
       GTR                         だぁ積み                    ペルシャ式
       x 0 1 2                    x 0 1 2                   x 0 1 2 3 4 5
  y=2    A B .                                            y=2  . . B B . .
  y=1    A A B                    y=1  A A B              y=1  . B C B . .
  y=0    B B C                    y=0  A B B              y=0  A A A C C C
```

この図の GTR は支え `C` を要求するため，出典図の「背景として空白」に厳密には一致しない．ゲーム上で浮いたセルを認めないための明示的な実装選択であり，人間の形状確認ではこの差も審査する．各 variant は `identity` のみを登録し，左右反転や底上げはこの pack の対象外とする．

## 固定入力と観測結果

fixture は `tests/fixtures/nextgen_template_catalog_cases.json` に底面からの 6 桁行，公開 current/NEXT の 1〜2 組，期待結果を保存する．先頭 2 行の hidden cell は `null` で matcher に渡し，実際のエンジンで毎手の合法配置と resolution を通している．`TemplateSelector` の初回選択を固定し，以後は同じ variant と binding の `match_templates(...).witness_actions` だけを採用する．成功ケースでは全て 14 自 decision 以内，chain count は 0 である．

| 固定ケース | 開始盤面の底面からの行 | 公開組 | witness action | 結果 |
| --- | --- | --- | --- | --- |
| `gtr_left_supported_core` | `221000 / 112000 / 020000` | `(1,2)` | `0` | 1 decision で成立 |
| `daa_two_interlocking_l` | `122000 / 012000` | `(1,1)` | なし | `unknown`，成立せず |
| `persian_central_step_flat_tail` | `111333 / 023200 / 000200` | `(2,2)` | なし | `unknown`，成立せず |
| `gtr_two_decision_rollout` | `221000 / 112000 / 000000` | `(1,2), (2,3)` | `0, 3` | 2 decisions で成立 |
| `daa_two_decision_rollout` | `122000 / 002000` | `(1,3), (1,3)` | `0, 3` | 2 decisions で成立 |
| `persian_two_decision_rollout` | `111333 / 023200 / 000000` | `(3,2), (2,3)` | `4, 11` | 2 decisions で成立 |

fixture を結果に合わせて無言で取り替えないため，最初の入力を commit `4673fde` に固定してから matcher を初実行した．その 3 ケースは 2 セル欠けで公開組が 1 個だけだった．各選択は正しい template と合法 witness を返したが，witness は 1 セルだけ進み，公開 stream が尽きた時点で完成を証明できなかった．続く `94e55f2` で 1 セル欠けに縮めた入力のうち，GTR は成立し，だぁ積みとペルシャ式は同色余剰ぷよを含む配置後に完成 witness が得られなかった．`5dba862` で元の 2 セル欠け盤面と新たな 2 組の公開 stream を再固定してから実行した 2 decision rollout は 3 件とも成立した．従って成功率を任意の入力へ一般化しない．

各ケースには必要セルの色を壊した negative 盤面と，明示された異色制約に反する盤面も固定した．いずれも全 binding で不成立である．GTR の成功ケースは `A=C` を使い，異なる記号への全域的な単射制約を設けていないことを検査する．

次のコマンドで人間が同じ結果と着地後の行を確認できる．出力の `selection`，`binding`，`statuses`，`witness_actions`，`final_rows_bottom_up` を照合する．

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  /home/sion2/workspaces/dev/puyo_ai_dev_platform/.venv/bin/python \
  -m eval.nextgen_template_fixtures
```

## 人間による形状レビュー

以下は**未確認**である．図と上記出典を照合し，必要なら variant を修正してから確認者・日付・結論を記録する．この確認がない間，PUYO-246 は Jira の In Progress に留める．

- [ ] `gtr/left_supported_core`：GTR の 2 色 core と追加支持 `C` の許容性，`A=C` の色条件．
- [ ] `daa/two_interlocking_l`：交互 L 字 2 個をだぁ積み variant と呼ぶ範囲の妥当性．
- [ ] `persian/central_step_flat_tail`：平坦な右 3 列と中央段差の位置，3 色の異色条件．

確認者：未記入．確認日：未記入．結論：保留．
