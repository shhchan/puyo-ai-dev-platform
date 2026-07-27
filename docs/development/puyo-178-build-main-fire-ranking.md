# PUYO-178 Build-main fire semantics and K-best coverage

## Scope

PUYO-178 changes only the compact long-horizon backend used by `build_main`.
`fire_main`, cancellation/counter, and survival workers keep their tactical
search semantics. The manager response guard continues to reserve active
incoming deadlines for `counter_or_return` or `survive`.

The public worker proposal remains `puyo.worker_proposal_batch.v2`: K is still
8, candidate IDs still derive from the decision and action sequence, and
candidate/legal/scenario masks retain their existing shapes.

## Fire classes

`puyo.expected_chain_ranking.v2` classifies each root/scenario before comparing
scalar scores.

| class | safe-build meaning | ranking responsibility |
|---|---|---|
| `winning_fire` | an externally supplied winning-score threshold is reached | permitted fire |
| `target_fire` | the objective target chain is reached | permitted fire |
| `forced_safety_fire` | the caller explicitly marks a necessary safety response | permitted fire |
| `quiet_continuation` | a non-game-over continuation survives | construction candidate |
| `premature_fire` | a safe `build_main` branch fires below target | terminal failure evidence |
| `unavailable` | no evaluated representative exists | not selectable |

The class priority is evaluated before the class-local scalar. Therefore a
1–9 chain official score cannot lift a safe premature fire above a quiet root.
A target fire ranks above construction continuations. A forced safety fire is
available only through the explicit `forced_safety` context; normal production
`build_main` profiles use `safe_build`.

## Terminal score

Every firing node is structurally evaluated before `record_and_stop`. The
versioned `puyo.build_main_terminal_score.v1` value contains:

- the full chain-structure score and breakdown;
- the existing premature-fire, tear, waste, trigger-damage, and danger terms;
- an additional target-chain-gap penalty for safe premature fire;
- official score only for permitted target, winning, or forced-safety fire.

The selected fire, observed best official-score fire, terminal score, target
gap, trigger state, and danger remain separately traceable. Quiet roots retain
their best survivor evaluation instead of being projected through an
incidental premature branch.

## Root survivor quota

At every depth and hidden scenario, pruning now happens in two stages:

1. reserve `root_survivor_quota` candidates for each legal root in deterministic
   root order;
2. fill the remaining width from the globally ranked shared beam.

Coverage records candidate and retained counts per root/depth. If the quota
cannot be met, the evidence distinguishes expanded-node exhaustion,
insufficient beam width, terminal-fire-only roots, game over, invalid
transition, and absence of a non-terminal survivor.

`root_diagnostics` additionally records quiet coverage, target-not-reached
fires, trigger preservation/damage, danger delta, and structural-potential
deltas. These fields are stored losslessly under Proposal v2 evidence without
changing ranker tensor dimensions.

## Schemas and profiles

- long-horizon profile: `puyo.long_horizon_profile.v2`
- expected-chain evidence: `puyo.expected_chain_evidence.v2`
- root ranking: `puyo.expected_chain_ranking.v2`
- terminal score: `puyo.build_main_terminal_score.v1`
- root survivor coverage: `puyo.root_survivor_coverage.v1`
- root diagnostics: `puyo.build_main_root_diagnostics.v1`

The `runtime`, `quality-d12`, and `quality-d16` profile identities are version
`2.0`. Their terminal threshold remains one chain so a premature branch is
recorded and stopped, while the objective target remains ten by default.

## Verification

`tests/fixtures/build_main_fire_cases.json` reproduces premature fire, quiet
continuation, target fire, and forced safety fire. It also pins the ten-chain
target boundary.

The tracked benchmark verifies:

- quiet-over-premature and target-over-quiet ordering;
- terminal structural penalty, target gap, trigger damage, and danger trace;
- per-root quota coverage and explicit shortfall reasons;
- compact-kernel/authoritative-simulator parity;
- Proposal v2 K=8, stable IDs, legal masks, and rank-0 compatibility;
- two-repeat latency-free determinism;
- response-guard responsibility on all six threat fixtures.

```bash
python -m eval.v1_7_build_main_fire_ranking_benchmark run
python -m eval.v1_7_build_main_fire_ranking_benchmark verify
```
