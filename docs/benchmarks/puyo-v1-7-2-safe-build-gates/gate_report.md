# PUYO-170 two-stage safe-build gates

- canonical integration: `d55951ee2574a2c938efce4af43a840ff6ab7379`
- training capability gate: **BLOCKED**
- post-training promotion gate: **PENDING_POST_TRAINING**
- PUYO-130 long-run: **BLOCKED**

The capability selector is evaluation-only. It proves candidate-set capacity; it does not stand in for a learned ranker.

## Pre-training capability

- fixed suite: 29/30 games x 40 moves
- actual capability-path mean maximum chain: 2.933
- candidate best-reachable chain mean/max: 2.933/11
- bounded-reference root/path coverage: 0.837/0.651
- premature fire avoidable/candidate-limited: 0/27
- avoidable no-fire candidate gaps: 0
- forced game-over candidate gaps: 0
- proposal latency p50/p95 (observational_wall_clock_including_proposal_projection): 582.67/917.12 ms
- configuration selection: NO_PASSING_CONFIGURATION (none)

### Checks

- `fixed_suite`: FAIL
- `mean_max_chain`: FAIL
- `avoidable_candidate_gap`: PASS
- `forced_game_over_gap`: PASS
- `registry_latency_budget`: FAIL
- `deterministic_repetition`: PASS

## Post-training promotion

- checkpoint role: `pretraining_reference`
- selected-policy mean max chain: 4.133
- selected-policy premature/game-over: 86/0
- post-PUYO-158 threat scenarios: 6/6

A pre-training reference checkpoint intentionally remains in `PENDING_POST_TRAINING`; PUYO-133 must provide five training seeds, lineage, and GUI/replay QA for registration.

## Reproduce

```bash
python3 -m eval.v1_7_safe_build_gates verify \
  --artifact-dir docs/benchmarks/puyo-v1-7-2-safe-build-gates
```
