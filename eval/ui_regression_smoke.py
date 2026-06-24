"""Integrated UI regression smoke runner for launcher, match, and viewer flows."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT_JSON = Path("/tmp/puyo-84-ui-regression-smoke.json")
DEFAULT_VIEWER_JSON = Path("/tmp/puyo-84-model-viewer-report.json")
DEFAULT_VIEWER_MARKDOWN = Path("/tmp/puyo-84-model-viewer-report.md")
DEFAULT_REPLAY = ROOT / "tests" / "fixtures" / "realtime_replay_seed123.json"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run integrated Puyo UI smoke checks.")
    parser.add_argument("--result-json", type=Path, default=DEFAULT_RESULT_JSON)
    parser.add_argument("--viewer-json", type=Path, default=DEFAULT_VIEWER_JSON)
    parser.add_argument("--viewer-markdown", type=Path, default=DEFAULT_VIEWER_MARKDOWN)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--lineage-root", action="append", default=["docs/benchmarks"])
    parser.add_argument("--launcher-frames", type=int, default=2)
    parser.add_argument("--match-frames", type=int, default=8)
    parser.add_argument("--viewer-frames", type=int, default=1)
    parser.add_argument("--max-frame-ms", type=float, default=40.0)
    return parser.parse_args(argv)


def _ensure_dummy_sdl() -> None:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def _timed_step(name: str, func) -> dict[str, Any]:
    started = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - started
    return {
        "name": name,
        "elapsed_seconds": elapsed,
        "result": result,
    }


def _frame_rate_step_passed(step: dict[str, Any], frames: int, max_frame_ms: float) -> bool:
    if frames <= 0:
        return False
    average_frame_ms = step["elapsed_seconds"] * 1000.0 / frames
    step["average_frame_ms"] = average_frame_ms
    step["max_frame_ms"] = max_frame_ms
    step["passed"] = bool(step.get("passed")) and average_frame_ms <= max_frame_ms
    return bool(step["passed"])


def run_smoke(
    *,
    result_json: Path = DEFAULT_RESULT_JSON,
    viewer_json: Path = DEFAULT_VIEWER_JSON,
    viewer_markdown: Path = DEFAULT_VIEWER_MARKDOWN,
    replay: Path = DEFAULT_REPLAY,
    lineage_roots: tuple[str, ...] = ("docs/benchmarks",),
    launcher_frames: int = 2,
    match_frames: int = 8,
    viewer_frames: int = 1,
    max_frame_ms: float = 40.0,
) -> dict[str, Any]:
    _ensure_dummy_sdl()
    from eval.realtime_versus_ui import RealtimeVersusUiConfig, run_ui
    from src.ui.launcher import LauncherController, LauncherService, run_launcher
    from src.ui.model_viewer import build_model_viewer_data, run_model_viewer

    steps: list[dict[str, Any]] = []

    def launcher_check():
        service = LauncherService(python_executable="python3")
        controller = LauncherController(service)
        screens = ["play", "spectate", "arena", "training", "models"]
        for screen in screens:
            controller.screen = screen
            assert service.command_for(screen)
            if service.settings.editable_fields(screen):
                controller.settings_mode = True
                assert controller.current_options
                controller.settings_mode = False
        rendered = run_launcher(service=service, max_frames=launcher_frames)
        return {
            "rendered_screen": rendered["screen"],
            "rendered_job": rendered["job"],
            "screens": screens,
        }

    launcher_step = _timed_step("launcher_navigation", launcher_check)
    launcher_step["passed"] = launcher_step["result"]["rendered_screen"] == "home"
    steps.append(launcher_step)

    match_step = _timed_step(
        "realtime_match_plan_overlay",
        lambda: run_ui(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="random",
                seed=57,
                max_ticks=180,
                speed=4.0,
            ),
            max_frames=match_frames,
        ),
    )
    match_result = match_step["result"]
    match_step["passed"] = (
        match_result["ticks"] > 0
        and match_result["decisions_player_0"] > 0
        and match_result["plan_overlay_player_0"]
        and match_result["plan_overlay_player_1"]
    )
    _frame_rate_step_passed(match_step, match_frames, max_frame_ms)
    steps.append(match_step)

    viewer_data = build_model_viewer_data(replay_path=replay, lineage_roots=lineage_roots)
    viewer_step = _timed_step(
        "model_viewer_replay_lineage",
        lambda: run_model_viewer(
            viewer_data,
            max_frames=viewer_frames,
            report_json=viewer_json,
            report_markdown=viewer_markdown,
        ),
    )
    viewer_result = viewer_step["result"]
    viewer_step["passed"] = (
        viewer_result["schema_version"] == "puyo.model_viewer_report.v1"
        and viewer_json.exists()
        and viewer_markdown.exists()
    )
    steps.append(viewer_step)

    report = {
        "schema_version": "puyo.ui_regression_smoke.v1",
        "passed": all(step.get("passed") for step in steps),
        "steps": steps,
        "artifacts": {
            "result_json": str(result_json),
            "viewer_json": str(viewer_json),
            "viewer_markdown": str(viewer_markdown),
        },
    }
    result_json.parent.mkdir(parents=True, exist_ok=True)
    result_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> None:
    args = parse_args(argv)
    report = run_smoke(
        result_json=args.result_json,
        viewer_json=args.viewer_json,
        viewer_markdown=args.viewer_markdown,
        replay=args.replay,
        lineage_roots=tuple(args.lineage_root),
        launcher_frames=args.launcher_frames,
        match_frames=args.match_frames,
        viewer_frames=args.viewer_frames,
        max_frame_ms=args.max_frame_ms,
    )
    print(json.dumps(report, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
