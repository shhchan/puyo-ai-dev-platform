# PUYO-270 比較記録

生存の合成反例と測定条件は [契約](../../development/puyo-270-survival-envelope.md) を参照．

seed 55/123/124，各 1 回，最大 40 配置，`nextgen_safe_build` と `deep_chain` reference，configured inference 0，inactive opponent を比較する．既存の PUYO-271 before と同じ native binary（source c0c77d9，release/cp312/scenario-6）を読み取り専用で再利用する．本件で native source/ABI は変更していない．Python source SHA と binary source SHA は別々に記録する．

再実行コマンド：`python -m eval.nextgen_safe_build_diagnostic --output <new-dir> --profile nextgen_safe_build --repeats 1`．出力先を再利用しない．正式 G2，GUI QA，未知未来の保証には用いない．

## 採用結果

採用した実装 source は `beead67b50d061065acb1f3125d392501f61ddbb`．`paired/` の 6 raw と `summary.json`/`declaration.json` が最終測定である．source の変更中実行はない．通常の 3 seed では nextgen/deep_chain とも変更前と全着手が一致した．batch v3 と診断理由の追加により semantic digest 自体は変更される．

| policy | seed | 変更前/後の最大実連鎖 | 変更前/後の premature | 変更前/後の自滅 |
| --- | --- | --- | --- | --- |
| nextgen | 55 | 12/12 | 0/0 | なし/なし |
| nextgen | 123 | 10/10 | 0/0 | なし/なし |
| nextgen | 124 | 10/10 | 0/0 | なし/なし |
| deep_chain reference | 55 | 1/1 | 1/1 | あり/あり（33 配置） |
| deep_chain reference | 123 | 10/10 | 0/0 | なし/なし |
| deep_chain reference | 124 | 10/10 | 0/0 | なし/なし |

| decision time | 変更前 p50/p95 | 変更後 p50/p95 |
| --- | --- | --- |
| nextgen（120 decisions） | 0.706/0.885 s | 0.702/0.855 s |
| deep_chain（113 decisions） | 0.544/0.642 s | 0.543/0.653 s |

単一 repeat の観測値であり，時間差を有意な高速化と扱わない．同じ公開入力へ有限 probe を再適用した before/after の nextgen では，candidate witness gap，witness があるのに死亡 root を選択/実行した件数，witness の receipt 不採用はいずれも 0 件だった．この通常 seed 群では必要単発の機会がなく，生存改善そのものは合成反例と実 scheduler のテストで確認している．隠し行は `public_estimate` であり，この件数は未知未来までの avoidable suffocation 判定ではない．

## 検証と manifest

- 最終実装で関連 95 tests が成功．`unit-tests.txt` に結果を保存した．コマンドは契約文書の検証節を参照．11 件の生存 tests は必要単発/安全非発火/消去後死亡/既知 NEXT/回避不能/到達不能/予算/期限/未知情報/通常発火維持/実 receipt を含む．
- `comparison.json` は seed ごとの結果と decision ごとの候補・順位・receipt 分類を持つ．`manifest.json` は最終 6 raw，source，config を含む declaration，集計，中間/棄却 run，baseline，解析器の SHA-256 と実際に読み込んだ native binary の hash/capabilities を持つ．
- 再解析：`PYTHONPATH=. python docs/benchmarks/puyo-270-survival/analyze.py`．旧 v1/v2 と v3 の codec を通し，raw の semantic digest，実連鎖の集計，通常局面の着手同値を確認する．
- 保存物検証：`PYTHONPATH=. python docs/benchmarks/puyo-270-survival/analyze.py --verify`．

## 中間測定と棄却

`rejected-9284bf6/` は source `9284bf6` の不採用記録である．安全な 12 連鎖まで非発火へ置換したため seed 55 が 37 配置・最大 0 連鎖で死亡した．nextgen の 3 raw を保存し，基準側の途中で自分の測定プロセスを停止した．この不完全 run に最終 summary はない．通常の安全な発火戦術を保持する修正と対例を追加した．

`intermediate-1b7acdf/` は修正後の 6 run が完了した中間記録である．品質は 12/10/10，premature 0，自滅 0 だった．生存 envelope の二重適用を共通 `SelectTacticStep` の 1 回へ整理し，採用する時間測定を `paired/` へ取り直した．中間値を最終測定へ混ぜない．
