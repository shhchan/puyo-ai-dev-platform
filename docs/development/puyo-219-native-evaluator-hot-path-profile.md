# PUYO-219 native evaluator hot-path profile

## Outcome

PUYO-219 is **PROFILE_COMPLETE_OPTIMIZATION_REQUIRED**. The source-bound
transition-plus-evaluator p95 is 12,759.772 ms for exactly 600,000 operations,
against the unchanged 820.625 ms gate. The measurement contract, semantic
checks, stage attribution, counter distribution, and budget ledger all pass;
the performance gate itself remains intentionally unmet.

The canonical evidence is the
[benchmark manifest](../benchmarks/puyo-219-evaluator-hot-path/benchmark_manifest.json)
and its hash-bound artifacts.

## Fixed measurement boundary

- PUYO-201 evaluator implementation commit:
  `c66d3ab054626b4015c203c790d136deb9b44a26`
- PUYO-219 measurement commit:
  `27f130024b545111fb40bc8019f3ba5972935194`
- frozen corpus: 512 source states, 11,264 legal transitions, and the selected
  legal action for every source state
- release wheel:
  `puyo_deep_chain_native-0.3.0-cp312-cp312-manylinux_2_28_x86_64.whl`
- canonical loop: one evaluator thread, 10,000 warmup operations, five samples
  of exactly 600,000 operations, nearest-rank p95, and no outlier removal
- diagnostic stage profile: the same operation count and sample count, plus a
  100 microsecond user-space interval sampler on a separate observer thread

`perf` and Valgrind are not installed in this WSL2 environment. The QA-only
native boundary therefore publishes explicit stage markers to an interval
sampler and exact call counters. The normal evaluator path does not start a
thread, mutate a marker, or update profile counters.

## Stage decomposition

The sampler attributes 99.985% of the combined loop to named, non-overlapping
stages. Evaluator projections subtract the fixed PUYO-207 transition projection
of 46.271520 ms and allocate the remaining observed evaluator time by sampled
cycle share.

| Evaluator stage | Cycle share | ns/node | 600k projection | Stage budget |
| --- | ---: | ---: | ---: | ---: |
| placement enumeration / trigger qualification | 57.317% | 12,144.976 | 7,286.985 ms | 443.835 ms |
| virtual resolve / gravity | 21.987% | 4,658.808 | 2,795.285 ms | 170.255 ms |
| remaining structure scan | 8.496% | 1,800.213 | 1,080.128 ms | 65.788 ms |
| candidate ranking / canonical SHA-256 | 6.421% | 1,360.498 | 816.299 ms | 49.719 ms |
| base feature / component extraction | 5.780% | 1,224.673 | 734.804 ms | 44.755 ms |

The target ledger sums exactly to the 774.353480 ms evaluator envelope, or
1,290.589 ns/node. A proportional allocation requires an overall 16.418x
evaluator improvement; it is a diagnostic budget, not a production semantic
change.

The outer p95 deltas are 9.370 ms for native request parsing, parent
preparation, response construction, and FFI, plus 1.205 ms for Python request
encoding and response decoding. They are not the dominant bottleneck.

## Exact call counts

| Counter | p50/node | p95/node | max/node | Exact 600k total |
| --- | ---: | ---: | ---: | ---: |
| pattern nodes | 415 | 415 | 415 | 249,000,000 |
| resolution nodes | 18 | 25 | 30 | 10,444,966 |
| rank comparisons | 17 | 24 | 29 | 9,846,138 |
| rank ties | 1 | 10 | 13 | 1,028,918 |
| SHA-256 calls | 2 | 11 | 14 | 1,415,633 |

## Diagnostic depth A/B

`max_added_puyos=1/2/3` medians are 1,868.005 / 6,219.758 / 20,980.162
ns/node. The depth 2-to-3 increment is 70.354% of depth-3 time. This is
diagnostic evidence only: production remains `max_added_puyos=3`, and neither
the search meaning nor its node budgets changed.

## Optimization order and stop conditions

The measured order is:

1. PUYO-223: localize placement enumeration and trigger qualification around
   the frontier (57.317% evaluator share).
2. PUYO-220: make virtual resolution, gravity, and remaining-structure work
   incremental (30.483% combined evaluator share).
3. PUYO-222: re-profile the next bottleneck after the first improvements.
4. PUYO-221: run the independent 600,000-node gate only after the combined p95
   is at or below 820.625 ms.

Even making PUYO-223's stage free leaves 5,426.515 ms of evaluator work; making
PUYO-220's two stages free leaves 8,838.087 ms. No single current candidate can
meet the final evaluator envelope. Stop immediately on any fixture,
transition-oracle, Python/native evaluator, determinism, allocation, or
80/24-byte ABI regression.

## Verification

Run from the repository root with the source-bound release extension installed:

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
  tests.test_deep_chain_native_evaluator_profile
.venv/bin/python -m eval.deep_chain_native_evaluator_benchmark verify
.venv/bin/python -m eval.deep_chain_native_evaluator_profile verify
```

The expensive canonical measurement is reproduced with:

```bash
.venv/bin/python -m eval.deep_chain_native_evaluator_profile run
```
