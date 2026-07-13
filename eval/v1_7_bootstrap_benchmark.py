"""PUYO-128 benchmark, GUI QA, and lineage evidence for v1.7.1."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch

from agents.state_analyzer import (
    ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
    ANALYZER_INPUT_SCHEMA_VERSION,
)
from agents.v1_7_planner import PLANNER_REQUEST_SCHEMA_VERSION
from agents.v1_7_strategy_manager import (
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    MODEL_VERSION,
    POLICY_TYPE,
    validate_v1_7_strategy_manager_checkpoint_payload,
)
from agents.v1_7_tactics import TACTIC_SCHEMA_VERSION
from eval.analyzer_scenarios import build_report, evaluate_scenarios, write_report
from eval.arena import (
    ArenaResult,
    MatchResult,
    run_parallel_paired_series,
    summarize_result,
    write_markdown_report,
    write_matches_csv,
    write_summary_csv,
)
from eval.lifecycle_audit import audit_realtime_lifecycle
from eval.realtime_arena import replay_realtime_match
from src.core.diagnostics import ALL_CLEAR_DIAGNOSTICS_SCHEMA_VERSION
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp
from train.lineage import (
    LINEAGE_MANIFEST_SCHEMA_VERSION,
    build_registry,
    validate_registry,
    write_markdown_report as write_lineage_markdown,
    write_registry,
)


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_bootstrap_benchmark.v1"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-1-smoke"
BASELINES = ("manager_rule", "beam", "v1_7_analyzer_manager")
BASELINE_LABELS = {
    "manager_rule": "manager_rule",
    "beam": "standard_beam",
    "v1_7_analyzer_manager": "v1_7_0",
}


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _policy_spec(
    policy_type: str,
    *,
    seed: int,
    checkpoint_path: str | None = None,
    beam_depth: int = 10,
    beam_width: int = 48,
) -> dict[str, Any]:
    return {
        "policy_type": policy_type,
        "seed": seed,
        "checkpoint_path": checkpoint_path,
        "device": "cpu",
        "deterministic": True,
        "beam_depth": beam_depth,
        "beam_width": beam_width,
        "beam_scenarios": 1,
        "beam_minimum_chain": 6,
    }


def _side_value(match: MatchResult, stem: str, *, policy_a: bool) -> Any:
    side = match.policy_a_side if policy_a else (
        "player_1" if match.policy_a_side == "player_0" else "player_0"
    )
    return getattr(match, f"{stem}_{side}")


def enrich_summary(summary: dict[str, Any], result: ArenaResult) -> dict[str, Any]:
    """Add policy-relative PUYO-128 lifecycle and behavior metrics."""

    matches = result.matches
    summary.update(
        {
            "max_chain_policy_a": max(
                (int(_side_value(match, "max_chain", policy_a=True)) for match in matches),
                default=0,
            ),
            "max_chain_policy_b": max(
                (int(_side_value(match, "max_chain", policy_a=False)) for match in matches),
                default=0,
            ),
            "self_chokes_policy_a": sum(
                int(_side_value(match, "self_choke", policy_a=True)) for match in matches
            ),
            "self_chokes_policy_b": sum(
                int(_side_value(match, "self_choke", policy_a=False)) for match in matches
            ),
            "initial_empty_false_positives": sum(
                int(getattr(match, f"initial_empty_false_positive_player_{index}"))
                for match in matches
                for index in (0, 1)
            ),
            "all_clear_bonus_double_consumptions": sum(
                int(getattr(match, f"all_clear_bonus_double_consumed_player_{index}"))
                for match in matches
                for index in (0, 1)
            ),
            "all_clear_achieved_policy_a": sum(
                int(_side_value(match, "all_clear_achieved", policy_a=True)) for match in matches
            ),
            "all_clear_bonus_consumed_policy_a": sum(
                int(_side_value(match, "all_clear_bonus_consumed", policy_a=True))
                for match in matches
            ),
            "all_clear_bonus_generated_ojama_policy_a": sum(
                int(_side_value(match, "all_clear_bonus_generated_ojama", policy_a=True))
                for match in matches
            ),
            "all_clear_bonus_canceled_ojama_policy_a": sum(
                int(_side_value(match, "all_clear_bonus_canceled_ojama", policy_a=True))
                for match in matches
            ),
            "all_clear_bonus_outgoing_ojama_policy_a": sum(
                int(_side_value(match, "all_clear_bonus_outgoing_ojama", policy_a=True))
                for match in matches
            ),
        }
    )
    denominator = max(1, len(matches))
    summary["self_choke_rate_policy_a"] = summary["self_chokes_policy_a"] / denominator
    summary["self_choke_rate_policy_b"] = summary["self_chokes_policy_b"] / denominator
    return summary


def load_checkpoint_evidence(checkpoint_path: str | Path) -> tuple[dict[str, Any], Mapping[str, Any]]:
    target = Path(checkpoint_path)
    if not target.is_file():
        raise FileNotFoundError(f"v1.7.1 checkpoint not found: {target}")
    payload = torch.load(target, map_location="cpu", weights_only=False)
    if not isinstance(payload, Mapping):
        raise ValueError("v1.7.1 checkpoint must contain a mapping")
    validation_errors = validate_v1_7_strategy_manager_checkpoint_payload(payload)
    schema = payload.get("checkpoint_schema", {})
    dataset = payload.get("dataset", {})
    evidence = {
        "schema_version": "puyo.v1_7_checkpoint_evidence.v1",
        "path": str(target),
        "sha256": file_sha256(target),
        "size_bytes": target.stat().st_size,
        "validation_errors": validation_errors,
        "run_id": payload.get("run_id"),
        "global_step": payload.get("global_step"),
        "policy_type": payload.get("policy_type"),
        "model_family": payload.get("model_family"),
        "model_version": payload.get("model_version"),
        "git_commit": schema.get("git_commit") if isinstance(schema, Mapping) else None,
        "seed": schema.get("seed") if isinstance(schema, Mapping) else None,
        "feature_contract": dict(payload.get("feature_contract", {})),
        "checkpoint_metadata": dict(payload.get("checkpoint_metadata", {})),
        "dataset": dict(dataset) if isinstance(dataset, Mapping) else {},
        "lifecycle_carry_contract": dict(payload.get("lifecycle_carry_contract", {})),
    }
    return evidence, payload


def _run_gui_qa(
    *,
    checkpoint: Path,
    output_dir: Path,
    seed: int,
    max_ticks: int,
    max_frames: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    qa_path = output_dir / "gui_qa.json"
    replay_path = output_dir / "gui_qa_replay.json"
    command = [
        sys.executable,
        "-m",
        "eval.realtime_versus_ui",
        "--policy-a",
        POLICY_TYPE,
        "--checkpoint-a",
        str(checkpoint),
        "--policy-b",
        "v1_7_analyzer_manager",
        "--seed",
        str(seed),
        "--max-ticks",
        str(max_ticks),
        "--speed",
        "1",
        "--result-json",
        str(qa_path),
        "--replay",
        str(replay_path),
        "--qa-notes",
        "PUYO-128 deterministic dummy-SDL GUI QA; repeat visibly through main.py",
        "--max-frames",
        str(max_frames),
    ]
    environment = os.environ.copy()
    environment.update({"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"})
    subprocess.run(command, check=True, cwd=Path(__file__).resolve().parents[1], env=environment)
    qa = _read_json(qa_path)
    replay = _read_json(replay_path)
    final_hash = replay_realtime_match(replay)
    lifecycle = audit_realtime_lifecycle(
        initial_all_clear_diagnostics=replay.get("initial_all_clear_diagnostics"),
        ticks=replay.get("ticks", ()),
    )
    if qa.get("diagnostics", {}).get("lifecycle_coverage") != lifecycle:
        raise AssertionError("GUI QA lifecycle audit does not match its replay")
    return qa, replay, {"verified": True, "final_hash": final_hash, "lifecycle": lifecycle}


def build_gates(
    *,
    checkpoint_evidence: Mapping[str, Any],
    scenario_report: Mapping[str, Any],
    summaries: Sequence[Mapping[str, Any]],
    gui_qa: Mapping[str, Any] | None,
    gui_verification: Mapping[str, Any] | None,
    lineage_issues: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    summary_by_policy = {str(item["policy_b"]): item for item in summaries}
    direct = summary_by_policy.get("v1_7_analyzer_manager", {})
    total_wins = sum(int(item.get("wins_policy_a", 0)) for item in summaries)
    max_chain = max((int(item.get("max_chain_policy_a", 0)) for item in summaries), default=0)
    initial_false_positives = sum(
        int(item.get("initial_empty_false_positives", 0)) for item in summaries
    )
    double_consumptions = sum(
        int(item.get("all_clear_bonus_double_consumptions", 0)) for item in summaries
    )
    gui_controller = (
        gui_qa.get("diagnostics", {}).get("controller", {}).get("player_0", {})
        if gui_qa is not None
        else {}
    )
    gui_result = gui_qa.get("result", {}) if gui_qa is not None else {}
    gui_lifecycle = (
        gui_verification.get("lifecycle", {}).get("players", {})
        if gui_verification is not None
        else {}
    )
    gui_initial_false = sum(
        int(value.get("initial_empty_false_positives", 0))
        for value in gui_lifecycle.values()
    )
    gui_double = sum(
        int(value.get("double_consumptions", 0)) for value in gui_lifecycle.values()
    )

    def gate(passed: bool, actual: Any, expected: str) -> dict[str, Any]:
        return {"passed": bool(passed), "actual": actual, "expected": expected}

    scenario_summary = scenario_report.get("summary", {})
    return {
        "checkpoint_valid": gate(
            not checkpoint_evidence.get("validation_errors"),
            checkpoint_evidence.get("validation_errors", []),
            "no validation errors",
        ),
        "analyzer_scenarios": gate(
            scenario_summary.get("scenarios") == 24 and scenario_summary.get("failed") == 0,
            scenario_summary,
            "24/24 passed",
        ),
        "initial_empty_false_positives": gate(
            initial_false_positives == 0 and gui_initial_false == 0,
            {"headless": initial_false_positives, "gui": gui_initial_false},
            "0",
        ),
        "all_clear_bonus_double_consumptions": gate(
            double_consumptions == 0 and gui_double == 0,
            {"headless": double_consumptions, "gui": gui_double},
            "0",
        ),
        "minimum_win": gate(total_wins >= 1, total_wins, ">= 1 across all formal matches"),
        "minimum_chain": gate(max_chain >= 1, max_chain, ">= 1"),
        "self_choke_vs_v1_7_0": gate(
            bool(direct)
            and float(direct.get("self_choke_rate_policy_a", 1.0))
            <= float(direct.get("self_choke_rate_policy_b", -1.0)),
            {
                "v1_7_1": direct.get("self_choke_rate_policy_a"),
                "v1_7_0": direct.get("self_choke_rate_policy_b"),
            },
            "v1.7.1 <= v1.7.0",
        ),
        "gui_completed": gate(
            bool(gui_qa) and bool(gui_result.get("completed")),
            gui_result,
            "completed without interruption",
        ),
        "gui_decision_activated": gate(
            int(gui_controller.get("decisions_activated", 0)) >= 1,
            int(gui_controller.get("decisions_activated", 0)),
            ">= 1",
        ),
        "replay_verified": gate(
            bool(gui_verification and gui_verification.get("verified")),
            None if gui_verification is None else gui_verification.get("final_hash"),
            "all ticks and final hash match",
        ),
        "lineage_valid": gate(not lineage_issues, list(lineage_issues), "0 issues"),
    }


def _lineage_manifest(
    *,
    output_dir: Path,
    checkpoint_evidence: Mapping[str, Any],
    checkpoint_payload: Mapping[str, Any],
    promotion_passed: bool,
) -> dict[str, Any]:
    checkpoint_id = f"checkpoint:{checkpoint_evidence['sha256']}"
    run_id = str(checkpoint_evidence.get("run_id") or "unknown")
    dataset = checkpoint_evidence.get("dataset", {})
    dataset_id = str(dataset.get("dataset_id") or "unknown")
    metadata = checkpoint_evidence.get("checkpoint_metadata", {})
    schemas = metadata.get("schemas", {}) if isinstance(metadata, Mapping) else {}
    common_schemas = {
        "analyzer": schemas.get("analyzer_diagnostics", ANALYZER_DIAGNOSTICS_SCHEMA_VERSION),
        "all_clear_diagnostics": ALL_CLEAR_DIAGNOSTICS_SCHEMA_VERSION,
        "feature": FEATURE_SCHEMA_VERSION,
    }
    role = "candidate" if promotion_passed else "rejected"
    decision = (
        "PUYO-128 benchmark hard gates passed"
        if promotion_passed
        else "PUYO-128 benchmark hard gates require another training round"
    )
    checkpoint_metadata = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "checkpoint_id": checkpoint_evidence["sha256"],
        "parent_checkpoint_id": "model_version:v1.7.0",
        "training_run_id": run_id,
        "git_commit": checkpoint_evidence.get("git_commit") or "unknown",
        "policy_type": POLICY_TYPE,
        "analyzer_schema_version": ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
        "tactic_schema_version": TACTIC_SCHEMA_VERSION,
        "planner_schema_version": PLANNER_REQUEST_SCHEMA_VERSION,
        "training_config_path": "train/config/v1_7_manager_bootstrap.yaml",
        "datasets": [dataset_id],
        "evaluations": ["evaluation:PUYO-128"],
        "promotion_state": role,
        "schemas": common_schemas,
        "compatibility": {"status": "native", "feature_shape_changed": False},
        "declared_path": checkpoint_evidence.get("path"),
    }
    feature_contract = checkpoint_evidence.get("feature_contract", {})
    nodes = [
        {
            "id": "model_version:v1.7.0",
            "node_type": "model_version",
            "label": "v1.7.0 Analyzer Manager",
            "metadata": {
                "version": "v1.7.0",
                "model_family": MODEL_FAMILY,
                "policy_type": "v1_7_analyzer_manager",
                "promotion_state": "previous_stable",
                "git_commit": git_commit(),
                "decision_reason": "PUYO-128 comparison baseline",
            },
        },
        {
            "id": "model_version:v1.7.1",
            "node_type": "model_version",
            "label": "v1.7.1 Bootstrap Manager",
            "metadata": {
                "version": MODEL_VERSION,
                "model_family": MODEL_FAMILY,
                "policy_type": POLICY_TYPE,
                "promotion_state": role,
                "git_commit": checkpoint_evidence.get("git_commit"),
                "decision_reason": decision,
            },
        },
        {
            "id": "feature_schema:v1.7.0",
            "node_type": "feature_schema",
            "label": "v1.7.0 structured analyzer input",
            "metadata": {
                "schema_version": ANALYZER_INPUT_SCHEMA_VERSION,
                "representation": "rule-based structured analyzer input",
                "compatibility": {"status": "native"},
            },
        },
        {
            "id": "feature_schema:v1.7.1",
            "node_type": "feature_schema",
            "label": "v1.7.1 learned manager feature contract",
            "metadata": {
                "schema_version": FEATURE_SCHEMA_VERSION,
                "representation": "ordered learned feature vectors",
                "context_dim": feature_contract.get("context_dim"),
                "tactic_dim": feature_contract.get("tactic_dim"),
                "preview_dim": feature_contract.get("preview_dim"),
                "difference_from_v1_7_0": "adds ordered context/tactic/preview vectors for learned arbitration",
                "compatibility": {"status": "retrain_required", "feature_shape_changed": True},
            },
        },
        {
            "id": f"dataset:{dataset_id}",
            "node_type": "dataset",
            "label": f"bootstrap dataset {dataset_id[:12]}",
            "metadata": {
                "dataset_version": dataset.get("dataset_version") or dataset.get("schema_version"),
                "sha256": dataset.get("manifest_sha256"),
                "declared_path": dataset.get("path"),
                "schemas": common_schemas,
                "compatibility": dict(dataset.get("compatibility", {})),
            },
        },
        {
            "id": f"training_run:{run_id}",
            "node_type": "training_run",
            "label": run_id,
            "metadata": {
                "trainer_name": checkpoint_payload.get("checkpoint_schema", {}).get("trainer_name"),
                "seed": checkpoint_evidence.get("seed"),
                "metrics": {"global_step": checkpoint_evidence.get("global_step")},
            },
        },
        {
            "id": checkpoint_id,
            "node_type": "checkpoint",
            "label": f"{run_id}:bootstrap",
            "metadata": checkpoint_metadata,
        },
        {
            "id": "evaluation:PUYO-128",
            "node_type": "evaluation",
            "label": "PUYO-128 bootstrap benchmark and GUI QA",
            "path": str(output_dir / "benchmark_summary.json"),
            "metadata": {
                "evaluation_kind": "paired_benchmark_gui_replay",
                "status": "passed" if promotion_passed else "failed",
                "schemas": common_schemas,
                "compatibility": {"status": "native"},
            },
        },
        {
            "id": f"registry_role:{role}",
            "node_type": "registry_role",
            "label": role,
            "metadata": {},
        },
    ]
    edges = [
        {"source": "model_version:v1.7.1", "target": "model_version:v1.7.0", "edge_type": "derived_from", "metadata": {}},
        {"source": "model_version:v1.7.0", "target": "feature_schema:v1.7.0", "edge_type": "uses_schema", "metadata": {}},
        {"source": "model_version:v1.7.1", "target": "feature_schema:v1.7.1", "edge_type": "uses_schema", "metadata": {}},
        {"source": f"training_run:{run_id}", "target": f"dataset:{dataset_id}", "edge_type": "trained_with", "metadata": {}},
        {"source": f"training_run:{run_id}", "target": checkpoint_id, "edge_type": "produced", "metadata": {}},
        {"source": "model_version:v1.7.1", "target": checkpoint_id, "edge_type": "implements", "metadata": {}},
        {"source": checkpoint_id, "target": "evaluation:PUYO-128", "edge_type": "evaluated_by", "metadata": {}},
        {"source": checkpoint_id, "target": f"registry_role:{role}", "edge_type": "promoted_to" if promotion_passed else "rejected_by", "metadata": {"reason": decision}},
    ]
    return {"schema_version": LINEAGE_MANIFEST_SCHEMA_VERSION, "nodes": nodes, "edges": edges}


def _write_benchmark_report(path: Path, summary: Mapping[str, Any]) -> None:
    gates = summary["gates"]
    lines = [
        "# v1.7.1 Bootstrap Benchmark / GUI QA / Lineage",
        "",
        f"- result: **{'PASS' if summary['passed'] else 'FAIL'}**",
        f"- checkpoint: `{summary['checkpoint']['path']}`",
        f"- checkpoint sha256: `{summary['checkpoint']['sha256']}`",
        f"- seeds: {summary['config']['seed']}–{summary['config']['seed'] + summary['config']['games'] - 1} (paired sides)",
        "",
        "## Hard Gates",
        "",
        "| gate | result | actual | expected |",
        "|---|---|---|---|",
    ]
    for name, gate in gates.items():
        lines.append(
            f"| `{name}` | {'PASS' if gate['passed'] else 'FAIL'} | "
            f"`{json.dumps(gate['actual'], sort_keys=True, default=str)}` | {gate['expected']} |"
        )
    lines.extend(
        [
            "",
            "## Paired Results",
            "",
            "| opponent | matches | wins | losses | draws | score rate | max chain | self-choke |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in summary["baselines"]:
        lines.append(
            f"| `{item['policy_b']}` | {item['games']} | {item['wins_policy_a']} | "
            f"{item['wins_policy_b']} | {item['draws']} | {item['score_rate_policy_a']:.3f} | "
            f"{item['max_chain_policy_a']} | {item['self_chokes_policy_a']} |"
        )
    lines.extend(
        [
            "",
            "## Human-visible QA",
            "",
            "Run `python3 main.py`, choose 観戦, and use the checkpoint above as "
            "1P `v1_7_bootstrap_manager` against 2P `v1_7_analyzer_manager` at seed 123 and speed 1x.",
            "Open `gui_qa_replay.json` with `python3 -m eval.model_viewer` to inspect tick diagnostics and lineage.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = Path(args.checkpoint)
    checkpoint_evidence, checkpoint_payload = load_checkpoint_evidence(checkpoint)
    _write_json(output_dir / "checkpoint_evidence.json", checkpoint_evidence)

    scenario_report = build_report(evaluate_scenarios())
    write_report(output_dir / "analyzer_report.json", scenario_report)

    learned_spec = _policy_spec(POLICY_TYPE, seed=args.seed, checkpoint_path=str(checkpoint))
    summaries: list[dict[str, Any]] = []
    for baseline in BASELINES:
        baseline_spec = _policy_spec(
            baseline,
            seed=args.seed + 10_000,
            beam_depth=args.beam_depth,
            beam_width=args.beam_width,
        )
        result = run_parallel_paired_series(
            learned_spec,
            baseline_spec,
            games=args.games,
            seed=args.seed,
            max_steps=args.max_steps,
            workers=args.workers,
        )
        label = BASELINE_LABELS[baseline]
        summary = summarize_result(
            result,
            label=f"v1_7_1_vs_{label}",
            policy_a=POLICY_TYPE,
            policy_b=baseline,
            checkpoint_a=str(checkpoint),
            checkpoint_b=None,
            games=len(result.matches),
            seed=args.seed,
            max_steps=args.max_steps,
        )
        enrich_summary(summary, result)
        summaries.append(summary)
        write_matches_csv(output_dir / f"{label}_matches.csv", result.matches)
        write_summary_csv(output_dir / f"{label}_summary.csv", summary)
        write_markdown_report(output_dir / f"{label}_report.md", summary)
        print(
            f"{baseline}: wins={summary['wins_policy_a']} "
            f"max_chain={summary['max_chain_policy_a']} "
            f"self_chokes={summary['self_chokes_policy_a']}"
        )

    gui_qa = None
    gui_verification = None
    if not args.skip_gui:
        gui_qa, _, gui_verification = _run_gui_qa(
            checkpoint=checkpoint,
            output_dir=output_dir,
            seed=args.gui_seed,
            max_ticks=args.gui_max_ticks,
            max_frames=args.gui_max_frames,
        )

    preliminary_gates = build_gates(
        checkpoint_evidence=checkpoint_evidence,
        scenario_report=scenario_report,
        summaries=summaries,
        gui_qa=gui_qa,
        gui_verification=gui_verification,
        lineage_issues=[],
    )
    promotion_passed = all(gate["passed"] for name, gate in preliminary_gates.items() if name != "lineage_valid")
    config = {
        "games": args.games,
        "matches_per_baseline": args.games * 2,
        "seed": args.seed,
        "max_steps": args.max_steps,
        "workers": args.workers,
        "beam_depth": args.beam_depth,
        "beam_width": args.beam_width,
        "beam_scenarios": 1,
        "beam_minimum_chain": 6,
        "gui_seed": args.gui_seed,
        "gui_max_ticks": args.gui_max_ticks,
        "gui_speed": 1,
    }
    summary_payload: dict[str, Any] = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "passed": False,
        "config": config,
        "checkpoint": checkpoint_evidence,
        "analyzer": scenario_report["summary"],
        "baselines": summaries,
        "gui": gui_qa,
        "gui_verification": gui_verification,
        "gates": preliminary_gates,
    }
    _write_json(output_dir / "benchmark_summary.json", summary_payload)

    lineage_manifest = _lineage_manifest(
        output_dir=output_dir,
        checkpoint_evidence=checkpoint_evidence,
        checkpoint_payload=checkpoint_payload,
        promotion_passed=promotion_passed,
    )
    lineage_manifest_path = output_dir / "lineage_manifest.json"
    _write_json(lineage_manifest_path, lineage_manifest)
    registry = build_registry([lineage_manifest_path])
    lineage_issues = validate_registry(registry)
    write_registry(registry, output_dir / "lineage_registry.json")
    write_lineage_markdown(registry, output_dir / "lineage_report.md")

    gates = build_gates(
        checkpoint_evidence=checkpoint_evidence,
        scenario_report=scenario_report,
        summaries=summaries,
        gui_qa=gui_qa,
        gui_verification=gui_verification,
        lineage_issues=lineage_issues,
    )
    summary_payload["gates"] = gates
    summary_payload["lineage_issues"] = lineage_issues
    summary_payload["passed"] = all(gate["passed"] for gate in gates.values())
    _write_json(output_dir / "benchmark_summary.json", summary_payload)
    _write_benchmark_report(output_dir / "benchmark_report.md", summary_payload)

    artifact_paths = sorted(
        path for path in output_dir.iterdir()
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "name": "puyo-v1-7-1-bootstrap",
        "created_at_utc": summary_payload["created_at_utc"],
        "checkpoint": {
            "path": str(checkpoint),
            "sha256": checkpoint_evidence["sha256"],
        },
        "config": config,
        "passed": summary_payload["passed"],
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifact_paths
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    print(f"PUYO-128 benchmark: {'PASS' if summary_payload['passed'] else 'FAIL'}")
    return summary_payload


def verify_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.artifact_dir)
    manifest = _read_json(output_dir / "benchmark_manifest.json")
    issues: list[str] = []
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append(f"unexpected benchmark schema: {manifest.get('schema_version')}")
    for artifact in manifest.get("artifacts", []):
        path = output_dir / str(artifact.get("path", ""))
        if not path.is_file():
            issues.append(f"missing artifact: {path}")
        elif artifact.get("sha256") != file_sha256(path):
            issues.append(f"artifact hash mismatch: {path}")
    checkpoint_evidence, _ = load_checkpoint_evidence(args.checkpoint)
    if checkpoint_evidence["sha256"] != manifest.get("checkpoint", {}).get("sha256"):
        issues.append("checkpoint hash does not match benchmark manifest")
    replay_path = output_dir / "gui_qa_replay.json"
    if replay_path.is_file():
        replay_realtime_match(_read_json(replay_path))
    summary = _read_json(output_dir / "benchmark_summary.json")
    if not summary.get("passed"):
        issues.append("benchmark hard gates are not all passed")
    registry = _read_json(output_dir / "lineage_registry.json")
    if registry.get("issues"):
        issues.append(f"lineage registry has {len(registry['issues'])} issues")
    result = {"passed": not issues, "issues": issues, "checkpoint": checkpoint_evidence["sha256"]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run and verify the PUYO-128 v1.7.1 benchmark.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run paired benchmark, GUI QA, and lineage generation")
    run.add_argument("--checkpoint", required=True)
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--games", type=int, default=20, help="seed count; paired sides doubles matches")
    run.add_argument("--seed", type=int, default=123)
    run.add_argument("--max-steps", type=int, default=40)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--beam-depth", type=int, default=10)
    run.add_argument("--beam-width", type=int, default=48)
    run.add_argument("--gui-seed", type=int, default=123)
    run.add_argument("--gui-max-ticks", type=int, default=600)
    run.add_argument("--gui-max-frames", type=int, default=680)
    run.add_argument("--skip-gui", action="store_true")
    verify = subparsers.add_parser("verify", help="verify saved artifact hashes and hard gates")
    verify.add_argument("--checkpoint", required=True)
    verify.add_argument("--artifact-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.games <= 0 or args.max_steps <= 0 or args.workers <= 0:
            parser.error("games, max-steps, and workers must be positive")
        if args.gui_max_ticks <= 0 or args.gui_max_frames <= 0:
            parser.error("GUI limits must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        result = run_benchmark(args)
    else:
        result = verify_benchmark(args)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
