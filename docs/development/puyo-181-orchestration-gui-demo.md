# PUYO-181 Orchestration GUI Demo

This runbook prepares the Friday presentation demo without changing PUYO-176
canonical evidence or starting PUYO-130, PUYO-131, or PUYO-133.

The primary preset uses a demo-only behavior-cloning checkpoint for
`v1_7_bootstrap_manager` against `v1_7_analyzer_manager`. The fallback is the
checkpoint-free, rule-based `v1_7_analyzer_manager` against `manager_rule`.

## Evidence boundary

This demo is deliberately not:

- PUYO-130 mixed-opponent PPO training;
- a learned CandidateRanker;
- PUYO-176 canonical `GO` or `GO_WITH_LATENCY_WAIVER` evidence;
- promotion, release, realtime-latency, or formal strength evidence.

The normal learned manager takes tens of seconds per decision on the
presentation CPU because it previews multiple Python search workers. The demo
preset preserves the checkpoint, Analyzer, eight-tactic registry, learned
arbitration, Planner, and worker path while applying an explicit runtime-only
cap:

```text
preview_top_k = 1
search_depth <= 1
search_width <= 4
candidate_count <= 2
latency_budget_ms <= 250
```

Every result records both the requested and effective search budgets under
`runtime_constraints`. Do not reuse the capped result as a benchmark.

## Prepare once before the presentation

From the repository root:

```bash
.venv/bin/python -m eval.v1_7_orchestration_demo prepare
```

This generates the ignored checkpoint at:

```text
runs/v1_7_manager/puyo-181-friday-demo-seed126/checkpoints/bootstrap.pt
```

The command validates checkpoint compatibility, the training artifact
manifest, and every recorded checksum. It does not commit the binary.

## Automated primary QA

```bash
.venv/bin/python -m eval.v1_7_orchestration_demo qa --preset primary
```

The dummy-SDL run automatically exits after the terminal screen, evaluates the
`playability` profile, verifies every replay snapshot and the final hash, and
writes:

```text
runs/puyo-181-demo/primary-seed126/qa/gui_qa.json
runs/puyo-181-demo/primary-seed126/qa/replay.json
runs/puyo-181-demo/primary-seed126/qa/demo.gif
runs/puyo-181-demo/primary-seed126/qa/demo_manifest.json
```

Recheck an existing replay without rerunning the GUI:

```bash
.venv/bin/python -m eval.v1_7_orchestration_demo verify --preset primary
```

## Live presentation

Use the one-command primary preset:

```bash
.venv/bin/python -m eval.v1_7_orchestration_demo live --preset primary
```

The primary deterministic seed is `126`. The window records the first 30
seconds to the same ignored `demo.gif` fallback while the live match runs.

## Runtime tuning

The checked primary preset remains the presentation default, but `live` and
`qa` accept runtime-only adjustments. The default QA artifacts are preserved:
any non-default configuration is written below a generated
`qa/custom-<digest>/` or `live/custom-<digest>/` directory. The adjacent
manifest records every effective tuning value.

The already available controls are:

```text
--seed                 change the deterministic piece sequence
--max-ticks            raise or lower the match tick ceiling
--speed                change presentation playback speed
--record-seconds       change GIF duration
--record-fps           change GIF capture rate
--output-dir           explicitly choose an artifact directory
```

`--max-ticks` is only an upper bound. A game over still ends the match earlier;
press `R` in the live UI to restart immediately with the configured seed.

The orchestration-specific controls are:

```text
--target-chain         override build_main objective.target_chain (1..19)
--opponent             replace player 2 with a supported checkpoint-free policy
--preview-top-k        preview more learned tactic proposals (1..8)
--planner-depth-cap    raise/lower the demo search-depth ceiling
--planner-width-cap    raise/lower the demo search-width ceiling
--planner-candidate-cap
--planner-latency-cap-ms
--no-planner-cap       remove all presentation caps (very slow)
--force-tactic         strict learned-manager diagnostic override
```

