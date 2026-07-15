"""PUYO-166 BuildPotential-v2 build_main budget sweep and verification."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import multiprocessing as mp
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import BUILD_POTENTIAL_SCHEMA_VERSION
from agents.strategy_workers import (
    StrategyOrchestrator,
    default_worker_profiles,
    profile_id_by_name,
)
from agents.v1_7_planner import PlannerRequest
from agents.v1_7_strategy_manager import V17StrategyManagerPolicy
from agents.v1_7_tactics import load_tactic_registry
from eval.v1_7_benchmark import _run_safe_game, aggregate_safe_suite
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_build_main_benchmark.v2"
SELECTION_SCHEMA_VERSION = "puyo.v1_7_build_main_selection.v2"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-build-main-v2"
DEFAULT_CHECKPOINT = (
    "runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/"
    "checkpoints/bootstrap.pt"
)
DEFAULT_DEPTHS = (6, 8, 10)
DEFAULT_WIDTHS = (24, 32, 48)
DEFAULT_PROBE_WIDTHS = (8, 16)
FALLBACK_BUDGET = {"depth": 3, "width": 24, "probe_width": 8}


@dataclass(frozen=True, order=True)
class BuildMainConfiguration:
    depth: int
    width: int
    probe_width: int

    @property
    def config_id(self) -> str:
        return f"d{self.depth}-w{self.width}-p{self.probe_width}"

    def to_dict(self) -> dict[str, int | str]:
        return {
            "config_id": self.config_id,
            "depth": int(self.depth),
            "width": int(self.width),
            "probe_width": int(self.probe_width),
        }


class ForcedBuildMainPolicy:
    """Execute build_main defaults with only the benchmark budget overridden."""

    def __init__(self, configuration: BuildMainConfiguration):
        self.configuration = configuration
        self.registry = load_tactic_registry()
        self.tactic = self.registry.tactic("build_main")
        self.profiles = default_worker_profiles()
        self.profile_id = profile_id_by_name(self.profiles, "build_large")
        self.orchestrator = StrategyOrchestrator(self.profiles)
        self.last_proposal = None
        self.last_plan = None

    def reset(self) -> None:
        self.orchestrator = StrategyOrchestrator(self.profiles)
        self.last_proposal = None
        self.last_plan = None

    @property
    def current_profile_name(self) -> str:
        return "build_main"

    def select_action(
        self,
        observation: dict[str, Any],
        info: dict[str, Any],
    ) -> int:
        request = self._request(info)
        self.last_proposal = self.orchestrator.propose(
            self.profile_id,
            observation,
            info,
            planner_request=request,
        )
        self.last_plan = self.orchestrator.last_plan
        return int(self.last_proposal.action)

    def _request(self, info: Mapping[str, Any]) -> PlannerRequest:
        parameters = self.tactic.resolve_parameters(
            {
                "planner": {
                    "beam_depth": self.configuration.depth,
                    "beam_width": self.configuration.width,
                    "candidate_count": self.configuration.probe_width,
                }
            }
        )
        objective = parameters["objective"]
        constraints = parameters["constraints"]
        weights = {
            name: float(value)
            for section in parameters.values()
            for name, value in section.items()
            if name.endswith("_weight")
        }
        return PlannerRequest(
            tactic_id="build_main",
            tactic_version=self.tactic.identity.version,
            objective_kind="build",
            target_chain=int(objective["target_chain"]),
            target_attack=0,
            deadline_turns=self.configuration.depth,
            deadline_ticks=max(0, int(info.get("policy_deadline", 0))),
            danger_tolerance=float(constraints["danger_tolerance"]),
            trigger_preservation=str(constraints["trigger_preservation"]),
            search_depth=self.configuration.depth,
            search_width=self.configuration.width,
            candidate_count=self.configuration.probe_width,
            latency_budget_ms=float(parameters["planner"]["latency_budget_ms"]),
            fallback_tactic=str(self.tactic.fallback["tactic_id"]),
            objective_weights=weights,
            parameters=parameters,
            score_carry=max(0, int(info.get("score_carry", 0))),
            incoming_attack=max(0, int(info.get("incoming_ojama", 0))),
            all_clear_achieved=bool(info.get("all_clear_achieved", False)),
            all_clear_bonus_pending=bool(
                info.get("all_clear_bonus_pending", False)
            ),
            all_clear_bonus_consumed=bool(
                info.get("all_clear_bonus_consumed", False)
            ),
            build_potential_schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
        )


def configuration_grid(
    depths: Sequence[int] = DEFAULT_DEPTHS,
    widths: Sequence[int] = DEFAULT_WIDTHS,
    probe_widths: Sequence[int] = DEFAULT_PROBE_WIDTHS,
) -> tuple[BuildMainConfiguration, ...]:
    return tuple(
        BuildMainConfiguration(depth, width, probe_width)
        for depth in depths
        for width in widths
        for probe_width in probe_widths
    )


def select_configuration(
    summaries: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    passing = [summary for summary in summaries if bool(summary.get("passed"))]
    if not passing:
        return None
    return min(
        passing,
        key=lambda item: (
            float(item["decision_p95_ms"]),
            int(item["depth"]),
            int(item["width"]),
            int(item["probe_width"]),
        ),
    )


def build_selection(summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    selected = select_configuration(summaries)
    selected_budget = (
        None
        if selected is None
        else {
            "depth": int(selected["depth"]),
            "width": int(selected["width"]),
            "probe_width": int(selected["probe_width"]),
        }
    )
    return {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "selection_order": ["decision_p95_ms", "depth", "width", "probe_width"],
        "selected_configuration": selected_budget,
        "selected_config_id": None if selected is None else selected["config_id"],
        "adopted_budget": dict(FALLBACK_BUDGET if selected is None else selected_budget),
        "quality_gate_passed": selected is not None,
        "all_configurations_failed": selected is None,
        "puyo_130_may_start": False,
        "puyo_130_blockers": ["PUYO-158", "PUYO-129"],
    }


def _evaluate_task(
    task: tuple[BuildMainConfiguration, int, int],
) -> dict[str, Any]:
    configuration, seed, max_steps = task
    policy = ForcedBuildMainPolicy(configuration)
    return _run_safe_game(policy, seed, max_steps)


def evaluate_configurations(
    configurations: Sequence[BuildMainConfiguration],
    *,
    seed: int,
    games: int,
    max_steps: int,
    workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summaries: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    executor = None
    if workers > 1:
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context("spawn"),
        )
    try:
        for configuration in configurations:
            tasks = [
                (configuration, item, max_steps)
                for item in range(seed, seed + games)
            ]
            if executor is None:
                raw = [_evaluate_task(task) for task in tasks]
            else:
                raw = list(executor.map(_evaluate_task, tasks, chunksize=1))
            summary = aggregate_safe_suite(
                configuration.config_id,
                raw,
                max_steps=max_steps,
            )
            summary.update(configuration.to_dict())
            summaries.append(summary)
            records.extend(
                {
                    **configuration.to_dict(),
                    **{key: value for key, value in record.items() if not key.startswith("_")},
                }
                for record in raw
            )
            print(
                f"{configuration.config_id}: mean={summary['mean_max_chain']:.2f} "
                f"premature={summary['premature_fire_count']} "
                f"game_over={summary['game_over_before_limit']} "
                f"p95={summary['decision_p95_ms']:.2f}ms "
                f"gate={'PASS' if summary['passed'] else 'FAIL'}",
                flush=True,
            )
    finally:
        if executor is not None:
            executor.shutdown()
    return summaries, records


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_seed_csv(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    fieldnames = [
        "config_id",
        "depth",
        "width",
        "probe_width",
        "seed",
        "steps",
        "max_chain",
        "premature_fire_count",
        "trigger_opportunities",
        "trigger_loss_count",
        "game_over_before_limit",
        "score_carry",
        "sent_ojama",
        "decision_p50_ms",
        "decision_p95_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def _write_report(
    path: Path,
    summaries: Sequence[Mapping[str, Any]],
    selection: Mapping[str, Any],
) -> None:
    selected = selection["selected_configuration"]
    outcome = (
        "PASS: selected " + str(selection["selected_config_id"])
        if selected is not None
        else "BLOCKED: no configuration passed all quality gates"
    )
    lines = [
        "# PUYO-157 build_main benchmark",
        "",
        f"- result: **{outcome}**",
        f"- adopted budget: `{selection['adopted_budget']}`",
        "- PUYO-130: **not started** (PUYO-158 and PUYO-129 remain incomplete)",
        "",
        "| config | mean max chain | premature | game over | p95 ms | gate |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for summary in summaries:
        lines.append(
            f"| `{summary['config_id']}` | {summary['mean_max_chain']:.2f} | "
            f"{summary['premature_fire_count']} | "
            f"{summary['game_over_before_limit']} | "
            f"{summary['decision_p95_ms']:.2f} | "
            f"{'PASS' if summary['passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "Gate: 30 seeds × 40 moves, mean maximum chain >= 10, "
            "premature 1-9 chain fires = 0, and early game-over = 0.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def checkpoint_evidence(path: str | Path) -> dict[str, Any]:
    target = Path(path).resolve()
    policy = V17StrategyManagerPolicy.from_checkpoint(target)
    registry = policy.registry
    return {
        "path": str(target),
        "sha256": file_sha256(target),
        "size_bytes": target.stat().st_size,
        "strict_load": True,
        "registry_version": registry.registry_version,
        "build_main_version": registry.tactic("build_main").identity.version,
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    configurations = configuration_grid(args.depths, args.widths, args.probe_widths)
    checkpoint = checkpoint_evidence(args.checkpoint)
    summaries, records = evaluate_configurations(
        configurations,
        seed=args.seed,
        games=args.games,
        max_steps=args.max_steps,
        workers=args.workers,
    )
    selection = build_selection(summaries)
    config = {
        "seed": int(args.seed),
        "games": int(args.games),
        "max_steps": int(args.max_steps),
        "workers": int(args.workers),
        "depths": [int(value) for value in args.depths],
        "widths": [int(value) for value in args.widths],
        "probe_widths": [int(value) for value in args.probe_widths],
    }
    result = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "git_commit": git_commit(),
        "evaluation_completed": len(summaries) == len(configurations)
        and all(int(item["games"]) == args.games for item in summaries),
        "quality_gate_passed": bool(selection["quality_gate_passed"]),
        "config": config,
        "checkpoint": checkpoint,
        "selection": selection,
        "configurations": summaries,
    }
    _write_json(output_dir / "checkpoint_evidence.json", checkpoint)
    _write_json(
        output_dir / "configuration_results.json",
        {"schema_version": BENCHMARK_SCHEMA_VERSION, "configurations": summaries},
    )
    _write_seed_csv(output_dir / "seed_results.csv", records)
    _write_json(output_dir / "selection.json", selection)
    _write_json(output_dir / "benchmark_summary.json", result)
    _write_report(output_dir / "benchmark_report.md", summaries, selection)
    artifact_paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "name": "puyo-v1-7-2-build-main-v2",
        "created_at_utc": result["created_at_utc"],
        "git_commit": result["git_commit"],
        "evaluation_completed": result["evaluation_completed"],
        "quality_gate_passed": result["quality_gate_passed"],
        "checkpoint": checkpoint,
        "config": config,
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifact_paths
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    print(
        "PUYO-157 evaluator: "
        f"{'COMPLETE' if result['evaluation_completed'] else 'INCOMPLETE'}; "
        f"quality gate: {'PASS' if result['quality_gate_passed'] else 'BLOCKED'}",
        flush=True,
    )
    return result


def verify_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.artifact_dir)
    manifest = json.loads(
        (output_dir / "benchmark_manifest.json").read_text(encoding="utf-8")
    )
    issues: list[str] = []
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append(f"unexpected benchmark schema: {manifest.get('schema_version')}")
    for artifact in manifest.get("artifacts", ()):
        path = output_dir / str(artifact.get("path", ""))
        if not path.is_file():
            issues.append(f"missing artifact: {path}")
        elif artifact.get("sha256") != file_sha256(path):
            issues.append(f"artifact hash mismatch: {path}")
    checkpoint = checkpoint_evidence(args.checkpoint)
    if checkpoint["sha256"] != manifest.get("checkpoint", {}).get("sha256"):
        issues.append("v1.7.1 checkpoint hash does not match manifest")
    summary = json.loads(
        (output_dir / "benchmark_summary.json").read_text(encoding="utf-8")
    )
    if not summary.get("evaluation_completed"):
        issues.append("benchmark evaluator did not complete")
    if args.require_quality_gate and not summary.get("quality_gate_passed"):
        issues.append("build_main quality gate is blocked")
    expected_selection = build_selection(summary.get("configurations", ()))
    if summary.get("selection") != expected_selection:
        issues.append("saved build_main selection does not match measured configurations")
    result = {
        "passed": not issues,
        "issues": issues,
        "evaluation_completed": bool(summary.get("evaluation_completed")),
        "quality_gate_passed": bool(summary.get("quality_gate_passed")),
        "selected_configuration": expected_selection["selected_configuration"],
        "checkpoint": checkpoint["sha256"],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or verify the PUYO-157 build_main budget sweep."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run all build_main configurations")
    run.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--seed", type=int, default=123)
    run.add_argument("--games", type=int, default=30)
    run.add_argument("--max-steps", type=int, default=40)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--depths", type=int, nargs="+", default=list(DEFAULT_DEPTHS))
    run.add_argument("--widths", type=int, nargs="+", default=list(DEFAULT_WIDTHS))
    run.add_argument(
        "--probe-widths",
        type=int,
        nargs="+",
        default=list(DEFAULT_PROBE_WIDTHS),
    )
    verify = subparsers.add_parser("verify", help="verify hashes and selection")
    verify.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    verify.add_argument("--artifact-dir", default=DEFAULT_OUTPUT_DIR)
    verify.add_argument("--require-quality-gate", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "run":
        positive = (
            args.games,
            args.max_steps,
            args.workers,
            *args.depths,
            *args.widths,
            *args.probe_widths,
        )
        if any(value <= 0 for value in positive):
            parser.error("benchmark counts and budgets must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        result = run_benchmark(args)
        return 0 if result["evaluation_completed"] else 1
    result = verify_benchmark(args)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
