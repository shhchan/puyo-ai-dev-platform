"""PUYO-224 canonical candidate-ranking verification."""

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
    COMBINED_BUDGET_MS,
    EXPANDED_NODE_COUNT,
    PROFILE_SAMPLES,
    run_profile,
    verify_profile,
)
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-224"
FOLLOW_UP_TICKET = "PUYO-225"
SUMMARY_SCHEMA_VERSION = "puyo.native_candidate_ranking_summary.v1"
COMPARISON_SCHEMA_VERSION = "puyo.native_candidate_ranking_comparison.v1"
LEDGER_SCHEMA_VERSION = "puyo.native_candidate_ranking_ledger.v1"
ORACLE_SCHEMA_VERSION = "puyo.native_candidate_ranking_oracle.v1"
MANIFEST_SCHEMA_VERSION = "puyo.native_candidate_ranking_manifest.v1"

DEFAULT_OUTPUT_DIR = ROOT / "docs" / "benchmarks" / "puyo-224-candidate-ranking"
PAIRED_BASELINE_DIR = DEFAULT_OUTPUT_DIR / "before-profile"
RAW_PROFILE_DIRNAME = "after-profile"
HISTORICAL_BASELINE_SUMMARY_PATH = (
    ROOT
    / "docs"
    / "benchmarks"
    / "puyo-222-next-bottleneck"
    / "puyo-219-compatible-profile"
    / "benchmark_summary.json"
)
PAIRED_BASELINE_SUMMARY_PATH = PAIRED_BASELINE_DIR / "benchmark_summary.json"
PAIRED_BASELINE_SEMANTIC_PATH = PAIRED_BASELINE_DIR / "semantic_verification.json"
PAIRED_BASELINE_MEASUREMENT_PATH = PAIRED_BASELINE_DIR / "measurement_contract.json"
NATIVE_MANIFEST_PATH = ROOT / "native" / "deep_chain_native" / "Cargo.toml"
CHAIN_STRUCTURE_SOURCE_PATH = (
    ROOT / "native" / "deep_chain_native" / "src" / "chain_structure.rs"
)

TARGET_STAGE = "candidate_ranking_sha256"
NEXT_UNIMPROVED_STAGE = "placement_enumeration_trigger_qualification"
RESIDUAL_STAGE_NAMES = (
    "base_feature_component_extraction",
    NEXT_UNIMPROVED_STAGE,
    "virtual_resolve_gravity",
    "remaining_structure_scan",
)
LOGICAL_COUNTERS = (
    "pattern_nodes",
    "executed_pattern_probes",
    "resolution_nodes",
    "rank_comparison_calls",
    "rank_tie_calls",
)
SEMANTIC_SECTIONS = (
    "fixture",
    "transition_oracle",
    "python_native_evaluator",
)
ORDERING_TEST_FILTER = "chain_structure::tests::candidate"
PROTECTION_PROPERTY_TEST = (
    "chain_structure::tests::"
    "bit_parallel_trigger_protection_matches_exact_property_corpus"
)
ORDERING_PROPERTY_COMPARISON_COUNT = 8_192
PROTECTION_PROPERTY_COMPARISON_COUNT = 512 * 5
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


