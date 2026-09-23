"""PUYO-221 independent 600,000-node native evaluator revalidation."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import platform
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from agents.chain_structure import load_chain_structure_config
from agents.deep_chain_native import decode_capabilities
from eval.deep_chain_native_evaluator_benchmark import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_CORPUS_PATH,
    DEFAULT_FIXTURE_PATH,
    END_TO_END_P95_MAX_MS,
    NATIVE_TOTAL_P95_MAX_MS,
    REQUIRED_CHILD_STATE_BYTES,
    REQUIRED_HOT_RESULT_BYTES,
    ROOT,
    SEMANTIC_SCHEMA_VERSION,
    _command_version,
    _cpu_model,
    _read_json,
    _semantic_verification,
    _source_state,
    _source_verification,
    nearest_rank,
)
from eval.deep_chain_native_evaluator_profile import (
    COMBINED_BUDGET_MS,
    EXPANDED_NODE_COUNT,
    EXPECTED_CONFIG_SHA256,
    EXPECTED_CORPUS_DIGEST,
    EXPECTED_CORPUS_SHA256,
    PROFILE_SAMPLES,
    WARMUP_OPERATIONS,
    _combined_profile,
    _release_sources_unchanged,
)
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-221"
SUMMARY_SCHEMA_VERSION = "puyo.native_evaluator_revalidation_summary.v1"
MANIFEST_SCHEMA_VERSION = "puyo.native_evaluator_revalidation_manifest.v1"
MEASUREMENT_SCHEMA_VERSION = "puyo.native_evaluator_revalidation_measurement.v1"
PROFILE_SCHEMA_VERSION = "puyo.native_evaluator_revalidation_raw_profile.v1"
SOURCE_SCHEMA_VERSION = "puyo.native_evaluator_revalidation_source.v1"
WHEEL_SCHEMA_VERSION = "puyo.native_evaluator_revalidation_wheel.v1"

DEFAULT_OUTPUT_DIR = (
    ROOT / "docs" / "benchmarks" / "puyo-221-native-evaluator-revalidation"
)
RUN_COMMAND = [
    ".venv/bin/python",
    "-m",
    "eval.deep_chain_native_evaluator_revalidation",
    "run",
]
VERIFY_COMMAND = [
    ".venv/bin/python",
    "-m",
    "eval.deep_chain_native_evaluator_revalidation",
    "verify",
    "--require-exact-wheel",
]
BUILD_COMMAND = ["./scripts/build_deep_chain_native.sh"]
WHEEL_GLOB = "puyo_deep_chain_native-*-cp312-*-manylinux_2_28_x86_64.whl"
ARTIFACT_NAMES = (
    "measurement_contract.json",
    "semantic_verification.json",
    "source_verification.json",
    "release_wheel_verification.json",
    "raw_profile.json",
    "benchmark_summary.json",
    "benchmark_report.md",
)


def _write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _is_full_git_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _canonical_wheel() -> Path:
    wheels = sorted((ROOT / "dist" / "native").glob(WHEEL_GLOB))
    if len(wheels) != 1:
        raise RuntimeError(
            "expected exactly one canonical CPython 3.12 manylinux_2_28 wheel"
        )
    return wheels[0]


def _wheel_extension(wheel_path: Path) -> tuple[str, str, int]:
    with ZipFile(wheel_path) as archive:
        members = [name for name in archive.namelist() if name.endswith(".so")]
        if len(members) != 1:
            raise RuntimeError("canonical release wheel must contain one extension")
        payload = archive.read(members[0])
    return members[0], hashlib.sha256(payload).hexdigest(), len(payload)


def _release_wheel_verification(
    *,
    source_commit: str,
    wheel_path: Path | None = None,
) -> dict[str, Any]:
    wheel = _canonical_wheel() if wheel_path is None else wheel_path
    extension = importlib.import_module(
        "_puyo_deep_chain_native._puyo_deep_chain_native"
    )
    module_path = Path(extension.__file__)
    capabilities = decode_capabilities(bytes(extension.capabilities()))
    member, wheel_module_sha256, wheel_module_size = _wheel_extension(wheel)
    installed_module_sha256 = file_sha256(module_path)
    source_revision_matches = capabilities.source_revision == source_commit
    installed_matches_wheel = installed_module_sha256 == wheel_module_sha256
    return {
        "schema_version": WHEEL_SCHEMA_VERSION,
        "ticket": TICKET,
        "release_build": True,
        "wheel_path": _display_path(wheel),
        "wheel_sha256": file_sha256(wheel),
        "wheel_extension_member": member,
        "wheel_extension_sha256": wheel_module_sha256,
        "wheel_extension_size_bytes": wheel_module_size,
        "installed_module_path": str(module_path),
        "installed_module_sha256": installed_module_sha256,
        "installed_module_matches_wheel": installed_matches_wheel,
        "capability_source_revision": capabilities.source_revision,
        "source_revision_matches_commit": source_revision_matches,
        "passed": bool(installed_matches_wheel and source_revision_matches),
    }


def derive_decision(
    *,
    semantic: Mapping[str, Any],
    profile: Mapping[str, Any],
    source: Mapping[str, Any],
    wheel: Mapping[str, Any],
    source_tree_dirty: bool,
    corpus_sha256: str,
    corpus_digest: str,
    config_sha256: str,
) -> dict[str, Any]:
    aggregate = profile["aggregate"]
    samples = profile["samples"]
    checks = {
        "clean_source_tree": not source_tree_dirty,
        "frozen_corpus_sha256": corpus_sha256 == EXPECTED_CORPUS_SHA256,
        "frozen_corpus_digest": corpus_digest == EXPECTED_CORPUS_DIGEST,
        "frozen_config_sha256": config_sha256 == EXPECTED_CONFIG_SHA256,
        "release_wheel_provenance": wheel.get("passed") is True,
        "five_exact_600k_samples": (
            aggregate["sample_count"] == PROFILE_SAMPLES
            and len(samples) == PROFILE_SAMPLES
            and aggregate["record_count"] == 512
            and aggregate["operations_per_sample"] == EXPANDED_NODE_COUNT
            and aggregate["operations_exact"] is True
            and all(
                row["record_count"] == 512 and row["operations"] == EXPANDED_NODE_COUNT
                for row in samples
            )
        ),
        "nearest_rank_no_outlier_removal": aggregate["outlier_removal"] == "none",
        "transition_evaluator_p95": (
            aggregate["transition_evaluator_p95_ms"] <= COMBINED_BUDGET_MS
        ),
        "native_call_total_p95": (
            aggregate["native_call_total_p95_ms"] <= NATIVE_TOTAL_P95_MAX_MS
        ),
        "end_to_end_p95": (aggregate["end_to_end_p95_ms"] <= END_TO_END_P95_MAX_MS),
        "fixture_eight_zero_mismatches": (
            semantic["fixture"]["record_count"] == 8
            and semantic["fixture"]["mismatch_count"] == 0
        ),
        "transition_11264_zero_mismatches": (
            semantic["transition_oracle"]["record_count"] == 11_264
            and semantic["transition_oracle"]["mismatch_count"] == 0
        ),
        "evaluator_512_zero_mismatches": (
            semantic["python_native_evaluator"]["record_count"] == 512
            and semantic["python_native_evaluator"]["mismatch_count"] == 0
            and semantic["python_native_evaluator"]["invalid_selected_count"] == 0
        ),
        "determinism": (
            semantic["determinism"]["mismatch_count"] == 0
            and aggregate["determinism_mismatch_count"] == 0
        ),
        "normal_hot_path_allocations_zero": (
            source["normal_hot_path_heap_allocations"] == 0
        ),
        "child_state_abi_80": (
            source["child_state_bytes"] == REQUIRED_CHILD_STATE_BYTES
        ),
        "hot_result_abi_24": source["hot_result_bytes"] == REQUIRED_HOT_RESULT_BYTES,
        "production_backend_not_promoted": True,
    }
    failures = [name for name, passed in checks.items() if not passed]
    passed = not failures
    return {
        "decision": "GO" if passed else "NO_GO",
        "passed": passed,
        "checks": checks,
        "failed_checks": failures,
        "puyo_202_block_removal_candidate": passed,
        "qa_pr_may_merge": passed,
        "follow_up_required": not passed,
        "production_backend_promoted": False,
        "production_backend_promotion_requires_separate_decision": True,
    }


def _report(summary: Mapping[str, Any]) -> str:
    aggregate = summary["profile"]["aggregate"]
    semantic = summary["semantic"]
    source = summary["source_verification"]
    decision = summary["decision"]
    samples = summary["profile"]["samples"]
    lines = [
        "# PUYO-221 independent native evaluator revalidation",
        "",
        f"Decision: **{decision['decision']}**.",
        "",
        (
            "The optimized transition-plus-evaluator was independently measured "
            "against the frozen PUYO-201 source-state contract."
        ),
        "",
        "| Gate | Observed | Target | Result |",
        "| --- | ---: | ---: | --- |",
        (
            f"| exact operations per sample | "
            f"{aggregate['operations_per_sample']:,} | {EXPANDED_NODE_COUNT:,} | "
            f"{'pass' if decision['checks']['five_exact_600k_samples'] else 'fail'} |"
        ),
        (
            f"| transition + evaluator p95 | "
            f"{aggregate['transition_evaluator_p95_ms']:.3f} ms | "
            f"<= {COMBINED_BUDGET_MS:.3f} ms | "
            f"{'pass' if decision['checks']['transition_evaluator_p95'] else 'fail'} |"
        ),
        (
            f"| native call total p95 | "
            f"{aggregate['native_call_total_p95_ms']:.3f} ms | "
            f"<= {NATIVE_TOTAL_P95_MAX_MS:.3f} ms | "
            f"{'pass' if decision['checks']['native_call_total_p95'] else 'fail'} |"
        ),
        (
            f"| end-to-end p95 | {aggregate['end_to_end_p95_ms']:.3f} ms | "
            f"<= {END_TO_END_P95_MAX_MS:.3f} ms | "
            f"{'pass' if decision['checks']['end_to_end_p95'] else 'fail'} |"
        ),
        (
            f"| fixture mismatches | {semantic['fixture']['mismatch_count']} / "
            f"{semantic['fixture']['record_count']} | 0 / 8 | "
            f"{'pass' if decision['checks']['fixture_eight_zero_mismatches'] else 'fail'} |"
        ),
        (
            f"| transition oracle mismatches | "
            f"{semantic['transition_oracle']['mismatch_count']} / "
            f"{semantic['transition_oracle']['record_count']:,} | 0 / 11,264 | "
            f"{'pass' if decision['checks']['transition_11264_zero_mismatches'] else 'fail'} |"
        ),
        (
            f"| evaluator mismatches | "
            f"{semantic['python_native_evaluator']['mismatch_count']} / "
            f"{semantic['python_native_evaluator']['record_count']} | 0 / 512 | "
            f"{'pass' if decision['checks']['evaluator_512_zero_mismatches'] else 'fail'} |"
        ),
        (
            f"| normal hot-path allocations | "
            f"{source['normal_hot_path_heap_allocations']} | 0 | "
            f"{'pass' if decision['checks']['normal_hot_path_allocations_zero'] else 'fail'} |"
        ),
        (
            f"| child/result ABI | {source['child_state_bytes']}/"
            f"{source['hot_result_bytes']} bytes | 80/24 bytes | "
            f"{'pass' if decision['checks']['child_state_abi_80'] and decision['checks']['hot_result_abi_24'] else 'fail'} |"
        ),
        "",
        "## Raw samples",
        "",
        "| Sample | Operations | Transition + evaluator | Native total | End-to-end |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in samples:
        lines.append(
            f"| {row['sample']} | {row['operations']:,} | "
            f"{row['transition_evaluator_ms']:.3f} ms | "
            f"{row['native_call_total_ms']:.3f} ms | "
            f"{row['end_to_end_ms']:.3f} ms |"
        )
    lines.extend(
        [
            "",
            (
                "Nearest-rank p95 retains all five observations. No timeout value, "
                "fallback timing, or outlier removal is used."
            ),
            "",
            (
                "PUYO-202 is an unblock candidate only when every gate above passes. "
                "Promotion of the native backend remains a separate decision."
            ),
            "",
            "Re-run commands:",
            "",
            f"- `{' '.join(BUILD_COMMAND)}`",
            f"- `{' '.join(RUN_COMMAND)}`",
            f"- `{' '.join(VERIFY_COMMAND)}`",
            "",
        ]
    )
    return "\n".join(lines)


def run_revalidation(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    destination = Path(output_dir)
    source_state = _source_state(destination)
    destination.mkdir(parents=True, exist_ok=True)
    source_commit = git_commit(ROOT)
    corpus = _read_json(DEFAULT_CORPUS_PATH)
    corpus_sha256 = file_sha256(DEFAULT_CORPUS_PATH)
    config_sha256 = file_sha256(DEFAULT_CONFIG_PATH)
    config = load_chain_structure_config(DEFAULT_CONFIG_PATH)
    module = importlib.import_module("_puyo_deep_chain_native")
    wheel = _release_wheel_verification(source_commit=source_commit)
    semantic, source_inputs = _semantic_verification(
        corpus=corpus,
        fixture_path=DEFAULT_FIXTURE_PATH,
        module=module,
        ticket=TICKET,
    )
    source = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "ticket": TICKET,
        **_source_verification(module),
    }
    measured = _combined_profile(
        source_inputs=source_inputs,
        module=module,
        config=config,
    )
    profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "ticket": TICKET,
        **measured,
    }
    decision = derive_decision(
        semantic=semantic,
        profile=profile,
        source=source,
        wheel=wheel,
        source_tree_dirty=source_state["dirty"],
        corpus_sha256=corpus_sha256,
        corpus_digest=str(corpus["corpus_digest"]),
        config_sha256=config_sha256,
    )
    measurement = {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "ticket": TICKET,
        "corpus": {
            "path": _display_path(DEFAULT_CORPUS_PATH),
            "sha256": corpus_sha256,
            "corpus_digest": corpus["corpus_digest"],
            "state_count": corpus["state_count"],
            "transition_count": corpus["transition_count"],
            "selected_action_count": len(source_inputs),
        },
        "config": {
            "path": _display_path(DEFAULT_CONFIG_PATH),
            "sha256": config_sha256,
        },
        "canonical_profile": {
            "release_build": True,
            "thread_count": 1,
            "warmup_operations": WARMUP_OPERATIONS,
            "operations_per_sample": EXPANDED_NODE_COUNT,
            "sample_count": PROFILE_SAMPLES,
            "percentile": "nearest-rank sorted[ceil(p/100*N)-1]",
            "outlier_removal": "none",
            "timeout_substitution": False,
            "fallback_timing": False,
        },
        "gates": {
            "transition_evaluator_p95_ms_max": COMBINED_BUDGET_MS,
            "native_call_total_p95_ms_max": NATIVE_TOTAL_P95_MAX_MS,
            "end_to_end_p95_ms_max": END_TO_END_P95_MAX_MS,
            "fixture_mismatch_count_required": 0,
            "transition_oracle_mismatch_count_required": 0,
            "evaluator_mismatch_count_required": 0,
            "determinism_mismatch_count_required": 0,
            "normal_hot_path_heap_allocations_required": 0,
            "child_state_bytes_required": REQUIRED_CHILD_STATE_BYTES,
            "hot_result_bytes_required": REQUIRED_HOT_RESULT_BYTES,
        },
        "commands": {
            "build_release_wheel": BUILD_COMMAND,
            "run": RUN_COMMAND,
            "verify_exact_wheel": VERIFY_COMMAND,
        },
    }
    created_at = utc_timestamp()
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": created_at,
        "source_commit": source_commit,
        "semantic": semantic,
        "source_verification": source,
        "release_wheel_verification": wheel,
        "profile": profile,
        "decision": decision,
    }
    artifact_payloads = {
        "measurement_contract.json": measurement,
        "semantic_verification.json": semantic,
        "source_verification.json": source,
        "release_wheel_verification.json": wheel,
        "raw_profile.json": profile,
        "benchmark_summary.json": summary,
    }
    for name, payload in artifact_payloads.items():
        _write_json(destination / name, payload)
    (destination / "benchmark_report.md").write_text(
        _report(summary),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": created_at,
        "source_commit": source_commit,
        "source_tree_dirty_before_run": source_state["dirty"],
        "source_tracked_diff_before_run": source_state["tracked_diff"],
        "source_untracked_paths_before_run": source_state["untracked_paths"],
        "environment": {
            "platform": platform.platform(),
            "cpu": _cpu_model(),
            "python": sys.version.split()[0],
            "rustc": _command_version(["rustc", "--version"]),
            "thread_count": 1,
            "release_wheel_path": wheel["wheel_path"],
            "release_wheel_sha256": wheel["wheel_sha256"],
            "native_module_path": wheel["installed_module_path"],
            "native_module_sha256": wheel["installed_module_sha256"],
        },
        "input_digest": _digest(measurement),
        "decision": decision["decision"],
        "passed": decision["passed"],
        "artifacts": [
            describe_artifact(
                destination / name,
                run_dir=destination,
                role=name.rsplit(".", 1)[0],
            )
            for name in (*artifact_payloads, "benchmark_report.md")
        ],
    }
    _write_json(destination / "benchmark_manifest.json", manifest)
    return summary


def _profile_issues(profile: Mapping[str, Any]) -> list[str]:
    issues = []
    samples = profile.get("samples", [])
    aggregate = profile.get("aggregate", {})
    if len(samples) != PROFILE_SAMPLES:
        issues.append("raw profile does not contain five samples")
        return issues
    if [row.get("sample") for row in samples] != list(range(PROFILE_SAMPLES)):
        issues.append("raw profile sample indexes are not canonical")
    if any(row.get("operations") != EXPANDED_NODE_COUNT for row in samples):
        issues.append("raw profile operation count drifted")
    if any(row.get("record_count") != 512 for row in samples):
        issues.append("raw profile source-state count drifted")
    if aggregate.get("sample_count") != PROFILE_SAMPLES:
        issues.append("aggregate sample count drifted")
    if aggregate.get("record_count") != 512:
        issues.append("aggregate source-state count drifted")
    if aggregate.get("operations_per_sample") != EXPANDED_NODE_COUNT:
        issues.append("aggregate operation count drifted")
    if aggregate.get("operations_exact") is not True:
        issues.append("aggregate operation authority failed")
    if aggregate.get("outlier_removal") != "none":
        issues.append("outlier policy drifted")
    metrics = (
        ("transition_evaluator_ns", "transition_evaluator_p95_ms"),
        ("native_call_total_ns", "native_call_total_p95_ms"),
        ("end_to_end_ns", "end_to_end_p95_ms"),
    )
    for raw_name, aggregate_name in metrics:
        try:
            expected = (
                nearest_rank([int(row[raw_name]) for row in samples], 95) / 1_000_000.0
            )
        except (KeyError, TypeError, ValueError):
            issues.append(f"raw profile is missing valid {raw_name} values")
            continue
        if not math.isclose(
            float(aggregate.get(aggregate_name, -1.0)),
            expected,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            issues.append(f"{aggregate_name} is not nearest-rank p95")
    checksums = {row.get("checksum") for row in samples}
    if len(checksums) != 1 or aggregate.get("determinism_mismatch_count") != 0:
        issues.append("raw profile checksum determinism failed")
    return issues


def verify_revalidation(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    require_exact_wheel: bool = False,
    historical: bool = False,
) -> list[str]:
    destination = Path(output_dir)
    issues = []
    try:
        manifest = _read_json(destination / "benchmark_manifest.json")
        summary = _read_json(destination / "benchmark_summary.json")
        measurement = _read_json(destination / "measurement_contract.json")
        semantic = _read_json(destination / "semantic_verification.json")
        source = _read_json(destination / "source_verification.json")
        wheel = _read_json(destination / "release_wheel_verification.json")
        profile = _read_json(destination / "raw_profile.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"artifact read failed: {exc}"]

    expected_schemas = (
        (manifest, MANIFEST_SCHEMA_VERSION, "manifest"),
        (summary, SUMMARY_SCHEMA_VERSION, "summary"),
        (measurement, MEASUREMENT_SCHEMA_VERSION, "measurement"),
        (profile, PROFILE_SCHEMA_VERSION, "profile"),
        (source, SOURCE_SCHEMA_VERSION, "source verification"),
        (wheel, WHEEL_SCHEMA_VERSION, "wheel verification"),
    )
    for payload, schema, label in expected_schemas:
        if payload.get("schema_version") != schema:
            issues.append(f"unexpected {label} schema")
        if payload.get("ticket") != TICKET:
            issues.append(f"unexpected {label} ticket")
    if semantic.get("schema_version") != SEMANTIC_SCHEMA_VERSION:
        issues.append("unexpected semantic schema")
    if semantic.get("ticket") != TICKET:
        issues.append("unexpected semantic ticket")
    artifact_names = [
        artifact.get("path") for artifact in manifest.get("artifacts", [])
    ]
    if sorted(artifact_names) != sorted(ARTIFACT_NAMES):
        issues.append("manifest artifact set drifted")
    for artifact in manifest.get("artifacts", []):
        path = destination / artifact["path"]
        if not path.is_file():
            issues.append(f"missing artifact: {artifact['path']}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"artifact hash mismatch: {artifact['path']}")
    if manifest.get("input_digest") != _digest(measurement):
        issues.append("measurement input digest drifted")
    source_commit = manifest.get("source_commit")
    if not _is_full_git_sha(source_commit):
        issues.append("source commit is not a full Git SHA")
    if summary.get("source_commit") != source_commit:
        issues.append("summary source commit drifted")
    if manifest.get("source_tree_dirty_before_run") is not False:
        issues.append("formal measurement source tree was dirty")
    if manifest.get("source_tracked_diff_before_run") is not False:
        issues.append("formal measurement had tracked source changes")
    if manifest.get("source_untracked_paths_before_run") != []:
        issues.append("formal measurement had unrelated untracked paths")

    canonical = measurement.get("canonical_profile", {})
    if canonical != {
        "release_build": True,
        "thread_count": 1,
        "warmup_operations": WARMUP_OPERATIONS,
        "operations_per_sample": EXPANDED_NODE_COUNT,
        "sample_count": PROFILE_SAMPLES,
        "percentile": "nearest-rank sorted[ceil(p/100*N)-1]",
        "outlier_removal": "none",
        "timeout_substitution": False,
        "fallback_timing": False,
    }:
        issues.append("canonical profile contract drifted")
    if measurement.get("commands") != {
        "build_release_wheel": BUILD_COMMAND,
        "run": RUN_COMMAND,
        "verify_exact_wheel": VERIFY_COMMAND,
    }:
        issues.append("re-run commands drifted")
    if measurement.get("gates") != {
        "transition_evaluator_p95_ms_max": COMBINED_BUDGET_MS,
        "native_call_total_p95_ms_max": NATIVE_TOTAL_P95_MAX_MS,
        "end_to_end_p95_ms_max": END_TO_END_P95_MAX_MS,
        "fixture_mismatch_count_required": 0,
        "transition_oracle_mismatch_count_required": 0,
        "evaluator_mismatch_count_required": 0,
        "determinism_mismatch_count_required": 0,
        "normal_hot_path_heap_allocations_required": 0,
        "child_state_bytes_required": REQUIRED_CHILD_STATE_BYTES,
        "hot_result_bytes_required": REQUIRED_HOT_RESULT_BYTES,
    }:
        issues.append("gate contract drifted")
    if measurement.get("corpus", {}).get("sha256") != EXPECTED_CORPUS_SHA256:
        issues.append("frozen corpus SHA-256 drifted")
    if measurement.get("corpus", {}).get("corpus_digest") != EXPECTED_CORPUS_DIGEST:
        issues.append("frozen corpus digest drifted")
    if measurement.get("config", {}).get("sha256") != EXPECTED_CONFIG_SHA256:
        issues.append("frozen config SHA-256 drifted")
    issues.extend(_profile_issues(profile))
    if summary.get("semantic") != semantic:
        issues.append("summary semantic payload drifted")
    if summary.get("source_verification") != source:
        issues.append("summary source verification payload drifted")
    if summary.get("release_wheel_verification") != wheel:
        issues.append("summary wheel verification payload drifted")
    if summary.get("profile") != profile:
        issues.append("summary raw profile payload drifted")

    try:
        expected_decision = derive_decision(
            semantic=semantic,
            profile=profile,
            source=source,
            wheel=wheel,
            source_tree_dirty=bool(manifest.get("source_tree_dirty_before_run")),
            corpus_sha256=str(measurement.get("corpus", {}).get("sha256")),
            corpus_digest=str(measurement.get("corpus", {}).get("corpus_digest")),
            config_sha256=str(measurement.get("config", {}).get("sha256")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(f"independent gate derivation failed: {exc}")
    else:
        if summary.get("decision") != expected_decision:
            issues.append("summary decision does not match independent gate derivation")
        if manifest.get("decision") != expected_decision["decision"]:
            issues.append("manifest and derived decisions differ")
        if manifest.get("passed") != expected_decision["passed"]:
            issues.append("manifest and derived pass states differ")
        if not expected_decision["passed"]:
            issues.append("PUYO-221 revalidation did not pass")

    if wheel.get("passed") is not True:
        issues.append("recorded release wheel provenance did not pass")
    if not _is_sha256(wheel.get("wheel_sha256")):
        issues.append("recorded release wheel SHA-256 is invalid")
    if not _is_sha256(wheel.get("installed_module_sha256")):
        issues.append("recorded native module SHA-256 is invalid")
    if wheel.get("installed_module_matches_wheel") is not True:
        issues.append("recorded installed module did not match the release wheel")
    if wheel.get("source_revision_matches_commit") is not True:
        issues.append("recorded wheel source revision did not match the source commit")
    if wheel.get("capability_source_revision") != source_commit:
        issues.append("recorded capability source revision drifted")
    if wheel.get("wheel_extension_sha256") != wheel.get("installed_module_sha256"):
        issues.append("recorded wheel extension and installed module hashes differ")
    environment = manifest.get("environment", {})
    for manifest_name, wheel_name in (
        ("release_wheel_path", "wheel_path"),
        ("release_wheel_sha256", "wheel_sha256"),
        ("native_module_path", "installed_module_path"),
        ("native_module_sha256", "installed_module_sha256"),
    ):
        if environment.get(manifest_name) != wheel.get(wheel_name):
            issues.append(f"manifest {manifest_name} drifted")
    for name in ("platform", "cpu", "python", "rustc"):
        if not environment.get(name):
            issues.append(f"manifest environment {name} is missing")
    if environment.get("thread_count") != 1:
        issues.append("manifest thread count drifted")
    if not historical and _is_full_git_sha(source_commit):
        try:
            current_wheel = _canonical_wheel()
            current = _release_wheel_verification(
                source_commit=git_commit(ROOT),
                wheel_path=current_wheel,
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            issues.append(f"current release wheel provenance unavailable: {exc}")
        else:
            exact_wheel = current["wheel_sha256"] == wheel.get("wheel_sha256")
            if require_exact_wheel and not exact_wheel:
                issues.append("canonical release wheel hash drifted")
            elif not exact_wheel and not _release_sources_unchanged(source_commit):
                issues.append("release wheel source inputs drifted")
            if current.get("passed") is not True:
                issues.append(
                    "current installed module does not match its release wheel"
                )
    return issues


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
        if command == "verify":
            subparser.add_argument("--require-exact-wheel", action="store_true")
            subparser.add_argument("--historical", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_revalidation(args.output_dir)
        print(json.dumps(summary["decision"], indent=2, sort_keys=True))
        return 0 if summary["decision"]["passed"] else 1
    issues = verify_revalidation(
        args.output_dir,
        require_exact_wheel=args.require_exact_wheel,
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
