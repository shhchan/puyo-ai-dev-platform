# PUYO-202 native long-horizon search benchmark

Decision: **GO**.

## Contract

- depth 16 / width 250 / 6 deterministic scenarios
- global count-authoritative maximum: 600,000 expanded nodes
- five release-wheel samples, nearest-rank p95, no outlier removal
- reusable six-worker pool; `oracle-1` is the semantic authority

## Results

| Gate | Observed p95 | Limit | Result |
| --- | ---: | ---: | --- |
| native decision total | 277.687 ms | 900.000 ms | pass |
| decision + Python materialization | 398.749 ms | 1,000.000 ms | pass |

- Python differential mismatches: 0
- oracle/parallel mismatches: 0
- repeat mismatches: 0
- isolated deterministic seeds: 30 / 30
- exact global-budget rerun: True
- GIL probe counter delta: 6591383
- peak live nodes / arena capacity: 5690 / 6000
- process max RSS: 173468 KiB

## Tie-break ablation

`puyo.long_horizon_survivor_tie_break.v2` replaces per-survivor SHA-256 with
lexicographic canonical state bytes. Across the frozen corpus, decision/ranking
and root-evidence mismatches were both zero; representative-only path changes
were 4.

Production backend selection remains out of scope for PUYO-202.
