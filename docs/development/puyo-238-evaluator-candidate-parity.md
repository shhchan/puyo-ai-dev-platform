# PUYO-238 正規化署名が重なる評価候補の整合

seed146 / target12 の22 decision目で発生した
`native fixed-width tie-break selected a different best candidate` を修正する。
候補の比較項目・SHA-256 tie-break・評価重み・探索予算・target・品質基準は維持する。

## 原因と候補契約

正規化署名は placements と anchors をそれぞれ平行移動・鏡映してまとめる。
盤面上の周囲の埋まり方を表す `trigger_protection` は署名に含まれないため、
同じ署名でも保護率が違う候補がある。

失敗した survivor の盤面は下から `B....B` / `R....R` / `.....G`、他は空。
native の hot 最良候補は全候補の比較キーから選択していた一方、
`CollectEvidence::push` と Python `bounded_quiescence` は同じ署名の最初の候補だけを保存していた。
そのため後から見つかった右端の RED（保護率2/3）は scalar best に採用されても一覧には残らず、
Python が一覧を並べ直すと BLUE（保護率1/2）が最上位になった。
同署名の左側 RED は保護率1/3。候補の欠落や浮動小数点の丸め差ではない。

| 比較項目 | RED（一覧の旧代表） | BLUE（旧Python best） | RED（native best） |
| --- | --- | --- | --- |
| chain / key cost / score | 1 / 3 / 40 | 1 / 3 / 40 | 1 / 3 / 40 |
| remaining link3 / link2 / edges / extension | 0 / 0 / 0 / 4 | 0 / 0 / 0 / 4 | 0 / 0 / 0 / 4 |
| protection | 1/3 | 1/2 | 2/3 |
| trigger height | 0 | 0 | 0 |
| SHA-256先頭 | ab1ffcdd2222535e | c6fb44158fb0caeb | ab1ffcdd2222535e |

差は protection で確定し、最終SHA-256比較まで到達しない。
全キー、実座標、候補一覧のindex、scalar best、旧Python結果は `before/diagnostics.json` に保存する。

共通契約は「同じ署名では既存比較キーが最大の候補を丸ごと保持し、完全同点なら先に列挙した候補を保持」。
Python は署名から保存位置を引き、上位候補でその位置を置換する。
native evidence は同署名が保証する共通項目を除いた suffix で比較し、上位なら全属性を置換する。
native hot 探索・最良候補の選択処理は変更しない。
本盤面では native score 17927 を維持し、Python score は17827から17927になる。
ABI/schema、候補署名、固定幅tie-breakは変わらず、修正後の意味は測定commitで追跡する。

materializer は従来の候補署名照合に加え、scalar best と evidence best の全属性も照合する。
同じ署名の保護率だけを改変した結果と、BLUEをbestとする結果をどちらも拒否する回帰テストを追加した。

## 再現・検証手順

`tests/fixtures/evaluator_candidate_alias.json` は元診断から抽出した盤面と期待値。
`evaluator_candidate_alias_request.hex` は旧commitで再取得した失敗decisionの未加工wire request。
盤面と鏡映盤面の最良候補・全Python/native結果・repeat bytes・候補順反転を検証し、
元decisionをoracle-1 / scenario-6で各2回実行して厳密materializationとdigest一致を確認する。
Rustでも鏡映盤面、候補置換順、完全同点の先着保持、hot/evidence一致、hot allocation 0を検証する。

測定用commitのclean checkoutで `scripts/build_deep_chain_native.sh` を実行する。
以下の `<python>` はrelease wheelをinstallしたCPython 3.12の絶対パス。
旧再現時は `195b41415324bb27643c59fbef8d8799d59a6296` のcheckoutをcwdとし、そのrelease wheelを使う。
`<checkout>` は本変更を含むcheckout。既存の診断ディレクトリ・rawの上書きは拒否する。

```bash
PYTHONPATH=. <python> <checkout>/docs/benchmarks/puyo-238-evaluator-candidate-parity/diagnose.py /tmp/puyo-238-before-new --reproduce
PYTHONPATH=. <python> docs/benchmarks/puyo-238-evaluator-candidate-parity/run_regression.py run /tmp/puyo-238-regression-new
PYTHONPATH=. <python> docs/benchmarks/puyo-238-evaluator-candidate-parity/run_regression.py finalize /tmp/puyo-238-regression-new
PYTHONPATH=. <python> docs/benchmarks/puyo-238-evaluator-candidate-parity/run_regression.py verify /tmp/puyo-238-regression-new
PYTHONPATH=. <python> <checkout>/docs/benchmarks/puyo-238-evaluator-candidate-parity/diagnose.py /tmp/puyo-238-diagnostic-new --corpus
<python> -m unittest tests.test_chain_structure tests.test_deep_chain_native_evaluator tests.test_deep_chain_native_search tests.test_deep_chain_search_backend tests.test_long_horizon_search tests.test_deep_chain_builder
cargo fmt --manifest-path native/deep_chain_native/Cargo.toml -- --check
cargo clippy --locked --manifest-path native/deep_chain_native/Cargo.toml -- -D warnings
cargo test --locked --manifest-path native/deep_chain_native/Cargo.toml
```

