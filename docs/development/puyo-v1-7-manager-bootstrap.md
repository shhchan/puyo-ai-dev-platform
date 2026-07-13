# v1.7.1 Manager Bootstrap Training

PUYO-126 adds the reproducible behavior-cloning pipeline for the learned v1.7.1
Strategy Manager. It consumes a dataset produced by
`train.v1_7_bootstrap_dataset`, verifies its checksums and complete feature
contract, reruns the PUYO-153 analyzer scenarios, and only then trains and saves
a checkpoint.

## Run

```bash
python3 -m train.train_v1_7_manager \
  --config train/config/v1_7_manager_bootstrap.yaml
```

The checked-in config uses the PUYO-125 smoke dataset so the command is
reviewable and reproducible. That dataset validates the pipeline rather than
model quality. For a real bootstrap run, first build a larger current-schema
dataset and override its path:

```bash
python3 -m train.train_v1_7_manager \
  --config train/config/v1_7_manager_bootstrap.yaml \
  --set dataset_dir=runs/v1_7_bootstrap_dataset/<dataset-id> \
  --set run_id=<run-id>
```

By default, any `legacy` or `rejected` record aborts before the run directory or
checkpoint is created. An audited mixed dataset may be used only with
`allow_audited_nontraining_records=true`; those records remain excluded and the
counts and reasons are copied into the summary, checkpoint, and manifest.

## Outputs

The run directory contains:

- `config.yaml`: resolved training config.
- `metrics.json`: epoch and final loss, tactic accuracy, and aggregate parameter error.
- `confusion_report.json`: teacher/prediction matrix and per-tactic precision, recall, and F1.
- `parameter_report.json`: numeric MAE/RMSE and discrete accuracy by tactic parameter.
- `scenario_report.json`: PUYO-153 analyzer results plus model predictions for all fixed scenarios.
- `checkpoints/bootstrap.pt`: model/optimizer state, model and policy identity, config digest, git commit, parent lineage, dataset provenance, schema snapshots, feature shape, lifecycle/carry contract, and scenario status.
- `artifact_manifest.json`: paths, sizes, and SHA-256 checksums for all artifacts.

Training artifacts are written below the ignored `runs/` directory and binary
checkpoints are not committed.

## Load and play

The saved model is registered as `v1_7_bootstrap_manager`. It always requires a
checkpoint path:

```bash
python3 -m eval.arena \
  --policy-a v1_7_bootstrap_manager \
  --checkpoint-a runs/v1_7_manager/<run-id>/checkpoints/bootstrap.pt \
  --policy-b v1_7_analyzer_manager \
  --games 2 \
  --max-steps 40
```

The same policy can be selected in the realtime versus UI:

```bash
python3 -m eval.realtime_versus_ui \
  --policy-a human \
  --policy-b v1_7_bootstrap_manager \
  --checkpoint-b runs/v1_7_manager/<run-id>/checkpoints/bootstrap.pt \
  --seed 123
```

`python3 main.py` exposes the same policy in the play, spectate, and arena
settings. Select `v1_7_bootstrap_manager` on a side and set its corresponding
checkpoint field before starting the workflow.

Loading fails before any weights are applied when the checkpoint does not match
the current policy. The error reports expected and actual values for the model
family/version, policy type, schema and tactic registry versions, ordered feature
contract, lifecycle/carry contract, tensor keys/shapes, config digest, or state
hash. A legacy checkpoint without the v1.7.1 compatibility metadata must be
regenerated with the command above; it is not padded or migrated implicitly.
