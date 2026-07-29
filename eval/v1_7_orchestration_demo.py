"""PUYO-181 reproducible GUI demo launcher and artifact verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
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
    ROOT / "runs" / "v1_7_manager" / DEMO_RUN_ID / "checkpoints" / "bootstrap.pt"
)
DEMO_TRAINING_MANIFEST = DEMO_CHECKPOINT.parents[1] / "artifact_manifest.json"
DEMO_OUTPUT_ROOT = ROOT / "runs" / "puyo-181-demo"
DEMO_PREVIEW_TOP_K = 1
DEMO_RECORD_SECONDS = 30
DEMO_RECORD_FPS = 5
DEMO_BUDGET_CAP = PlannerBudgetCap(
    profile="puyo-181-gui-demo",
    max_search_depth=1,
    max_search_width=4,
    max_candidate_count=2,
    max_latency_budget_ms=250.0,
)
DEMO_TACTIC_CHOICES = (
    "build_main",
    "prepare_response",
    "counter_or_return",
    "pressure",
    "lethal_attack",
    "all_clear",
    "fire_main",
    "survive",
)
DEMO_OPPONENT_CHOICES = (
    "v1_7_analyzer_manager",
    "manager_rule",
    "worker_large",
    "worker_quick",
    "worker_fire",
    "greedy",
    "random",
    "first",
)
DEMO_DISCLAIMERS = (
    "Demo-only behavior-cloning checkpoint; this is not PUYO-130 mixed PPO.",
    "The demo does not use a learned CandidateRanker.",
    "The runtime planner cap is for presentation responsiveness only.",
    "The result is not PUYO-176 canonical GO or GO_WITH_LATENCY_WAIVER evidence.",
    "The result is not promotion, release, or formal strength evidence.",
)


@dataclass(frozen=True)
class DemoRuntimeTuning:
    """Auditable runtime-only adjustments for exploratory presentation runs."""

    preview_top_k: int = DEMO_PREVIEW_TOP_K
    planner_budget_cap: PlannerBudgetCap | None = DEMO_BUDGET_CAP
    target_chain: int | None = None
    forced_tactic_id: str | None = None
    opponent: str | None = None

    def parameter_overrides(
        self,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        if self.target_chain is None:
            return {}
        return {
            "build_main": {
                "objective": {
                    "target_chain": int(self.target_chain),
                }
            }
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "preview_top_k": int(self.preview_top_k),
            "planner_budget_cap": (
                None
                if self.planner_budget_cap is None
                else self.planner_budget_cap.to_dict()
            ),
            "target_chain": self.target_chain,
            "forced_tactic_id": self.forced_tactic_id,
            "opponent": self.opponent,
            "parameter_overrides": self.parameter_overrides(),
        }


DEFAULT_DEMO_TUNING = DemoRuntimeTuning()


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
    tuning: DemoRuntimeTuning = DEFAULT_DEMO_TUNING,
) -> Policy:
    """Build normal policies while bounding only the v1.7 demo orchestration."""

    parameter_overrides = tuning.parameter_overrides()
    if policy_type == "v1_7_bootstrap_manager":
        if checkpoint_path is None:
            raise ValueError("demo bootstrap policy requires a checkpoint")
        return V17StrategyManagerPolicy.from_checkpoint(
            checkpoint_path,
            preview_top_k=tuning.preview_top_k,
            device=device,
            deterministic=deterministic,
            forced_tactic_id=tuning.forced_tactic_id or forced_tactic_id,
            parameter_overrides=parameter_overrides,
            planner_budget_cap=tuning.planner_budget_cap,
        )
    if policy_type == "v1_7_analyzer_manager":
        return V17AnalyzerManagerPolicy(
            parameter_overrides=parameter_overrides,
            planner_budget_cap=tuning.planner_budget_cap,
        )
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
    tuning: DemoRuntimeTuning = DEFAULT_DEMO_TUNING,
):
    from eval.realtime_versus_ui import RealtimeVersusUiConfig

    return RealtimeVersusUiConfig(
        policy_a=preset.policy_a,
        policy_b=tuning.opponent or preset.policy_b,
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
        raise RuntimeError(
            "prepared demo checkpoint did not validate: " + "; ".join(status["errors"])
        )
    status["reproduce_command"] = " ".join(command)
    status_path = DEMO_OUTPUT_ROOT / "checkpoint_status.json"
    _write_json(status_path, status)
    status["status_path"] = _display_path(status_path)
    return status


def build_runtime_tuning(args: argparse.Namespace) -> DemoRuntimeTuning:
    planner_budget_cap = None
    if args.planner_cap:
        planner_budget_cap = PlannerBudgetCap(
            profile="puyo-181-gui-demo",
            max_search_depth=args.planner_depth_cap,
            max_search_width=args.planner_width_cap,
            max_candidate_count=args.planner_candidate_cap,
            max_latency_budget_ms=args.planner_latency_cap_ms,
        )
    return DemoRuntimeTuning(
        preview_top_k=args.preview_top_k,
        planner_budget_cap=planner_budget_cap,
        target_chain=args.target_chain,
        forced_tactic_id=args.force_tactic,
        opponent=args.opponent,
    )


def default_output_dir(
    *,
    preset: DemoPreset,
    seed: int,
    max_ticks: int,
    speed: float,
    tuning: DemoRuntimeTuning,
    qa: bool,
    record_seconds: int = DEMO_RECORD_SECONDS,
    record_fps: int = DEMO_RECORD_FPS,
    record_gif: bool = True,
) -> Path:
    base = DEMO_OUTPUT_ROOT / f"{preset.name}-seed{seed}" / ("qa" if qa else "live")
    if (
        seed == preset.seed
        and max_ticks == preset.max_ticks
        and speed == preset.speed
        and tuning == DEFAULT_DEMO_TUNING
        and record_seconds == DEMO_RECORD_SECONDS
        and record_fps == DEMO_RECORD_FPS
        and record_gif
    ):
        return base
    payload = {
        "preset": preset.name,
        "seed": seed,
        "max_ticks": max_ticks,
        "speed": speed,
        "tuning": tuning.to_dict(),
        "recording": {
            "enabled": record_gif,
            "seconds": record_seconds,
            "fps": record_fps,
        },
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return base / f"custom-{digest}"


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
    tuning = build_runtime_tuning(args)
    output_dir = (
        default_output_dir(
            preset=preset,
            seed=seed,
            max_ticks=max_ticks,
            speed=speed,
            tuning=tuning,
            qa=qa,
            record_seconds=args.record_seconds,
            record_fps=args.record_fps,
            record_gif=args.record_gif,
        )
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
        tuning=tuning,
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
        policy_factory=partial(make_demo_policy, tuning=tuning),
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
        tuning=tuning,
        record_seconds=args.record_seconds,
        record_fps=args.record_fps,
        record_gif=args.record_gif,
    )
    manifest_path = output_dir / "demo_manifest.json"
    _write_json(manifest_path, manifest)
    summary = {
        "preset": preset.name,
        "result": result["result"],
        "quality_gate": result["quality_gate"],
        "replay": replay_verification,
        "recording": _artifact_record(gif_path) if gif_path.is_file() else None,
        "tuning": tuning.to_dict(),
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
    tuning: DemoRuntimeTuning = DEFAULT_DEMO_TUNING,
    record_seconds: int = DEMO_RECORD_SECONDS,
    record_fps: int = DEMO_RECORD_FPS,
    record_gif: bool = True,
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
            "tuning": tuning.to_dict(),
            "planner_budget_cap": (
                None
                if tuning.planner_budget_cap is None
                else tuning.planner_budget_cap.to_dict()
            ),
            "preview_top_k": tuning.preview_top_k,
            "recording": {
                "enabled": bool(record_gif),
                "seconds": int(record_seconds),
                "fps": int(record_fps),
            },
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


def _bounded_int(value: str, *, minimum: int, maximum: int, label: str) -> int:
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"{label} must be in [{minimum}, {maximum}]")
    return parsed


def _positive_int(value: str) -> int:
    return _bounded_int(
        value,
        minimum=1,
        maximum=sys.maxsize,
        label="value",
    )


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--preset", choices=tuple(DEMO_PRESETS), default="primary")
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--max-ticks",
        type=_positive_int,
        help="Maximum match ticks; game over can still end the match earlier.",
    )
    parser.add_argument("--speed", type=float, choices=(0.25, 0.5, 1.0, 2.0, 4.0))
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--record-seconds",
        type=_positive_int,
        default=DEMO_RECORD_SECONDS,
    )
    parser.add_argument(
        "--record-fps",
        type=_positive_int,
        default=DEMO_RECORD_FPS,
    )
    parser.add_argument(
        "--opponent",
        choices=DEMO_OPPONENT_CHOICES,
        help="Override player 2 while retaining the preset's player 1.",
    )
    parser.add_argument(
        "--target-chain",
        type=lambda value: _bounded_int(
            value,
            minimum=1,
            maximum=19,
            label="target chain",
        ),
        help="Override build_main objective.target_chain for v1.7 managers.",
    )
    parser.add_argument(
        "--force-tactic",
        choices=DEMO_TACTIC_CHOICES,
        help=(
            "Strictly force one learned-manager tactic for diagnostics; "
            "the run fails if it becomes ineligible."
        ),
    )
    parser.add_argument(
        "--preview-top-k",
        type=lambda value: _bounded_int(
            value,
            minimum=1,
            maximum=len(DEMO_TACTIC_CHOICES),
            label="preview top-k",
        ),
        default=DEMO_PREVIEW_TOP_K,
        help="Number of learned-manager tactic proposals to preview.",
    )
    parser.add_argument(
        "--planner-depth-cap",
        type=_positive_int,
        default=DEMO_BUDGET_CAP.max_search_depth,
        help="Upper bound for effective planner search depth.",
    )
    parser.add_argument(
        "--planner-width-cap",
        type=_positive_int,
        default=DEMO_BUDGET_CAP.max_search_width,
        help="Upper bound for effective planner search width.",
    )
    parser.add_argument(
        "--planner-candidate-cap",
        type=_positive_int,
        default=DEMO_BUDGET_CAP.max_candidate_count,
        help="Upper bound for effective planner candidate count.",
    )
    parser.add_argument(
        "--planner-latency-cap-ms",
        type=_positive_float,
        default=DEMO_BUDGET_CAP.max_latency_budget_ms,
        help="Upper bound for the planner's requested latency budget.",
    )
    parser.add_argument(
        "--no-planner-cap",
        dest="planner_cap",
        action="store_false",
        help="Disable the presentation cap; decisions can take tens of seconds.",
    )
    parser.set_defaults(planner_cap=True)
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
    prepare = subparsers.add_parser(
        "prepare", help="Generate and validate the demo checkpoint."
    )
    prepare.add_argument("--force", action="store_true")
    live = subparsers.add_parser("live", help="Run the interactive GUI demo.")
    _add_run_arguments(live)
    qa = subparsers.add_parser(
        "qa", help="Run dummy-SDL playability QA and record a GIF."
    )
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
        print(
            json.dumps(prepare_checkpoint(force=args.force), indent=2, sort_keys=True)
        )
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
            DEMO_OUTPUT_ROOT / f"{preset.name}-seed{seed}" / "qa" / "replay.json"
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
