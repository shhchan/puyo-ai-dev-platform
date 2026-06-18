"""Shared artifact and checkpoint contracts for training runs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ARTIFACT_MANIFEST_SCHEMA_VERSION = "puyo.artifact_manifest.v1"
CHECKPOINT_SCHEMA_VERSION = "puyo.checkpoint.v1"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(root: str | Path | None = None) -> str:
    repo_root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _display_path(path: Path, run_dir: Path) -> str:
    try:
        return str(path.relative_to(run_dir))
    except ValueError:
        pass
    try:
        return str(path.resolve().relative_to(run_dir.resolve()))
    except ValueError:
        return str(path)


def infer_artifact_type(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".pt":
        return "torch_checkpoint"
    if suffix == ".json":
        return "json"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".csv":
        return "csv"
    if suffix == ".md":
        return "markdown"
    return suffix.lstrip(".") or "file"


def describe_artifact(
    path: str | Path,
    *,
    run_dir: str | Path,
    role: str,
    artifact_type: str | None = None,
    required: bool = True,
) -> dict[str, Any]:
    target = Path(path)
    record: dict[str, Any] = {
        "role": role,
        "artifact_type": artifact_type or infer_artifact_type(target),
        "path": _display_path(target, Path(run_dir)),
        "required": bool(required),
        "exists": target.exists(),
    }
    if target.is_file():
        record["size_bytes"] = target.stat().st_size
        record["sha256"] = file_sha256(target)
    return record


def build_checkpoint_schema(
    *,
    trainer_name: str,
    run_id: str,
    checkpoint_kind: str,
    global_step: int,
    config: Mapping[str, Any],
    git_commit: str,
    seed: int | None,
    parent_checkpoint_path: str | None = None,
    has_optimizer_state: bool = True,
    has_rng_state: bool = False,
    has_trainer_state: bool = False,
    environment_progress: Mapping[str, Any] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "trainer_name": trainer_name,
        "run_id": run_id,
        "checkpoint_kind": checkpoint_kind,
        "global_step": int(global_step),
        "created_at_utc": created_at_utc or utc_timestamp(),
        "git_commit": git_commit or "unknown",
        "seed": seed,
        "config_digest": json_digest(dict(config)),
        "parent_checkpoint_path": parent_checkpoint_path or None,
        "resume_contract": {
            "model_state_key": "model_state_dict",
            "optimizer_state_key": "optimizer_state_dict" if has_optimizer_state else None,
            "rng_state_key": "rng_state" if has_rng_state else None,
            "trainer_state_key": "trainer_state" if has_trainer_state else None,
            "has_optimizer_state": bool(has_optimizer_state),
            "has_rng_state": bool(has_rng_state),
            "has_trainer_state": bool(has_trainer_state),
            "environment_progress": dict(environment_progress or {}),
        },
    }


def attach_checkpoint_schema(
    payload: Mapping[str, Any],
    *,
    trainer_name: str,
    run_id: str,
    checkpoint_kind: str,
    global_step: int,
    config: Mapping[str, Any],
    git_commit: str,
    seed: int | None,
    parent_checkpoint_path: str | None = None,
    environment_progress: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(payload)
    result["artifact_schema_version"] = CHECKPOINT_SCHEMA_VERSION
    result["checkpoint_schema"] = build_checkpoint_schema(
        trainer_name=trainer_name,
        run_id=run_id,
        checkpoint_kind=checkpoint_kind,
        global_step=global_step,
        config=config,
        git_commit=git_commit,
        seed=seed,
        parent_checkpoint_path=parent_checkpoint_path,
        has_optimizer_state=result.get("optimizer_state_dict") is not None,
        has_rng_state=result.get("rng_state") is not None,
        has_trainer_state=result.get("trainer_state") is not None,
        environment_progress=environment_progress,
    )
    return result


def validate_checkpoint_payload(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    schema = payload.get("checkpoint_schema")
    if not isinstance(schema, Mapping):
        return ["missing checkpoint_schema"]
    for key in (
        "schema_version",
        "trainer_name",
        "run_id",
        "checkpoint_kind",
        "global_step",
        "git_commit",
        "config_digest",
        "resume_contract",
    ):
        if key not in schema:
            errors.append(f"checkpoint_schema.{key} is required")
    if schema.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        errors.append("checkpoint_schema.schema_version is unsupported")
    if "model_state_dict" not in payload:
        errors.append("model_state_dict is required")
    resume_contract = schema.get("resume_contract")
    if isinstance(resume_contract, Mapping) and resume_contract.get("has_optimizer_state"):
        if "optimizer_state_dict" not in payload:
            errors.append("optimizer_state_dict is required by resume_contract")
    if isinstance(resume_contract, Mapping) and resume_contract.get("has_rng_state"):
        if "rng_state" not in payload:
            errors.append("rng_state is required by resume_contract")
    if isinstance(resume_contract, Mapping) and resume_contract.get("has_trainer_state"):
        if "trainer_state" not in payload:
            errors.append("trainer_state is required by resume_contract")
    return errors


def write_artifact_manifest(
    *,
    run_dir: str | Path,
    run_id: str,
    trainer_name: str,
    config: Mapping[str, Any],
    git_commit: str,
    seed: int | None,
    artifacts: Mapping[str, str | Path | None],
    checkpoints: Mapping[str, str | Path | None],
    manifest_path: str | Path | None = None,
    parent_checkpoint_path: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(run_dir)
    target = Path(manifest_path) if manifest_path is not None else root / "artifact_manifest.json"
    artifact_records = [
        describe_artifact(path, run_dir=root, role=role)
        for role, path in artifacts.items()
        if path is not None
    ]
    checkpoint_records = [
        describe_artifact(path, run_dir=root, role=role, artifact_type="torch_checkpoint")
        for role, path in checkpoints.items()
        if path is not None
    ]
    manifest = {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "run": {
            "run_id": run_id,
            "trainer_name": trainer_name,
            "seed": seed,
            "git_commit": git_commit or "unknown",
            "config_digest": json_digest(dict(config)),
            "parent_checkpoint_path": parent_checkpoint_path or None,
        },
        "artifacts": artifact_records,
        "checkpoints": checkpoint_records,
        "extra": dict(extra or {}),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def validate_artifact_manifest(manifest: Mapping[str, Any], *, run_dir: str | Path) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != ARTIFACT_MANIFEST_SCHEMA_VERSION:
        errors.append("manifest schema_version is unsupported")
    root = Path(run_dir)
    for section in ("artifacts", "checkpoints"):
        records = manifest.get(section, [])
        if not isinstance(records, list):
            errors.append(f"{section} must be a list")
            continue
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                errors.append(f"{section}[{index}] must be an object")
                continue
            record_path = record.get("path")
            if not isinstance(record_path, str):
                errors.append(f"{section}[{index}].path is required")
                continue
            path = Path(record_path)
            if not path.is_absolute():
                path = root / path
            if not path.exists():
                if record.get("required", True):
                    errors.append(f"{section}[{index}] is missing: {record_path}")
                continue
            if path.is_file() and record.get("sha256") and file_sha256(path) != record["sha256"]:
                errors.append(f"{section}[{index}] sha256 mismatch: {record_path}")
    return errors
