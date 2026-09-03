"""PUYO-226 canonical base-feature/component-extraction verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from eval.deep_chain_native_evaluator_benchmark import ROOT, _read_json
from eval.deep_chain_native_evaluator_profile import (
    BASE_SUBSTAGE_NAMES,
    COMBINED_BUDGET_MS,
    EXPANDED_NODE_COUNT,
    PROFILE_SAMPLES,
    run_profile,
    verify_profile,
)
from eval.deep_chain_native_placement_enumeration import (
    verify_placement_enumeration_artifacts,
)
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-226"
FOLLOW_UP_TICKET = "PUYO-227"
SUMMARY_SCHEMA_VERSION = "puyo.native_base_feature_summary.v1"
COMPARISON_SCHEMA_VERSION = "puyo.native_base_feature_comparison.v1"
LEDGER_SCHEMA_VERSION = "puyo.native_base_feature_ledger.v1"
ORACLE_SCHEMA_VERSION = "puyo.native_base_feature_oracle.v1"
MANIFEST_SCHEMA_VERSION = "puyo.native_base_feature_manifest.v1"

DEFAULT_OUTPUT_DIR = (
    ROOT / "docs" / "benchmarks" / "puyo-226-base-feature-component-extraction"
)
RAW_PROFILE_DIRNAME = "after-profile"
BASELINE_DIR = ROOT / "docs" / "benchmarks" / "puyo-225-placement-enumeration"
BASELINE_PROFILE_DIR = BASELINE_DIR / "after-profile"
BASELINE_SUMMARY_PATH = BASELINE_PROFILE_DIR / "benchmark_summary.json"
BASELINE_SEMANTIC_PATH = BASELINE_PROFILE_DIR / "semantic_verification.json"
BASELINE_MEASUREMENT_PATH = BASELINE_PROFILE_DIR / "measurement_contract.json"
NATIVE_MANIFEST_PATH = ROOT / "native" / "deep_chain_native" / "Cargo.toml"
CHAIN_STRUCTURE_SOURCE_PATH = (
    ROOT / "native" / "deep_chain_native" / "src" / "chain_structure.rs"
)

TARGET_STAGE = "base_feature_component_extraction"
NEXT_UNIMPROVED_STAGE = "placement_enumeration_trigger_qualification"
RESIDUAL_STAGE_NAMES = (
    NEXT_UNIMPROVED_STAGE,
    "candidate_ranking_sha256",
    "virtual_resolve_gravity",
    "remaining_structure_scan",
)
BASELINE_UNIMPROVED_STAGE_NAMES = (
    TARGET_STAGE,
    "candidate_ranking_sha256",
    "virtual_resolve_gravity",
    "remaining_structure_scan",
)
LOGICAL_COUNTERS = (
    "pattern_nodes",
    "executed_pattern_probes",
    "resolution_nodes",
    "rank_comparison_calls",
    "rank_tie_calls",
    "sha256_calls",
)
SEMANTIC_SECTIONS = ("fixture", "transition_oracle", "python_native_evaluator")
ORACLE_TESTS = (
    "chain_structure::tests::component_metadata_aggregation_matches_exact_property_corpus",
    "chain_structure::tests::frontier_search_matches_exhaustive_property_corpus",
    "chain_structure::tests::profiled_path_matches_hot_result_and_reports_exact_work",
    "chain_structure::tests::hot_workspaces_stay_below_stack_probe_boundary",
    "chain_structure::tests::candidate_json_capacity_covers_largest_valid_identity",
)
COMPONENT_METADATA_PROPERTY_COUNT = 512
FRONTIER_PROPERTY_COMPARISON_COUNT = 132 * 2
MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT = 30.0
MINIMUM_COMBINED_REDUCTION_PERCENT = 20.0
REQUIRED_CHILD_STATE_BYTES = 80
REQUIRED_HOT_RESULT_BYTES = 24


def _write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _stage(summary: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return summary["stage_decomposition"]["evaluator_stages"][name]


def _stage_cycles_per_node(summary: Mapping[str, Any], name: str) -> float:
    return float(_stage(summary, name)["estimated_cycles_at_profile_p50"]) / float(
        EXPANDED_NODE_COUNT
    )


def _combined_p95_ms(summary: Mapping[str, Any]) -> float:
    return float(
        summary["combined_profile"]["aggregate"][
            "transition_evaluator_p95_ms"
        ]
    )


def _counter_signature(summary: Mapping[str, Any], name: str) -> dict[str, Any]:
    row = summary["call_counts"]["distribution"][name]
    return {
        key: row[key]
        for key in (
            "p50_per_node",
            "p95_per_node",
            "maximum_per_node",
            "exact_600k_total",
        )
    }


def _reduction(before: float, after: float) -> float:
    return (before - after) / before * 100.0


def _run_base_oracle() -> dict[str, Any]:
    commands = [
        [
            "cargo",
            "test",
            "--release",
            "--locked",
            "--manifest-path",
            str(NATIVE_MANIFEST_PATH),
            test_name,
            "--",
            "--exact",
        ]
        for test_name in ORACLE_TESTS
    ]
    outputs: list[str] = []
    return_codes: list[int] = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        outputs.append(completed.stdout + completed.stderr)
        return_codes.append(completed.returncode)
    output = "\n".join(outputs)
    passed = all(code == 0 for code in return_codes)
    return {
        "schema_version": ORACLE_SCHEMA_VERSION,
        "ticket": TICKET,
        "commands": commands,
        "passed": passed,
        "mismatch_count": 0 if passed else None,
        "component_metadata_property_count": COMPONENT_METADATA_PROPERTY_COUNT,
        "frontier_property_comparison_count": FRONTIER_PROPERTY_COMPARISON_COUNT,
        "cache_profile_comparison_count": 1,
        "workspace_layout_check_count": 1,
        "candidate_capacity_check_count": 1,
        "coverage": [
            "bit-parallel component metadata against the exact component scanner",
            "frontier component identities and candidates against exhaustive search",
            "cold/hot exact-key cache result and profile-counter parity",
            "fixed hot workspaces below the 4 KiB stack-probe boundary",
            "largest valid canonical candidate encoding",
        ],
        "source_sha256": file_sha256(CHAIN_STRUCTURE_SOURCE_PATH),
        "test_output_sha256": hashlib.sha256(output.encode()).hexdigest(),
    }


def derive_summary(
    *,
    baseline_summary: Mapping[str, Any],
    baseline_semantic: Mapping[str, Any],
    baseline_measurement: Mapping[str, Any],
    current_summary: Mapping[str, Any],
    current_measurement: Mapping[str, Any],
    oracle: Mapping[str, Any],
    follow_up_ticket: str = FOLLOW_UP_TICKET,
) -> dict[str, Any]:
    baseline_stage_cycles = _stage_cycles_per_node(baseline_summary, TARGET_STAGE)
    current_stage_cycles = _stage_cycles_per_node(current_summary, TARGET_STAGE)
    baseline_stage_ms = float(
        _stage(baseline_summary, TARGET_STAGE)["current_projected_600k_ms"]
    )
    current_stage_ms = float(
        _stage(current_summary, TARGET_STAGE)["current_projected_600k_ms"]
    )
    baseline_combined_ms = _combined_p95_ms(baseline_summary)
    current_combined_ms = _combined_p95_ms(current_summary)
    stage_reduction = _reduction(baseline_stage_cycles, current_stage_cycles)
    combined_reduction = _reduction(baseline_combined_ms, current_combined_ms)
    combined_gate_met = current_combined_ms <= COMBINED_BUDGET_MS
    follow_up_required = not combined_gate_met

    baseline_largest_stage = max(
        BASELINE_UNIMPROVED_STAGE_NAMES,
        key=lambda name: float(
            _stage(baseline_summary, name)["current_projected_600k_ms"]
        ),
    )
    current_largest_stage = max(
        RESIDUAL_STAGE_NAMES,
        key=lambda name: float(
            _stage(current_summary, name)["current_projected_600k_ms"]
        ),
    )
    next_stage_ms = float(
        _stage(current_summary, current_largest_stage)["current_projected_600k_ms"]
    )
    base_substages_ms = {
        name: float(_stage(current_summary, name)["current_projected_600k_ms"])
        for name in BASE_SUBSTAGE_NAMES
    }
    base_substage_total_ms = sum(base_substages_ms.values())

    baseline_counters = {
        name: _counter_signature(baseline_summary, name) for name in LOGICAL_COUNTERS
    }
    current_counters = {
        name: _counter_signature(current_summary, name) for name in LOGICAL_COUNTERS
    }
    current_semantic = current_summary["semantic"]
    response_sha256 = {
        name: {
            "before": baseline_semantic[name]["response_sha256"],
            "after": current_semantic[name]["response_sha256"],
            "matches": baseline_semantic[name]["response_sha256"]
            == current_semantic[name]["response_sha256"],
        }
        for name in SEMANTIC_SECTIONS
    }
    baseline_profile = baseline_measurement["canonical_profile"]
    current_profile = current_measurement["canonical_profile"]
    source = current_summary["source_verification"]
    baseline_attribution = float(
        baseline_summary["stage_decomposition"]["attribution"][
            "sampled_stage_share"
        ]
    )
    current_attribution = float(
        current_summary["stage_decomposition"]["attribution"][
            "sampled_stage_share"
        ]
    )

    comparison = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "operations_per_sample": EXPANDED_NODE_COUNT,
        "sample_count": PROFILE_SAMPLES,
        "outlier_removal": "none",
        "target_stage": TARGET_STAGE,
        "baseline_stage_cycles_per_node": baseline_stage_cycles,
        "current_stage_cycles_per_node": current_stage_cycles,
        "stage_cycle_reduction_percent": stage_reduction,
        "minimum_stage_cycle_reduction_percent": MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT,
        "baseline_stage_projected_600k_ms": baseline_stage_ms,
        "current_stage_projected_600k_ms": current_stage_ms,
        "baseline_combined_p95_600k_ms": baseline_combined_ms,
        "current_combined_p95_600k_ms": current_combined_ms,
        "combined_reduction_percent": combined_reduction,
        "minimum_combined_reduction_percent": MINIMUM_COMBINED_REDUCTION_PERCENT,
        "combined_gate_600k_ms": COMBINED_BUDGET_MS,
        "combined_gate_met": combined_gate_met,
        "acceptance_baseline": (
            "canonical PUYO-225 after profile on the same CPU, frozen corpus, "
            "production config, release wheel contract, and 5 x 600,000 operations"
        ),
    }
    ledger = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "baseline_attributed_share": baseline_attribution,
        "current_attributed_share": current_attribution,
        "baseline_largest_stage": baseline_largest_stage,
        "base_substages_600k_ms": base_substages_ms,
        "base_substage_total_600k_ms": base_substage_total_ms,
        "current_largest_unimproved_stage": current_largest_stage,
        "current_largest_unimproved_stage_600k_ms": next_stage_ms,
        "current_combined_budget_gap_ms": max(
            0.0, current_combined_ms - COMBINED_BUDGET_MS
        ),
        "remaining_if_next_stage_free_600k_ms": max(
            0.0, current_combined_ms - next_stage_ms
        ),
        "follow_up_required": follow_up_required,
        "follow_up_ticket": follow_up_ticket if follow_up_required else None,
    }

    checks = {
        "same_frozen_corpus": baseline_measurement["corpus"]["sha256"]
        == current_measurement["corpus"]["sha256"],
        "same_frozen_config": baseline_measurement["config"]["sha256"]
        == current_measurement["config"]["sha256"],
        "same_five_by_600k_contract": (
            baseline_profile["operations_per_sample"]
            == current_profile["operations_per_sample"]
            == EXPANDED_NODE_COUNT
            and baseline_profile["sample_count"]
            == current_profile["sample_count"]
            == PROFILE_SAMPLES
            and baseline_profile["outlier_removal"]
            == current_profile["outlier_removal"]
            == "none"
        ),
        "production_depth_three": current_measurement["config"][
            "production_max_added_puyos"
        ]
        == 3,
        "baseline_and_current_attribution_at_least_95_percent": (
            baseline_attribution >= 0.95 and current_attribution >= 0.95
        ),
        "target_is_largest_puyo_225_after_stage": baseline_largest_stage
        == TARGET_STAGE,
        "minimum_30_percent_stage_cycle_reduction": stage_reduction
        >= MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT,
        "minimum_20_percent_combined_reduction_or_gate_met": combined_reduction
        >= MINIMUM_COMBINED_REDUCTION_PERCENT
        or combined_gate_met,
        "base_substage_ledger_exact": abs(
            base_substage_total_ms - current_stage_ms
        )
        <= 1e-6,
        "fixture_eight_zero_mismatches": current_semantic["fixture"][
            "record_count"
        ]
        == 8
        and current_semantic["fixture"]["mismatch_count"] == 0,
        "transition_11264_zero_mismatches": current_semantic[
            "transition_oracle"
        ]["record_count"]
        == 11_264
        and current_semantic["transition_oracle"]["mismatch_count"] == 0,
        "selected_child_512_zero_mismatches": current_semantic[
            "python_native_evaluator"
        ]["record_count"]
        == 512
        and current_semantic["python_native_evaluator"]["mismatch_count"] == 0
        and current_semantic["python_native_evaluator"]["invalid_selected_count"]
        == 0,
        "response_bytes_and_sha_order_match": all(
            row["matches"] for row in response_sha256.values()
        ),
        "logical_budget_and_rank_counters_match": baseline_counters
        == current_counters,
        "base_component_oracle_zero_mismatches": (
            oracle.get("passed") is True
            and oracle.get("mismatch_count") == 0
            and oracle.get("component_metadata_property_count")
            == COMPONENT_METADATA_PROPERTY_COUNT
            and oracle.get("frontier_property_comparison_count")
            == FRONTIER_PROPERTY_COMPARISON_COUNT
        ),
        "determinism": current_semantic["determinism"]["mismatch_count"] == 0
        and current_summary["combined_profile"]["aggregate"][
            "determinism_mismatch_count"
        ]
        == 0,
        "normal_hot_path_allocations_zero": source[
            "normal_hot_path_heap_allocations"
        ]
        == 0,
        "child_state_abi_80": source["child_state_bytes"]
        == REQUIRED_CHILD_STATE_BYTES,
        "hot_result_abi_24": source["hot_result_bytes"]
        == REQUIRED_HOT_RESULT_BYTES,
        "profile_checks_pass": bool(current_summary["decision"]["passed"]),
        "follow_up_recorded_when_gate_unmet": not follow_up_required
        or (
            follow_up_ticket == FOLLOW_UP_TICKET
            and current_largest_stage == NEXT_UNIMPROVED_STAGE
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    decision_name = (
        "INVALID"
        if failures
        else "PASS_FOLLOW_UP_REQUIRED"
        if follow_up_required
        else "PASS_GATE_MET"
    )
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "measurement_commit": git_commit(ROOT),
        "comparison": comparison,
        "bottleneck_ledger": ledger,
        "logical_counters": {
            "before": baseline_counters,
            "after": current_counters,
            "matches": baseline_counters == current_counters,
        },
        "response_sha256": response_sha256,
        "semantic": current_semantic,
        "source_verification": source,
        "oracle": dict(oracle),
        "decision": {
            "decision": decision_name,
            "passed": not failures,
            "checks": checks,
            "failed_checks": failures,
        },
    }


def _report(summary: Mapping[str, Any]) -> str:
    comparison = summary["comparison"]
    ledger = summary["bottleneck_ledger"]
    decision = summary["decision"]
    return "\n".join(
        [
            "# PUYO-226 canonical base-feature/component-extraction verification",
            "",
            f"Decision: **{decision['decision']}**.",
            "",
            (
                "Base-feature/component-extraction cycles per node fell from "
                f"{comparison['baseline_stage_cycles_per_node']:.3f} to "
                f"{comparison['current_stage_cycles_per_node']:.3f} "
                f"({comparison['stage_cycle_reduction_percent']:.3f}% reduction)."
            ),
            (
                "Combined transition-plus-evaluator p95 fell from "
                f"{comparison['baseline_combined_p95_600k_ms']:.3f} ms to "
                f"{comparison['current_combined_p95_600k_ms']:.3f} ms "
                f"({comparison['combined_reduction_percent']:.3f}% reduction)."
            ),
            "",
            "## Gates",
            "",
            "- exact component metadata/frontier oracle mismatches: 0",
            "- fixture / transition / selected-child mismatches: 0 / 0 / 0",
            "- response SHA-256 and logical budget/rank counters: unchanged",
            "- normal hot-path allocations: 0; child/result ABI: 80/24 bytes",
            "- production max_added_puyos remains 3",
            "",
            "## Residual budget",
            "",
            (
                f"The combined p95 remains {ledger['current_combined_budget_gap_ms']:.3f} ms "
                "above the 820.625 ms gate. The next independent stage is "
                f"`{ledger['current_largest_unimproved_stage']}`; follow-up "
                f"{ledger['follow_up_ticket']} is required."
                if ledger["follow_up_required"]
                else "The combined p95 meets the 820.625 ms gate."
            ),
            "",
        ]
    )


def run_base_feature_verification(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    destination = Path(output_dir)
    raw_dir = destination / RAW_PROFILE_DIRNAME
    current = run_profile(output_dir=raw_dir)
    current_measurement = _read_json(raw_dir / "measurement_contract.json")
    baseline = _read_json(BASELINE_SUMMARY_PATH)
    baseline_semantic = _read_json(BASELINE_SEMANTIC_PATH)
    baseline_measurement = _read_json(BASELINE_MEASUREMENT_PATH)
    oracle = _run_base_oracle()
    summary = derive_summary(
        baseline_summary=baseline,
        baseline_semantic=baseline_semantic,
        baseline_measurement=baseline_measurement,
        current_summary=current,
        current_measurement=current_measurement,
        oracle=oracle,
    )
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "base_component_oracle.json", oracle)
    _write_json(destination / "before_after.json", summary["comparison"])
    _write_json(destination / "bottleneck_ledger.json", summary["bottleneck_ledger"])
    _write_json(destination / "benchmark_summary.json", summary)
    (destination / "benchmark_report.md").write_text(
        _report(summary), encoding="utf-8"
    )
    artifact_paths = sorted(
        path
        for path in destination.rglob("*")
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    raw_manifest = _read_json(raw_dir / "benchmark_manifest.json")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "measurement_commit": summary["measurement_commit"],
        "baseline_summary_path": str(BASELINE_SUMMARY_PATH.relative_to(ROOT)),
        "baseline_summary_sha256": file_sha256(BASELINE_SUMMARY_PATH),
        "baseline_semantic_sha256": file_sha256(BASELINE_SEMANTIC_PATH),
        "baseline_measurement_sha256": file_sha256(BASELINE_MEASUREMENT_PATH),
        "raw_profile_manifest_sha256": file_sha256(
            raw_dir / "benchmark_manifest.json"
        ),
        "release_wheel_path": raw_manifest["environment"]["release_wheel_path"],
        "release_wheel_sha256": raw_manifest["environment"]["release_wheel_sha256"],
        "decision": summary["decision"]["decision"],
        "passed": summary["decision"]["passed"],
        "follow_up_ticket": summary["bottleneck_ledger"]["follow_up_ticket"],
        "artifacts": [
            describe_artifact(path, run_dir=destination, role=path.stem)
            for path in artifact_paths
        ],
    }
    _write_json(destination / "benchmark_manifest.json", manifest)
    return summary


def verify_base_feature_artifacts(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    require_exact_wheel: bool = False,
    rerun_oracle: bool = True,
    historical: bool = False,
) -> list[str]:
    destination = Path(output_dir)
    issues = verify_profile(
        destination / RAW_PROFILE_DIRNAME,
        require_exact_wheel=require_exact_wheel,
        allow_historical_wheel=historical,
    )
    issues.extend(
        f"PUYO-225 baseline: {issue}"
        for issue in verify_placement_enumeration_artifacts(
            BASELINE_DIR,
            rerun_oracle=False,
            historical=True,
        )
    )
    try:
        manifest = _read_json(destination / "benchmark_manifest.json")
        summary = _read_json(destination / "benchmark_summary.json")
        oracle = _read_json(destination / "base_component_oracle.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [*issues, f"artifact read failed: {exc}"]
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        issues.append("unexpected manifest schema")
    if summary.get("schema_version") != SUMMARY_SCHEMA_VERSION:
        issues.append("unexpected summary schema")
    for artifact in manifest.get("artifacts", []):
        path = destination / artifact["path"]
        if not path.is_file():
            issues.append(f"missing artifact: {artifact['path']}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"artifact hash mismatch: {artifact['path']}")
    for path, field, label in (
        (BASELINE_SUMMARY_PATH, "baseline_summary_sha256", "baseline summary"),
        (BASELINE_SEMANTIC_PATH, "baseline_semantic_sha256", "baseline semantic"),
        (
            BASELINE_MEASUREMENT_PATH,
            "baseline_measurement_sha256",
            "baseline measurement",
        ),
    ):
        if file_sha256(path) != manifest.get(field):
            issues.append(f"{label} hash drifted")
    raw_manifest_path = destination / RAW_PROFILE_DIRNAME / "benchmark_manifest.json"
    if file_sha256(raw_manifest_path) != manifest.get("raw_profile_manifest_sha256"):
        issues.append("raw profile manifest hash drifted")
    decision = summary.get("decision", {})
    if decision.get("passed") != all(decision.get("checks", {}).values()):
        issues.append("decision does not match checks")
    if not decision.get("passed"):
        issues.append("PUYO-226 verification did not pass")
    if manifest.get("follow_up_ticket") != summary.get("bottleneck_ledger", {}).get(
        "follow_up_ticket"
    ):
        issues.append("follow-up ticket differs between manifest and ledger")
    if not historical and oracle.get("source_sha256") != file_sha256(
        CHAIN_STRUCTURE_SOURCE_PATH
    ):
        issues.append("base-component oracle source drifted")
    if rerun_oracle:
        rerun = _run_base_oracle()
        if not rerun["passed"]:
            issues.append("base-component oracle rerun failed")
    return issues


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
        if command == "verify":
            subparser.add_argument("--require-exact-wheel", action="store_true")
            subparser.add_argument("--skip-oracle-rerun", action="store_true")
            subparser.add_argument("--historical", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_base_feature_verification(args.output_dir)
        print(json.dumps(summary["decision"], indent=2, sort_keys=True))
        return 0 if summary["decision"]["passed"] else 1
    issues = verify_base_feature_artifacts(
        args.output_dir,
        require_exact_wheel=args.require_exact_wheel,
        rerun_oracle=not args.skip_oracle_rerun and not args.historical,
        historical=args.historical,
    )
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print(f"verified: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
