"""Privacy-safe audit records for human match collection controls."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
from typing import Any, Mapping

from train.artifacts import utc_timestamp

COLLECTION_AUDIT_SCHEMA_VERSION = "puyo.human_collection_audit.v1"
COLLECTION_CONTENTS = ("inputs", "boards", "ai_plans", "result", "optional_feedback")


def append_collection_audit(
    dataset_root: str | Path,
    *,
    event: str,
    enabled: bool,
    tick: int,
    details: Mapping[str, Any] | None = None,
) -> Path:
    """Append control metadata only; gameplay and feedback never enter this log."""
    path = Path(dataset_root) / "collection_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": COLLECTION_AUDIT_SCHEMA_VERSION,
        "recorded_at_utc": utc_timestamp(),
        "actor": (
            os.environ.get("PUYO_AUDIT_ACTOR")
            or os.environ.get("USER")
            or getpass.getuser()
            or "unknown"
        ),
        "event": event,
        "enabled": bool(enabled),
        "tick": int(tick),
        "details": dict(details or {}),
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    return path
