# PUYO-202 native long-horizon search

## Decision

`run_compact_long_horizon_search` の decision-level search kernel を Rust へ
移植した。production builder の backend 選択は変更せず、PUYO-203 の範囲に
残す。

native boundary は、観測可能な compact state、current + NEXT2、設定、
decision seed だけを受け取る。hidden future queue や simulator の private
state は受け取らず、future queue は native 内で Python `random.Random` と
`PuyoSequence` の規約どおりに決定的に補完する。

## Search ownership

1 回の `decide` 呼び出しの中で、次を Python callback なしに完結させる。

- legal root expansion
- scenario ごとの layered beam search
- compact transition と chain-structure evaluator の直接呼び出し
- terminal fire の分類と score breakdown
- root survivor quota を先に確保する stable top-K prune
- full lifecycle/search identity を比較する transposition table
- scenario evidence の集約、root ranking、representative path の選択
- bounded result envelope の生成

hot node は fixed-width の state、2 個の evaluator scalar record、直前の
transition、最大 64 手の inline path を保持する。Python object、board tuple、
predicted board、JSON、可変長 path copy は保持しない。candidate/beam/TT は
width から上限を算出して scenario 開始時に確保し、各 layer で再利用する。

TT key は board だけではなく、all-clear pending、game-over、score、
last-chain-end score、root action、scenario ID、pair cursor、depth を含む。
open addressing の index hash が衝突しても、entry は key 全体を比較する。

## Deterministic parallel contract

`oracle-1` は単一 thread の意味論 oracle である。`scenario-6` は process 内で
再利用する 6 worker の専用 pool を使い、global/Rayon pool は使わない。

各 worker は scenario を独立に先行計算する。coordinator は sample 順にだけ
結果を確定し、global expanded-node budget を加算する。先行結果が残予算を
越える場合、その scenario を残予算ちょうどの oracle 順序で再実行し、後続
結果は確定しない。このため thread 完了順は action、evidence、counter、
truncation、digest に影響しない。worker panic は boundary 内で捕捉し、部分的
success は返さない。

## Survivor tie-break v2

`puyo.long_horizon_survivor_tie_break.v2` は、同点 survivor の state 比較を
`sha256(state.to_bytes())` から canonical `state.to_bytes()` の辞書順へ変更する。
これは hot loop の per-node digest を除くための versioned change である。

frozen corpus の ablation では root decision、ranking、root evidence は全件
一致する。representative-only path は同点時に変わり得るため、Python oracle
と native の両方を v2 に揃え、差分結果を benchmark artifact に保存する。
external evidence の state fingerprint は従来どおり SHA-256 であり、fire
evidence の意味論は変えない。

## Result boundary

既存の v1 envelope に予約済みの 6 section を使用する。

- decision: selected action、ranked roots、complete/budget flags、semantic digest
- counters: 9 個の意味論 counter と arena/TT/worker/timing telemetry
- root evidence: `(root action, scenario)` tracker matrix
- representatives: path、compact state、最終 evaluator evidence
- diagnostics: native 生成 scenario queue と root evaluator evidence
- provenance: crate/source/compiler/profile/target、thread mode/count、SIMD path

Python の `materialize_native_long_horizon_result` は、future queue を Python
authority でも再生成して一致を検証し、root/scenario evidence を既存
dataclass へ復元する。その後、既存 aggregation と diagnostics を再計算し、
native の root ranking と selected action が一致しない response を拒否する。
詳細 component/candidate object は root と最終 representative だけで復元し、
search node ごとの FFI/materialization は行わない。

## Verification

自動検証は次を固定する。

- frozen corpus の Python/native full-result differential
- `oracle-1` / `scenario-6` の action、record、counter、digest 一致
- 30 decision seed の future isolation と同一 seed の repeatability
- TT on/off differential、full-key omission、forced hash collision
- scenario 境界をまたぐ 300-node exact global budget と deterministic rerun
- 実 decision 中の GIL release
- reusable worker pool と bounded arena/TT/peak-live telemetry
- release wheel の depth 16 / width 250 / 6 scenario / max 600,000 node gate

performance gate は PUYO-198 で固定した native total p95 900 ms 以下、Python
materialization を含む end-to-end p95 1,000 ms 以下である。nearest-rank p95、
outlier removal なしの raw sample、source/wheel provenance、ablation を
`docs/benchmarks/puyo-202-native-long-horizon/` に保存する。

再現手順:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_search_benchmark run
.venv/bin/python -m eval.deep_chain_native_search_benchmark verify
```
