# PUYO-167 Diverse beam candidate generator

## Scope

`BeamSearchPolicy` now exposes a deterministic candidate-generator contract in
addition to the existing single-action policy API. The implementation targets
BuildPotential v2 build search and does not add a learned ranker or named-style
score.

The public entry points are:

- `generate_candidates(observation, info) -> tuple[DiverseBeamCandidate, ...]`
- `select_action(observation, info) -> int`, retained as a compatibility adapter
  that returns candidate rank 0
- `BeamSearchDiagnostics.proposals` / `BeamStrategyWorker`'s
  `SearchProposal.beam_candidates` for downstream proposal/ranker integration

Each candidate records its root action, complete bounded plan, scalar rank,
BuildPotential v2 result, danger, continuation flexibility, trigger
recoverability, value breakdown, hidden-scenario support, and deterministic
generation/retention/pruning reasons.

## Selection semantics

Legacy mode keeps the previous scalar ordering and pruning path. Diverse mode
uses the same scalar elites plus fixed deterministic slots for:

1. future BuildPotential;
2. survival/danger margin;
3. continuation flexibility;
4. actual chain outcome;
5. trigger recoverability; and
6. root-action coverage.

Potential probe candidates are selected with those slots before the final beam
width prune. Final retained nodes are sorted back into deterministic scalar
rank, with lower action/path indexes as stable tie-breakers. The best scalar
candidate is always present so `select_action` remains a well-defined
single-best adapter.

Field fingerprints remove transpositions at each depth. Equal-valued duplicate
replacement is deterministic in diverse mode, and legacy mode keeps its old
first-seen behavior. BuildPotential evaluation uses a decision-scoped cache;
cache-on and cache-off modes consume the same count budget and return the same
candidate payload.

## Budget and uncertainty contract

- `potential_probe_budget` bounds BuildPotential calls.
- `max_expanded_nodes` is the deterministic runtime-budget proxy. Exhaustion
  stops deeper/scenario expansion and returns the best valid non-game-over
  candidates generated so far. A safe legal preview is used only if no scored
  candidate survived.
- The current pair and two queued next pairs remain known. Only hidden future
  pairs are replaced by `_ScenarioSequence`.
- Diagnostics record known-pair count, requested/evaluated hidden scenarios,
  scenario IDs/seed, expanded nodes, reached depth, cache/transposition counts,
  and fallback reason.

No wall-clock deadline participates in ranking or fallback, so equal
seed/config inputs retain equal candidate sets and diagnostics across machine
load. Wall-clock latency remains an offline observational metric.

## Compatibility and safety

- Default `BeamSearchConfig` remains `candidate_mode="legacy"` with one output
  candidate.
- BuildPotential-v2 `build_main` requests use diverse mode and reuse planner
  `candidate_count` as both probe width and output candidate limit.
- `fire_main` remains on its legacy one-step tactical worker route.
- Invalid placements and states that immediately game over are rejected before
  candidate construction.
- Existing legacy fixed-action/value regression fixtures remain unchanged.

## Evaluation

The committed artifact replays the immutable PUYO-165 decision source at all 31
bounded-reference opportunities where the reference reaches at least 10 chains
and improves on the current selected chain. It compares equal-budget scalar and
diverse search with raw depth/width scaling.

| configuration | long-chain action coverage | reference-path coverage | max candidate chain | expanded nodes | p50 / p95 ms |
|---|---:|---:|---:|---:|---:|
| scalar d6/w48/p16/k16 | 1.000 | 0.129 | 1.677 | 3306.8 | 1829.19 / 3240.47 |
| diverse d6/w48/p16/k16 | 1.000 | 0.129 | 2.032 | 3278.5 | 1718.84 / 4577.74 |
| scalar d8/w64/p32/k16 | 1.000 | 0.161 | 4.387 | 6124.8 | 3222.56 / 6073.08 |

The two latency-free repetitions have the same digest
`3d65105519ae501bfd6cf322131ba68fa45fae426f41be2ee76c6df34f3510ad`.
All candidate actions were legal and non-game-over. The diverse equal-budget
configuration retained long-chain root-action coverage and improved the mean
maximum chain available to a downstream ranker, while raw scaling found more
chain at substantially higher node/latency cost.

Exact reference-path survival improved for seeds 126 and 127, regressed for
133 and 136, and was unchanged elsewhere. Those results remain visible in the
seed artifact; the gate is intentionally based on the ranker-visible long-chain
root candidate set and aggregate candidate quality rather than hiding path
losses through fixed-weight retuning. Wall-clock p95 is observational and was
worse for diverse mode in this concurrent run despite its lower node count.

Artifacts:

- `docs/benchmarks/puyo-v1-7-2-diverse-beam/benchmark_summary.json`
- `docs/benchmarks/puyo-v1-7-2-diverse-beam/decision_records.json`
- `docs/benchmarks/puyo-v1-7-2-diverse-beam/seed_results.json`
- `docs/benchmarks/puyo-v1-7-2-diverse-beam/determinism.json`
- `docs/benchmarks/puyo-v1-7-2-diverse-beam/benchmark_report.md`
- `docs/benchmarks/puyo-v1-7-2-diverse-beam/benchmark_manifest.json`

Reproduce and verify:

```bash
python -m eval.v1_7_diverse_beam_benchmark run \
  --games 30 --max-steps 40 --scope long-chain \
  --repetitions 2 --workers 20
python -m eval.v1_7_diverse_beam_benchmark verify
```

The run command always persists its summary and decision evidence, including a
failed gate. `verify` returns failure after validating those files when the gate
does not pass.
