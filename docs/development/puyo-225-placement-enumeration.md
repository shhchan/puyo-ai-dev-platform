# PUYO-225 placement enumeration optimization

## Outcome

PUYO-225 is **PASS_FOLLOW_UP_REQUIRED**. The canonical
`placement_enumeration_trigger_qualification` cost fell from 3,745.145 to
2,545.633 cycles per node, a 32.028% reduction. Its 600,000-node projection
fell from 669.844 ms to 444.978 ms.

Combined transition-plus-evaluator p95 fell from 1,591.629 ms to 1,256.651 ms,
a 21.046% reduction. Both PUYO-225 performance gates pass, but the combined
result remains 436.026 ms above the 820.625 ms final gate. PUYO-226 tracks the
next independent stage, `base_feature_component_extraction`.

The canonical evidence is the
[benchmark manifest](../benchmarks/puyo-225-placement-enumeration/benchmark_manifest.json)
and its hash-bound artifacts. It was measured from commit
`a2f2cf6ef8f59df25d6a6112bebbc48bf65b2fa9` with a clean source tree and the
canonical manylinux wheel.

## Placement frontier design

The evaluator keeps the exact 83 placement patterns, 43 mirror orbits,
production depth three, logical node budgets, candidate ordering, and full
canonical SHA-256 tie break. The optimization changes only their internal
representation and dispatch:

- placement profiling is split into orbit enumeration, single- and
  multi-component frontier search, qualification, deduplication, dispatch,
  and frontier bookkeeping;
- component frontier metadata and catalog transitions use packed fixed-width
  topology, slot, and transition records;
- single-component triggers use precomputed slot-to-pattern unions, while the
  bounded multi-component search retains its exact frontier oracle;
- trigger candidates are recorded compactly and qualified through fixed-size
  resolution-spec groups instead of dense per-color metadata matrices;
- incremental surface results, trigger protection, and rank prefixes are
  reused per resolution group;
- candidate groups carry packed resolution IDs in canonical orbit/color order,
  and candidates that lose the rank prefix do not materialize the larger
  precomputed resolution payload;
- x86 BMI1/BMI2/POPCNT support is detected once and reused by component and
  frontier paths.

The normal path remains allocation-free. No public result field, response
encoding, logical pattern/resolution/ranking counter, or 80/24-byte
child/result ABI changed.

## Canonical results

| Gate | PUYO-224 after | PUYO-225 after | Required | Result |
| --- | ---: | ---: | ---: | --- |
| placement stage cycles/node | 3,745.145 | 2,545.633 | at least 30% lower | pass |
| placement stage, 600k projection | 669.844 ms | 444.978 ms | informational | 33.570% lower |
| combined p95, 600k | 1,591.629 ms | 1,256.651 ms | at least 20% lower or <=820.625 ms | pass |
| combined final gate | 1,591.629 ms | 1,256.651 ms | <=820.625 ms | follow-up required |
| attributed profile share | 99.900% | 99.913% | at least 95% | pass |

Both sides use one evaluator thread, 10,000 warmup operations, five samples of
exactly 600,000 operations, nearest-rank p95, no outlier removal, and the same
frozen 512-state corpus and production config with `max_added_puyos=3`.

## Placement substage ledger

| Substage | Projected 600k cost |
| --- | ---: |
| multi-component frontier | 149.706 ms |
| trigger qualification | 116.539 ms |
| candidate dispatch | 104.889 ms |
| single-component frontier | 40.945 ms |
| orbit enumeration | 19.833 ms |
| deduplication | 11.078 ms |
| frontier bookkeeping | 1.987 ms |
| **Total** | **444.978 ms** |

The substage total exactly reconstructs the canonical placement aggregate.

## Semantic and safety gates

- 264 full/bounded frontier comparisons against exact placement enumeration:
  0 mismatches
- 83 placement patterns and 43 mirror orbits: unchanged
- 53,248 compact-board comparisons and 2,561 trigger-protection comparisons:
  0 mismatches
- 8 fixed fixtures: 0 mismatches
- 11,264 frozen transitions: 0 transition/action mismatches
- 512 selected child evaluations: 0 result mismatches
- fixture, transition, and child response SHA-256: byte-identical to PUYO-224
- logical pattern, executed-probe, resolution, ranking, tie, and SHA counter
  distributions: unchanged
- deterministic repeated responses: 0 mismatches
- normal hot-path heap allocations: 0
- child/result ABI: 80/24 bytes

## Residual Amdahl ledger

The largest independent unimproved stage is
`base_feature_component_extraction`, projected at 358.406 ms per 600,000
nodes. Even removing that stage completely would leave 898.244 ms combined
p95, so PUYO-226 is the next bounded optimization rather than a claim that one
change alone will meet the final gate.

PUYO-225 blocks PUYO-226, and PUYO-226 blocks the independent 600,000-node gate
in PUYO-221. PUYO-226 remains unstarted in Sprint 9.

## Verification

Run from the repository root with the source-bound release wheel installed:

```bash
cargo fmt --manifest-path native/deep_chain_native/Cargo.toml -- --check
cargo clippy --locked --manifest-path native/deep_chain_native/Cargo.toml \
  --all-targets -- -D warnings
cargo test --release --locked \
  --manifest-path native/deep_chain_native/Cargo.toml
.venv/bin/ruff check \
  eval/deep_chain_native_placement_enumeration.py \
  tests/test_deep_chain_native_placement_enumeration.py
.venv/bin/python -m unittest \
  tests.test_deep_chain_native_placement_enumeration
.venv/bin/python -m eval.deep_chain_native_candidate_ranking verify --historical
.venv/bin/python -m eval.deep_chain_native_placement_enumeration verify \
  --require-exact-wheel
```

Reproduce the canonical measurement with:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_placement_enumeration run
```
