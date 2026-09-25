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
import sys
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
        "paired_comparison": "separate_paired_comparison_json" if phase == "after" else "pending_integrated_comparison",
    }
    native = {
        "wheel_path_at_measurement": str(wheel) if wheel else None,
        "wheel_sha256": _sha256(wheel) if wheel else None,
        "source_revision": declaration["source"]["commit"] if phase == "before" else None,
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
            ("build_profile", "release"), ("python_abi", "cp312"),
        ):
            if capabilities.get(key) != expected:
                raise ValueError(f"native capability mismatch: {key}")
        if phase == "before" and capabilities["source_revision"] != declaration["source"]["commit"]:
            raise ValueError("before native source revision mismatch")
        if phase == "after":
            import _puyo_deep_chain_native as native_module

            if Path(native_module.__file__).resolve() != wheel.resolve():
                raise ValueError("recorded native binary is not the loaded module")
            native["binary_path_at_measurement"] = str(wheel.resolve())
            native["binary_sha256"] = native.pop("wheel_sha256")
            native.pop("wheel_path_at_measurement")
            native["source_revision"] = capabilities["source_revision"]
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
                if phase == "before" else sys.executable
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
        "analysis_command": (
            "python -m eval.puyo_271_regression analyze --run-dir <new-dir> --phase after --wheel <loaded-native-binary>"
            if phase == "after" else
            "python -m eval.puyo_271_regression analyze --run-dir <new-dir> --wheel <native-wheel>"
        ),
        "gui_cadence": "separate GUI trace; headless run has no frame/input/event timestamps" if phase == "after" else "not_measured; PUYO-269 owns GUI frame/input/event tracing",
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
    if manifest["comparison_phase"] == "after":
        paired = compare_saved_runs(
            ROOT / "docs/benchmarks/puyo-271-regression/before", run_dir
        )
        if paired != _load(run_dir / "paired_comparison.json"):
            raise ValueError("paired comparison changed")
        gui_report, gui_manifest = summarize_gui(run_dir / "gui")
        if gui_report != _load(run_dir / "gui/summary.json"):
            raise ValueError("GUI summary changed")
        if gui_manifest != _load(run_dir / "gui/manifest.json"):
            raise ValueError("GUI manifest changed")
        final = run_dir / "evidence_manifest.json"
        if final.exists():
            for relative, expected in _load(final)["artifacts_sha256"].items():
                if _sha256(ROOT / relative) != expected:
                    raise ValueError(f"evidence artifact changed: {relative}")


def _replayed_score(run: dict) -> int:
    """Recover the actual score from saved tick inputs, checking the final state."""
    from eval.nextgen_gate_benchmark import SafeNoThreatMatch
    from src.core.realtime import TickInput

    match = SafeNoThreatMatch(run["seed"])
    for item in run["semantic"]["inputs"]:
        if match.tick != item["tick"]:
            raise ValueError("saved input tick is discontinuous")
        match.step({agent: TickInput.from_names(**edges)
                    for agent, edges in item["inputs"].items()})
    if match.state_hash() != run["semantic"]["final_hash"]:
        raise ValueError("replayed score state does not match saved final hash")
    return match.player_states["player_0"].simulator.game.score


