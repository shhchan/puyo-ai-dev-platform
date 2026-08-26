# PUYO-205 compact transition profile

- Evaluated commit: `25bb530df58b3f2e46cdd57026b5fc67c418ff4b`
- Wheel SHA-256: `4351e77a563629f2c6a41d5dae83c8c41b233d82657e7cd890ac243093a7b601`
- Corpus digest: `7132ac24b92c275560513f15e2a827fa491df89f6aa70770181ecfeac27d0eb2`
- CPU affinity: `0` (one thread)
- Outliers: none removed; p50/p95 use nearest rank

## Outcome latency

| Outcome | Samples | Records/sample | p50 ns | p95 ns | p95 cycles |
| --- | ---: | ---: | ---: | ---: | ---: |
| mixed | 120 | 10000 | 67.419 | 80.633 | 265.281 |
| quiet | 40 | 4096 | 47.634 | 54.305 | 177.916 |
| one_chain | 40 | 4096 | 311.851 | 400.586 | 1316.866 |
| multi_chain | 40 | 4096 | 363.986 | 436.548 | 1436.039 |

## Quiet-path decomposition

| Stage | p50 baseline-adjusted cycles/transition |
| --- | ---: |
| direct_placement | 35.146 |
| color_plane_extraction | 12.054 |
| inserted_connectivity | 49.624 |
| state_result_materialization | 44.096 |
| chain_scan | 0.000 |
| gravity | 0.000 |
| score_lifecycle | 1.146 |

The semantic stages explain `92.656%` of the quiet transition loop estimate. `inserted_connectivity` is largest by cycles and `inserted_connectivity` is largest by Cachegrind-simulated instructions.

Hardware PMU counters are unavailable in this WSL2 kernel (`perf_event_open` returns `ENOENT`). Cycles use RDTSC serialized with LFENCE; instructions, branches, and cache events use Valgrind Cachegrind and are labelled as simulated.

## Call-count model

The fixed search corpus measured `396` transition calls for `396` expanded nodes. The 600,000-node ceiling therefore remains a 600,000-call transition ceiling; it is neither relaxed nor multiplied by scenarios.

## ADR decision

Adopt **hot-path redesign plus transition/evaluator fusion budget**. PUYO-206 must meet `100.0 ns` mixed and `50.0 ns` quiet p95. PUYO-207 must then enforce the unchanged transition+evaluator combined budget of `820.625 ms`, the native 900 ms budget, and the end-to-end 1,000 ms gate.

The selected representation remains the exact three-bit slices with existing height/settled metadata. A 24-byte hot result is written beside the caller-owned child state; full QA summaries and traces are materialized lazily.

## Reproduction

```bash
./scripts/build_deep_chain_native.sh
python -m eval.deep_chain_native_transition_profile run --corpus eval/deep_chain_native_transition_corpus.json --search-corpus eval/deep_chain_native_corpus.json --output-dir docs/benchmarks/puyo-205-native-compact-profile --mixed-samples 120 --outcome-samples 40 --stage-samples 30 --alternative-samples 40 --warmup 5 --mixed-batch-size 10000 --outcome-batch-size 4096 --cachegrind-records 256 --cachegrind-repeats 512 --cpu 0
python -m eval.deep_chain_native_transition_profile verify
```
