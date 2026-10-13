# PUYO-266：発火目標と 60 手の追加比較

[HTML レポート](index.html)に結果と pattern 別の表をまとめた．本比較は探索的評価であり，既存の [正式 46 手 gate](../puyo-266-single-quality/sprint15-formal-v4/README.md) の事前登録条件や raw を変更しない．

同じ eスポーツ通の 30 pattern を各設定で 2 回実行し，最初の実 10 連鎖以上，窒息，または 60 配置で停止した．相手は停止し，攻撃の送受信を抑止した単独環境である．current／NEXT／NEXT2 の公開情報，depth 16，width 250，6 scenario，shared quota 600,000 は両設定で同じである．95,000 点設定では探索の発火分類・不足点減点，`fire_main` 候補と rule selector にスコア目標を適用した．構造評価の連鎖数目標は 10 のままである．

| 指標（各 pattern の repeat 1） | 10 連鎖目標 | 95,000 点目標 |
| --- | ---: | ---: |
| 実 10 連鎖以上 | 28/30 | 5/30 |
| 実 95,000 点以上 | 0/30 | 3/30 |
| 平均最大実連鎖 | 10.03 | 4.27 |
| 窒息 | 0/30 | 16/30 |
| 60 手到達・10 連鎖未達 | 2/30 | 9/30 |
| repeat 不一致 | 0 | 0 |

旧 46 手で最大 2 連鎖だった 11299 は 49 手で 11 連鎖へ到達した．18078 と 42936 は 60 手でも最大 3 連鎖だった．95,000 点目標の実到達は 51976（14 連鎖・100,481 点），54235（14 連鎖・107,961 点），63275（13 連鎖・98,580 点）である．95,000 点未満の 10 連鎖以上は 31637 と 65535 で見られた．

旧 46 手評価の平均最大実連鎖 9.67 と今回の 10.03 は，後者が最初の 10 連鎖以上で停止するため，同一の最大値観測窓ではない．また 95,000 点未満の 10 連鎖で停止した run は，その先の 95,000 点到達可否を観測しない．小消しの局所分類も本追加比較では再判定していない．pattern 29377 は 6 手目の 1 連鎖後，29 手目に 11 連鎖・52,501 点へ到達した．旧「不当」分類を後続の本線失敗と解釈しない．

`manifest.json` は source commit・全対象ファイルの SHA，native build，配ぷよ source の SHA と実行設定を固定する．`report.json` は 120 件すべての集計行と各 raw の SHA を収める．全 raw は作業ツリーの `runs/puyo-266-fire-goal-v3/` に保存し，代表 8 件の repeat 1 raw を本ディレクトリに収録した．

- `chain10-pattern-11299-repeat-1.json.gz`：46 手から 49 手への回復．
- `chain10-pattern-18078-repeat-1.json.gz` と `chain10-pattern-42936-repeat-1.json.gz`：60 手でも低連鎖．
- `chain10-pattern-29377-repeat-1.json.gz`：小発火後に 11 連鎖．
- `score95000-pattern-31637-repeat-1.json.gz`：13 連鎖でも 95,000 点未満．
- `score95000-pattern-51976-repeat-1.json.gz`，`score95000-pattern-54235-repeat-1.json.gz`，`score95000-pattern-63275-repeat-1.json.gz`：実 95,000 点達成例．

全 raw がある作業ツリーでは以下で再生照合できる．実行時と同じ native wheel および配ぷよ source が必要である．

```bash
cd /home/sion2000114/workspaces/dev/puyo-s15-266
.venv/bin/python -m eval.nextgen_fire_goal_comparison verify --output runs/puyo-266-fire-goal-v3
```

上記の full replay，repeat の意味的ハッシュ一致，固定 quota，公開情報境界，実 lock の一致を確認した．この追加比較だけで PUYO-266 の既存 A/C を PASS／COMPLETE へ変更しない．
