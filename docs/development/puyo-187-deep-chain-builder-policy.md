# PUYO-187: deep-chain builder policy and N-turn plan

## Implemented contract

`DeepChainBuilderPolicy` now executes all seven `DeepChainBuildFlow` steps and
returns a legal placement action together with JSON-serializable diagnostics.
`make_policy("deep_chain_builder")` constructs the policy without a checkpoint;
callers may select the bounded smoke profile with
`deep_chain_profile="smoke"`.

The selected representative trajectory is emitted as `n-turn-plan-v1`. Every
plan step records:

- the action, axis, rotation, and placed cells;
- whether the tsumo is observed (`known_tsumo=true`) or scenario-completed;
- the scenario ID and pair colors;
- predicted chain/score/attack values and the resolved predicted board;
- a stable state fingerprint for replay comparison.

The policy searches every new observation. A content-derived plan ID remains
stable when the observation and selected trajectory are unchanged. When the
fresh result changes, the new plan receives a new ID and
`replan_reason="new_observation"`.

## Diagnostics and fallback

`tactical_diagnostics` uses schema
`puyo.deep_chain_builder.diagnostics.v1` and exposes the selected action, plan,
candidate count, all scenario aggregates, selection reason, search counters,
and the complete timed decision trace.

Search exceptions, timeouts, missing representatives, and illegal selected
actions cross the policy boundary as a deterministic first-legal fallback.
The fallback still emits a plan whose first action matches the returned action,
and always records a machine-readable reason plus exception detail.

## Reproducible headless smoke

Regenerate the committed safe/no-threat artifact with:

```bash
.venv/bin/python -m eval.deep_chain_builder_smoke
```

Verify its schema, checks, and artifact digest without rerunning search:

```bash
.venv/bin/python -m eval.deep_chain_builder_smoke --verify
```

The default run executes three placements twice from seed 187 with the smoke
profile. The two runs must have identical action, plan, and final-board digests.
The artifact is
`docs/benchmarks/puyo-187-deep-chain-builder-smoke.json`.

## Human review checklist

1. Compare each `board_before`, selected `action`, and `board_after`.
2. Confirm `plan.steps[0].action` equals the policy action on every turn.
3. Confirm steps 1–3 are known tsumo and the fourth is an unknown scenario step.
4. Inspect the selected score breakdown and per-step trace timings.
5. Confirm both fixed-seed repeats report the same action and plan digests.
