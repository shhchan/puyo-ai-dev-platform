# PUYO-169 Worker Proposal Benchmark

Latency is observational. Preview budget is the deterministic expanded-node cap,
and peak memory is measured with `tracemalloc` through JSON serialization.

| configuration | K | node budget | candidates | expanded nodes | latency p50 / p95 ms | memory p50 / p95 KiB | JSON p50 bytes |
|---|---:|---:|---:|---:|---:|---:|---:|
| k1-n96 | 1 | 96 | 1.00 | 96.00 | 157.52 / 160.12 | 919.42 / 999.92 | 6928 |
| k4-n96 | 4 | 96 | 4.00 | 96.00 | 325.11 / 357.54 | 1164.92 / 1254.81 | 21066 |
| k8-n96 | 8 | 96 | 8.00 | 96.00 | 565.21 / 614.80 | 1301.16 / 1363.58 | 39489 |
| k4-n48 | 4 | 48 | 4.00 | 48.00 | 297.86 / 327.25 | 871.83 / 881.01 | 20903 |
| k4-n192 | 4 | 192 | 4.00 | 192.00 | 380.41 / 402.62 | 1649.48 / 1947.74 | 21115 |

## Checks

- `selected_action_legal`: PASS
- `compatibility_rank_0`: PASS
- `fixed_shape`: PASS
- `round_trip`: PASS
- `all_candidates_legal`: PASS
- `deterministic_repetitions`: PASS
- `passed`: PASS

Reproduce with:

```bash
python -m eval.v1_7_worker_proposal_benchmark run
python -m eval.v1_7_worker_proposal_benchmark verify
```
