"""Replay PUYO-271 public counterexamples and inspect paired safe-build runs.

The measured trajectories come from ``eval.nextgen_safe_build_diagnostic``.
This module only replays small synthetic boards and analyzes saved raw runs;
it does not turn three diagnostic seeds into the formal PUYO-266 G2 gate.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
from collections import Counter
from pathlib import Path

from agents.template_catalog import (
    TemplateSelector,
    _conditions,
    _evaluate,
    _game,
    _wire,
    load_template_catalog,
    match_templates,
)
from eval.nextgen_template_fixtures import _public_board
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.constants import Direction

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests/fixtures/puyo_271_regression_cases.json"
SCHEMA = "puyo.271.regression.v1"
SEEDS = (55, 123, 124)
POLICIES = ("nextgen", "deep_chain")
MAX_PLACEMENTS = 40
BASELINE_SHA = "c0c77d944ff3ed276a4b44a40a779cd72bc0977a"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "p50": None, "p95": None, "max": None}

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        low = int(position)
        return ordered[low] + (
            ordered[min(low + 1, len(ordered) - 1)] - ordered[low]
        ) * (position - low)

    return {"n": len(ordered), "p50": percentile(.5),
            "p95": percentile(.95), "max": ordered[-1]}


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name") and ":" in line:
                return line.split(":", 1)[1].strip()
    return platform.processor()


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _board(rows: list[str]) -> list[list[int]]:
    if len(rows) > 12 or any(len(row) != 6 or set(row) - set("01234") for row in rows):
        raise ValueError("expected at most 12 bottom-up six-cell visible rows")
    return [[int(cell) for cell in row] for row in rows] + [[0] * 6 for _ in range(14 - len(rows))]


def _action(game, description: dict) -> tuple[dict, tuple[tuple[int, ...], ...]]:
    result = game.place_current_pair_and_resolve(
        description["axis_x"], Direction[description["rotation"]], spawn_next=True
    )
    if result is None:
        raise AssertionError(f"fixture action is not legal: {description}")
    return result, _wire(game)


def _legal(game) -> tuple[bool, ...]:
    return tuple(
        game.find_landing_y(action.axis_x, action.rotation) is not None
        for action in PLACEMENT_ACTIONS
    )


def replay_fixtures(path: Path = CASES) -> dict:
    fixture = _load(path)
    if fixture["schema_version"] != "puyo.271.regression_cases.v1":
        raise ValueError("unsupported regression fixture schema")
    catalog = load_template_catalog(ROOT / fixture["catalog"])
    budget = fixture["matcher_budget"]
    output: dict[str, list[dict]] = {
        "persian_counterexamples": [], "survival_counterexamples": [],
        "template_rollouts": [], "visibility_probes": [], "gui_scenarios": [],
    }

    persian = next(t for t in catalog.templates if t.id == "persian").variants[0]
    for case in fixture["persian_counterexamples"]:
        board = _board(case["rows_bottom_up"])
        satisfied, conflicts = _evaluate(
            board, _conditions(persian, "identity"), case["binding"]
        )
        game = _game(board, case["current_pair"])
        legal = _legal(game)
        binding_key = tuple(sorted(case["binding"].items()))
        selected_key = ("persian", persian.id, "identity", binding_key)
        match = match_templates(
            catalog, _public_board(game), [case["current_pair"]],
            node_budget=budget["node_budget"],
            binding_budget=budget["binding_budget"],
            reachable_mask=legal, preferred_key=selected_key,
        )
        matched = next(c for c in match.candidates if c.key == selected_key)
        action = next(
            i for i, value in enumerate(PLACEMENT_ACTIONS)
            if value.axis_x == case["action"]["axis_x"]
            and value.rotation == Direction[case["action"]["rotation"]]
        )
        assert legal[action], case["id"]
        result, after = _action(game, case["action"])
        # Frozen expected values describe the original matcher without guards.
        legacy_conditions = _conditions(persian, "identity")
        legacy_satisfied, legacy_conflicts = _evaluate(board, (*legacy_conditions[:3], ()), case["binding"])
        assert (len(legacy_satisfied), legacy_conflicts) == (
            case["expected_static_satisfied"], case["expected_static_conflicts"]
        ), case["id"]
        assert (result["chain_count"], result["game_over"]) == (
            case["expected_chain_count"], case["expected_game_over"]
        ), case["id"]
        quiet_alternatives = 0
        shape_preserving_quiet_alternatives = 0
        for i, candidate in enumerate(PLACEMENT_ACTIONS):
            if i == action or not legal[i]:
                continue
            branch = _game(board, case["current_pair"])
            actual, branch_board = _action(
                branch, {"axis_x": candidate.axis_x, "rotation": candidate.rotation.name}
            )
            quiet = actual["chain_count"] == 0 and not actual["game_over"]
            quiet_alternatives += int(quiet)
            after_satisfied, after_conflicts = _evaluate(
                branch_board, _conditions(persian, "identity"), case["binding"]
            )
            shape_preserving_quiet_alternatives += int(
                quiet and after_conflicts == 0 and len(after_satisfied) >= len(satisfied)
            )
        output["persian_counterexamples"].append({
            "id": case["id"], "origin": "synthetic",
            "static_satisfied": len(satisfied), "static_conflicts": conflicts,
            "before_static_conflicts": legacy_conflicts,
            "selected_binding_compatible": matched.compatible,
            "action_index": action, "chain_count": result["chain_count"],
            "game_over": result["game_over"],
            "quiet_legal_alternatives": quiet_alternatives,
            "shape_preserving_quiet_alternatives": shape_preserving_quiet_alternatives,
            "selected_binding_fit_status": matched.fit_status,
            "selected_binding_reason": matched.reason,
            "selected_binding_witness_actions": list(matched.witness_actions),
            "matcher_cutoff": match.cutoff,
            "after_rows_bottom_up": [list(row) for row in after[:3]],
        })

    for case in fixture["survival_counterexamples"]:
        board = _board(case["rows_bottom_up"])
        outcomes = {}
        for name in ("safe", "fatal"):
            game = _game(board, case["current_pair"])
            result, _ = _action(game, case[f"{name}_action"])
            outcomes[name] = {
                "chain_count": result["chain_count"],
                "game_over": result["game_over"],
            }
            assert outcomes[name] == {
                "chain_count": case[f"expected_{name}_chain_count"],
                "game_over": case[f"expected_{name}_game_over"],
            }, case["id"]
        output["survival_counterexamples"].append({"id": case["id"], "origin": "synthetic", **outcomes})

    for case in fixture["template_rollouts"]:
        game = _game(_board(case["rows_bottom_up"]), case["public_pieces"][0])
        chosen_key = None
        actions = []
        statuses = []
        for piece in case["public_pieces"]:
            game = _game([list(row) for row in _wire(game)], piece)
            result = match_templates(
                catalog, _public_board(game), [piece],
                node_budget=budget["node_budget"],
                binding_budget=budget["binding_budget"],
                reachable_mask=_legal(game),
            )
            if chosen_key is None:
                selection = TemplateSelector(catalog, 246).select_initial(result)
                assert selection.candidate is not None, case["id"]
                chosen_key = selection.candidate.key
                assert chosen_key[:2] == (case["template_id"], case["variant_id"]), case["id"]
            candidate = next(c for c in result.candidates if c.key == chosen_key)
            statuses.append(candidate.fit_status)
            assert candidate.fit_status == "fit" and candidate.witness_actions, case["id"]
            action_index = candidate.witness_actions[0]
            assert _legal(game)[action_index], case["id"]
            action = PLACEMENT_ACTIONS[action_index]
            actual, _ = _action(game, {"axis_x": action.axis_x, "rotation": action.rotation.name})
            assert actual["chain_count"] == 0 and not actual["game_over"], case["id"]
            actions.append(action_index)
        final = match_templates(catalog, _public_board(game), (), node_budget=0, binding_budget=0)
        completed = any(c.key == chosen_key and c.complete for c in final.candidates)
        assert (len(actions), completed) == (
            case["expected_decisions"], case["expected_completed"]
        ), case["id"]
        output["template_rollouts"].append({
            "id": case["id"], "origin": "synthetic",
            "template_id": case["template_id"], "binding": dict(chosen_key[3]),
            "fit_statuses": statuses, "witness_actions": actions,
            "decisions": len(actions), "completed": completed,
        })

    for case in fixture["visibility_probes"]:
        game = _game(_board(case["rows_bottom_up"]), [1, 2])
        result = match_templates(
            catalog, _public_board(game), case["known_pieces"],
            node_budget=case["node_budget"],
            binding_budget=budget["binding_budget"], reachable_mask=_legal(game),
        )
        candidates = [c for c in result.candidates if c.template_id == case["template_id"]]
        has_fit = any(c.fit_status == "fit" for c in candidates)
        assert (has_fit, result.cutoff) == (
            case["expected_has_fit"], case["expected_cutoff"]
        ), case["id"]
        output["visibility_probes"].append({
            "id": case["id"], "origin": "synthetic",
            "known_prefix_length": len(case["known_pieces"]),
            "node_budget": case["node_budget"],
            "coverage_nodes": result.coverage_nodes,
            "has_fit": has_fit, "cutoff": result.cutoff,
            "fit_statuses": dict(Counter(c.fit_status for c in candidates)),
        })
    output["gui_scenarios"] = fixture["gui_scenarios"]
    return {"schema_version": SCHEMA, "fixture_sha256": _sha256(path),
            "catalog_digest": catalog.semantic_digest, **output}


def analyze_runs(
    run_dir: Path, *, fixture_path: Path = CASES,
    wheel: Path | None = None, phase: str = "before",
    validate_current_source: bool = True,
) -> tuple[dict, dict]:
    if phase not in ("before", "after"):
        raise ValueError("phase must be before or after")
    if validate_current_source:
        from agents.deep_chain_builder import load_deep_chain_builder_config

        config = load_deep_chain_builder_config()
        if config.default_target_chain_count != 10 or config.quality_floor != 10:
            raise ValueError("deep_chain target10/quality10 configuration changed")
    declaration = _load(run_dir / "declaration.json")
    summary = _load(run_dir / "summary.json")
    if summary["source_changed_during_run"] or summary["declaration"] != declaration:
        raise ValueError("source changed during the measured run or declaration mismatch")
    if tuple(declaration["seeds"]) != SEEDS or declaration["repeats"] != 1:
        raise ValueError("PUYO-271 before sample requires prespecified 55/123/124 x one repeat")
    if phase == "before" and declaration["source"]["commit"] != BASELINE_SHA:
        raise ValueError("before run is not the frozen c0c77d9 source")
    if (declaration["profile"], declaration["backend"], declaration["placements"], declaration["latency_mode"]) != (
        "nextgen_safe_build", "native", MAX_PLACEMENTS, "configured"
    ):
        raise ValueError("measured conditions differ from the frozen sample")
    rows = []
    all_latency: dict[str, list[float]] = {policy: [] for policy in POLICIES}
    run_profiles = {}
    artifacts = {}
    config_paths = (
        "train/config/deep_chain_builder.yaml", "train/config/deep_chain_backend.yaml",
        "train/config/nextgen_templates.yaml", "agents/nextgen_profiles.py",
    )
    config_hashes = {name: _sha256(ROOT / name) for name in config_paths}
    if validate_current_source:
        for name, actual in config_hashes.items():
            if declaration["source"]["files_sha256"].get(name) != actual:
                raise ValueError(f"source/config mismatch: {name}")
    for filename in ("declaration.json", "summary.json"):
        artifacts[filename] = _sha256(run_dir / filename)
    for policy in POLICIES:
        for seed in SEEDS:
            name = f"{policy}-{seed}-1.json.gz"
            path = run_dir / name
            artifacts[name] = _sha256(path)
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                run = json.load(stream)
            if (run["policy"], run["seed"], run["repeat"]) != (policy, seed, 1):
                raise ValueError(f"wrong identity in {name}")
            run_profiles[f"{policy}/{seed}/1"] = {
                "profile": run["profile"], "search_config": run["search_config"]
            }
            chains = run["chains"]
            if (len(chains), max(chains, default=0), sum(0 < n < 10 for n in chains)) != (
                run["placements"], run["max_chain"], run["premature"]
            ):
                raise ValueError(f"invalid quality counters in {name}")
            if run["semantic_digest"] != _semantic_digest(run["semantic"]):
                raise ValueError(f"semantic digest mismatch in {name}")
            if policy == "deep_chain" and (
                run["profile"]["depth"], run["profile"]["width"],
                run["profile"]["scenarios"], run["profile"]["max_expanded_nodes"]
            ) != (16, 250, 6, 600000):
                raise ValueError("deep_chain reference budget mismatch")
            if policy == "nextgen":
                quotas = [r["search"]["quotas"] for r in run["rows"]]
                if any((q["shared_quota"], q["template_quota"], q["response_quota"]) != (600000, 128, 256) for q in quotas):
                    raise ValueError("nextgen safe-build quota mismatch")
            latency = [float(r["seconds"]) for r in run["rows"]]
            all_latency[policy].extend(latency)
            receipts = [d["receipt"] for d in run["ledger"]] if policy == "nextgen" else []
            summary_row = next(
                result for result in summary["policies"][policy]["results"]
                if result["seed"] == seed and result["repeat"] == 1
            )
            if any(summary_row[key] != run[key] for key in (
                "max_chain", "premature", "game_over", "placements", "semantic_digest"
            )):
                raise ValueError(f"saved quality summary mismatch in {name}")
            row = {
                "policy": policy, "seed": seed, "repeat": 1,
                "origin": "seeded_simulation; not the user GUI replay",
                "placements": run["placements"], "max_actual_chain": run["max_chain"],
                "actual_score": None,
                "actual_score_reason": "existing safe-build diagnostic does not export a comparable score ledger",
                "premature_fire_count": run["premature"], "game_over": run["game_over"],
                "premature_exception_classification": "not_evaluated",
                "completion": "complete" if run["placements"] == MAX_PLACEMENTS else "incomplete",
                "incomplete_reason": None if run["placements"] == MAX_PLACEMENTS else (
                    "game_over_before_40" if run["game_over"] else "tick_limit_or_other_before_40"
                ),
                "semantic_digest": run["semantic_digest"],
                "decision_seconds": _distribution(latency),
                "candidate_gap": None,
                "candidate_gap_reason": "No frozen per-decision safe/template oracle for these seeded trajectories",
                "shared_rank_nonzero_count": sum(r.get("shared_rank", 0) != 0 for r in run["rows"]) if policy == "nextgen" else None,
                "shared_rank_scope": "selected action versus shared-search order; this alone is not a tactic or quality ranking error" if policy == "nextgen" else None,
                "receipt_nonactivated_count": sum(r["outcome"] != "activated" for r in receipts) if policy == "nextgen" else None,
                "receipt_count": len(receipts) if policy == "nextgen" else None,
                "selection_to_receipt_request_mismatch_count": sum(
                    selected["action"] != receipt["requested_action"]
                    for selected, receipt in zip(run["rows"], receipts)
                ) if policy == "nextgen" and len(run["rows"]) == len(receipts) else None,
                "receipt_action_mismatch_count": sum(
                    r["outcome"] == "activated" and r["requested_action"] != r["executed_action"]
                    for r in receipts
                ) if policy == "nextgen" else None,
                "selection_tactics": dict(Counter(r["selection"]["selected_tactic_id"] for r in run["rows"])) if policy == "nextgen" else None,
                "gui_frame_cadence": None,
                "gui_frame_cadence_reason": "headless diagnostic has no GUI frame/input/event timestamps",
            }
            rows.append(row)
    for policy in POLICIES:
        old = summary["policies"][policy]["decision_seconds"]
        new = _distribution(all_latency[policy])
        if old != new:
            raise ValueError(f"{policy} saved latency summary mismatch")
    report = {
        "schema_version": SCHEMA, "comparison_phase": phase,
        "source_sha": declaration["source"]["commit"],
        "sample": "diagnostic_3_seeds_x_1_repeat; formal_G2_not_executed",
        "rows": rows,
        "policies": {
            policy: {
                "decision_seconds": _distribution(all_latency[policy]),
                "max_actual_chains_by_seed": {str(r["seed"]): r["max_actual_chain"] for r in rows if r["policy"] == policy},
                "premature_fire_count": sum(r["premature_fire_count"] for r in rows if r["policy"] == policy),
                "game_over_count": sum(r["game_over"] for r in rows if r["policy"] == policy),
            } for policy in POLICIES
        },
        "quality_verdict": "diagnostic_observation_only",
        "human_gui_qa": "not_performed",
        "paired_comparison": "pending_integrated_comparison",
    }
    native = {
        "wheel_path_at_measurement": str(wheel) if wheel else None,
        "wheel_sha256": _sha256(wheel) if wheel else None,
        "source_revision": declaration["source"]["commit"],
        "build_profile": "release",
        "python_abi": "cp312",
        "execution_mode": "scenario-6",
        "scenario_threads": 6,
    }
    if phase == "before":
        native["build_target_dir_at_measurement"] = "/tmp/puyo271-corpus-target"
        native["build_command"] = (
            "CARGO_TARGET_DIR=/tmp/puyo271-corpus-target "
            f"PUYO_NATIVE_SOURCE_REVISION={BASELINE_SHA} "
            "/home/sion2/workspaces/dev/puyo_ai_dev_platform/.venv/bin/python "
            "-m maturin build --release --locked --zig "
            "--compatibility manylinux_2_28 "
            "--interpreter /tmp/puyo271-corpus-venv/bin/python "
            "--manifest-path native/deep_chain_native/Cargo.toml --out dist/native"
        )
    if wheel:
        from agents.deep_chain_native import NativeDeepChainBackend

        capabilities = NativeDeepChainBackend(canonical=True).capabilities.to_dict()
        for key, expected in (
            ("source_revision", declaration["source"]["commit"]),
            ("build_profile", "release"), ("python_abi", "cp312"),
        ):
            if capabilities.get(key) != expected:
                raise ValueError(f"native capability mismatch: {key}")
        native["capabilities"] = capabilities
    manifest = {
        "schema_version": SCHEMA,
        "comparison_phase": phase,
        "source_sha": declaration["source"]["commit"],
        "fixture_sha256": _sha256(fixture_path),
        "synthetic_before_result_sha256": (
            _sha256(ROOT / "docs/benchmarks/puyo-271-regression/synthetic_results.json")
            if phase == "before" else None
        ),
        "input_conditions": {
            "seeds": list(SEEDS), "repeats": 1, "max_placements": MAX_PLACEMENTS,
            "public_current_next_next2": True,
            "opponent": "inactive_safe_no_threat",
            "latency_mode": "configured_zero_inference_ticks",
            "backend": "native", "deep_chain_profile": "reference",
            "deep_chain_target": 10, "deep_chain_quality_floor": 10,
            "deep_chain_budget": {"depth": 16, "width": 250, "scenarios": 6, "max_expanded_nodes": 600000},
            "nextgen_profile": "nextgen_safe_build",
            "nextgen_quotas": {"shared": 600000, "template": 128, "response": 256},
            "cache": "fresh policy instance for each policy/seed/repeat; one process; fixed nextgen-then-deep_chain order",
            "native": native,
            "comparison_limits": declaration["comparison_limits"],
            "run_profiles": run_profiles,
        },
        "host": {
            "capture_timing": "post-run analysis on the same host; the original diagnostic did not embed per-run affinity",
            "python_environment_at_measurement": (
                "/tmp/puyo271-corpus-venv with read-only dependency path to main .venv"
                if phase == "before" else "caller-provided; record separately"
            ),
            "platform": platform.platform(), "processor": _cpu_model(),
            "logical_cpus": os.cpu_count(),
            "affinity_cpus": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "python": platform.python_version(),
            "thread_env": {name: os.environ.get(name) for name in ("OMP_NUM_THREADS", "RAYON_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")},
        },
        "config_sha256": config_hashes,
        "raw_artifacts_sha256": artifacts,
        "run_command": "python -m eval.nextgen_safe_build_diagnostic --output <new-dir> --profile nextgen_safe_build --repeats 1",
        "analysis_command": "python -m eval.puyo_271_regression analyze --run-dir <new-dir> --wheel <native-wheel>",
        "gui_cadence": "not_measured; PUYO-269 owns GUI frame/input/event tracing",
        "formal_G2": "not_executed; PUYO-266 owns 30 seeds x 2 repeats",
    }
    return report, manifest


def verify(run_dir: Path) -> None:
    manifest = _load(run_dir / "regression_manifest.json")
    if manifest["fixture_sha256"] != _sha256(CASES):
        raise ValueError("fixture changed")
    expected_fixture_result = manifest.get("synthetic_before_result_sha256")
    if expected_fixture_result is not None and expected_fixture_result != _sha256(
        ROOT / "docs/benchmarks/puyo-271-regression/synthetic_results.json"
    ):
        raise ValueError("saved synthetic before result changed")
    for name, expected in manifest["raw_artifacts_sha256"].items():
        if _sha256(run_dir / name) != expected:
            raise ValueError(f"raw artifact changed: {name}")
    report, _ = analyze_runs(
        run_dir, phase=manifest["comparison_phase"], validate_current_source=False
    )
    if report != _load(run_dir / "regression_analysis.json"):
        raise ValueError("derived analysis changed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fixtures", "analyze", "verify"))
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--phase", choices=("before", "after"), default="before")
    args = parser.parse_args()
    if args.command == "fixtures":
        result = replay_fixtures()
        if args.output:
            _write(args.output, result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.run_dir is None:
            parser.error("--run-dir is required")
        if args.command == "analyze":
            report, manifest = analyze_runs(args.run_dir, wheel=args.wheel, phase=args.phase)
            _write(args.run_dir / "regression_analysis.json", report)
            _write(args.run_dir / "regression_manifest.json", manifest)
            print(json.dumps(report["policies"], ensure_ascii=False, indent=2))
        else:
            verify(args.run_dir)
            print("PUYO-271 regression evidence verified")


if __name__ == "__main__":
    main()
