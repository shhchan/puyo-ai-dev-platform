"""Human-dataset derived-model training and persistent background job control."""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

try:
    import numpy as np
    import torch
    from torch import optim
    from torch.nn import functional as F
except ImportError:  # pragma: no cover - dependency guard
    np = None
    torch = None
    optim = None
    F = None

from agents.networks import PuyoActorCritic, VECTOR_FEATURE_DIM
from agents.realtime_ppo import stack_realtime_masks, stack_realtime_observations, validate_realtime_training_checkpoint
from human_data.sampler import TRAINING_METHODS, PlacementSample, sample_human_dataset
from human_data.audit import append_audit_event
from puyo_env.realtime_ai import realtime_checkpoint_metadata
from train.artifacts import attach_checkpoint_schema, file_sha256, git_commit, utc_timestamp, write_artifact_manifest
from train.restore import checkpoint_state_hash


JOB_SCHEMA_VERSION = "puyo.human_training_job.v1"
SUMMARY_SCHEMA_VERSION = "puyo.human_training_summary.v1"


@dataclass
class HumanTrainingConfig:
    seed: int = 87
    run_id: str = "puyo-87-smoke"
    log_dir: str = "runs/human_training"
    dataset_root: str = "human_datasets"
    session_ids: tuple[str, ...] = ()
    parent_checkpoint_path: str = ""
    active_checkpoint_path: str = ""
    method: str = "imitation"
    self_play_ratio: float = 0.25
    minimum_advantage: float = 0.5
    epochs: int = 2
    batch_size: int = 32
    learning_rate: float = 0.0001
    validation_ratio: float = 0.2
    small_dataset_threshold: int = 32
    overfit_gap_threshold: float = 0.5
    forgetting_kl_threshold: float = 1.0
    device: str = "cpu"


def _require_deps() -> None:
    if np is None or torch is None or optim is None or F is None:
        raise ImportError("human training requires numpy and torch")


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    return safe.strip("-") or "human-derived"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _coerce(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        return raw.lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, tuple):
        return tuple(part for part in raw.split(",") if part)
    return raw


def load_config(path: str | Path, overrides: Sequence[str] = ()) -> HumanTrainingConfig:
    target = Path(path)
    values = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(values, dict):
        raise ValueError(f"{target} must contain a YAML mapping")
    defaults = HumanTrainingConfig()
    valid = {field.name for field in fields(HumanTrainingConfig)}
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"override must be KEY=VALUE: {override}")
        key, raw = override.split("=", 1)
        if key not in valid:
            raise ValueError(f"unknown config field: {key}")
        values[key] = _coerce(raw, getattr(defaults, key))
    if isinstance(values.get("session_ids"), list):
        values["session_ids"] = tuple(str(item) for item in values["session_ids"])
    return HumanTrainingConfig(**values)


def validate_config(config: HumanTrainingConfig) -> None:
    if not config.parent_checkpoint_path:
        raise ValueError("parent_checkpoint_path is required")
    if config.method not in TRAINING_METHODS:
        raise ValueError(f"method must be one of: {', '.join(TRAINING_METHODS)}")
    if config.epochs <= 0 or config.batch_size <= 0:
        raise ValueError("epochs and batch_size must be positive")
    if not 0.0 <= config.validation_ratio < 1.0:
        raise ValueError("validation_ratio must be in [0, 1)")
    if not 0.0 <= config.self_play_ratio <= 1.0:
        raise ValueError("self_play_ratio must be between 0 and 1")


def _batch(samples: Sequence[PlacementSample], device: str):
    observations = stack_realtime_observations([sample.observation for sample in samples], device)
    masks = stack_realtime_masks([{"action_mask": sample.action_mask} for sample in samples], device)
    actions = torch.as_tensor([sample.action_index for sample in samples], dtype=torch.long, device=device)
    weights = torch.as_tensor([sample.weight for sample in samples], dtype=torch.float32, device=device)
    return observations, masks, actions, weights


