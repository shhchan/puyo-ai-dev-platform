"""PUYO-205 compact-transition cycle, layout, and call-count evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agents import long_horizon_search
from agents.chain_structure import ChainStructureEvaluator
from agents.deep_chain_native_transition import (
    NativeCompactBatchClient,
    NativeCompactTransitionInput,
    decode_native_compact_batch_response,
    encode_native_compact_batch,
)
from agents.long_horizon_search import LongHorizonSearchConfig
from eval.deep_chain_native_profile import (
    _pair_from_names as _profile_pair_from_names,
)
from eval.deep_chain_native_profile import (
    _search_payload,
    _state_from_payload,
)
from eval.deep_chain_native_transition_benchmark import (
    DEFAULT_CORPUS_PATH,
    _digest,
    _flatten_inputs,
    _installed_wheel_sha256,
    _read_json,
    _write_json,
    evaluate_native_parity,
    verify_frozen_corpus,
)
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-205"
PROFILE_SCHEMA_VERSION = "puyo.native_compact_profile.v1"
BENCHMARK_SCHEMA_VERSION = "puyo.native_compact_profile_benchmark.v1"
DEFAULT_SEARCH_CORPUS_PATH = Path("eval/deep_chain_native_corpus.json")
DEFAULT_OUTPUT_DIR = Path("docs/benchmarks/puyo-205-native-compact-profile")
CANONICAL_MAX_EXPANDED_NODES = 600_000
END_TO_END_BUDGET_MS = 1_000.0
NATIVE_TOTAL_BUDGET_MS = 900.0
ORIGINAL_TRANSITION_BUDGET_MS = 10.596
ORIGINAL_EVALUATOR_BUDGET_MS = 810.029
COMBINED_TRANSITION_EVALUATOR_BUDGET_MS = (
    ORIGINAL_TRANSITION_BUDGET_MS + ORIGINAL_EVALUATOR_BUDGET_MS
)
TRANSITION_TARGET_NS = 100.0
QUIET_TARGET_NS = 50.0
TRANSITION_TARGET_MS = (
    TRANSITION_TARGET_NS * CANONICAL_MAX_EXPANDED_NODES / 1_000_000.0
)
EVALUATOR_REMAINING_BUDGET_MS = (
    COMBINED_TRANSITION_EVALUATOR_BUDGET_MS - TRANSITION_TARGET_MS
)

PROFILE_MODES = {
    "baseline": 0,
    "full_transition": 1,
    "direct_placement": 2,
    "color_plane_extraction": 3,
    "inserted_connectivity": 4,
    "state_result_materialization": 5,
    "chain_scan": 6,
    "gravity": 7,
    "score_lifecycle": 8,
    "layout_three_bit_slices": 101,
    "layout_six_color_planes": 102,
    "layout_column_local": 103,
    "layout_local_metadata_cache": 104,
    "result_full_summary": 201,
    "result_minimal_hot": 202,
    "result_hot_with_metadata": 203,
}
STAGE_NAMES = (
    "direct_placement",
    "color_plane_extraction",
    "inserted_connectivity",
    "state_result_materialization",
    "chain_scan",
    "gravity",
    "score_lifecycle",
)
LAYOUT_NAMES = (
    "layout_three_bit_slices",
    "layout_six_color_planes",
    "layout_column_local",
    "layout_local_metadata_cache",
)
RESULT_NAMES = (
    "result_full_summary",
    "result_minimal_hot",
    "result_hot_with_metadata",
)
OUTCOME_NAMES = ("quiet", "one_chain", "multi_chain")

_PROFILE_RESPONSE = struct.Struct("<4sHHHHIIQQQQIIIIIII")
_PROFILE_MAGIC = b"PCPS"


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Return a nearest-rank percentile without dropping any observation."""

    if not values:
        raise ValueError("cannot calculate a percentile from no values")
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(percentile / 100.0 * len(ordered)) - 1)
    return ordered[index]


def decode_profile_measurement(payload: bytes) -> dict[str, Any]:
    if payload[:4] == b"PCTE":
        decode_native_compact_batch_response(payload)
        raise AssertionError("profile error frame unexpectedly decoded")
    if len(payload) != _PROFILE_RESPONSE.size:
        raise ValueError("native compact profile response has an invalid length")
    (
        magic,
        major,
        minor,
        mode,
        flags,
        record_count,
        repeats,
        operations,
        elapsed_ns,
        cycles,
        checksum,
        mismatch_count,
        state_bytes,
        result_bytes,
        copy_bytes_per_record,
        update_bytes_per_record,
        reusable_metadata_bytes,
        reserved,
    ) = _PROFILE_RESPONSE.unpack(payload)
    if magic != _PROFILE_MAGIC or (major, minor) != (1, 0) or reserved != 0:
        raise ValueError("native compact profile response has invalid framing")
    measured_records = int(record_count) * int(repeats)
    if measured_records <= 0:
        raise ValueError("native compact profile response has no measured records")
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "mode": int(mode),
        "flags": int(flags),
        "record_count": int(record_count),
        "repeats": int(repeats),
        "measured_records": measured_records,
        "operations": int(operations),
        "stage_invocations_per_record": float(operations / measured_records),
        "elapsed_ns": int(elapsed_ns),
        "cycles": int(cycles),
        "per_record_ns": float(elapsed_ns / measured_records),
        "per_record_cycles": float(cycles / measured_records),
        "checksum": int(checksum),
        "mismatch_count": int(mismatch_count),
        "state_bytes": int(state_bytes),
        "result_bytes": int(result_bytes),
        "copy_bytes_per_record": int(copy_bytes_per_record),
        "update_bytes_per_record": int(update_bytes_per_record),
        "reusable_metadata_bytes": int(reusable_metadata_bytes),
        "cycle_source": "rdtsc-lfence" if flags & 0x1 else "unavailable",
    }


