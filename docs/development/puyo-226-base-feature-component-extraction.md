# PUYO-226 base feature / component extraction optimization

## Outcome

PUYO-226 is **PASS_FOLLOW_UP_REQUIRED**. The canonical
`base_feature_component_extraction` cost fell from 2,050.376 to 458.042
cycles per node, a 77.661% reduction. Its 600,000-node projection fell from
358.406 ms to 80.287 ms.

Combined transition-plus-evaluator p95 fell from 1,256.651 ms to 995.521 ms,
a 20.780% reduction. Both PUYO-226 performance gates pass, but the combined
result remains 174.896 ms above the 820.625 ms final gate. PUYO-227 tracks the
next independent stage, `placement_enumeration_trigger_qualification`.

The canonical evidence is the
[benchmark manifest](../benchmarks/puyo-226-base-feature-component-extraction/benchmark_manifest.json)
and its hash-bound artifacts. It was measured from commit
`8daa8f6a6765756821b9d42b5bb523006c64a4e9` with a clean source tree and the
canonical manylinux wheel.

## Base analysis design

The evaluator keeps exact component identities, feature values, candidate
ordering, logical budgets, and response encoding. The optimization changes
only internal extraction and fixed workspace representation:

- the stage profile is split into board geometry, cache lookup, stack
  topology, component flood, metadata aggregation, frontier topology, and
  feature aggregation;
- settled boards without hidden-row occupancy derive global link, edge,
  isolated, and component counts with bit-parallel masks, then flood only
  components that touch the bounded placement frontier;
- bounded frontier components use a small fixed flood and packed topology
  seeds; x86 BMI2 extraction packs stack-frontier slots directly;
- lower-compact states reuse their stored drop geometry rather than scanning
  every occupied lane again;
- a 1,024-set, four-way, thread-local cache uses the complete `BoardKey` and
  `max_added_puyos` for exact equality. It retains only board-derived
  components and base features; lifecycle, action, quiescence, score, ranking,
  and response work is recomputed for every evaluation;
- evidence storage is selected at the type level so the normal path does not
  carry the 96-candidate evidence buffer;
- resolution and candidate-ranking scratch records remain fixed-size, with
  exact fallback when the compact precomputed resolution cache is exhausted.

The normal path remains allocation-free. No public result field, response
encoding, logical pattern/resolution/ranking counter, or 80/24-byte
child/result ABI changed.

## Canonical results

| Gate | PUYO-225 after | PUYO-226 after | Required | Result |
| --- | ---: | ---: | ---: | --- |
| base stage cycles/node | 2,050.376 | 458.042 | at least 30% lower | pass |
| base stage, 600k projection | 358.406 ms | 80.287 ms | informational | 77.599% lower |
| combined p95, 600k | 1,256.651 ms | 995.521 ms | at least 20% lower or <=820.625 ms | pass |
| combined final gate | 1,256.651 ms | 995.521 ms | <=820.625 ms | follow-up required |
| attributed profile share | 99.913% | 99.868% | at least 95% | pass |

Both sides use one evaluator thread, 10,000 warmup operations, five samples of
exactly 600,000 operations, nearest-rank p95, no outlier removal, and the same
frozen 512-state corpus and production config with `max_added_puyos=3`.

## Base substage ledger

| Substage | Projected 600k cost |
| --- | ---: |
| feature aggregation | 41.422 ms |
| exact-key cache lookup | 29.679 ms |
| board geometry | 7.638 ms |
| stack topology | 0.740 ms |
| frontier topology | 0.572 ms |
| component metadata aggregation | 0.135 ms |
| component flood | 0.101 ms |
| **Total** | **80.287 ms** |

The substage total exactly reconstructs the canonical base aggregate. After
warmup, stack extraction is entered for 0.9765% of nodes; all hits still
recompute evaluation-dependent features and ranking.

## Semantic and safety gates

- 512 bit-parallel metadata comparisons against the exact component scanner:
  0 mismatches
- 264 bounded-frontier comparisons against exhaustive search: 0 mismatches
- cold/hot exact-key cache results and profile work counters: identical
- largest ABI-valid canonical candidate encoding: within the fixed buffer
- fixed hot workspaces: below the 4 KiB stack-probe boundary
- 8 fixed fixtures: 0 mismatches
- 11,264 frozen transitions: 0 transition/action mismatches
- 512 selected child evaluations: 0 result mismatches
- fixture, transition, and child response SHA-256: byte-identical to PUYO-225
- logical pattern, executed-probe, resolution, ranking, tie, and SHA counter
  distributions: unchanged
- deterministic repeated responses: 0 mismatches
- normal hot-path heap allocations: 0
- child/result ABI: 80/24 bytes

## Residual Amdahl ledger

The largest independent unimproved stage is
`placement_enumeration_trigger_qualification`, projected at 480.446 ms per
600,000 nodes. Removing that stage completely would leave 515.075 ms combined
p95, so it can independently close the remaining 174.896 ms gap.

PUYO-226 blocks PUYO-227, and PUYO-227 blocks the independent 600,000-node gate
in PUYO-221. PUYO-227 remains unstarted in Sprint 9.

## Verification

Run from the repository root with the source-bound release wheel installed:

```bash
cargo fmt --manifest-path native/deep_chain_native/Cargo.toml -- --check
cargo clippy --release --locked \
  --manifest-path native/deep_chain_native/Cargo.toml \
  --all-targets -- -D warnings
cargo test --release --locked \
  --manifest-path native/deep_chain_native/Cargo.toml
.venv/bin/ruff check \
  agents/deep_chain_native_evaluator.py \
  eval/deep_chain_native_evaluator_profile.py \
  eval/deep_chain_native_base_features.py \
  tests/test_deep_chain_native_evaluator.py \
  tests/test_deep_chain_native_base_features.py
.venv/bin/python -m unittest \
  tests.test_deep_chain_native_evaluator \
  tests.test_deep_chain_native_base_features
.venv/bin/python -m eval.deep_chain_native_placement_enumeration verify \
  --historical
.venv/bin/python -m eval.deep_chain_native_base_features verify \
  --require-exact-wheel
```

Reproduce the canonical measurement with:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_base_features run
```