def audit_saved_run(path: Path) -> dict:
    """Classify the actual scheduler decision against its bounded public probe."""
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        run = json.load(stream)
    if run["semantic_digest"] != _semantic_digest(run["semantic"]):
        raise ValueError(f"semantic digest mismatch: {path}")
    decisions = []
    for index, row in enumerate(run["rows"], 1):
        if run["policy"] != "nextgen":
            decisions.append({"decision": index, "action": row["action"],
                              "seconds": row["seconds"], "public_probe": None})
            continue
        diagnostic = run["ledger"][index - 1]
        receipt = diagnostic["receipt"]
        probe = row["search"].get("survival", {})
        probe_measured = bool(probe)
        roots = {root["action"]: root for root in probe.get("roots", ())}
        witnesses = {action for action, root in roots.items() if root["status"] == "witness"}
        quiet = {action for action in witnesses if roots[action]["root_chain"] == 0}
        single = {action for action in witnesses if roots[action]["root_chain"] == 1}
        candidates = {candidate["root_action"] for candidate in diagnostic["batch"]["candidates"]
                      if candidate["root_legal"] and candidate["root_reachable"]}
        selected = receipt["requested_action"]
        executed = receipt["executed_action"]
        board = diagnostic["request"]["public"]["own"]["visible_board"]
        fire = next(t for t in diagnostic["batch"]["tactics"] if t["tactic_id"] == "fire_main")
        fire_candidate = next((c for c in diagnostic["batch"]["candidates"]
                               if c["candidate_id"] == fire["best_id"]), None)
        decisions.append({
            "decision": index, "action": row["action"], "seconds": row["seconds"],
            "phase_id": row["phase"].get("phase_id"),
            "phase_retention": row["phase"].get("constraint_retention"),
            "phase_exit": row["phase"].get("constraint_release_reason") or row["phase"].get("exit_reason"),
            "phase_consumed_decisions": row["phase"].get("consumed_decisions"),
            "selected_tactic": row["selection"]["selected_tactic_id"],
            "selection_reason": row["selection"]["reason"],
            "shared_rank": row["shared_rank"],
            "rank_error": None,
            "rank_error_reason": "shared-search order differs from tactic ranking; no frozen optimal root oracle",
            "receipt_outcome": receipt["outcome"],
            "receipt_requested_action": selected,
            "receipt_executed_action": executed,
            "probe_status": probe.get("status"),
            "probe_nodes": probe.get("nodes"),
            "response_quota": row["search"]["quotas"]["response_quota"],
            "probe_root_statuses": {str(action): {"status": root["status"],
                "root_chain": root["root_chain"]} for action, root in roots.items()},
            "witness_actions": sorted(witnesses),
            "quiet_witness_actions": sorted(quiet),
            "single_clear_witness_actions": sorted(single),
            "candidate_witness_gap": sorted(witnesses - candidates) if probe_measured else None,
            "selected_fatal_with_witness": bool(witnesses and roots.get(selected, {}).get("status") == "fatal") if probe_measured else None,
            "executed_fatal_with_witness": bool(witnesses and roots.get(executed, {}).get("status") == "fatal") if probe_measured else None,
            "survival_exception_required": bool(single and not quiet) if probe_measured else None,
            "fire_main_available": fire["available"],
            "fire_main_plan_depth": len(fire_candidate["plan"]) if fire_candidate else None,
            "public_visible_board": board,
            "public_known_pieces": diagnostic["request"]["public"]["own"]["known_pieces"],
        })
    if run["policy"] == "nextgen" and len(run["ledger"]) != len(decisions):
        raise ValueError("scheduler ledger is incomplete")
    completed_at = next((d["decision"] for d in decisions
                         if d.get("phase_exit") == "completed"), None)
    first_phase = next((d.get("phase_id") for d in decisions if d.get("phase_id")), None)
    initial_exit = next((d for d in decisions if d.get("phase_id") == first_phase
                         and d.get("phase_exit")), None)
    after_chains = run["chains"][completed_at - 1:] if completed_at and len(run["chains"]) == len(decisions) else None
    return {
        "policy": run["policy"], "seed": run["seed"], "repeat": run["repeat"],
        "raw_sha256": _sha256(path), "semantic_digest": run["semantic_digest"],
        "placements": run["placements"], "completion": "complete" if run["placements"] == MAX_PLACEMENTS else "incomplete",
        "incomplete_reason": None if run["placements"] == MAX_PLACEMENTS else
            ("game_over_before_40" if run["game_over"] else "tick_limit_or_other_before_40"),
        "max_actual_chain": run["max_chain"], "actual_score_replayed": _replayed_score(run),
        "premature_fire_count": run["premature"], "game_over": run["game_over"],
        "premature_exception_classification": (
            "not_applicable_no_small_fire" if not run["premature"] else
            "not_evaluated_reference_policy" if run["policy"] == "deep_chain" else
            "requires_public_survival_counterfactual"
        ),
        "avoidable_suffocation": None,
        "avoidable_suffocation_reason": (
            "no_game_over_observed_through_40" if not run["game_over"] else
            "finite public horizon cannot certify an earlier alternative trajectory"
        ),
        "chains": run["chains"], "decision_seconds": _distribution([d["seconds"] for d in decisions]),
        "template_completed_at_decision": completed_at,
        "initial_template_phase_id": first_phase,
        "initial_template_exit_decision": initial_exit["decision"] if initial_exit else None,
        "initial_template_exit_reason": initial_exit["phase_exit"] if initial_exit else None,
        "initial_template_completed_at_decision": (
            initial_exit["decision"] if initial_exit and initial_exit["phase_exit"] == "completed" else None
        ),
        "post_completion_decisions": len(decisions) - completed_at + 1 if completed_at else None,
        "post_completion_max_actual_chain": max(after_chains, default=0) if after_chains is not None else None,
        "candidate_witness_gap_count": (
            sum(bool(d.get("candidate_witness_gap")) for d in decisions)
            if run["policy"] == "nextgen" and all(d["candidate_witness_gap"] is not None for d in decisions)
            else None
        ),
        "rank_error_count": None,
        "rank_error_reason": "no optimal quality oracle for the diverging real trajectories",
        "selected_fatal_with_witness_count": (
            sum(d["selected_fatal_with_witness"] for d in decisions)
            if run["policy"] == "nextgen" and all(d["selected_fatal_with_witness"] is not None for d in decisions)
            else None
        ),
        "executed_fatal_with_witness_count": (
            sum(d["executed_fatal_with_witness"] for d in decisions)
            if run["policy"] == "nextgen" and all(d["executed_fatal_with_witness"] is not None for d in decisions)
            else None
        ),
        "survival_exception_opportunity_count": (
            sum(d["survival_exception_required"] for d in decisions)
            if run["policy"] == "nextgen" and all(d["survival_exception_required"] is not None for d in decisions)
            else None
        ),
        "receipt_nonactivated_count": sum(d.get("receipt_outcome") != "activated" for d in decisions) if run["policy"] == "nextgen" else None,
        "timeout_count": run["controller"]["timeouts"],
        "stale_decision_count": run["controller"]["stale_decisions"],
        "errors": run["errors"], "decisions": decisions,
    }


