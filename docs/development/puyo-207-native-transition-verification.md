# PUYO-207 independent native transition verification

- Status: **No-Go**
- Successor closure: PUYO-213 **NO_GO_STOP**
- Decision date: 2026-08-27
- Evaluated commit: `cff9ea5d0295087db8332c2a6efa59db74f886ef`
- Source tree: `e8f73fcaaa868909e56fcc71eef6fd7a29f1dcb5`
- Release wheel SHA-256: `c7214697031e553b5d6ae9047e3dff918a134069f76f9264e183801458db06cc`
- Decision source: [PUYO-205 ADR delta](puyo-205-native-transition-adr-delta.md)
- Implementation source: [PUYO-206 hot-path report](puyo-206-native-compact-hot-path.md)
- Canonical artifact: [benchmark manifest](../benchmarks/puyo-207-native-transition-verification/benchmark_manifest.json)

## Decision

Do not start PUYO-201. The independently rebuilt release wheel passes the
mixed component target and every semantic, deterministic, allocation, ABI,
and memory contract, but its locked quiet p95 is `57.325 ns`, above the fixed
`50.0 ns` target. The PUYO-205 stop condition applies even though the measured
mixed projection leaves mathematical room inside the combined budget.

This is a transition verification No-Go, not a production rollback. The native
backend was never promoted. PUYO-200 returns to In Progress as the owner of the
unresolved transition gate, PUYO-201 remains To Do and blocked, and PUYO-202
through PUYO-204 must not use this result to continue the native line.

PR #102 was already merged before this verification, so it cannot be returned
to Draft. Its code remains isolated from production routing; the merged commit
and this No-Go artifact preserve the audit trail without rewriting history.

## Locked semantic and determinism result

| Gate | Coverage | Mismatches |
| --- | ---: | ---: |
| Fixed fixtures | 9 | 0 |
| Authoritative/Python frozen transitions | 11,264 | 0 |
| Native frozen transitions | 11,264 | 0 |
| Legal and symmetry-reduced action results | 512 states | 0 |
| Reachable local path vs forced full scanner | more than 100,000 transitions | 0 |
| Response bytes, exact key, search/ranking digest | repeated run | 0 |

The source-bound release tests also prove zero normal hot-path heap
allocations, exact child-state/hot-result equivalence with detailed materialized
evidence, and complete-state search-key comparison. The executable ISA path is
`scalar`; the optimized path in this decision is the reachable local-update
algorithm, compared against the forced general scalar scanner.

## Locked performance result

The run reused PUYO-205's exact sample counts, warm-up, nearest-rank
percentile, CPU affinity, one-thread policy, and no-outlier-removal rule.

| Outcome | Samples | Records/sample | p50 ns | p95 ns | Target | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| mixed | 120 | 10,000 | 51.170 | 77.119 | <= 100.0 | PASS |
| quiet | 40 | 4,096 | 31.397 | 57.325 | <= 50.0 | **FAIL** |
| one-chain | 40 | 4,096 | 235.714 | 279.852 | diagnostic | recorded |
| multi-chain | 40 | 4,096 | 304.238 | 381.803 | diagnostic | recorded |

No sample was removed. The quiet sample set ranged from `25.310 ns` to
`109.039 ns`; nearest-rank p95 is the 38th ordered observation and remains the
authority. The failure is therefore not replaced by PUYO-206's earlier
component-pass measurement.

The quiet p50 stage profile still identifies inserted connectivity as the
largest semantic stage. Cachegrind retains raw simulated instruction,
branch, and cache events; LFENCE-serialized RDTSC remains the cycle source
because hardware PMU events are unavailable under this WSL2 kernel.

## Memory and state contract

| Item | Observed | Fixed limit |
| --- | ---: | ---: |
| Normal hot-path heap allocations | 0 | 0 |
| Child state | 80 bytes | 80 bytes |
| Hot result | 24 bytes | 24 bytes |
| Total write per transition | 104 bytes | 104 bytes |
| Reusable state metadata | 8 bytes | 8 bytes |
| Process peak RSS | 161,608 KiB | observational |

The selected three-slice layout update measured `37.186 ns` p95 and 54 updated
bytes per record. A local full-component cache remains rejected; PUYO-201 is
not authorized to enlarge or reinterpret the shared state.

## Combined budget and Amdahl diagnostic

The combined arithmetic is retained as a diagnostic only because the
component stop condition failed:

| Category | p95 envelope | Per evaluated node |
| --- | ---: | ---: |
| Measured transition projection | 46.272 ms | 77.119 ns |
| Hypothetical evaluator/quiescence residual | 774.353 ms | 1,290.589 ns |
| Transition + evaluator authority | 820.625 ms | - |
| Native total | 900.000 ms | - |
| End-to-end with adapter margin | 1,000.000 ms | - |

The transition is `1,782.682x` faster than the frozen Python transition
reference. Meeting the hypothetical residual would require approximately
`1,869.269x` against the frozen Python evaluator reference. That headroom does
not override the quiet target and is not an allocation granted to PUYO-201.

## Measurement integrity

The manifest verifies the evaluated commit/tree, wheel hash, measurement
contract digest, both frozen corpus hashes, raw samples, Cachegrind output,
semantic verification, memory evidence, and the machine-readable No-Go
decision. `verify` treats a consistent No-Go as an integral artifact while the
`run` command exits non-zero for the failed performance gate.

A preliminary run was rejected before decision because the newly added
cold/warm auxiliary probe preceded the locked profile. Commit `cff9ea5` moves
that probe into an isolated child process after the authoritative profile, so
it cannot heat or perturb the locked sequence. Only the corrected run is
checked in.

Reproduce on Linux x86_64 with CPython 3.12 and Valgrind 3.22.0 on `PATH`:

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_transition_profile run \
  --ticket PUYO-207 --cpu 0
.venv/bin/python -m eval.deep_chain_native_transition_profile verify \
  --ticket PUYO-207
```

PUYO-211 subsequently repeated the identical wheel in three fresh processes,
calibrated three same-wheel process pairs, re-profiled the quiet path, and
compared the strongest safe micro-optimization candidate. The baseline did not
reproduce the fixed mixed/quiet gates, and the candidate could not create the
required margin, so PUYO-211 selected **no implementation candidate**. PUYO-212
must remain unstarted unless a controlled baseline or new evidence passes the
unchanged gates. See the
[PUYO-211 investigation](puyo-211-quiet-transition-investigation.md).

PUYO-207 itself performs no further optimization and does not reduce depth,
width, scenarios, or the node ceiling.

## PUYO-213 successor closure

PUYO-211 selected no implementation candidate, and PUYO-212 therefore closed
without candidate code or a PR. The integration range from PR #105's merge to
PR #106's merge contains no `native/deep_chain_native` change. PUYO-213 leaves
this `NO_GO` result and every fixed target intact and selects **NO_GO_STOP**.
PUYO-201 and PUYO-202 must not start on the current line. The final audit is in
[the PUYO-213 decision report](puyo-213-transition-restart-decision.md).
