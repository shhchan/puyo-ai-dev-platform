# PUYO-222 next evaluator bottleneck

## Outcome

PUYO-222 is **PASS_FOLLOW_UP_REQUIRED**. After PUYO-223 and PUYO-220, the
largest unimproved evaluator stage was
`base_feature_component_extraction`. Its sampled cost fell from 5,415.460 to
2,225.326 cycles per node, a 58.908% reduction. The corresponding 600,000-node
projection fell from 988.506 ms to 385.953 ms.

Combined transition-plus-evaluator p95 fell from 3,244.640 ms to 1,918.176 ms,
a 40.882% reduction. This passes the required 20% combined improvement but
remains 1,097.551 ms above the 820.625 ms final gate. PUYO-224 tracks the next
unimproved stage, `candidate_ranking_sha256`.

The canonical evidence is the
[benchmark manifest](../benchmarks/puyo-222-next-bottleneck/benchmark_manifest.json)
and its hash-bound artifacts. It was measured from commit
`d17a3f9883a9a050257d113fc6110845c1ae5f24` with a clean source tree and the
canonical manylinux wheel.

## Component metadata design

The evaluator still performs exact color-component extraction and keeps the
existing frontier, virtual-resolution, ranking, budget, and fallback behavior.
Only repeated base component aggregation was removed:

- production no longer materializes the full 84-entry component array; the
  retained array exists only in tests as the exact aggregation oracle;
- each extracted component updates link-2, link-3, connection-edge, isolated,
  reachable-ignition, and growth-site aggregates once;
- reachable landing extensions use a compact six-column mask instead of a
  board-width mask;
- per-color seen-once/seen-multiple masks derive connection candidates during
  extraction, eliminating the later landing × color × component scan;
- `build_features` consumes the aggregate metadata directly instead of
  rescanning every component for each feature family.

The normal path remains allocation-free. No public schema, result field,
logical node counter, ranking prefix, canonical SHA-256 ordering, or 80/24-byte
child/result ABI changed.

## Canonical results

| Gate | Before | After | Required | Result |
| --- | ---: | ---: | ---: | --- |
| base stage cycles/node | 5,415.460 | 2,225.326 | at least 30% lower | pass |
| base stage, 600k projection | 988.506 ms | 385.953 ms | informational | 60.956% lower |
| combined p95, 600k | 3,244.640 ms | 1,918.176 ms | at least 20% lower or <=820.625 ms | pass |
| combined final gate | 3,244.640 ms | 1,918.176 ms | <=820.625 ms | follow-up required |
| attributed profile share | 99.967% | 99.959% | at least 95% | pass |

Both sides use one evaluator thread, 10,000 warmup operations, five samples of
exactly 600,000 operations, nearest-rank p95, no outlier removal, and the same
frozen 512-state corpus and production config with `max_added_puyos=3`.

## Semantic and safety gates

- 8 fixed fixtures: 0 mismatches
- 11,264 frozen transitions: 0 transition/action mismatches
- 512 selected child evaluations: 0 result mismatches
- 512 component-metadata property states: 0 aggregate mismatches against the
  retained exact component scans
- fixture, transition, and child response SHA-256: byte-identical to PUYO-220
- logical pattern, executed-probe, resolution, ranking, tie, and SHA counter
  distributions: unchanged
- deterministic repeated responses: 0 mismatches
- normal hot-path heap allocations: 0
- child/result ABI: 80/24 bytes

## Residual Amdahl ledger

The next unimproved stage is `candidate_ranking_sha256`, projected at 672.355
ms per 600,000 nodes. Even removing that stage completely would leave a
1,245.821 ms combined p95, so PUYO-224 is the next independent improvement in
the bounded optimization loop rather than a claim that one change alone will
meet the final gate.

PUYO-222 blocks PUYO-224, and PUYO-224 blocks the independent 600,000-node gate
in PUYO-221. PUYO-224 remains unstarted in the backlog.

## Verification

Run from the repository root with the source-bound release wheel installed:

```bash
cargo fmt --manifest-path native/deep_chain_native/Cargo.toml -- --check
cargo clippy --locked --manifest-path native/deep_chain_native/Cargo.toml \
  --all-targets -- -D warnings
cargo test --locked --manifest-path native/deep_chain_native/Cargo.toml
.venv/bin/ruff check \
  eval/deep_chain_native_next_bottleneck.py \
  tests/test_deep_chain_native_next_bottleneck.py
.venv/bin/python -m unittest \
  tests.test_deep_chain_native_next_bottleneck
.venv/bin/python -m eval.deep_chain_native_incremental_resolution verify \
  --historical
.venv/bin/python -m eval.deep_chain_native_next_bottleneck verify \
  --require-exact-wheel
```

Reproduce the canonical measurement with:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_next_bottleneck run
```
