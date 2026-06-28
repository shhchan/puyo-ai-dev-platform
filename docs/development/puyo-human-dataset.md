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
