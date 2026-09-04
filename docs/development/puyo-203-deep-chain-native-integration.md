# PUYO-203 deep-chain native backend integration

## Decision

`deep_chain_builder` の long-horizon search に `python` / `native` / `auto`
backend を導入した。既存 caller の既定値は `python` のままとし、native への切替は
CLI または統合 launcher で明示する。canonical `reference` benchmark は
`native` の明示指定を必須とする。

backend routing の versioned 設定は
`train/config/deep_chain_backend.yaml`、探索量と evaluator の既存設定は
`train/config/deep_chain_builder.yaml` と `train/config/v1_7_chain_structure.yaml`
が authority である。reference の `depth=16`、`width=250`、`scenarios=6`、
`max_expanded_nodes=600000`、minimum chain 6、evaluator weight は変更しない。
minimum chain 6 は PUYO-204 canonical benchmark の固定値であり、後述する UI の
実験用目標連鎖とは別に管理する。

## Runtime contract

`RunLongRangeSearchStep` は共通の `LongHorizonBackendRequest` を 1 回だけ backend
へ渡し、どちらの実装も既存 `LongHorizonSearchResult` を返す。

- `python`: 従来の `run_compact_long_horizon_search` を呼ぶ互換経路。
- `native`: 1 decision につき native `decide` を 1 回だけ呼ぶ。release build、
  wire schema、ABI、CPython ABI、GIL detach、scenario-6 capability を起動時に検証する。
- `auto`: smoke 診断専用。native unavailable / incompatible / resource failure などを
  diagnostics に残して Python へ戻せる。`reference` では fallback せず fail closed。

native result は bounded binary record から既存 result 型へ lossless に復元する。
全 root の score/evidence は保持する一方、predicted board を含む N-turn plan は選択 root
だけ materialize する。action、root ranking、scenario evidence、counter、plan step、
state fingerprint の既存 schema は維持する。

policy diagnostics、decision trace、selection evidence、plan の `search_control.backend`
には次を記録する。

- requested / resolved backend と fallback 理由
- search/backend config version と SHA-256
- native crate、source revision、compiler、release profile、target、thread mode/count
- native search / aggregation / serialization、FFI boundary、Python materialization、total timing
- expanded/generated/pruned/TT hit、worker/pool/arena/TT telemetry、result record count

realtime executor の `spawn` では extension module 自体を pickle せず、child process 内で
strict adapter を再生成する。native call は PUYO-202 の GIL-detach contract を使い、executor
shutdown は pending future を cancel して child process を有界時間で終了できる。

## Build and strict validation

canonical 用 wheel は追跡対象の worktree が clean な commit であることを確認してから
release build する。build script は Linux x86_64 / CPython 3.12 を検証し、wheel を
`.venv` に再 install する。

```bash
git status --short
./scripts/build_deep_chain_native.sh
.venv/bin/python - <<'PY'
from agents.deep_chain_native import NativeDeepChainBackend

capabilities = NativeDeepChainBackend(canonical=True).capabilities.to_dict()
assert capabilities["build_profile"] == "release"
assert capabilities["gil_detach"] is True
assert "scenario-6" in capabilities["thread_modes"]
print(capabilities)
PY
```

module 不在、debug build、schema/ABI/Python ABI mismatch は `backend=native` の policy
生成時に例外となる。canonical run はその状態で停止し、Python decision や deterministic
policy fallback に読み替えない。

## Selecting a backend

CLI の smoke:

```bash
.venv/bin/python -m eval.deep_chain_builder_smoke \
  --seed 203 \
  --turns 3 \
  --repeats 2 \
  --profile smoke \
  --backend native \
  --ticket PUYO-203 \
  --output /tmp/puyo-203-native-smoke.json
```

realtime GUI / replay:

```bash
.venv/bin/python -m eval.realtime_versus_ui \
  --policy-a deep_chain_builder \
  --policy-b random \
  --deep-chain-profile smoke \
  --deep-chain-backend native \
  --seed 203 \
  --speed 0.25 \
  --result-json /tmp/puyo-203-gui-result.json \
  --replay /tmp/puyo-203-gui-replay.json
```

realtime arena:

```bash
.venv/bin/python -m eval.realtime_arena \
  --policy-a deep_chain_builder \
  --policy-b random \
  --deep-chain-profile smoke \
  --deep-chain-backend native \
  --seed 203
```

`python3 main.py` では「対戦」または「観戦」を開き、policy を
`deep_chain_builder`、`deep-chain profile` を `reference`、`deep-chain backend` を
`native` に設定する。短時間 integration acceptance の HUD は `reference/native` と
なる。軽量な操作確認だけなら profile を `smoke` にできる。`auto` が Python へ戻った
場合は `smoke/python!` と表示する。

## Experimenting with a larger target chain

launcher の `deep-chain 目標連鎖` は `6 / 8 / 10 / 12` から選べる。これは探索の
`minimum_chain_count` に直接入り、設定値未満の安全時発火を `premature_fire`、設定値以上を
`target_fire` として評価する。たとえば 10 を選ぶと 7 連鎖で妥協せず、10 連鎖以上になる候補を
優先して組み続ける。探索で到達可能な候補や盤面状況に依存するため、設定値への到達自体を保証する
hard limit ではない。値を上げるほど発火が遅れ、窒息するリスクも上がる。

大連鎖の目視比較には `deep-chain profile=reference`、`deep-chain backend=native`、
`deep-chain 目標連鎖=10` または `12` を使う。対戦画面の HUD は次を同時表示する。

