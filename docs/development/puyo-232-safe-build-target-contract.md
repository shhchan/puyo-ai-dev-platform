# PUYO-232 safe-build target と品質契約

既定の内部 target を6から10に変更し、実発火の品質基準10と整合させた。
設定整合は完了条件であり、experimental baseline の品質合格を意味しない。
GUI/CLIで明示指定した1〜19は保持する。たとえば target7 の7連鎖は内部では
`target_fire` だが、safe/no-threat品質統計では引き続き premature となる。

## 契約と伝播

`train/config/deep_chain_builder.yaml` は config v1.1、品質契約
`puyo.deep_chain_builder.safe_build.v2`、`default_target_chain_count: 10`、
`quality_floor: 10` を宣言する。profile の探索量と evaluator weights は変更していない。

| 経路 | target の扱い |
| --- | --- |
| policy直接生成 | 引数省略時は設定の既定target、明示引数があればその値 |
| launcher / realtime CLI / policy factory / smoke CLI | 共通の既定定数10。明示した値をpolicyへ渡す |
| Python/native search | `LongHorizonSearchConfig.minimum_chain_count` へ伝播 |
| evaluator / terminal classification / root ranking | 探索要求のtargetを使用。既存の評価重み・順位方式を維持 |
| diagnostics / plan / replay | 実targetとplan objectiveに同じ値を記録。policy診断には品質基準と契約版も追加 |
| 新canonical manifest | 実target10、品質基準10、契約版、設定checksum、全60 identityを別々に記録 |

safe-buildの0連鎖は `quiet_continuation`、1〜9は `premature_fire`、10以上は
`target_fire`。quietの安全な継続候補を目標未達の発火より優先する既存規則を維持する。
`forced_safety` を明示した探索では目標未達の発火を `forced_safety_fire` と分類できるが、
通常のDeepChainBuilderPolicyはsafe-buildを要求する。安全例外が記録された場合、新canonical
summaryの `forced_safety_fires` に理由・placement・実連鎖数を別計上し、1〜9連鎖を
premature集計から除外しない。winning-score例外も通常safe-buildでは設定しない。

## 過去評価との分離

- `eval.deep_chain_builder_benchmark.CANONICAL_TARGET_CHAIN_COUNT` はPUYO-204の6を保持。
- PUYO-189/204/231のraw、manifest、定数、過去説明資料は上書きしない。
- 新コマンド `eval.deep_chain_safe_build_benchmark` はtarget10だけを宣言し、
  出力先は `docs/benchmarks/puyo-236-safe-build-baseline`。
- 新実行manifest schemaは `puyo.deep_chain_safe_build_benchmark.v2`。
  実行・正式評価の担当はPUYO-236、本契約の導入はPUYO-232。
- PUYO-231のrunnerを明示的な不変 `ExperimentContract` 引数で共用する。
  過去コマンドの既定条件は6/8/10/12の240 identitiesのまま。
- 契約が異なるmanifestの読み込み・resume・finalizeを拒否する。新評価から過去の既定出力先への書き込みも拒否する。
- PUYO-204の `verify --historical` は測定commitの設定blobをGit履歴から取得して照合する。
  通常のverifyは引き続き現在の設定との一致を要求する。履歴が不足する場合は検証失敗となる。

再実行はmanifestの `build_provenance.evaluated_commit` を別のclean worktreeでcheckoutし、
そのcommitのrelease wheelをbuild/installして、新しい空の出力先を指定する。
当時のtarget6軌跡を再現するには当時のcommitが必要であり、現在のコードでtarget6を
指定するだけではPUYO-229/237/238の変更以前の挙動は復元されない。

```bash
bash scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_safe_build_benchmark init --output-dir /tmp/new-safe-build
.venv/bin/python -m eval.deep_chain_safe_build_benchmark diagnostic --target 10 --output-dir /tmp/new-safe-build
.venv/bin/python -m eval.deep_chain_safe_build_benchmark run --output-dir /tmp/new-safe-build
.venv/bin/python -m eval.deep_chain_safe_build_benchmark verify --output-dir /tmp/new-safe-build

.venv/bin/python -m eval.deep_chain_builder_benchmark verify --historical
.venv/bin/python -m eval.deep_chain_target_ablation verify
```

## 変更理由と残課題

[PUYO-231比較](puyo-231-target-ablation.md)ではtarget10は58/60runを完全評価し、
repeat1の29seedで最大実連鎖平均9.0、10以上24/29、発火なし3。
両repeatでpremature4、game over4、成功decisionの観測p95は0.739339秒だった。
未完了があるため、このp95は正式な性能PASSではない。
共通27seedでtarget6比の最大実連鎖平均差は+2.444444だが、目標10への変更だけでは品質未達が残る。

- seed126/130: 小連鎖発火。安全な継続候補の枯渇・予測と実発火の差をPUYO-233で分析する。
- seed133/147: 未発火game over。非発火盤面評価・生存制御は設定変更から独立した改善が必要。
- seed135: 40手まで発火なし。scenario支持数と発火タイミングの検証が必要。
- parityエラーはPUYO-237/238で先行修正済み。過去未完了runを本チケットで補完して過去結果を再集計しない。

