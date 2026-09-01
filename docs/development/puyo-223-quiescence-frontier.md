# PUYO-223 quiescence frontier

## Outcome

PUYO-223 is **PASS**. The source-bound placement-enumeration and
trigger-qualification stage fell from 7,286.985 ms to 427.842 ms for exactly
600,000 evaluated nodes. This is a 17.032x speedup and a 94.129% reduction.
The fixed PUYO-219 stage budget is 443.835 ms, leaving 15.994 ms of measured
margin.

The canonical evidence is the
[benchmark manifest](../benchmarks/puyo-223-quiescence-frontier/benchmark_manifest.json)
and its hash-bound artifacts. It was measured from implementation commit
`9df1ea31265e6c6d7f4494d341de8e0e12717cbf` with a clean source tree and the
canonical manylinux wheel.

## Frontier design

The production search still exposes the original logical catalog of 83
column-multiset patterns in 43 mirror orbits. Logical pattern-node accounting,
orbit order, group-atomic truncation, `max_added_puyos=3`, and all result fields
remain unchanged.

Physical work is now localized as follows:

- component extraction records each component's size deficit and reachable
  three-cell stack frontier once;
- `ComponentSet` owns the compact stack-neighbor and component-adjacency
  topology consumed by quiescence, avoiding a second board scan;
- a single reachable component uses static slot-to-pattern masks;
- multiple same-color components use a bounded, allocation-free frontier
  expansion over only connected placement states;
- static subpattern/superset tables remove non-minimal triggers without
  re-enumerating the full catalog;
- the hot path resolves only qualifying candidates, and derives trigger
  anchors during the first existing resolve flood rather than with a duplicate
  component traversal.

The normal path remains allocation-free. The QA-only stage-profile schema is
v2 and adds an exact `executed_pattern_probes` counter; no production result or
80/24-byte child/result ABI changed.

## Canonical results

| Gate | Before | After | Target | Result |
| --- | ---: | ---: | ---: | --- |
| placement stage, 600k | 7,286.985 ms | 427.842 ms | 443.835 ms | pass |
| placement stage, per node | 12,144.976 ns | 713.070 ns | 739.726 ns | pass |
| executed probes, p95/node | n/a | 25 | <= 96 | pass |
| executed probes, max/node | n/a | 30 | <= 96 | pass |

Both sides use one evaluator thread, 10,000 warmup operations, five samples of
exactly 600,000 operations, nearest-rank p95, no outlier removal, and the same
frozen 512-state corpus and production config.

The overall transition-plus-evaluator p95 is still 4,961.052 ms, so the final
820.625 ms decision gate remains follow-up work for PUYO-220/PUYO-222. PUYO-223
meets its isolated stage budget without changing the production depth or
search meaning.

## Semantic and safety gates

- 8 fixed fixtures: 0 mismatches
- 11,264 frozen transitions: 0 transition/action mismatches
- 512 selected child evaluations: 0 result mismatches
- 132 hidden-row, ojama, unreachable-column, and deterministic random states,
  each checked under full and varied truncation budgets: 264 exhaustive-oracle
  comparisons, 0 mismatches
- fixture, transition, and child response SHA-256: byte-identical to PUYO-219
- logical pattern/resolution/ranking/SHA counter distributions: identical to
  PUYO-219
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
.venv/bin/python -m unittest \
  tests.test_chain_structure \
  tests.test_deep_chain_native \
  tests.test_deep_chain_native_transition \
  tests.test_deep_chain_native_evaluator \
  tests.test_deep_chain_native_evaluator_benchmark \
  tests.test_deep_chain_native_evaluator_profile \
  tests.test_deep_chain_native_quiescence_frontier
.venv/bin/python -m eval.deep_chain_native_quiescence_frontier verify
```

Reproduce the expensive canonical measurement with:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_quiescence_frontier run
```
