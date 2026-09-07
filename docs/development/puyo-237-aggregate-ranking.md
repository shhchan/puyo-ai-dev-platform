# PUYO-237 Python/native root 集約順位の数値契約

Python と native の root 集約を、scenario ID 昇順の binary64 逐次加算へ統一する。
候補の比較項目・順序（ranking v2）、evaluator の重み、target 既定値、探索予算は変更しない。
全 root 順位を照合する materializer の厳密検証も維持する。

## 原因と共通契約

CPython 3.12 の組込み `sum` は浮動小数点の加算精度を改善するアルゴリズムを使う。
[Python 3.12 公式ドキュメント](https://docs.python.org/3.12/library/functions.html#sum)
に記載された変更であり、Rust の逐次加算とは丸め位置が異なる。
演算結果をそのまま比較する本実装では、約1.16e-10の差でも同点判定と後続 tie-break が変わる。
Python の `** 2` と native の乗算も共通演算として固定されていなかった。

共通契約は次のとおり。

1. scenario ID の昇順に並べ、未評価 scenario を除く。対象 fire class 等の抽出後も順序を保つ。
2. 合計は `+0.0` を初期値に、各 binary64 加算を順に丸める。補償加算・再結合をしない。
3. 平均はその合計を対象件数で除算。母標準偏差は同じ平均を使用し、
   `(value - mean) * (value - mean)` を逐次加算して件数で除算した後に平方根を取る。
   `pow` や積和演算への置換をしない。空集合の統計値は `+0.0`、欠損 continuation は既存の `None`。
4. 丸め桁や epsilon を追加せず、既存の比較項目を順に厳密比較する。
   すべて同じ場合は root action の小さい方を優先する。

`_ordered_sum` と native `ordered_sum` が契約を実装する。Python/native の集約で共用される
平均・標準偏差・terminal score 合計に適用する。整数 counter の加算は既存のまま。
Python の結果は旧3.12の僅差から変わり得るが、native の有限値の演算順を維持する。
ABI/schema と ranking v2 の比較項目は変えていない。数値契約の変更は評価 commit で識別する。

## 修正前の再現

旧 `195b41415324bb27643c59fbef8d8799d59a6296` の既存 clean worktree と release wheel で、
PUYO-231 の診断スクリプトを再実行した。`before/` は今回取得した診断であり、過去 raw は保持する。
seed137 target6 は2 decision目、seed141 target6 と seed151 target10 は1 decision目に同じエラー。
選択初手はそれぞれ 0 / 14 / 0 で一致するが、以下の非選択 root の順序が異なる。

| seed / target | Python 旧順位内の相対順 | native / 修正後契約の相対順 |
| --- | --- | --- |
| 137 / 6 | 8 → 14 | 14 → 8 |
| 141 / 6 | 9 → 13 → 7 → 11 | 7 → 11 → 9 → 13 |
| 151 / 10 | 7 → 9 | 9 → 7 |

seed137では旧Pythonのroot8/14の継続平均がともに `153244.8152958153` で、action順に
root8が上だった。逐次加算ではroot14が `153244.81529581532` となり、平均で先に順位が決まる。
seed151では旧root7/9がともに `140076.44444444444` だったが、逐次加算のroot7は
`140076.4444444444` となりroot9が上になる。

seed141のroot7/9のchain score列は、それぞれscenario ID 2/4だけが10780、他は0。
旧Pythonの標準偏差は両方 `4017.4687995746226` だが、逐次加算のroot7は
`4017.468799574622` になる。小さい標準偏差を優先する比較項目でroot7が先に決まり、
旧同点時にroot9を上位にした継続平均の比較まで進まなくなる。このケースでは `** 2` を
乗算に変えるだけでは差は解消せず、分散の加算方法が直接の原因。乗算も共通契約として固定した。

`before/*.json` に全rootの旧key・各scenario値・逐次加算key・両順位を保持する。
`tests/fixtures/aggregate_ranking_requests.json` は同じ失敗時に取得した未加工のwire request、
旧native全22root順位、選択action。release extension がある場合、元の3ケースとseed151の
全4targetをoracle-1 / scenario-6で再実行し、全root・scenario evidence・digest・counterが一致する。
非選択の2順位を交換した偽resultは引き続き拒否する。Pythonの小さいテストとRustの同一bit列テストで、
補償加算との差、平均・分散、scenario入力の並べ替え、coverage、同点action順を固定する。

旧再現は旧commitのrelease buildをinstallし、そのworktree内から次を実行する
（`<checkout>` は本変更を含むcheckout、`<python>` はそのvenvの絶対パス）。

```bash
PYTHONPATH=. <python> <checkout>/docs/benchmarks/puyo-231-target-ablation/error_diagnostics/reproduce.py /tmp/puyo-237-before-new
```

## 12-run 回帰測定

対象は seed137/141 target6、seed151 target6/8/10/12、各2 repeats、各40 placementsまたは正当なgame over。
PUYO-231 と同じ reference depth16 / width250 / scenarios6 / 600,000 nodes、safe/no-threat、
elapsed timeoutなし、fresh processの逐次実行。品質基準10、premature/game over 0、p95上限1秒を保持する。
この選定は不具合回帰用であり、全60-runの品質・baseline採用判定ではない。

`run_regression.py` は測定前に12 identitiesとcommit/build/host/config/元manifest checksumを固定する。
rawは既存PUYO-231のablation schemaを再利用し、`regression_ticket: PUYO-237` と専用manifestで
所属を識別する。集計・検証も既存の品質基準、parity、target、予算、digest処理を利用する。
過去manifestとのconfiguration完全一致と、各decisionのrelease provenanceを照合する。

測定コードをcommitし、同commitのclean worktreeで `scripts/build_deep_chain_native.sh` を実行する。
wheelを測定checkoutの `dist/native/` に配置し、測定用venvにinstallしてから実行する。

```bash
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-237-aggregate-ranking/run_regression.py run /tmp/puyo-237-regression-new
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-237-aggregate-ranking/run_regression.py finalize /tmp/puyo-237-regression-new
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-237-aggregate-ranking/run_regression.py verify /tmp/puyo-237-regression-new
.venv/bin/python -m unittest tests.test_long_horizon_search tests.test_deep_chain_native_search tests.test_deep_chain_search_backend tests.test_deep_chain_builder tests.test_deep_chain_native_evaluator
cargo fmt --manifest-path native/deep_chain_native/Cargo.toml -- --check
cargo clippy --locked --manifest-path native/deep_chain_native/Cargo.toml -- -D warnings
cargo test --locked --manifest-path native/deep_chain_native/Cargo.toml
```

## 2026-09-07 測定結果

12/12 run が各40手を完了し、policy error 0、正当なgame over 0。
全480 decisionで全root順位検証が通り、simulator parity mismatch / fallback / scenario accounting異常は0。
6組すべてでaction・plan・trajectory digestが一致した。
対象3seedのprivate sentinel境界監査と、seed151全4targetの実policy counterfactualも一致し、fallbackは0。

| target | seed | 完全評価run | 最大実連鎖（各repeat共通） | premature（両repeat） | game over | decision p95秒 |
| --- | --- | --- | --- | --- | --- | --- |
| 6 | 137 / 141 / 151 | 6 / 6 | 6 / 7 / 6 | 6 | 0 | 0.665888 |
| 8 | 151 | 2 / 2 | 2 | 2 | 0 | 0.699364 |
| 10 | 151 | 2 / 2 | 10 | 0 | 0 | 0.678843 |
| 12 | 151 | 2 / 2 | 13 | 0 | 0 | 0.722445 |

全targetでこの回帰集合のp95は1秒以下。最大expanded nodesは465,036で予算600,000以下、
最大RSSは351,192 KiB。品質基準10に対してtarget6/8は未達で、prematureが計8件ある。
seed151 target8の最大実連鎖2もそのまま残す。今回の12runを全60-runの採用判定に流用しない。
PUYO-238の独立修正、PUYO-235の通常画面QA、PUYO-236の全体再評価が残る。

測定は `0a535ea5f74333b044c722185b7a4fd12f373afb` のclean worktree
`/tmp/puyo-237-release-build` で実施した。release wheel SHA-256は
`8958c2d01ca1e7c400d58238549d62a7699f64092d306f2d1e046761edfcf5bd`。
元PUYO-231のcommit/buildは元manifestへのlineageで保持し、configuration完全一致を確認した。
測定後に追加したseed141の固定bit列assertionはテストのみで、探索・集約処理には追加変更がない。

`docs/benchmarks/puyo-237-aggregate-ranking/after/` にmanifest、12raw、再計算可能なsummaryを保存した。
`diagnostics.json.gz` は6再現条件×2実行modeの全root key/scenario値と4targetのprivate比較。
`comparison.json` は旧Python/native順位と新順位の照合を記録する。
3元ケースの選択action・native全22root順位は旧nativeと同一で、Pythonの全66 keyが
修正前診断の逐次加算keyに厳密一致した。追加診断は軌跡の品質・性能には算入していない。

診断を再作成する場合、測定commitのcheckoutを作業ディレクトリにして本変更のスクリプトを使う。
既存証跡の上書きは拒否する。

```bash
PYTHONPATH=. <python> <checkout>/docs/benchmarks/puyo-237-aggregate-ranking/diagnose.py /tmp/puyo-237-regression-new
# 保存済み証跡の整合性確認はrelease rebuild不要
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-237-aggregate-ranking/run_regression.py verify docs/benchmarks/puyo-237-aggregate-ranking/after
sha256sum -c docs/benchmarks/puyo-237-aggregate-ranking/evidence.sha256
```

関連Python suiteは73 tests成功。seed141のassertion補強後は変更した2 testsを再実行して成功。
Rustは46 tests成功（手動profile 2件は既定でignored）、fmt / clippy成功、
release最適化下の数値契約1 testも補強後に成功した。実行情報は `qa.json` とログに保存。
旧PUYO-231の証跡は変更していない。人間は本表、`comparison.json`、PRの集約関数と
検証器を拒否させる回帰テストを確認できる。通常window QAはPUYO-235で実施する。

## References

- [PUYO-237](https://shhchan.atlassian.net/browse/PUYO-237)
- [PUYO-231](https://shhchan.atlassian.net/browse/PUYO-231) / [実測と未完了理由](puyo-231-target-ablation.md)
- [PUYO-238](https://shhchan.atlassian.net/browse/PUYO-238): evaluator候補属性の不一致は別修正。
- [PUYO-235](https://shhchan.atlassian.net/browse/PUYO-235) / [PUYO-236](https://shhchan.atlassian.net/browse/PUYO-236): 両修正取り込み後のGUI QAと60-run再評価。
