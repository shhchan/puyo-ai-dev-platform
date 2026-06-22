"""Replay diagnostics and model lineage viewer data/model helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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


@dataclass(frozen=True)
class ReplayTimelineEntry:
    tick: int
    snapshot_hash: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    policy_diagnostics: dict[str, Any] = field(default_factory=dict)

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


@dataclass(frozen=True)
class ModelViewerData:
    replay_path: str | None
    replay_format: str
    seed: int | None
    expected_final_hash: str
    timeline: tuple[ReplayTimelineEntry, ...]
    lineage: LineageSummary

    def to_report(self, *, selected_tick: int | None = None, bookmarks: tuple[int, ...] = ()) -> dict[str, Any]:
        return {
            "schema_version": "puyo.model_viewer_report.v1",
            "replay": {
                "path": self.replay_path,
                "format": self.replay_format,
                "seed": self.seed,
                "expected_final_hash": self.expected_final_hash,
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
        }


class ModelViewerController:
    def __init__(self, data: ModelViewerData):
        self.data = data
        self.index = 0
        self.paused = True
        self.playback_stride = 1
        self.bookmarks: set[int] = set()
        self.message = "ready"

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
        if not self.paused:
            self.seek(self.playback_stride)

    def report(self) -> dict[str, Any]:
        entry = self.selected_entry
        report = self.data.to_report(
            selected_tick=None if entry is None else entry.tick,
            bookmarks=tuple(sorted(self.bookmarks)),
        )
        report["replay"]["playback_stride"] = self.playback_stride
        return report


def load_replay_timeline(path: str | Path | None) -> tuple[str | None, str, int | None, str, tuple[ReplayTimelineEntry, ...]]:
    if path is None:
        return None, "none", None, "", ()
    replay_path = Path(path)
    payload = json.loads(replay_path.read_text(encoding="utf-8"))
    replay_format = str(payload.get("format", "puyo-realtime-fixture-v1"))
    seed = payload.get("seed")
    expected_final_hash = str(payload.get("expected_final_hash", ""))
    if replay_format == "puyo-realtime-match-v1" and isinstance(payload.get("ticks"), list):
        entries = tuple(
            ReplayTimelineEntry(
                tick=int(item.get("tick", index)),
                snapshot_hash=str(item.get("snapshot_hash", "")),
                inputs=dict(item.get("inputs", {})),
                policy_diagnostics=dict(item.get("policy_diagnostics", {})),
            )
            for index, item in enumerate(payload["ticks"])
            if isinstance(item, dict)
        )
        return str(replay_path), replay_format, int(seed) if seed is not None else None, expected_final_hash, entries
    capture_every = int(payload.get("capture_every", 1))
    hashes = payload.get("expected_hashes", [])
    entries = tuple(
        ReplayTimelineEntry(tick=(index + 1) * capture_every, snapshot_hash=str(snapshot_hash))
        for index, snapshot_hash in enumerate(hashes)
    )
    if not entries and "ticks" in payload:
        entries = tuple(ReplayTimelineEntry(tick=int(payload["ticks"]), snapshot_hash=expected_final_hash))
    return str(replay_path), replay_format, int(seed) if seed is not None else None, expected_final_hash, entries


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
) -> ModelViewerData:
    replay_path_str, replay_format, seed, expected_final_hash, timeline = load_replay_timeline(replay_path)
    return ModelViewerData(
        replay_path=replay_path_str,
        replay_format=replay_format,
        seed=seed,
        expected_final_hash=expected_final_hash,
        timeline=timeline,
        lineage=build_lineage_summary(lineage_roots),
    )


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
        lines = [
            "# Puyo Model Viewer Report",
            "",
            "## Replay",
            "",
            f"- path: `{replay.get('path') or '-'}`",
            f"- format: `{replay.get('format') or '-'}`",
            f"- seed: `{replay.get('seed')}`",
            f"- ticks: `{replay.get('ticks')}`",
            f"- selected_tick: `{replay.get('selected_tick')}`",
            f"- bookmarks: `{replay.get('bookmarks')}`",
            "",
            "## Lineage",
            "",
            f"- runs: `{lineage.get('runs')}`",
            f"- checkpoints: `{lineage.get('checkpoints')}`",
            f"- nodes: `{lineage.get('nodes')}`",
            f"- edges: `{lineage.get('edges')}`",
            f"- issues: `{len(lineage.get('issues', []))}`",
        ]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
        tick = "-" if entry is None else str(entry.tick)
        status = (
            f"{'PAUSED' if controller.paused else 'PLAYING'}  "
            f"speed {controller.playback_stride}x  tick {tick}  {controller.message}"
        )
        self._draw_text(status, self.font, ACCENT, (34, 64))

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
            f"inputs: {', '.join(entry.inputs.keys()) or '-'}",
            f"plans: {', '.join(entry.plan_ids) or '-'}",
        ]
        y = rect.y + 58
        for line in details:
            self._draw_text(line, self.small_font, TEXT, (rect.x + 18, y), width=rect.width - 36)
            y += 24
        y += 12
        for offset, item in enumerate(controller.data.timeline[max(0, controller.index - 5) : controller.index + 8]):
            selected = item.tick == entry.tick
            row = pygame.Rect(rect.x + 18, y + offset * 28, rect.width - 36, 24)
            pygame.draw.rect(self.screen, PANEL_ACTIVE if selected else BACKGROUND, row, border_radius=4)
            label = f"{item.tick:>5}  {item.snapshot_hash[:18] or '-'}"
            self._draw_text(label, self.small_font, WARNING if selected else MUTED, (row.x + 8, row.y + 4))

    def _draw_lineage_panel(self, controller: ModelViewerController) -> None:
        rect = pygame.Rect(568, 104, 500, 548)
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=6)
        pygame.draw.rect(self.screen, WARNING, rect, 2)
        self._draw_text("Lineage", self.heading_font, TEXT, (rect.x + 18, rect.y + 16))
        summary = controller.data.lineage
        y = rect.y + 58
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
        y += 12
        self._draw_text("Recent checkpoints", self.font, WARNING, (rect.x + 18, y))
        y += 30
        for node in summary.checkpoints[:12]:
            label = f"{node.get('label', '-')}  {node.get('path', '-')}"
            self._draw_text(label, self.small_font, MUTED, (rect.x + 18, y), width=rect.width - 36)
            y += 24

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
                elif event.key == pygame.K_SPACE:
                    controller.toggle_pause()
                elif event.key in (pygame.K_EQUALS, pygame.K_PLUS):
                    controller.change_speed(1)
                elif event.key in (pygame.K_MINUS, pygame.K_UNDERSCORE):
                    controller.change_speed(-1)
                elif event.key == pygame.K_b:
                    controller.toggle_bookmark()
        controller.advance_playback()
        renderer.draw(controller)
        frames += 1
    report = controller.report()
    write_viewer_report(report, json_path=report_json, markdown_path=report_markdown)
    pygame.quit()
    return report


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
