# PUYO-266: 定型完成後の自由構築と連鎖品質

完成した定型から `build_main` へ移る既存の phase 契約を維持し，候補の重複排除で発火側の順位が構築側へ混ざる不具合を修正した．通常の nextgen は target 10，depth 16，width 250，6 scenario の `nextgen_safe_build` を使う．正式 G2 と人間 GUI QA は未実施のため，品質 PASS／COMPLETE としない．

## 原因と変更

旧 shared batch は同じ plan を 1 候補へ統合し，全戦術で最小の sort key を使っていた．即発火や response の key は構築より先なので，`RuleTacticSelector` が短期攻撃を選んでいなくても，`build_main` の best 候補が即発火へ置き換わった．`fire_main` の閾値を変えるだけではこの経路を防げない．

起点 `b36bbdddc83a223eb200651346ddc7007ddbfd04` の無脅威 GTR 完成 fixture は，初回照合で `phase=completed`，消費手数 0，次の戦術は `build_main` だった．しかし選択 action 0 は 2 連鎖を発火し，shared search の最上位 action 7 は非発火だった．tower fixture でも shared 最上位 action 2 の非発火より action 0 の単発を優先した．

同じ起点の seed 55 を実 scheduler で動かすと，13 手で GTR を完成し，14・15 手目に単発を実行した．その後も 17・18・20・21 手目に単発があり，選択 root は本来の shared 順位 4〜14 だった．phase の 14 手上限ではなく，候補順位と小さい探索設定の問題だった．

修正では semantic candidate ID と plan の重複排除を保ち，検索中に戦術ごとの順位を保存する．`build_main` は元の shared root 順を保ち，response／template／発火は各自の評価順を使う．selector 後の追加探索や並べ替えは行わない．構造 mask の条件を target 10 へ変更するのではなく，明示的な脅威への cancel/counter と決定的短期攻撃の候補・rule 条件を保持した．

`candidate_batch.v2` は戦術別固定順位を表す．旧 v1 は従来の global rank 検証を維持して読み取り，既存 fixture・digest・receipt を書き換えない．trajectory manifest は実際の batch 版を記録し，v1/v2 混在と宣言不一致を拒否する．詳細は[公開契約](puyo-244-nextgen-contracts.md)を参照．

## 探索予算と phase

| Profile | depth / width / scenario | shared / template / response quota | target |
| --- | --- | --- | --- |
| nextgen_safe_build（既定） | 16 / 250 / 6 | 600000 / 128 / 256 | 10 |
| nextgen_smoke | 4 / 4 / 1 | 256 / 128 / 256 | 10 |
| nextgen_diagnostic | 4 / 4 / 1 | 512 / 256 / 512 | 10 |

safe_build の shared 探索量と `legacy-fixed-six` 補完方式は `deep_chain_builder` reference に合わせる．template と response は独立 quota のままで，戦術選択後に予算を増やしたり未使用 quota を移したりしない．native の fail-closed は維持する．

既存の PUYO-255 gate benchmark と PUYO-265 latency corpus は旧探索量を明示し，通常 policy の既定変更で workload が増えないようにした．ただし現行コードでは v2 の修正済み順位を使うため，旧 raw と同じ行動を再現するという意味ではない．過去結果は当時の source SHA に属し，新しい測定は source と batch 版を別途記録する．

固定 6 盤面で shared quota 30000／120000／600000 を比較した．600000 の実使用は 458742〜465036 node で，policy は 0.611〜0.792 s だった．120000 は quota 打切りで 0.663〜0.868 s だったため，この測定では上限を小さくしても時間が比例して短くならなかった．native の並列探索経路と quota 打切りの差を含むため，一般的な単調性は主張しない．正式な latency 合格判定ではなく，reference 相当の上限を使うための局所的な校正である．