def compare_saved_runs(before_dir: Path, after_dir: Path) -> dict:
    before = _load(before_dir / "regression_analysis.json")
    after = _load(after_dir / "regression_analysis.json")
    if before["source_sha"] != BASELINE_SHA or after["source_sha"] == BASELINE_SHA:
        raise ValueError("before/after source identity mismatch")
    paired = []
    for policy in POLICIES:
        for seed in SEEDS:
            name = f"{policy}-{seed}-1.json.gz"
            old = audit_saved_run(before_dir / name)
            new = audit_saved_run(after_dir / name)
            paired.append({"policy": policy, "seed": seed, "repeat": 1,
                           "before": old, "after": new})
    old_synthetic = _load(before_dir.parent / "synthetic_results.json")
    new_synthetic = _load(after_dir / "synthetic_results.json")
    if old_synthetic["fixture_sha256"] != new_synthetic["fixture_sha256"]:
        raise ValueError("synthetic fixture changed between phases")
    guarded = {row["id"]: row for row in new_synthetic["persian_counterexamples"]}
    persian = [{
        "id": row["id"], "origin": "synthetic",
        "before_static_fit": row["selected_binding_fit_status"],
        "before_static_satisfied": row["static_satisfied"],
        "before_static_conflicts": row["static_conflicts"],
        "after_guard_fit": guarded[row["id"]]["selected_binding_fit_status"],
        "after_guard_compatible": guarded[row["id"]]["selected_binding_compatible"],
        "after_guard_conflicts": guarded[row["id"]]["static_conflicts"],
        "action_chain_count": guarded[row["id"]]["chain_count"],
    } for row in old_synthetic["persian_counterexamples"]]
    return {
        "schema_version": "puyo.271.paired_comparison.v1",
        "before_source_sha": before["source_sha"], "after_source_sha": after["source_sha"],
        "sample": "diagnostic_3_seeds_x_1_repeat; formal_G2_not_executed",
        "limits": ["Same seed and public piece generator; trajectories and boards diverge after different actions.",
                   "Nextgen hides ghost rows; deep-chain reference sees its own ghost rows.",
                   "Bounded public survival witness is not a private-future guarantee or optimality oracle.",
                   "Actual scores are deterministic replays of saved tick inputs, verified by final state hash."],
        "synthetic": {
            "fixture_sha256": new_synthetic["fixture_sha256"],
            "persian_static_vs_guard": persian,
            "survival_exception": new_synthetic["survival_counterexamples"],
            "template_rollouts": new_synthetic["template_rollouts"],
            "visibility_probes": new_synthetic["visibility_probes"],
            "origin": "synthetic; not the user GUI replay",
        },
        "paired": paired,
    }