Start with a longer recording while retaining the validated responsive
configuration:

```bash
.venv/bin/python -m eval.v1_7_orchestration_demo live \
  --preset primary \
  --max-ticks 14400 \
  --record-seconds 60
```

For a larger-chain exploration, increase the build target and search budget
gradually:

```bash
.venv/bin/python -m eval.v1_7_orchestration_demo live \
  --preset primary \
  --max-ticks 14400 \
  --target-chain 8 \
  --opponent worker_large \
  --preview-top-k 2 \
  --planner-depth-cap 2 \
  --planner-width-cap 12 \
  --planner-candidate-cap 4 \
  --planner-latency-cap-ms 500 \
  --record-seconds 60
```

This raises the objective and gives the planner more room, but it cannot
guarantee an eight-chain result: the learned arbitration may respond to danger,
incoming ojama, or a fire opportunity. Try another `--seed` if the match ends
too early. Increase one search control at a time because CPU decision latency
rises quickly.

`--force-tactic build_main` is available for inspecting the build worker, but
it is intentionally strict and aborts if `build_main` becomes ineligible (for
example, when the incoming-response guard takes responsibility). It is not the
recommended live-presentation default. `--no-planner-cap` restores the normal
request but can make one decision take tens of seconds on the presentation CPU.
The command prints the exact custom manifest path. Recheck its replay by
passing that path explicitly, for example:

```bash
.venv/bin/python -m eval.v1_7_orchestration_demo verify \
  --replay runs/puyo-181-demo/primary-seed126/live/custom-<digest>/replay.json
```

During the presentation, point out:

1. the active pair moves, rotates, drops, and locks instead of jumping directly
   to a placement;
2. `tactic` and `why` show the selected tactic and reason;
3. `obj`, `plan`, and `w c… a…` show the Planner objective, plan identifier,
   predicted worker chain, and predicted attack;
4. the input/plan cursor changes while the match clock advances;
5. chain and ojama labels appear when attacks resolve;
6. the winner banner appears when the match terminates.

The machine-readable manifest still marks real-display verification as a human
check; complete the checklist below before presenting.

## Fallback

If checkpoint loading or learned inference is unstable, switch immediately to:

```bash
.venv/bin/python -m eval.v1_7_orchestration_demo live --preset fallback
```

The fallback deterministic seed is `123`. State explicitly that this is the
rule-based v1.7 Analyzer Manager demonstrating the same eight-tactic
orchestration and diagnostics, not a learned policy.

If the live display itself is unstable, open:

```text
runs/puyo-181-demo/primary-seed126/qa/demo.gif
```

The GIF was rendered from the same policy, seed, checkpoint, runtime cap, and
commit recorded in the adjacent `demo_manifest.json`. Interactive live artifacts
are written separately below `primary-seed126/live/`, so they do not overwrite
the automated QA evidence.

## Human verification checklist

- Run `status`; confirm `valid: true` and no checkpoint or manifest errors.
- Run primary `qa`; confirm `quality_gate.passed: true`.
- Run `verify`; confirm `expected_final_hash` equals `verified_final_hash`.
- Open `demo.gif`; confirm the board, active pair, and HUD are legible.
- Run the primary live command on the actual presentation display.
- Confirm horizontal movement, rotation, drop, lock, and repeated placement.
- Confirm tactic, reason, objective, plan, predicted chain, and predicted attack
  update during play.
- Confirm chain/ojama feedback and the winner banner, or observe a sufficiently
  complete continuous run.
- Run the fallback command once and keep its command ready in shell history.
- Keep the evidence-boundary statement visible in the presenter notes.

## References

- PUYO-181 / PUYO-176 / PUYO-130 / PUYO-131 / PUYO-133
- `eval/v1_7_orchestration_demo.py`
- `eval/realtime_versus_ui.py`
- `agents/v1_7_strategy_manager.py`
- `agents/v1_7_analyzer_manager.py`
- `train/config/v1_7_manager_bootstrap.yaml`
