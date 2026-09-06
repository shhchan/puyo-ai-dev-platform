# PUYO-204 deep-chain native baseline 最終評価

## 目的

PUYO-203 までに統合した release native `deep_chain_builder` を、PUYO-189 と同じ
固定条件で60 run評価する。品質、性能、決定論、simulator parity、private future
isolation、GUIを独立gateとして判定し、全gateがPASSした場合だけ
`accepted_as_experimental_baseline=true` とする。

PUYO-189 の成果物は履歴証跡として変更しない。PUYO-204 は v2 schema を使い、
`docs/benchmarks/puyo-204-deep-chain-native-baseline/` だけへ出力する。

## 固定条件

| 項目 | 固定値 |
| --- | ---: |
| profile / backend | `reference` / `native` |
| depth / width / scenarios | 16 / 250 / 6 |
| maximum expanded nodes | 600,000 |
| target chain | 6 |
| seeds / repeats | 123–152 / 各2回 |
| placements | 各run 40 |
| expected runs | 60 |
| 平均最大実発火連鎖数 | 10以上 |
| premature fire / game over / parity / leak / fallback | すべて0 |
| repeat action / plan / trajectory digest | 一致 |
| one-decision p95 | 1.0秒以下 |

target chain 6 は探索の固定入力で、品質gateの10連鎖とは別の値である。実発火が
1〜9連鎖なら premature fire と数える。prediction-only chainとevaluator scoreは
実発火へ含めない。

## release build と preflight

追跡対象の変更がないcommitからrelease wheelをbuildする。runnerはwheel、ABI/schema、
CPython ABI、compiler、CPU feature、source revision、thread mode、config checksumを検査し、
dirty/debug/stale buildではcanonical実行を開始しない。

```bash
git status --short
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_builder_benchmark preflight \
  --backend native \
  --seed 123 \
  --timeout-seconds 5 \
  --output-dir docs/benchmarks/puyo-204-deep-chain-native-baseline
```

preflightはcanonical quality sampleではない。採用設定`scenario-6`のcold/warmと
`oracle-1`の1-thread診断を分離して保存し、action/plan/search digest、native search /
aggregation / serialization、boundary、materialization、Python flow、CPU、peak RSS、
expanded/evaluated node、TT hitを記録する。production long-horizon hot pathが出力しない
evaluator resolution cache hitは`null`とし、PUYO-221/227の独立profileを正本として明記する。

## canonical 60 runs

各runは完了直後にatomic renameで保存する。同じコマンドを再実行すると既存runを
上書きせず、未実行identityから再開する。

```bash
.venv/bin/python -m eval.deep_chain_builder_benchmark run \
  --backend native \
  --max-runs 1 \
  --output-dir docs/benchmarks/puyo-204-deep-chain-native-baseline
```

`--max-runs`を省略すると全pending identityを処理する。canonical searchはelapsed timeoutで
候補を変えず、600,000 node budgetをauthorityとする。

## GUI QA

focused automated contractを実行する。

```bash
.venv/bin/python -m unittest \
  tests.test_deep_chain_builder_benchmark \
  tests.test_deep_chain_builder \
  tests.test_deep_chain_builder_smoke \
  tests.test_deep_chain_search_backend \
  tests.test_deep_chain_native_search \
  tests.test_realtime_ai \
  tests.test_realtime_versus_ui \
  tests.test_realtime_arena \
  tests.test_launcher
```

dummy SDLではreference/native/target 6のevent loopとreplay contractを保存する。

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  .venv/bin/python -m eval.realtime_versus_ui \
  --policy-a deep_chain_builder \
  --policy-b random \
  --deep-chain-profile reference \
  --deep-chain-backend native \
  --deep-chain-target-chain 6 \
  --seed 187 \
  --speed 4 \
  --max-frames 240 \
  --result-json docs/benchmarks/puyo-204-deep-chain-native-baseline/gui_dummy_result.json \
  --replay docs/benchmarks/puyo-204-deep-chain-native-baseline/gui_dummy_replay.json
```

結果を記録する。通常windowの目視をしていない場合、`manual-status`は必ず`pending`にする。

```bash
.venv/bin/python -m eval.deep_chain_builder_benchmark record-gui-qa \
  --automated-passed \
  --automated-command "<exact unittest command>" \
  --dummy-result docs/benchmarks/puyo-204-deep-chain-native-baseline/gui_dummy_result.json \
  --dummy-replay docs/benchmarks/puyo-204-deep-chain-native-baseline/gui_dummy_replay.json \
  --manual-status pending \
  --notes "<automated/dummy result and remaining visual review>"
```

通常windowでは次を人間が確認する。

1. plan step 1のghostと選択actionが一致する。
2. selection reason、backend、target、node counter、flow timingが読める。
3. 3 decision以上が適用され、replanで古いghostが置換される。
4. `O`を往復してもaction/plan IDは変わらず、描画と操作が継続する。
5. replayのreference/native/fallback=false、action、planが画面と一致する。

## 集計と検証

```bash
.venv/bin/python -m eval.deep_chain_builder_benchmark finalize \
  --output-dir docs/benchmarks/puyo-204-deep-chain-native-baseline
.venv/bin/python -m eval.deep_chain_builder_benchmark verify \
  --output-dir docs/benchmarks/puyo-204-deep-chain-native-baseline
```

`benchmark_summary.json`はcoverage、native build、quality、performance、determinism、
scenario accounting、parity、future isolation、GUIを独立判定する。`historical_comparison.json`
はPUYO-189の300秒lower boundとnative p95を比較するが、PUYO-189に同等のhost provenanceが
ないためsame-host比率とは扱わない。

## 成果物

- `runs/seed-NNN-repeat-NN.json`: canonical raw run
- `preflight.json`: cold/warm/1-thread診断
- `run_index.json`: 60 identityと未完了理由
- `benchmark_summary.json`: machine-readable gate結果
- `build_provenance.json`: wheel/build/host/config証跡
- `future_isolation.json`: private sentinel監査
- `gui_dummy_result.json` / `gui_dummy_replay.json` / `gui_qa.json`: GUI証跡
- `historical_comparison.json`: PUYO-189比較
- `lineage.json` / `benchmark_manifest.json`: lineageとchecksum
- `benchmark_report.md`: 人間向け判定

この作業ではformal model version、Git tag、stable/champion promotionを行わない。FAIL時も
閾値やprofileを変更せず、failure taxonomyを人間が確認してから別タスクを判断する。
