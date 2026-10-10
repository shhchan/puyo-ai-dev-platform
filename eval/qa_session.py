"""Publish one self-contained realtime GUI QA session."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from eval.realtime_arena import replay_realtime_match
from train.artifacts import file_sha256, git_commit, json_digest, utc_timestamp

QA_SESSION_SCHEMA_VERSION = "puyo.gui_qa_session.v1"


class QASessionSaveError(OSError):
    """A session could not be published; pending_path may contain recovery data."""

    def __init__(self, session_id: str, pending_path: Path, reason: str):
        self.session_id = session_id
        self.pending_path = pending_path
        super().__init__(f"QA session {session_id} was not saved: {reason}; pending: {pending_path}")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_record(path: Path) -> dict[str, Any]:
    return {"path": path.name, "sha256": file_sha256(path), "size_bytes": path.stat().st_size}


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reserve_session(root: Path) -> tuple[str, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        session_id = secrets.token_hex(16)
        published = root / session_id
        pending = root / f".{session_id}.pending"
        if published.exists():
            continue
        try:
            pending.mkdir()
        except FileExistsError:
            continue
        return session_id, pending, published
    raise FileExistsError(f"could not reserve a unique QA session in {root}")


def _publish_session(pending: Path, published: Path) -> None:
    # Lock the parent directory while checking and renaming so that a second
    # writer cannot create/replace the destination between those operations.
    descriptor = os.open(published.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if published.exists():
            raise FileExistsError(f"QA session already exists: {published}")
        os.replace(pending, published)
        _sync_directory(published.parent)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def save_qa_session(
    root: str | Path,
    *,
    replay: Mapping[str, Any],
    result: Mapping[str, Any],
    config: Mapping[str, Any],
    source: Mapping[str, Any] | None = None,
    native: Mapping[str, Any] | None = None,
    tsumo: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Save an immutable replay/result/manifest directory and return its path.

    The directory becomes visible under its session ID only after all files are
    durable. Failed saves leave a hidden pending directory for manual recovery.
    Optional identities remain null when the caller cannot establish them.
    """
    if replay.get("format") != "puyo-realtime-match-v1":
        raise ValueError("unsupported replay format")
    if result.get("schema_version") != "puyo.gui_qa.v1":
        raise ValueError("unsupported result format")
    ticks = replay.get("ticks")
    if not isinstance(ticks, list):
        raise TypeError("replay ticks must be a list")
    if result.get("result", {}).get("ticks") != len(ticks):
        raise ValueError("result and replay tick counts differ")
    if result.get("match", {}).get("seed") != replay.get("seed"):
        raise ValueError("result and replay seeds differ")
    if bool(result.get("result", {}).get("interrupted")) != bool(replay.get("outcome", {}).get("interrupted")):
        raise ValueError("result and replay interruption flags differ")
    replay_realtime_match(replay)

    root = Path(root).expanduser().resolve()
    session_id, pending, published = _reserve_session(root)
    try:
        replay_path = pending / "replay.json"
        result_path = pending / "result.json"
        saved_result = dict(result)
        saved_result["artifacts"] = {
            **dict(result.get("artifacts") or {}),
            "qa_session": str(published),
            "replay": str(published / "replay.json"),
            "result": str(published / "result.json"),
            "manifest": str(published / "manifest.json"),
        }
        _write_json_atomic(replay_path, replay)
        _write_json_atomic(result_path, saved_result)
        manifest = {
            "schema_version": QA_SESSION_SCHEMA_VERSION,
            "session_id": session_id,
            "created_at_utc": utc_timestamp(),
            "save": {"status": "complete", "interrupted": bool(result.get("result", {}).get("interrupted")), "replay_validated": True},
            "match": {
                "seed": replay.get("seed"),
                "policy_seeds": {
                    "player_0": replay.get("policies", {}).get("player_0", {}).get("seed"),
                    "player_1": replay.get("policies", {}).get("player_1", {}).get("seed"),
                },
                "speed": result.get("match", {}).get("speed"),
                "ticks": len(ticks),
                "expected_final_hash": replay.get("expected_final_hash"),
            },
            "identity": {
                "source": dict(source) if source is not None else {"git_commit": git_commit(), "dirty": None},
                "config_sha256": json_digest(dict(config)),
                "native": dict(native) if native is not None else None,
                "tsumo": dict(tsumo) if tsumo is not None else None,
            },
            "config": dict(config),
            "artifacts": {"replay": _file_record(replay_path), "result": _file_record(result_path)},
        }
        _write_json_atomic(pending / "manifest.json", manifest)
        _sync_directory(pending)
        _publish_session(pending, published)
        return published, manifest
    except Exception as exc:
        # Keep the pending directory: a write failure must not discard a replay.
        raise QASessionSaveError(session_id, pending if pending.exists() else published, str(exc)) from exc


def validate_qa_session(session_dir: str | Path) -> list[str]:
    """Check checksums and replay the persisted inputs and hashes."""
    root = Path(session_dir).expanduser().resolve()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        replay = json.loads((root / "replay.json").read_text(encoding="utf-8"))
        result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unreadable session: {exc}"]
    errors = []
    if manifest.get("schema_version") != QA_SESSION_SCHEMA_VERSION:
        errors.append("unsupported manifest schema")
    if manifest.get("session_id") != root.name:
        errors.append("session ID mismatch")
    if manifest.get("save", {}).get("status") != "complete":
        errors.append("session was not completed")
    for name, path in (("replay", root / "replay.json"), ("result", root / "result.json")):
        record = manifest.get("artifacts", {}).get(name, {})
        if record.get("path") != path.name or record.get("sha256") != file_sha256(path) or record.get("size_bytes") != path.stat().st_size:
            errors.append(f"{name} checksum or size mismatch")
    if manifest.get("identity", {}).get("config_sha256") != json_digest(manifest.get("config")):
        errors.append("config checksum mismatch")
    if manifest.get("match", {}).get("seed") != replay.get("seed"):
        errors.append("seed mismatch")
    if manifest.get("match", {}).get("ticks") != len(replay.get("ticks", ())):
        errors.append("tick count mismatch")
    if manifest.get("match", {}).get("expected_final_hash") != replay.get("expected_final_hash"):
        errors.append("final hash mismatch")
    if result.get("result", {}).get("ticks") != len(replay.get("ticks", ())):
        errors.append("result tick count mismatch")
    artifacts = result.get("artifacts") or {}
    # These absolute paths describe the original save location. The bundle is
    # portable, so validate their names while reading files from session_dir.
    if (
        Path(str(artifacts.get("qa_session") or "")).name != root.name
        or any(Path(str(artifacts.get(name) or "")).name != f"{name}.json" for name in ("replay", "result", "manifest"))
    ):
        errors.append("result artifact paths mismatch")
    if not errors:
        try:
            replay_realtime_match(replay)
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"replay verification failed: {exc}")
    return errors
