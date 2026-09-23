# PUYO-220 differential virtual resolution

## Outcome

PUYO-220 is **PASS**. The source-bound `virtual_resolve_gravity` plus
`remaining_structure_scan` stages fell from 3,130.497 ms to 231.108 ms for
exactly 600,000 evaluated nodes. This is a 13.546x speedup and a 92.618%
reduction. The fixed PUYO-219 allocation is 236.044 ms, leaving 4.935 ms of
measured margin.

The canonical evidence is the
[benchmark manifest](../benchmarks/puyo-220-incremental-resolution/benchmark_manifest.json)
and its hash-bound artifacts. It was measured from implementation commit
`ebe5acbbaa8f62616e9e8b36a8a3c271cb83fc8a` with a clean source tree and the
canonical manylinux wheel.

## Differential design

The exact resolver remains the semantic oracle. Production selects the
differential path only for compact boards without hidden cells or an existing
poppable component, and only when x86-64 BMI2 and POPCNT are available. Every
unsupported case explicitly calls the exact resolver.

The supported path localizes physical work as follows:

- base component extraction records each component mask, size, color,
  connection edges, frontier slots, and aggregate remaining structure once;
- trigger-frontier groups carry the exact connected components and anchor mask
  for each minimal placement;
- a one-chain surface removal whose columns remain compact is resolved directly
  from component metadata, without materializing or scanning a candidate board;
- repeated candidates with the same trigger group reuse a fixed-size,
  allocation-free resolution cache;
- other candidates seed the first vanish from the trigger group, clear adjacent
  ojama, and apply BMI2 lane compaction only to columns containing removed base
  cells;
- terminal component analysis detects a continuation and computes link/edge
  counts in one bit-parallel pass; when no continuation exists, link-2 and
  link-3 counts are derived from edge and degree-two counts without another
  flood;
- the fused terminal result carries remaining structure directly into ranking,
  so no separate remaining scan is entered.

Logical `resolution_nodes`, candidate ordering, truncation, ranking, and all
result fields remain unchanged. Stage profiling marks only candidates that
physically execute resolve/gravity; metadata-complete candidates still count as
logical resolution nodes.

## Canonical results

| Gate | Before | After | Target | Result |
| --- | ---: | ---: | ---: | --- |
| resolve + remaining, 600k | 3,130.497 ms | 231.108 ms | 236.044 ms | pass |
| resolve + remaining, per node | 5,217.494 ns | 385.181 ns | 393.406 ns | pass |
| combined p95, 600k | 4,961.052 ms | 3,244.640 ms | no regression | pass |
| stage reduction | - | 92.618% | at least 70% | pass |

Both sides use one evaluator thread, 10,000 warmup operations, five samples of
exactly 600,000 operations, nearest-rank p95, no outlier removal, and the same
frozen 512-state corpus and production config with `max_added_puyos=3`.

## Semantic and safety gates

- 8 fixed fixtures: 0 mismatches
- 11,264 frozen transitions: 0 transition/action mismatches
- 512 selected child evaluations: 0 result mismatches
- 256 incremental-versus-exact candidate comparisons: 0 mismatches
- property special cases: multiple chains, adjacent ojama, hidden row, and
  left/right asymmetry
- 512 additional bitset prefilter/remaining comparisons against exact scans
- fixture, transition, and child response SHA-256: byte-identical to PUYO-223
- logical pattern, executed-probe, resolution, ranking, and SHA counter
  distributions: unchanged
- deterministic repeated responses: 0 mismatches
- normal hot-path heap allocations: 0
- child/result ABI: 80/24 bytes

## Verification

Run from the repository root with the source-bound release wheel installed:

```bash
cargo fmt --manifest-path native/deep_chain_native/Cargo.toml -- --check
cargo clippy --locked --manifest-path native/deep_chain_native/Cargo.toml \
  --all-targets -- -D warnings
cargo test --locked --manifest-path native/deep_chain_native/Cargo.toml
.venv/bin/ruff check \
  eval/deep_chain_native_incremental_resolution.py \
  tests/test_deep_chain_native_incremental_resolution.py
.venv/bin/python -m unittest \
  tests.test_deep_chain_native_incremental_resolution
.venv/bin/python -m eval.deep_chain_native_incremental_resolution verify \
  --require-exact-wheel
```

Reproduce the canonical measurement with:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_incremental_resolution run
```
