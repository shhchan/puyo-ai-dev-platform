# PUYO-200 native compact transition kernel

- Status: implementation/parity complete; PUYO-207 final gate is **No-Go**
- Parent design: [PUYO-198 native boundary ADR](puyo-198-deep-chain-native-boundary.md)
- Foundation: [PUYO-199 native extension contract](puyo-199-native-extension-contract.md)
- Native crate: `native/deep_chain_native` 0.3.0
- QA adapter: `agents.deep_chain_native_transition.NativeCompactBatchClient`

## Outcome and stop condition

The native kernel implements the complete compact transition contract and is
differential-identical on the nine locked fixtures and the frozen 11,264
state/action triples. It also preserves legal-action IDs, equal-pair symmetry
reduction, deterministic response bytes, hidden rows, OJAMA, lifecycle state,
and checked score behavior.

The PUYO-198 allocation is 10.596 ms at 600,000 expanded nodes, or 17.66 ns
per node. The checked-in release-wheel benchmark does not meet that allocation.
On the recorded Intel Core Ultra 7 258V / WSL2 run, the 10,000-record batch
measured 78.048 ns p50 and 130.345 ns p95 per transition, projecting to
78.207 ms at 600,000 nodes (7.38 times the allocation). The outcome profile
measured quiet transitions at 65.278 ns p95, one-chain transitions at
595.730 ns, and multi-chain transitions at 1,246.500 ns. The release wheel is
bound to source commit `183721c54d61c7247cc36b0eef28c4cb6149e7c8` and SHA-256
`fe5e0f723d4931095052dc06c3bc8a475b5b434c6d1299ed27cc2c5b6f04db53`.
The authoritative result and outcome-class profile are in
[`benchmark_report.md`](../benchmarks/puyo-200-native-compact-transition/benchmark_report.md)
and
[`internal_profile.json`](../benchmarks/puyo-200-native-compact-transition/internal_profile.json).
PUYO-198 requires the native design to return to
ADR review when a component misses its allocation. Therefore PUYO-201 must not
start, and PUYO-203 must not hide this miss with fallback or policy-level
timing.

PUYO-205 completed that ADR review without changing this implementation's
No-Go status.  Its release-wheel reprofile measured mixed p95 at 80.633 ns and
quiet p95 at 54.305 ns.  The accepted
[PUYO-205 ADR delta](puyo-205-native-transition-adr-delta.md) replaces the
historical transition-only allocation with a fixed 100.0 ns mixed target, a
50.0 ns quiet target, and an 820.625 ms combined transition plus
evaluator/quiescence authority.  This kernel passes the mixed target but still
misses the quiet target, so PUYO-201 remains blocked through PUYO-206 and the
independent PUYO-207 decision.  The generated PUYO-205 evidence is in
[`benchmark_report.md`](../benchmarks/puyo-205-native-compact-profile/benchmark_report.md).

PUYO-206 then implemented the selected local-update and fixed hot-result
design. Its release-wheel run measured mixed p95 at 66.497 ns and quiet p95 at
33.904 ns, meeting both fixed component targets with all 11,264 frozen
transitions at mismatch zero. The implementation and evidence contract are in
[the PUYO-206 hot-path report](puyo-206-native-compact-hot-path.md). That result
was a component Go only.

PUYO-207 independently rebuilt the release wheel and measured 77.119 ns mixed
p95 and 57.325 ns quiet p95 under the unchanged contract. Semantic,
deterministic, allocation, ABI, and memory gates all passed, but the quiet p95
exceeded the fixed 50.0 ns stop condition. The final result is therefore
No-Go; PUYO-200 owns an unresolved performance gate and PUYO-201 remains
blocked. See the
[PUYO-207 verification report](puyo-207-native-transition-verification.md).

## Internal representation

The wire contract remains the exact 87-byte `CSK1` payload owned by
`CompactSearchState`: six non-overlapping 84-bit planes, lifecycle flags, and
two little-endian `u64` scores. No Python schema or action ID changes.

Inside Rust, the board is encoded as three color bit-slices. Each of the six
columns occupies one 16-bit lane; rows 0 through 13 occupy the lower 14 bits of
that lane. Color IDs 1 through 6 are represented by the three slices and zero
means empty. This reduces the native state to a fixed 80-byte value while
retaining a one-to-one representation of all six planes.

The state caches only the six drop heights and two proven invariants:

- `lower_compact`: rows 0 through 12 contain no hole in a column;
- `settled`: the board contains no already-poppable normal-color group.

