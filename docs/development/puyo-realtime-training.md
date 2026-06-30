# Realtime PPO smoke training

`agents.realtime_ppo` connects PUYO-54 fixed-tick realtime matches to the PUYO-55
artifact and exact-resume contract. The trainer still selects placement actions,
but `RealtimeRolloutAdapter` executes each action as low-level `TickInput`
through `RealtimePuyoEnv` and accumulates the fixed-tick reward until player 0 can
make the next placement decision.

Smoke run:

```bash
python3 -m train.train_realtime --config train/config/realtime_smoke.yaml
```

The run directory contains:

| path | role |
| --- | --- |
| `config.yaml` | resolved trainer config |
| `metadata.json` | realtime checkpoint contract metadata |
| `metrics.csv` | PPO losses, episode rewards, latency and failure diagnostics |
| `summary.json` | final metrics and one checkpoint reload/evaluation smoke result |
| `artifact_manifest.json` | PUYO-69 artifact manifest |
| `checkpoints/latest.pt` | PUYO-70 exact-resume checkpoint |

`checkpoints/latest.pt` is saved with trainer name `realtime_ppo`,
`realtime_policy` metadata, optimizer state, RNG state, and `trainer_state`
containing the rollout adapter state. It can be validated with:

```python
from agents.realtime_ppo import validate_realtime_training_checkpoint

validate_realtime_training_checkpoint(
    "runs/realtime_ppo/<run_id>/checkpoints/latest.pt",
    manifest_path="runs/realtime_ppo/<run_id>/artifact_manifest.json",
    require_exact=True,
)
```

Exact resume uses `resume_checkpoint_path`:

```bash
python3 -m train.train_realtime --config train/config/realtime_smoke.yaml \
  --set resume_checkpoint_path=runs/realtime_ppo/<run_id>/checkpoints/latest.pt \
  --set total_timesteps=8
```

Turn-based trainer checkpoints are intentionally rejected by
`validate_realtime_training_checkpoint` with a trainer mismatch. Realtime
metadata is also required with `allow_turn_based_adapter=False`, so a manifest or
checkpoint without the realtime contract fails before it can be used for realtime
training resume.