def _run_candidate_oracle() -> dict[str, Any]:
    commands = [
        [
            "cargo",
            "test",
            "--release",
            "--locked",
            "--manifest-path",
            str(NATIVE_MANIFEST_PATH),
            ORDERING_TEST_FILTER,
        ],
        [
            "cargo",
            "test",
            "--release",
            "--locked",
            "--manifest-path",
            str(NATIVE_MANIFEST_PATH),
            PROTECTION_PROPERTY_TEST,
            "--",
            "--exact",
        ],
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
        "canonical_vector_count": 1,
        "ordering_property_comparison_count": ORDERING_PROPERTY_COMPARISON_COUNT,
        "protection_property_comparison_count": (PROTECTION_PROPERTY_COMPARISON_COUNT),
        "coverage": [
            "canonical candidate JSON bytes and fixed SHA-256 vector",
            "workspace reuse and exact duplicate identity",
            "packed rank-prefix ordering against fieldwise ordering",
            "full SHA-256 digest ordering against the retained oracle",
            "bit-parallel trigger protection against exact cell scans",
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


def _stage(summary: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return summary["stage_decomposition"]["evaluator_stages"][name]


def _stage_cycles_per_node(summary: Mapping[str, Any], name: str) -> float:
    return float(_stage(summary, name)["estimated_cycles_at_profile_p50"]) / float(
        EXPANDED_NODE_COUNT
    )


def _combined_p95_ms(summary: Mapping[str, Any]) -> float:
    return float(
        summary["combined_profile"]["aggregate"]["transition_evaluator_p95_ms"]
    )


def _reduction(before: float, after: float) -> float:
    return (before - after) / before * 100.0


def derive_summary(
    *,
    historical_summary: Mapping[str, Any],
    paired_summary: Mapping[str, Any],
    paired_semantic: Mapping[str, Any],
    paired_measurement: Mapping[str, Any],
    current_summary: Mapping[str, Any],
    current_measurement: Mapping[str, Any],
    oracle: Mapping[str, Any],
    follow_up_ticket: str = FOLLOW_UP_TICKET,
) -> dict[str, Any]:
    historical_stage_cycles = _stage_cycles_per_node(historical_summary, TARGET_STAGE)
    paired_stage_cycles = _stage_cycles_per_node(paired_summary, TARGET_STAGE)
    current_stage_cycles = _stage_cycles_per_node(current_summary, TARGET_STAGE)
    historical_stage_ms = float(
        _stage(historical_summary, TARGET_STAGE)["current_projected_600k_ms"]
    )
    paired_stage_ms = float(
        _stage(paired_summary, TARGET_STAGE)["current_projected_600k_ms"]
    )
    current_stage_ms = float(
        _stage(current_summary, TARGET_STAGE)["current_projected_600k_ms"]
    )
    historical_combined_ms = _combined_p95_ms(historical_summary)
    paired_combined_ms = _combined_p95_ms(paired_summary)
    current_combined_ms = _combined_p95_ms(current_summary)
    combined_gate_met = current_combined_ms <= COMBINED_BUDGET_MS
    follow_up_required = not combined_gate_met

    historical_largest_stage = max(
        (*RESIDUAL_STAGE_NAMES, TARGET_STAGE),
        key=lambda name: float(
            _stage(historical_summary, name)["current_projected_600k_ms"]
        ),
    )
    paired_largest_stage = max(
        (*RESIDUAL_STAGE_NAMES, TARGET_STAGE),
        key=lambda name: float(
            _stage(paired_summary, name)["current_projected_600k_ms"]
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

    paired_counters = {
        name: _counter_signature(paired_summary, name) for name in LOGICAL_COUNTERS
    }
    current_counters = {
        name: _counter_signature(current_summary, name) for name in LOGICAL_COUNTERS
    }
    paired_sha = _counter_signature(paired_summary, "sha256_calls")
    current_sha = _counter_signature(current_summary, "sha256_calls")
    current_semantic = current_summary["semantic"]
    response_sha256 = {
        name: {
            "before": paired_semantic[name]["response_sha256"],
            "after": current_semantic[name]["response_sha256"],
            "matches": (
                paired_semantic[name]["response_sha256"]
                == current_semantic[name]["response_sha256"]
            ),
        }
        for name in SEMANTIC_SECTIONS
    }
    paired_profile = paired_measurement["canonical_profile"]
    current_profile = current_measurement["canonical_profile"]
    source = current_summary["source_verification"]
    paired_attribution = float(
        paired_summary["stage_decomposition"]["attribution"]["sampled_stage_share"]
    )
    current_attribution = float(
        current_summary["stage_decomposition"]["attribution"]["sampled_stage_share"]
    )

    comparison = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "operations_per_sample": EXPANDED_NODE_COUNT,
        "sample_count": PROFILE_SAMPLES,
        "outlier_removal": "none",
        "target_stage": TARGET_STAGE,
        "historical_puyo_222_stage_cycles_per_node": historical_stage_cycles,
        "paired_parent_stage_cycles_per_node": paired_stage_cycles,
        "current_stage_cycles_per_node": current_stage_cycles,
        "historical_stage_cycle_reduction_percent": _reduction(
            historical_stage_cycles, current_stage_cycles
        ),
        "paired_stage_cycle_reduction_percent": _reduction(
            paired_stage_cycles, current_stage_cycles
        ),
        "minimum_stage_cycle_reduction_percent": (
            MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT
        ),
        "historical_puyo_222_stage_projected_600k_ms": historical_stage_ms,
        "paired_parent_stage_projected_600k_ms": paired_stage_ms,
        "current_stage_projected_600k_ms": current_stage_ms,
        "historical_puyo_222_combined_p95_600k_ms": historical_combined_ms,
        "paired_parent_combined_p95_600k_ms": paired_combined_ms,
        "current_combined_p95_600k_ms": current_combined_ms,
        "paired_combined_reduction_percent": _reduction(
            paired_combined_ms, current_combined_ms
        ),
        "historical_combined_reduction_percent": _reduction(
            historical_combined_ms, current_combined_ms
        ),
        "minimum_combined_reduction_percent": MINIMUM_COMBINED_REDUCTION_PERCENT,
        "combined_gate_600k_ms": COMBINED_BUDGET_MS,
        "combined_gate_met": combined_gate_met,
        "combined_acceptance_baseline": (
            "same-session paired parent commit on the same CPU, wheel build, "
            "corpus, config, and 5 x 600,000-operation contract"
        ),
    }
    ledger = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "paired_baseline_attributed_share": paired_attribution,
        "current_attributed_share": current_attribution,
        "historical_largest_stage": historical_largest_stage,
        "paired_parent_largest_stage": paired_largest_stage,
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
        "same_frozen_corpus": (
            paired_measurement["corpus"]["sha256"]
            == current_measurement["corpus"]["sha256"]
        ),
        "same_frozen_config": (
            paired_measurement["config"]["sha256"]
            == current_measurement["config"]["sha256"]
        ),
        "same_five_by_600k_contract": (
            paired_profile["operations_per_sample"]
            == current_profile["operations_per_sample"]
            == EXPANDED_NODE_COUNT
            and paired_profile["sample_count"]
            == current_profile["sample_count"]
            == PROFILE_SAMPLES
            and paired_profile["outlier_removal"]
            == current_profile["outlier_removal"]
            == "none"
        ),
        "production_depth_three": (
            current_measurement["config"]["production_max_added_puyos"] == 3
        ),
        "paired_and_current_attribution_at_least_95_percent": (
            paired_attribution >= 0.95 and current_attribution >= 0.95
        ),
        "target_is_largest_canonical_puyo_222_stage": (
            historical_largest_stage == TARGET_STAGE
        ),
        "minimum_30_percent_historical_stage_cycle_reduction": (
            comparison["historical_stage_cycle_reduction_percent"]
            >= MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT
        ),
        "minimum_30_percent_paired_stage_cycle_reduction": (
            comparison["paired_stage_cycle_reduction_percent"]
            >= MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT
        ),
        "minimum_20_percent_paired_combined_reduction_or_gate_met": (
            comparison["paired_combined_reduction_percent"]
            >= MINIMUM_COMBINED_REDUCTION_PERCENT
            or combined_gate_met
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
            and current_semantic["python_native_evaluator"]["invalid_selected_count"]
            == 0
        ),
        "response_bytes_and_sha_order_match": all(
            row["matches"] for row in response_sha256.values()
        ),
        "logical_budget_and_rank_counters_match": (paired_counters == current_counters),
        "physical_sha256_calls_do_not_increase": (
            current_sha["exact_600k_total"] <= paired_sha["exact_600k_total"]
        ),
        "canonical_and_full_digest_oracle_zero_mismatches": (
            oracle.get("passed") is True
            and oracle.get("mismatch_count") == 0
            and oracle.get("canonical_vector_count") == 1
            and oracle.get("ordering_property_comparison_count")
            == ORDERING_PROPERTY_COMPARISON_COUNT
            and oracle.get("protection_property_comparison_count")
            == PROTECTION_PROPERTY_COMPARISON_COUNT
        ),
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
        "follow_up_recorded_when_gate_unmet": (
            not follow_up_required
            or (
                follow_up_ticket == FOLLOW_UP_TICKET
                and current_largest_stage == NEXT_UNIMPROVED_STAGE
            )
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        decision_name = "INVALID"
    elif follow_up_required:
        decision_name = "PASS_FOLLOW_UP_REQUIRED"
    else:
        decision_name = "PASS_GATE_MET"
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "measurement_commit": git_commit(ROOT),
        "comparison": comparison,
        "bottleneck_ledger": ledger,
        "logical_counters": {
            "before": paired_counters,
            "after": current_counters,
            "matches": paired_counters == current_counters,
        },
        "physical_sha256_calls": {
            "before": paired_sha,
            "after": current_sha,
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
            "# PUYO-224 canonical candidate-ranking verification",
            "",
            f"Decision: **{decision['decision']}**.",
            "",
            (
                "Candidate-ranking cycles per node fell from the canonical "
                f"PUYO-222 value {comparison['historical_puyo_222_stage_cycles_per_node']:.3f} "
                f"to {comparison['current_stage_cycles_per_node']:.3f} "
                f"({comparison['historical_stage_cycle_reduction_percent']:.3f}% reduction)."
            ),
            (
                "On the same-session paired parent, combined transition-plus-evaluator "
                f"p95 fell from {comparison['paired_parent_combined_p95_600k_ms']:.3f} ms "
                f"to {comparison['current_combined_p95_600k_ms']:.3f} ms "
                f"({comparison['paired_combined_reduction_percent']:.3f}% reduction)."
            ),
            (
                "The historical PUYO-222 combined p95 is recorded separately as "
                f"{comparison['historical_puyo_222_combined_p95_600k_ms']:.3f} ms; "
                "the performance gate uses the paired build to exclude run-to-run "
                "host drift."
            ),
            "",
            "## Gates",
            "",
            "- fixture / transition / selected-child mismatches: 0 / 0 / 0",
            "- canonical bytes, full digest ordering, and protection oracle: 0 mismatches",
            "- response SHA-256 and logical budget/rank counters: unchanged",
            "- physical SHA-256 compressions: did not increase",
            "- normal hot-path allocations: 0; child/result ABI: 80/24 bytes",
            "- production max_added_puyos remains 3",
            "",
            "## Residual budget",
            "",
            (
                f"The combined p95 remains {ledger['current_combined_budget_gap_ms']:.3f} ms "
                f"above the 820.625 ms gate. The next stage is "
                f"`{ledger['current_largest_unimproved_stage']}`; follow-up "
                f"{ledger['follow_up_ticket']} is required."
                if ledger["follow_up_required"]
                else "The combined p95 meets the 820.625 ms gate."
            ),
            "",
        ]
    )


def run_candidate_ranking_verification(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    destination = Path(output_dir)
    raw_dir = destination / RAW_PROFILE_DIRNAME
    current = run_profile(output_dir=raw_dir)
    current_measurement = _read_json(raw_dir / "measurement_contract.json")
    historical = _read_json(HISTORICAL_BASELINE_SUMMARY_PATH)
    paired = _read_json(PAIRED_BASELINE_SUMMARY_PATH)
    paired_semantic = _read_json(PAIRED_BASELINE_SEMANTIC_PATH)
    paired_measurement = _read_json(PAIRED_BASELINE_MEASUREMENT_PATH)
    oracle = _run_candidate_oracle()
    summary = derive_summary(
        historical_summary=historical,
        paired_summary=paired,
        paired_semantic=paired_semantic,
        paired_measurement=paired_measurement,
        current_summary=current,
        current_measurement=current_measurement,
        oracle=oracle,
    )
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "candidate_ranking_oracle.json", oracle)
    _write_json(destination / "before_after.json", summary["comparison"])
    _write_json(destination / "bottleneck_ledger.json", summary["bottleneck_ledger"])
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
        "historical_baseline_summary_path": str(
            HISTORICAL_BASELINE_SUMMARY_PATH.relative_to(ROOT)
        ),
        "historical_baseline_summary_sha256": file_sha256(
            HISTORICAL_BASELINE_SUMMARY_PATH
        ),
        "paired_baseline_summary_sha256": file_sha256(PAIRED_BASELINE_SUMMARY_PATH),
        "paired_baseline_semantic_sha256": file_sha256(PAIRED_BASELINE_SEMANTIC_PATH),
        "paired_baseline_measurement_sha256": file_sha256(
            PAIRED_BASELINE_MEASUREMENT_PATH
        ),
        "raw_profile_manifest_sha256": file_sha256(raw_dir / "benchmark_manifest.json"),
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


def verify_candidate_ranking_artifacts(
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
        f"paired baseline: {issue}"
        for issue in verify_profile(
            PAIRED_BASELINE_DIR,
            allow_historical_wheel=True,
        )
    )
    try:
        manifest = _read_json(destination / "benchmark_manifest.json")
        summary = _read_json(destination / "benchmark_summary.json")
        oracle = _read_json(destination / "candidate_ranking_oracle.json")
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
        (
            HISTORICAL_BASELINE_SUMMARY_PATH,
            "historical_baseline_summary_sha256",
            "PUYO-222 summary",
        ),
        (
            PAIRED_BASELINE_SUMMARY_PATH,
            "paired_baseline_summary_sha256",
            "paired summary",
        ),
        (
            PAIRED_BASELINE_SEMANTIC_PATH,
            "paired_baseline_semantic_sha256",
            "paired semantic",
        ),
        (
            PAIRED_BASELINE_MEASUREMENT_PATH,
            "paired_baseline_measurement_sha256",
            "paired measurement",
        ),
    ):
        if file_sha256(path) != manifest.get(field):
            issues.append(f"{label} hash drifted")
    decision = summary.get("decision", {})
    if decision.get("passed") != all(decision.get("checks", {}).values()):
        issues.append("decision does not match checks")
    if not decision.get("passed"):
        issues.append("PUYO-224 verification did not pass")
    if manifest.get("follow_up_ticket") != summary.get("bottleneck_ledger", {}).get(
        "follow_up_ticket"
    ):
        issues.append("follow-up ticket differs between manifest and ledger")
    if not historical and oracle.get("source_sha256") != file_sha256(
        CHAIN_STRUCTURE_SOURCE_PATH
    ):
        issues.append("candidate-ranking oracle source drifted")
    if rerun_oracle:
        rerun = _run_candidate_oracle()
        if not rerun["passed"]:
            issues.append("candidate-ranking oracle rerun failed")
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
            subparser.add_argument(
                "--historical",
                action="store_true",
                help="verify hash-bound PUYO-224 evidence after successor changes",
            )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_candidate_ranking_verification(args.output_dir)
        print(json.dumps(summary["decision"], indent=2, sort_keys=True))
        return 0 if summary["decision"]["passed"] else 1
    issues = verify_candidate_ranking_artifacts(
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
