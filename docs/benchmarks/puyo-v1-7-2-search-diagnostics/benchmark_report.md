# PUYO-165 safe-build search diagnostics

- current: `d6-w48-p16`
- reference: `d8-w64-p32` (bounded reference, not an oracle)
- deterministic replay: **PASS** (2 runs)
- original build_main gate: **BLOCKED**
- dominant diagnosis: **horizon_or_uncertainty** — 1058 decisions; both searches agree or the reference remains below target within its bounded horizon

## Aggregate

- reference-path candidate coverage: 0.777
- reference root-action coverage: 0.998
- mean best reachable chain: 1.357
- mean selected chain: 1.145
- mean chain regret: 0.124
- mean game maximum chain: 7.533
- premature fire: avoidable=0, candidate-limited=9
- early game-over: 0
- latency (offline_wall_clock): current p50/p95=1058.76/1402.39 ms, reference p50/p95=2151.34/2807.22 ms

## Failure classification

| class | decisions |
|---|---:|
| `candidate_coverage` | 62 |
| `ranking` | 4 |
| `horizon_or_uncertainty` | 1058 |
| `safety_constraint` | 0 |
| `none` | 76 |

## Seed summary

| seed | decisions | coverage | best chain | selected chain | regret | premature A/C | game over | current p95 ms | reference p95 ms |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| 123 | 40 | 0.700 | 1.85 | 1.32 | 0.53 | 0/0 | no | 1433.04 | 2928.08 |
| 124 | 40 | 0.900 | 0.68 | 0.40 | 0.28 | 0/1 | no | 1494.38 | 2908.59 |
| 125 | 40 | 0.625 | 1.50 | 1.25 | 0.25 | 0/0 | no | 1420.27 | 2862.11 |
| 126 | 40 | 0.750 | 2.38 | 1.60 | 0.53 | 0/0 | no | 1554.76 | 2964.95 |
| 127 | 40 | 0.750 | 1.50 | 1.00 | 0.50 | 0/0 | no | 1396.78 | 2742.34 |
| 128 | 40 | 0.750 | 1.50 | 1.50 | 0.00 | 0/0 | no | 1359.81 | 2773.44 |
| 129 | 40 | 0.675 | 0.17 | 0.10 | 0.07 | 0/1 | no | 1263.92 | 2551.81 |
| 130 | 40 | 0.850 | 1.85 | 1.57 | 0.00 | 0/0 | no | 1395.12 | 2683.84 |
| 131 | 40 | 0.825 | 0.15 | 0.12 | 0.03 | 0/1 | no | 1492.93 | 3039.24 |
| 132 | 40 | 0.875 | 0.07 | 0.07 | 0.00 | 0/1 | no | 1353.86 | 2754.68 |
| 133 | 40 | 0.850 | 1.75 | 1.25 | 0.00 | 0/0 | no | 1384.03 | 2754.82 |
| 134 | 40 | 0.875 | 0.12 | 0.07 | 0.00 | 0/1 | no | 1267.05 | 2610.98 |
| 135 | 40 | 0.725 | 1.93 | 1.93 | 0.00 | 0/1 | no | 1477.30 | 2852.23 |
| 136 | 40 | 0.725 | 1.00 | 0.25 | 0.25 | 0/0 | no | 1330.56 | 2780.75 |
| 137 | 40 | 0.800 | 1.80 | 2.08 | 0.00 | 0/0 | no | 1552.38 | 3049.43 |
| 138 | 40 | 0.800 | 2.15 | 1.88 | 0.00 | 0/1 | no | 1452.34 | 2880.39 |
| 139 | 40 | 0.850 | 1.27 | 1.25 | 0.00 | 0/0 | no | 1392.42 | 2789.07 |
| 140 | 40 | 0.800 | 1.50 | 1.00 | 0.25 | 0/0 | no | 1371.61 | 2702.01 |
| 141 | 40 | 0.775 | 0.05 | 0.05 | 0.00 | 0/1 | no | 1268.86 | 2647.53 |
| 142 | 40 | 0.775 | 2.77 | 2.75 | 0.03 | 0/0 | no | 1400.84 | 2814.54 |
| 143 | 40 | 0.750 | 0.75 | 0.75 | 0.00 | 0/0 | no | 1304.74 | 2730.97 |
| 144 | 40 | 0.900 | 0.07 | 0.07 | 0.00 | 0/1 | no | 1377.52 | 2799.95 |
| 145 | 40 | 0.625 | 3.15 | 2.35 | 0.50 | 0/0 | no | 1433.33 | 2867.51 |
| 146 | 40 | 0.850 | 0.75 | 0.75 | 0.00 | 0/0 | no | 1328.52 | 2702.13 |
| 147 | 40 | 0.750 | 1.38 | 1.38 | 0.00 | 0/0 | no | 1268.20 | 2697.24 |
| 148 | 40 | 0.575 | 1.27 | 0.75 | 0.53 | 0/0 | no | 1215.06 | 2377.74 |
| 149 | 40 | 0.900 | 1.35 | 1.35 | 0.00 | 0/0 | no | 1341.42 | 2524.32 |
| 150 | 40 | 0.700 | 2.15 | 2.15 | 0.00 | 0/0 | no | 1248.00 | 2449.59 |
| 151 | 40 | 0.775 | 1.60 | 1.35 | 0.00 | 0/0 | no | 1243.07 | 2568.77 |
| 152 | 40 | 0.825 | 2.25 | 2.00 | 0.00 | 0/0 | no | 1190.80 | 2368.93 |

Determinism covers actions, candidate stages, predicted outcomes, regret, classifications, and latency-free aggregates. Wall-clock latency is intentionally excluded.
