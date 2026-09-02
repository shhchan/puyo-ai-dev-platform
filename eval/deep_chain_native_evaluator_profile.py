"""PUYO-219 source-bound native evaluator stage profile and budget ledger."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents.chain_structure import ChainStructureConfig, load_chain_structure_config
from agents.deep_chain_native import decode_capabilities
from agents.deep_chain_native_evaluator import (
    NATIVE_CHAIN_STRUCTURE_PROFILE_COUNTER_NAMES,
    NATIVE_CHAIN_STRUCTURE_PROFILE_STAGE_NAMES,
    NativeChainStructureBatchClient,
    NativeChainStructureInput,
    decode_native_combined_profile,
    encode_native_chain_structure_batch,
)
from eval.deep_chain_native_evaluator_benchmark import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_CORPUS_PATH,
    DEFAULT_FIXTURE_PATH,
    REQUIRED_CHILD_STATE_BYTES,
    REQUIRED_HOT_RESULT_BYTES,
    ROOT,
    _command_version,
    _cpu_model,
    _read_json,
    _semantic_verification,
    _source_state,
    _source_verification,
    nearest_rank,
)
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-219"
SOURCE_TICKET = "PUYO-201"
SOURCE_IMPLEMENTATION_COMMIT = "c66d3ab054626b4015c203c790d136deb9b44a26"
EXPECTED_CORPUS_SHA256 = (
    "ff144ddb38f4776458c4a4dae7bc631cc3f4bede16e556eb00d410d41c5c6356"
)
EXPECTED_CORPUS_DIGEST = (
    "7132ac24b92c275560513f15e2a827fa491df89f6aa70770181ecfeac27d0eb2"
)
EXPECTED_CONFIG_SHA256 = (
    "549a4a18c8f17bea79545704541472951f51de3ffeff28f4d1a38ab26e938da7"
)

SUMMARY_SCHEMA_VERSION = "puyo.native_chain_structure_hot_path_profile.v1"
MANIFEST_SCHEMA_VERSION = "puyo.native_chain_structure_hot_path_manifest.v1"
MEASUREMENT_SCHEMA_VERSION = "puyo.native_chain_structure_hot_path_measurement.v1"
RAW_PROFILE_SCHEMA_VERSION = "puyo.native_chain_structure_hot_path_raw.v1"
STAGE_PROFILE_SCHEMA_VERSION = "puyo.native_chain_structure_stage_analysis.v1"
CALL_COUNT_SCHEMA_VERSION = "puyo.native_chain_structure_call_counts.v1"
ABLATION_SCHEMA_VERSION = "puyo.native_chain_structure_depth_ablation.v1"
BUDGET_SCHEMA_VERSION = "puyo.native_chain_structure_stage_budget.v1"

DEFAULT_OUTPUT_DIR = ROOT / "docs" / "benchmarks" / "puyo-219-evaluator-hot-path"

EXPANDED_NODE_COUNT = 600_000
PROFILE_SAMPLES = 5
WARMUP_OPERATIONS = 10_000
SAMPLE_INTERVAL_US = 100
COMBINED_BUDGET_MS = 820.625
TRANSITION_PROJECTION_MS = 46.271520
EVALUATOR_BUDGET_MS = COMBINED_BUDGET_MS - TRANSITION_PROJECTION_MS
MIN_ATTRIBUTED_SHARE = 0.95

EVALUATOR_STAGE_NAMES = (
    "base_feature_component_extraction",
    "placement_enumeration_trigger_qualification",
    "virtual_resolve_gravity",
    "remaining_structure_scan",
    "candidate_ranking_sha256",
)
PLACEMENT_SUBSTAGE_NAMES = (
    "placement_orbit_enumeration",
    "placement_frontier_update",
    "placement_trigger_qualification",
    "placement_deduplication",
    "placement_candidate_dispatch",
    "placement_single_component_frontier",
    "placement_multi_component_frontier",
)
EVALUATOR_LEAF_STAGE_NAMES = (
    "base_feature_component_extraction",
    *PLACEMENT_SUBSTAGE_NAMES,
    "virtual_resolve_gravity",
    "remaining_structure_scan",
    "candidate_ranking_sha256",
)
COUNT_NAMES = NATIVE_CHAIN_STRUCTURE_PROFILE_COUNTER_NAMES
RELEASE_BUILD_INPUT_PATHS = (
    "native/deep_chain_native",
    "agents/chain_structure.py",
    "agents/deep_chain_native.py",
    "agents/deep_chain_native_evaluator.py",
    "agents/deep_chain_native_transition.py",
    "requirements-native.txt",
    "rust-toolchain.toml",
    "scripts/build_deep_chain_native.sh",
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


def _source_commit_is_ancestor() -> bool:
    return (
        subprocess.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                SOURCE_IMPLEMENTATION_COMMIT,
                "HEAD",
            ],
            cwd=ROOT,
            check=False,
        ).returncode
        == 0
    )


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


def _release_sources_unchanged(measurement_commit: Any) -> bool:
    if not _is_full_git_sha(measurement_commit):
        return False
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", measurement_commit, "HEAD"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if ancestor.returncode != 0:
        return False
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            measurement_commit,
            "HEAD",
            "--",
            *RELEASE_BUILD_INPUT_PATHS,
        ],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return diff.returncode == 0


def _combined_profile(
    *,
    source_inputs: Sequence[NativeChainStructureInput],
    module: Any,
    config: ChainStructureConfig,
    warmup: bool = True,
) -> dict[str, Any]:
    warmup_request = encode_native_chain_structure_batch(
        source_inputs,
        config,
        include_evidence=False,
    )
    if warmup:
        module._chain_structure_combined_profile(
            warmup_request,
            WARMUP_OPERATIONS,
        )

    samples = []
    for sample_index in range(PROFILE_SAMPLES):
        end_to_end_started = time.perf_counter_ns()
        request = encode_native_chain_structure_batch(
            source_inputs,
            config,
            include_evidence=False,
        )
        native_started = time.perf_counter_ns()
        response = module._chain_structure_combined_profile(
            request,
            EXPANDED_NODE_COUNT,
        )
        native_call_ns = time.perf_counter_ns() - native_started
        measurement = decode_native_combined_profile(response)
        end_to_end_ns = time.perf_counter_ns() - end_to_end_started
        samples.append(
            {
                "sample": sample_index,
                "operations": measurement.operations,
                "record_count": measurement.record_count,
                "transition_evaluator_ns": measurement.elapsed_ns,
                "transition_evaluator_ms": measurement.elapsed_ms,
                "native_call_total_ns": native_call_ns,
                "native_call_total_ms": native_call_ns / 1_000_000.0,
                "end_to_end_ns": end_to_end_ns,
                "end_to_end_ms": end_to_end_ns / 1_000_000.0,
                "ns_per_operation": measurement.ns_per_operation,
                "checksum": measurement.checksum,
            }
        )

    combined = [row["transition_evaluator_ns"] for row in samples]
    native_total = [row["native_call_total_ns"] for row in samples]
    end_to_end = [row["end_to_end_ns"] for row in samples]
    checksums = {row["checksum"] for row in samples}
    return {
        "samples": samples,
        "aggregate": {
            "sample_count": len(samples),
            "record_count": len(source_inputs),
            "operations_per_sample": EXPANDED_NODE_COUNT,
            "operations_exact": all(
                row["operations"] == EXPANDED_NODE_COUNT for row in samples
            ),
            "outlier_removal": "none",
            "transition_evaluator_p50_ms": nearest_rank(combined, 50) / 1_000_000.0,
            "transition_evaluator_p95_ms": nearest_rank(combined, 95) / 1_000_000.0,
            "native_call_total_p50_ms": nearest_rank(native_total, 50) / 1_000_000.0,
            "native_call_total_p95_ms": nearest_rank(native_total, 95) / 1_000_000.0,
            "end_to_end_p50_ms": nearest_rank(end_to_end, 50) / 1_000_000.0,
            "end_to_end_p95_ms": nearest_rank(end_to_end, 95) / 1_000_000.0,
            "determinism_mismatch_count": max(0, len(checksums) - 1),
            "checksum": samples[0]["checksum"],
        },
    }


def _stage_profile(
    *,
    source_inputs: Sequence[NativeChainStructureInput],
    module: Any,
    config: ChainStructureConfig,
) -> tuple[dict[str, Any], tuple[dict[str, int], ...]]:
    client = NativeChainStructureBatchClient(module)
    client.stage_profile(
        source_inputs,
        config,
        operations=WARMUP_OPERATIONS,
        sample_interval_us=SAMPLE_INTERVAL_US,
    )
    samples = []
    record_counts: tuple[dict[str, int], ...] | None = None
    aggregate_counts: dict[str, int] | None = None
    for sample_index in range(PROFILE_SAMPLES):
        result = client.stage_profile(
            source_inputs,
            config,
            operations=EXPANDED_NODE_COUNT,
            sample_interval_us=SAMPLE_INTERVAL_US,
        )
        current_record_counts = tuple(row.to_dict() for row in result.record_counts)
        current_aggregate_counts = result.aggregate_counts.to_dict()
        if record_counts is None:
            record_counts = current_record_counts
            aggregate_counts = current_aggregate_counts
        elif (
            current_record_counts != record_counts
            or current_aggregate_counts != aggregate_counts
        ):
            raise AssertionError("stage-profile exact counters are not deterministic")
        samples.append(
            {
                "sample": sample_index,
                "operations": result.operations,
                "record_count": result.record_count,
                "elapsed_ns": result.elapsed_ns,
                "elapsed_ms": result.elapsed_ms,
                "ns_per_operation": result.ns_per_operation,
                "cycles": result.cycles,
                "cycles_per_operation": result.cycles / result.operations,
                "checksum": result.checksum,
                "sample_interval_us": result.sample_interval_us,
                "sample_count": result.sample_count,
                "mismatch_count": result.mismatch_count,
                "cycle_counter_available": result.cycle_counter_available,
                "sampler_available": result.sampler_available,
                "aggregate_counts": current_aggregate_counts,
                "stage_samples": result.stage_samples,
                "stage_entries": result.stage_entries,
            }
        )
    assert record_counts is not None and aggregate_counts is not None
    stage_sample_totals = {
        name: sum(int(row["stage_samples"][name]) for row in samples)
        for name in NATIVE_CHAIN_STRUCTURE_PROFILE_STAGE_NAMES
    }
    total_samples = sum(stage_sample_totals.values())
    elapsed = [int(row["elapsed_ns"]) for row in samples]
    cycles = [int(row["cycles"]) for row in samples]
    return (
        {
            "schema_version": STAGE_PROFILE_SCHEMA_VERSION,
            "ticket": TICKET,
            "method": (
                "QA-only 100us user-space interval sampler over explicit native "
                "stage markers, with exact inner-loop call counters"
            ),
            "samples": samples,
            "aggregate": {
                "profile_sample_count": len(samples),
                "operations_per_sample": EXPANDED_NODE_COUNT,
                "operations_exact": all(
                    row["operations"] == EXPANDED_NODE_COUNT for row in samples
                ),
                "outlier_removal": "none",
                "elapsed_p50_ms": nearest_rank(elapsed, 50) / 1_000_000.0,
                "elapsed_p95_ms": nearest_rank(elapsed, 95) / 1_000_000.0,
                "cycles_p50": nearest_rank(cycles, 50),
                "cycles_p95": nearest_rank(cycles, 95),
                "determinism_mismatch_count": max(
                    0,
                    len({int(row["checksum"]) for row in samples}) - 1,
                ),
                "profiled_result_mismatch_count": max(
                    int(row["mismatch_count"]) for row in samples
                ),
                "sample_interval_us": SAMPLE_INTERVAL_US,
                "sampler_sample_count": total_samples,
                "stage_sample_totals": stage_sample_totals,
                "stage_sample_shares": {
                    name: count / total_samples
                    for name, count in stage_sample_totals.items()
                },
                "aggregate_counts_per_sample": aggregate_counts,
                "stage_entries_per_sample": samples[0]["stage_entries"],
                "cycle_counter_available": all(
                    bool(row["cycle_counter_available"]) for row in samples
                ),
                "sampler_available": all(
                    bool(row["sampler_available"]) for row in samples
                ),
            },
        },
        record_counts,
    )


def summarize_call_counts(
    record_counts: Sequence[Mapping[str, int]],
    aggregate_counts: Mapping[str, int],
) -> dict[str, Any]:
    if not record_counts:
        raise ValueError("call-count corpus is empty")
    distribution = {}
    for name in COUNT_NAMES:
        values = [int(row[name]) for row in record_counts]
        distribution[name] = {
            "p50_per_node": int(nearest_rank(values, 50)),
            "p95_per_node": int(nearest_rank(values, 95)),
            "maximum_per_node": max(values),
            "mean_per_node": sum(values) / len(values),
            "exact_600k_total": int(aggregate_counts[name]),
        }
    return {
        "schema_version": CALL_COUNT_SCHEMA_VERSION,
        "ticket": TICKET,
        "source_state_count": len(record_counts),
        "operations": EXPANDED_NODE_COUNT,
        "distribution": distribution,
        "per_source_state": [dict(row) for row in record_counts],
    }


def derive_stage_decomposition(
    combined_profile: Mapping[str, Any],
    stage_profile: Mapping[str, Any],
) -> dict[str, Any]:
    combined = combined_profile["aggregate"]
    stage = stage_profile["aggregate"]
    shares = stage["stage_sample_shares"]
    attributed_share = 1.0 - float(shares["driver_unattributed"])
    evaluator_samples = sum(float(shares[name]) for name in EVALUATOR_LEAF_STAGE_NAMES)
    if evaluator_samples <= 0.0:
        raise ValueError("stage sampler did not observe evaluator work")
    observed_combined_ms = float(combined["transition_evaluator_p95_ms"])
    observed_evaluator_ms = max(
        0.0,
        observed_combined_ms - TRANSITION_PROJECTION_MS,
    )
    median_cycles = float(stage["cycles_p50"])
    evaluator_stages = {}
    for name in EVALUATOR_LEAF_STAGE_NAMES:
        combined_cycle_share = float(shares[name])
        evaluator_share = combined_cycle_share / evaluator_samples
        projected_ms = observed_evaluator_ms * evaluator_share
        evaluator_stages[name] = {
            "sample_count": int(stage["stage_sample_totals"][name]),
            "cycle_ratio_of_profiled_loop": combined_cycle_share,
            "cycle_ratio_of_evaluator": evaluator_share,
            "estimated_cycles_at_profile_p50": median_cycles * combined_cycle_share,
            "current_projected_600k_ms": projected_ms,
            "current_ns_per_node": projected_ms * 1_000_000.0 / EXPANDED_NODE_COUNT,
            "stage_entries_per_node": (
                int(stage["stage_entries_per_sample"][name]) / EXPANDED_NODE_COUNT
            ),
        }
    placement_sample_count = sum(
        int(stage["stage_sample_totals"][name]) for name in PLACEMENT_SUBSTAGE_NAMES
    )
    placement_cycle_share = sum(float(shares[name]) for name in PLACEMENT_SUBSTAGE_NAMES)
    placement_evaluator_share = placement_cycle_share / evaluator_samples
    placement_projected_ms = observed_evaluator_ms * placement_evaluator_share
    evaluator_stages["placement_enumeration_trigger_qualification"] = {
        "sample_count": placement_sample_count,
        "cycle_ratio_of_profiled_loop": placement_cycle_share,
        "cycle_ratio_of_evaluator": placement_evaluator_share,
        "estimated_cycles_at_profile_p50": median_cycles * placement_cycle_share,
        "current_projected_600k_ms": placement_projected_ms,
        "current_ns_per_node": (
            placement_projected_ms * 1_000_000.0 / EXPANDED_NODE_COUNT
        ),
        "stage_entries_per_node": (
            sum(
                int(stage["stage_entries_per_sample"][name])
                for name in PLACEMENT_SUBSTAGE_NAMES
            )
            / EXPANDED_NODE_COUNT
        ),
        "substage_names": list(PLACEMENT_SUBSTAGE_NAMES),
    }
    native_boundary_ms = max(
        0.0,
        float(combined["native_call_total_p95_ms"]) - observed_combined_ms,
    )
    encode_decode_ms = max(
        0.0,
        float(combined["end_to_end_p95_ms"])
        - float(combined["native_call_total_p95_ms"]),
    )
    return {
        "attribution": {
            "sampled_stage_share": attributed_share,
            "required_minimum": MIN_ATTRIBUTED_SHARE,
            "passes": attributed_share >= MIN_ATTRIBUTED_SHARE,
            "driver_unattributed_share": float(shares["driver_unattributed"]),
        },
        "combined": {
            "observed_p95_ms": observed_combined_ms,
            "observed_ns_per_node": observed_combined_ms
            * 1_000_000.0
            / EXPANDED_NODE_COUNT,
        },
        "transition": {
            "fixed_projection_600k_ms": TRANSITION_PROJECTION_MS,
            "fixed_projection_ns_per_node": TRANSITION_PROJECTION_MS
            * 1_000_000.0
            / EXPANDED_NODE_COUNT,
            "sampled_cycle_ratio_of_profiled_loop": float(shares["transition"]),
            "estimated_cycles_at_profile_p50": median_cycles
            * float(shares["transition"]),
        },
        "evaluator": {
            "observed_projected_600k_ms": observed_evaluator_ms,
            "observed_ns_per_node": observed_evaluator_ms
            * 1_000_000.0
            / EXPANDED_NODE_COUNT,
            "sampled_cycle_ratio_of_profiled_loop": evaluator_samples,
        },
        "evaluator_stages": evaluator_stages,
        "outer_overhead": {
            "native_call_parse_prepare_response_600k_ms": native_boundary_ms,
            "native_call_parse_prepare_response_ns_per_node": native_boundary_ms
            * 1_000_000.0
            / EXPANDED_NODE_COUNT,
            "python_encode_decode_600k_ms": encode_decode_ms,
            "python_encode_decode_ns_per_node": encode_decode_ms
            * 1_000_000.0
            / EXPANDED_NODE_COUNT,
        },
    }


def _amdahl_entry(
    current_total_ms: float, target_total_ms: float, stage_ms: float
) -> dict[str, Any]:
    remaining = max(0.0, current_total_ms - stage_ms)
    maximum_speedup = None if remaining == 0.0 else current_total_ms / remaining
    possible = remaining < target_total_ms
    required = stage_ms / (target_total_ms - remaining) if possible else None
    return {
        "current_stage_600k_ms": stage_ms,
        "remaining_if_stage_free_600k_ms": remaining,
        "maximum_evaluator_speedup_if_stage_free": maximum_speedup,
        "gate_possible_if_only_stage_changes": possible,
        "required_stage_speedup_if_only_stage_changes": required,
    }


def derive_stage_budget(decomposition: Mapping[str, Any]) -> dict[str, Any]:
    current_total = float(decomposition["evaluator"]["observed_projected_600k_ms"])
    stages = decomposition["evaluator_stages"]
    current_sum = sum(
        float(stages[name]["current_projected_600k_ms"])
        for name in EVALUATOR_STAGE_NAMES
    )
    ledger = {}
    for name in EVALUATOR_STAGE_NAMES:
        current = float(stages[name]["current_projected_600k_ms"])
        share = 0.0 if current_sum == 0.0 else current / current_sum
        target = EVALUATOR_BUDGET_MS * share
        ledger[name] = {
            "current_projected_600k_ms": current,
            "current_ns_per_node": current * 1_000_000.0 / EXPANDED_NODE_COUNT,
            "target_budget_600k_ms": target,
            "target_budget_ns_per_node": target * 1_000_000.0 / EXPANDED_NODE_COUNT,
            "required_speedup_to_proportional_budget": (
                None if target == 0.0 else current / target
            ),
            "amdahl": _amdahl_entry(current_total, EVALUATOR_BUDGET_MS, current),
        }

    candidates = [
        {
            "ticket": "PUYO-223",
            "stages": ["placement_enumeration_trigger_qualification"],
        },
        {
            "ticket": "PUYO-220",
            "stages": ["virtual_resolve_gravity", "remaining_structure_scan"],
        },
    ]
    for candidate in candidates:
        contribution = sum(
            float(stages[name]["current_projected_600k_ms"])
            for name in candidate["stages"]
        )
        candidate["current_projected_600k_ms"] = contribution
        candidate["current_evaluator_cycle_share"] = sum(
            float(stages[name]["cycle_ratio_of_evaluator"])
            for name in candidate["stages"]
        )
        candidate["amdahl"] = _amdahl_entry(
            current_total,
            EVALUATOR_BUDGET_MS,
            contribution,
        )
    candidates.sort(
        key=lambda row: float(row["current_projected_600k_ms"]),
        reverse=True,
    )
    return {
        "schema_version": BUDGET_SCHEMA_VERSION,
        "ticket": TICKET,
        "combined_budget_600k_ms": COMBINED_BUDGET_MS,
        "transition_budget_600k_ms": TRANSITION_PROJECTION_MS,
        "evaluator_budget_600k_ms": EVALUATOR_BUDGET_MS,
        "evaluator_budget_ns_per_node": EVALUATOR_BUDGET_MS
        * 1_000_000.0
        / EXPANDED_NODE_COUNT,
        "allocation_method": (
            "proportional to the source-bound evaluator cycle share; the ledger "
            "is a diagnostic allocation and does not change production semantics"
        ),
        "stage_budget_ledger": ledger,
        "stage_budget_sum_600k_ms": sum(
            float(row["target_budget_600k_ms"]) for row in ledger.values()
        ),
        "implementation_priority": candidates,
        "follow_up_order": [row["ticket"] for row in candidates]
        + ["PUYO-222", "PUYO-221"],
        "stop_conditions": [
            "stop on any fixture, transition-oracle, evaluator, or determinism mismatch",
            "stop on any hot-path allocation or 80/24-byte ABI regression",
            "after the first implementation candidate, rerun PUYO-222 profiling before the next optimization",
            "do not start the independent PUYO-221 gate until the combined 600,000-node p95 is at or below 820.625 ms",
        ],
    }


def _depth_ablation(
    *,
    source_inputs: Sequence[NativeChainStructureInput],
    module: Any,
    production_config: ChainStructureConfig,
    depth_three_profile: Mapping[str, Any],
) -> dict[str, Any]:
    variants: dict[str, Any] = {}
    for depth in (1, 2):
        config = replace(
            production_config,
            budget=replace(production_config.budget, max_added_puyos=depth),
        )
        variants[str(depth)] = {
            "max_added_puyos": depth,
            "config_digest": _digest(config.to_dict()),
            "profile": _combined_profile(
                source_inputs=source_inputs,
                module=module,
                config=config,
            ),
        }
    variants["3"] = {
        "max_added_puyos": 3,
        "config_digest": _digest(production_config.to_dict()),
        "profile": depth_three_profile,
    }
    medians = {
        depth: float(value["profile"]["aggregate"]["transition_evaluator_p50_ms"])
        * 1_000_000.0
        / EXPANDED_NODE_COUNT
        for depth, value in variants.items()
    }
    increment = medians["3"] - medians["2"]
    return {
        "schema_version": ABLATION_SCHEMA_VERSION,
        "ticket": TICKET,
        "diagnostic_only": True,
        "production_semantics_changed": False,
        "production_max_added_puyos": production_config.budget.max_added_puyos,
        "operations_per_sample": EXPANDED_NODE_COUNT,
        "sample_count": PROFILE_SAMPLES,
        "outlier_removal": "none",
        "variants": variants,
        "median_ns_per_node": medians,
        "depth_2_to_3_increment_ns_per_node": increment,
        "depth_2_to_3_share_of_depth_3": (
            0.0 if medians["3"] == 0.0 else increment / medians["3"]
        ),
    }


def derive_profile_decision(
    *,
    semantic: Mapping[str, Any],
    source: Mapping[str, Any],
    combined: Mapping[str, Any],
    stage: Mapping[str, Any],
    decomposition: Mapping[str, Any],
    budget: Mapping[str, Any],
    ablation: Mapping[str, Any],
    source_commit_is_ancestor: bool,
    corpus_sha256: str,
    corpus_digest: str,
    config_sha256: str,
) -> dict[str, Any]:
    checks = {
        "source_implementation_commit": source_commit_is_ancestor,
        "frozen_corpus_sha256": corpus_sha256 == EXPECTED_CORPUS_SHA256,
        "frozen_corpus_digest": corpus_digest == EXPECTED_CORPUS_DIGEST,
        "frozen_config_sha256": config_sha256 == EXPECTED_CONFIG_SHA256,
        "exact_600k_combined_operations": bool(
            combined["aggregate"]["operations_exact"]
        ),
        "exact_600k_stage_operations": bool(stage["aggregate"]["operations_exact"]),
        "five_samples_no_outlier_removal": (
            combined["aggregate"]["sample_count"] == PROFILE_SAMPLES
            and stage["aggregate"]["profile_sample_count"] == PROFILE_SAMPLES
            and combined["aggregate"]["outlier_removal"] == "none"
            and stage["aggregate"]["outlier_removal"] == "none"
        ),
        "stage_attribution_at_least_95_percent": bool(
            decomposition["attribution"]["passes"]
        ),
        "cycle_counter_and_sampler": bool(
            stage["aggregate"]["cycle_counter_available"]
            and stage["aggregate"]["sampler_available"]
            and stage["aggregate"]["sampler_sample_count"] > 0
        ),
        "profiled_result_parity": (
            stage["aggregate"]["profiled_result_mismatch_count"] == 0
        ),
        "fixture_parity": semantic["fixture"]["mismatch_count"] == 0,
        "transition_oracle_parity": (
            semantic["transition_oracle"]["mismatch_count"] == 0
        ),
        "python_native_evaluator_parity": (
            semantic["python_native_evaluator"]["mismatch_count"] == 0
            and semantic["python_native_evaluator"]["invalid_selected_count"] == 0
        ),
        "determinism": (
            semantic["determinism"]["mismatch_count"] == 0
            and combined["aggregate"]["determinism_mismatch_count"] == 0
            and stage["aggregate"]["determinism_mismatch_count"] == 0
        ),
        "normal_hot_path_allocations": source["normal_hot_path_heap_allocations"] == 0,
        "child_state_abi": source["child_state_bytes"] == REQUIRED_CHILD_STATE_BYTES,
        "hot_result_abi": source["hot_result_bytes"] == REQUIRED_HOT_RESULT_BYTES,
        "diagnostic_ablation_only": bool(
            ablation["diagnostic_only"]
            and not ablation["production_semantics_changed"]
            and ablation["production_max_added_puyos"] == 3
        ),
        "budget_ledger_exact": math.isclose(
            float(budget["stage_budget_sum_600k_ms"]),
            EVALUATOR_BUDGET_MS,
            rel_tol=0.0,
            abs_tol=1e-9,
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "decision": (
            "PROFILE_COMPLETE_OPTIMIZATION_REQUIRED"
            if not failures
            else "PROFILE_INVALID"
        ),
        "passed": not failures,
        "checks": checks,
        "failed_checks": failures,
        "combined_gate_met": (
            combined["aggregate"]["transition_evaluator_p95_ms"] <= COMBINED_BUDGET_MS
        ),
        "production_semantics_changed": False,
    }


def _report(summary: Mapping[str, Any]) -> str:
    combined = summary["combined_profile"]["aggregate"]
    decomposition = summary["stage_decomposition"]
    budget = summary["stage_budget"]
    call_counts = summary["call_counts"]["distribution"]
    ablation = summary["diagnostic_ablation"]
    lines = [
        "# PUYO-219 native evaluator hot-path profile",
        "",
        f"Decision: **{summary['decision']['decision']}**.",
        "",
        (
            f"The source-bound combined p95 is "
            f"{combined['transition_evaluator_p95_ms']:.3f} ms for exactly "
            f"{EXPANDED_NODE_COUNT:,} operations. The unchanged gate is "
            f"{COMBINED_BUDGET_MS:.3f} ms."
        ),
        "",
        "## Stage decomposition",
        "",
        "| Stage | Evaluator cycle share | ns/node | 600k projection | Budget |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name in EVALUATOR_STAGE_NAMES:
        stage = decomposition["evaluator_stages"][name]
        target = budget["stage_budget_ledger"][name]
        lines.append(
            f"| `{name}` | {stage['cycle_ratio_of_evaluator']:.3%} | "
            f"{stage['current_ns_per_node']:.3f} | "
            f"{stage['current_projected_600k_ms']:.3f} ms | "
            f"{target['target_budget_600k_ms']:.3f} ms |"
        )
    lines.extend(
        [
            "",
            (
                f"The interval sampler attributes "
                f"{decomposition['attribution']['sampled_stage_share']:.3%} of "
                "the profiled combined loop to named, non-overlapping stages."
            ),
            "",
            "## Exact call-count distribution",
            "",
            "| Counter | p50/node | p95/node | max/node | exact 600k total |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for name in COUNT_NAMES:
        row = call_counts[name]
        lines.append(
            f"| `{name}` | {row['p50_per_node']:,} | "
            f"{row['p95_per_node']:,} | {row['maximum_per_node']:,} | "
            f"{row['exact_600k_total']:,} |"
        )
    priority = " → ".join(budget["follow_up_order"])
    lines.extend(
        [
            "",
            "## Diagnostic depth A/B and follow-up",
            "",
            (
                "The diagnostic-only max_added_puyos=1/2/3 medians are "
                + " / ".join(
                    f"{ablation['median_ns_per_node'][str(depth)]:.3f}"
                    for depth in (1, 2, 3)
                )
                + " ns/node. Production remains max_added_puyos=3."
            ),
            "",
            f"Measured follow-up order: **{priority}**.",
            "",
            (
                "The proportional stage ledger sums exactly to the 774.353480 ms "
                "evaluator envelope. Each implementation must preserve all semantic, "
                "allocation, determinism, and 80/24-byte ABI gates."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run_profile(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    corpus_path: str | Path = DEFAULT_CORPUS_PATH,
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
) -> dict[str, Any]:
    destination = Path(output_dir)
    source_state = _source_state(destination)
    destination.mkdir(parents=True, exist_ok=True)
    corpus = _read_json(corpus_path)
    module = importlib.import_module("_puyo_deep_chain_native")
    config = load_chain_structure_config(DEFAULT_CONFIG_PATH)
    semantic, source_inputs = _semantic_verification(
        corpus=corpus,
        fixture_path=fixture_path,
        module=module,
        ticket=TICKET,
    )
    source = _source_verification(module)
    combined = _combined_profile(
        source_inputs=source_inputs,
        module=module,
        config=config,
    )
    stage, record_counts = _stage_profile(
        source_inputs=source_inputs,
        module=module,
        config=config,
    )
    call_counts = summarize_call_counts(
        record_counts,
        stage["aggregate"]["aggregate_counts_per_sample"],
    )
    decomposition = derive_stage_decomposition(combined, stage)
    budget = derive_stage_budget(decomposition)
    ablation = _depth_ablation(
        source_inputs=source_inputs,
        module=module,
        production_config=config,
        depth_three_profile=combined,
    )
    corpus_sha256 = file_sha256(corpus_path)
    config_sha256 = file_sha256(DEFAULT_CONFIG_PATH)
    source_ancestor = _source_commit_is_ancestor()
    decision = derive_profile_decision(
        semantic=semantic,
        source=source,
        combined=combined,
        stage=stage,
        decomposition=decomposition,
        budget=budget,
        ablation=ablation,
        source_commit_is_ancestor=source_ancestor,
        corpus_sha256=corpus_sha256,
        corpus_digest=str(corpus["corpus_digest"]),
        config_sha256=config_sha256,
    )
    measurement = {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "ticket": TICKET,
        "source_ticket": SOURCE_TICKET,
        "source_implementation_commit": SOURCE_IMPLEMENTATION_COMMIT,
        "source_implementation_is_ancestor": source_ancestor,
        "corpus": {
            "path": str(Path(corpus_path).relative_to(ROOT)),
            "sha256": corpus_sha256,
            "corpus_digest": corpus["corpus_digest"],
            "state_count": corpus["state_count"],
            "transition_count": corpus["transition_count"],
            "selected_action_count": len(source_inputs),
        },
        "config": {
            "path": str(DEFAULT_CONFIG_PATH.relative_to(ROOT)),
            "sha256": config_sha256,
            "production_max_added_puyos": config.budget.max_added_puyos,
        },
        "canonical_profile": {
            "release_build": True,
            "evaluator_thread_count": 1,
            "warmup_operations": WARMUP_OPERATIONS,
            "operations_per_sample": EXPANDED_NODE_COUNT,
            "sample_count": PROFILE_SAMPLES,
            "percentile": "nearest-rank sorted[ceil(p/100*N)-1]",
            "outlier_removal": "none",
            "fallback_timing": False,
        },
        "stage_profile": {
            "diagnostic_only": True,
            "sample_interval_us": SAMPLE_INTERVAL_US,
            "sampler_thread_count": 1,
            "evaluator_thread_count": 1,
            "marker_stages": list(NATIVE_CHAIN_STRUCTURE_PROFILE_STAGE_NAMES),
            "external_sampling_tools": {
                "perf": shutil.which("perf"),
                "valgrind": shutil.which("valgrind"),
                "fallback": "built-in user-space interval sampler",
            },
        },
        "budgets": {
            "combined_600k_ms": COMBINED_BUDGET_MS,
            "transition_600k_ms": TRANSITION_PROJECTION_MS,
            "evaluator_600k_ms": EVALUATOR_BUDGET_MS,
            "evaluator_ns_per_node": EVALUATOR_BUDGET_MS
            * 1_000_000.0
            / EXPANDED_NODE_COUNT,
        },
    }
    raw_profile = {
        "schema_version": RAW_PROFILE_SCHEMA_VERSION,
        "ticket": TICKET,
        "combined": combined,
        "stage": stage,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "measurement_commit": git_commit(ROOT),
        "source_implementation_commit": SOURCE_IMPLEMENTATION_COMMIT,
        "semantic": semantic,
        "source_verification": source,
        "combined_profile": combined,
        "stage_decomposition": decomposition,
        "call_counts": call_counts,
        "diagnostic_ablation": ablation,
        "stage_budget": budget,
        "decision": decision,
    }
    artifact_payloads = {
        "measurement_contract.json": measurement,
        "semantic_verification.json": semantic,
        "raw_profile.json": raw_profile,
        "stage_profile.json": stage,
        "call_counts.json": call_counts,
        "diagnostic_ablation.json": ablation,
        "stage_budget.json": budget,
        "benchmark_summary.json": summary,
    }
    for name, payload in artifact_payloads.items():
        _write_json(destination / name, payload)
    (destination / "benchmark_report.md").write_text(
        _report(summary),
        encoding="utf-8",
    )

    extension = importlib.import_module(
        "_puyo_deep_chain_native._puyo_deep_chain_native"
    )
    module_path = Path(extension.__file__)
    wheels = sorted(
        (ROOT / "dist" / "native").glob(
            "puyo_deep_chain_native-*-cp312-*-manylinux_2_28_x86_64.whl"
        )
    )
    if len(wheels) != 1:
        raise RuntimeError(
            "expected exactly one canonical manylinux_2_28 release wheel"
        )
    wheel_path = wheels[0]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "measurement_commit": summary["measurement_commit"],
        "source_implementation_commit": SOURCE_IMPLEMENTATION_COMMIT,
        "source_tree_dirty_before_run": source_state["dirty"],
        "source_tracked_diff_before_run": source_state["tracked_diff"],
        "source_untracked_paths_before_run": source_state["untracked_paths"],
        "environment": {
            "platform": platform.platform(),
            "cpu": _cpu_model(),
            "python": sys.version.split()[0],
            "rustc": _command_version(["rustc", "--version"]),
            "canonical_evaluator_thread_count": 1,
            "native_module_path": str(module_path),
            "native_module_sha256": file_sha256(module_path),
            "release_wheel_path": str(wheel_path.relative_to(ROOT)),
            "release_wheel_sha256": file_sha256(wheel_path),
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


def verify_profile(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    require_exact_wheel: bool = False,
    allow_historical_wheel: bool = False,
) -> list[str]:
    destination = Path(output_dir)
    issues = []
    try:
        manifest = _read_json(destination / "benchmark_manifest.json")
        summary = _read_json(destination / "benchmark_summary.json")
        measurement = _read_json(destination / "measurement_contract.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"artifact read failed: {exc}"]
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        issues.append("unexpected manifest schema")
    if summary.get("schema_version") != SUMMARY_SCHEMA_VERSION:
        issues.append("unexpected summary schema")
    if measurement.get("schema_version") != MEASUREMENT_SCHEMA_VERSION:
        issues.append("unexpected measurement schema")
    for artifact in manifest.get("artifacts", []):
        path = destination / artifact["path"]
        if not path.is_file():
            issues.append(f"missing artifact: {artifact['path']}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"artifact hash mismatch: {artifact['path']}")
    canonical = measurement.get("canonical_profile", {})
    if canonical.get("operations_per_sample") != EXPANDED_NODE_COUNT:
        issues.append("expanded-node authority drifted")
    if canonical.get("sample_count") != PROFILE_SAMPLES:
        issues.append("sample-count authority drifted")
    if canonical.get("outlier_removal") != "none":
        issues.append("outlier policy drifted")
    if measurement.get("source_implementation_commit") != SOURCE_IMPLEMENTATION_COMMIT:
        issues.append("source implementation commit drifted")
    if measurement.get("corpus", {}).get("sha256") != EXPECTED_CORPUS_SHA256:
        issues.append("frozen corpus hash drifted")
    if measurement.get("config", {}).get("sha256") != EXPECTED_CONFIG_SHA256:
        issues.append("frozen config hash drifted")
    environment = manifest.get("environment", {})
    wheel = environment.get("release_wheel_path")
    expected_wheel_sha256 = environment.get("release_wheel_sha256")
    if not _is_sha256(expected_wheel_sha256):
        issues.append("canonical release wheel hash is invalid")
    if not wheel or "manylinux_2_28_x86_64" not in wheel:
        issues.append("canonical release wheel path drifted")
    elif not (ROOT / wheel).is_file():
        issues.append("canonical release wheel is missing")
    elif file_sha256(ROOT / wheel) != expected_wheel_sha256:
        if require_exact_wheel:
            issues.append("canonical release wheel hash drifted")
        elif allow_historical_wheel:
            # The caller still validates every manifest-bound artifact; a
            # successor build may legitimately replace this untracked wheel.
            pass
        elif not _release_sources_unchanged(manifest.get("measurement_commit")):
            issues.append("rebuilt release wheel source inputs drifted")
        else:
            try:
                extension = importlib.import_module(
                    "_puyo_deep_chain_native._puyo_deep_chain_native"
                )
                capabilities = decode_capabilities(bytes(extension.capabilities()))
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                issues.append(f"rebuilt release wheel provenance unavailable: {exc}")
            else:
                if capabilities.source_revision != git_commit(ROOT):
                    issues.append("rebuilt release wheel revision differs from HEAD")
    decision = summary.get("decision", {})
    if manifest.get("decision") != decision.get("decision"):
        issues.append("manifest and summary decisions differ")
    if decision.get("passed") != all(decision.get("checks", {}).values()):
        issues.append("decision does not match profile checks")
    if not decision.get("passed"):
        issues.append("profile decision did not pass")
    if not summary.get("stage_decomposition", {}).get("attribution", {}).get("passes"):
        issues.append("stage attribution is below 95 percent")
    budget = summary.get("stage_budget", {})
    if not math.isclose(
        float(budget.get("stage_budget_sum_600k_ms", -1.0)),
        EVALUATOR_BUDGET_MS,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        issues.append("stage budget does not sum to evaluator envelope")
    return issues


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
        if command == "verify":
            subparser.add_argument(
                "--require-exact-wheel",
                action="store_true",
                help="require the locally present wheel to match the measured wheel SHA",
            )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_profile(output_dir=args.output_dir)
        print(json.dumps(summary["decision"], indent=2, sort_keys=True))
        return 0 if summary["decision"]["passed"] else 1
    issues = verify_profile(
        args.output_dir,
        require_exact_wheel=args.require_exact_wheel,
    )
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print(f"verified: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
