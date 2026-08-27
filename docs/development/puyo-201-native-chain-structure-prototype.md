# PUYO-201 native chain-structure prototype

## Outcome

The bounded PUYO-201 transition-plus-evaluator prototype is
**NO_GO_CLOSE_PR_UNMERGED**. It preserves the Python evaluator contract but
misses all three unchanged latency envelopes. Per the PUYO-213 risk-acceptance
decision, the implementation must not merge, PUYO-202 must remain To Do, and
further work requires a new reviewed decision.

The canonical evidence is the
[benchmark manifest](../benchmarks/puyo-201-native-chain-structure/benchmark_manifest.json)
and its hash-bound artifacts.

## Prototype boundary

The prototype adds an allocation-free Rust scalar path which consumes the
native compact transition state directly. It implements component extraction,
connection and shape features, count-bounded one-to-three-puyo quiescence,
virtual resolution, action deltas, and the configured score breakdown.

The production search backend is unchanged. Python is crossed only by a
QA-only binary batch boundary for differential evidence and by an exact-count
combined profiler. Candidate relations, component identities, and the final
evaluation digest are materialized only in that cold QA path.

Two Python details are reproduced exactly rather than approximated:

- the Python 3.12 compensated float summation used for the total score;
- the SHA-256 canonical candidate tie-break, evaluated with a fixed stack
  buffer and no heap allocation in the normal hot path.

The existing transition child-state and hot-result ABI remains 80 and 24
bytes. No evaluator cache, search routing, fallback, target rewrite, reduced
operation profile, or production promotion was added after the bounded gate
failed.

## Source-bound verification

The release measurement uses all 512 states and selected legal actions from
the frozen 11,264-transition PUYO-200 corpus. Five samples each execute exactly
600,000 combined transition-plus-evaluator operations. p95 is nearest-rank,
all observations are retained, and there is no timeout or fallback timing.

| Gate | Observed | Required | Result |
| --- | ---: | ---: | --- |
| combined operations per sample | 600,000 | exactly 600,000 | pass |
| transition + evaluator p95 | 13,973.529 ms | <= 820.625 ms | **fail** |
| native call total p95 | 13,983.520 ms | <= 900.000 ms | **fail** |
| end-to-end p95 | 13,985.087 ms | <= 1,000.000 ms | **fail** |
| PUYO-173 fixture mismatches | 0 / 8 | 0 | pass |
| frozen transition-oracle mismatches | 0 / 11,264 | 0 | pass |
| evaluator Python/native mismatches | 0 / 512 | 0 | pass |
| deterministic repeat/checksum mismatches | 0 | 0 | pass |
| normal combined hot-path heap allocations | 0 | 0 | pass |
| child state / hot result | 80 / 24 bytes | 80 / 24 bytes | pass |
| production backend promotion | false | prohibited | pass |

The combined p95 is approximately 17.0 times the allowed 820.625 ms envelope.
This is a binding failure even though every semantic and memory contract
passes.

## Verification

Run from the repository root with the release extension installed:

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
  tests.test_deep_chain_native_evaluator_benchmark
.venv/bin/python -m eval.deep_chain_native_evaluator_benchmark verify
```

The expensive canonical profile is reproduced with:

```bash
.venv/bin/python -m eval.deep_chain_native_evaluator_benchmark run
```
