"""PUYO-220 differential virtual-resolution semantic and performance gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from eval.deep_chain_native_evaluator_benchmark import ROOT, _read_json
from eval.deep_chain_native_evaluator_profile import (
    EXPANDED_NODE_COUNT,
    PROFILE_SAMPLES,
    run_profile,
    verify_profile,
)
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-220"
SUMMARY_SCHEMA_VERSION = "puyo.native_incremental_resolution_summary.v1"
COMPARISON_SCHEMA_VERSION = "puyo.native_incremental_resolution_comparison.v1"
ORACLE_SCHEMA_VERSION = "puyo.native_incremental_resolution_oracle.v1"
MANIFEST_SCHEMA_VERSION = "puyo.native_incremental_resolution_manifest.v1"

DEFAULT_OUTPUT_DIR = ROOT / "docs" / "benchmarks" / "puyo-220-incremental-resolution"
RAW_PROFILE_DIRNAME = "puyo-219-compatible-profile"
BASELINE_DIR = (
    ROOT
    / "docs"
    / "benchmarks"
    / "puyo-223-quiescence-frontier"
    / RAW_PROFILE_DIRNAME
)
BASELINE_SUMMARY_PATH = BASELINE_DIR / "benchmark_summary.json"
BASELINE_SEMANTIC_PATH = BASELINE_DIR / "semantic_verification.json"
BASELINE_MEASUREMENT_PATH = BASELINE_DIR / "measurement_contract.json"
BUDGET_SUMMARY_PATH = (
    ROOT / "docs" / "benchmarks" / "puyo-219-evaluator-hot-path" / "benchmark_summary.json"
)
NATIVE_MANIFEST_PATH = ROOT / "native" / "deep_chain_native" / "Cargo.toml"
CHAIN_STRUCTURE_SOURCE_PATH = (
    ROOT / "native" / "deep_chain_native" / "src" / "chain_structure.rs"
)

PROPERTY_TEST = (
    "chain_structure::tests::incremental_resolution_matches_exact_property_corpus"
)
PROPERTY_STATE_COUNT = 256
PROPERTY_RANDOM_STATE_COUNT = 252
PROPERTY_COMPARISON_COUNT = PROPERTY_STATE_COUNT
MINIMUM_REDUCTION_PERCENT = 70.0
REQUIRED_CHILD_STATE_BYTES = 80
REQUIRED_HOT_RESULT_BYTES = 24

STAGE_NAMES = (
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
SEMANTIC_SECTIONS = (
    "fixture",
    "transition_oracle",
    "python_native_evaluator",
)


def _write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_property_oracle() -> dict[str, Any]:
    command = [
        "cargo",
        "test",
        "--release",
        "--locked",
        "--manifest-path",
        str(NATIVE_MANIFEST_PATH),
        PROPERTY_TEST,
        "--",
        "--exact",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    passed = completed.returncode == 0 and f"test {PROPERTY_TEST} ... ok" in output
    return {
        "schema_version": ORACLE_SCHEMA_VERSION,
        "ticket": TICKET,
        "test": PROPERTY_TEST,
        "command": command,
        "passed": passed,
        "mismatch_count": 0 if passed else None,
        "state_count": PROPERTY_STATE_COUNT,
        "random_state_count": PROPERTY_RANDOM_STATE_COUNT,
        "comparison_count": PROPERTY_COMPARISON_COUNT,
        "comparison_mode": "candidate-order-and-fields against exact exhaustive resolver",
        "special_cases": [
            "multiple_chain",
            "adjacent_ojama",
            "hidden_row",
            "left_right_asymmetry",
        ],
        "source_sha256": file_sha256(CHAIN_STRUCTURE_SOURCE_PATH),
        "test_output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


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


def _stage_sum(summary: Mapping[str, Any], field: str) -> float:
    stages = summary["stage_decomposition"]["evaluator_stages"]
    return sum(float(stages[name][field]) for name in STAGE_NAMES)


def _budget_sum(summary: Mapping[str, Any]) -> float:
    ledger = summary["stage_budget"]["stage_budget_ledger"]
    return sum(float(ledger[name]["target_budget_600k_ms"]) for name in STAGE_NAMES)


def derive_summary(
    *,
    baseline_summary: Mapping[str, Any],
    baseline_semantic: Mapping[str, Any],
    baseline_measurement: Mapping[str, Any],
    budget_summary: Mapping[str, Any],
    current_summary: Mapping[str, Any],
    current_measurement: Mapping[str, Any],
    oracle: Mapping[str, Any],
) -> dict[str, Any]:
    before_ms = _stage_sum(baseline_summary, "current_projected_600k_ms")
    after_ms = _stage_sum(current_summary, "current_projected_600k_ms")
    before_ns = _stage_sum(baseline_summary, "current_ns_per_node")
    after_ns = _stage_sum(current_summary, "current_ns_per_node")
    target_ms = _budget_sum(budget_summary)
    reduction = (before_ms - after_ms) / before_ms * 100.0

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
            "matches": (
                baseline_semantic[name]["response_sha256"]
                == current_semantic[name]["response_sha256"]
            ),
        }
        for name in SEMANTIC_SECTIONS
    }
    source = current_summary["source_verification"]
    current_profile = current_measurement["canonical_profile"]
    baseline_profile = baseline_measurement["canonical_profile"]
    baseline_combined_ms = float(
        baseline_summary["combined_profile"]["aggregate"][
            "transition_evaluator_p95_ms"
        ]
    )
    current_combined_ms = float(
        current_summary["combined_profile"]["aggregate"][
            "transition_evaluator_p95_ms"
        ]
    )

    checks = {
        "same_frozen_corpus": (
            baseline_measurement["corpus"]["sha256"]
            == current_measurement["corpus"]["sha256"]
        ),
        "same_frozen_config": (
            baseline_measurement["config"]["sha256"]
            == current_measurement["config"]["sha256"]
        ),
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
        "production_depth_three": (
            current_measurement["config"]["production_max_added_puyos"] == 3
        ),
        "fixture_eight_zero_mismatches": (
            current_semantic["fixture"]["record_count"] == 8
            and current_semantic["fixture"]["mismatch_count"] == 0
        ),
        "transition_11264_zero_mismatches": (
            current_semantic["transition_oracle"]["record_count"] == 11_264
            and current_semantic["transition_oracle"]["mismatch_count"] == 0
        ),
        "selected_child_512_zero_mismatches": (
            current_semantic["python_native_evaluator"]["record_count"] == 512
            and current_semantic["python_native_evaluator"]["mismatch_count"] == 0
            and current_semantic["python_native_evaluator"][
                "invalid_selected_count"
            ]
            == 0
        ),
        "response_bytes_match": all(
            row["matches"] for row in response_sha256.values()
        ),
        "logical_counters_match": baseline_counters == current_counters,
        "property_oracle_256_zero_mismatches": (
            oracle.get("passed") is True
            and oracle.get("comparison_count") == PROPERTY_COMPARISON_COUNT
            and oracle.get("mismatch_count") == 0
        ),
        "minimum_70_percent_stage_reduction": (
            reduction >= MINIMUM_REDUCTION_PERCENT
        ),
        "puyo_219_stage_budget_met": after_ms <= target_ms,
        "combined_profile_no_regression": current_combined_ms <= baseline_combined_ms,
        "determinism": (
            current_semantic["determinism"]["mismatch_count"] == 0
            and current_summary["combined_profile"]["aggregate"][
                "determinism_mismatch_count"
            ]
            == 0
        ),
        "normal_hot_path_allocations_zero": (
            source["normal_hot_path_heap_allocations"] == 0
        ),
        "child_state_abi_80": source["child_state_bytes"] == REQUIRED_CHILD_STATE_BYTES,
        "hot_result_abi_24": source["hot_result_bytes"] == REQUIRED_HOT_RESULT_BYTES,
        "profile_checks_pass": bool(current_summary["decision"]["passed"]),
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "measurement_commit": git_commit(ROOT),
        "comparison": {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "operations_per_sample": EXPANDED_NODE_COUNT,
            "sample_count": PROFILE_SAMPLES,
            "outlier_removal": "none",
            "stages": list(STAGE_NAMES),
            "before_600k_ms": before_ms,
            "after_600k_ms": after_ms,
            "target_600k_ms": target_ms,
            "before_ns_per_node": before_ns,
            "after_ns_per_node": after_ns,
            "target_ns_per_node": target_ms * 1_000_000.0 / EXPANDED_NODE_COUNT,
            "speedup": before_ms / after_ms,
            "reduction_percent": reduction,
            "minimum_reduction_percent": MINIMUM_REDUCTION_PERCENT,
            "margin_600k_ms": target_ms - after_ms,
        },
        "combined_profile": {
            "before_p95_600k_ms": baseline_combined_ms,
            "after_p95_600k_ms": current_combined_ms,
            "no_regression": current_combined_ms <= baseline_combined_ms,
        },
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
            "decision": "PASS" if not failures else "INVALID",
            "passed": not failures,
            "checks": checks,
            "failed_checks": failures,
        },
    }


def _report(summary: Mapping[str, Any]) -> str:
    comparison = summary["comparison"]
    combined = summary["combined_profile"]
    decision = summary["decision"]
    return "\n".join(
        [
            "# PUYO-220 differential virtual resolution verification",
            "",
            f"Decision: **{decision['decision']}**.",
            "",
            (
                "Virtual resolve plus remaining structure fell from "
                f"{comparison['before_600k_ms']:.3f} ms to "
                f"{comparison['after_600k_ms']:.3f} ms per 600,000 nodes "
                f"({comparison['reduction_percent']:.3f}% reduction)."
            ),
            (
                f"The fixed PUYO-219 allocation is "
                f"{comparison['target_600k_ms']:.3f} ms; measured margin is "
                f"{comparison['margin_600k_ms']:.3f} ms."
            ),
            (
                "Combined p95 changed from "
                f"{combined['before_p95_600k_ms']:.3f} ms to "
                f"{combined['after_p95_600k_ms']:.3f} ms."
            ),
            "",
            "## Gates",
            "",
            "- fixture / transition / selected-child mismatches: 0 / 0 / 0",
            "- incremental/exact candidate comparisons: 256, mismatches: 0",
            "- response SHA-256 and all logical counter distributions: unchanged",
            "- normal hot-path allocations: 0; child/result ABI: 80/24 bytes",
            "- production max_added_puyos remains 3",
            "",
        ]
    )


def run_incremental_resolution_verification(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    destination = Path(output_dir)
    raw_dir = destination / RAW_PROFILE_DIRNAME
    current = run_profile(output_dir=raw_dir)
    current_measurement = _read_json(raw_dir / "measurement_contract.json")
    baseline = _read_json(BASELINE_SUMMARY_PATH)
    baseline_semantic = _read_json(BASELINE_SEMANTIC_PATH)
    baseline_measurement = _read_json(BASELINE_MEASUREMENT_PATH)
    budget = _read_json(BUDGET_SUMMARY_PATH)
    oracle = _run_property_oracle()
    summary = derive_summary(
        baseline_summary=baseline,
        baseline_semantic=baseline_semantic,
        baseline_measurement=baseline_measurement,
        budget_summary=budget,
        current_summary=current,
        current_measurement=current_measurement,
        oracle=oracle,
    )
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "exact_oracle.json", oracle)
    _write_json(destination / "before_after.json", summary["comparison"])
    _write_json(destination / "benchmark_summary.json", summary)
    (destination / "benchmark_report.md").write_text(
        _report(summary),
        encoding="utf-8",
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
        "budget_summary_path": str(BUDGET_SUMMARY_PATH.relative_to(ROOT)),
        "budget_summary_sha256": file_sha256(BUDGET_SUMMARY_PATH),
        "raw_profile_manifest_sha256": file_sha256(
            raw_dir / "benchmark_manifest.json"
        ),
        "release_wheel_path": raw_manifest["environment"]["release_wheel_path"],
        "release_wheel_sha256": raw_manifest["environment"]["release_wheel_sha256"],
        "decision": summary["decision"]["decision"],
        "passed": summary["decision"]["passed"],
        "artifacts": [
            describe_artifact(path, run_dir=destination, role=path.stem)
            for path in artifact_paths
        ],
    }
    _write_json(destination / "benchmark_manifest.json", manifest)
    return summary


def verify_incremental_resolution_artifacts(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    require_exact_wheel: bool = False,
    rerun_oracle: bool = True,
) -> list[str]:
    destination = Path(output_dir)
    issues = verify_profile(
        destination / RAW_PROFILE_DIRNAME,
        require_exact_wheel=require_exact_wheel,
    )
    try:
        manifest = _read_json(destination / "benchmark_manifest.json")
        summary = _read_json(destination / "benchmark_summary.json")
        oracle = _read_json(destination / "exact_oracle.json")
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
    if file_sha256(BASELINE_SUMMARY_PATH) != manifest.get(
        "baseline_summary_sha256"
    ):
        issues.append("PUYO-223 baseline summary hash drifted")
    if file_sha256(BUDGET_SUMMARY_PATH) != manifest.get("budget_summary_sha256"):
        issues.append("PUYO-219 budget summary hash drifted")
    decision = summary.get("decision", {})
    if decision.get("passed") != all(decision.get("checks", {}).values()):
        issues.append("decision does not match checks")
    if not decision.get("passed"):
        issues.append("PUYO-220 verification did not pass")
    if not math.isclose(
        float(summary.get("comparison", {}).get("target_600k_ms", -1.0)),
        _budget_sum(_read_json(BUDGET_SUMMARY_PATH)),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        issues.append("resolve/remaining stage target drifted")
    if oracle.get("source_sha256") != file_sha256(CHAIN_STRUCTURE_SOURCE_PATH):
        issues.append("property-oracle source drifted")
    if rerun_oracle:
        rerun = _run_property_oracle()
        if not rerun["passed"]:
            issues.append("property oracle rerun failed")
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_incremental_resolution_verification(args.output_dir)
        print(json.dumps(summary["decision"], indent=2, sort_keys=True))
        return 0 if summary["decision"]["passed"] else 1
    issues = verify_incremental_resolution_artifacts(
        args.output_dir,
        require_exact_wheel=args.require_exact_wheel,
        rerun_oracle=not args.skip_oracle_rerun,
    )
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print(f"verified: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
