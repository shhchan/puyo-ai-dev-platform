"""Storage, validation, replay, and indexing for human-match trajectories."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
from pathlib import Path
from typing import Any, Mapping

from eval.realtime_arena import replay_realtime_match
from train.artifacts import file_sha256, git_commit, json_digest, utc_timestamp

DATASET_VERSION = "puyo.human_dataset.v1"
TRAJECTORY_SCHEMA_VERSION = "puyo.human_trajectory.v1"
SESSION_MANIFEST_SCHEMA_VERSION = "puyo.human_session_manifest.v1"
DATASET_INDEX_SCHEMA_VERSION = "puyo.human_dataset_index.v1"
ENVIRONMENT_FORMAT_VERSION = "puyo-realtime-match-v1"
AGENTS = ("player_0", "player_1")
SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _session_path(dataset_root: str | Path, session_id: str) -> Path:
    if not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("session_id must be 32 lowercase hexadecimal characters")
    return Path(dataset_root) / "sessions" / session_id


def _model_record(value: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint_path = value.get("checkpoint_path")
    checkpoint_sha256 = value.get("checkpoint_sha256")
    if checkpoint_path and not checkpoint_sha256:
        path = Path(str(checkpoint_path))
        if path.is_file():
            checkpoint_sha256 = file_sha256(path)
    return {
        "policy": str(value.get("policy") or "human"),
        "model_id": value.get("model_id"),
        "checkpoint_path": checkpoint_path,
        "checkpoint_sha256": checkpoint_sha256,
        "parent_checkpoint_path": value.get("parent_checkpoint_path"),
    }


def trajectory_from_replay(
    replay: Mapping[str, Any],
    *,
    session_id: str,
    outcome: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert the established realtime replay format into the dataset contract."""
    ticks = []
    for entry in replay.get("ticks", ()):
        diagnostics = entry.get("policy_diagnostics", {})
        statuses = entry.get("controller_status", {})
        snapshot_hash = entry.get("snapshot_hash")
        ticks.append(
            {
                "tick": int(entry["tick"]),
                "inputs": {
                    agent: dict(entry.get("inputs", {}).get(agent, {"press": [], "release": []}))
                    for agent in AGENTS
                },
                "decisions": {
                    agent: {
                        "action_index": statuses.get(agent, {}).get("active_action_index"),
                        "plan_id": diagnostics.get(agent, {}).get("plan_id") or None,
                    }
                    for agent in AGENTS
                },
                "observation_refs": {
                    agent: {"kind": "post_tick_match_snapshot", "sha256": snapshot_hash}
                    for agent in AGENTS
                },
                "plans": {
                    agent: diagnostics.get(agent, {}).get("plan", {})
                    for agent in AGENTS
                },
                "rewards": {agent: 0.0 for agent in AGENTS},
                "snapshot_hash": snapshot_hash,
            }
        )
    result_outcome = dict(outcome or {})
    result_outcome.setdefault("winner", None)
    result_outcome["expected_final_hash"] = replay.get("expected_final_hash")
    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "session_id": session_id,
        "environment_format": replay.get("format", ENVIRONMENT_FORMAT_VERSION),
        "seed": replay.get("seed"),
        "max_ticks": replay.get("max_ticks"),
        "ticks": ticks,
        "outcome": result_outcome,
    }


