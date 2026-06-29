# Human Match Dataset Contract

PUYO-86 adds an immutable, versioned contract for human-versus-AI realtime trajectories.
Each anonymous session is stored below `sessions/<32-hex-session-id>/` with:

- `human_session_manifest.json`: environment/git version, config digest, seed, model and parent-checkpoint lineage, outcome, and trajectory checksum.
- `trajectory.json`: per-tick input edges, decisions, observation references, plans, rewards, and deterministic snapshot hashes.

The current versions are `puyo.human_dataset.v1`, `puyo.human_session_manifest.v1`, and
`puyo.human_trajectory.v1`. A valid session can be replayed to the recorded final hash before
it is admitted to training. `dataset_index.json` is derived data and can always be rebuilt.

## Validation and maintenance

```bash
python3 -m human_data.dataset validate human_datasets/sessions/<session-id>
python3 -m human_data.dataset replay human_datasets/sessions/<session-id>
python3 -m human_data.dataset quarantine human_datasets
python3 -m human_data.dataset rebuild-index human_datasets
python3 -m human_data.dataset delete human_datasets <session-id>
```

Validation rejects unknown schema versions, non-anonymous IDs, missing provenance, config or
trajectory checksum drift, non-contiguous ticks, incomplete player fields, invalid hashes, and
replays that diverge from their recorded snapshots or final state.
Quarantine moves invalid sessions out of `sessions/`, records all reasons, and rebuilds the index.
Deletion accepts only canonical anonymous IDs and also rebuilds the index.

`python3 -m train.lineage --root human_datasets ...` adds environment and dataset-model ancestors
for every valid session manifest, so a dataset can be traced back to its policy/checkpoint and
runtime version.
## Collection controls

Human match collection is OFF by default. The launcher Play settings expose the collection state,
dataset root, and optional feedback before a match starts. During a realtime match, the footer shows
the state and storage scope; press `C` to toggle collection. Enabling collection restarts the match
at tick 0 so the stored trajectory remains deterministically replayable. Disabling it immediately
discards buffered gameplay and does not create a dataset session.

```bash
python3 -m eval.realtime_versus_ui --policy-a human --policy-b greedy \
  --collect-human-data --dataset-root human_datasets --collection-feedback "useful match"
```

Only inputs, board snapshots, AI plans, the result, and optional feedback enter a trajectory. The
`collection_audit.jsonl` file records ON/OFF/save events without gameplay or feedback. Replay data is
kept in memory until session finalization, so interruption cannot leave partial trajectory files;
normal window close and `Ctrl-C` finalize an enabled session as `interrupted`, while stopping
collection discards it.

## Derived challenger training

`train.human_training` validates and deterministically replays every selected session, then
reconstructs the human player's placement action and the observation at the start of that piece.
The available samplers are `imitation`, `advantage_weighted`, and `mixed_replay`. The latter mixes
recorded non-human placements at the configured ratio rather than reading untracked external data.

Run a foreground smoke job with an existing realtime parent checkpoint:

```bash
python3 -m train.human_training run \
  --config train/config/human_derived_smoke.yaml \
  --set run_id=puyo-87-smoke \
  --set dataset_root=human_datasets \
  --set parent_checkpoint_path=runs/realtime_ppo/<parent>/checkpoints/latest.pt \
  --set active_checkpoint_path=runs/realtime_ppo/<parent>/checkpoints/latest.pt \
  --set method=imitation
```

The output is written below `runs/human_training/<run-id>/`: `dataset_selection.json`, resolved
`config.yaml`, `summary.json`, `artifact_manifest.json`, and `checkpoints/challenger.pt`.
The summary monitors train/validation loss, overfit gap, parent-policy KL, and small-dataset bias.
Training only writes a challenger; it hashes the configured active checkpoint before and after the
run and fails if that file changes.

For a queued background job, replace `run` with `submit`. The launcher Training screen exposes the
same dataset, parent, method, and job controls. Jobs can also be controlled from CUI:

```bash
python3 -m train.human_training status --job-id puyo-87-smoke
python3 -m train.human_training pause --job-id puyo-87-smoke
python3 -m train.human_training resume --job-id puyo-87-smoke
python3 -m train.human_training cancel --job-id puyo-87-smoke
```

Job records and logs are stored under `runs/human_training/jobs/`. A cancelled or failed job never
updates an active-model registry; promotion is a separate evaluation-gate responsibility.
