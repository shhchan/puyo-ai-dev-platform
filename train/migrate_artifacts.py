"""Create v1.3 metadata for legacy training artifacts without modifying them."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from train.artifacts import file_sha256, git_commit, write_artifact_manifest
from train.experiment_suite import safe_name

try:
    import torch
except ImportError:  # pragma: no cover - dependency guard
    torch = None

MIGRATION_SCHEMA_VERSION = "puyo.legacy_migration.v1"


@dataclass(frozen=True)
class LegacyAsset:
    asset_id: str
    source_dir: str
    trainer_name: str
    run_id: str
    config_path: str | None = None
    metadata_path: str | None = None
    summary_path: str | None = None
    checkpoint_paths: tuple[str, ...] = ()
    arena_summary_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationRecord:
    asset_id: str
    run_id: str
    trainer_name: str
    status: str
    manifest_path: str | None
    source_dir: str
    migrated_paths: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    load_smoke: dict[str, str] = field(default_factory=dict)


def infer_trainer_name(path: Path) -> str:
    text = str(path)
    if "manager_ppo" in text:
        return "manager_ppo"
    if "versus_long" in text or "versus_ppo" in text:
        return "versus_ppo"
    if "flat_ppo" in text:
        return "flat_ppo"
    return "unknown"


def discover_legacy_assets(roots: Iterable[str | Path]) -> list[LegacyAsset]:
    assets: dict[str, LegacyAsset] = {}
    for root in roots:
        base = Path(root)
        if not base.exists():
            continue
        for summary_path in base.rglob("summary.json"):
            run_dir = summary_path.parent
            if (run_dir / "artifact_manifest.json").exists():
                continue
            checkpoints = tuple(str(path.resolve()) for path in sorted((run_dir / "checkpoints").glob("*.pt")))
            arena = tuple(str(path.resolve()) for path in sorted(run_dir.glob("arena_*_summary.csv")))
            asset = LegacyAsset(
                asset_id=safe_name(str(run_dir)),
                source_dir=str(run_dir.resolve()),
                trainer_name=infer_trainer_name(run_dir),
                run_id=run_dir.name,
                config_path=str((run_dir / "config.yaml").resolve()) if (run_dir / "config.yaml").exists() else None,
                metadata_path=str((run_dir / "metadata.json").resolve()) if (run_dir / "metadata.json").exists() else None,
                summary_path=str(summary_path.resolve()),
                checkpoint_paths=checkpoints,
                arena_summary_paths=arena,
            )
            assets[asset.asset_id] = asset
        for checkpoint_path in base.rglob("*.pt"):
            if "checkpoints" in checkpoint_path.parts:
                continue
            run_dir = checkpoint_path.parent
            asset_id = safe_name(str(checkpoint_path))
            assets.setdefault(
                asset_id,
                LegacyAsset(
                    asset_id=asset_id,
                    source_dir=str(run_dir.resolve()),
                    trainer_name=infer_trainer_name(checkpoint_path),
                    run_id=checkpoint_path.stem,
                    checkpoint_paths=(str(checkpoint_path.resolve()),),
                ),
            )
    return sorted(assets.values(), key=lambda asset: asset.asset_id)


def _load_summary(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _checkpoint_role(path: str) -> str:
    stem = Path(path).stem
    if stem in {"latest", "best", "behavior_cloned"}:
        return stem
    if stem.startswith("step_"):
        return stem
    return "legacy_checkpoint"


def _load_smoke(path: str) -> str:
    if torch is None:
        return "skipped: torch unavailable"
    try:
        checkpoint = torch.load(Path(path), map_location="cpu", weights_only=False)
    except Exception as exc:
        return f"failed: {type(exc).__name__}: {exc}"
    if isinstance(checkpoint, dict):
        if "model_state_dict" in checkpoint:
            return "ok: model_state_dict"
        if any(str(key).endswith("cnn.0.weight") for key in checkpoint):
            return "ok: raw_state_dict"
        return "ok: dict_unknown_schema"
    return f"failed: unsupported type {type(checkpoint).__name__}"


def migrate_asset(asset: LegacyAsset, output_dir: str | Path, *, load_smoke: bool = False) -> MigrationRecord:
    target_dir = Path(output_dir) / "manifests" / safe_name(asset.asset_id)
    source_paths = [
        path
        for path in (
            asset.config_path,
            asset.metadata_path,
            asset.summary_path,
            *asset.checkpoint_paths,
            *asset.arena_summary_paths,
        )
        if path
    ]
    missing_fields = []
    if asset.trainer_name == "unknown":
        missing_fields.append("trainer_name")
    if asset.config_path is None:
        missing_fields.append("config_path")
    if asset.summary_path is None:
        missing_fields.append("summary_path")
    if not asset.checkpoint_paths:
        missing_fields.append("checkpoint_paths")

    issues = []
    for path in source_paths:
        if not Path(path).exists():
            issues.append(f"missing_path:{path}")

    smoke = {}
    if load_smoke:
        smoke = {path: _load_smoke(path) for path in asset.checkpoint_paths}
        issues.extend(f"load_smoke:{path}:{status}" for path, status in smoke.items() if status.startswith("failed"))

    summary = _load_summary(asset.summary_path)
    artifacts = {
        "config": asset.config_path,
        "metadata": asset.metadata_path,
        "summary": asset.summary_path,
    }
    artifacts.update({f"arena_summary_{index + 1}": path for index, path in enumerate(asset.arena_summary_paths)})
    checkpoints = {_checkpoint_role(path): path for path in asset.checkpoint_paths}
    manifest = write_artifact_manifest(
        run_dir=target_dir,
        run_id=asset.run_id,
        trainer_name=asset.trainer_name,
        config={
            "legacy_source_dir": asset.source_dir,
            "legacy_missing_fields": missing_fields,
        },
        git_commit=summary.get("git_commit") or "unknown",
        seed=summary.get("seed"),
        artifacts=artifacts,
        checkpoints=checkpoints,
        manifest_path=target_dir / "artifact_manifest.json",
        extra={
            "migration_schema_version": MIGRATION_SCHEMA_VERSION,
            "source_dir": asset.source_dir,
            "missing_fields": missing_fields,
            "load_smoke": smoke,
        },
    )
    status = "migrated" if not issues else "migrated_with_issues"
    return MigrationRecord(
        asset_id=asset.asset_id,
        run_id=asset.run_id,
        trainer_name=asset.trainer_name,
        status=status,
        manifest_path=str(target_dir / "artifact_manifest.json"),
        source_dir=asset.source_dir,
        migrated_paths=tuple(source_paths),
        missing_fields=tuple(missing_fields),
        issues=tuple(issues),
        load_smoke=smoke,
    )


def migrate_legacy_artifacts(
    roots: Iterable[str | Path],
    output_dir: str | Path,
    *,
    load_smoke: bool = False,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    assets = discover_legacy_assets(roots)
    records = [migrate_asset(asset, output, load_smoke=load_smoke) for asset in assets]
    summary = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "created_git_commit": git_commit(),
        "roots": [str(root) for root in roots],
        "output_dir": str(output),
        "asset_count": len(assets),
        "status_counts": {
            status: sum(1 for record in records if record.status == status)
            for status in sorted({record.status for record in records})
        },
        "records": [asdict(record) for record in records],
    }
    (output / "migration_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (output / "migration_records.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["asset_id", "run_id", "trainer_name", "status", "manifest_path", "source_dir", "missing_fields", "issues"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = asdict(record)
            row["missing_fields"] = ";".join(record.missing_fields)
            row["issues"] = ";".join(record.issues)
            writer.writerow({key: row[key] for key in fieldnames})
    write_markdown_report(summary, output / "migration_report.md")
    return summary


def write_markdown_report(summary: dict[str, Any], path: str | Path) -> None:
    lines = [
        "# Legacy Artifact Migration Report",
        "",
        f"- assets: {summary['asset_count']}",
        f"- output_dir: `{summary['output_dir']}`",
        "",
        "## Status",
        "",
    ]
    for status, count in summary["status_counts"].items():
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Records", "", "| asset | trainer | status | missing | issues |", "|---|---|---|---|---|"])
    for record in summary["records"]:
        lines.append(
            "| `{asset_id}` | {trainer_name} | {status} | {missing} | {issues} |".format(
                asset_id=record["asset_id"],
                trainer_name=record["trainer_name"],
                status=record["status"],
                missing=", ".join(record["missing_fields"]) or "-",
                issues=", ".join(record["issues"]) or "-",
            )
        )
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Migrate legacy training artifacts into v1.3 metadata.")
    parser.add_argument("--root", action="append", required=True, help="Legacy artifact root to scan.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--load-smoke", action="store_true", help="Try torch.load on discovered checkpoints.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    summary = migrate_legacy_artifacts(args.root, args.output_dir, load_smoke=args.load_smoke)
    print(f"assets: {summary['asset_count']}")
    print(f"summary: {Path(args.output_dir) / 'migration_summary.json'}")
    print(f"records: {Path(args.output_dir) / 'migration_records.csv'}")
    print(f"report: {Path(args.output_dir) / 'migration_report.md'}")


if __name__ == "__main__":
    main()
