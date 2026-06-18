"""Local model lineage registry built from artifact manifests."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from train.artifacts import ARTIFACT_MANIFEST_SCHEMA_VERSION, json_digest
from train.experiment_suite import SUITE_SCHEMA_VERSION

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


def _resolve_record_path(manifest_path: Path, record_path: str) -> Path:
    path = Path(record_path)
    if path.is_absolute():
        return path
    return manifest_path.parent / path


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
        registry.add_edge(LineageEdge(source=run_node_id, target=node_id, edge_type="uses_opponent"))


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


def build_registry(roots: Iterable[str | Path]) -> LineageRegistry:
    registry = LineageRegistry()
    path_index: dict[str, str] = {}
    manifest_paths = []
    suite_paths = []
    for root in roots:
        base = Path(root)
        if base.is_file():
            candidates = [base]
        else:
            candidates = list(base.rglob("*.json")) if base.exists() else []
        for path in candidates:
            if path.name == "artifact_manifest.json":
                manifest_paths.append(path)
            elif path.name == "suite_manifest.json":
                suite_paths.append(path)
    for path in sorted(manifest_paths):
        _add_artifact_manifest(registry, path, path_index)
    for path in sorted(suite_paths):
        _add_suite_manifest(registry, path)
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
