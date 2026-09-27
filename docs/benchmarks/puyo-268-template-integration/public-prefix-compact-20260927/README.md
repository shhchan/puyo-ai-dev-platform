# 2026-09-27 GTR 公開 prefix の中間進捗

人間 GUI の seed 123 replay/ledger は未保存である．以下は保存済み PUYO-271 after の公開入力に基づく新規機械再現であり，元の human/0.25x run の復元ではない．セカンド 10 連鎖の逆発火は seed 不明で，完成後品質を所有する PUYO-266 の調査対象とする．有効な逆発火を禁止していない．

## 原因と修正

保存済み seed 123 の 6 手目は current=(1,2)，NEXT=(2,3)，NEXT2=(3,2)，GTR の固定 binding は A=2/B=1/C=3 だった．root 0/1/5 はいずれも現在の必須セル進捗 +2 で，長期順位が高い root 1 を実採用した．その後の 7/8 手目では全 root の現在進捗が 0 となり，14 手までに最後の A が入らなかった．

production の未知上段を含む matcher は current だけを評価し，backend の完成 witness は全セル完成の証拠だけを渡していた．このため，公開 NEXT を使って中間進捗を増やす候補を比較できなかった．root 0 の後に root 0 を置けば NEXT の A を左列へ入れ，進捗 +3 にできる．

追加探索は有効な選択済み phase のみ，既存 template128 nodes の残予算を使う．root を交互に試し，公開 NEXT/NEXT2・無消去・可視 12 段内・必須セルと guard 保持の経路を記録する．進捗は witness が示す下限であり，未探索候補の完成不能や未知未来の保証ではない．現在完成，公開 prefix 完成，公開 prefix 進捗，現在進捗，長期順位の順で比較し，生存優先と現在 root だけの実行 plan を維持する．初回選択や終了済み phase の再選択には追加予算を使わない．

## 同じ公開入力の比較

`exact-request-6.json.gz` は元 ledger/row，全比較の request，candidate batch，quota/counters，順位，witness を保持する．3 条件をそれぞれ warmup1＋3 repeat で測定した．

| 条件 | 3 回の選択 root | 現在進捗 | 記録された公開 prefix 進捗 |
| --- | --- | ---: | ---: |
| prefix 証拠を順位から外す ablation | 1/1/1 | +2 | +2（順位には不使用） |
| 最終 prefix 順位 | 0/0/0 | +2 | +3，witness=(0,0) |
| 無制約 reference backend | 15/15/15 | 対象外 | 対象外 |

制約ありの 2 条件は backend semantic digest が全 repeat で一致し，共有探索は各 458206 nodes，matcher は各 128 nodes だった．候補不足による fallback ではなく，同じ backend 結果の順位変更である．旧 raw の root 1/0/5 の sampled 最大連鎖は 4/3/2 だったが，これは制約保持中の将来サンプル値で，現在発火や 10 連鎖の保証ではない．

## 3 seed の実採用

`SafeNoThreatMatch`，GTR 固定，nextgen_safe_build，configured inference0，最大 40 resolved placements．human/0.25x，random，通常速度 GUI の結果を混ぜない．旧実装は同じ host/native 環境で新規再測定し，保存済み PUYO-271 after と全 seed の semantic digest が一致した．旧 seed 55 窒息と初回 GTR 2/3 は消していない．

| seed | 旧→最終 初回 GTR 完成手数 | 最大実連鎖 | 窒息 | premature | 旧→最終 decision p50/p95（秒） |
| --- | --- | --- | --- | --- | --- |
| 55 | 12→10 | 0→10 | 35 手で窒息→40 手生存 | 0→0 | 0.632/0.798→0.648/0.800 |
| 123 | 14 手 limit→9 | 10→10 | なし→なし | 0→0 | 0.748/0.973→0.608/0.755 |
| 124 | 9→9 | 11→11 | なし→なし | 0→0 | 0.684/0.813→0.633/0.825 |

初回成立は 2/3→3/3．最終 120 receipts はすべて activated，scheduler errors は空．seed 123 は 31 手目に実 10 連鎖を発火した．同じ 9 実設置後の最大列高は旧 6→最終 5，旧は必須セル 7/8，最終は 8/8 だった．phase 境界で比較すると旧 14 手の列高は [2,7,6,7,4,2]，最終 9 手では [5,3,5,4,1,0] となるが，こちらは設置数が異なる比較である．

PUYO-271 after の保存済み deep_chain 実対局は同じ 3 seed で最大連鎖 1/10/10，seed 55 は premature1/窒息ありだった．今回の reference は上記 exact-input backend 比較とこの保存済み実対局の参照であり，reference policy の新規 3 seed 再測定ではない．既存 reference adapter の自盤面上段可視性と scenario seed の差も残る．一般品質や正式 G2 の PASS とは扱わない．

## source・性能・証跡

- 最終 source: `9ce014552d5b103f87290095b2156e29e83608a4`．測定中の code/config 変更は false．
- 旧再測定 source: `258397624245407e402ebf81c5ca20bdc007a475`．`git archive` による一時 export を使用し，worktree/branch を切り替えていない．raw と全 source hash は `../public-prefix-20260927/fresh-before/`．
- 中間 source: `b502627fbe277982c93f9f3ec7ee83cd5a560b90`．GameState deepcopy 版の raw は `../public-prefix-20260927/`．追加 prefix の約 60–80 ms を確認したため，既存 `compact_search.transition` に置き換えた．3 定型で全追加遷移を GameState oracle と突合し，最終 3 seed の全 action/chain も中間版と一致した．
- native SHA-256: `79b7d6f43305a27d169722ca014cfba09cd0e97932d49284896de63231bdb0d2`．release/source `9b641860`，scenario-6/6 workers．ABI/native build は変更していない．
- 共通 shared budget: depth16/width250/6 scenarios/600000 nodes，template128/response256．source/config hash，Python/platform/affinity，native capabilities，raw SHA は各 summary/manifest．
- 旧・中間・最終は同じ host で直列実行した（実行順は中間→旧→最終）．seed 55/124 の p95 は旧よりわずかに増えており，速度全般の改善とは主張しない．trajectory の違いと壁時計の変動も含む．
- 対象 105 tests 成功（44.575 s）と追加の 3 定型 compact/GameState oracle 1 test 成功（0.177 s），計 106 件．詳細は `verification.txt`．

元 GUI run の同一性と修正後の人間 GUI 確認，一般品質・正式 G2 は未確認である．PUYO-268 は In Progress，PR #165 は draft を維持する．

## 再確認

```bash
.venv/bin/python -m unittest tests.test_template_preserving_integration
.venv/bin/python docs/benchmarks/puyo-268-template-integration/prefix_measure.py --baseline-dir <PUYO-271-after-dir> --output /tmp/puyo268-new-prefix
.venv/bin/python docs/benchmarks/puyo-268-template-integration/prefix_baseline.py --source-sha 258397624245407e402ebf81c5ca20bdc007a475 --output /tmp/puyo268-new-before
```

`comparison.json` には fresh before/最終の比較と旧 raw 一致・compact trajectory 一致を保存した．`summary.json` の baseline latency は当時保存された値であり，上表の fresh before latency と区別する．
