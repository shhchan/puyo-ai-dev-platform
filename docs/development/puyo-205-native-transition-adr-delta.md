# PUYO-205 compact transition ADR delta

- Status: Accepted for PUYO-206 implementation and PUYO-207 verification
- Date: 2026-08-27
- Amends: [PUYO-198 deep-chain native boundary ADR](puyo-198-deep-chain-native-boundary.md)
- Inputs: PUYO-200 No-Go evidence and the frozen PUYO-198/PUYO-200 corpora
- Production impact: none; PUYO-201 and the production backend remain blocked

## Decision

Keep the exact three-bit-slice board representation, including the existing
drop heights and settled/lifecycle flags. PUYO-206 may redesign the reachable
transition path around fused placement and inserted-component updates, but it
must not migrate the stored state to six planes, column-local cells, or a
persisted full component cache.

Split the per-node result contract into an 80-byte caller-owned child state and
a 24-byte fixed-width hot result. The hot result contains the two `u64` score
deltas, vanished/garbage counts, action and landing IDs, chain count, and
lifecycle flags. The current 128-byte `TransitionSummary`, wire planes,
fingerprint, trace, and human-facing evidence are materialized only for a root,
selected/final representative, or an explicit QA request. Exact equality and
TT identity continue to use the complete three slices and lifecycle/search
coordinates, never the fingerprint alone.

Replace the PUYO-198 transition-only share as the downstream pass authority
with one combined transition plus evaluator/quiescence gate. The combined
budget is the unchanged sum of the original categories:

```text
10.596 ms + 810.029 ms = 820.625 ms p95 per decision
```

This does not change the native 900 ms budget, the 100 ms adapter margin, the
1,000 ms end-to-end p95 gate, or the 600,000 expanded-node ceiling. It changes
only the internal accounting authority because transition and evaluator will
share native state and derived board data.

PUYO-206 has two fixed component targets under the PUYO-205 measurement
contract:

- mixed p95 at or below **100.0 ns per transition**;
- quiet p95 at or below **50.0 ns per transition**.

At the confirmed 600,000-call ceiling, the mixed target consumes 60.000 ms and
leaves 760.625 ms, or 1,267.708 ns per evaluated node, for PUYO-201. PUYO-207
must measure the combined boundary and is the authority for the final Go/No-Go.
Meeting only the component targets does not unblock PUYO-201.

## Why the PUYO-198 component share is not authoritative

The 10.596 ms allocation was derived from the exclusive-time share of the
instrumented Python implementation. It was not a measured lower bound or an
architectural requirement for the native transition. PUYO-205 confirms that
the node conversion itself was correct: the fixed search corpus produced 396
expanded, generated, evaluated, and transition calls. Therefore the canonical
ceiling remains exactly 600,000 transition calls, not 600,000 calls per
scenario or another multiplier.

The new release profile measured mixed p95 at 80.633 ns, a 48.380 ms canonical
projection. Requiring 17.66 ns would still require a 4.566x transition-only
speedup. The largest quiet stage has an Amdahl upper bound of only 1.479x even
if made free. Preserving the old share as an isolated hard gate would therefore
reject a transition that occupies less than six percent of the native budget
while ignoring reusable evaluator work.

The combined 820.625 ms gate preserves every outer constraint and prevents the
accounting change from becoming a performance relaxation. PUYO-207 must stop
the native path if the combined implementation misses it.

## Evidence and measurement contract

The canonical artifact is
[`benchmark_manifest.json`](../benchmarks/puyo-205-native-compact-profile/benchmark_manifest.json),
with the readable result in
[`benchmark_report.md`](../benchmarks/puyo-205-native-compact-profile/benchmark_report.md).

| Item | Recorded value |
| --- | --- |
| Evaluated commit | `25bb530df58b3f2e46cdd57026b5fc67c418ff4b` |
| Release wheel SHA-256 | `4351e77a563629f2c6a41d5dae83c8c41b233d82657e7cd890ac243093a7b601` |
| Transition corpus digest | `7132ac24b92c275560513f15e2a827fa491df89f6aa70770181ecfeac27d0eb2` |
| Search corpus digest | `6db18dde8b18c3b71cca701313655ac7f45f87443c7776b0253fa35bcb8fb1b1` |
| CPU / platform | Intel Core Ultra 7 258V / Linux x86_64 WSL2 |
| Compiler | rustc 1.98.0 |
| Affinity / threads | CPU 0 / one thread |
| Warm-up | 5 calls per mode |
| Mixed samples | 120 batches of 10,000 transitions |
| Outcome samples | 40 batches of 4,096 transitions per class |
| Stage samples | 30 batches of 4,096 transitions per class and stage |
| Percentile | nearest rank, `sorted[ceil(p/100*N)-1]` |
| Outlier removal | none |

The release wheel recorded zero mismatches against the authoritative/Python
oracles and all 11,264 frozen native transitions. Every layout and result
alternative also produced zero profile-workload mismatches.

Hardware `perf_event_open` returned `ENOENT` for hardware events under this
WSL2 kernel, and the `perf` executable was unavailable. Cycle values therefore
use `RDTSC` serialized by `LFENCE` while pinned to one CPU. Instructions,
branches, and cache events use Valgrind 3.22.0 Cachegrind simulation. The
artifact labels those counters as simulated and retains the raw events; they
must not be presented as hardware PMU samples.

