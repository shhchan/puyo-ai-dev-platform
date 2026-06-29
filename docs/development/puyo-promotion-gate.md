# Challenger promotion gate

`eval.promotion_gate` evaluates a PUYO-87 challenger against the current champion before any active
model role changes. The registry is a single atomic JSON document containing the `champion`,
`challenger`, and `previous_stable` roles, evaluation/transition history, and bounded opponent pool.

## Evaluation contract

`train/config/promotion_gate.yaml` is the reproducibility contract. It fixes arena and tactical seed
sets, maximum ticks, device, criteria, and opponent-pool retention. The gate runs paired-side realtime
matches for both seed sets and records:

- challenger score rate in the fixed-seed arena;
- challenger and champion tactical-seed score rates;
- challenger and champion mean maximum chain;
- unreachable/timeout operation failure rate and deadline-miss rate;
- mean policy decision latency.

All criteria must pass. A rejected challenger remains visible as `challenger` but never replaces
`champion`. A promotion atomically moves the old champion to `previous_stable`, moves the challenger
to `champion`, clears `challenger`, appends the displaced stable model to the opponent pool, and records
the immutable evaluation artifact. A file lock rejects a completed evaluation if another process has
changed the champion in the meantime. The lock-held update also verifies the champion and challenger
SHA-256 values so a replaced or damaged checkpoint cannot be activated. Rollback performs the same
integrity check for `champion` and `previous_stable`.

The evaluation ID is derived from both checkpoint hashes and the complete gate config. Repeating an
already recorded evaluation is idempotent and returns the same decision without another role update.

## Commands

Initialize the registry once with the currently deployed realtime checkpoint:

```bash
python3 -m eval.promotion_gate --registry runs/model_registry.json init \
  --champion runs/realtime/<active-run>/checkpoints/latest.pt
```

Evaluate a human-derived challenger and apply promotion or rejection:

```bash
python3 -m eval.promotion_gate --registry runs/model_registry.json evaluate \
  --challenger runs/human_training/<run-id>/checkpoints/challenger.pt \
  --config train/config/promotion_gate.yaml \
  --output-dir runs/promotion_gate
```

Inspect current roles and the evaluation artifact:

```bash
python3 -m eval.promotion_gate --registry runs/model_registry.json status
python3 -m eval.model_viewer \
  --lineage-root runs \
  --model-registry runs/model_registry.json \
  --report-json /tmp/puyo-88-model-status.json \
  --report-markdown /tmp/puyo-88-model-status.md \
  --max-frames 1
```

Rollback immediately to the previous stable checkpoint:

```bash
python3 -m eval.promotion_gate --registry runs/model_registry.json rollback \
  --reason "production deadline-miss regression"
```

`python3 main.py` opens the launcher. Select **Model viewer** to inspect the three roles and the most
recent promotion, rejection, or rollback alongside lineage. The evaluation backend can also be
verified without a GUI:

```bash
python3 -m unittest tests.test_promotion_gate tests.test_model_viewer -q
```
