"""Local model lineage registry built from artifact manifests."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from train.artifacts import ARTIFACT_MANIFEST_SCHEMA_VERSION, json_digest
from train.experiment_suite import SUITE_SCHEMA_VERSION

try:
    from eval.benchmark_suite import BENCHMARK_SCHEMA_VERSION
except ImportError:  # pragma: no cover - optional evaluation module guard
    BENCHMARK_SCHEMA_VERSION = "puyo.benchmark_suite.v1"

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guard
    yaml = None

REGISTRY_SCHEMA_VERSION = "puyo.lineage_registry.v1"


@dataclass
class LineageNode:
    id: str
    node_type: str
    label: str
    path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LineageEdge:
    source: str
    target: str
    edge_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LineageRegistry:
    nodes: dict[str, LineageNode] = field(default_factory=dict)
    edges: list[LineageEdge] = field(default_factory=list)

    def add_node(self, node: LineageNode) -> None:
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return
        existing.metadata.update(node.metadata)
        if existing.path is None:
            existing.path = node.path

    def add_edge(self, edge: LineageEdge) -> None:
        key = (edge.source, edge.target, edge.edge_type, json.dumps(edge.metadata, sort_keys=True, default=str))
        existing = {
            (item.source, item.target, item.edge_type, json.dumps(item.metadata, sort_keys=True, default=str))
            for item in self.edges
        }
        if key not in existing:
            self.edges.append(edge)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "nodes": [asdict(node) for node in sorted(self.nodes.values(), key=lambda item: item.id)],
            "edges": [asdict(edge) for edge in self.edges],
            "issues": validate_registry(self),
        }


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _resolve_record_path(manifest_path: Path, record_path: str) -> Path:
    path = Path(record_path)
    if path.is_absolute():
        return path
    return manifest_path.parent / path


def _resolve_legacy_path(run_dir: Path, record_path: str | Path | None) -> Path | None:
    if not record_path:
        return None
    path = Path(record_path)
    if path.is_absolute():
        return path
    if path.exists():
        return path
    repo_relative = Path.cwd() / path
    if repo_relative.exists():
        return path
    return run_dir / path


def _path_id(prefix: str, path: str | Path) -> str:
    return f"{prefix}:{json_digest({'path': str(Path(path))})[:16]}"


def _checkpoint_id(record: dict[str, Any], path: Path) -> str:
    digest = record.get("sha256") or json_digest({"path": str(path)})
    return f"checkpoint:{digest}"


def _numeric_metrics(summary: dict[str, Any]) -> dict[str, float]:
    selected = {}
    for key, value in summary.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            selected[key] = float(value)
    return selected


def _checkpoint_step(path: Path) -> int | None:
    if not path.stem.startswith("step_"):
        return None
    try:
        return int(path.stem.removeprefix("step_"))
    except ValueError:
        return None


def _summary_metrics(manifest_path: Path, manifest: dict[str, Any]) -> dict[str, float]:
    for record in manifest.get("artifacts", []):
        if record.get("role") != "summary" or not isinstance(record.get("path"), str):
            continue
        summary_path = _resolve_record_path(manifest_path, record["path"])
        if summary_path.exists() and summary_path.suffix == ".json":
            return _numeric_metrics(_read_json(summary_path))
    return {}


def _add_artifact_manifest(registry: LineageRegistry, manifest_path: Path, path_index: dict[str, str]) -> None:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != ARTIFACT_MANIFEST_SCHEMA_VERSION:
        return
    run = manifest.get("run", {})
    run_id = str(run.get("run_id") or manifest_path.parent.name)
    run_node_id = f"run:{run_id}"
    registry.add_node(
        LineageNode(
            id=run_node_id,
            node_type="run",
            label=run_id,
            path=str(manifest_path.parent),
            metadata={
                "manifest_path": str(manifest_path),
                "trainer_name": run.get("trainer_name"),
                "seed": run.get("seed"),
                "git_commit": run.get("git_commit"),
                "config_digest": run.get("config_digest"),
                "metrics": _summary_metrics(manifest_path, manifest),
            },
        )
    )
    parent_checkpoint_path = run.get("parent_checkpoint_path")
    if parent_checkpoint_path:
        parent_path = _resolve_record_path(manifest_path, str(parent_checkpoint_path))
        parent_id = path_index.get(str(parent_path.resolve()), _path_id("external_checkpoint", parent_path))
        registry.add_node(
            LineageNode(
                id=parent_id,
                node_type="external_checkpoint",
                label=Path(parent_checkpoint_path).name,
                path=str(parent_path),
                metadata={"declared_by": str(manifest_path)},
            )
        )
        registry.add_edge(LineageEdge(source=parent_id, target=run_node_id, edge_type="resume"))

    for record in manifest.get("checkpoints", []):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            continue
        checkpoint_path = _resolve_record_path(manifest_path, record["path"])
        node_id = _checkpoint_id(record, checkpoint_path)
        path_index[str(checkpoint_path.resolve())] = node_id
        registry.add_node(
            LineageNode(
                id=node_id,
                node_type="checkpoint",
                label=f"{run_id}:{record.get('role', 'checkpoint')}",
                path=str(checkpoint_path),
                metadata={
                    "run_id": run_id,
                    "role": record.get("role"),
                    "sha256": record.get("sha256"),
                    "size_bytes": record.get("size_bytes"),
                    "artifact_type": record.get("artifact_type"),
                },
            )
        )
        registry.add_edge(
            LineageEdge(
                source=run_node_id,
                target=node_id,
                edge_type="produces",
                metadata={"role": record.get("role")},
            )
        )

    for record in manifest.get("artifacts", []):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            continue
        role = str(record.get("role", "artifact"))
        artifact_path = _resolve_record_path(manifest_path, record["path"])
        if role == "opponent_pool" and artifact_path.exists():
            _add_opponent_pool(registry, run_node_id, artifact_path)
        if role == "selfplay_evaluations" and artifact_path.exists():
            _add_selfplay_evaluations(registry, run_node_id, artifact_path)
        if "arena" in role or "arena" in artifact_path.name:
            node_id = _path_id("arena_result", artifact_path)
            registry.add_node(
                LineageNode(
                    id=node_id,
                    node_type="arena_result",
                    label=artifact_path.name,
                    path=str(artifact_path),
                    metadata={"role": role},
                )
            )
            registry.add_edge(LineageEdge(source=run_node_id, target=node_id, edge_type="evaluates"))


def _add_opponent_pool(registry: LineageRegistry, run_node_id: str, path: Path) -> None:
    try:
        data = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return
    for snapshot in data.get("snapshots", []):
        if not isinstance(snapshot, dict):
            continue
        name = str(snapshot.get("name", "snapshot"))
        node_id = f"opponent_snapshot:{json_digest(snapshot)[:16]}"
        registry.add_node(
            LineageNode(
                id=node_id,
                node_type="opponent_snapshot",
                label=name,
                path=snapshot.get("checkpoint_path"),
                metadata={
                    "policy_type": snapshot.get("policy_type"),
                    "rating": snapshot.get("rating"),
                    "games_played": snapshot.get("games_played"),
                    "source_pool": str(path),
                },
            )
        )
        edge_type = "produces" if snapshot.get("metadata", {}).get("role") == "selfplay_snapshot" else "uses_opponent"
        registry.add_edge(LineageEdge(source=run_node_id, target=node_id, edge_type=edge_type))
        parent_checkpoint_path = snapshot.get("metadata", {}).get("parent_checkpoint_path")
        if parent_checkpoint_path:
            parent_id = _path_id("external_checkpoint", parent_checkpoint_path)
            registry.add_node(
                LineageNode(
                    id=parent_id,
                    node_type="external_checkpoint",
                    label=Path(str(parent_checkpoint_path)).name,
                    path=str(parent_checkpoint_path),
                    metadata={"declared_by": str(path), "source_field": "parent_checkpoint_path"},
                )
            )
            registry.add_edge(LineageEdge(source=parent_id, target=node_id, edge_type="promotes_to_selfplay"))


def _add_selfplay_evaluations(registry: LineageRegistry, run_node_id: str, path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return
    if not isinstance(data, list):
        return
    for index, record in enumerate(data):
        if not isinstance(record, dict):
            continue
        node_id = f"selfplay_eval:{json_digest(record)[:16]}"
        registry.add_node(
            LineageNode(
                id=node_id,
                node_type="selfplay_evaluation",
                label=f"{record.get('latest_name', 'latest')} vs {record.get('opponent_name', 'opponent')}",
                path=str(path),
                metadata={
                    "index": index,
                    "global_step": record.get("global_step"),
                    "latest_name": record.get("latest_name"),
                    "opponent_name": record.get("opponent_name"),
                    "latest_rating": record.get("latest_rating"),
                    "opponent_rating": record.get("opponent_rating"),
                    "games": record.get("games"),
                    "win_rate": record.get("win_rate"),
                    "mean_score": record.get("mean_score"),
                },
            )
        )
        registry.add_edge(LineageEdge(source=run_node_id, target=node_id, edge_type="evaluates"))


def _add_legacy_run(registry: LineageRegistry, run_dir: Path, path_index: dict[str, str]) -> None:
    summary_path = run_dir / "summary.json"
    metadata_path = run_dir / "metadata.json"
    config_path = run_dir / "config.yaml"
    checkpoints_dir = run_dir / "checkpoints"
    checkpoint_files = sorted(checkpoints_dir.glob("*.pt")) if checkpoints_dir.exists() else []
    standalone_checkpoints = sorted(run_dir.glob("*.pt"))
    if not summary_path.exists() and not metadata_path.exists() and not config_path.exists() and not checkpoint_files and not standalone_checkpoints:
        return

    summary = _safe_read_json(summary_path)
    metadata = _safe_read_json(metadata_path)
    config_dump = _read_yaml_mapping(config_path)
    config = config_dump.get("config") if isinstance(config_dump.get("config"), dict) else config_dump
    resolved = config_dump.get("resolved") if isinstance(config_dump.get("resolved"), dict) else {}
    if not isinstance(config, dict):
        config = {}

    run_id = str(summary.get("run_id") or metadata.get("run_id") or resolved.get("run_id") or run_dir.name)
    run_node_id = f"run:{run_id}"
    registry.add_node(
        LineageNode(
            id=run_node_id,
            node_type="run",
            label=run_id,
            path=str(run_dir),
            metadata={
                "legacy": True,
                "summary_path": str(summary_path) if summary_path.exists() else None,
                "metadata_path": str(metadata_path) if metadata_path.exists() else None,
                "config_path": str(config_path) if config_path.exists() else None,
                "trainer_name": metadata.get("trainer_name") or _infer_legacy_trainer_name(run_dir, config),
                "seed": summary.get("seed", metadata.get("seed", config.get("seed"))),
                "git_commit": metadata.get("git_commit"),
                "created_at_utc": metadata.get("created_at_utc"),
                "opponent_policy": metadata.get("opponent_policy") or config.get("opponent_policy"),
                "metrics": _numeric_metrics(summary),
            },
        )
    )

    checkpoint_nodes: list[tuple[str, Path, str, int | None]] = []
    for role, checkpoint_path in _legacy_checkpoint_records(
        run_dir,
        summary,
        metadata,
        resolved,
        checkpoint_files,
        standalone_checkpoints,
    ):
        if checkpoint_path is None:
            continue
        node_id = _path_id("checkpoint", checkpoint_path)
        path_index[str(checkpoint_path.resolve())] = node_id
        step = _checkpoint_step(checkpoint_path)
        checkpoint_nodes.append((node_id, checkpoint_path, role, step))
        registry.add_node(
            LineageNode(
                id=node_id,
                node_type="checkpoint",
                label=f"{run_id}:{role}",
                path=str(checkpoint_path),
                metadata={
                    "legacy": True,
                    "run_id": run_id,
                    "role": role,
                    "step": step,
                    "artifact_type": "torch_checkpoint",
                },
            )
        )
        registry.add_edge(
            LineageEdge(
                source=run_node_id,
                target=node_id,
                edge_type="produces",
                metadata={"role": role, "legacy": True},
            )
        )

    _add_checkpoint_progress_edges(registry, checkpoint_nodes)
    _add_legacy_parent_edges(registry, run_node_id, run_dir, config, metadata, path_index)
    _add_legacy_arena_results(registry, run_node_id, run_dir)
    opponent_pool_path = metadata.get("opponent_pool_path") or config.get("opponent_pool_path")
    resolved_pool_path = _resolve_legacy_path(run_dir, opponent_pool_path)
    if resolved_pool_path is not None and resolved_pool_path.exists():
        _add_opponent_pool(registry, run_node_id, resolved_pool_path)


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _infer_legacy_trainer_name(run_dir: Path, config: dict[str, Any]) -> str:
    run_name = str(config.get("run_name", ""))
    text = f"{run_dir} {run_name}"
    if "manager" in text:
        return "manager_ppo"
    if "versus" in text:
        return "versus_ppo"
    if "flat" in text:
        return "flat_ppo"
    return "legacy"


def _legacy_checkpoint_records(
    run_dir: Path,
    summary: dict[str, Any],
    metadata: dict[str, Any],
    resolved: dict[str, Any],
    checkpoint_files: list[Path],
    standalone_checkpoints: list[Path],
) -> list[tuple[str, Path | None]]:
    records: list[tuple[str, Path | None]] = []
    known: set[str] = set()

    def add(role: str, value: str | Path | None) -> None:
        path = _resolve_legacy_path(run_dir, value)
        if path is None or not path.exists():
            return
        key = str(path)
        if key in known:
            return
        known.add(key)
        records.append((role, path))

    add("latest", summary.get("checkpoint_path") or metadata.get("checkpoint_path") or resolved.get("checkpoint_path"))
    add("best", summary.get("best_checkpoint_path") or resolved.get("best_checkpoint_path"))
    for index, value in enumerate(summary.get("periodic_checkpoints", [])):
        add(f"periodic_{index + 1}", value)
    for path in checkpoint_files:
        step = _checkpoint_step(path)
        if path.name == "latest.pt":
            add("latest", path)
        elif path.name == "best.pt":
            add("best", path)
        elif step is not None:
            add(f"step_{step}", path)
        else:
            add(path.stem, path)
    for path in standalone_checkpoints:
        add(path.stem, path)
    return records


def _add_checkpoint_progress_edges(
    registry: LineageRegistry,
    checkpoint_nodes: list[tuple[str, Path, str, int | None]],
) -> None:
    stepped = sorted(
        (item for item in checkpoint_nodes if item[3] is not None),
        key=lambda item: (int(item[3]), item[2], str(item[1])),
    )
    for previous, current in zip(stepped, stepped[1:]):
        registry.add_edge(
            LineageEdge(
                source=previous[0],
                target=current[0],
                edge_type="advances_to",
                metadata={"scope": "legacy_run", "from_step": previous[3], "to_step": current[3]},
            )
        )
    if stepped:
        for node_id, _, role, _ in checkpoint_nodes:
            if role == "latest":
                registry.add_edge(
                    LineageEdge(
                        source=stepped[-1][0],
                        target=node_id,
                        edge_type="advances_to",
                        metadata={"scope": "legacy_run", "to_role": "latest"},
                    )
                )


def _add_legacy_parent_edges(
    registry: LineageRegistry,
    run_node_id: str,
    run_dir: Path,
    config: dict[str, Any],
    metadata: dict[str, Any],
    path_index: dict[str, str],
) -> None:
    for key in ("resume_checkpoint_path", "initial_checkpoint_path", "opponent_checkpoint_path"):
        parent_path = _resolve_legacy_path(run_dir, config.get(key) or metadata.get(key))
        if parent_path is None:
            continue
        parent_id = path_index.get(str(parent_path.resolve()), _path_id("external_checkpoint", parent_path))
        registry.add_node(
            LineageNode(
                id=parent_id,
                node_type="external_checkpoint",
                label=parent_path.name,
                path=str(parent_path),
                metadata={"declared_by": str(run_dir), "source_field": key},
            )
        )
        edge_type = "resume" if key in {"resume_checkpoint_path", "initial_checkpoint_path"} else "uses_opponent"
        registry.add_edge(
            LineageEdge(
                source=parent_id,
                target=run_node_id,
                edge_type=edge_type,
                metadata={"source_field": key},
            )
        )


def _add_legacy_arena_results(registry: LineageRegistry, run_node_id: str, run_dir: Path) -> None:
    for path in sorted(run_dir.glob("arena_*")):
        if not path.is_file() or path.suffix not in {".csv", ".md", ".json"}:
            continue
        node_id = _path_id("arena_result", path)
        registry.add_node(
            LineageNode(
                id=node_id,
                node_type="arena_result",
                label=path.name,
                path=str(path),
                metadata={"legacy": True, "artifact_type": path.suffix.lstrip(".")},
            )
        )
        registry.add_edge(LineageEdge(source=run_node_id, target=node_id, edge_type="evaluates", metadata={"legacy": True}))


def _add_suite_manifest(registry: LineageRegistry, manifest_path: Path) -> None:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SUITE_SCHEMA_VERSION:
        return
    suite = manifest.get("suite", {})
    suite_name = str(suite.get("name") or manifest_path.parent.name)
    suite_id = f"suite:{suite_name}"
    registry.add_node(
        LineageNode(
            id=suite_id,
            node_type="suite",
            label=suite_name,
            path=str(manifest_path.parent),
            metadata={"manifest_path": str(manifest_path), "digest": suite.get("digest")},
        )
    )
    for record in manifest.get("records", []):
        if not isinstance(record, dict):
            continue
        run_id = record.get("run_id")
        if not run_id:
            continue
        run_node_id = f"run:{run_id}"
        registry.add_node(
            LineageNode(
                id=run_node_id,
                node_type="run",
                label=str(run_id),
                path=record.get("run_dir"),
                metadata={
                    "scenario": record.get("scenario"),
                    "seed": record.get("seed"),
                    "replicate": record.get("replicate"),
                    "suite": suite_name,
                    "metrics": record.get("metrics", {}),
                },
            )
        )
        registry.add_edge(LineageEdge(source=suite_id, target=run_node_id, edge_type="includes"))


def _add_benchmark_manifest(registry: LineageRegistry, manifest_path: Path) -> None:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        return
    name = str(manifest.get("name") or manifest_path.parent.name)
    benchmark_id = f"benchmark_suite:{name}:{str(manifest.get('digest', ''))[:12]}"
    registry.add_node(
        LineageNode(
            id=benchmark_id,
            node_type="benchmark_suite",
            label=name,
            path=str(manifest_path.parent),
            metadata={
                "manifest_path": str(manifest_path),
                "digest": manifest.get("digest"),
                "recommended_model": manifest.get("recommended_model", {}),
                "dry_run": manifest.get("dry_run"),
            },
        )
    )
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict) or not artifact.get("path"):
            continue
        artifact_path = Path(str(artifact["path"]))
        node_id = _path_id("benchmark_artifact", artifact_path)
        registry.add_node(
            LineageNode(
                id=node_id,
                node_type="benchmark_artifact",
                label=artifact_path.name,
                path=str(artifact_path),
                metadata={
                    "role": artifact.get("role"),
                    "artifact_type": artifact.get("artifact_type"),
                },
            )
        )
        registry.add_edge(LineageEdge(source=benchmark_id, target=node_id, edge_type="evaluates"))


def build_registry(roots: Iterable[str | Path]) -> LineageRegistry:
    registry = LineageRegistry()
    path_index: dict[str, str] = {}
    manifest_paths = []
    suite_paths = []
    benchmark_paths = []
    legacy_run_dirs: set[Path] = set()
    for root in roots:
        base = Path(root)
        if base.is_file():
            candidates = [base]
        else:
            candidates = list(base.rglob("*.json")) if base.exists() else []
            if base.exists():
                for path in base.rglob("summary.json"):
                    legacy_run_dirs.add(path.parent)
                for path in base.rglob("metadata.json"):
                    legacy_run_dirs.add(path.parent)
                for path in base.rglob("checkpoints"):
                    if path.is_dir():
                        legacy_run_dirs.add(path.parent)
                for path in base.rglob("*.pt"):
                    if path.parent.name != "checkpoints":
                        legacy_run_dirs.add(path.parent)
        for path in candidates:
            if path.name == "artifact_manifest.json":
                manifest_paths.append(path)
            elif path.name == "suite_manifest.json":
                suite_paths.append(path)
            elif path.name == "benchmark_manifest.json":
                benchmark_paths.append(path)
    for path in sorted(manifest_paths):
        _add_artifact_manifest(registry, path, path_index)
    manifest_run_dirs = {path.parent.resolve() for path in manifest_paths}
    for path in sorted(legacy_run_dirs):
        if path.resolve() not in manifest_run_dirs:
            _add_legacy_run(registry, path, path_index)
    for path in sorted(suite_paths):
        _add_suite_manifest(registry, path)
    for path in sorted(benchmark_paths):
        _add_benchmark_manifest(registry, path)
    return registry


def validate_registry(registry: LineageRegistry) -> list[dict[str, Any]]:
    issues = []
    for node in registry.nodes.values():
        if node.path and not Path(node.path).exists():
            issues.append({"type": "missing_path", "node_id": node.id, "path": node.path})
    for edge in registry.edges:
        if edge.source not in registry.nodes:
            issues.append({"type": "missing_source", "edge": asdict(edge)})
        if edge.target not in registry.nodes:
            issues.append({"type": "missing_target", "edge": asdict(edge)})
    return issues


def ancestors(registry: LineageRegistry, node_id: str) -> list[str]:
    reverse: dict[str, list[str]] = {}
    for edge in registry.edges:
        reverse.setdefault(edge.target, []).append(edge.source)
    return _walk(reverse, node_id)


def descendants(registry: LineageRegistry, node_id: str) -> list[str]:
    forward: dict[str, list[str]] = {}
    for edge in registry.edges:
        forward.setdefault(edge.source, []).append(edge.target)
    return _walk(forward, node_id)


def _walk(graph: dict[str, list[str]], start: str) -> list[str]:
    seen = set()
    ordered = []
    stack = list(graph.get(start, []))
    while stack:
        node_id = stack.pop(0)
        if node_id in seen:
            continue
        seen.add(node_id)
        ordered.append(node_id)
        stack.extend(graph.get(node_id, []))
    return ordered


def write_registry(registry: LineageRegistry, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(registry.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown_report(registry: LineageRegistry, path: str | Path) -> None:
    runs = [node for node in registry.nodes.values() if node.node_type == "run"]
    checkpoints = [node for node in registry.nodes.values() if node.node_type == "checkpoint"]
    issues = validate_registry(registry)
    lines = [
        "# Model Lineage Report",
        "",
        f"- runs: {len(runs)}",
        f"- checkpoints: {len(checkpoints)}",
        f"- edges: {len(registry.edges)}",
        f"- issues: {len(issues)}",
        "",
        "## Runs",
        "",
        "| run | trainer/scenario | seed | key metrics |",
        "|---|---|---:|---|",
    ]
    for node in sorted(runs, key=lambda item: item.label):
        metadata = node.metadata
        metrics = metadata.get("metrics") or {}
        trainer = metadata.get("trainer_name") or metadata.get("scenario") or "-"
        seed = metadata.get("seed")
        key_metrics = ", ".join(
            f"{key}={value:.3f}" if isinstance(value, float) else f"{key}={value}"
            for key, value in sorted(metrics.items())
            if key in {"mean_win_rate", "mean_episode_score", "mean_max_chain", "global_step", "episodes"}
        )
        lines.append(f"| `{node.label}` | {trainer} | {seed if seed is not None else ''} | {key_metrics or '-'} |")
    lines.extend(["", "## Checkpoints", "", "| checkpoint | role | path |", "|---|---|---|"])
    for node in sorted(checkpoints, key=lambda item: item.label):
        lines.append(f"| `{node.label}` | {node.metadata.get('role', '')} | `{node.path}` |")
    if issues:
        lines.extend(["", "## Issues", ""])
        for issue in issues:
            lines.append(f"- `{issue['type']}`: `{issue.get('node_id') or issue.get('edge')}`")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build a local model lineage registry.")
    parser.add_argument("--root", action="append", required=True, help="Root directory or manifest file to scan.")
    parser.add_argument("--output", default="lineage_registry.json")
    parser.add_argument("--markdown", default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    registry = build_registry(args.root)
    write_registry(registry, args.output)
    if args.markdown:
        write_markdown_report(registry, args.markdown)
    print(f"nodes: {len(registry.nodes)}")
    print(f"edges: {len(registry.edges)}")
    print(f"issues: {len(validate_registry(registry))}")
    print(f"registry: {args.output}")
    if args.markdown:
        print(f"report: {args.markdown}")


if __name__ == "__main__":
    main()