Reproduce from a clean checkout with Valgrind 3.22.0 on `PATH`:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_transition_profile run \
  --corpus eval/deep_chain_native_transition_corpus.json \
  --search-corpus eval/deep_chain_native_corpus.json \
  --output-dir docs/benchmarks/puyo-205-native-compact-profile \
  --mixed-samples 120 --outcome-samples 40 --stage-samples 30 \
  --alternative-samples 40 --warmup 5 \
  --mixed-batch-size 10000 --outcome-batch-size 4096 \
  --cachegrind-records 256 --cachegrind-repeats 512 --cpu 0
.venv/bin/python -m eval.deep_chain_native_transition_profile verify
```

## Outcome and stage profile

| Outcome | Samples | Records/sample | p50 ns | p95 ns | p95 cycles |
| --- | ---: | ---: | ---: | ---: | ---: |
| mixed | 120 | 10,000 | 67.419 | 80.633 | 265.281 |
| quiet | 40 | 4,096 | 47.634 | 54.305 | 177.916 |
| one-chain | 40 | 4,096 | 311.851 | 400.586 | 1,316.866 |
| multi-chain | 40 | 4,096 | 363.986 | 436.548 | 1,436.039 |

The non-overlapping semantic stages explain 92.656% of the baseline-adjusted
quiet p50 cycle estimate:

| Quiet stage | Adjusted cycles/transition | Cachegrind instructions/transition |
| --- | ---: | ---: |
| direct placement | 35.146 | 167.226 |
| color-plane extraction | 12.054 | 63.579 |
| inserted connectivity | 49.624 | 281.702 |
| state/result materialization | 44.096 | 67.209 |
| score/lifecycle | 1.146 | 3.187 |
| control residual | 11.260 | not isolated |

Inserted connectivity is the largest quiet factor by both cycles and simulated
instructions. State/result materialization is the next cycle factor. Chain scan
and gravity have zero invocations on quiet records; on one/multi-chain records,
materialization, scan, and gravity account for the additional cold-path cost.

## Layout and result alternatives

The layout workload copies one stored state, applies the same two-cell local
board update, and verifies the canonical placed board. It does not claim to be
an end-to-end evaluator benchmark.

| Layout | State bytes | Update bytes | Reusable metadata | p50 ns | p95 ns |
| --- | ---: | ---: | ---: | ---: | ---: |
| three-bit slices | 80 | 54 | 8 | 14.749 | 29.045 |
| six color planes | 128 | 38 | 102 | 13.682 | 24.558 |
| column-local cells | 72 | 22 | 6 | 9.644 | 27.178 |
| local component/trigger cache | 256 | 134 | 182 | 35.792 | 48.107 |

Column-local cells are fastest for this isolated update, but they discard the
plane operations needed by transition resolution and PUYO-201. Six planes add
60% state-copy volume for only a small local-update difference. The metadata
cache triples current state size and is slower. These results do not justify a
state migration before an evaluator exists, so the current three slices and
small existing metadata remain the accepted shared state.

| Result contract | Child state bytes | Result bytes | Total write bytes | p50 ns | p95 ns |
| --- | ---: | ---: | ---: | ---: | ---: |
| full `TransitionSummary` | embedded | 128 | 128 | 7.489 | 13.287 |
| minimal hot result | 80 | 24 | 104 | 7.541 | 15.163 |
| hot result plus metadata | 80 | 64 | 144 | 11.364 | 27.170 |

The minimal result is not selected as a microbenchmark latency win; it is
selected because it reduces per-node storage, makes QA-only fields impossible
to consume accidentally in the search loop, and permits lazy evidence without
adding the larger metadata sidecar. PUYO-206 must demonstrate the complete
fused path rather than claiming this isolated write measurement as its gain.

## Alternatives and Amdahl decision

| Option | Decision | Reason |
| --- | --- | --- |
| Keep the 17.66 ns transition-only gate | Reject | Requires 4.566x; the share came from instrumented Python, not native call cost |
| Redesign the reachable hot path | Adopt for PUYO-206 | Connectivity and materialization are the measured quiet factors |
| Fuse transition/evaluator accounting | Adopt as authority | Preserves the original 820.625 ms sum and values shared state work once |
| Stop native implementation | Conditional | Mandatory when any fixed target, combined gate, or semantic gate fails |

PUYO-206 and PUYO-207 must stop without starting PUYO-201 when any of the
following occurs:

1. mixed p95 exceeds 100.0 ns per transition;
2. quiet p95 exceeds 50.0 ns per transition;
3. transition plus evaluator p95 exceeds 820.625 ms under the measured call
   model;
4. native total exceeds 900 ms or end-to-end p95 exceeds 1,000 ms;
5. parity, determinism, exact-key, or allocation-free requirements fail.

The current PUYO-200 implementation remains No-Go for PUYO-201 because its
quiet p95 is 54.305 ns, above the newly fixed 50.0 ns target. PUYO-206 may not
change these targets, sample counts, warm-up, percentile rule, call conversion,
or locked outer gates. PUYO-207 performs the independent final judgment.

## Consequences

- PUYO-205 changes no production backend, search semantics, quality setting,
  action ID, state lifecycle, or fallback behavior.
- PUYO-206 owns only the selected three-slice local-update/hot-result redesign.
- PUYO-201 remains To Do and blocked through PUYO-207.
- PUYO-207 must reuse the checked-in runner and targets without post-hoc
  adjustment.
- A later real-Linux run may add hardware PMU evidence, but it cannot replace
  or silently reinterpret this accepted measurement contract.