回帰測定はseed146 / target12の2 repeats、各40 placementsまたは正当なgame over。
元PUYO-231の設定checksumと完全一致するreference depth16 / width250 / scenarios6 / 600,000 nodes、
safe/no-threat、elapsed timeoutなし、fresh processの逐次実行を使う。
品質基準10、premature/game over 0、p95上限1秒を維持し、この2-run集合を全体採用判定には使わない。
既存ablationのraw schema・検証・集計を利用し、専用manifestと `regression_ticket: PUYO-238` で区別する。

## 2026-09-07 測定結果

測定commitは `013c91c`（完全SHAは `after/experiment_manifest.json`）、clean worktreeは
`/tmp/puyo-238-release-build`。release wheel SHA-256は
`d437f6d4c647b86391271390512bc7e402dcb749dadde533c9e57601825ac415`。
元PUYO-231のmanifest・rawは変更せず、同じconfigurationで新しいlineageを保存した。
PUYO-237のPRは含まない独立branchで測定した。

| 項目 | 修正前（元PUYO-231） | 修正後 |
| --- | --- | --- |
| seed146 target12、2 repeats | 両方21手後にpolicy error | 両方40手完了、error 0 |
| 最大実連鎖 | 未完了のため品質判定対象外 | 両方12連鎖、34手目に発火 |
| premature / game over | 未完了 | 0 / 0 |
| parity mismatch / fallback | 未完了 | 0 / 0 |
| decision p95 | 未完了のため同じ分母で比較不可 | 0.663584秒（80判断、上限1秒） |
| repeat action / plan / trajectory digest | 失敗まで一致 | 3種類すべて一致 |

既存rawの最初の21手と修正後の最初の21手は、両repeatともactionとplanが一致した。
全80判断で厳密materialization、6 scenarioのaccounting、予算上限が通る。
最大expanded nodesは465,036、最大RSSは348,552 KiB。
private sentinel境界監査とseed146/target12の実policy counterfactualも一致し、fallbackは0。

本盤面のnative scalar best・features・scoreは修正前後で同一。
候補一覧index6のRED代表が全属性ごと置き換わり、Python結果がnativeと完全一致した。
既存固定fixture 8件、evaluator corpus 512件、transition oracle 11,264件も不一致0、repeat bytes一致。
hot transition/evaluatorの補助測定は10,000 warmup後600,000 operationsを5 samples実行し、
修正前の最大339.234ms、修正後289.952ms。checksumは一致する。
これは単一盤面の参考測定で、速度改善率や全体性能の採用根拠には使わない。

Python関連72 testsのうち初回71件が成功し、追加した探索テストの誤った属性参照を修正後、
残り1件を再実行して成功した。測定commit後の変更はこのtest assertion、CI、診断・証跡・文書のみ。
Rustは46 tests成功（手動profile 2件は既定ignored）、fmt / clippy成功。
CIにも候補fixtureの変更検知、Python評価器suite、保存済み回帰証跡の検証を追加した。
実行ログとチェック結果は `logs/`、`qa.json` に保存する。

人間はPRの候補保持処理、拒否テスト、`before/diagnostics.json` と
`after-diagnostic/diagnostics.json` のindex6・best・Python結果を比較できる。
保存証跡だけの検証はrelease rebuild不要。

```bash
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-238-evaluator-candidate-parity/run_regression.py verify docs/benchmarks/puyo-238-evaluator-candidate-parity/after
sha256sum -c docs/benchmarks/puyo-238-evaluator-candidate-parity/evidence.sha256
```

この1seed・2 repeatsで全60-runのexperimental baseline採用を判定しない。
PUYO-237と両修正を統合した通常画面QA（PUYO-235）と全体再評価（PUYO-236）は後続作業として残る。

## References

- [PUYO-238](https://shhchan.atlassian.net/browse/PUYO-238)
- [PUYO-231 元測定と失敗診断](puyo-231-target-ablation.md)
- [PUYO-237](https://shhchan.atlassian.net/browse/PUYO-237): root集約の独立修正
- [PUYO-235](https://shhchan.atlassian.net/browse/PUYO-235): 両修正後の通常画面QA
- [PUYO-236](https://shhchan.atlassian.net/browse/PUYO-236): 両修正後の60-run再評価
