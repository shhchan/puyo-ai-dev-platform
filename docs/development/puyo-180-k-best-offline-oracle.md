# PUYO-180 K-best offline oracle

## Scope

PUYO-180 separates candidate-generation capability from candidate-selection
quality. The evaluator in `eval/v1_7_k_best_oracle.py` is an offline upper
bound: it may inspect the authoritative future queue for the evaluation seed,
but every executed action must be the legal root of a candidate already
present in the current Proposal v2 K-best.

The oracle does not add legal actions outside K-best, train a Candidate Ranker,
or make a promotion decision. PUYO-176 consumes this evidence for the
training-start gate. Learned-policy quality remains a PUYO-133 responsibility.

## Continuous build/fire trajectory

One simulator instance and one trajectory ID cover both phases.

| Phase | Limit | Selection objective |
| --- | ---: | --- |
| `build` | 40 placements | avoid game over and 1–9-chain fire, then maximize target readiness, structural potential, trigger continuity, continuation, and survival |
| `fire` | 6 placements | reach the recorded main-chain trigger and maximize authoritative actual chain count |

The oracle enters `fire` when a K-best candidate plan can actually reach the
target against the authoritative queue, or after 40 build placements. It stops
on a target fire, game over, classified premature fire, candidate absence, or
the six-placement fire timeout. Phase boundary, reason, and termination reason
are explicit artifact fields. `phase_lifecycle` records each phase's entry,
decision count, and end reason; when build terminates early, it also records
why fire was not entered.

The default target is ten chains. Tests and contract smoke runs may lower phase
or search budgets, but the production defaults remain 40 build placements,
six fire placements, target ten, and K = 8.

## Authoritative future boundary

`puyo.k_best_offline_oracle_future.v1` snapshots current + NEXT 2 and the exact
hidden continuation from a clone of the authoritative simulator.
Candidate-plan replay runs on a clone that retains that authoritative
sequence; the explicit future snapshot is stored only in offline evidence.
Proposal generation keeps the PUYO-179 seeded-sampling contract.

Every decision records an isolation check proving that neither the private
future digest nor oracle-only field names occur in:

- the Candidate Ranker input;
- the PPO/runtime observation.

The private queue may appear in the offline evidence artifact so the oracle
decision can be audited. It is not a runtime feature.

## Candidate and trigger evidence

Each decision records, separately:

- K-best maximum search-predicted chain;
- K-best maximum structural potential;
- each candidate's authoritative-plan maximum chain and target-fire depth;
- structural score, predicted trigger color/cells, anchor cells, and trigger ID;
- selected oracle value, rank-0 value, legacy-selector value, and regret;
- authoritative actual chain count after executing the selected root;
- danger, trigger continuity/loss, candidate gap, and game-over turn.

Candidate-plan replay is counterfactual evaluation only. The evaluator executes
only the first action, regenerates Proposal v2 at the next real state, and again
selects only from that new K-best.

Build-phase 1–9-chain fire is classified as:

- `oracle_error`: a quiet or target candidate existed in K-best;
- `candidate_limited`: K-best lacked one, but an eligible legal root existed;
- `unavoidable`: no eligible legal root existed.

## Selector and failure classification

The same decision artifact compares:

- `offline_oracle`;
- `compatibility_rank_0`;
- `legacy_capability_selector`;
- a `learned_selector` placeholder whose status is `not_evaluated`.

Full trajectories are also run for the first three selectors. A successful
oracle with a failed compatibility/legacy selector is
`candidate_selection_failure`. A failed oracle is
`candidate_generator_failure`.

Oracle failure flags use the fixed machine-readable vocabulary:

- `candidate_absence`;
- `evaluator_overestimate`;
- `premature_fire`;
- `dead_end`;
- `game_over`;
- `fire_window_timeout`.

Structural/search prediction of ten or more with authoritative actual fire
below ten is explicitly marked `evaluator_overestimate`.

## Schemas and artifacts

- config: `puyo.k_best_offline_oracle_config.v1`
- candidate: `puyo.k_best_offline_oracle_candidate.v1`
- decision: `puyo.k_best_offline_oracle_decision.v1`
- trajectory: `puyo.k_best_offline_oracle_trajectory.v1`
- suite: `puyo.k_best_offline_oracle_suite.v1`
- determinism: `puyo.k_best_offline_oracle_determinism.v1`

The artifact directory contains config, summary, full trajectory records,
two-repeat latency-free determinism, report, and a checksum manifest.

```bash
python -m eval.v1_7_k_best_oracle run \
  --seeds 123 \
  --profile runtime \
  --build-steps 40 \
  --fire-steps 6 \
  --target-chain 10

python -m eval.v1_7_k_best_oracle verify
```

The tracked PUYO-180 contract artifact uses reduced count-bounded search
budgets while retaining the 40 + 6 phase envelope. PUYO-176 is responsible for
the canonical 30-seed quality matrix and latency-waiver decision.
