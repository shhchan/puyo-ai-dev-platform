# PUYO-227 placement frontier / trigger qualification optimization

## Outcome

PUYO-227 is **PASS_GATE_MET**. The canonical
`placement_enumeration_trigger_qualification` cost fell from 2,740.955 to
732.945 cycles per node, a 73.260% reduction. Its 600,000-node projection fell
from 480.446 ms to 136.174 ms.

Combined transition-plus-evaluator p95 fell from 995.521 ms to 810.385 ms, an
18.597% reduction, and now passes the 820.625 ms final gate by 10.240 ms. The
acceptance rule permits either a 20% combined reduction or the absolute final
gate, so no follow-up optimization ticket is required.

The canonical evidence is the
[benchmark manifest](../benchmarks/puyo-227-placement-frontier-trigger-qualification/benchmark_manifest.json)
and its hash-bound artifacts. It was measured from commit
`4aa08f40df1eed20ca3a41b86424cb4d5f9bc6a7` with a clean source tree and the
canonical manylinux wheel (`4ab8104b089e87808e967dee0d2e8c781ed2b24e3f6012a7aa75a4bca95b41f6`).

## Frontier cache design

The evaluator preserves exact placement identities, component identities,
candidate ordering, logical budgets, and response encoding. The optimization
changes only board-derived work retained by the existing exact-key cache:

- the cached component analysis now also retains the valid placement-pattern
  mask and fully qualified `TriggerFrontier`;
- the cache key remains the complete `BoardKey` plus `max_added_puyos`, the
  complete input set for component extraction and frontier construction;
- cache misses construct the frontier once and retain its candidate masks,
  prequalified resolution groups, anchors, and exact cached resolutions;
- cache hits skip frontier traversal, trigger qualification, resolution-group
  deduplication, and their component work, while candidate dispatch, budgets,
  scoring, ranking, and SHA-256 tie breaking still execute for every call;
- build-only candidate-component records remain local scratch instead of being
  retained in each cache entry;
- compile-time bounds keep one cached analysis below 3 KiB, the full
  thread-local cache below 12 MiB, and the hot `TriggerFrontier` below 2 KiB.

The normal path remains allocation-free. No public result field, response
encoding, logical pattern/resolution/ranking counter, or 80/24-byte
child/result ABI changed.

## Canonical results

| Gate | PUYO-226 after | PUYO-227 after | Required | Result |
| --- | ---: | ---: | ---: | --- |
| placement stage cycles/node | 2,740.955 | 732.945 | at least 30% lower | pass |
| placement stage, 600k projection | 480.446 ms | 136.174 ms | informational | 71.657% lower |
| combined p95, 600k | 995.521 ms | 810.385 ms | at least 20% lower or <=820.625 ms | pass |
| combined final gate | 995.521 ms | 810.385 ms | <=820.625 ms | pass |
| attributed profile share | 99.868% | 99.844% | at least 95% | pass |

Both sides use one evaluator thread, 10,000 warmup operations, five samples of
exactly 600,000 operations, nearest-rank p95, no outlier removal, and the same
frozen 512-state corpus and production config with `max_added_puyos=3`.

## Placement substage ledger

| Substage | Projected 600k cost |
| --- | ---: |
| candidate dispatch | 122.874 ms |
| orbit enumeration | 5.695 ms |
| trigger qualification | 3.707 ms |
| multi-component frontier | 2.714 ms |
| single-component frontier | 0.803 ms |
| deduplication | 0.306 ms |
| frontier update | 0.076 ms |
| **Total** | **136.174 ms** |

The substage total exactly reconstructs the canonical placement aggregate.
Across 600,000 operations, frontier-state visits fell from 19,467,680 to
154,686 and qualified candidates fell from 10,444,966 to 89,054. The logical
pattern, executed-probe, resolution, ranking, tie, and SHA-256 counters remain
unchanged because cached work is still dispatched under the current call's
budgets.

## Semantic and safety gates

- 264 bounded-frontier comparisons against exhaustive search: 0 mismatches
- 83 placement patterns and 43 mirror orbits: exact catalog layout preserved
- 53,248 compact-board qualification comparisons against exact lane scans:
  0 mismatches
- 2,561 trigger-protection comparisons against exact cell scans: 0 mismatches
- 512 component-metadata comparisons against the exact component scanner:
  0 mismatches
- cold/hot exact-key frontier result and logical counters: identical
- 8 fixed fixtures: 0 mismatches
- 11,264 frozen transitions: 0 transition/action mismatches
- 512 selected child evaluations: 0 result mismatches
- fixture, transition, and child response SHA-256: byte-identical to PUYO-226
- logical pattern, executed-probe, resolution, ranking, tie, and SHA counter
  distributions: unchanged
- deterministic repeated responses: 0 mismatches
- normal hot-path heap allocations: 0
- child/result ABI: 80/24 bytes

## Residual Amdahl ledger

The final 820.625 ms combined gate is closed, so PUYO-227 does not require a
follow-up ticket. The largest independent unimproved stage is now
`candidate_ranking_sha256`, projected at 308.084 ms per 600,000 nodes. This is
recorded for attribution only; it is not a blocker for PUYO-227.

PUYO-227 unblocks the independent 600,000-node gate tracked by PUYO-221.

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
  eval/deep_chain_native_placement_frontier.py \
  tests/test_deep_chain_native_placement_frontier.py
.venv/bin/python -m unittest \
  tests.test_deep_chain_native_placement_frontier
.venv/bin/python -m eval.deep_chain_native_base_features verify \
  --historical
.venv/bin/python -m eval.deep_chain_native_placement_frontier verify \
  --require-exact-wheel
```

Reproduce the canonical measurement with:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_placement_frontier run
```