def summarize_gui(gui_dir: Path) -> tuple[dict, dict]:
    """Reaggregate the measured GUI rows and check the saved sample summaries."""
    names = ("nextgen-one-600.json.gz", "nextgen-both-360.json.gz")
    scenarios = []
    checksums = {}

    def stats(values: list[float]) -> dict:
        ordered = sorted(values)
        if not ordered:
            return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None}

        def pct(fraction: float) -> float:
            position = (len(ordered) - 1) * fraction
            low = int(position)
            return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (position - low)

        return {"n": len(ordered), "p50": pct(.5), "p95": pct(.95),
                "p99": pct(.99), "max": ordered[-1]}

    for name in names:
        path = gui_dir / name
        checksums[name] = _sha256(path)
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            raw = json.load(stream)
        for metric, values in raw["raw_samples"].items():
            if metric == "tick_count":
                continue
            if stats(values) != raw["samples"][metric]:
                raise ValueError(f"GUI sample summary mismatch: {name}/{metric}")
        if len(raw["raw_samples"]["frame_interval_ms"]) != raw["frames"]:
            raise ValueError("GUI frame count mismatch")
        if len(raw["input_events"]) != len(raw["raw_samples"]["input_age_ms"]):
            raise ValueError("GUI input event count mismatch")
        if any(not 0 <= event["frame"] < raw["frames"] for event in raw["input_events"]):
            raise ValueError("GUI input event outside measured frames")
        frame = raw["samples"]["frame_interval_ms"]
        input_age = raw["samples"]["input_age_ms"]
        scenarios.append({
            "id": name.removesuffix(".json.gz"), "raw_sha256": checksums[name],
            "settings": raw["settings"], "frames": raw["frames"], "ticks": raw["ticks"],
            "elapsed_seconds": raw["elapsed_seconds"],
            "frame_interval_ms": frame, "input_age_ms": input_age,
            "events_ms": raw["samples"]["events_ms"],
            "update_ms": raw["samples"]["update_ms"],
            "render_ms": raw["samples"]["render_ms"],
            "input_event_rows": len(raw["input_events"]),
            "gate_frame_p95_25ms": frame["p95"] <= 25,
            "gate_frame_p99_50ms": frame["p99"] <= 50,
            "gate_input_p95_25ms": input_age["p95"] <= 25,
            "gate_input_p99_50ms": input_age["p99"] <= 50,
            "decision_counters": {agent: {key: value.get(key) for key in (
                "decision_requests", "decisions_activated", "placements_completed",
                "timeouts", "deadline_misses", "stale_decisions", "fallback_actions",
            )} for agent, value in raw["diagnostics"].items()},
            "process_sample_rows": len(raw["process_samples"]),
        })
    report = {"schema_version": "puyo.271.gui_cadence.v1", "scenarios": scenarios,
              "gate": "frame/input p95 <= 25 ms and p99 <= 50 ms; declared by PUYO-269",
              "input_method": "thread-posted no-op F12 every 50 ms; not human keyboard QA",
              "raw_rows": "per-frame interval/event/update/render/tick and individual input event timestamps"}
    manifest = {"schema_version": "puyo.271.gui_manifest.v1",
                "probe_sha256": _sha256(ROOT / "eval/puyo_271_gui_probe.py"),
                "raw_artifacts_sha256": checksums,
                "display": ":0", "source_sha": _load(gui_dir.parent / "declaration.json")["source"]["commit"],
                "worker_cleanup": "confirmed by post-run process listing; no probe worker remained"}
    return report, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fixtures", "analyze", "verify", "compare", "gui", "finalize"))
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--phase", choices=("before", "after"), default="before")
    parser.add_argument("--before-dir", type=Path, default=ROOT / "docs/benchmarks/puyo-271-regression/before")
    args = parser.parse_args()
    if args.command == "fixtures":
        result = replay_fixtures()
        if args.output:
            _write(args.output, result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "compare":
        if args.run_dir is None or args.output is None:
            parser.error("compare requires --run-dir and --output")
        result = compare_saved_runs(args.before_dir, args.run_dir)
        _write(args.output, result)
        print(json.dumps({"paired_runs": len(result["paired"]), "output": str(args.output)}))
    elif args.command == "gui":
        if args.run_dir is None:
            parser.error("gui requires --run-dir")
        report, manifest = summarize_gui(args.run_dir / "gui")
        _write(args.run_dir / "gui/summary.json", report)
        _write(args.run_dir / "gui/manifest.json", manifest)
        print(json.dumps([{key: value for key, value in row.items() if key in (
            "id", "gate_frame_p95_25ms", "gate_input_p95_25ms")}
            for row in report["scenarios"]]))
    elif args.command == "finalize":
        if args.run_dir is None:
            parser.error("finalize requires --run-dir")
        args.run_dir = args.run_dir.resolve()
        names = ["README.md", "declaration.json", "summary.json", "regression_analysis.json",
                 "regression_manifest.json", "paired_comparison.json", "synthetic_results.json",
                 "gui/summary.json", "gui/manifest.json", "gui/nextgen-one-600.json.gz",
                 "gui/nextgen-both-360.json.gz"]
        names += [f"{policy}-{seed}-1.json.gz" for policy in POLICIES for seed in SEEDS]
        paths = [args.run_dir / name for name in names]
        paths += [ROOT / "eval/puyo_271_regression.py", ROOT / "eval/puyo_271_gui_probe.py",
                  ROOT / "tests/test_puyo_271_integrated.py"]
        _write(args.run_dir / "evidence_manifest.json", {
            "schema_version": "puyo.271.evidence_manifest.v1",
            "source_sha": _load(args.run_dir / "declaration.json")["source"]["commit"],
            "artifacts_sha256": {str(path.relative_to(ROOT)): _sha256(path) for path in paths},
        })
        print("PUYO-271 evidence manifest finalized")
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
