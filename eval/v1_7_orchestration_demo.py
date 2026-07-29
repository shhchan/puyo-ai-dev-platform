"""PUYO-181 reproducible GUI demo launcher and artifact verifier."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import torch

from agents.v1_7_analyzer_manager import V17AnalyzerManagerPolicy
from agents.v1_7_planner import PlannerBudgetCap
from agents.v1_7_strategy_manager import (
    V17StrategyManagerPolicy,
    validate_v1_7_strategy_manager_checkpoint_payload,
)
from eval.realtime_arena import replay_realtime_match
from selfplay.policies import Policy, make_policy
from train.artifacts import (
    file_sha256,
    git_commit,
    utc_timestamp,
    validate_artifact_manifest,
)

DEMO_SCHEMA_VERSION = "puyo.puyo_181_orchestration_demo.v1"
DEMO_RUN_ID = "puyo-181-friday-demo-seed126"
DEMO_CHECKPOINT = (
    ROOT
    / "runs"
    / "v1_7_manager"
    / DEMO_RUN_ID
    / "checkpoints"
    / "bootstrap.pt"
)
DEMO_TRAINING_MANIFEST = DEMO_CHECKPOINT.parents[1] / "artifact_manifest.json"
DEMO_OUTPUT_ROOT = ROOT / "runs" / "puyo-181-demo"
DEMO_PREVIEW_TOP_K = 1
DEMO_BUDGET_CAP = PlannerBudgetCap(
    profile="puyo-181-gui-demo",
    max_search_depth=1,
    max_search_width=4,
    max_candidate_count=2,
    max_latency_budget_ms=250.0,
)
DEMO_DISCLAIMERS = (
    "Demo-only behavior-cloning checkpoint; this is not PUYO-130 mixed PPO.",
    "The demo does not use a learned CandidateRanker.",
    "The runtime planner cap is for presentation responsiveness only.",
    "The result is not PUYO-176 canonical GO or GO_WITH_LATENCY_WAIVER evidence.",
    "The result is not promotion, release, or formal strength evidence.",
)


@dataclass(frozen=True)
class DemoPreset:
    name: str
    policy_a: str
    policy_b: str
    seed: int
    max_ticks: int
    speed: float
    description: str
    seed_selection_reason: str


DEMO_PRESETS = {
    "primary": DemoPreset(
        name="primary",
        policy_a="v1_7_bootstrap_manager",
        policy_b="v1_7_analyzer_manager",
        seed=126,
        max_ticks=7_200,
        speed=1.0,
        description="Demo-only learned bootstrap versus the analyzer-driven baseline.",
        seed_selection_reason=(
            "Training seed retained as the primary deterministic presentation seed; "
            "dummy-SDL playability and replay verification are recorded before use."
        ),
    ),
    "fallback": DemoPreset(
        name="fallback",
        policy_a="v1_7_analyzer_manager",
        policy_b="manager_rule",
        seed=123,
        max_ticks=7_200,
        speed=1.0,
        description="Checkpoint-free rule-based orchestration fallback.",
        seed_selection_reason=(
            "Existing v1.7 GUI baseline seed retained as the checkpoint-free fallback."
        ),
    ),
}


class GifRecorder:
    """Capture a bounded, presentation-sized animated GIF from rendered frames."""

    def __init__(
        self,
        path: str | Path,
        *,
        capture_fps: int = 5,
        duration_seconds: int = 30,
        render_fps: int = 60,
        size: tuple[int, int] = (560, 390),
    ):
        if min(capture_fps, duration_seconds, render_fps, *size) <= 0:
            raise ValueError("GIF recording settings must be positive")
        self.path = Path(path)
        self.capture_fps = int(capture_fps)
        self.duration_seconds = int(duration_seconds)
        self.frame_interval = max(1, round(render_fps / capture_fps))
        self.max_frames = self.capture_fps * self.duration_seconds
        self.size = size
        self.frames: list[Any] = []

    def capture(self, screen: Any, frame_index: int) -> None:
        if len(self.frames) >= self.max_frames or frame_index % self.frame_interval:
            return
        try:
            import pygame
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ImportError("GIF recording requires Pillow and pygame") from exc
        raw = pygame.image.tobytes(screen, "RGB")
        image = Image.frombytes("RGB", screen.get_size(), raw)
        image = image.resize(self.size, Image.Resampling.LANCZOS)
        self.frames.append(image.quantize(colors=128))

    def save(self) -> Path | None:
        if not self.frames:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.frames[0].save(
            self.path,
            save_all=True,
            append_images=self.frames[1:],
            duration=round(1000 / self.capture_fps),
            loop=0,
            optimize=False,
            disposal=2,
        )
        return self.path


def make_demo_policy(
    policy_type: str,
    *,
    seed: int = 1,
    checkpoint_path: str | None = None,
    device: str = "cpu",
    deterministic: bool = True,
    beam_depth: int = 10,
    beam_width: int = 48,
    beam_scenarios: int = 1,
    beam_minimum_chain: int = 6,
    forced_tactic_id: str | None = None,
) -> Policy:
    """Build normal policies while bounding only the v1.7 demo orchestration."""

    if policy_type == "v1_7_bootstrap_manager":
        if checkpoint_path is None:
            raise ValueError("demo bootstrap policy requires a checkpoint")
        return V17StrategyManagerPolicy.from_checkpoint(
            checkpoint_path,
            preview_top_k=DEMO_PREVIEW_TOP_K,
            device=device,
            deterministic=deterministic,
            forced_tactic_id=forced_tactic_id,
            planner_budget_cap=DEMO_BUDGET_CAP,
        )
    if policy_type == "v1_7_analyzer_manager":
        return V17AnalyzerManagerPolicy(planner_budget_cap=DEMO_BUDGET_CAP)
    return make_policy(
        policy_type,
        seed=seed,
        checkpoint_path=checkpoint_path,
        device=device,
        deterministic=deterministic,
        beam_depth=beam_depth,
        beam_width=beam_width,
        beam_scenarios=beam_scenarios,
        beam_minimum_chain=beam_minimum_chain,
        forced_tactic_id=forced_tactic_id,
    )


def build_demo_config(
    preset: DemoPreset,
    *,
    seed: int,
    max_ticks: int,
    speed: float,
    result_path: Path,
    replay_path: Path,
    qa_profile: str | None,
):
    from eval.realtime_versus_ui import RealtimeVersusUiConfig

    return RealtimeVersusUiConfig(
        policy_a=preset.policy_a,
        policy_b=preset.policy_b,
        checkpoint_a=(
            str(DEMO_CHECKPOINT)
            if preset.policy_a == "v1_7_bootstrap_manager"
            else None
        ),
        checkpoint_b=(
            str(DEMO_CHECKPOINT)
            if preset.policy_b == "v1_7_bootstrap_manager"
            else None
        ),
        seed=seed,
        max_ticks=max_ticks,
        speed=speed,
        latency_mode="measured",
        result_json=str(result_path),
        replay_path=str(replay_path),
        qa_notes=(
            f"PUYO-181 {preset.name}: {preset.description} "
            "Demo-only; not canonical or promotion evidence."
        ),
        qa_profile=qa_profile,
        exit_after_finish_frames=90,
        plan_overlay=True,
    )


def verify_replay(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    replay = _read_json(target)
    verified_hash = replay_realtime_match(replay)
    return {
        "path": _display_path(target),
        "ticks": len(replay.get("ticks", ())),
        "expected_final_hash": replay.get("expected_final_hash"),
        "verified_final_hash": verified_hash,
        "valid": verified_hash == replay.get("expected_final_hash"),
    }


def checkpoint_status() -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": _display_path(DEMO_CHECKPOINT),
        "exists": DEMO_CHECKPOINT.is_file(),
        "valid": False,
        "errors": [],
    }
    if not DEMO_CHECKPOINT.is_file():
        result["errors"].append("checkpoint is missing; run the prepare command")
        return result
    payload = torch.load(DEMO_CHECKPOINT, map_location="cpu", weights_only=False)
    if not isinstance(payload, Mapping):
        result["errors"].append("checkpoint payload must be a mapping")
        return result
    result["errors"].extend(validate_v1_7_strategy_manager_checkpoint_payload(payload))
    result.update(
        {
            "sha256": file_sha256(DEMO_CHECKPOINT),
            "run_id": payload.get("run_id"),
            "checkpoint_git_commit": (
                payload.get("checkpoint_schema", {}).get("git_commit")
                if isinstance(payload.get("checkpoint_schema"), Mapping)
                else None
            ),
            "dataset_id": (
                payload.get("dataset", {}).get("dataset_id")
                if isinstance(payload.get("dataset"), Mapping)
                else None
            ),
        }
    )
    if DEMO_TRAINING_MANIFEST.is_file():
        manifest = _read_json(DEMO_TRAINING_MANIFEST)
        result["training_manifest"] = {
            "path": _display_path(DEMO_TRAINING_MANIFEST),
            "sha256": file_sha256(DEMO_TRAINING_MANIFEST),
            "errors": validate_artifact_manifest(
                manifest,
                run_dir=DEMO_TRAINING_MANIFEST.parent,
            ),
        }
        result["errors"].extend(result["training_manifest"]["errors"])
    else:
        result["errors"].append("training artifact manifest is missing")
    result["valid"] = not result["errors"]
    return result


def prepare_checkpoint(*, force: bool) -> dict[str, Any]:
    status = checkpoint_status()
    if status["valid"] and not force:
        return status
    command = [
        sys.executable,
        "-m",
        "train.train_v1_7_manager",
        "--config",
        "train/config/v1_7_manager_bootstrap.yaml",
        "--set",
        f"run_id={DEMO_RUN_ID}",
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    status = checkpoint_status()
    if not status["valid"]:
        raise RuntimeError("prepared demo checkpoint did not validate: " + "; ".join(status["errors"]))
    status["reproduce_command"] = " ".join(command)
    status_path = DEMO_OUTPUT_ROOT / "checkpoint_status.json"
    _write_json(status_path, status)
    status["status_path"] = _display_path(status_path)
    return status


def run_demo(args: argparse.Namespace, *, qa: bool) -> int:
    if args.preset == "primary":
        status = checkpoint_status()
        if not status["valid"]:
            raise RuntimeError(
                "primary demo checkpoint is not ready; run "
                f"'{sys.executable} -m eval.v1_7_orchestration_demo prepare'"
            )
    if qa:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    from eval.realtime_versus_ui import run_ui

    preset = DEMO_PRESETS[args.preset]
    seed = preset.seed if args.seed is None else args.seed
    max_ticks = preset.max_ticks if args.max_ticks is None else args.max_ticks
    speed = preset.speed if args.speed is None else args.speed
    output_dir = (
        DEMO_OUTPUT_ROOT
        / f"{preset.name}-seed{seed}"
        / ("qa" if qa else "live")
        if args.output_dir is None
        else _resolve_path(args.output_dir)
    )
    result_path = output_dir / "gui_qa.json"
    replay_path = output_dir / "replay.json"
    gif_path = output_dir / "demo.gif"
    output_dir.mkdir(parents=True, exist_ok=True)

    config = build_demo_config(
        preset,
        seed=seed,
        max_ticks=max_ticks,
        speed=speed,
        result_path=result_path,
        replay_path=replay_path,
        qa_profile="playability" if qa else None,
    )
    recorder = (
        GifRecorder(
            gif_path,
            capture_fps=args.record_fps,
            duration_seconds=args.record_seconds,
        )
        if args.record_gif
        else None
    )
    result = run_ui(
        config,
        policy_factory=make_demo_policy,
        frame_callback=None if recorder is None else recorder.capture,
    )
    if recorder is not None:
        recorder.save()
    _write_json(result_path, result)
    replay_verification = verify_replay(replay_path)
    manifest = build_demo_manifest(
        preset=preset,
        config=config,
        result=result,
        result_path=result_path,
        replay_path=replay_path,
        gif_path=gif_path if gif_path.is_file() else None,
        replay_verification=replay_verification,
        qa=qa,
    )
    manifest_path = output_dir / "demo_manifest.json"
    _write_json(manifest_path, manifest)
    summary = {
        "preset": preset.name,
        "result": result["result"],
        "quality_gate": result["quality_gate"],
        "replay": replay_verification,
        "recording": _artifact_record(gif_path) if gif_path.is_file() else None,
        "manifest": _display_path(manifest_path),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if qa and not result["quality_gate"]["passed"]:
        return 2
    return 0


def build_demo_manifest(
    *,
    preset: DemoPreset,
    config: Any,
    result: Mapping[str, Any],
    result_path: Path,
    replay_path: Path,
    gif_path: Path | None,
    replay_verification: Mapping[str, Any],
    qa: bool,
) -> dict[str, Any]:
    return {
        "schema_version": DEMO_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "issue": "PUYO-181",
        "preset": {
            "name": preset.name,
            "description": preset.description,
            "seed_selection_reason": preset.seed_selection_reason,
        },
        "runtime": {
            "git_commit": git_commit(ROOT),
            "git_dirty": _repository_dirty(),
            "policy_a": config.policy_a,
            "policy_b": config.policy_b,
            "seed": config.seed,
            "max_ticks": config.max_ticks,
            "speed": config.speed,
            "latency_mode": config.latency_mode,
            "qa_profile": config.qa_profile,
            "planner_budget_cap": DEMO_BUDGET_CAP.to_dict(),
            "preview_top_k": DEMO_PREVIEW_TOP_K,
        },
        "checkpoint": checkpoint_status(),
        "result": dict(result["result"]),
        "quality_gate": result["quality_gate"],
        "orchestration": {
            agent: {
                "model_metadata": diagnostics.get("model_metadata", {}),
                "tactic_registry": diagnostics.get("tactic_registry", {}),
                "selected_tactic": diagnostics.get("selected_tactic", {}),
                "runtime_constraints": diagnostics.get("runtime_constraints", {}),
            }
            for agent, diagnostics in result["diagnostics"]["policy"].items()
            if isinstance(diagnostics, Mapping)
        },
        "replay_verification": dict(replay_verification),
        "artifacts": {
            "result": _artifact_record(result_path),
            "replay": _artifact_record(replay_path),
            "recording": None if gif_path is None else _artifact_record(gif_path),
        },
        "qa_environment": "dummy_sdl" if qa else "interactive_display",
        "human_display_verification": {
            "required": True,
            "completed": False,
            "checklist": (
                "active pair movement/rotation/drop",
                "placements and chain/ojama labels",
                "tactic/reason/objective/plan/worker HUD",
                "winner banner or sufficiently complete continuous play",
            ),
        },
        "disclaimers": list(DEMO_DISCLAIMERS),
    }


def _artifact_record(path: Path) -> dict[str, Any]:
    return {
        "path": _display_path(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": file_sha256(path) if path.is_file() else None,
    }


def _repository_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _resolve_path(path: str | Path) -> Path:
    target = Path(path)
    return target if target.is_absolute() else ROOT / target


def _display_path(path: str | Path) -> str:
    target = Path(path)
    try:
        return str(target.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(target)


def _read_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--preset", choices=tuple(DEMO_PRESETS), default="primary")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument("--speed", type=float, choices=(0.25, 0.5, 1.0, 2.0, 4.0))
    parser.add_argument("--output-dir")
    parser.add_argument("--record-seconds", type=int, default=30)
    parser.add_argument("--record-fps", type=int, default=5)
    parser.add_argument(
        "--record-gif",
        dest="record_gif",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-record-gif",
        dest="record_gif",
        action="store_false",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare, run, and verify the PUYO-181 GUI demonstration.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Generate and validate the demo checkpoint.")
    prepare.add_argument("--force", action="store_true")
    live = subparsers.add_parser("live", help="Run the interactive GUI demo.")
    _add_run_arguments(live)
    qa = subparsers.add_parser("qa", help="Run dummy-SDL playability QA and record a GIF.")
    _add_run_arguments(qa)
    verify = subparsers.add_parser("verify", help="Verify a diagnostic replay hash.")
    verify.add_argument("--replay")
    verify.add_argument("--preset", choices=tuple(DEMO_PRESETS), default="primary")
    verify.add_argument("--seed", type=int)
    subparsers.add_parser("status", help="Validate the current demo checkpoint.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "prepare":
        print(json.dumps(prepare_checkpoint(force=args.force), indent=2, sort_keys=True))
        return
    if args.command == "status":
        status = checkpoint_status()
        print(json.dumps(status, indent=2, sort_keys=True))
        if not status["valid"]:
            raise SystemExit(2)
        return
    if args.command == "verify":
        preset = DEMO_PRESETS[args.preset]
        seed = preset.seed if args.seed is None else args.seed
        replay_path = (
            DEMO_OUTPUT_ROOT
            / f"{preset.name}-seed{seed}"
            / "qa"
            / "replay.json"
            if args.replay is None
            else _resolve_path(args.replay)
        )
        verification = verify_replay(replay_path)
        print(json.dumps(verification, indent=2, sort_keys=True))
        if not verification["valid"]:
            raise SystemExit(2)
        return
    raise SystemExit(run_demo(args, qa=args.command == "qa"))


if __name__ == "__main__":
    main()
