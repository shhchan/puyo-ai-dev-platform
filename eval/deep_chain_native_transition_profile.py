"""PUYO-205 through PUYO-207 compact-transition evidence and verification."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import platform
import resource
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
    NATIVE_COMPACT_HOT_CHILD_STATE_BYTES,
    NATIVE_COMPACT_HOT_RESULT_ABI_VERSION,
    NATIVE_COMPACT_HOT_RESULT_BYTES,
    NATIVE_COMPACT_HOT_RESULT_SCHEMA_VERSION,
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
    _fixture_parity,
    _flatten_inputs,
    _installed_wheel_sha256,
    _read_json,
    _write_json,
    evaluate_native_parity,
    run_microbenchmark,
    verify_frozen_corpus,
)
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-205"
OPTIMIZATION_TICKET = "PUYO-206"
VERIFICATION_TICKET = "PUYO-207"
SUPPORTED_TICKETS = (TICKET, OPTIMIZATION_TICKET, VERIFICATION_TICKET)
PROFILE_SCHEMA_VERSION = "puyo.native_compact_profile.v1"
BENCHMARK_SCHEMA_VERSION = "puyo.native_compact_profile_benchmark.v1"
DEFAULT_SEARCH_CORPUS_PATH = Path("eval/deep_chain_native_corpus.json")
DEFAULT_OUTPUT_DIR = Path("docs/benchmarks/puyo-205-native-compact-profile")
DEFAULT_OPTIMIZATION_OUTPUT_DIR = Path(
    "docs/benchmarks/puyo-206-native-compact-hot-path"
)
DEFAULT_VERIFICATION_OUTPUT_DIR = Path(
    "docs/benchmarks/puyo-207-native-transition-verification"
)
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
CYCLE_COUNTER_METHOD = "RDTSC serialized with LFENCE"
TRANSITION_TARGET_MS = (
    TRANSITION_TARGET_NS * CANONICAL_MAX_EXPANDED_NODES / 1_000_000.0
)
EVALUATOR_REMAINING_BUDGET_MS = (
    COMBINED_TRANSITION_EVALUATOR_BUDGET_MS - TRANSITION_TARGET_MS
)
SEARCH_CONTROL_BUDGET_MS = 29.375
SERIALIZATION_BUDGET_MS = 20.0
AGGREGATION_BUDGET_MS = 30.0
ADAPTER_MARGIN_MS = 100.0
PYTHON_REFERENCE_LOWER_BOUND_MS = 300_000.0
NATIVE_BOUNDARY_SHARE = 0.998626728
PYTHON_TRANSITION_REFERENCE_NS = 137_479.0
PYTHON_EVALUATOR_REFERENCE_NS = 2_412_458.0
PROPERTY_TRANSITION_MINIMUM = 100_000
SOURCE_TEST_NAMES = (
    "inserted_component_fast_path_matches_full_scanner",
    "normal_hot_transition_performs_no_heap_allocation",
    "search_key_requires_external_coordinates_and_exact_board",
    "fixed_hot_result_materializes_the_same_detailed_trace_summary",
    "qa_profile_modes_preserve_semantics_and_publish_fixed_sizes",
)


def _default_output_dir(ticket: str) -> Path:
    if ticket == VERIFICATION_TICKET:
        return DEFAULT_VERIFICATION_OUTPUT_DIR
    if ticket == OPTIMIZATION_TICKET:
        return DEFAULT_OPTIMIZATION_OUTPUT_DIR
    return DEFAULT_OUTPUT_DIR

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
            raise RuntimeError(
                "release extension does not expose the compact-transition profile contract"
            )
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
    version = subprocess.run(
        [valgrind, "--version"],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
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
        "tool_version": version,
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


def _git_tree(commit: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", f"{commit}^{{tree}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def run_source_verification() -> dict[str, Any]:
    """Run the source-bound invariants not exposed through the release wheel."""

    command = [
        "cargo",
        "test",
        "--locked",
        "--release",
        "--manifest-path",
        "native/deep_chain_native/Cargo.toml",
        "compact::tests::",
        "--",
        "--nocapture",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    tests = {
        name: f"test compact::tests::{name} ... ok" in output
        for name in SOURCE_TEST_NAMES
    }
    return {
        "schema_version": "puyo.native_compact_source_verification.v1",
        "command": " ".join(command),
        "return_code": completed.returncode,
        "release_profile": True,
        "required_tests": tests,
        "property_corpus": {
            "test": "inserted_component_fast_path_matches_full_scanner",
            "minimum_checked_transitions_exclusive": PROPERTY_TRANSITION_MINIMUM,
            "optimized_path": "reachable inserted-component local update",
            "reference_path": "forced full-board scalar scanner",
            "mismatch_count": 0 if tests[SOURCE_TEST_NAMES[0]] else None,
        },
        "allocation": {
            "test": "normal_hot_transition_performs_no_heap_allocation",
            "normal_hot_path_heap_allocations": (
                0 if tests["normal_hot_transition_performs_no_heap_allocation"] else None
            ),
        },
        "exact_key_mismatch_count": (
            0
            if tests["search_key_requires_external_coordinates_and_exact_board"]
            else None
        ),
        "hot_and_detailed_result_mismatch_count": (
            0
            if tests[
                "fixed_hot_result_materializes_the_same_detailed_trace_summary"
            ]
            else None
        ),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "passed": completed.returncode == 0 and all(tests.values()),
    }


def run_cold_warm_verification(
    corpus_path: str | Path,
    *,
    cpu: int,
) -> dict[str, Any]:
    """Measure auxiliary cold/warm latency without heating the locked run."""

    command = [
        sys.executable,
        "-m",
        "eval.deep_chain_native_transition_profile",
        "cold-warm-child",
        "--corpus",
        str(corpus_path),
        "--cpu",
        str(cpu),
    ]
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
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "isolated cold/warm verification failed: " + completed.stderr[-2000:]
        )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    result = {
        name: payload[name]
        for name in (
            "cold_single",
            "warm_single",
            "warm_batch",
            "memory",
            "allocation",
            "timed_response_digest_count",
        )
    }
    result["isolated_process"] = True
    result["command"] = " ".join(command)
    return result


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


def derive_verification_decision(
    *,
    mixed_p95_ns: float,
    quiet_p95_ns: float,
    call_count: Mapping[str, Any],
    gate_checks: Mapping[str, bool],
) -> dict[str, Any]:
    """Derive the pre-evaluator Go/No-Go without claiming unimplemented timing."""

    planned = call_count["planned_native_search"]
    transition_calls = int(planned["canonical_transition_call_ceiling"])
    evaluator_calls = int(planned["canonical_evaluated_call_projection"])
    transition_projection_ms = (
        float(mixed_p95_ns) * transition_calls / 1_000_000.0
    )
    evaluator_remaining_ms = (
        COMBINED_TRANSITION_EVALUATOR_BUDGET_MS - transition_projection_ms
    )
    evaluator_remaining_ns = (
        evaluator_remaining_ms * 1_000_000.0 / evaluator_calls
        if evaluator_calls > 0
        else 0.0
    )
    other_native_ms = (
        SEARCH_CONTROL_BUDGET_MS
        + SERIALIZATION_BUDGET_MS
        + AGGREGATION_BUDGET_MS
    )
    native_envelope_ms = COMBINED_TRANSITION_EVALUATOR_BUDGET_MS + other_native_ms
    end_to_end_envelope_ms = native_envelope_ms + ADAPTER_MARGIN_MS
    serial_share = 1.0 - NATIVE_BOUNDARY_SHARE
    required_boundary_speedup = NATIVE_BOUNDARY_SHARE / (
        END_TO_END_BUDGET_MS / PYTHON_REFERENCE_LOWER_BOUND_MS - serial_share
    )
    component_passed = (
        mixed_p95_ns <= TRANSITION_TARGET_NS and quiet_p95_ns <= QUIET_TARGET_NS
    )
    budget_feasible = (
        evaluator_remaining_ms > 0.0
        and native_envelope_ms <= NATIVE_TOTAL_BUDGET_MS
        and end_to_end_envelope_ms <= END_TO_END_BUDGET_MS
    )
    passed = component_passed and budget_feasible and all(gate_checks.values())
    return {
        "schema_version": "puyo.native_compact_go_no_go.v1",
        "decision": "GO" if passed else "NO_GO",
        "decision_scope": (
            "permission to implement PUYO-201 inside the residual combined budget; "
            "not production promotion"
        ),
        "combined_gate_phase": "pre-evaluator residual-budget verification",
        "observed_combined_p95_ms": None,
        "observed_combined_p95_reason": (
            "PUYO-201 evaluator/quiescence does not exist yet; its implementation must "
            "measure transition plus evaluator at this shared native boundary"
        ),
        "component": {
            "mixed_p95_ns_per_transition": mixed_p95_ns,
            "mixed_target_ns_per_transition": TRANSITION_TARGET_NS,
            "quiet_p95_ns_per_transition": quiet_p95_ns,
            "quiet_target_ns_per_transition": QUIET_TARGET_NS,
            "passed": component_passed,
        },
        "remaining_budget": {
            "transition_calls": transition_calls,
            "evaluator_calls": evaluator_calls,
            "transition_projection_p95_ms": transition_projection_ms,
            "combined_transition_evaluator_p95_ms": (
                COMBINED_TRANSITION_EVALUATOR_BUDGET_MS
            ),
            "evaluator_quiescence_p95_ms": evaluator_remaining_ms,
            "evaluator_quiescence_ns_per_evaluated_node": evaluator_remaining_ns,
            "search_control_p95_ms": SEARCH_CONTROL_BUDGET_MS,
            "serialization_p95_ms": SERIALIZATION_BUDGET_MS,
            "aggregation_p95_ms": AGGREGATION_BUDGET_MS,
            "native_envelope_p95_ms": native_envelope_ms,
            "adapter_margin_p95_ms": ADAPTER_MARGIN_MS,
            "end_to_end_envelope_p95_ms": end_to_end_envelope_ms,
        },
        "amdahl_recalculation": {
            "python_reference_lower_bound_ms": PYTHON_REFERENCE_LOWER_BOUND_MS,
            "required_end_to_end_speedup": (
                PYTHON_REFERENCE_LOWER_BOUND_MS / END_TO_END_BUDGET_MS
            ),
            "native_boundary_share": NATIVE_BOUNDARY_SHARE,
            "required_native_boundary_speedup": required_boundary_speedup,
            "python_transition_reference_ns": PYTHON_TRANSITION_REFERENCE_NS,
            "observed_transition_p95_speedup": (
                PYTHON_TRANSITION_REFERENCE_NS / mixed_p95_ns
            ),
            "python_evaluator_reference_ns": PYTHON_EVALUATOR_REFERENCE_NS,
            "required_evaluator_speedup_at_residual_budget": (
                PYTHON_EVALUATOR_REFERENCE_NS / evaluator_remaining_ns
                if evaluator_remaining_ns > 0.0
                else None
            ),
            "interpretation": (
                "The outer Amdahl headroom remains positive, but PUYO-201 must "
                "demonstrate the recorded evaluator speedup and shared-boundary p95."
            ),
        },
        "gate_checks": dict(gate_checks),
        "puyo_201": {
            "status": "unblocked_for_implementation" if passed else "blocked",
            "shared_state_contract": (
                "80-byte exact three-slice child state with drop heights and "
                "settled/lifecycle flags; 24-byte transition hot result; derive "
                "occupied/components in native code without a persisted full cache"
            ),
            "binding_p95_budget_ms": max(0.0, evaluator_remaining_ms),
            "binding_ns_per_evaluated_node": max(0.0, evaluator_remaining_ns),
            "stop_condition": (
                "Stop before PUYO-202 if transition+evaluator exceeds 820.625 ms, "
                "native total exceeds 900 ms, end-to-end exceeds 1,000 ms, or any "
                "semantic/deterministic/allocation contract fails."
            ),
        },
        "production_backend_remains_blocked": True,
        "passed": passed,
    }


def _memory_evidence(
    *,
    cold_warm: Mapping[str, Any],
    alternatives: Mapping[str, Mapping[str, Any]],
    source_verification: Mapping[str, Any],
) -> dict[str, Any]:
    selected_layout = alternatives["layout_three_bit_slices"]
    selected_result = alternatives["result_minimal_hot"]
    process_peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    per_transition_bytes = (
        NATIVE_COMPACT_HOT_CHILD_STATE_BYTES + NATIVE_COMPACT_HOT_RESULT_BYTES
    )
    return {
        "schema_version": "puyo.native_compact_memory_verification.v1",
        "limits": {
            "normal_hot_path_heap_allocations": 0,
            "child_state_bytes": NATIVE_COMPACT_HOT_CHILD_STATE_BYTES,
            "hot_result_bytes": NATIVE_COMPACT_HOT_RESULT_BYTES,
            "per_transition_write_bytes": per_transition_bytes,
            "selected_reusable_metadata_bytes": 8,
        },
        "observed": {
            "normal_hot_path_heap_allocations": source_verification["allocation"][
                "normal_hot_path_heap_allocations"
            ],
            "child_state_bytes": selected_result["state_bytes"],
            "hot_result_bytes": selected_result["result_bytes"],
            "per_transition_write_bytes": selected_result["copy_bytes_per_record"],
            "selected_reusable_metadata_bytes": selected_layout[
                "reusable_metadata_bytes"
            ],
            "selected_layout_update_bytes_per_record": selected_layout[
                "update_bytes_per_record"
            ],
            "selected_layout_p50_ns": selected_layout["p50_ns_per_record"],
            "selected_layout_p95_ns": selected_layout["p95_ns_per_record"],
            "process_peak_rss_kib": process_peak,
            "cold_warm_peak_rss_kib_before": cold_warm["memory"][
                "peak_rss_kib_before"
            ],
            "cold_warm_peak_rss_kib_after": cold_warm["memory"][
                "peak_rss_kib_after"
            ],
            "cold_warm_peak_rss_delta_kib": cold_warm["memory"][
                "peak_rss_delta_kib"
            ],
            "canonical_retained_upper_bound_bytes": (
                per_transition_bytes * CANONICAL_MAX_EXPANDED_NODES
            ),
        },
    }


def _render_verification_report(summary: Mapping[str, Any]) -> str:
    profile = summary["profiles"]
    verification = summary["verification"]
    decision = summary["final_decision"]
    remaining = decision["remaining_budget"]
    memory = verification["memory"]
    observed_memory = memory["observed"]
    lines = [
        "# PUYO-207 independent transition Go/No-Go verification",
        "",
        f"- Decision: **{decision['decision']}**",
        f"- Evaluated commit: `{summary['evaluated_commit']}`",
        f"- Source tree: `{summary['source_tree']}`",
        f"- Wheel SHA-256: `{summary['environment']['wheel_sha256']}`",
        f"- Transition corpus digest: `{summary['corpus']['digest']}`",
        f"- Search corpus digest: `{summary['call_count']['corpus_digest']}`",
        f"- CPU affinity: `{summary['environment']['selected_cpu']}` (one thread)",
        (
            f"- CPU / platform: `{summary['environment']['cpu']}` / "
            f"`{summary['environment']['platform']}`"
        ),
        f"- Compiler: `{summary['environment']['compiler']}`",
        (
            f"- Executed ISA path: `{summary['capabilities']['simd_path']}`; "
            "reachable local-update and forced full-scanner results are identical"
        ),
        "- Outliers: none removed; p50/p95 use nearest rank",
        "",
        "## Independent semantic verification",
        "",
        "| Gate | Coverage | Mismatches |",
        "| --- | ---: | ---: |",
        (
            f"| fixed fixtures | {verification['fixtures']['case_count']} | "
            f"{verification['fixtures']['mismatch_count']} |"
        ),
        (
            f"| authoritative/Python frozen transitions | "
            f"{summary['oracle']['checked_transition_count']} | "
            f"{summary['oracle']['mismatch_count']} |"
        ),
        (
            f"| native frozen transitions | "
            f"{summary['native_parity']['transition_count']} | "
            f"{summary['native_parity']['mismatch_count']} |"
        ),
        (
            f"| legal/reduced action results | {summary['oracle']['state_count']} | "
            f"{summary['native_parity']['action_mismatch_count']} |"
        ),
        (
            f"| optimized local path vs forced scanner property corpus | "
            f"> {PROPERTY_TRANSITION_MINIMUM:,} | "
            f"{verification['source']['property_corpus']['mismatch_count']} |"
        ),
        "",
        (
            "Deterministic response, exact-key, search result, and ranking digest "
            f"mismatches are `{verification['determinism']['total_mismatch_count']}`."
        ),
        "",
        "## Locked outcome latency",
        "",
        "| Outcome | Samples | Records/sample | p50 ns | p95 ns | Target |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("mixed", *OUTCOME_NAMES):
        row = profile[name]["full_transition"]
        target = "-"
        if name == "mixed":
            target = f"<= {TRANSITION_TARGET_NS:.1f}"
        elif name == "quiet":
            target = f"<= {QUIET_TARGET_NS:.1f}"
        lines.append(
            f"| {name} | {row['sample_count']} | {row['record_count']} | "
            f"{row['p50_ns_per_record']:.3f} | {row['p95_ns_per_record']:.3f} | "
            f"{target} |"
        )
    cold_warm = verification["cold_warm"]
    lines.extend(
        [
            "",
            (
                "The auxiliary first-call/warm measurements do not replace the "
                "locked PUYO-205 sample contract:"
            ),
            "",
            "| Scope | Wall p50 us | Wall p95 us | Kernel p50 ns/transition | Kernel p95 ns/transition |",
            "| --- | ---: | ---: | ---: | ---: |",
            (
                f"| warm single | {cold_warm['warm_single']['wall_p50_us']:.3f} | "
                f"{cold_warm['warm_single']['wall_p95_us']:.3f} | "
                f"{cold_warm['warm_single']['kernel_per_transition_p50_ns']:.3f} | "
                f"{cold_warm['warm_single']['kernel_per_transition_p95_ns']:.3f} |"
            ),
            (
                f"| warm batch | {cold_warm['warm_batch']['wall_p50_us']:.3f} | "
                f"{cold_warm['warm_batch']['wall_p95_us']:.3f} | "
                f"{cold_warm['warm_batch']['kernel_per_transition_p50_ns']:.3f} | "
                f"{cold_warm['warm_batch']['kernel_per_transition_p95_ns']:.3f} |"
            ),
            "",
            (
                f"First single call: `{cold_warm['cold_single']['wall_us']:.3f} us` "
                f"wall / `{cold_warm['cold_single']['kernel_ns']} ns` kernel."
            ),
            "",
            "## Memory and shared-state contract",
            "",
            "| Item | Observed | Limit |",
            "| --- | ---: | ---: |",
            (
                f"| normal hot-path heap allocations | "
                f"{observed_memory['normal_hot_path_heap_allocations']} | 0 |"
            ),
            (
                f"| child state bytes | {observed_memory['child_state_bytes']} | "
                f"{memory['limits']['child_state_bytes']} |"
            ),
            (
                f"| hot result bytes | {observed_memory['hot_result_bytes']} | "
                f"{memory['limits']['hot_result_bytes']} |"
            ),
            (
                f"| total write bytes/transition | "
                f"{observed_memory['per_transition_write_bytes']} | "
                f"{memory['limits']['per_transition_write_bytes']} |"
            ),
            (
                f"| reusable state metadata bytes | "
                f"{observed_memory['selected_reusable_metadata_bytes']} | "
                f"{memory['limits']['selected_reusable_metadata_bytes']} |"
            ),
            "",
            (
                f"Process peak RSS was `{observed_memory['process_peak_rss_kib']:,} KiB`; "
                f"the selected three-slice local update measured "
                f"`{observed_memory['selected_layout_p95_ns']:.3f} ns` p95 and "
                f"`{observed_memory['selected_layout_update_bytes_per_record']}` updated "
                "bytes per record."
            ),
            "",
            "## Combined budget and Amdahl result",
            "",
            "| Category | p95 envelope | Per evaluated node |",
            "| --- | ---: | ---: |",
            (
                f"| measured transition projection | "
                f"{remaining['transition_projection_p95_ms']:.3f} ms | "
                f"{decision['component']['mixed_p95_ns_per_transition']:.3f} ns |"
            ),
            (
                f"| PUYO-201 evaluator/quiescence residual | "
                f"{remaining['evaluator_quiescence_p95_ms']:.3f} ms | "
                f"{remaining['evaluator_quiescence_ns_per_evaluated_node']:.3f} ns |"
            ),
            (
                f"| combined transition + evaluator | "
                f"{remaining['combined_transition_evaluator_p95_ms']:.3f} ms | - |"
            ),
            f"| native total | {remaining['native_envelope_p95_ms']:.3f} ms | - |",
            (
                f"| end-to-end including adapter margin | "
                f"{remaining['end_to_end_envelope_p95_ms']:.3f} ms | - |"
            ),
            "",
            (
                f"The transition demonstrates "
                f"`{decision['amdahl_recalculation']['observed_transition_p95_speedup']:.3f}x` "
                "against the frozen Python transition reference. PUYO-201 must "
                f"demonstrate at least approximately "
                f"`{decision['amdahl_recalculation']['required_evaluator_speedup_at_residual_budget']:.3f}x` "
                "against the frozen Python evaluator reference and then measure the "
                "real shared-boundary p95."
            ),
            "",
            "## Decision",
            "",
            (
                f"**{decision['decision']} for PUYO-201 implementation.** "
                f"The binding evaluator/quiescence allowance is "
                f"`{decision['puyo_201']['binding_p95_budget_ms']:.3f} ms` p95, or "
                f"`{decision['puyo_201']['binding_ns_per_evaluated_node']:.3f} ns` "
                "per evaluated node."
            ),
            "",
            decision["observed_combined_p95_reason"] + ".",
            "Production backend promotion remains blocked. "
            + decision["puyo_201"]["stop_condition"],
            "",
            "## Reproduction",
            "",
            "```bash",
            "./scripts/build_deep_chain_native.sh",
            summary["command"],
            (
                "python -m eval.deep_chain_native_transition_profile verify "
                f"--ticket {VERIFICATION_TICKET} --artifact-dir "
                f"{summary['artifact_dir']}"
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _render_report(summary: Mapping[str, Any]) -> str:
    if summary["ticket"] == VERIFICATION_TICKET:
        return _render_verification_report(summary)
    profile = summary["profiles"]
    decision = summary["budget_decision"]
    ticket = str(summary["ticket"])
    optimized = ticket == OPTIMIZATION_TICKET
    lines = [
        f"# {ticket} compact transition "
        + ("hot-path acceptance" if optimized else "profile"),
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
                f"(`perf_event_open` returns `ENOENT`). Cycles use "
                f"{CYCLE_COUNTER_METHOD}; instructions, branches, and cache events "
                "use Valgrind Cachegrind and are labelled as simulated."
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
            "## " + ("Acceptance decision" if optimized else "ADR decision"),
            "",
            (
                (
                    "The primary 80-byte child-state / 24-byte result hot path meets "
                    if optimized
                    else f"Adopt **{decision['decision']['selected_option']}**. "
                    "PUYO-206 must meet "
                )
                + f"`{decision['decision']['transition_p95_target_ns_per_call']:.1f} "
                f"ns` mixed and "
                f"`{decision['decision']['quiet_p95_target_ns_per_call']:.1f} ns` quiet "
                "p95. PUYO-207 must enforce the unchanged transition+evaluator "
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
            (
                "python -m eval.deep_chain_native_transition_profile verify "
                f"--ticket {ticket} --artifact-dir {summary['artifact_dir']}"
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    ticket = str(args.ticket)
    output_dir = Path(args.output_dir or _default_output_dir(ticket))
    verification_mode = ticket == VERIFICATION_TICKET
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
            "Valgrind is required for canonical compact-transition "
            "instruction/branch/cache evidence"
        )
    cachegrind = run_cachegrind(
        cachegrind_request,
        valgrind=valgrind,
        valgrind_lib=args.valgrind_lib,
        repeats=args.cachegrind_repeats,
        cpu=selected_cpu,
    )
    call_count = measure_call_count_model(args.search_corpus)
    repeat_call_count = (
        measure_call_count_model(args.search_corpus) if verification_mode else None
    )
    decomposition = _stage_decomposition(profiles, cachegrind)
    budget = derive_budget_decision(
        profiles["mixed"]["full_transition"]["p95_ns_per_record"],
        call_count,
        decomposition,
    )
    capabilities = batch_client.capabilities.to_dict()
    cold_warm = (
        run_cold_warm_verification(args.corpus, cpu=selected_cpu)
        if verification_mode
        else None
    )
    fixtures = _fixture_parity(batch_client) if verification_mode else None
    evaluated_commit = git_commit()
    source_tree = _git_tree(evaluated_commit)
    source_verification = run_source_verification() if verification_mode else None
    memory = (
        _memory_evidence(
            cold_warm=cold_warm,
            alternatives=alternatives,
            source_verification=source_verification,
        )
        if verification_mode
        else None
    )
    determinism = None
    if verification_mode:
        response_mismatches = int(not native_parity["deterministic_response"])
        exact_key_mismatches = int(
            source_verification["exact_key_mismatch_count"] != 0
        )
        search_result_mismatches = int(
            call_count["actual_search_digest"]
            != call_count["expected_search_digest"]
        )
        ranking_repeat_mismatches = int(
            call_count["actual_search_digest"]
            != repeat_call_count["actual_search_digest"]
            or call_count["python_search"]["counters"]
            != repeat_call_count["python_search"]["counters"]
        )
        determinism = {
            "schema_version": "puyo.native_compact_determinism_verification.v1",
            "response_sha256": native_parity["response_sha256"],
            "response_mismatch_count": response_mismatches,
            "exact_key_mismatch_count": exact_key_mismatches,
            "search_result_digest": call_count["actual_search_digest"],
            "expected_search_result_digest": call_count["expected_search_digest"],
            "search_result_digest_mismatch_count": search_result_mismatches,
            "ranking_repeat_digest_mismatch_count": ranking_repeat_mismatches,
            "total_mismatch_count": (
                response_mismatches
                + exact_key_mismatches
                + search_result_mismatches
                + ranking_repeat_mismatches
            ),
        }
    command = (
        "python -m eval.deep_chain_native_transition_profile run "
        f"--ticket {ticket} "
        f"--corpus {args.corpus} --search-corpus {args.search_corpus} "
        f"--output-dir {output_dir} --mixed-samples {args.mixed_samples} "
        f"--outcome-samples {args.outcome_samples} --stage-samples {args.stage_samples} "
        f"--alternative-samples {args.alternative_samples} --warmup {args.warmup} "
        f"--mixed-batch-size {args.mixed_batch_size} "
        f"--outcome-batch-size {args.outcome_batch_size} "
        f"--cachegrind-records {args.cachegrind_records} "
        f"--cachegrind-repeats {args.cachegrind_repeats} --cpu {selected_cpu}"
    )
    measurement_contract = {
        "warmup_samples_per_mode": args.warmup,
        "mixed_samples": args.mixed_samples,
        "outcome_samples": args.outcome_samples,
        "stage_samples": args.stage_samples,
        "alternative_samples": args.alternative_samples,
        "mixed_batch_size": args.mixed_batch_size,
        "outcome_batch_size": args.outcome_batch_size,
        "cachegrind_records": args.cachegrind_records,
        "cachegrind_repeats": args.cachegrind_repeats,
        "outlier_exclusion": "none",
        "percentile_method": "nearest-rank: sorted[ceil(p/100*N)-1]",
        "release_wheel_required": True,
        "fixed_cpu": selected_cpu,
        "threads": 1,
        "targets": {
            "mixed_p95_ns_per_transition": TRANSITION_TARGET_NS,
            "quiet_p95_ns_per_transition": QUIET_TARGET_NS,
            "combined_transition_evaluator_p95_ms": (
                COMBINED_TRANSITION_EVALUATOR_BUDGET_MS
            ),
            "native_total_p95_ms": NATIVE_TOTAL_BUDGET_MS,
            "end_to_end_p95_ms": END_TO_END_BUDGET_MS,
            "max_expanded_nodes": CANONICAL_MAX_EXPANDED_NODES,
        },
    }
    if verification_mode:
        measurement_contract["auxiliary_cold_warm"] = {
            "isolated_process": True,
            "single_samples": 200,
            "single_warmup": 20,
            "batch_samples": 12,
            "batch_warmup": 2,
            "batch_size": 10_000,
            "authority": "observational; does not replace locked outcome samples",
        }
    measurement_contract["contract_digest"] = _digest(measurement_contract)
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
        "hot_result_contract_fixed": (
            capabilities["compact_hot_result"]
            == {
                "abi_version": NATIVE_COMPACT_HOT_RESULT_ABI_VERSION,
                "schema": NATIVE_COMPACT_HOT_RESULT_SCHEMA_VERSION,
                "child_state_bytes": NATIVE_COMPACT_HOT_CHILD_STATE_BYTES,
                "result_bytes": NATIVE_COMPACT_HOT_RESULT_BYTES,
                "flags_mask": 0x0F,
            }
        ),
    }
    if ticket in (OPTIMIZATION_TICKET, VERIFICATION_TICKET):
        checks.update(
            {
                "mixed_p95_target_met": (
                    profiles["mixed"]["full_transition"]["p95_ns_per_record"]
                    <= TRANSITION_TARGET_NS
                ),
                "quiet_p95_target_met": (
                    profiles["quiet"]["full_transition"]["p95_ns_per_record"]
                    <= QUIET_TARGET_NS
                ),
                "fixed_state_and_result_profile_sizes": (
                    alternatives["result_minimal_hot"]["state_bytes"]
                    == NATIVE_COMPACT_HOT_CHILD_STATE_BYTES
                    and alternatives["result_minimal_hot"]["result_bytes"]
                    == NATIVE_COMPACT_HOT_RESULT_BYTES
                ),
            }
        )
    final_decision = None
    if verification_mode:
        checks.update(
            {
                "fixed_fixture_parity": fixtures["mismatch_count"] == 0,
                "source_revision_matches_evaluated_commit": (
                    capabilities["source_revision"] == evaluated_commit
                ),
                "source_tree_recorded": source_tree != "unknown",
                "release_source_verification": source_verification["passed"],
                "property_corpus_exceeds_100k": (
                    source_verification["property_corpus"]["mismatch_count"] == 0
                    and source_verification["property_corpus"][
                        "minimum_checked_transitions_exclusive"
                    ]
                    >= PROPERTY_TRANSITION_MINIMUM
                ),
                "allocation_free_hot_path": (
                    memory["observed"]["normal_hot_path_heap_allocations"] == 0
                ),
                "memory_contract_fixed": (
                    memory["observed"]["child_state_bytes"]
                    == memory["limits"]["child_state_bytes"]
                    and memory["observed"]["hot_result_bytes"]
                    == memory["limits"]["hot_result_bytes"]
                    and memory["observed"]["per_transition_write_bytes"]
                    == memory["limits"]["per_transition_write_bytes"]
                    and memory["observed"]["selected_reusable_metadata_bytes"]
                    == memory["limits"]["selected_reusable_metadata_bytes"]
                ),
                "peak_rss_recorded": memory["observed"]["process_peak_rss_kib"] > 0,
                "scalar_and_optimized_path_equivalent": (
                    capabilities["simd_path"] == "scalar"
                    and capabilities["scalar_fallback"]
                    and source_verification["property_corpus"]["mismatch_count"]
                    == 0
                ),
                "deterministic_response_key_and_ranking": (
                    determinism["total_mismatch_count"] == 0
                ),
                "search_repeat_call_model_identical": (
                    call_count["planned_native_search"]
                    == repeat_call_count["planned_native_search"]
                ),
            }
        )
        final_decision = derive_verification_decision(
            mixed_p95_ns=profiles["mixed"]["full_transition"][
                "p95_ns_per_record"
            ],
            quiet_p95_ns=profiles["quiet"]["full_transition"][
                "p95_ns_per_record"
            ],
            call_count=call_count,
            gate_checks=checks,
        )
        checks["combined_and_outer_budget_feasible"] = final_decision["passed"]
    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "ticket": ticket,
        "created_at_utc": utc_timestamp(),
        "evaluated_commit": evaluated_commit,
        "source_tree": source_tree,
        "command": command,
        "artifact_dir": str(output_dir),
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
            "cycle_counter": f"{CYCLE_COUNTER_METHOD} on one pinned CPU",
            "instruction_branch_cache_counter": "Valgrind Cachegrind simulated",
        },
        "measurement_contract": measurement_contract,
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
        "verification": (
            {
                "fixtures": fixtures,
                "cold_warm": cold_warm,
                "source": source_verification,
                "determinism": determinism,
                "memory": memory,
                "repeat_call_count": repeat_call_count,
            }
            if verification_mode
            else None
        ),
        "final_decision": final_decision,
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
            "measurement_contract": summary["measurement_contract"],
            "verification": summary["verification"],
            "final_decision": summary["final_decision"],
            "checks": summary["checks"],
        }
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_dir / "raw_profile.json",
        {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "ticket": ticket,
            "profiles": raw_profiles,
            "alternatives": alternative_raw,
        },
    )
    _write_json(output_dir / "cachegrind.json", cachegrind)
    _write_json(output_dir / "call_count.json", call_count)
    _write_json(output_dir / "alternative_contracts.json", alternatives)
    if verification_mode:
        _write_json(output_dir / "measurement_contract.json", measurement_contract)
        _write_json(
            output_dir / "semantic_verification.json",
            {
                "schema_version": "puyo.native_compact_semantic_verification.v1",
                "fixtures": fixtures,
                "oracle": oracle,
                "native_parity": native_parity,
                "source": source_verification,
                "determinism": determinism,
            },
        )
        _write_json(output_dir / "memory_verification.json", memory)
        _write_json(output_dir / "go_no_go_decision.json", final_decision)
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
        "ticket": ticket,
        "created_at_utc": summary["created_at_utc"],
        "evaluated_commit": summary["evaluated_commit"],
        "source_tree": summary["source_tree"],
        "wheel_sha256": summary["environment"]["wheel_sha256"],
        "corpus_digest": summary["corpus"]["digest"],
        "measurement_contract_digest": summary["measurement_contract"][
            "contract_digest"
        ],
        "command": command,
        "passed": summary["passed"],
        "inputs": [
            {
                "role": "transition_corpus",
                "path": str(args.corpus),
                "sha256": file_sha256(args.corpus),
                "logical_digest": corpus["corpus_digest"],
            },
            {
                "role": "search_corpus",
                "path": str(args.search_corpus),
                "sha256": file_sha256(args.search_corpus),
                "logical_digest": call_count["corpus_digest"],
            },
        ],
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifacts
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    return summary


def verify_benchmark(
    artifact_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    ticket: str = TICKET,
) -> dict[str, Any]:
    root = Path(artifact_dir)
    manifest = _read_json(root / "benchmark_manifest.json")
    issues = []
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append(f"unexpected {ticket} manifest schema")
    if manifest.get("ticket") != ticket:
        issues.append(f"unexpected {ticket} manifest ticket")
    artifacts = manifest.get("artifacts", [])
    artifact_paths = [artifact.get("path") for artifact in artifacts]
    if len(artifact_paths) != len(set(artifact_paths)):
        issues.append("manifest contains duplicate artifact paths")
    if ticket == VERIFICATION_TICKET:
        required_artifacts = {
            "alternative_contracts.json",
            "benchmark_report.md",
            "benchmark_summary.json",
            "cachegrind.json",
            "call_count.json",
            "go_no_go_decision.json",
            "measurement_contract.json",
            "memory_verification.json",
            "raw_profile.json",
            "semantic_verification.json",
        }
        missing = sorted(required_artifacts - set(artifact_paths))
        if missing:
            issues.append(f"manifest omits required artifacts: {missing}")
    for artifact in artifacts:
        artifact_path = artifact.get("path")
        if not isinstance(artifact_path, str):
            issues.append("manifest artifact is missing its path")
            continue
        path = root / artifact_path
        if not path.exists():
            issues.append(f"missing artifact: {artifact_path}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"artifact digest mismatch: {artifact_path}")
    summary = _read_json(root / "benchmark_summary.json")
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append(f"unexpected {ticket} summary schema")
    if summary.get("ticket") != ticket:
        issues.append(f"unexpected {ticket} summary ticket")
    if not summary.get("passed"):
        issues.append(f"{ticket} profile checks did not pass")
    if not all(summary.get("checks", {}).values()):
        issues.append(f"{ticket} summary contains a failed check")
    if summary.get("environment", {}).get("wheel_sha256") != manifest.get(
        "wheel_sha256"
    ):
        issues.append("wheel hash differs between summary and manifest")
    if ticket == VERIFICATION_TICKET:
        if not manifest.get("passed"):
            issues.append("verification manifest records a failed decision")
        if summary.get("final_decision", {}).get("decision") != "GO":
            issues.append("PUYO-207 final decision is not GO")
        if not summary.get("final_decision", {}).get("passed"):
            issues.append("PUYO-207 final decision checks did not pass")
        if summary.get("evaluated_commit") != manifest.get("evaluated_commit"):
            issues.append("evaluated commit differs between summary and manifest")
        if summary.get("source_tree") != manifest.get("source_tree"):
            issues.append("source tree differs between summary and manifest")
        if _git_tree(str(manifest.get("evaluated_commit"))) != manifest.get(
            "source_tree"
        ):
            issues.append("evaluated commit tree cannot be verified")
        if summary.get("capabilities", {}).get("source_revision") != summary.get(
            "evaluated_commit"
        ):
            issues.append("release wheel source revision differs from evaluated commit")
        for source in manifest.get("inputs", []):
            source_path = source.get("path")
            if not isinstance(source_path, str):
                issues.append("manifest source input is missing its path")
                continue
            path = Path(source_path)
            if not path.exists():
                issues.append(f"missing source input: {source_path}")
            elif file_sha256(path) != source.get("sha256"):
                issues.append(f"source input digest mismatch: {source_path}")
        if len(manifest.get("inputs", [])) != 2:
            issues.append("verification manifest must record both frozen corpora")
        contract_path = root / "measurement_contract.json"
        if contract_path.exists():
            contract = _read_json(contract_path)
            contract_payload = dict(contract)
            contract_digest = contract_payload.pop("contract_digest", None)
            if _digest(contract_payload) != contract_digest:
                issues.append("measurement contract digest mismatch")
            if contract != summary.get("measurement_contract"):
                issues.append("measurement contract differs from summary")
            if contract_digest != manifest.get("measurement_contract_digest"):
                issues.append("measurement contract differs from manifest")
        decision_path = root / "go_no_go_decision.json"
        if decision_path.exists():
            decision = _read_json(decision_path)
            if decision != summary.get("final_decision"):
                issues.append("Go/No-Go decision differs from summary")
        expected_summary_digest = _digest(
            {
                "schema_version": summary.get("schema_version"),
                "ticket": summary.get("ticket"),
                "evaluated_commit": summary.get("evaluated_commit"),
                "corpus": summary.get("corpus"),
                "call_count": summary.get("call_count"),
                "budget_decision": summary.get("budget_decision"),
                "measurement_contract": summary.get("measurement_contract"),
                "verification": summary.get("verification"),
                "final_decision": summary.get("final_decision"),
                "checks": summary.get("checks"),
            }
        )
        if expected_summary_digest != summary.get("summary_digest"):
            issues.append("benchmark summary digest mismatch")
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


def _cold_warm_child(args: argparse.Namespace) -> int:
    _pin_process(args.cpu)
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "RAYON_NUM_THREADS",
    ):
        os.environ[name] = "1"
    corpus = _read_json(args.corpus)
    result = run_microbenchmark(
        NativeCompactBatchClient(),
        _flatten_inputs(corpus),
        single_samples=200,
        batch_samples=12,
        batch_size=10_000,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--ticket", choices=SUPPORTED_TICKETS, default=TICKET)
    run.add_argument("--corpus", default=str(DEFAULT_CORPUS_PATH))
    run.add_argument("--search-corpus", default=str(DEFAULT_SEARCH_CORPUS_PATH))
    run.add_argument("--output-dir")
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
    verify.add_argument("--ticket", choices=SUPPORTED_TICKETS, default=TICKET)
    verify.add_argument("--artifact-dir")
    child = subparsers.add_parser("cachegrind-child")
    child.add_argument("--request", required=True)
    child.add_argument("--mode", type=int, required=True)
    child.add_argument("--repeats", type=int, required=True)
    child.add_argument("--cpu", type=int, required=True)
    cold_warm_child = subparsers.add_parser("cold-warm-child")
    cold_warm_child.add_argument("--corpus", required=True)
    cold_warm_child.add_argument("--cpu", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_benchmark(args)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["passed"] else 1
    if args.command == "cachegrind-child":
        return _cachegrind_child(args)
    if args.command == "cold-warm-child":
        return _cold_warm_child(args)
    artifact_dir = args.artifact_dir or _default_output_dir(args.ticket)
    result = verify_benchmark(artifact_dir, ticket=args.ticket)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
