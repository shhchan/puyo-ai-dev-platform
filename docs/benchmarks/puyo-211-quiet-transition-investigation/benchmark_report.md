# PUYO-211 quiet transition reproducibility investigation

- Decision: **NO_IMPLEMENTATION_CANDIDATE**
- Baseline wheel SHA-256: `c7214697031e553b5d6ae9047e3dff918a134069f76f9264e183801458db06cc`
- Baseline source revision: `cff9ea5d0295087db8332c2a6efa59db74f886ef`
- CPU affinity: `0` (one thread)
- Percentile: nearest-rank; no samples excluded

## Three fresh-process authoritative runs

| Run | mixed p50 | mixed p95 | quiet p50 | quiet p95 | one-chain p95 | multi-chain p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 59.624 | 106.607 | 34.719 | 41.552 | 374.439 | 637.352 |
| 2 | 62.913 | 108.904 | 36.378 | 65.578 | 337.608 | 447.915 |
| 3 | 63.703 | 98.655 | 35.888 | 50.918 | 381.168 | 444.590 |

Quiet run-p95 median is `50.918 ns`; range is `24.026 ns`. Mixed run-p95 median is `106.607 ns`.
The baseline itself is not reproducible at the fixed gates: two of three quiet runs exceed 50 ns, two of three mixed runs exceed 100 ns, and the quiet run-p95 median exceeds the 45 ns engineering margin.
Every raw sample, run order, process ID, affinity, and before/after CPU-frequency snapshot is retained.

## PUYO-206 / PUYO-207 gap

PUYO-206 and PUYO-207 used the identical compact.rs Git object and locked contract. The 23.421 ns p95 gap has no algorithmic source change and is attributed to process/frequency/scheduling variance; fresh-process replication quantifies that variance separately.

Three additional reference/control process pairs calibrate environment variance. PUYO-212 must alternate baseline/candidate order and compare paired ratios; the median may not regress and each run has a fixed 2% tolerance.

| Same-wheel pair | median control/reference p95 | maximum absolute drift |
| --- | ---: | ---: |
| mixed | 0.8694 | 30.0% |
| quiet | 0.8473 | 33.3% |

Same-wheel drift is far above the 2% code-regression tolerance, so this environment cannot resolve a small production change reliably.

## Re-profiled quiet cost

| Stage | adjusted p50 cycles | Cachegrind instructions | branches | L1 data misses | simulated writes |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct_placement | 44.417 | 133.317 | 14.163 | 0.303 | 21.734 |
| color_plane_extraction | 34.554 | 101.055 | 4.853 | 3.467 | 9.008 |
| inserted_connectivity | 60.550 | 354.997 | 10.765 | 0.000 | 36.004 |
| state_result_materialization | 17.518 | 39.566 | 0.574 | 0.020 | 11.004 |
| chain_scan | 0.000 | 0.000 | 0.405 | 0.000 | 0.000 |
| gravity | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| score_lifecycle | 3.172 | 7.160 | 1.490 | 0.000 | 1.003 |

The fixed semantic output remains `80 + 24 = 104` bytes. The three-slice placement profile copies `80` bytes and updates `54` bytes.

## Candidate decision

No candidate has sufficient conservative evidence for the required three-run margin; do not modify native production code.

The strongest isolated option, reusing placed child planes, has zero semantic mismatches and median candidate/baseline ratio `0.7665`, but its conservative p05 saving is only `0.521 ns`.
That projects quiet p95 to `41.031, 65.057, 50.397` ns with median `50.397 ns`; mixed also remains above target in two runs. PUYO-212 should remain unstarted unless a controlled baseline or a new candidate can satisfy the same gates.

## Fixed gate for any authorized follow-up

- Quiet: all three p95 `<= 50.0 ns`; median `<= 45.0 ns`.
- Mixed: all three p95 `<= 100.0 ns`; paired median no regression; every regression `<= 2.0%`.
- Stop/rollback: rollback the local plane-source change if any semantic/ABI/allocation gate fails, any quiet run exceeds 50 ns, the quiet run median exceeds 45 ns, any mixed run exceeds 100 ns, paired median regresses, or an individual paired regression exceeds 2%

## Reproduction

```bash
VALGRIND_BIN=/path/to/valgrind-3.22.0
VALGRIND_LIB_DIR=/path/to/libexec/valgrind
.venv/bin/python -m eval.deep_chain_native_transition_investigation run --corpus eval/deep_chain_native_transition_corpus.json --search-corpus eval/deep_chain_native_corpus.json --output-dir docs/benchmarks/puyo-211-quiet-transition-investigation --cpu 0 --valgrind "$VALGRIND_BIN" --valgrind-lib "$VALGRIND_LIB_DIR"
.venv/bin/python -m eval.deep_chain_native_transition_investigation verify --artifact-dir docs/benchmarks/puyo-211-quiet-transition-investigation
```