- `aim cN`: UI で選んだ目標連鎖数
- `plan cN`: 現 decision で選択した探索 trajectory の最大予測連鎖数
- `actual cN`: その試合で実際に発火した最大連鎖数

CLI でも同じ実験ができる。

```bash
.venv/bin/python -m eval.realtime_versus_ui \
  --policy-a deep_chain_builder \
  --policy-b random \
  --deep-chain-profile reference \
  --deep-chain-backend native \
  --deep-chain-target-chain 10 \
  --seed 187
```

実装時の再現確認では、同一の seed 187、40 placements、`reference/native` で次の結果になった。

| 目標 | 実発火 | 最大実連鎖 | 最大予測連鎖 | game over |
| ---: | --- | ---: | ---: | --- |
| 6 | 28 手目（turn index 27）: 6 連鎖 | 6 | 9 | false |
| 10 | 36 手目（turn index 35）: 12 連鎖 | 12 | 13 | false |
| 12 | 34 手目（turn index 33）: 12 連鎖 | 12 | 13 | false |

これは UI 制御が実際の選択に効くことを確かめる非 canonical 実験であり、一般 seed に対する品質保証や
PUYO-204 の合否証跡には使わない。PUYO-204 の `run` / `preflight` コマンドは UI 設定を読まず、
目標 6 を明示的に固定する。canonical run artifact が 6 以外なら loader が拒否する。また、固定済みの
`train/config/deep_chain_builder.yaml` はこの機能で変更していない。

canonical benchmark は backend の省略を受け付けない。

```bash
.venv/bin/python -m eval.deep_chain_builder_benchmark preflight \
  --backend native \
  --timeout-seconds 5 \
  --output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline

.venv/bin/python -m eval.deep_chain_builder_benchmark run \
  --backend native \
  --max-runs 1 \
  --output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline
```

## Rollback

通常実行を即時に従来実装へ戻す場合は、同じ profile のまま backend だけを
`python` に変える。

```bash
.venv/bin/python -m eval.realtime_versus_ui \
  --policy-a deep_chain_builder \
  --deep-chain-profile smoke \
  --deep-chain-backend python
```

launcher でも `deep-chain backend=python` に戻せる。`auto` は smoke の可用性診断用であり、
canonical rollback には使用しない。canonical native failure 時は benchmark を停止し、wheel
を修復・再 build してから同じ `--backend native` command を再実行する。

## Verification

自動検証:

```bash
.venv/bin/python -m unittest \
  tests.test_deep_chain_search_backend \
  tests.test_deep_chain_builder \
  tests.test_deep_chain_builder_benchmark \
  tests.test_deep_chain_builder_smoke \
  tests.test_deep_chain_native_search \
  tests.test_realtime_ai \
  tests.test_realtime_versus_ui \
  tests.test_realtime_arena \
  tests.test_launcher
```

`tests.test_deep_chain_search_backend` は one-call adapter、strict/auto fallback、
固定6-seed corpusでの Python/native action/ranking/counter/plan/trace differential、
30 contiguous seeds に対する
private future sentinel isolation、spawned realtime decision、pending native reference decision
の cancel を検証する。native extension がない環境では real-native case は skip されるため、
canonical CI/QA では先に release wheel を build する。

dummy SDL の画面 loop と JSON schema:

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  .venv/bin/python -m eval.realtime_versus_ui \
  --policy-a deep_chain_builder \
  --policy-b random \
  --deep-chain-profile smoke \
  --deep-chain-backend native \
  --seed 203 \
  --speed 4 \
  --max-frames 240 \
  --result-json /tmp/puyo-203-dummy-result.json \
  --replay /tmp/puyo-203-dummy-replay.json
```

通常ウィンドウの短時間 integration acceptance では `reference/native` を選び、次を
人間が確認する。

1. launcher で `reference/native` を選べ、開始直後も main loop が応答する。
2. `thinking` 中も描画・pause・終了操作が止まらない。
3. 3回以上の decision が盤面へ適用され、少なくとも1回の replan が発生する。
4. decision 後の HUD が `reference/native`、candidate/scenario/node、selection reason、
   `aim` / `plan` / `actual` chain、plan ID、replan reason、flow timing を表示する。
5. ghost step 1 が action と一致し、step 2 以降、未知 tsumo、replan 後の置換が正しい。
6. overlay toggle を往復しても操作・描画が継続する。
7. replay の backend provenance/timing/counter、profile=reference、fallback=false と
   画面の plan/action が一致する。
8. `deep-chain backend=python` に戻した同じ seed/profile でも action/plan contract が保たれる。
9. `deep-chain 目標連鎖` を 6 から 10 または 12 へ変えると HUD の `aim` が一致し、実発火時の
   `actual` と chain animation が一致する。

dummy SDL は描画内容の色・可読性を保証しないため、最終 visual review は通常ウィンドウで
実施する。timeout/cancel では未完了の native result を採用せず、future を cancel して
worker を終了するため、partial result や stale plan は表示しない。

clean release build で取得した自動検証、固定 differential、reference preflight、native smoke、
simulator parity、dummy GUI/replay の要約は
[PUYO-203 QA summary](../benchmarks/puyo-203-deep-chain-native-integration/qa_summary.json)
に記録する。通常ウィンドウの目視項目だけは `pending_human_visual_review` として自動検証から
明確に分離している。

## Related work

- [PUYO-198 native boundary](puyo-198-deep-chain-native-boundary.md)
- [PUYO-199 extension contract](puyo-199-native-extension-contract.md)
- [PUYO-202 native long-horizon search](puyo-202-native-long-horizon-search.md)
- [PUYO-189 baseline benchmark](puyo-189-deep-chain-builder-baseline.md)
