# PUYO-206 compact transition hot-path acceptance

- Evaluated commit: `a015bbf3f0e03c7ec0afb747e5df389ec668d6a9`
- Wheel SHA-256: `e1b837f932463906b13ce86d12d3187f1c115d2e9c7a2dbf1c089a672ffa1938`
- Corpus digest: `7132ac24b92c275560513f15e2a827fa491df89f6aa70770181ecfeac27d0eb2`
- CPU affinity: `0` (one thread)
- Outliers: none removed; p50/p95 use nearest rank

## Outcome latency

| Outcome | Samples | Records/sample | p50 ns | p95 ns | p95 cycles |
| --- | ---: | ---: | ---: | ---: | ---: |
| mixed | 120 | 10000 | 42.111 | 66.497 | 219.239 |
| quiet | 40 | 4096 | 26.603 | 33.904 | 111.791 |
| one_chain | 40 | 4096 | 197.130 | 235.780 | 778.094 |
| multi_chain | 40 | 4096 | 249.249 | 315.128 | 1039.767 |

## Quiet-path decomposition

| Stage | p50 baseline-adjusted cycles/transition |
| --- | ---: |
| direct_placement | 27.899 |
| color_plane_extraction | 17.550 |
| inserted_connectivity | 47.512 |
| state_result_materialization | 10.374 |
| chain_scan | 0.000 |
| gravity | 0.000 |
| score_lifecycle | 1.135 |

The semantic stages explain `100.000%` of the quiet transition loop estimate. `inserted_connectivity` is largest by cycles and `inserted_connectivity` is largest by Cachegrind-simulated instructions.

Hardware PMU counters are unavailable in this WSL2 kernel (`perf_event_open` returns `ENOENT`). Cycles use RDTSC serialized with LFENCE; instructions, branches, and cache events use Valgrind Cachegrind and are labelled as simulated.

## Call-count model

The fixed search corpus measured `396` transition calls for `396` expanded nodes. The 600,000-node ceiling therefore remains a 600,000-call transition ceiling; it is neither relaxed nor multiplied by scenarios.

## Acceptance decision

The primary 80-byte child-state / 24-byte result hot path meets `100.0 ns` mixed and `50.0 ns` quiet p95. PUYO-207 must enforce the unchanged transition+evaluator combined budget of `820.625 ms`, the native 900 ms budget, and the end-to-end 1,000 ms gate.

The selected representation remains the exact three-bit slices with existing height/settled metadata. A 24-byte hot result is written beside the caller-owned child state; full QA summaries and traces are materialized lazily.

## Reproduction

```bash
./scripts/build_deep_chain_native.sh
python -m eval.deep_chain_native_transition_profile run --ticket PUYO-206 --corpus eval/deep_chain_native_transition_corpus.json --search-corpus eval/deep_chain_native_corpus.json --output-dir docs/benchmarks/puyo-206-native-compact-hot-path --mixed-samples 120 --outcome-samples 40 --stage-samples 30 --alternative-samples 40 --warmup 5 --mixed-batch-size 10000 --outcome-batch-size 4096 --cachegrind-records 256 --cachegrind-repeats 512 --cpu 0
python -m eval.deep_chain_native_transition_profile verify --ticket PUYO-206 --artifact-dir docs/benchmarks/puyo-206-native-compact-hot-path
```
