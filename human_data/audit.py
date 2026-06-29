"""Cross-stage audit reporting and safe deletion for human-derived artifacts."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from train.artifacts import file_sha256, json_digest, utc_timestamp


AUDIT_SCHEMA_VERSION = "puyo.human_data_audit.v1"
AUDIT_REPORT_SCHEMA_VERSION = "puyo.human_data_audit_report.v1"
DELETION_PLAN_SCHEMA_VERSION = "puyo.human_data_deletion_plan.v1"
SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _actor() -> str:
    return os.environ.get("PUYO_AUDIT_ACTOR") or os.environ.get("USER") or getpass.getuser() or "unknown"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def append_audit_event(
    path: str | Path,
    *,
    event: str,
    resource_type: str,
    resource_id: str,
    status: str = "completed",
    details: Mapping[str, Any] | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Append one privacy-safe event without storing trajectory or feedback payloads."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "recorded_at_utc": utc_timestamp(),
        "actor": actor or _actor(),
        "event": event,
        "status": status,
        "resource": {"type": resource_type, "id": resource_id},
        "details": dict(details or {}),
    }
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def _read_events(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    issues: list[str] = []
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                issues.append(f"{path}:{number}: {exc}")
                continue
            if path.name == "collection_audit.jsonl":
                record = {
                    "schema_version": record.get("schema_version"),
                    "recorded_at_utc": record.get("recorded_at_utc"),
                    "actor": record.get("actor", "unknown"),
                    "event": f"collection.{record.get('event', 'unknown')}",
                    "status": "completed",
                    "resource": {"type": "collection", "id": str(record.get("details", {}).get("session_id", "session"))},
                    "details": {"enabled": record.get("enabled"), "tick": record.get("tick")},
                }
            if isinstance(record, dict):
                record["source_path"] = str(path)
                events.append(record)
    events.sort(key=lambda item: str(item.get("recorded_at_utc", "")))
    return events, issues


def build_audit_report(
    *,
    dataset_root: str | Path,
    training_root: str | Path,
    registry_path: str | Path,
) -> dict[str, Any]:
    dataset = Path(dataset_root)
    training = Path(training_root)
    registry_target = Path(registry_path)
    audit_paths = list(dataset.rglob("audit_events.jsonl")) + list(training.rglob("audit_events.jsonl"))
    audit_paths.extend(dataset.rglob("collection_audit.jsonl"))
    if registry_target.parent.exists():
        audit_paths.append(registry_target.parent / "audit_events.jsonl")
    events, issues = _read_events(audit_paths)

    sessions = []
    for path in sorted((dataset / "sessions").glob("*/human_session_manifest.json")):
        try:
            manifest = _read_json(path)
            sessions.append(
                {
                    "session_id": manifest.get("session_id"),
                    "created_at_utc": manifest.get("created_at_utc"),
                    "manifest_path": str(path),
                    "manifest_sha256": file_sha256(path),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append(f"{path}: {exc}")

    runs = []
    for path in sorted(training.rglob("summary.json")):
        try:
            summary = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append(f"{path}: {exc}")
            continue
        if not str(summary.get("schema_version", "")).startswith("puyo.human_training_summary"):
            continue
        runs.append(
            {
                "run_id": summary.get("run_id"),
                "created_at_utc": summary.get("created_at_utc"),
                "session_ids": list(summary.get("session_ids", [])),
                "checkpoint_path": summary.get("checkpoint_path"),
                "parent_checkpoint_path": summary.get("parent_checkpoint_path"),
                "summary_path": str(path),
            }
        )

    registry: dict[str, Any] | None = None
    if registry_target.is_file():
        try:
            value = _read_json(registry_target)
            registry = {
                "path": str(registry_target),
                "revision": value.get("revision"),
                "roles": value.get("roles", {}),
                "evaluations": value.get("evaluations", []),
                "transitions": value.get("transitions", []),
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append(f"{registry_target}: {exc}")

    return {
        "schema_version": AUDIT_REPORT_SCHEMA_VERSION,
        "generated_at_utc": utc_timestamp(),
        "actor": _actor(),
        "events": events,
        "sessions": sessions,
        "derived_runs": runs,
        "model_registry": registry,
        "issues": issues,
    }


def report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Human data audit report",
        "",
        f"Generated: `{report.get('generated_at_utc')}`",
        f"Sessions: **{len(report.get('sessions', []))}**",
        f"Derived runs: **{len(report.get('derived_runs', []))}**",
        f"Audit events: **{len(report.get('events', []))}**",
        "",
        "## Lineage",
        "",
        "| Session | Derived run | Challenger checkpoint | Parent checkpoint |",
        "| --- | --- | --- | --- |",
    ]
    runs = list(report.get("derived_runs", []))
    for session in report.get("sessions", []):
        session_id = session.get("session_id")
        linked = [run for run in runs if session_id in run.get("session_ids", [])]
        if not linked:
            lines.append(f"| `{session_id}` | - | - | - |")
        for run in linked:
            lines.append(
                f"| `{session_id}` | `{run.get('run_id')}` | `{run.get('checkpoint_path')}` | `{run.get('parent_checkpoint_path')}` |"
            )
    lines.extend(["", "## Model transitions", ""])
    registry = report.get("model_registry") or {}
    transitions = registry.get("transitions", [])
    if transitions:
        for transition in transitions:
            lines.append(
                f"- `{transition.get('created_at_utc')}` {transition.get('kind')}: "
                f"`{transition.get('from_sha256')}` -> `{transition.get('to_sha256')}`"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Audit events",
            "",
            "| UTC time | Actor | Event | Resource | Status |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for event in report.get("events", []):
        resource = event.get("resource") or {}
        lines.append(
            f"| `{event.get('recorded_at_utc')}` | `{event.get('actor')}` | "
            f"`{event.get('event')}` | `{resource.get('type')}:{resource.get('id')}` | "
            f"`{event.get('status')}` |"
        )
    if not report.get("events"):
        lines.append("| - | - | - | - | - |")
    if report.get("issues"):
        lines.extend(["", "## Issues", ""] + [f"- {item}" for item in report["issues"]])
    return "\n".join(lines) + "\n"


def _registry_references(registry_path: Path) -> list[dict[str, str]]:
    if not registry_path.is_file():
        return []
    registry = _read_json(registry_path)
    references: list[dict[str, str]] = []
    for role, record in (registry.get("roles") or {}).items():
        if isinstance(record, Mapping) and record.get("path"):
            references.append({"kind": f"role:{role}", "path": str(Path(record["path"]).resolve())})
    for index, record in enumerate(registry.get("opponent_pool", [])):
        if isinstance(record, Mapping) and record.get("path"):
            references.append({"kind": f"opponent_pool:{index}", "path": str(Path(record["path"]).resolve())})
    for record in registry.get("evaluations", []):
        artifact = record.get("artifact_path") if isinstance(record, Mapping) else None
        if artifact:
            references.append({"kind": "evaluation", "path": str(Path(artifact).resolve())})
    return references


def plan_session_deletion(
    *,
    dataset_root: str | Path,
    training_root: str | Path,
    registry_path: str | Path,
    session_id: str,
) -> dict[str, Any]:
    if not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("session_id must be 32 lowercase hexadecimal characters")
    dataset = Path(dataset_root).resolve()
    training = Path(training_root).resolve()
    registry_target = Path(registry_path).resolve()
    session_path = dataset / "sessions" / session_id
    derived_runs = []
    checkpoint_paths: set[str] = set()
    for summary_path in sorted(training.rglob("summary.json")):
        try:
            summary = _read_json(summary_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if session_id not in summary.get("session_ids", []):
            continue
        checkpoint = summary.get("checkpoint_path")
        if checkpoint:
            checkpoint_paths.add(str(Path(checkpoint).resolve()))
        derived_runs.append(
            {
                "run_id": str(summary.get("run_id") or summary_path.parent.name),
                "path": str(summary_path.parent.resolve()),
                "checkpoint_path": checkpoint,
                "parent_checkpoint_path": summary.get("parent_checkpoint_path"),
            }
        )
    references = _registry_references(registry_target)
    protected = [item for item in references if item["path"] in checkpoint_paths]
    payload = {
        "schema_version": DELETION_PLAN_SCHEMA_VERSION,
        "session_id": session_id,
        "dataset_root": str(dataset),
        "training_root": str(training),
        "registry_path": str(registry_target),
        "session_path": str(session_path),
        "derived_runs": derived_runs,
        "protected_references": protected,
        "blocked": bool(protected),
    }
    payload["confirmation_token"] = json_digest(payload)[:20]
    return payload


def execute_deletion(plan: Mapping[str, Any], *, confirmation_token: str) -> dict[str, Any]:
    if plan.get("schema_version") != DELETION_PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported deletion plan")
    if confirmation_token != plan.get("confirmation_token"):
        raise ValueError("confirmation token does not match the deletion plan")
    current = plan_session_deletion(
        dataset_root=str(plan["dataset_root"]),
        training_root=str(plan["training_root"]),
        registry_path=str(plan["registry_path"]),
        session_id=str(plan["session_id"]),
    )
    if current.get("confirmation_token") != confirmation_token:
        raise RuntimeError("deletion plan is stale; generate and review a new plan")
    if plan.get("blocked") or plan.get("protected_references"):
        raise RuntimeError("deletion is blocked by model registry references")
    dataset = Path(str(plan["dataset_root"]))
    registry = Path(str(plan["registry_path"]))
    registry_digest = file_sha256(registry) if registry.is_file() else None
    trash = dataset / "deletion_trash" / str(plan["confirmation_token"])
    moves: list[tuple[Path, Path]] = []
    session = Path(str(plan["session_path"]))
    if session.exists():
        moves.append((session, trash / "session" / session.name))
    for run in plan.get("derived_runs", []):
        source = Path(str(run["path"]))
        if source.exists():
            moves.append((source, trash / "derived_runs" / str(run["run_id"])))
    completed: list[tuple[Path, Path]] = []
    try:
        for source, target in moves:
            if target.exists():
                raise FileExistsError(f"deletion trash target exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), target)
            completed.append((source, target))
        from human_data.dataset import rebuild_index

        rebuild_index(dataset)
        if registry_digest is not None and file_sha256(registry) != registry_digest:
            raise RuntimeError("model registry changed during deletion")
    except Exception:
        for source, target in reversed(completed):
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), source)
        from human_data.dataset import rebuild_index

        rebuild_index(dataset)
        raise
    append_audit_event(
        dataset / "audit_events.jsonl",
        event="deletion.completed",
        resource_type="human_session",
        resource_id=str(plan["session_id"]),
        details={
            "trash_path": str(trash),
            "derived_runs": [run["run_id"] for run in plan.get("derived_runs", [])],
        },
    )
    return {"deleted": bool(moves), "trash_path": str(trash), "moved": [str(source) for source, _ in moves]}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and safely delete human-derived data.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    report = subparsers.add_parser("report")
    plan = subparsers.add_parser("plan-delete")
    execute = subparsers.add_parser("execute-delete")
    for child in (report, plan):
        child.add_argument("--dataset-root", default="human_datasets")
        child.add_argument("--training-root", default="runs/human_training")
        child.add_argument("--registry", default="runs/model_registry.json")
    report.add_argument("--output-json", required=True)
    report.add_argument("--output-markdown")
    plan.add_argument("--session-id", required=True)
    plan.add_argument("--output", required=True)
    execute.add_argument("--plan", required=True)
    execute.add_argument("--confirm", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "report":
        result = build_audit_report(
            dataset_root=args.dataset_root,
            training_root=args.training_root,
            registry_path=args.registry,
        )
        _write_json(Path(args.output_json), result)
        if args.output_markdown:
            target = Path(args.output_markdown)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(report_markdown(result), encoding="utf-8")
    elif args.command == "plan-delete":
        result = plan_session_deletion(
            dataset_root=args.dataset_root,
            training_root=args.training_root,
            registry_path=args.registry,
            session_id=args.session_id,
        )
        _write_json(Path(args.output), result)
    else:
        result = execute_deletion(_read_json(Path(args.plan)), confirmation_token=args.confirm)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
