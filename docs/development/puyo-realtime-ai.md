# Realtime AI Adapter

PUYO-54 adds a realtime policy layer above the PUYO-53 fixed-tick core.
The core match still advances with `TickInput`; placement-level policies keep
their existing `Policy.select_action(observation, info) -> action_index`
contract and are adapted by `RealtimePolicyController`.

## Runtime Pieces

- `RealtimePuyoEnv` is a PettingZoo-like fixed-tick rollout wrapper around
  `RealtimeVersusMatch`.
- `RealtimePolicyController` schedules placement-policy decisions, injects
  deterministic inference latency, validates selected placements with the input
  planner, and emits one `TickInput` per match tick.
- `build_realtime_observation` keeps the turn-based checkpoint-compatible
  keys: `board`, `own_board`, `opponent_board`, `next_pairs`, and `scalars`.
  Extra realtime-only data is exposed in `realtime_scalars`.
- `build_realtime_info` exposes active-pair state, phase, incoming attack
  tick deadlines, opponent phase, controller-compatible placement simulator
  snapshots, and action masks.
- `realtime_reachable_action_mask` is available when callers need planner-
  verified reachability. The default controller path uses the cheaper placement
  legal mask and verifies the selected placement before execution.

## Scheduler Contract

`RealtimeDecisionConfig` controls the deterministic match-clock effects:

- `inference_latency_ticks`: idle ticks inserted before a plan starts.
- `timeout_ticks`: deadline for inference. If exceeded, the controller uses the
  fallback action at the timeout tick.
- `action_deadline_ticks`: optional maximum input-plan length. Longer plans are
  counted as deadline misses and replaced by fallback when possible.
- `fallback_action_index`: optional fixed fallback; otherwise the first legal
  action is used.
- `abort_unreachable_active_plan`: periodically rechecks an active target so a
  newly invalidated placement can be replanned.

The controller diagnostics count decisions, timeout fallbacks, deadline misses,
unreachable selected placements, replans, emitted input ticks, idle ticks, and
mean policy elapsed time.

## Checkpoint Compatibility

Realtime-native checkpoints should store:

```python
from puyo_env.realtime_ai import realtime_checkpoint_metadata

metadata = realtime_checkpoint_metadata(native_realtime=True)
```

Turn-based actor checkpoints remain usable through the placement adapter when
they expose a `model_state_dict` or a raw actor state dict. Use
`validate_realtime_checkpoint_metadata(checkpoint)` to reject incompatible
contracts with an explicit error.

## Arena And Replay

Run a paired realtime smoke evaluation:

```bash
python eval/realtime_arena.py \
  --policy-a first \
  --policy-b random \
  --games 1 \
  --seed 54 \
  --max-ticks 180 \
  --paired-sides \
  --inference-latency-ticks 1 \
  --timeout-ticks 4
```

Write a deterministic replay for one match:

```bash
python eval/realtime_arena.py \
  --policy-a first \
  --policy-b random \
  --seed 54 \
  --max-ticks 180 \
  --replay docs/benchmarks/puyo-54-realtime-replay.json
```

`eval.realtime_arena.replay_realtime_match` replays the saved tick inputs and
checks every recorded snapshot hash plus the final match hash.

## Realtime Versus UI

Launch an AI v.s. AI realtime viewer:

```bash
python eval/realtime_versus_ui.py \
  --policy-a first \
  --policy-b random \
  --seed 54 \
  --max-ticks 600 \
  --inference-latency-ticks 1 \
  --timeout-ticks 4
```

The viewer drives `RealtimePuyoEnv` at the fixed realtime tick rate and renders
the active pair from `game.puyo_x`, `game.puyo_y`, and `game.puyo_rot`. The side
panel shows the current `TickInput`, active plan cursor, controller event, and
incoming attack deadline.

Run a dummy video smoke test and write a QA artifact:

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python eval/realtime_versus_ui.py \
  --policy-a first \
  --policy-b random \
  --seed 54 \
  --max-ticks 180 \
  --speed 4.0 \
  --max-frames 8 \
  --result-json docs/benchmarks/puyo-54-realtime-ui-smoke.json
```

Manual QA checklist:

- The active pair moves by horizontal, rotation, and down inputs rather than an
  immediate placement jump.
- Target ghost, active plan cursor, and current input update while the match
  clock advances.
- Lock, chain, and ojama labels appear without pausing the realtime tick loop.
- First/random and one search policy, for example `beam`, start from the same
  UI command path.
- Pause, reset, step, and speed controls work in the realtime viewer.