class NativeCompactProfiler:
    def __init__(self, module: Any | None = None) -> None:
        selected = module or importlib.import_module("_puyo_deep_chain_native")
        if (
            getattr(selected, "COMPACT_PROFILE_SCHEMA", None)
            != PROFILE_SCHEMA_VERSION
            or not callable(getattr(selected, "_compact_transition_profile", None))
        ):
            raise RuntimeError("release extension does not expose the PUYO-205 profile contract")
        self.module = selected

    def measure(self, request: bytes, *, mode: int, repeats: int = 1) -> dict[str, Any]:
        response = self.module._compact_transition_profile(request, mode, repeats)
        result = decode_profile_measurement(bytes(response))
        if result["mode"] != mode or result["repeats"] != repeats:
            raise AssertionError("native profile response changed the requested dimensions")
        return result


def _summarize_samples(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("profile sample set is empty")
    first = rows[0]
    per_record_ns = [float(row["per_record_ns"]) for row in rows]
    per_record_cycles = [float(row["per_record_cycles"]) for row in rows]
    return {
        "sample_count": len(rows),
        "record_count": int(first["record_count"]),
        "repeats": int(first["repeats"]),
        "operations_per_sample": int(first["operations"]),
        "stage_invocations_per_record": float(first["stage_invocations_per_record"]),
        "p50_ns_per_record": _percentile(per_record_ns, 50),
        "p95_ns_per_record": _percentile(per_record_ns, 95),
        "p50_cycles_per_record": _percentile(per_record_cycles, 50),
        "p95_cycles_per_record": _percentile(per_record_cycles, 95),
        "minimum_ns_per_record": min(per_record_ns),
        "maximum_ns_per_record": max(per_record_ns),
        "outlier_exclusion": "none",
        "percentile_method": "nearest-rank: sorted[ceil(p/100*N)-1]",
        "cycle_source": first["cycle_source"],
        "mismatch_count": max(int(row["mismatch_count"]) for row in rows),
        "state_bytes": int(first["state_bytes"]),
        "result_bytes": int(first["result_bytes"]),
        "copy_bytes_per_record": int(first["copy_bytes_per_record"]),
        "update_bytes_per_record": int(first["update_bytes_per_record"]),
        "reusable_metadata_bytes": int(first["reusable_metadata_bytes"]),
    }


def _sample_mode(
    profiler: NativeCompactProfiler,
    request: bytes,
    *,
    mode: int,
    samples: int,
    warmup: int,
    repeats: int = 1,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if samples <= 0 or warmup < 0:
        raise ValueError("invalid profile sample dimensions")
    for _ in range(warmup):
        profiler.measure(request, mode=mode, repeats=repeats)
    rows = [
        profiler.measure(request, mode=mode, repeats=repeats)
        for _ in range(samples)
    ]
    return _summarize_samples(rows), rows


def _cycle_records(
    records: Sequence[NativeCompactTransitionInput], count: int
) -> tuple[NativeCompactTransitionInput, ...]:
    if not records or count <= 0:
        raise ValueError("cannot cycle an empty native profile category")
    return tuple(records[index % len(records)] for index in range(count))


def _profile_inputs(
    corpus: Mapping[str, Any], *, mixed_batch_size: int, outcome_batch_size: int
) -> tuple[
    dict[str, tuple[NativeCompactTransitionInput, ...]],
    dict[str, dict[str, Any]],
]:
    flattened = _flatten_inputs(corpus)
    mixed = tuple(flattened[: min(mixed_batch_size, len(flattened))])
    classified = NativeCompactBatchClient().transition_batch(mixed)
    categories: dict[str, list[NativeCompactTransitionInput]] = {
        name: [] for name in OUTCOME_NAMES
    }
    for transition_input, output in zip(mixed, classified.records, strict=True):
        if not output.valid:
            continue
        if output.chain_count == 0:
            categories["quiet"].append(transition_input)
        elif output.chain_count == 1:
            categories["one_chain"].append(transition_input)
        else:
            categories["multi_chain"].append(transition_input)
    selected = {"mixed": mixed}
    metadata = {
        "mixed": {
            "source_records": len(mixed),
            "measured_records": len(mixed),
            "cycling": False,
        }
    }
    for name in OUTCOME_NAMES:
        selected[name] = _cycle_records(categories[name], outcome_batch_size)
        metadata[name] = {
            "source_records": len(categories[name]),
            "measured_records": outcome_batch_size,
            "cycling": len(categories[name]) < outcome_batch_size,
        }
    return selected, metadata


class _CountingEvaluator:
    def __init__(self) -> None:
        self.delegate = ChainStructureEvaluator()
        self.calls = 0

    def evaluate(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self.delegate.evaluate(*args, **kwargs)


def measure_call_count_model(
    corpus_path: str | Path = DEFAULT_SEARCH_CORPUS_PATH,
) -> dict[str, Any]:
    corpus = _read_json(corpus_path)
    search_case = corpus["search_case"]
    state = _state_from_payload(search_case["state"])
    known_pairs = tuple(
        _profile_pair_from_names(pair) for pair in search_case["known_pairs"]
    )
    config = LongHorizonSearchConfig(**search_case["config"])
    evaluator = _CountingEvaluator()
    original_transition = long_horizon_search.transition
    transition_calls = 0

    def counted_transition(*args: Any, **kwargs: Any) -> Any:
        nonlocal transition_calls
        transition_calls += 1
        return original_transition(*args, **kwargs)

    long_horizon_search.transition = counted_transition
    try:
        result = long_horizon_search.run_compact_long_horizon_search(
            state,
            known_pairs,
            config,
            evaluator=evaluator,
        )
    finally:
        long_horizon_search.transition = original_transition
    actual = _search_payload(result)
    expected = search_case["expected"]
    counters = result.counters.to_dict()
    expanded = int(counters["expanded_nodes"])
    generated = int(counters["generated_nodes"])
    evaluated = int(counters["evaluated_nodes"])
    transition_per_expanded = transition_calls / expanded if expanded else 0.0
    evaluator_per_evaluated = (evaluator.calls - 1) / evaluated if evaluated else 0.0
    return {
        "schema_version": "puyo.native_compact_call_count.v1",
        "corpus_path": str(corpus_path),
        "corpus_sha256": file_sha256(corpus_path),
        "corpus_digest": corpus["corpus_digest"],
        "config": search_case["config"],
        "expected_search_matches": actual == expected,
        "actual_search_digest": actual["deterministic_digest"],
        "expected_search_digest": expected["deterministic_digest"],
        "python_search": {
            "counters": counters,
            "transition_calls": transition_calls,
            "evaluator_calls_including_root": evaluator.calls,
            "root_evaluator_calls": 1,
            "transition_calls_per_expanded_node": transition_per_expanded,
            "transition_calls_per_generated_node": (
                transition_calls / generated if generated else 0.0
            ),
            "non_root_evaluator_calls_per_evaluated_node": evaluator_per_evaluated,
        },
        "planned_native_search": {
            "semantic_loop": "one transition per budget-consumed candidate",
            "transition_calls_per_expanded_node": transition_per_expanded,
            "non_root_evaluator_calls_per_evaluated_node": evaluator_per_evaluated,
            "canonical_transition_call_ceiling": int(
                CANONICAL_MAX_EXPANDED_NODES * transition_per_expanded
            ),
            "canonical_evaluated_call_projection": int(
                CANONICAL_MAX_EXPANDED_NODES
                * (evaluated / expanded if expanded else 0.0)
            ),
        },
        "assumption_600k_is_one_transition_each": (
            expanded == generated == evaluated == transition_calls
        ),
    }


def _parse_cachegrind(path: Path) -> dict[str, int]:
    events: list[str] | None = None
    summary: list[int] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("events: "):
            events = line.split()[1:]
        elif line.startswith("summary: "):
            summary = [int(value) for value in line.split()[1:]]
    if events is None or summary is None or len(events) != len(summary):
        raise RuntimeError(f"cannot parse Cachegrind summary from {path}")
    return dict(zip(events, summary, strict=True))


def _cachegrind_metrics(events: Mapping[str, int]) -> dict[str, int]:
    return {
        "instructions": int(events.get("Ir", 0)),
        "instruction_l1_misses": int(events.get("I1mr", 0)),
        "instruction_ll_misses": int(events.get("ILmr", 0)),
        "data_reads": int(events.get("Dr", 0)),
        "data_writes": int(events.get("Dw", 0)),
        "data_l1_misses": int(events.get("D1mr", 0) + events.get("D1mw", 0)),
        "data_ll_misses": int(events.get("DLmr", 0) + events.get("DLmw", 0)),
        "branches": int(events.get("Bc", 0) + events.get("Bi", 0)),
        "branch_misses": int(events.get("Bcm", 0) + events.get("Bim", 0)),
    }


def _subtract_metrics(
    value: Mapping[str, int], baseline: Mapping[str, int]
) -> dict[str, int]:
    return {
        name: max(0, int(metric) - int(baseline.get(name, 0)))
        for name, metric in value.items()
    }


def run_cachegrind(
    request: bytes,
    *,
    valgrind: str,
    valgrind_lib: str | None,
    repeats: int,
    cpu: int,
) -> dict[str, Any]:
    selected_modes = ("baseline", "full_transition", *STAGE_NAMES)
    environment = os.environ.copy()
    environment.update(
        {
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "RAYON_NUM_THREADS": "1",
        }
    )
    if valgrind_lib:
        environment["VALGRIND_LIB"] = valgrind_lib
    raw: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="puyo-205-cachegrind-") as directory:
        root = Path(directory)
        request_path = root / "request.bin"
        request_path.write_bytes(request)
        for name in selected_modes:
            output_path = root / f"{name}.out"
            command = [
                valgrind,
                "--tool=cachegrind",
                "--cache-sim=yes",
                "--branch-sim=yes",
                f"--cachegrind-out-file={output_path}",
                sys.executable,
                "-m",
                "eval.deep_chain_native_transition_profile",
                "cachegrind-child",
                "--request",
                str(request_path),
                "--mode",
                str(PROFILE_MODES[name]),
                "--repeats",
                str(repeats),
                "--cpu",
                str(cpu),
            ]
            completed = subprocess.run(
                command,
                cwd=Path.cwd(),
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"Cachegrind mode {name} failed ({completed.returncode}): "
                    f"{completed.stderr[-2000:]}"
                )
            child = json.loads(completed.stdout.strip().splitlines()[-1])
            events = _parse_cachegrind(output_path)
            raw[name] = {
                "events": events,
                "metrics": _cachegrind_metrics(events),
                "measurement": child,
            }
    baseline = raw["baseline"]["metrics"]
    adjusted = {
        name: _subtract_metrics(value["metrics"], baseline)
        for name, value in raw.items()
        if name != "baseline"
    }
    measured_records = int(raw["baseline"]["measurement"]["measured_records"])
    per_record = {
        name: {
            metric: value / measured_records for metric, value in metrics.items()
        }
        for name, metrics in adjusted.items()
    }
    stage_instruction_leader = max(
        STAGE_NAMES,
        key=lambda name: per_record[name]["instructions"],
    )
    return {
        "schema_version": "puyo.native_compact_cachegrind.v1",
        "tool": "Valgrind Cachegrind simulated counters",
        "command_shape": (
            "valgrind --tool=cachegrind --cache-sim=yes --branch-sim=yes "
            "python -m eval.deep_chain_native_transition_profile cachegrind-child ..."
        ),
        "hardware_pmu": {
            "available": False,
            "probe_result": "perf_event_open hardware counters returned ENOENT under WSL2",
            "fallback": "Cachegrind instruction/cache/branch simulation",
        },
        "repeats": repeats,
        "measured_records": measured_records,
        "baseline_subtraction": "clamped mode total minus identical native baseline mode",
        "raw": raw,
        "baseline_adjusted": adjusted,
        "per_record": per_record,
        "largest_stage_by_instructions": stage_instruction_leader,
    }


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def _compiler_version() -> str:
    completed = subprocess.run(
        ["rustc", "--version"], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _pin_process(cpu: int | None) -> tuple[int, list[int]]:
    available = sorted(os.sched_getaffinity(0))
    selected = available[0] if cpu is None else int(cpu)
    if selected not in available:
        raise ValueError(f"CPU {selected} is outside current affinity {available}")
    os.sched_setaffinity(0, {selected})
    return selected, available


def _stage_decomposition(
    profile: Mapping[str, Any], cachegrind: Mapping[str, Any]
) -> dict[str, Any]:
    quiet = profile["quiet"]
    baseline_cycles = float(quiet["baseline"]["p50_cycles_per_record"])
    full_cycles = max(
        0.0,
        float(quiet["full_transition"]["p50_cycles_per_record"])
        - baseline_cycles,
    )
    stage_cycles = {
        name: max(
            0.0,
            float(quiet[name]["p50_cycles_per_record"]) - baseline_cycles,
        )
        for name in STAGE_NAMES
    }
    active_stage_cycles = {
        name: value
        for name, value in stage_cycles.items()
        if quiet[name]["stage_invocations_per_record"] > 0
    }
    explained = sum(active_stage_cycles.values())
    raw_fraction = explained / full_cycles if full_cycles else 0.0
    leader = max(active_stage_cycles, key=active_stage_cycles.get)
    cache_per_record = cachegrind["per_record"]
    return {
        "method": (
            "baseline-adjusted isolated semantic stages; stages are non-overlapping "
            "operations but compiler fusion makes their summed loop cost an estimate"
        ),
        "quiet_full_p50_cycles_per_record": full_cycles,
        "quiet_stage_p50_cycles_per_record": stage_cycles,
        "quiet_active_stage_sum_cycles_per_record": explained,
        "raw_explained_fraction": raw_fraction,
        "bounded_explained_fraction": min(1.0, raw_fraction),
        "residual_control_cycles_per_record": max(0.0, full_cycles - explained),
        "largest_stage_by_cycles": leader,
        "largest_stage_by_instructions": cachegrind["largest_stage_by_instructions"],
        "largest_stage_instruction_count_per_record": cache_per_record[
            cachegrind["largest_stage_by_instructions"]
        ]["instructions"],
    }


def derive_budget_decision(
    mixed_p95_ns: float,
    call_count: Mapping[str, Any],
    stage_decomposition: Mapping[str, Any],
) -> dict[str, Any]:
    calls = int(
        call_count["planned_native_search"]["canonical_transition_call_ceiling"]
    )
    current_projection_ms = mixed_p95_ns * calls / 1_000_000.0
    required_strict_speedup = mixed_p95_ns / (
        ORIGINAL_TRANSITION_BUDGET_MS * 1_000_000.0 / calls
    )
    leader_cycles = float(
        stage_decomposition["quiet_stage_p50_cycles_per_record"].get(
            stage_decomposition["largest_stage_by_cycles"], 0.0
        )
    )
    full_cycles = float(stage_decomposition["quiet_full_p50_cycles_per_record"])
    amdahl_without_leader = (
        full_cycles / max(full_cycles - leader_cycles, 1e-12)
        if full_cycles > 0
        else 0.0
    )
    return {
        "locked_constraints": {
            "end_to_end_p95_ms": END_TO_END_BUDGET_MS,
            "native_total_p95_ms": NATIVE_TOTAL_BUDGET_MS,
            "max_expanded_nodes": CANONICAL_MAX_EXPANDED_NODES,
            "semantic_parity": "exact",
        },
        "observed": {
            "mixed_p95_ns_per_transition": mixed_p95_ns,
            "canonical_transition_call_ceiling": calls,
            "projected_transition_ms": current_projection_ms,
            "strict_17_66ns_required_speedup": required_strict_speedup,
            "largest_quiet_stage": stage_decomposition["largest_stage_by_cycles"],
            "amdahl_speedup_if_largest_stage_were_free": amdahl_without_leader,
        },
        "decision": {
            "selected_option": "hot-path redesign plus transition/evaluator fusion budget",
            "authoritative_gate": "transition_plus_evaluator_shared_state",
            "combined_p95_budget_ms": COMBINED_TRANSITION_EVALUATOR_BUDGET_MS,
            "transition_p95_target_ns_per_call": TRANSITION_TARGET_NS,
            "quiet_p95_target_ns_per_call": QUIET_TARGET_NS,
            "transition_projection_target_ms": TRANSITION_TARGET_MS,
            "evaluator_remaining_p95_budget_ms": EVALUATOR_REMAINING_BUDGET_MS,
            "evaluator_remaining_ns_per_evaluated_call": (
                EVALUATOR_REMAINING_BUDGET_MS * 1_000_000.0 / calls
            ),
            "selected_layout": "three_bit_slices_with_existing_heights_and_settled_flags",
            "selected_hot_result": "24-byte minimal hot result with caller-owned child state",
            "qa_materialization": "lazy for root/final representative or explicit evidence request",
            "shared_state_contract": (
                "three exact color slices, drop heights, settled/lifecycle flags; "
                "derive occupied once per evaluator call and do not persist a full component cache"
            ),
        },
        "options": [
            {
                "option": "keep strict transition component gate",
                "decision": "reject",
                "reason": (
                    "17.66 ns/call is a Python-share allocation and requires the recorded "
                    f"{required_strict_speedup:.3f}x transition-only speedup"
                ),
            },
            {
                "option": "redesign reachable hot path",
                "decision": "adopt in PUYO-206",
                "reason": "remove repeated extraction/connectivity work and lazy-materialize QA data",
            },
            {
                "option": "transition/evaluator fused budget",
                "decision": "adopt as authority",
                "reason": "preserves the original 820.625 ms sum and rewards shared board metadata",
            },
            {
                "option": "stop native implementation",
                "decision": "conditional",
                "reason": "mandatory if PUYO-206 misses either fixed per-call target or PUYO-207 combined gate",
            },
        ],
        "stop_conditions": [
            "PUYO-206 mixed p95 exceeds 100.0 ns/transition under this exact contract",
            "PUYO-206 quiet p95 exceeds 50.0 ns/transition under this exact contract",
            "PUYO-207 transition+evaluator p95 exceeds 820.625 ms at the measured call model",
            "native total exceeds 900 ms or end-to-end p95 exceeds 1,000 ms",
            "any semantic, deterministic, exact-key, or allocation-free contract fails",
        ],
    }


def _render_report(summary: Mapping[str, Any]) -> str:
    profile = summary["profiles"]
    decision = summary["budget_decision"]
    lines = [
        "# PUYO-205 compact transition profile",
        "",
        f"- Evaluated commit: `{summary['evaluated_commit']}`",
        f"- Wheel SHA-256: `{summary['environment']['wheel_sha256']}`",
        f"- Corpus digest: `{summary['corpus']['digest']}`",
        f"- CPU affinity: `{summary['environment']['selected_cpu']}` (one thread)",
        "- Outliers: none removed; p50/p95 use nearest rank",
        "",
        "## Outcome latency",
        "",
        "| Outcome | Samples | Records/sample | p50 ns | p95 ns | p95 cycles |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("mixed", *OUTCOME_NAMES):
        row = profile[name]["full_transition"]
        lines.append(
            f"| {name} | {row['sample_count']} | {row['record_count']} | "
            f"{row['p50_ns_per_record']:.3f} | {row['p95_ns_per_record']:.3f} | "
            f"{row['p95_cycles_per_record']:.3f} |"
        )
    decomposition = summary["stage_decomposition"]
    lines.extend(
        [
            "",
            "## Quiet-path decomposition",
            "",
            "| Stage | p50 baseline-adjusted cycles/transition |",
            "| --- | ---: |",
        ]
    )
    for name, cycles in decomposition["quiet_stage_p50_cycles_per_record"].items():
        lines.append(f"| {name} | {cycles:.3f} |")
    lines.extend(
        [
            "",
            (
                f"The semantic stages explain "
                f"`{decomposition['bounded_explained_fraction']:.3%}` of the quiet "
                f"transition loop estimate. `{decomposition['largest_stage_by_cycles']}` "
                f"is largest by cycles and "
                f"`{decomposition['largest_stage_by_instructions']}` is largest by "
                "Cachegrind-simulated instructions."
            ),
            "",
            (
                "Hardware PMU counters are unavailable in this WSL2 kernel "
                "(`perf_event_open` returns `ENOENT`). Cycles use RDTSCP/RDTSC "
                "ordering; instructions, branches, and cache events use Valgrind "
                "Cachegrind and are labelled as simulated."
            ),
            "",
            "## Call-count model",
            "",
            (
                f"The fixed search corpus measured "
                f"`{summary['call_count']['python_search']['transition_calls']}` "
                f"transition calls for "
                f"`{summary['call_count']['python_search']['counters']['expanded_nodes']}` "
                "expanded nodes. The 600,000-node ceiling therefore remains a "
                "600,000-call transition ceiling; it is neither relaxed nor multiplied "
                "by scenarios."
            ),
            "",
            "## ADR decision",
            "",
            (
                f"Adopt **{decision['decision']['selected_option']}**. PUYO-206 must "
                f"meet `{decision['decision']['transition_p95_target_ns_per_call']:.1f} "
                f"ns` mixed and "
                f"`{decision['decision']['quiet_p95_target_ns_per_call']:.1f} ns` quiet "
                "p95. PUYO-207 must then enforce the unchanged transition+evaluator "
                f"combined budget of "
                f"`{decision['decision']['combined_p95_budget_ms']:.3f} ms`, the native "
                "900 ms budget, and the end-to-end 1,000 ms gate."
            ),
            "",
            (
                "The selected representation remains the exact three-bit slices with "
                "existing height/settled metadata. A 24-byte hot result is written "
                "beside the caller-owned child state; full QA summaries and traces are "
                "materialized lazily."
            ),
            "",
            "## Reproduction",
            "",
            "```bash",
            "./scripts/build_deep_chain_native.sh",
            summary["command"],
            "python -m eval.deep_chain_native_transition_profile verify",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    selected_cpu, available_cpus = _pin_process(args.cpu)
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "RAYON_NUM_THREADS",
    ):
        os.environ[name] = "1"
    corpus = _read_json(args.corpus)
    oracle = verify_frozen_corpus(args.corpus)
    batch_client = NativeCompactBatchClient()
    native_parity = evaluate_native_parity(batch_client, corpus)
    selected, selection_metadata = _profile_inputs(
        corpus,
        mixed_batch_size=args.mixed_batch_size,
        outcome_batch_size=args.outcome_batch_size,
    )
    requests = {
        name: encode_native_compact_batch(records) for name, records in selected.items()
    }
    profiler = NativeCompactProfiler()
    raw_profiles: dict[str, dict[str, list[dict[str, Any]]]] = {}
    profiles: dict[str, dict[str, dict[str, Any]]] = {}
    for outcome, request in requests.items():
        raw_profiles[outcome] = {}
        profiles[outcome] = {}
        full_samples = args.mixed_samples if outcome == "mixed" else args.outcome_samples
        full_summary, full_raw = _sample_mode(
            profiler,
            request,
            mode=PROFILE_MODES["full_transition"],
            samples=full_samples,
            warmup=args.warmup,
        )
        profiles[outcome]["full_transition"] = full_summary
        raw_profiles[outcome]["full_transition"] = full_raw
        baseline_summary, baseline_raw = _sample_mode(
            profiler,
            request,
            mode=PROFILE_MODES["baseline"],
            samples=args.stage_samples,
            warmup=args.warmup,
        )
        profiles[outcome]["baseline"] = baseline_summary
        raw_profiles[outcome]["baseline"] = baseline_raw
        for name in STAGE_NAMES:
            stage_summary, stage_raw = _sample_mode(
                profiler,
                request,
                mode=PROFILE_MODES[name],
                samples=args.stage_samples,
                warmup=args.warmup,
            )
            profiles[outcome][name] = stage_summary
            raw_profiles[outcome][name] = stage_raw

    alternatives: dict[str, dict[str, Any]] = {}
    alternative_raw: dict[str, list[dict[str, Any]]] = {}
    for name in (*LAYOUT_NAMES, *RESULT_NAMES):
        summary_row, raw_rows = _sample_mode(
            profiler,
            requests["mixed"],
            mode=PROFILE_MODES[name],
            samples=args.alternative_samples,
            warmup=args.warmup,
        )
        alternatives[name] = summary_row
        alternative_raw[name] = raw_rows

    cachegrind_records = selected["quiet"][: args.cachegrind_records]
    cachegrind_request = encode_native_compact_batch(cachegrind_records)
    valgrind = args.valgrind or shutil.which("valgrind")
    if not valgrind:
        raise RuntimeError(
            "Valgrind is required for canonical PUYO-205 instruction/branch/cache evidence"
        )
    cachegrind = run_cachegrind(
        cachegrind_request,
        valgrind=valgrind,
        valgrind_lib=args.valgrind_lib,
        repeats=args.cachegrind_repeats,
        cpu=selected_cpu,
    )
    call_count = measure_call_count_model(args.search_corpus)
    decomposition = _stage_decomposition(profiles, cachegrind)
    budget = derive_budget_decision(
        profiles["mixed"]["full_transition"]["p95_ns_per_record"],
        call_count,
        decomposition,
    )
    capabilities = batch_client.capabilities.to_dict()
    command = (
        "python -m eval.deep_chain_native_transition_profile run "
        f"--corpus {args.corpus} --search-corpus {args.search_corpus} "
        f"--output-dir {args.output_dir} --mixed-samples {args.mixed_samples} "
        f"--outcome-samples {args.outcome_samples} --stage-samples {args.stage_samples} "
        f"--alternative-samples {args.alternative_samples} --warmup {args.warmup} "
        f"--mixed-batch-size {args.mixed_batch_size} "
        f"--outcome-batch-size {args.outcome_batch_size} "
        f"--cachegrind-records {args.cachegrind_records} "
        f"--cachegrind-repeats {args.cachegrind_repeats} --cpu {selected_cpu}"
    )
    checks = {
        "release_wheel": capabilities["build_profile"] == "release",
        "frozen_corpus_oracle_parity": oracle["passed"],
        "native_11264_parity": (
            native_parity["mismatch_count"] == 0
            and native_parity["action_mismatch_count"] == 0
        ),
        "mixed_has_at_least_100_samples": args.mixed_samples >= 100,
        "all_outcomes_profiled": all(
            selection_metadata[name]["source_records"] > 0 for name in OUTCOME_NAMES
        ),
        "no_profile_semantic_mismatch": all(
            row["mismatch_count"] == 0
            for values in profiles.values()
            for row in values.values()
        ),
        "alternative_layout_and_result_parity": all(
            row["mismatch_count"] == 0 for row in alternatives.values()
        ),
        "cycle_counter_available": all(
            row["cycle_source"] == "rdtsc-lfence"
            for values in profiles.values()
            for row in values.values()
        ),
        "cachegrind_counters_present": all(
            cachegrind["per_record"]["full_transition"][name] > 0
            for name in ("instructions", "data_reads", "branches")
        ),
        "quiet_stages_explain_at_least_90_percent": (
            decomposition["bounded_explained_fraction"] >= 0.9
        ),
        "call_count_model_confirmed": (
            call_count["expected_search_matches"]
            and call_count["assumption_600k_is_one_transition_each"]
        ),
        "locked_gate_unchanged": (
            budget["locked_constraints"]["end_to_end_p95_ms"] == 1_000.0
            and budget["locked_constraints"]["native_total_p95_ms"] == 900.0
            and budget["locked_constraints"]["max_expanded_nodes"] == 600_000
        ),
        "numeric_follow_up_target_fixed": (
            budget["decision"]["transition_p95_target_ns_per_call"] == 100.0
            and budget["decision"]["combined_p95_budget_ms"] == 820.625
        ),
    }
    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "evaluated_commit": git_commit(),
        "command": command,
        "environment": {
            "cpu": _cpu_model(),
            "selected_cpu": selected_cpu,
            "available_cpus_before_pin": available_cpus,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "compiler": _compiler_version(),
            "wheel_sha256": _installed_wheel_sha256(),
            "thread_environment": {
                name: os.environ[name]
                for name in (
                    "OMP_NUM_THREADS",
                    "MKL_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS",
                    "RAYON_NUM_THREADS",
                )
            },
            "hardware_pmu_available": False,
            "hardware_pmu_error": "ENOENT from perf_event_open on WSL2",
            "cycle_counter": "RDTSC serialized with LFENCE on one pinned CPU",
            "instruction_branch_cache_counter": "Valgrind Cachegrind simulated",
        },
        "measurement_contract": {
            "warmup_samples_per_mode": args.warmup,
            "mixed_samples": args.mixed_samples,
            "outcome_samples": args.outcome_samples,
            "stage_samples": args.stage_samples,
            "alternative_samples": args.alternative_samples,
            "outlier_exclusion": "none",
            "percentile_method": "nearest-rank: sorted[ceil(p/100*N)-1]",
            "release_wheel_required": True,
            "fixed_cpu": selected_cpu,
            "threads": 1,
        },
        "corpus": {
            "path": str(args.corpus),
            "sha256": file_sha256(args.corpus),
            "digest": corpus["corpus_digest"],
            "selection": selection_metadata,
            "request_sha256": {
                name: hashlib.sha256(request).hexdigest()
                for name, request in requests.items()
            },
        },
        "capabilities": capabilities,
        "oracle": oracle,
        "native_parity": native_parity,
        "profiles": profiles,
        "stage_decomposition": decomposition,
        "alternatives": alternatives,
        "cachegrind": {
            key: value for key, value in cachegrind.items() if key != "raw"
        },
        "call_count": call_count,
        "budget_decision": budget,
        "checks": checks,
        "passed": all(checks.values()),
    }
    summary["summary_digest"] = _digest(
        {
            "schema_version": summary["schema_version"],
            "ticket": summary["ticket"],
            "evaluated_commit": summary["evaluated_commit"],
            "corpus": summary["corpus"],
            "call_count": summary["call_count"],
            "budget_decision": summary["budget_decision"],
            "checks": summary["checks"],
        }
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_dir / "raw_profile.json",
        {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profiles": raw_profiles,
            "alternatives": alternative_raw,
        },
    )
    _write_json(output_dir / "cachegrind.json", cachegrind)
    _write_json(output_dir / "call_count.json", call_count)
    _write_json(output_dir / "alternative_contracts.json", alternatives)
    _write_json(output_dir / "benchmark_summary.json", summary)
    (output_dir / "benchmark_report.md").write_text(
        _render_report(summary), encoding="utf-8"
    )
    artifacts = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": summary["created_at_utc"],
        "evaluated_commit": summary["evaluated_commit"],
        "wheel_sha256": summary["environment"]["wheel_sha256"],
        "corpus_digest": summary["corpus"]["digest"],
        "command": command,
        "passed": summary["passed"],
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifacts
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    return summary


def verify_benchmark(artifact_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    root = Path(artifact_dir)
    manifest = _read_json(root / "benchmark_manifest.json")
    issues = []
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected PUYO-205 manifest schema")
    if manifest.get("ticket") != TICKET:
        issues.append("unexpected PUYO-205 manifest ticket")
    for artifact in manifest.get("artifacts", []):
        path = root / artifact["path"]
        if not path.exists():
            issues.append(f"missing artifact: {artifact['path']}")
        elif file_sha256(path) != artifact["sha256"]:
            issues.append(f"artifact digest mismatch: {artifact['path']}")
    summary = _read_json(root / "benchmark_summary.json")
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected PUYO-205 summary schema")
    if not summary.get("passed"):
        issues.append("PUYO-205 profile checks did not pass")
    if not all(summary.get("checks", {}).values()):
        issues.append("PUYO-205 summary contains a failed check")
    if summary.get("environment", {}).get("wheel_sha256") != manifest.get(
        "wheel_sha256"
    ):
        issues.append("wheel hash differs between summary and manifest")
    return {"passed": not issues, "issues": issues}


def _cachegrind_child(args: argparse.Namespace) -> int:
    _pin_process(args.cpu)
    request = Path(args.request).read_bytes()
    result = NativeCompactProfiler().measure(
        request,
        mode=args.mode,
        repeats=args.repeats,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--corpus", default=str(DEFAULT_CORPUS_PATH))
    run.add_argument("--search-corpus", default=str(DEFAULT_SEARCH_CORPUS_PATH))
    run.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    run.add_argument("--mixed-samples", type=int, default=120)
    run.add_argument("--outcome-samples", type=int, default=40)
    run.add_argument("--stage-samples", type=int, default=30)
    run.add_argument("--alternative-samples", type=int, default=40)
    run.add_argument("--warmup", type=int, default=5)
    run.add_argument("--mixed-batch-size", type=int, default=10_000)
    run.add_argument("--outcome-batch-size", type=int, default=4_096)
    run.add_argument("--cachegrind-records", type=int, default=256)
    run.add_argument("--cachegrind-repeats", type=int, default=512)
    run.add_argument("--valgrind")
    run.add_argument("--valgrind-lib")
    run.add_argument("--cpu", type=int)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--artifact-dir", default=str(DEFAULT_OUTPUT_DIR))
    child = subparsers.add_parser("cachegrind-child")
    child.add_argument("--request", required=True)
    child.add_argument("--mode", type=int, required=True)
    child.add_argument("--repeats", type=int, required=True)
    child.add_argument("--cpu", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_benchmark(args)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["passed"] else 1
    if args.command == "cachegrind-child":
        return _cachegrind_child(args)
    result = verify_benchmark(args.artifact_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