PUYO-233が改善案を採否判断して独立Taskへ分割し、PUYO-235で通常GUI QA、
PUYO-236で修正後の全60runと性能を再評価する。通常windowの目視は本チケットでは未実施。

## 人間による確認

`python main.py` → 観戦 → `deep_chain_builder` を選び、目標連鎖の初期値10を確認する。
7や19へ変更して開始し、HUDのaimとplan objective、保存replayのtargetが一致することを確認する。
以前保存したtarget6のpresetは明示設定としてそのまま保持する。

## 自動検証

- 既存の関連suite: 117 tests成功（405.796秒）。
- 新規契約suite: 4 tests成功。Python/nativeの実盤面・順位比較: 1 test成功。
- Rust: `target_ten_fire_boundaries_keep_safety_separate` 成功（0〜19 × safe/forced）。
- GUI CLIの既定10と、全整数1〜19の明示指定を検証。Python/native両backendで
  全整数のtargetがsearch/backend設定/planへ伝播し、fallbackがないことを確認。
  1/7/19のnative controller/replay伝播も既存suiteで確認。
- PUYO-204 historical verify、PUYO-231 artifact verify成功。
- 変更したPythonファイルのRuff、Rust format、`git diff --check` 成功。

```bash
.venv/bin/python -m unittest tests.test_deep_chain_target_ablation \
  tests.test_deep_chain_builder tests.test_deep_chain_builder_benchmark \
  tests.test_launcher tests.test_realtime_versus_ui tests.test_deep_chain_builder_smoke \
  tests.test_deep_chain_search_backend tests.test_realtime_arena
.venv/bin/python -m unittest tests.test_deep_chain_safe_build_contract \
  tests.test_deep_chain_native_search.TestDeepChainNativeSearch.test_safe_quiet_target_and_safety_ranking_match_python
cargo test --locked --manifest-path native/deep_chain_native/Cargo.toml \
  target_ten_fire_boundaries_keep_safety_separate
```

## 重点seedの回帰再実行

測定commitは `2c53bf8`、clean worktree `/tmp/puyo-232-release-build` からrelease buildした。
wheel SHA-256は `a677d55f52ef652df079f0400583603d79feeed514bdc5883e0ac737382b070f`。
証跡は `docs/benchmarks/puyo-232-safe-build-contract/` に保存した。
新canonical runnerの動作確認として、事前に選んだ6seedのrepeat1を各40手まで実行した。
manifestの実行契約はPUYO-236だが、これはPUYO-232の部分回帰QAであり、PUYO-236の完了ではない。

| seed | 最大実連鎖 | premature | game over | PUYO-231 target10とのaction・実結果 |
| --- | --- | --- | --- | --- |
| 123 | 10 | 0 | なし | 一致 |
| 126 | 2 | 1 | なし | 一致 |
| 130 | 3 | 1 | なし | 一致 |
| 133 | 0 | 0 | あり | 一致 |
| 135 | 0 | 0 | なし | 一致 |
| 147 | 0 | 0 | あり | 一致 |

全239手でactionと実結果（連鎖・score delta・game over）が過去target10と一致し、
simulator parity mismatch / fallbackは0。forced-safety記録は0。
同一初期盤面のcold/warm/private counterfactual digestも一致した。
設定版が変わるため、設定を含むplan/search識別子そのものの同一性は要求しない。

観測decision p95は0.762711秒、最大0.834443秒。失敗seedを意図的に含む部分集合なので、
30seed全体の品質・性能推定には使わない。60 identities中6runだけが完全評価され、
残り54はpending、baselineは不採用のまま。target10でも残る失敗を再確認した結果である。

```bash
# 測定commitと同じrelease buildを使用し、新しい空の出力先を指定する。
.venv/bin/python -m eval.deep_chain_safe_build_benchmark init --output-dir /tmp/new-contract-regression
.venv/bin/python -m eval.deep_chain_safe_build_benchmark diagnostic --target 10 --output-dir /tmp/new-contract-regression
for regression_seed in 123 126 130 133 135 147; do
  .venv/bin/python -m eval.deep_chain_safe_build_benchmark worker \
    --target 10 --seed "$regression_seed" --repeat 1 --output-dir /tmp/new-contract-regression
done
.venv/bin/python -m eval.deep_chain_safe_build_benchmark finalize --output-dir /tmp/new-contract-regression
.venv/bin/python -m eval.deep_chain_safe_build_benchmark verify --output-dir /tmp/new-contract-regression

# checked-in証跡を現在の実装で検証・比較再計算する（native再build不要）。
.venv/bin/python -m eval.deep_chain_safe_build_benchmark verify --output-dir docs/benchmarks/puyo-232-safe-build-contract
.venv/bin/python docs/benchmarks/puyo-232-safe-build-contract/compare.py
sha256sum -c docs/benchmarks/puyo-232-safe-build-contract/regression.sha256
```