def _loss(agent, samples: Sequence[PlacementSample], device: str) -> float:
    if not samples:
        return 0.0
    with torch.no_grad():
        observations, masks, actions, weights = _batch(samples, device)
        logits, _ = agent.forward(observations["board"], observations["vector_features"], action_mask=masks)
        losses = F.cross_entropy(logits, actions, reduction="none")
        return float((losses * weights).sum().item() / max(float(weights.sum().item()), 1e-9))


def train_human_derived(config: HumanTrainingConfig) -> dict[str, Any]:
    """Warm-start from a parent and write a challenger without changing active model state."""
    _require_deps()
    validate_config(config)
    audit_path = Path(config.log_dir) / "audit_events.jsonl"
    append_audit_event(
        audit_path,
        event="training.started",
        resource_type="derived_run",
        resource_id=_safe_name(config.run_id),
        status="started",
        details={"session_ids": list(config.session_ids), "method": config.method},
    )
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    device = torch.device(config.device)
    parent_path = Path(config.parent_checkpoint_path).resolve()
    active_path = Path(config.active_checkpoint_path).resolve() if config.active_checkpoint_path else None
    parent = validate_realtime_training_checkpoint(parent_path, map_location=device)
    parent_digest = file_sha256(parent_path)
    active_digest_before = file_sha256(active_path) if active_path else None
    sampled = sample_human_dataset(
        config.dataset_root,
        session_ids=config.session_ids,
        method=config.method,
        self_play_ratio=config.self_play_ratio,
        minimum_advantage=config.minimum_advantage,
        seed=config.seed,
    )
    samples = list(sampled.samples)
    split = 0 if len(samples) == 1 else max(1, min(len(samples) - 1, round(len(samples) * config.validation_ratio)))
    validation_samples = samples[:split]
    training_samples = samples[split:] or samples
    agent = PuyoActorCritic(
        board_shape=tuple(parent["board_shape"]),
        vector_dim=int(parent.get("vector_dim", VECTOR_FEATURE_DIM)),
    ).to(device)
    agent.load_state_dict(parent["model_state_dict"])
    parent_agent = copy.deepcopy(agent).eval()
    optimizer = optim.Adam(agent.parameters(), lr=config.learning_rate)
    initial_train_loss = _loss(agent, training_samples, str(device))
    initial_validation_loss = _loss(agent, validation_samples, str(device))
    indices = list(range(len(training_samples)))
    for _ in range(config.epochs):
        random.shuffle(indices)
        for start in range(0, len(indices), config.batch_size):
            selected = [training_samples[index] for index in indices[start : start + config.batch_size]]
            observations, masks, actions, weights = _batch(selected, str(device))
            logits, _ = agent.forward(observations["board"], observations["vector_features"], action_mask=masks)
            losses = F.cross_entropy(logits, actions, reduction="none")
            loss = (losses * weights).sum() / weights.sum().clamp_min(1e-9)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(agent.parameters(), 0.5)
            optimizer.step()
    final_train_loss = _loss(agent, training_samples, str(device))
    final_validation_loss = _loss(agent, validation_samples, str(device))
    probe_samples = validation_samples or training_samples[: min(32, len(training_samples))]
    with torch.no_grad():
        observations, masks, _, _ = _batch(probe_samples, str(device))
        parent_logits, _ = parent_agent.forward(observations["board"], observations["vector_features"], action_mask=masks)
        derived_logits, _ = agent.forward(observations["board"], observations["vector_features"], action_mask=masks)
        parent_prob = parent_logits.softmax(dim=-1)
        forgetting_kl = float(
            F.kl_div(derived_logits.log_softmax(dim=-1), parent_prob, reduction="batchmean").item()
        )

    run_id = _safe_name(config.run_id)
    run_dir = Path(config.log_dir) / run_id
    checkpoint_path = run_dir / "checkpoints" / "challenger.pt"
    selection_path = run_dir / "dataset_selection.json"
    config_path = run_dir / "config.yaml"
    summary_path = run_dir / "summary.json"
    manifest_path = run_dir / "artifact_manifest.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(selection_path, sampled.selection)
    config_path.write_text(yaml.safe_dump(asdict(config), sort_keys=True), encoding="utf-8")
    warnings = []
    if len(samples) < config.small_dataset_threshold:
        warnings.append("small_dataset_bias")
    overfit_gap = final_validation_loss - final_train_loss if validation_samples else 0.0
    if validation_samples and overfit_gap > config.overfit_gap_threshold:
        warnings.append("overfit_gap")
    if forgetting_kl > config.forgetting_kl_threshold:
        warnings.append("catastrophic_forgetting_risk")
    active_digest_after = file_sha256(active_path) if active_path else None
    if active_digest_before != active_digest_after:
        raise RuntimeError("active checkpoint changed during human-derived training")
    global_step = config.epochs * len(training_samples)
    checkpoint = attach_checkpoint_schema(
        {
            "model_state_dict": agent.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "config": asdict(config),
            "run_id": run_id,
            "global_step": global_step,
            "board_shape": agent.board_shape,
            "vector_dim": agent.vector_dim,
            "realtime_policy": realtime_checkpoint_metadata(native_realtime=True),
            "human_training": {
                "method": config.method,
                "dataset_selection_path": str(selection_path),
                "parent_checkpoint_sha256": parent_digest,
            },
        },
        trainer_name="realtime_ppo",
        run_id=run_id,
        checkpoint_kind="challenger",
        global_step=global_step,
        config=asdict(config),
        git_commit=git_commit(),
        seed=config.seed,
        parent_checkpoint_path=str(parent_path),
        environment_progress={"human_samples": len(samples), "epochs": config.epochs},
    )
    checkpoint["state_hash"] = checkpoint_state_hash(checkpoint)
    torch.save(checkpoint, checkpoint_path)
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at_utc": utc_timestamp(),
        "method": config.method,
        "seed": config.seed,
        "parent_checkpoint_path": str(parent_path),
        "parent_checkpoint_sha256": parent_digest,
        "active_checkpoint_path": str(active_path) if active_path else None,
        "active_checkpoint_unchanged": active_digest_before == active_digest_after,
        "dataset_selection_path": str(selection_path),
        "session_ids": [record["session_id"] for record in sampled.selection["sessions"]],
        "sample_count": len(samples),
        "training_sample_count": len(training_samples),
        "validation_sample_count": len(validation_samples),
        "initial_train_loss": initial_train_loss,
        "final_train_loss": final_train_loss,
        "initial_validation_loss": initial_validation_loss,
        "final_validation_loss": final_validation_loss,
        "overfit_gap": overfit_gap,
        "parent_policy_kl": forgetting_kl,
        "warnings": warnings,
        "checkpoint_path": str(checkpoint_path),
    }
    _write_json(summary_path, summary)
    write_artifact_manifest(
        run_dir=run_dir,
        run_id=run_id,
        trainer_name="human_derived_realtime",
        config=asdict(config),
        git_commit=git_commit(),
        seed=config.seed,
        artifacts={"config": config_path, "dataset_selection": selection_path, "summary": summary_path},
        checkpoints={"challenger": checkpoint_path},
        manifest_path=manifest_path,
        parent_checkpoint_path=str(parent_path),
        extra={
            "human_training": sampled.selection,
            "active_checkpoint_unchanged": active_digest_before == active_digest_after,
            "monitoring": {"overfit_gap": overfit_gap, "parent_policy_kl": forgetting_kl, "warnings": warnings},
        },
    )
    validate_realtime_training_checkpoint(checkpoint_path, map_location=device)
    append_audit_event(
        audit_path,
        event="training.completed",
        resource_type="derived_run",
        resource_id=run_id,
        details={
            "session_ids": summary["session_ids"],
            "checkpoint_path": str(checkpoint_path),
            "parent_checkpoint_sha256": parent_digest,
            "active_checkpoint_unchanged": summary["active_checkpoint_unchanged"],
        },
    )
    return {**summary, "run_dir": str(run_dir), "manifest_path": str(manifest_path)}


