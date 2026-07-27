# PUYO-179 Seeded Hidden-Future Sampling

## Scope

PUYO-179 changes only the hidden-tsumo contract used by the compact
long-horizon `build_main` search. The current pair and visible NEXT 2 remain
authoritative game state. Sampling starts at cursor 3 and never rewrites that
known prefix.

Canonical runtime, smoke, quality, evaluation, and training paths now use
`seeded-authoritative`. The previous six fixed two-pair cycles remain available
only through the explicit `legacy-fixed-six` profile for Ama comparison and
regression fixtures.

## Versioned sampling contract

The queue metadata schema is `puyo.future_tsumo_sampling.v1`; scenario sequence
metadata is `puyo.long_horizon_scenario_sequence.v2`.

For one decision:

1. An explicit `decision_seed` is used when supplied. Otherwise a stable seed
   is derived from the simulator sequence seed, compact decision state, current
   pair, and NEXT 2.
2. Each `sample_index` receives a 64-bit `rollout_seed` through
   `sha256-decision-seed-sample-index-v1`.
3. A fresh production `src.core.tsumo.PuyoSequence` uses that rollout seed.
   It samples each axis/child color independently and uniformly from
   `NORMAL_PUYO_COLORS`, exactly like the game simulator.
4. The sampler materializes only the unknown pairs needed to reach the search
   depth. Canonical samples are finite and have repeat policy `none`.

Each sample stores:

- decision seed and whether it was explicit or state-derived;
- rollout seed and derivation contract;
- opaque sample ID and sample index;
- known/unknown boundary and full sampled pair list;
- full queue digest and sequence digest;
- generator and distribution identity.

The same decision state, seed, and configuration reproduce the same queues and
digests. A different decision seed produces independent rollout seeds and
queues.

## Profiles and count budgets

The search profile schema is `puyo.long_horizon_profile.v3`.

| profile | depth | width | samples | expanded-node budget | sampling |
|---|---:|---:|---:|---:|---|
| `runtime@3.0` | 4 | 24 | 2 | 4,096 | seeded authoritative |
| `smoke@3.0` | 6 | 48 | 3 | 30,000 | seeded authoritative |
| `quality-d12@3.0` | 12 | 128 | 6 | 200,000 | seeded authoritative |
| `quality-d16@3.0` | 16 | 250 | 6 | 600,000 | seeded authoritative |
| `legacy-fixed-six@1.0` | 16 | 250 | 6 | 600,000 | fixed two-pair cycle |

Runtime retains its external deadline contract. Smoke and quality profiles use
expanded-node counts as the authoritative budget and treat wall time as
observational.

## Proposal v2 and permutation invariance

`puyo.worker_proposal_batch.v2`, K = 8, stable candidate IDs, rank-0
compatibility, legal/candidate/scenario masks, and the fixed tensor shapes are
unchanged.

Sample identity is decision-shared metadata. Neither `sample_id`, sample index,
rollout seed, nor queue digest appears as a scalar or embedding feature. The
candidate row continues to use the permutation-invariant expected-chain
statistics: mean, worst value, dispersion, support, and coverage. Per-sample
rows are matched through internal scenario slots and canonicalized before
ranker tensor construction, so reordering serialized samples does not change
the ranker input digest.

The complete sample metadata and per-sample evidence remain losslessly
available for replay, audit, and artifact reconstruction.

## Verification

The fixture `tests/fixtures/future_sampling_cases.json` checks the known queue
boundary, same/different decision seeds, six independent samples, and the
legacy fixed-six path.

The tracked benchmark additionally verifies production-generator parity,
absence of the canonical two-pair cycle, profile budgets, two-repeat
latency-free proposal determinism, K = 8 and masks, stable candidate IDs,
sample-order-invariant ranker input, and exclusion of sample identity from
ranker features.

```bash
python -m eval.v1_7_future_sampling_benchmark run
python -m eval.v1_7_future_sampling_benchmark verify
```

Artifacts are written to
`docs/benchmarks/puyo-v1-7-2-future-sampling/`.