完成時には N=14 を待たず既存の `reconcile → completed` で閉じる．未完成のときだけ 14 個目の採用 pair まで定型構築でき，15 個目では mask が無効になる．完成後に静かな構築を続けている間は phase を再開しない．明示的な発火／脅威対応の解決を公開 event で観測した後は，既存契約どおり定型を再選択できる．

## 通常速度での再利用

reference 相当の予算を導入した最初の実装では，通常速度の seed 55／random 相手／2400 tick で採用 4 件，stale 41 件，fallback 0 件だった．同じ自盤面を毎回約 46 万 node 探索し直すため，0.8 秒前後の判断中に相手の公開状態が変わり続けた．安全条件を緩めると過去の snapshot を実行することになるため，その対応は行わない．

最終実装では policy 自身が生成した strict native instance だけを再利用対象へ明示登録する．backend request の全入力（telemetry request ID を除く）が一致する場合だけ，Python の materialized result を 1 件再利用する．注入した native／独自 backend は対象外で，別 instance への交換も失効する．native の設定，capabilities，client，module，呼び出し実装の identity も照合し，spawn 復元後には strict client の ABI/build 検証を行う．

cache は batch ではない．新しい公開 snapshot／到達可能 mask に対して template・response・selector と候補 ID・batch digest を再構成し，採用時には全公開 snapshot を検証する．元の探索の全 node を新しい固定 quota に計上する．`shared_reuse` は出典 request ID と現在の boundary call 数を示し，backend timing は出典の探索時間と明記する．盤面，ツモ，seed，evaluator，quota が変われば再探索する．

合わせて，module 内の不変な Contract 型の `get_type_hints` 結果だけを cache した．schema・型・値・参照・quota の validation は毎回実行する．module 外の拡張型は introspection cache の対象にしない．

## 検証と比較条件

`eval.nextgen_safe_build_diagnostic` は事前固定 seed 55，123，124，各 2 repeat，最大 40 resolution を使用する．両 policy は同じ realtime solo 環境，公開 current/NEXT/NEXT2，native，configured latency 0 tick，停止相手，攻撃送信抑止で実行する．実際の配置入力，連鎖結果，receipt，phase，候補 root evidence，policy 判断時間を保存する．configured latency の対局と通常 wall-clock の GUI 応答性は別の測定である．

比較には次の差が残る．nextgen は GTR を先に構築し，deep_chain には定型 phase がない．補完 seed は nextgen が専用 seed stream，deep_chain が可視観測 hash から導出する．nextgen は hidden 行を未知のまま扱い，既存 deep_chain adapter は自盤面の ghost 行も読む．この比較を同じ batch に対する selector 単独の優劣と解釈しない．

測定結果と再実行用 declaration は [benchmark 証跡](../benchmarks/puyo-266-safe-build/)に保存する．3 seed の部分比較は正式 G2 の 30 seed × 2 repeat を満たさず，only240 や接続 smoke と同様に品質 PASS の代替にしない．

| 測定 | run 数 | seed 55 / 123 / 124 の最大実連鎖 | 最大実連鎖の平均 | premature / 窒息 | 判断 p50 / p95 |
| --- | ---: | --- | ---: | --- | --- |
| 起点 nextgen `b36bbddd` | 3 | 2 / 3 / 2 | 2.33 | 35 / 0 | raw に保存 |
| 修正・最適化前 nextgen `6e82d788` | 6 | 12 / 10 / 10（各 2 repeat 一致） | 10.67 | 0 / 0 | 0.863 / 1.221 s |
| 最終 nextgen `851b02fc` | 3 | 12 / 10 / 10 | 10.67 | 0 / 0 | 0.691 / 0.865 s |
| 最終 deep_chain reference `851b02fc` | 3 | 1 / 10 / 10 | 7.00 | 1 / 1 | 0.528 / 0.651 s |

premature は無脅威環境で発生した 1〜9 連鎖を数える．最終 nextgen は 120 判断，deep_chain は seed 55 の窒息で 113 判断だった．最適化前の deep_chain 6 run は premature 2／窒息 2，平均 7.00，p95 0.647 s だった．3 seed での比較から一般的な優劣は主張しない．最終 source の 3 run は再測定であり，最適化前の 6 run と合算して repeat 数を増やさない．

