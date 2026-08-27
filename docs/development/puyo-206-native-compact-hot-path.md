# PUYO-206 native compact transition hot path

- Status: component implementation complete; PUYO-207 final decision is **No-Go**
- Decision source: [PUYO-205 compact transition ADR delta](puyo-205-native-transition-adr-delta.md)
- Parent boundary: [PUYO-198 deep-chain native boundary ADR](puyo-198-deep-chain-native-boundary.md)
- Native crate: `native/deep_chain_native` 0.3.0
- Production impact: none; PUYO-201 remains blocked after PUYO-207

## Outcome

The reachable settled-state transition now writes one 80-byte caller-owned
child state and one 24-byte fixed hot result. Placement updates only the two
inserted cells, their affected height entries, and lifecycle flags. The first
vanish check extracts only the inserted colors and explores only components
touching either inserted cell. Full-board scanning, gravity, and score
lifecycle remain cold work and run only when the placement fires or when an
arbitrary input cannot use the reachable-state invariants.

The frozen release-wheel run meets both PUYO-205 targets with no outlier
removal:

| Outcome | Samples | Records/sample | p50 ns | p95 ns | Target |
| --- | ---: | ---: | ---: | ---: | ---: |
| mixed | 120 | 10,000 | 42.111 | 66.497 | <= 100.0 ns |
| quiet | 40 | 4,096 | 26.603 | 33.904 | <= 50.0 ns |
| one-chain | 40 | 4,096 | 197.130 | 235.780 | diagnostic |
| multi-chain | 40 | 4,096 | 249.249 | 315.128 | diagnostic |

The same run compared all 11,264 frozen transitions with the Python oracle and
reported zero state, scalar, and action mismatches. The Rust property workload
also compares more than 100,000 reachable local transitions with a forced
full-scanner path, including every normal-color pair and action, with exact
child-state and hot-result equality.

This was a PUYO-206 component Go only. It did not start or unblock PUYO-201.
PUYO-207 independently rebuilt and remeasured the release artifact at 77.119 ns
mixed p95 and 57.325 ns quiet p95. The mixed target passed, but the quiet target
failed, so the final decision is No-Go and the native line stops before
PUYO-201. See the
[PUYO-207 verification report](puyo-207-native-transition-verification.md).

## Fixed native contract

`CompactState` remains `repr(C, align(16))`, exactly 80 bytes, and stores the
complete native lifecycle state:

| Offset | Type | Ownership |
| ---: | --- | --- |
| 0 | three `u128` color bit slices | exact board, including OJAMA and hidden rows |
| 48 | six `u8` drop heights | locally updated placement metadata |
| 54 | `lower_compact: bool` | placement-path invariant |
| 55 | `settled: bool` | no pre-existing poppable group |
| 56 | `all_clear_bonus_pending: bool` | persistent score lifecycle |
| 57 | `game_over: bool` | persistent terminal lifecycle |
| 58 | six padding bytes | C layout/alignment only |
| 64 | `score: u64` | checked cumulative score |
| 72 | `last_chain_end_score: u64` | checked attack-score lifecycle |

