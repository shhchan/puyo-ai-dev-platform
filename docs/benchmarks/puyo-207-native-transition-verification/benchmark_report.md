# PUYO-207 independent transition Go/No-Go verification

- Decision: **NO_GO**
- Evaluated commit: `cff9ea5d0295087db8332c2a6efa59db74f886ef`
- Source tree: `e8f73fcaaa868909e56fcc71eef6fd7a29f1dcb5`
- Wheel SHA-256: `c7214697031e553b5d6ae9047e3dff918a134069f76f9264e183801458db06cc`
- Transition corpus digest: `7132ac24b92c275560513f15e2a827fa491df89f6aa70770181ecfeac27d0eb2`
- Search corpus digest: `6db18dde8b18c3b71cca701313655ac7f45f87443c7776b0253fa35bcb8fb1b1`
- CPU affinity: `0` (one thread)
- CPU / platform: `Intel(R) Core(TM) Ultra 7 258V` / `Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.39`
- Compiler: `rustc 1.98.0 (88d9e12ae 2026-08-18)`
- Executed ISA path: `scalar`; reachable local-update and forced full-scanner results are identical
- Outliers: none removed; p50/p95 use nearest rank

## Independent semantic verification

| Gate | Coverage | Mismatches |
| --- | ---: | ---: |
| fixed fixtures | 9 | 0 |
| authoritative/Python frozen transitions | 11264 | 0 |
| native frozen transitions | 11264 | 0 |
| legal/reduced action results | 512 | 0 |
| optimized local path vs forced scanner property corpus | > 100,000 | 0 |

Deterministic response, exact-key, search result, and ranking digest mismatches are `0`.

## Locked outcome latency

| Outcome | Samples | Records/sample | p50 ns | p95 ns | Target |
| --- | ---: | ---: | ---: | ---: | ---: |
| mixed | 120 | 10000 | 51.170 | 77.119 | <= 100.0 |
| quiet | 40 | 4096 | 31.397 | 57.325 | <= 50.0 |
| one_chain | 40 | 4096 | 235.714 | 279.852 | - |
| multi_chain | 40 | 4096 | 304.238 | 381.803 | - |

The auxiliary first-call/warm measurements do not replace the locked PUYO-205 sample contract:

| Scope | Wall p50 us | Wall p95 us | Kernel p50 ns/transition | Kernel p95 ns/transition |
| --- | ---: | ---: | ---: | ---: |
| warm single | 24.384 | 33.509 | 83.000 | 138.000 |
| warm batch | 213459.667 | 252655.012 | 70.180 | 195.258 |

First single call: `155.114 us` wall / `950 ns` kernel.

## Memory and shared-state contract

| Item | Observed | Limit |
| --- | ---: | ---: |
| normal hot-path heap allocations | 0 | 0 |
| child state bytes | 80 | 80 |
| hot result bytes | 24 | 24 |
| total write bytes/transition | 104 | 104 |
| reusable state metadata bytes | 8 | 8 |

Process peak RSS was `161,608 KiB`; the selected three-slice local update measured `37.186 ns` p95 and `54` updated bytes per record.

## Combined budget and Amdahl result

| Category | p95 envelope | Per evaluated node |
| --- | ---: | ---: |
| measured transition projection | 46.272 ms | 77.119 ns |
| PUYO-201 evaluator/quiescence residual | 774.353 ms | 1290.589 ns |
| combined transition + evaluator | 820.625 ms | - |
| native total | 900.000 ms | - |
| end-to-end including adapter margin | 1000.000 ms | - |

The transition demonstrates `1782.682x` against the frozen Python transition reference. PUYO-201 must demonstrate at least approximately `1869.269x` against the frozen Python evaluator reference and then measure the real shared-boundary p95.

## Decision

**NO_GO for PUYO-201 implementation.** The binding evaluator/quiescence allowance is `774.353 ms` p95, or `1290.589 ns` per evaluated node.

PUYO-201 evaluator/quiescence does not exist yet; its implementation must measure transition plus evaluator at this shared native boundary.
Production backend promotion remains blocked. Stop before PUYO-202 if transition+evaluator exceeds 820.625 ms, native total exceeds 900 ms, end-to-end exceeds 1,000 ms, or any semantic/deterministic/allocation contract fails.

## Reproduction

```bash
./scripts/build_deep_chain_native.sh
python -m eval.deep_chain_native_transition_profile run --ticket PUYO-207 --corpus eval/deep_chain_native_transition_corpus.json --search-corpus eval/deep_chain_native_corpus.json --output-dir docs/benchmarks/puyo-207-native-transition-verification --mixed-samples 120 --outcome-samples 40 --stage-samples 30 --alternative-samples 40 --warmup 5 --mixed-batch-size 10000 --outcome-batch-size 4096 --cachegrind-records 256 --cachegrind-repeats 512 --cpu 0
python -m eval.deep_chain_native_transition_profile verify --ticket PUYO-207 --artifact-dir docs/benchmarks/puyo-207-native-transition-verification
```