最終の両 policy・全 3 seed で，配置入力・実連鎖・最終状態・selection・phase・receipt を含む semantic digest が最適化前の repeat 1 と一致した．seed 55 は 13 手で定型を完成し，その後に静かな自由構築を続け，26 手目で実際の 12 連鎖を発火した．最終 nextgen の判断最大値は 0.960 s だった．最適化前の p95 超過は shared だけでなく template／response と Contract 構築も含み，stage の p95 は shared 566 ms，template 133 ms，response 293 ms だった．各 stage の分位点は同じ判断とは限らず，加算しない．

| 通常速度 seed 55 / random | 採用配置 / tick | 経過時間 | stale / fallback | shared cache hit | 判断 p50 / p95 |
| --- | --- | ---: | --- | ---: | --- |
| 最適化前 `6e82d788` | 4 / 2400 | 55.619 s | 41 / 0 | 0 | 0.782 / 0.848 s |
| 最終 `851b02fc` | 15 / 1818 | 38.328 s | 14 / 0 | 14 | 0.583 / 0.756 s |

同じ最大 2400 tick／目標 15 配置で，最終版は配置上限へ到達した．平均採用配置数は 0.072 から 0.391 件/s に改善したが，通常速度の相手進行は wall-clock に依存するため，行動一致の比較ではない．最終版は 11 回の `build_template` の後，新しい脅威に対して `cancel` を採用している．この対戦を定型完成の証拠には使わない．

145 件の関連テストが成功し，追加の cached/fresh 比較でも新しい相手公開状態・request ID・到達可能 mask に対する batch digest が一致した．起点の旧 v1 診断 120 件は変更なしで roundtrip した．完成済み fixture の連続 2 配置と，未完成 fixture の 14／15 手目を実 scheduler の採用 receipt で確認した．通常速度の別の 2 配置 replay は 220 tick の全状態 hash と最終 hash が一致し，埋め込まれた診断 175 件を契約検証した．

## 再実行と人間 QA

既存 release native 拡張を導入済みの環境で，CPU 負荷を直列化して実行する．

```bash
python -m unittest tests.test_nextgen_safe_build tests.test_nextgen_shared_search tests.test_nextgen_contracts tests.test_nextgen_trajectory
python -m eval.nextgen_safe_build_diagnostic --output /tmp/puyo266-new-comparison --repeats 2
python -m eval.nextgen_realtime_diagnostic --mode normal --seed 55 --profile nextgen_safe_build --placements 15 --max-ticks 2400 --omit-replay --output /tmp/puyo266-normal
python -m eval.realtime_versus_ui --seed 55 --policy-a nextgen_tactic_manager --policy-b random --nextgen-templates gtr --nextgen-selection-mode argmax --nextgen-seed 55 --nextgen-commit-turns 14 --nextgen-profile nextgen_safe_build --nextgen-backend native
```

GUI では `nextgen_safe_build`，native，GTR，argmax，seed 55，N=14 を選び，通常速度で完成後の自由構築，単発消し，判断待ち時間，採用／stale，実際の連鎖を確認する．既存 preset に `nextgen_smoke` が保存されている場合は明示的に変更する．smoke と diagnostic は動作確認用の小予算であり，10 連鎖の能力確認には使わない．safe_build も全 seed の品質を保証するものではない．

正式 G2 の全条件，人間 GUI QA，通常対戦での勝敗・脅威ごとの能力は残課題である．PR は draft，Jira は In Progress のまま維持する．

## References

- [PUYO-266](https://shhchan.atlassian.net/browse/PUYO-266)
- [PUYO-265 の速度測定](puyo-265-nextgen-latency.md)
- [safe-build 品質契約](puyo-232-safe-build-target-contract.md)
