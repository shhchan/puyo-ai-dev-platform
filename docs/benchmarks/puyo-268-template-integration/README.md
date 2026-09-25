# PUYO-268 定型保持・採用の検証

実装は [接続仕様](../../development/puyo-268-template-integration.md) を参照．この測定は targeted fixture と nextgen 1 seed の短い実対局であり，reference 実対局との一般品質比較や G2 PASS を示さない．PUYO-268 は draft PR / In Progress のまま，残る一般比較を PUYO-271 後半へ引き継ぐ．人間の GUI QA は未実施．

## source と再現条件

- 最終実装 source: `b991c920fcb94e4407fcfeb706fe1a66a150187c`．最終 artifact は `results/`．
- 中間の長期順位だけの source: `efc242149487443098fa8941a1193754b15a4196`．未完成の失敗 run も `intermediate-chain-only/` に保存し，最終値と合算しない．
- native binary SHA-256: `79b7d6f43305a27d169722ca014cfba09cd0e97932d49284896de63231bdb0d2`．PUYO-272 release build，source `9b64186087ea8b6e1219861ddbb534b39233966c`，scenario-6 / 6 workers．専用 venv へコピーし，共有環境を変更していない．
- 同一 host で直列実行．summary に platform/Python/CPU affinity，公開盤面・current/NEXT/NEXT2 は fixtures の全 request，config/quota と counters は各 raw に記録する．fixture 元ファイル，script，raw gzip の SHA-256 も保存した．測定中の source 変更は両 run とも false．
- native shared は depth16/width250/6 scenarios/最大600000 nodes，nextgen template128/response256 nodes．fixture は warmup1＋3 repeat，cache なし．両条件で同じ公開盤面推定・公開ツモ・scenario seed・native binary・shared quota を使う．
- reference は同じ公開入力を受ける無制約 deep-chain backend の比較であり，reference policy の実対局ではない．fixture の current chain は実採用の対局結果と区別する．

## 同一公開入力の固定予算比較

時間は batch 全体の ms，p50/p95．3 repeat の backend semantic digest は全8組で一致した．

| fixture | 制約 | p50/p95 ms | 選択戦術 / root | current chain |
| --- | --- | ---: | --- | ---: |
| persian_flat_three | なし | 458.3/491.1 | build_main / 19 | 0 |
| persian_flat_three | あり | 288.3/292.7 | build_template / 7 | 0 |
| persian_l_corner | なし | 469.8/494.0 | build_main / 5 | 0 |
| persian_l_corner | あり | 13.7/16.2 | build_main / 0 | 1 |
| persian_other_color_support | なし | 444.7/493.3 | build_main / 8 | 0 |
| persian_other_color_support | あり | 298.6/312.0 | build_template / 8 | 0 |
| persian_public_completion | なし | 490.6/530.0 | build_main / 1 | 0 |
| persian_public_completion | あり | 455.8/458.3 | build_template / 11 | 0 |

`persian_l_corner` の制約ありは，矛盾した固定 binding を強制的に active にした全失敗 fixture である．全 root が violated，build_template は unavailable，表の root0 は build_main fallback であり，定型保持の成功ではない．production の phase reconcile はこの初期矛盾を検出して先に閉じ，次の探索は無制約になる．元の横 2 から L 字を新たに作る候補は native/Python の両方で root 違反として除外した．

他色支持は維持でき，公開完成 fixture は root11 を定型候補として選べた．保持中の探索最大連鎖は制約なしより低い場合がある．保持は大連鎖品質向上の証明ではなく，完成後の解除・自由構築を含む実対局で別途比較する．

## seed55 の実採用と進捗

`SafeNoThreatMatch(55)`，GTR 固定，configured inference0，nextgen_safe_build，20 resolved placements．全公開入力・candidate batch・selection・receipt・root trace・実連鎖・実入力を gzip に保存した．

| 実装 | 定型成立 | phase 終了 | 最大実連鎖 | premature（1〜9連鎖） | 窒息 | decision p50/p95 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 中間・長期順位のみ | 未成立 | 未完成14手 limit | 0 | 0 | 0 | 0.611/0.715 s |
| 最終・公開進捗を比較 | 12実設置で GTR 成立 | 次の13番目 requestで completed，保持全解除 | 0 | 0 | 0 | 0.633/0.881 s |

両 run は20件すべて activated，scheduler errors は空．最終 run の最初の12件は build_template，残り8件は build_main．この1seedでは成立の改善を確認したが，20手で発火がなく最大実連鎖は0のままである．decision 時間の改善も示していない．reference 実対局との定型成立率/所要手数・最大実連鎖・premature・窒息・decision 比較は **PUYO-271 後半の40手×3seed** で再判定する．正式 G2 は PUYO-266 に残る．

## 回帰

最終進捗調整後の対象103件が成功．調整前には codec/trajectory/response/backend parity を含む154件，未知 guard の追加後には関連46件が成功した．詳細コマンドと実ログは `verification.txt`．新規 integration suite では以下を確認する．

- 凍結した横2/L字入力，色置換・mirror・同色 alias・他色支持・L字一般の許容，guard unknown と quota．
- matcher の同じ root 層の比較，全 root 進捗，固定 binding 保持，semantic digest/cache 分離．
- Python/native の複数整合 root 順位，公開完成・sampled 完成・unknown/cutoff/violation の区別．
- 3定型の公開 fixture 成立，中間配置，現在完成 root の phase 終端特例，完成 witness だけでは閉じず実採用後の次公開盤面で解除．
- 14/15手境界，timeout 不消費，実 receipt/ledger，定型全失敗より必要な生存単発を優先．
- 271 fixture/before の SHA は変更せず，旧 guard なし評価と新しい guard 付き評価を別列へ保存．

再実行は repository root の専用 release-native venv で以下を実行する．新しい出力先を指定する．

```bash
.venv/bin/python docs/benchmarks/puyo-268-template-integration/measure.py --output /tmp/puyo268-new-run
.venv/bin/python -m unittest tests.test_template_preserving_integration tests.test_template_catalog tests.test_nextgen_template_catalog tests.test_template_phase tests.test_nextgen_shared_search tests.test_nextgen_tactic_manager tests.test_nextgen_survival tests.test_puyo_271_regression
```