The versioned hot result schema is
`puyo.native_compact_hot_result.v1`, ABI version 1:

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u64` | score delta |
| 8 | `u64` | attack-score delta |
| 16 | `u16` | vanished count |
| 18 | `u16` | garbage-cleared count |
| 20 | `u8` | unchanged action ID |
| 21 | `u8` | landing `axis_y`, or 255 when invalid |
| 22 | `u8` | chain count |
| 23 | `u8` | valid, game-over, all-clear-achieved, and bonus-consumed flags |

Compile-time size assertions reject any drift from 80 or 24 bytes. The
capability envelope publishes ABI version, schema, both sizes, and the `0x0f`
flag mask in optional TLV tag `0x0102`. Older envelope readers may skip the
new optional section; the 0.3.0 Python adapter requires it and fails closed on
any mismatch. The extension also exports matching module constants for the
bounded compact QA adapter.

The hot result deliberately does not own the child state, board planes,
fingerprint, complete occupancy, component masks, trace, or human-readable
evidence. Persistent lifecycle values live only in the child state. This
prevents stale duplication and fixes a 104-byte total per-transition write
contract for the future in-crate search/evaluator loop.

## Local update and invalidation rules

`transition_hot_into` accepts caller-provided `MaybeUninit` slots and
initializes both slots on every successful call, including invalid placement
results. Its reachable fast path is:

1. Copy the parent once into the caller-owned child value.
2. Add the two puyos directly to the three exact bit slices.
3. Increment only the one or two touched drop-height entries and recompute the
   center-column game-over bit.
4. Extract only the axis and child color planes from the parent and add the two
   inserted bits.
5. Flood only components rooted at the inserted bits. Three fixed expansions
   reject components smaller than four; complete convergence occurs only for
   a pop candidate. Equal-color inserted cells share one checked mask.
6. On a quiet result, write the child and fixed result immediately. No full
   board scan or QA materialization occurs.

The locality proof depends on both `lower_compact` and `settled`. Wire parsing
derives those invariants from the complete input. A non-compact or unsettled
input goes through the cold general placement and full scanner. A chain result
rebuilds heights from the settled post-gravity bit slices before it can become
a parent. No component/trigger cache persists across transitions, so there is
no separate cache invalidation protocol or enlarged transposition identity.

All scalar lifecycle behavior is unchanged: visible-only vanish, adjacent
OJAMA removal, chain/connection/color bonus tables, pending and newly armed
all-clear bonuses, `last_chain_end_score`, attack score, hidden rows, and the
center game-over cell. Checked arithmetic still returns a typed overflow
instead of wrapping.

## Hot/cold and QA separation

The normal `TRACE=false` specialization contains no heap allocation, Python
API, JSON, SHA-256, wire-plane conversion, fingerprint, trace vector, or
128-byte detailed summary. The allocation probe exercises the actual
`transition_hot_into` call and records zero allocations.

Chain resolution and arbitrary-input handling are marked cold. The existing
`TransitionSummary` and optional placement/chain evidence are derived lazily
from the child and fixed result through the `TRACE=true` QA specialization.
Tests require that this detailed result is exactly equal to the hot result
materialized after the same transition. `_compact_transition_batch` continues
to return the established QA wire schema; only its kernel timer now measures
the primary 80/24-byte path.

Exact equality and transposition identity still use all three board slices,
lifecycle state, and required search coordinates. The 128-bit fingerprint
remains diagnostic/lookup evidence and never establishes equality by itself.
No search depth, beam width, scenario count, action layout, or quality setting
changed in PUYO-206.

## Evidence and reproduction

The authoritative artifact is
[`benchmark_manifest.json`](../benchmarks/puyo-206-native-compact-hot-path/benchmark_manifest.json),
with the readable result in
[`benchmark_report.md`](../benchmarks/puyo-206-native-compact-hot-path/benchmark_report.md).

| Item | Recorded value |
| --- | --- |
| Evaluated commit | `a015bbf3f0e03c7ec0afb747e5df389ec668d6a9` |
| Release wheel SHA-256 | `e1b837f932463906b13ce86d12d3187f1c115d2e9c7a2dbf1c089a672ffa1938` |
| Transition corpus digest | `7132ac24b92c275560513f15e2a827fa491df89f6aa70770181ecfeac27d0eb2` |
| CPU / affinity / threads | Intel Core Ultra 7 258V / CPU 0 / one thread |
| Compiler | rustc 1.98.0 |
| Counter sources | serialized RDTSC cycles; Valgrind 3.22.0 Cachegrind simulation |
| Percentile / outliers | nearest rank / none removed |

The source manifest retains raw samples, alternative layouts/results,
Cachegrind events, the frozen call-count proof, environment, command, wheel
hash, and SHA-256 for every artifact. Verify the checked-in result without
rerunning it:

```bash
.venv/bin/python -m eval.deep_chain_native_transition_profile verify \
  --ticket PUYO-206 \
  --artifact-dir docs/benchmarks/puyo-206-native-compact-hot-path
```

Reproduce it from a clean checkout with Valgrind 3.22.0 on `PATH`:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_transition_profile run \
  --ticket PUYO-206 --cpu 0
.venv/bin/python -m eval.deep_chain_native_transition_profile verify \
  --ticket PUYO-206
```

The runner fixes one CPU and one thread, performs five warm-up calls per mode,
uses 120 mixed samples and 40 outcome samples, removes no observations, and
fails the command if either fixed p95 target, parity, ABI, size, call-count, or
outer-budget check fails.