def _job_path(job_root: str | Path, job_id: str) -> Path:
    return Path(job_root) / f"{_safe_name(job_id)}.json"


def submit_job(config: HumanTrainingConfig, *, job_root: str | Path = "runs/human_training/jobs") -> dict[str, Any]:
    validate_config(config)
    job_id = _safe_name(config.run_id)
    path = _job_path(job_root, job_id)
    if path.exists() and _read_job(path).get("state") in {"queued", "running", "paused"}:
        raise ValueError(f"job is already active: {job_id}")
    record = {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": job_id,
        "state": "queued",
        "config": asdict(config),
        "submitted_at_utc": utc_timestamp(),
        "pid": None,
    }
    _write_json(path, record)
    log_path = path.with_suffix(".log")
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "train.human_training", "worker", "--job-file", str(path)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    record = _read_job(path)
    record.update({"pid": process.pid, "log_path": str(log_path)})
    _write_json(path, record)
    return record


def _read_job(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != JOB_SCHEMA_VERSION:
        raise ValueError(f"invalid job record: {path}")
    return value


def control_job(job_id: str, operation: str, *, job_root: str | Path = "runs/human_training/jobs") -> dict[str, Any]:
    path = _job_path(job_root, job_id)
    record = _read_job(path)
    pid = record.get("pid")
    if operation == "status":
        return record
    transitions = {
        "pause": ({"running"}, signal.SIGSTOP, "paused"),
        "resume": ({"paused"}, signal.SIGCONT, "running"),
        "cancel": ({"queued", "running", "paused"}, signal.SIGTERM, "cancelled"),
    }
    if operation not in transitions:
        raise ValueError("operation must be pause, resume, cancel, or status")
    allowed, job_signal, target = transitions[operation]
    if record.get("state") not in allowed or not isinstance(pid, int):
        raise ValueError(f"cannot {operation} job in state {record.get('state')}")
    if operation == "cancel" and record.get("state") == "paused":
        os.kill(pid, signal.SIGCONT)
    os.kill(pid, job_signal)
    record["state"] = target
    record[f"{target}_at_utc"] = utc_timestamp()
    _write_json(path, record)
    return record


def run_worker(job_file: str | Path) -> None:
    path = Path(job_file)
    record = _read_job(path)
    record.update({"state": "running", "started_at_utc": utc_timestamp(), "pid": os.getpid()})
    _write_json(path, record)
    try:
        result = train_human_derived(HumanTrainingConfig(**record["config"]))
    except Exception as exc:
        record.update({"state": "failed", "finished_at_utc": utc_timestamp(), "error": str(exc)})
        _write_json(path, record)
        config = HumanTrainingConfig(**record["config"])
        append_audit_event(
            Path(config.log_dir) / "audit_events.jsonl",
            event="training.failed",
            resource_type="derived_run",
            resource_id=_safe_name(config.run_id),
            status="failed",
            details={"error_type": type(exc).__name__},
        )
        raise
    latest = _read_job(path)
    if latest.get("state") != "cancelled":
        record.update({"state": "completed", "finished_at_utc": utc_timestamp(), "result": result})
        _write_json(path, record)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and control human-derived realtime challengers.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "submit"):
        child = subparsers.add_parser(name)
        child.add_argument("--config", default="train/config/human_derived_smoke.yaml")
        child.add_argument("--set", action="append", default=[])
        child.add_argument("--job-root", default="runs/human_training/jobs")
    worker = subparsers.add_parser("worker")
    worker.add_argument("--job-file", required=True)
    for name in ("status", "pause", "resume", "cancel"):
        child = subparsers.add_parser(name)
        child.add_argument("--job-id", required=True)
        child.add_argument("--job-root", default="runs/human_training/jobs")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "worker":
        run_worker(args.job_file)
        return
    if args.command in {"status", "pause", "resume", "cancel"}:
        result = control_job(args.job_id, args.command, job_root=args.job_root)
    else:
        config = load_config(args.config, args.set)
        result = train_human_derived(config) if args.command == "run" else submit_job(config, job_root=args.job_root)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
