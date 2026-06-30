# Human data audit and safe deletion

PUYO-89 provides a CUI audit and deletion workflow. There is no dedicated GUI view in this version.
Collection controls, dataset import/quarantine, derived training, evaluation, promotion, rollback,
and deletion write privacy-safe events. Events contain actor, UTC time, resource ID, status, and
checksums or paths needed for provenance; trajectory contents and optional feedback are excluded.

## Audit report

Generate both machine-readable JSON and a reviewable Markdown report:

```bash
python3 -m human_data.audit report \
  --dataset-root human_datasets \
  --training-root runs/human_training \
  --registry runs/model_registry.json \
  --output-json /tmp/puyo-89-audit.json \
  --output-markdown /tmp/puyo-89-audit.md
```

The report links anonymous sessions to derived runs, challenger and parent checkpoint paths, model
roles, evaluation records, and promotion/rollback transitions. Set `PUYO_AUDIT_ACTOR` when the OS
account is not the desired audit identity.

## Deletion procedure

Deletion is deliberately two-step. First generate and review a plan:

```bash
python3 -m human_data.audit plan-delete \
  --dataset-root human_datasets \
  --training-root runs/human_training \
  --registry runs/model_registry.json \
  --session-id <32-hex-session-id> \
  --output /tmp/puyo-89-deletion-plan.json
python3 -m json.tool /tmp/puyo-89-deletion-plan.json
```

Do not continue when `blocked` is true. A plan is blocked when its derived checkpoint is the
champion, challenger, previous stable model, or an opponent-pool member. Retire that registry
reference through the model lifecycle before generating a new plan. Parent checkpoints are never
deletion targets.

For an unblocked plan, copy its `confirmation_token` exactly:

```bash
python3 -m human_data.audit execute-delete \
  --plan /tmp/puyo-89-deletion-plan.json \
  --confirm <confirmation-token>
```

Execution recomputes the plan before modifying files. A stale plan, bad token, new registry
reference, registry checksum change, or file operation failure aborts the operation. Partial moves
are rolled back. Successful deletion moves the session and all linked derived runs to
`human_datasets/deletion_trash/<confirmation-token>/`, rebuilds the dataset index, preserves the
model registry and parent checkpoints, and appends a deletion event. Trash retention and permanent
destruction are an operator policy outside this command.

## Fault and safety verification

The following tests cover collection OFF, corrupt dataset quarantine, job failure, deletion disk
errors, stale/referenced deletion plans, evaluation rejection, audit-write failure, active-model
integrity, and collection-to-training-to-promotion-to-rollback audit reporting:

```bash
python3 -m unittest \
  tests.test_realtime_versus_ui \
  tests.test_human_dataset \
  tests.test_human_training \
  tests.test_promotion_gate \
  tests.test_human_audit -q
```

For full regression coverage:

```bash
python3 -m unittest discover -s tests -q
```