def create_session(
    dataset_root: str | Path,
    replay: Mapping[str, Any],
    *,
    models: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
    environment: Mapping[str, Any] | None = None,
    outcome: Mapping[str, Any] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Persist one immutable session and rebuild the dataset index."""
    session_id = session_id or secrets.token_hex(16)
    session_dir = _session_path(dataset_root, session_id)
    if session_dir.exists():
        raise FileExistsError(f"session already exists: {session_id}")
    trajectory = trajectory_from_replay(replay, session_id=session_id, outcome=outcome)
    trajectory_path = session_dir / "trajectory.json"
    _write_json(trajectory_path, trajectory)
    environment_record = {
        "name": "puyo-realtime-versus",
        "format_version": ENVIRONMENT_FORMAT_VERSION,
        "git_commit": git_commit(),
        **dict(environment or {}),
    }
    manifest = {
        "schema_version": SESSION_MANIFEST_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "session_id": session_id,
        "created_at_utc": utc_timestamp(),
        "seed": replay.get("seed"),
        "environment": environment_record,
        "config": dict(config),
        "config_digest": json_digest(dict(config)),
        "models": {agent: _model_record(models.get(agent, {})) for agent in AGENTS},
        "trajectory": {
            "path": "trajectory.json",
            "sha256": file_sha256(trajectory_path),
            "size_bytes": trajectory_path.stat().st_size,
            "ticks": len(trajectory["ticks"]),
        },
        "outcome": trajectory["outcome"],
    }
    _write_json(session_dir / "human_session_manifest.json", manifest)
    errors = validate_session(session_dir, verify_replay=True)
    if errors:
        shutil.rmtree(session_dir)
        raise ValueError("invalid session: " + "; ".join(errors))
    rebuild_index(dataset_root)
    return manifest


def _validate_agent_mapping(
    errors: list[str],
    value: Any,
    field: str,
    tick_index: int,
) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"ticks[{tick_index}].{field} must be an object")
        return
    for agent in AGENTS:
        if agent not in value:
            errors.append(f"ticks[{tick_index}].{field}.{agent} is required")


def _replay_trajectory(trajectory: Mapping[str, Any]) -> str:
    replay = {
        "format": trajectory["environment_format"],
        "seed": trajectory["seed"],
        "ticks": [
            {
                "tick": tick["tick"],
                "inputs": tick["inputs"],
                "snapshot_hash": tick["snapshot_hash"],
            }
            for tick in trajectory["ticks"]
        ],
        "expected_final_hash": trajectory["outcome"]["expected_final_hash"],
    }
    return replay_realtime_match(replay)


def validate_session(session_dir: str | Path, *, verify_replay: bool = False) -> list[str]:
    """Return all schema, checksum, and replay-precondition errors for a session."""
    root = Path(session_dir)
    manifest_path = root / "human_session_manifest.json"
    trajectory_path = root / "trajectory.json"
    try:
        manifest = _read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"manifest is unreadable: {exc}"]
    try:
        trajectory = _read_json(trajectory_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"trajectory is unreadable: {exc}"]

    errors: list[str] = []
    if manifest.get("schema_version") != SESSION_MANIFEST_SCHEMA_VERSION:
        errors.append("manifest schema_version is unsupported")
    if trajectory.get("schema_version") != TRAJECTORY_SCHEMA_VERSION:
        errors.append("trajectory schema_version is unsupported")
    if manifest.get("dataset_version") != DATASET_VERSION or trajectory.get("dataset_version") != DATASET_VERSION:
        errors.append("dataset_version is unsupported")
    session_id = manifest.get("session_id")
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        errors.append("manifest session_id is not anonymous hexadecimal")
    if trajectory.get("session_id") != session_id or root.name != session_id:
        errors.append("session_id does not match directory and trajectory")
    if manifest.get("seed") != trajectory.get("seed"):
        errors.append("manifest and trajectory seeds differ")
    environment = manifest.get("environment")
    if not isinstance(environment, Mapping):
        errors.append("environment is required")
    else:
        for key in ("name", "format_version", "git_commit"):
            if not environment.get(key):
                errors.append(f"environment.{key} is required")
        if environment.get("format_version") != trajectory.get("environment_format"):
            errors.append("environment format versions differ")
    config = manifest.get("config")
    if not isinstance(config, Mapping) or manifest.get("config_digest") != json_digest(dict(config or {})):
        errors.append("config_digest mismatch")
    models = manifest.get("models")
    if not isinstance(models, Mapping):
        errors.append("models is required")
    else:
        for agent in AGENTS:
            model = models.get(agent)
            if not isinstance(model, Mapping) or not model.get("policy"):
                errors.append(f"models.{agent}.policy is required")
            elif model.get("checkpoint_sha256") and not SHA256_RE.fullmatch(str(model["checkpoint_sha256"])):
                errors.append(f"models.{agent}.checkpoint_sha256 is invalid")
    trajectory_record = manifest.get("trajectory")
    if not isinstance(trajectory_record, Mapping):
        errors.append("manifest trajectory record is required")
    else:
        if trajectory_record.get("path") != "trajectory.json":
            errors.append("manifest trajectory path is unsupported")
        if trajectory_record.get("sha256") != file_sha256(trajectory_path):
            errors.append("trajectory sha256 mismatch")
        if trajectory_record.get("size_bytes") != trajectory_path.stat().st_size:
            errors.append("trajectory size mismatch")

    ticks = trajectory.get("ticks")
    if not isinstance(ticks, list):
        errors.append("ticks must be a list")
        ticks = []
    for index, tick in enumerate(ticks):
        if not isinstance(tick, Mapping):
            errors.append(f"ticks[{index}] must be an object")
            continue
        if tick.get("tick") != index:
            errors.append(f"ticks[{index}].tick must be contiguous from zero")
        for field in ("inputs", "decisions", "observation_refs", "plans", "rewards"):
            _validate_agent_mapping(errors, tick.get(field), field, index)
        inputs = tick.get("inputs")
        if isinstance(inputs, Mapping):
            for agent in AGENTS:
                payload = inputs.get(agent)
                if not isinstance(payload, Mapping):
                    errors.append(f"ticks[{index}].inputs.{agent} must be an object")
                    continue
                for edge in ("press", "release"):
                    actions = payload.get(edge)
                    if not isinstance(actions, list) or not all(isinstance(action, str) for action in actions):
                        errors.append(f"ticks[{index}].inputs.{agent}.{edge} must be a string list")
        for field in ("decisions", "observation_refs", "plans"):
            payload = tick.get(field)
            if isinstance(payload, Mapping):
                for agent in AGENTS:
                    if not isinstance(payload.get(agent), Mapping):
                        errors.append(f"ticks[{index}].{field}.{agent} must be an object")
        rewards = tick.get("rewards")
        if isinstance(rewards, Mapping):
            for agent in AGENTS:
                reward = rewards.get(agent)
                if not isinstance(reward, (int, float)) or isinstance(reward, bool):
                    errors.append(f"ticks[{index}].rewards.{agent} must be numeric")
        snapshot_hash = tick.get("snapshot_hash")
        if not isinstance(snapshot_hash, str) or not SHA256_RE.fullmatch(snapshot_hash):
            errors.append(f"ticks[{index}].snapshot_hash is invalid")
    outcome = trajectory.get("outcome")
    if not isinstance(outcome, Mapping) or not SHA256_RE.fullmatch(str(outcome.get("expected_final_hash", ""))):
        errors.append("outcome.expected_final_hash is invalid")
    if isinstance(trajectory_record, Mapping) and trajectory_record.get("ticks") != len(ticks):
        errors.append("manifest trajectory tick count mismatch")
    if verify_replay and not errors:
        try:
            _replay_trajectory(trajectory)
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"replay verification failed: {exc}")
    return errors


def replay_session(session_dir: str | Path) -> str:
    errors = validate_session(session_dir)
    if errors:
        raise ValueError("invalid session: " + "; ".join(errors))
    trajectory = _read_json(Path(session_dir) / "trajectory.json")
    return _replay_trajectory(trajectory)


def rebuild_index(dataset_root: str | Path) -> dict[str, Any]:
    root = Path(dataset_root)
    sessions = []
    for session_dir in sorted((root / "sessions").glob("*")):
        if not session_dir.is_dir() or validate_session(session_dir, verify_replay=True):
            continue
        manifest_path = session_dir / "human_session_manifest.json"
        manifest = _read_json(manifest_path)
        sessions.append(
            {
                "session_id": manifest["session_id"],
                "manifest_path": str(manifest_path.relative_to(root)),
                "manifest_sha256": file_sha256(manifest_path),
                "created_at_utc": manifest["created_at_utc"],
                "seed": manifest["seed"],
                "ticks": manifest["trajectory"]["ticks"],
                "outcome": manifest["outcome"],
            }
        )
    index = {
        "schema_version": DATASET_INDEX_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "rebuilt_at_utc": utc_timestamp(),
        "session_count": len(sessions),
        "sessions": sessions,
    }
    _write_json(root / "dataset_index.json", index)
    return index


def quarantine_invalid_sessions(dataset_root: str | Path) -> list[dict[str, Any]]:
    root = Path(dataset_root)
    records = []
    for session_dir in sorted((root / "sessions").glob("*")):
        if not session_dir.is_dir():
            continue
        errors = validate_session(session_dir, verify_replay=True)
        if not errors:
            continue
        target = root / "quarantine" / session_dir.name
        if target.exists():
            raise FileExistsError(f"quarantine target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(session_dir), target)
        record = {"session": session_dir.name, "quarantined_at_utc": utc_timestamp(), "errors": errors}
        _write_json(target / "quarantine_reason.json", record)
        records.append(record)
    rebuild_index(root)
    return records


def delete_session(dataset_root: str | Path, session_id: str) -> bool:
    session_dir = _session_path(dataset_root, session_id)
    if not session_dir.exists():
        return False
    shutil.rmtree(session_dir)
    rebuild_index(dataset_root)
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage human-match trajectory datasets.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "replay"):
        child = subparsers.add_parser(command)
        child.add_argument("session_dir")
    rebuild = subparsers.add_parser("rebuild-index")
    rebuild.add_argument("dataset_root")
    quarantine = subparsers.add_parser("quarantine")
    quarantine.add_argument("dataset_root")
    delete = subparsers.add_parser("delete")
    delete.add_argument("dataset_root")
    delete.add_argument("session_id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "validate":
        errors = validate_session(args.session_dir, verify_replay=True)
        print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
        raise SystemExit(0 if not errors else 1)
    if args.command == "replay":
        print(json.dumps({"final_hash": replay_session(args.session_dir)}, indent=2))
    elif args.command == "rebuild-index":
        print(json.dumps(rebuild_index(args.dataset_root), indent=2))
    elif args.command == "quarantine":
        print(json.dumps({"quarantined": quarantine_invalid_sessions(args.dataset_root)}, indent=2))
    elif args.command == "delete":
        print(json.dumps({"deleted": delete_session(args.dataset_root, args.session_id)}, indent=2))


if __name__ == "__main__":
    main()