Wire parsing derives and validates both. A reachable settled state uses a fast
first-chain check restricted to components touching the two newly placed
puyos. An arbitrary or non-compact input takes the complete scanner, so the
optimization does not narrow accepted semantics.

Normal transitions use only fixed-size values and perform zero heap
allocations. Chain traces allocate only when the QA caller explicitly requests
diagnostics. The native search/evaluator added by later tickets is expected to
call `transition_hot_into` directly with an 80-byte caller-owned child state and
a versioned 24-byte result; it must not cross into Python per node. The
128-byte `TransitionSummary`, wire planes, fingerprints, and traces remain
lazy QA/detail values.

## Locked transition semantics

- Action IDs 0 through 21 use the existing `puyo_env.actions` ordering and are
  returned unchanged.
- Placement legality and equal-color outcome reduction preserve hidden-row
  edge cases rather than assuming rotations are always equivalent.
- Gravity compacts rows 0 through 12 independently and leaves row 13 static,
  matching the authoritative simulator.
- Only rows 0 through 11 can vanish. Adjacent visible OJAMA is removed but is
  not counted in the scoring base.
- Chain, connection, and color bonuses use the existing tables. Bonus is at
  least one.
- A pending all-clear bonus is consumed by the first chain step; a newly empty
  board arms the next bonus. `last_chain_end_score` and attack-score delta use
  the existing lifecycle.
- Occupancy at `(2, 11)` after resolution sets game over.
- Every score/counter operation that can exceed its fixed width is checked.
  Overflow returns the typed `ARITHMETIC_OVERFLOW` frame with the failing batch
  record index; it never wraps or poisons the extension process.

## Identity and collision policy

`BoardKey` stores the complete three-slice board, including both hidden rows
and OJAMA. `SearchStateKey` additionally requires lifecycle values plus root
action, scenario ID, pair cursor, and depth. A 128-bit deterministic board
fingerprint is emitted for diagnostics, but it is never sufficient for state
equality: exact slices are compared after any fingerprint lookup. A collision
therefore cannot merge different search states.

## QA-only binary boundary

`_compact_transition_batch(bytes) -> bytes` is intentionally underscore
prefixed and bounded. It exists for differential tests and component evidence,
not as a production node API. The GIL is detached for the whole Rust call.

The `PCTB` request contains ABI/schema version, flags, a bounded record count,
and fixed 90-byte records (`CSK1` state, two color IDs, action ID). `PCTS`
success records contain the canonical child state and scalar summary. Legal
actions, symmetry-reduced actions, placement planes, per-chain boards/masks,
and parse/kernel/encode timing are materialized only under explicit QA flags.
`PCTE` errors distinguish invalid input, arithmetic overflow, internal panic,
and resource exhaustion.

Without timing flags, the complete response is byte deterministic. Kernel
timing repeats the direct fixed-size transition and excludes request parsing,
Python objects, binary result encoding, and trace construction. Wall timing
continues to include those QA costs.

## Verification

The frozen corpus contains 512 reachable states generated from seeds 123
through 186 for eight turns, with every legal action retained (11,264 triples).
Generation and verification replay both the authoritative headless simulator
and the Python compact oracle. The corpus digest is
`7132ac24b92c275560513f15e2a827fa491df89f6aa70770181ecfeac27d0eb2`.

Run the focused checks from a CPython 3.12 environment:

```bash
cargo fmt --manifest-path native/deep_chain_native/Cargo.toml -- --check
cargo clippy --locked --manifest-path native/deep_chain_native/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path native/deep_chain_native/Cargo.toml
./scripts/build_deep_chain_native.sh
.venv/bin/python -m unittest tests.test_deep_chain_native_transition tests.test_deep_chain_native_transition_benchmark
.venv/bin/python -m eval.deep_chain_native_transition_benchmark verify
```

The final `verify` command intentionally exits non-zero while the performance
gate is a No-Go; it still verifies every artifact digest before reporting that
single failed check.

For a release-only Rust component profile:

```bash
cargo test --release --manifest-path native/deep_chain_native/Cargo.toml profile_quiet_transition_components -- --ignored --nocapture
```

## Attribution

The poppable-group connectivity identity is adapted from Ama at commit
`dea210bcd92965ae08fbc311f23565b0fab6dbbb`. Ama is MIT licensed; its copyright
and permission notice are retained in `native/deep_chain_native/LICENSE-AMA-MIT`
and described in `NOTICE`. The three-bit-slice representation, transition and
lifecycle implementation, codecs, fingerprint policy, and QA/evidence tooling
are original project code under this crate's MIT license.
