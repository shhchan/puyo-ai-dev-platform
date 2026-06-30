"""Replay diagnostics and model lineage viewer data/model helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    import pygame
except ImportError:  # pragma: no cover - dependency guard
    pygame = None

from train.lineage import build_registry, validate_registry


SCREEN_WIDTH = 1100
SCREEN_HEIGHT = 700
FPS = 60
BACKGROUND = (18, 22, 30)
PANEL = (31, 38, 50)
PANEL_ACTIVE = (48, 60, 78)
TEXT = (235, 238, 245)
MUTED = (158, 169, 188)
ACCENT = (79, 199, 163)
WARNING = (238, 188, 94)
ERROR = (238, 112, 112)
NODE_COLORS = {
    "run": (79, 199, 163),
    "checkpoint": (238, 188, 94),
    "arena_result": (126, 178, 255),
    "benchmark": (190, 143, 255),
    "benchmark_artifact": (170, 180, 198),
    "external_checkpoint": (238, 112, 112),
}


@dataclass(frozen=True)
class ReplayTimelineEntry:
    tick: int
    snapshot_hash: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    policy_diagnostics: dict[str, Any] = field(default_factory=dict)
    controller_diagnostics: dict[str, Any] = field(default_factory=dict)
    controller_status: dict[str, Any] = field(default_factory=dict)

    @property
    def plan_ids(self) -> tuple[str, ...]:
        ids = []
        for diagnostics in self.policy_diagnostics.values():
            if isinstance(diagnostics, Mapping) and diagnostics.get("plan_id"):
                ids.append(str(diagnostics["plan_id"]))
        return tuple(ids)


@dataclass(frozen=True)
class LineageSummary:
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    issues: tuple[dict[str, Any], ...]

    @property
    def checkpoints(self) -> tuple[dict[str, Any], ...]:
        return tuple(node for node in self.nodes if node.get("node_type") == "checkpoint")

    @property
    def runs(self) -> tuple[dict[str, Any], ...]:
        return tuple(node for node in self.nodes if node.get("node_type") == "run")

    def node_by_id(self, node_id: str | None) -> dict[str, Any] | None:
        if node_id is None:
            return None
        for node in self.nodes:
            if node.get("id") == node_id:
                return node
        return None

    def node_by_path(self, path: str | Path | None) -> dict[str, Any] | None:
        if path is None:
            return None
        target = _path_key(path)
        for node in self.nodes:
            node_path = node.get("path")
            if node_path is not None and _path_key(node_path) == target:
                return node
        return None

    def incoming_edges(self, node_id: str | None) -> tuple[dict[str, Any], ...]:
        if node_id is None:
            return ()
        return tuple(edge for edge in self.edges if edge.get("target") == node_id)

    def outgoing_edges(self, node_id: str | None) -> tuple[dict[str, Any], ...]:
        if node_id is None:
            return ()
        return tuple(edge for edge in self.edges if edge.get("source") == node_id)

    def parent_nodes(self, node_id: str | None) -> tuple[dict[str, Any], ...]:
        return tuple(
            node for edge in self.incoming_edges(node_id)
            if (node := self.node_by_id(str(edge.get("source")))) is not None
        )

    def child_nodes(self, node_id: str | None) -> tuple[dict[str, Any], ...]:
        return tuple(
            node for edge in self.outgoing_edges(node_id)
            if (node := self.node_by_id(str(edge.get("target")))) is not None
        )


@dataclass(frozen=True)
class ModelViewerData:
    replay_path: str | None
    replay_format: str
    seed: int | None
    expected_final_hash: str
    policy_metadata: dict[str, Any]
    timeline: tuple[ReplayTimelineEntry, ...]
    lineage: LineageSummary
    model_registry: dict[str, Any]

    def to_report(self, *, selected_tick: int | None = None, bookmarks: tuple[int, ...] = ()) -> dict[str, Any]:
        return {
            "schema_version": "puyo.model_viewer_report.v1",
            "replay": {
                "path": self.replay_path,
                "format": self.replay_format,
                "seed": self.seed,
                "expected_final_hash": self.expected_final_hash,
                "policies": self.policy_metadata,
                "ticks": len(self.timeline),
                "selected_tick": selected_tick,
                "bookmarks": list(bookmarks),
                "plan_ids": sorted({plan_id for entry in self.timeline for plan_id in entry.plan_ids}),
            },
            "lineage": {
                "runs": len(self.lineage.runs),
                "checkpoints": len(self.lineage.checkpoints),
                "nodes": len(self.lineage.nodes),
                "edges": len(self.lineage.edges),
                "issues": list(self.lineage.issues),
            },
            "model_registry": dict(self.model_registry),
        }


class ModelViewerController:
    def __init__(self, data: ModelViewerData):
        self.data = data
        self.index = 0
        self.paused = True
        self.playback_stride = 1
        self.lineage_order = _lineage_order(data.lineage)
        self.lineage_index = 0
        self.bookmarks: set[int] = set()
        self.message = "ready" if data.timeline else "lineage only"
        self.focus_replay_checkpoint()

    @property
    def selected_entry(self) -> ReplayTimelineEntry | None:
        if not self.data.timeline:
            return None
        return self.data.timeline[self.index]

    def seek(self, delta: int) -> None:
        if not self.data.timeline:
            return
        self.index = max(0, min(len(self.data.timeline) - 1, self.index + delta))
        entry = self.selected_entry
        self.message = "ready" if entry is None else f"tick {entry.tick}"

    def toggle_pause(self) -> None:
        if not self.data.timeline:
            self.paused = True
            self.message = "lineage only"
            return
        self.paused = not self.paused
        self.message = "paused" if self.paused else "playing"

    def change_speed(self, delta: int) -> None:
        choices = (1, 2, 4, 8)
        index = choices.index(self.playback_stride)
        self.playback_stride = choices[max(0, min(len(choices) - 1, index + delta))]
        self.message = f"speed {self.playback_stride}x"

    def toggle_bookmark(self) -> None:
        entry = self.selected_entry
        if entry is None:
            return
        if entry.tick in self.bookmarks:
            self.bookmarks.remove(entry.tick)
            self.message = f"removed bookmark tick {entry.tick}"
        else:
            self.bookmarks.add(entry.tick)
            self.message = f"bookmarked tick {entry.tick}"

    def advance_playback(self) -> None:
        if self.data.timeline and not self.paused:
            self.seek(self.playback_stride)

    @property
    def selected_lineage_id(self) -> str | None:
        if not self.lineage_order:
            return None
        return self.lineage_order[self.lineage_index]

    @property
    def selected_lineage_node(self) -> dict[str, Any] | None:
        return self.data.lineage.node_by_id(self.selected_lineage_id)

    @property
    def selected_lineage_parents(self) -> tuple[dict[str, Any], ...]:
        return self.data.lineage.parent_nodes(self.selected_lineage_id)

    @property
    def selected_lineage_children(self) -> tuple[dict[str, Any], ...]:
        return self.data.lineage.child_nodes(self.selected_lineage_id)

    def seek_lineage(self, delta: int) -> None:
        if not self.lineage_order:
            return
        self.lineage_index = max(0, min(len(self.lineage_order) - 1, self.lineage_index + delta))
        node = self.selected_lineage_node
        self.message = "lineage selected" if node is None else f"lineage {node.get('label', node.get('id', '-'))}"

    def focus_replay_checkpoint(self) -> bool:
        for policy in self.data.policy_metadata.values():
            if not isinstance(policy, Mapping):
                continue
            checkpoint_node = self.data.lineage.node_by_path(policy.get("checkpoint_path"))
            if checkpoint_node is None or checkpoint_node.get("id") not in self.lineage_order:
                continue
            self.lineage_index = self.lineage_order.index(str(checkpoint_node["id"]))
            self.message = f"lineage {checkpoint_node.get('label', checkpoint_node.get('id', '-'))}"
            return True
        return False

    def report(self) -> dict[str, Any]:
        entry = self.selected_entry
        report = self.data.to_report(
            selected_tick=None if entry is None else entry.tick,
            bookmarks=tuple(sorted(self.bookmarks)),
        )
        selected = self.selected_lineage_node
        report["replay"]["playback_stride"] = self.playback_stride
        report["replay"]["mode"] = "timeline" if self.data.timeline else "lineage_only"
        report["replay"]["selected_entry"] = {} if entry is None else summarize_replay_entry(
            entry,
            self.data.policy_metadata,
            self.data.lineage,
        )
        report["lineage"]["selected_node"] = {} if selected is None else {
            "id": selected.get("id"),
            "label": selected.get("label"),
            "node_type": selected.get("node_type"),
            "path": selected.get("path"),
            "parents": [node.get("id") for node in self.selected_lineage_parents],
            "children": [node.get("id") for node in self.selected_lineage_children],
        }
        return report


def load_replay_timeline(
    path: str | Path | None,
) -> tuple[str | None, str, int | None, str, dict[str, Any], tuple[ReplayTimelineEntry, ...]]:
    if path is None:
        return None, "none", None, "", {}, ()
    replay_path = Path(path)
    payload = json.loads(replay_path.read_text(encoding="utf-8"))
    replay_format = str(payload.get("format", "puyo-realtime-fixture-v1"))
    seed = payload.get("seed")
    expected_final_hash = str(payload.get("expected_final_hash", ""))
    policy_metadata = dict(payload.get("policies", {}))
    if replay_format == "puyo-realtime-match-v1" and isinstance(payload.get("ticks"), list):
        entries = tuple(
            ReplayTimelineEntry(
                tick=int(item.get("tick", index)),
                snapshot_hash=str(item.get("snapshot_hash", "")),
                inputs=dict(item.get("inputs", {})),
                policy_diagnostics=dict(item.get("policy_diagnostics", {})),
                controller_diagnostics=dict(item.get("controller_diagnostics", {})),
                controller_status=dict(item.get("controller_status", {})),
            )
            for index, item in enumerate(payload["ticks"])
            if isinstance(item, dict)
        )
        return (
            str(replay_path),
            replay_format,
            int(seed) if seed is not None else None,
            expected_final_hash,
            policy_metadata,
            entries,
        )
    capture_every = int(payload.get("capture_every", 1))
    hashes = payload.get("expected_hashes", [])
    entries = tuple(
        ReplayTimelineEntry(tick=(index + 1) * capture_every, snapshot_hash=str(snapshot_hash))
        for index, snapshot_hash in enumerate(hashes)
    )
    if not entries and "ticks" in payload:
        entries = tuple(ReplayTimelineEntry(tick=int(payload["ticks"]), snapshot_hash=expected_final_hash))
    return (
        str(replay_path),
        replay_format,
        int(seed) if seed is not None else None,
        expected_final_hash,
        policy_metadata,
        entries,
    )


def build_lineage_summary(roots: tuple[str, ...]) -> LineageSummary:
    registry = build_registry(roots) if roots else build_registry(("runs", "docs/benchmarks"))
    registry_dict = registry.to_dict()
    return LineageSummary(
        nodes=tuple(registry_dict.get("nodes", ())),
        edges=tuple(registry_dict.get("edges", ())),
        issues=tuple(validate_registry(registry)),
    )


def build_model_viewer_data(
    *,
    replay_path: str | Path | None = None,
    lineage_roots: tuple[str, ...] = (),
    model_registry_path: str | Path | None = "runs/model_registry.json",
) -> ModelViewerData:
    replay_path_str, replay_format, seed, expected_final_hash, policy_metadata, timeline = load_replay_timeline(replay_path)
    return ModelViewerData(
        replay_path=replay_path_str,
        replay_format=replay_format,
        seed=seed,
        expected_final_hash=expected_final_hash,
        policy_metadata=policy_metadata,
        timeline=timeline,
        lineage=build_lineage_summary(lineage_roots),
        model_registry=load_model_registry_summary(model_registry_path),
    )


def load_model_registry_summary(path: str | Path | None) -> dict[str, Any]:
    if path is None or not Path(path).is_file():
        return {"path": None if path is None else str(path), "available": False, "roles": {}, "last_transition": None}
    target = Path(path)
    try:
        registry = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"path": str(target), "available": False, "roles": {}, "last_transition": None}
    if not isinstance(registry, Mapping) or registry.get("schema_version") != "puyo.model_role_registry.v1":
        return {"path": str(target), "available": False, "roles": {}, "last_transition": None}
    roles = registry.get("roles", {})
    evaluations = registry.get("evaluations", [])
    transitions = registry.get("transitions", [])
    return {
        "path": str(target),
        "available": True,
        "revision": registry.get("revision"),
        "updated_at_utc": registry.get("updated_at_utc"),
        "roles": dict(roles) if isinstance(roles, Mapping) else {},
        "last_evaluation": evaluations[-1] if isinstance(evaluations, list) and evaluations else None,
        "last_transition": transitions[-1] if isinstance(transitions, list) and transitions else None,
        "opponent_pool_size": len(registry.get("opponent_pool", [])),
    }


def write_viewer_report(report: Mapping[str, Any], *, json_path: str | Path | None = None, markdown_path: str | Path | None = None) -> None:
    if json_path is not None:
        target = Path(json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if markdown_path is not None:
        target = Path(markdown_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        replay = report.get("replay", {})
        lineage = report.get("lineage", {})
        model_registry = report.get("model_registry", {})
        selected_entry = replay.get("selected_entry", {})
        lines = [
            "# Puyo Model Viewer Report",
            "",
            "## Replay",
            "",
            f"- path: `{replay.get('path') or '-'}`",
            f"- format: `{replay.get('format') or '-'}`",
            f"- seed: `{replay.get('seed')}`",
            f"- mode: `{replay.get('mode')}`",
            f"- ticks: `{replay.get('ticks')}`",
            f"- selected_tick: `{replay.get('selected_tick')}`",
            f"- bookmarks: `{replay.get('bookmarks')}`",
        ]
        agents = selected_entry.get("agents", {}) if isinstance(selected_entry, Mapping) else {}
        if agents:
            lines.extend(["", "### Selected Tick Decisions", ""])
            for agent, payload in agents.items():
                decision = payload.get("decision", {})
                diagnostics = payload.get("diagnostics", {})
                plan = payload.get("plan", {})
                lines.extend(
                    [
                        f"- {agent}: `{payload.get('policy_type')}` input `{payload.get('input')}`",
                        f"  - action: `{decision.get('action_index')}` x`{decision.get('axis_x')}` `{decision.get('rotation')}` reason `{decision.get('reason')}`",
                        f"  - profile: `{diagnostics.get('profile_name') or '-'}` expanded `{diagnostics.get('expanded_nodes') or '-'}`",
                        f"  - plan: `{plan.get('plan_id') or '-'}`",
                    ]
                )
                lineage_node_id = payload.get("lineage_node_id")
                checkpoint_path = payload.get("checkpoint_path")
                if lineage_node_id or checkpoint_path:
                    lines.append(
                        f"  - checkpoint: `{lineage_node_id or checkpoint_path}` ancestors `{payload.get('lineage_ancestors', [])}`"
                    )
        lines.extend(
            [
                "",
                "## Model Roles",
                "",
                f"- registry: `{model_registry.get('path') or '-'}`",
                f"- revision: `{model_registry.get('revision')}`",
                *[
                    f"- {role}: `{(model_registry.get('roles', {}).get(role) or {}).get('path', '-')}`"
                    for role in ("champion", "challenger", "previous_stable")
                ],
                f"- last_transition: `{(model_registry.get('last_transition') or {}).get('kind', '-')}`",
                f"- last_evaluation: `{(model_registry.get('last_evaluation') or {}).get('artifact_path', '-')}`",
                "",
                "## Lineage",
                "",
                f"- runs: `{lineage.get('runs')}`",
                f"- checkpoints: `{lineage.get('checkpoints')}`",
                f"- nodes: `{lineage.get('nodes')}`",
                f"- edges: `{lineage.get('edges')}`",
                f"- issues: `{len(lineage.get('issues', []))}`",
                f"- selected_node: `{(lineage.get('selected_node') or {}).get('id', '-')}`",
            ]
        )
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_replay_entry(
    entry: ReplayTimelineEntry,
    policy_metadata: Mapping[str, Any],
    lineage: LineageSummary | None = None,
) -> dict[str, Any]:
    agents = {}
    for agent in sorted(set(entry.inputs) | set(entry.policy_diagnostics) | set(entry.controller_diagnostics)):
        policy = policy_metadata.get(agent, {}) if isinstance(policy_metadata.get(agent, {}), Mapping) else {}
        checkpoint_node = lineage.node_by_path(policy.get("checkpoint_path")) if lineage is not None else None
        diagnostics = entry.policy_diagnostics.get(agent, {})
        controller = entry.controller_diagnostics.get(agent, {})
        decision = controller.get("last_decision", {}) if isinstance(controller, Mapping) else {}
        plan = diagnostics.get("plan", {}) if isinstance(diagnostics, Mapping) else {}
        objective = diagnostics.get("search_objective", {}) if isinstance(diagnostics, Mapping) else {}
        if not objective and isinstance(plan, Mapping):
            objective = plan.get("objective", {})
        first_step = _first_plan_step(plan)
        search_diagnostics = diagnostics.get("search_diagnostics", {}) if isinstance(diagnostics, Mapping) else {}
        agents[agent] = {
            "policy_type": policy.get("policy_type", agent),
            "checkpoint_path": policy.get("checkpoint_path"),
            "lineage_node_id": None if checkpoint_node is None else checkpoint_node.get("id"),
            "lineage_ancestors": (
                [] if checkpoint_node is None or lineage is None
                else _lineage_ancestor_ids(lineage, str(checkpoint_node["id"]))
            ),
            "input": _input_label(entry.inputs.get(agent, {})),
            "decision": {
                "action_index": decision.get("action_index"),
                "axis_x": decision.get("axis_x"),
                "rotation": decision.get("rotation"),
                "reason": decision.get("reason"),
                "reachable": decision.get("reachable"),
                "plan_ticks": decision.get("plan_ticks"),
                "policy_elapsed_ms": _seconds_to_ms(decision.get("policy_elapsed_seconds")),
            },
            "diagnostics": {
                "profile_name": (
                    diagnostics.get("profile_name")
                    or plan.get("profile_name")
                    or objective.get("source_profile_name")
                    if isinstance(diagnostics, Mapping) and isinstance(plan, Mapping) and isinstance(objective, Mapping)
                    else ""
                ),
                "strategy": diagnostics.get("strategy", "") if isinstance(diagnostics, Mapping) else "",
                "reason": diagnostics.get("reason", "") if isinstance(diagnostics, Mapping) else "",
                "target_attack": diagnostics.get("target_attack", 0) if isinstance(diagnostics, Mapping) else 0,
                "deadline": diagnostics.get("deadline", 0) if isinstance(diagnostics, Mapping) else 0,
                "expanded_nodes": (
                    diagnostics.get("expanded_nodes")
                    if isinstance(diagnostics, Mapping) and diagnostics.get("expanded_nodes")
                    else search_diagnostics.get("expanded_nodes")
                    if isinstance(search_diagnostics, Mapping)
                    else None
                ),
                "elapsed_ms": _seconds_to_ms(
                    diagnostics.get("elapsed_seconds")
                    if isinstance(diagnostics, Mapping)
                    else None
                ),
            },
            "objective": objective if isinstance(objective, Mapping) else {},
            "plan": {
                "plan_id": diagnostics.get("plan_id", "") if isinstance(diagnostics, Mapping) else "",
                "update_reason": diagnostics.get("plan_update_reason", "") if isinstance(diagnostics, Mapping) else "",
                "first_step": first_step,
            },
        }
    return {
        "tick": entry.tick,
        "snapshot_hash": entry.snapshot_hash,
        "agents": agents,
    }


def replay_entry_display_lines(
    entry: ReplayTimelineEntry,
    policy_metadata: Mapping[str, Any],
    lineage: LineageSummary | None = None,
) -> tuple[str, ...]:
    summary = summarize_replay_entry(entry, policy_metadata, lineage)
    lines = []
    for agent, payload in summary["agents"].items():
        decision = payload["decision"]
        diagnostics = payload["diagnostics"]
        objective = payload["objective"]
        plan = payload["plan"]
        first_step = plan.get("first_step") or {}
        elapsed = decision.get("policy_elapsed_ms")
        elapsed_text = "-" if elapsed is None else f"{elapsed:.1f}ms"
        lines.extend(
            [
                f"{agent} {payload['policy_type']} input {payload['input']}",
                (
                    f"  decision action {decision.get('action_index')} "
                    f"x{decision.get('axis_x')} {decision.get('rotation')} "
                    f"{decision.get('reason')} {elapsed_text}"
                ),
            ]
        )
        profile = diagnostics.get("profile_name") or "-"
        objective_kind = objective.get("kind", "-") if isinstance(objective, Mapping) else "-"
        target_chain = objective.get("target_chain", "-") if isinstance(objective, Mapping) else "-"
        expanded = diagnostics.get("expanded_nodes")
        lines.append(
            f"  profile {profile} objective {objective_kind} chain {target_chain} expanded {expanded or '-'}"
        )
        if plan.get("plan_id"):
            lines.append(
                f"  plan {str(plan['plan_id'])[:10]} step1 action {first_step.get('action')} "
                f"x{first_step.get('axis_x')} {first_step.get('rotation')}"
            )
        if payload.get("lineage_node_id"):
            lines.append(f"  checkpoint {str(payload['lineage_node_id'])[:54]}")
        elif payload.get("checkpoint_path"):
            lines.append(f"  checkpoint {str(payload['checkpoint_path'])[:54]}")
        if diagnostics.get("reason"):
            lines.append(f"  why {str(diagnostics['reason'])[:54]}")
    return tuple(lines)


class ModelViewerRenderer:
    def __init__(self, screen):
        self.screen = screen
        self.title_font = _font(30, bold=True)
        self.heading_font = _font(22, bold=True)
        self.font = _font(17)
        self.small_font = _font(14, monospace=True)

    def draw(self, controller: ModelViewerController) -> None:
        self.screen.fill(BACKGROUND)
        self._draw_header(controller)
        self._draw_replay_panel(controller)
        self._draw_lineage_panel(controller)
        pygame.display.flip()

    def _draw_header(self, controller: ModelViewerController) -> None:
        self._draw_text("Model / Replay / Lineage Viewer", self.title_font, TEXT, (32, 24))
        entry = controller.selected_entry
        if entry is None:
            status = f"LINEAGE ONLY  {controller.message}"
        else:
            status = (
                f"{'PAUSED' if controller.paused else 'PLAYING'}  "
                f"speed {controller.playback_stride}x  tick {entry.tick}  {controller.message}"
            )
        self._draw_text(status, self.font, ACCENT, (34, 64))
        controls = "keys: Left/Right seek  Space play/pause  +/- speed  b bookmark  PgUp/PgDn lineage  c checkpoint"
        self._draw_text(controls, self.small_font, MUTED, (34, 86))

    def _draw_replay_panel(self, controller: ModelViewerController) -> None:
        rect = pygame.Rect(32, 104, 500, 548)
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=6)
        pygame.draw.rect(self.screen, ACCENT, rect, 2)
        self._draw_text("Replay timeline", self.heading_font, TEXT, (rect.x + 18, rect.y + 16))
        entry = controller.selected_entry
        if entry is None:
            self._draw_text("No replay loaded", self.font, MUTED, (rect.x + 18, rect.y + 58))
            return
        details = [
            f"path: {controller.data.replay_path or '-'}",
            f"format: {controller.data.replay_format}",
            f"seed: {controller.data.seed}",
            f"tick: {entry.tick}",
            f"hash: {entry.snapshot_hash[:24] or '-'}",
            f"plans: {', '.join(entry.plan_ids) or '-'}",
        ]
        y = rect.y + 58
        for line in details:
            self._draw_text(line, self.small_font, TEXT, (rect.x + 18, y), width=rect.width - 36)
            y += 24
        y += 6
        for line in replay_entry_display_lines(entry, controller.data.policy_metadata, controller.data.lineage)[:12]:
            color = WARNING if not line.startswith("  ") else TEXT
            self._draw_text(line, self.small_font, color, (rect.x + 18, y), width=rect.width - 36)
            y += 20
        timeline_y = max(y + 10, rect.bottom - 154)
        for offset, item in enumerate(controller.data.timeline[max(0, controller.index - 3) : controller.index + 5]):
            selected = item.tick == entry.tick
            row = pygame.Rect(rect.x + 18, timeline_y + offset * 24, rect.width - 36, 20)
            if row.bottom > rect.bottom - 14:
                break
            pygame.draw.rect(self.screen, PANEL_ACTIVE if selected else BACKGROUND, row, border_radius=4)
            label = f"{item.tick:>5}  {item.snapshot_hash[:18] or '-'}"
            self._draw_text(label, self.small_font, WARNING if selected else MUTED, (row.x + 8, row.y + 2))

    def _draw_lineage_panel(self, controller: ModelViewerController) -> None:
        rect = pygame.Rect(568, 104, 500, 548)
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=6)
        pygame.draw.rect(self.screen, WARNING, rect, 2)
        self._draw_text("Model roles / Lineage", self.heading_font, TEXT, (rect.x + 18, rect.y + 16))
        summary = controller.data.lineage
        y = rect.y + 58
        model_registry = controller.data.model_registry
        roles = model_registry.get("roles", {}) if isinstance(model_registry, Mapping) else {}
        for role in ("champion", "challenger", "previous_stable"):
            record = roles.get(role) if isinstance(roles, Mapping) else None
            label = "-" if not isinstance(record, Mapping) else Path(str(record.get("path", "-"))).name
            self._draw_text(f"{role}: {label}", self.small_font, ACCENT, (rect.x + 18, y), width=rect.width - 36)
            y += 20
        transition = model_registry.get("last_transition") if isinstance(model_registry, Mapping) else None
        transition_kind = transition.get("kind", "-") if isinstance(transition, Mapping) else "-"
        self._draw_text(f"last transition: {transition_kind}", self.small_font, WARNING, (rect.x + 18, y))
        y += 24
        stats = [
            f"runs: {len(summary.runs)}",
            f"checkpoints: {len(summary.checkpoints)}",
            f"nodes: {len(summary.nodes)}",
            f"edges: {len(summary.edges)}",
            f"issues: {len(summary.issues)}",
        ]
        for line in stats:
            self._draw_text(line, self.small_font, TEXT, (rect.x + 18, y))
            y += 24
        graph_rect = pygame.Rect(rect.x + 18, y + 8, rect.width - 36, 126)
        self._draw_lineage_graph(controller, graph_rect)
        detail_rect = pygame.Rect(rect.x + 18, graph_rect.bottom + 14, rect.width - 36, rect.bottom - graph_rect.bottom - 28)
        self._draw_lineage_detail(controller, detail_rect)

    def _draw_lineage_graph(self, controller: ModelViewerController, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, BACKGROUND, rect, border_radius=5)
        pygame.draw.rect(self.screen, (82, 92, 112), rect, 1, border_radius=5)
        selected = controller.selected_lineage_node
        if selected is None:
            self._draw_text("No lineage nodes", self.font, MUTED, (rect.x + 14, rect.y + 14))
            return

        parents = controller.selected_lineage_parents[:4]
        children = controller.selected_lineage_children[:4]
        center = (rect.centerx, rect.centery)
        parent_positions = _vertical_positions(rect.x + 84, rect.y + 44, rect.bottom - 44, len(parents))
        child_positions = _vertical_positions(rect.right - 84, rect.y + 44, rect.bottom - 44, len(children))

        for node, pos in zip(parents, parent_positions):
            pygame.draw.line(self.screen, (105, 120, 148), pos, center, 2)
            self._draw_lineage_node(node, pos, selected=False)
        for node, pos in zip(children, child_positions):
            pygame.draw.line(self.screen, (105, 120, 148), center, pos, 2)
            self._draw_lineage_node(node, pos, selected=False)
        self._draw_lineage_node(selected, center, selected=True)

        if not parents and not children:
            around = [
                controller.data.lineage.node_by_id(node_id)
                for node_id in controller.lineage_order[controller.lineage_index + 1 : controller.lineage_index + 5]
            ]
            for node, pos in zip((item for item in around if item is not None), child_positions or _vertical_positions(rect.right - 84, rect.y + 44, rect.bottom - 44, 4)):
                pygame.draw.line(self.screen, (80, 90, 112), center, pos, 1)
                self._draw_lineage_node(node, pos, selected=False)

    def _draw_lineage_node(self, node: dict[str, Any], center: tuple[int, int], *, selected: bool) -> None:
        node_type = str(node.get("node_type", "node"))
        color = NODE_COLORS.get(node_type, MUTED)
        width = 132 if selected else 112
        height = 42 if selected else 34
        rect = pygame.Rect(0, 0, width, height)
        rect.center = center
        pygame.draw.rect(self.screen, PANEL_ACTIVE if selected else PANEL, rect, border_radius=5)
        pygame.draw.rect(self.screen, color, rect, 2 if selected else 1, border_radius=5)
        label = str(node.get("label") or node.get("id") or "-")
        self._draw_text(label, self.small_font, TEXT, (rect.x + 8, rect.y + 6), width=rect.width - 16)
        self._draw_text(node_type, self.small_font, color, (rect.x + 8, rect.y + 22), width=rect.width - 16)

    def _draw_lineage_detail(self, controller: ModelViewerController, rect: pygame.Rect) -> None:
        selected = controller.selected_lineage_node
        if selected is None:
            return
        pygame.draw.rect(self.screen, BACKGROUND, rect, border_radius=5)
        pygame.draw.rect(self.screen, (82, 92, 112), rect, 1, border_radius=5)
        parents = ", ".join(str(node.get("label", "-")) for node in controller.selected_lineage_parents) or "-"
        children = ", ".join(str(node.get("label", "-")) for node in controller.selected_lineage_children) or "-"
        metadata = selected.get("metadata", {}) if isinstance(selected.get("metadata"), dict) else {}
        detail_lines = [
            f"selected: {selected.get('label', '-')}",
            f"type: {selected.get('node_type', '-')}",
            f"path: {selected.get('path') or '-'}",
            f"parents: {parents}",
            f"children: {children}",
            f"role: {metadata.get('role', '-')}",
            f"run: {metadata.get('run_id', '-')}",
        ]
        y = rect.y + 12
        for line in detail_lines:
            self._draw_text(line, self.small_font, TEXT, (rect.x + 10, y), width=rect.width - 20)
            y += 20

    def _draw_text(self, text: str, font, color, pos: tuple[int, int], *, width: int | None = None) -> None:
        text = str(text)
        if width is not None:
            text = _fit_text(text, font, width)
        self.screen.blit(font.render(text, True, color), pos)


def run_model_viewer(
    data: ModelViewerData,
    *,
    max_frames: int | None = None,
    report_json: str | Path | None = None,
    report_markdown: str | Path | None = None,
) -> dict[str, Any]:
    if pygame is None:
        controller = ModelViewerController(data)
        report = controller.report()
        write_viewer_report(report, json_path=report_json, markdown_path=report_markdown)
        return report
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Puyo Model Viewer")
    clock = pygame.time.Clock()
    controller = ModelViewerController(data)
    renderer = ModelViewerRenderer(screen)
    running = True
    frames = 0
    while running and (max_frames is None or frames < max_frames):
        clock.tick(FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key in (pygame.K_RIGHT, pygame.K_DOWN):
                    controller.seek(1)
                elif event.key in (pygame.K_LEFT, pygame.K_UP):
                    controller.seek(-1)
                elif event.key in (pygame.K_PAGEDOWN, pygame.K_TAB):
                    controller.seek_lineage(1)
                elif event.key == pygame.K_PAGEUP:
                    controller.seek_lineage(-1)
                elif event.key == pygame.K_SPACE:
                    controller.toggle_pause()
                elif event.key in (pygame.K_EQUALS, pygame.K_PLUS):
                    controller.change_speed(1)
                elif event.key in (pygame.K_MINUS, pygame.K_UNDERSCORE):
                    controller.change_speed(-1)
                elif event.key == pygame.K_b:
                    controller.toggle_bookmark()
                elif event.key == pygame.K_c:
                    if not controller.focus_replay_checkpoint():
                        controller.message = "no replay checkpoint in lineage"
        controller.advance_playback()
        renderer.draw(controller)
        frames += 1
    report = controller.report()
    write_viewer_report(report, json_path=report_json, markdown_path=report_markdown)
    pygame.quit()
    return report


def _lineage_order(summary: LineageSummary) -> tuple[str, ...]:
    priority = {
        "checkpoint": 0,
        "run": 1,
        "external_checkpoint": 2,
        "arena_result": 3,
        "benchmark": 4,
        "benchmark_artifact": 5,
    }
    nodes = sorted(
        summary.nodes,
        key=lambda node: (
            priority.get(str(node.get("node_type")), 99),
            str(node.get("label") or node.get("id") or ""),
        ),
    )
    return tuple(str(node["id"]) for node in nodes if node.get("id"))


def _lineage_ancestor_ids(summary: LineageSummary, node_id: str) -> list[str]:
    ancestors = []
    seen = set()
    stack = [str(node["id"]) for node in summary.parent_nodes(node_id) if node.get("id")]
    while stack:
        current = stack.pop(0)
        if current in seen:
            continue
        seen.add(current)
        ancestors.append(current)
        stack.extend(str(node["id"]) for node in summary.parent_nodes(current) if node.get("id"))
    return ancestors


def _path_key(path: str | Path) -> str:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    return str(target.resolve())


def _first_plan_step(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        return {}
    steps = plan.get("steps", ())
    if not isinstance(steps, list) or not steps:
        return {}
    first = steps[0]
    return dict(first) if isinstance(first, Mapping) else {}


def _input_label(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return "-"
    labels = []
    press = payload.get("press", ())
    release = payload.get("release", ())
    if press:
        labels.extend(f"+{item}" for item in press)
    if release:
        labels.extend(f"-{item}" for item in release)
    return " ".join(labels) if labels else "idle"


def _seconds_to_ms(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return float(value) * 1000.0


def _vertical_positions(x: int, top: int, bottom: int, count: int) -> tuple[tuple[int, int], ...]:
    if count <= 0:
        return ()
    if count == 1:
        return ((x, (top + bottom) // 2),)
    step = (bottom - top) / float(count - 1)
    return tuple((x, int(round(top + index * step))) for index in range(count))


def _font(size: int, *, bold: bool = False, monospace: bool = False):
    if not pygame.font.get_init():
        pygame.font.init()
    candidates = (
        ["Noto Sans Mono CJK JP", "Noto Sans Mono", "DejaVu Sans Mono"]
        if monospace
        else ["Noto Sans CJK JP", "Noto Sans JP", "TakaoGothic", "IPAGothic", "DejaVu Sans"]
    )
    for name in candidates:
        if pygame.font.match_font(name):
            return pygame.font.SysFont(name, size, bold=bold)
    return pygame.font.SysFont(None, size, bold=bold)


def _fit_text(text: str, font, width: int) -> str:
    if font.size(text)[0] <= width:
        return text
    ellipsis = "..."
    available = max(0, width - font.size(ellipsis)[0])
    trimmed = ""
    for character in text:
        candidate = trimmed + character
        if font.size(candidate)[0] > available:
            break
        trimmed = candidate
    return trimmed.rstrip() + ellipsis
