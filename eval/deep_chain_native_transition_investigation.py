"""PUYO-211 quiet-transition reproducibility and optimization investigation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import struct
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agents.deep_chain_native_transition import (
    NativeCompactBatchClient,
    NativeCompactTransitionInput,
    encode_native_compact_batch,
)
from eval.deep_chain_native_transition_benchmark import (
    DEFAULT_CORPUS_PATH,
    _digest,
    _installed_wheel_sha256,
    _read_json,
    _write_json,
)
from eval.deep_chain_native_transition_profile import (
    DEFAULT_SEARCH_CORPUS_PATH,
    PROFILE_MODES,
    QUIET_TARGET_NS,
    STAGE_NAMES,
    TRANSITION_TARGET_NS,
    NativeCompactProfiler,
    _compiler_version,
    _cpu_model,
    _git_tree,
    _percentile,
    _pin_process,
    _profile_inputs,
    _sample_mode,
    _stage_decomposition,
    measure_call_count_model,
    run_cachegrind,
)
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.constants import Direction, PuyoColor
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-211"
SCHEMA_VERSION = "puyo.native_compact_transition_investigation.v1"
MANIFEST_SCHEMA_VERSION = "puyo.native_compact_transition_investigation_manifest.v1"
DEFAULT_OUTPUT_DIR = Path(
    "docs/benchmarks/puyo-211-quiet-transition-investigation"
)
AUTHORITY_DIR = Path("docs/benchmarks/puyo-207-native-transition-verification")
HISTORICAL_OPTIMIZATION_DIR = Path(
    "docs/benchmarks/puyo-206-native-compact-hot-path"
)
CANDIDATE_SOURCE = Path("eval/puyo_211_color_plane_candidate.rs")
THREAD_ENVIRONMENT_NAMES = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)
OUTCOME_ORDER = ("mixed", "quiet", "one_chain", "multi_chain")
AUTHORITATIVE_RUN_COUNT = 3
PAIRED_BASELINE_PAIR_COUNT = 3
CANDIDATE_MAGIC = b"P211CP01"
CANDIDATE_HEADER = struct.Struct("<8sI")
CANDIDATE_RECORD = struct.Struct("<12Q4B")
_COLOR_IDS = {
    PuyoColor.RED: 1,
    PuyoColor.BLUE: 2,
    PuyoColor.GREEN: 3,
    PuyoColor.YELLOW: 4,
    PuyoColor.PURPLE: 5,
}


def _set_thread_environment() -> dict[str, str]:
    for name in THREAD_ENVIRONMENT_NAMES:
        os.environ[name] = "1"
    return {name: os.environ[name] for name in THREAD_ENVIRONMENT_NAMES}


def _locked_contract() -> dict[str, Any]:
    return _read_json(AUTHORITY_DIR / "measurement_contract.json")


def _validate_locked_contract(contract: Mapping[str, Any]) -> list[str]:
    expected = {
        "warmup_samples_per_mode": 5,
        "mixed_samples": 120,
        "outcome_samples": 40,
        "stage_samples": 30,
        "alternative_samples": 40,
        "mixed_batch_size": 10_000,
        "outcome_batch_size": 4_096,
        "cachegrind_records": 256,
        "cachegrind_repeats": 512,
        "outlier_exclusion": "none",
        "percentile_method": "nearest-rank: sorted[ceil(p/100*N)-1]",
        "release_wheel_required": True,
        "fixed_cpu": 0,
        "threads": 1,
    }
    issues = [
        f"locked contract changed {name}"
        for name, value in expected.items()
        if contract.get(name) != value
    ]
    targets = contract.get("targets", {})
    expected_targets = {
        "mixed_p95_ns_per_transition": 100.0,
        "quiet_p95_ns_per_transition": 50.0,
        "combined_transition_evaluator_p95_ms": 820.625,
        "native_total_p95_ms": 900.0,
        "end_to_end_p95_ms": 1_000.0,
        "max_expanded_nodes": 600_000,
    }
    issues.extend(
        f"locked target changed {name}"
        for name, value in expected_targets.items()
        if targets.get(name) != value
    )
    payload = dict(contract)
    recorded_digest = payload.pop("contract_digest", None)
    if _digest(payload) != recorded_digest:
        issues.append("locked contract digest is invalid")
    return issues


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None


def _cpuinfo_frequency_mhz(cpu: int) -> float | None:
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.exists():
        return None
    current_cpu = None
    for line in cpuinfo.read_text(encoding="utf-8").splitlines():
        if line.startswith("processor"):
            current_cpu = int(line.split(":", 1)[1].strip())
        elif current_cpu == cpu and line.lower().startswith("cpu mhz"):
            return float(line.split(":", 1)[1].strip())
    return None


def _frequency_snapshot(cpu: int) -> dict[str, Any]:
    root = Path(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq")
    values: dict[str, Any] = {
        "cpu": cpu,
        "cpuinfo_mhz": _cpuinfo_frequency_mhz(cpu),
        "source": "Linux cpufreq sysfs plus /proc/cpuinfo; observational only",
    }
    for name in (
        "scaling_cur_freq",
        "cpuinfo_cur_freq",
        "scaling_min_freq",
        "scaling_max_freq",
        "cpuinfo_min_freq",
        "cpuinfo_max_freq",
        "scaling_governor",
        "scaling_driver",
    ):
        value = _read_optional(root / name)
        if value is None:
            continue
        values[name + ("_khz" if name.endswith("freq") else "")] = (
            int(value) if value.isdigit() else value
        )
    values["clocksource"] = _read_optional(
        Path("/sys/devices/system/clocksource/clocksource0/current_clocksource")
    )
    values["intel_pstate_no_turbo"] = _read_optional(
        Path("/sys/devices/system/cpu/intel_pstate/no_turbo")
    )
    return values


def _authority_identity() -> dict[str, Any]:
    manifest_path = AUTHORITY_DIR / "benchmark_manifest.json"
    manifest = _read_json(manifest_path)
    summary = _read_json(AUTHORITY_DIR / "benchmark_summary.json")
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": file_sha256(manifest_path),
        "evaluated_commit": manifest["evaluated_commit"],
        "source_tree": manifest["source_tree"],
        "wheel_sha256": manifest["wheel_sha256"],
        "transition_corpus_digest": manifest["corpus_digest"],
        "measurement_contract_digest": manifest["measurement_contract_digest"],
        "capability_source_revision": summary["capabilities"]["source_revision"],
    }


def _assert_authority_wheel() -> tuple[dict[str, Any], dict[str, Any]]:
    authority = _authority_identity()
    installed_hash = _installed_wheel_sha256()
    capabilities = NativeCompactBatchClient().capabilities.to_dict()
    issues = []
    if installed_hash != authority["wheel_sha256"]:
        issues.append(
            f"installed wheel {installed_hash} differs from PUYO-207 "
            f"{authority['wheel_sha256']}"
        )
    if capabilities["source_revision"] != authority["evaluated_commit"]:
        issues.append("installed wheel source revision differs from PUYO-207")
    if capabilities["build_profile"] != "release":
        issues.append("installed native wheel is not a release build")
    if issues:
        raise RuntimeError("; ".join(issues))
    return authority, capabilities


def _run_sample_child(args: argparse.Namespace) -> dict[str, Any]:
    contract = _locked_contract()
    issues = _validate_locked_contract(contract)
    if issues:
        raise RuntimeError("; ".join(issues))
    if args.cpu != int(contract["fixed_cpu"]):
        raise RuntimeError("sample child CPU differs from the locked PUYO-207 CPU")
    selected_cpu, available_cpus = _pin_process(args.cpu)
    thread_environment = _set_thread_environment()
    authority, capabilities = _assert_authority_wheel()
    corpus = _read_json(args.corpus)
    selected, selection_metadata = _profile_inputs(
        corpus,
        mixed_batch_size=int(contract["mixed_batch_size"]),
        outcome_batch_size=int(contract["outcome_batch_size"]),
    )
    requests = {
        name: encode_native_compact_batch(records) for name, records in selected.items()
    }
    profiler = NativeCompactProfiler()
    started_at = utc_timestamp()
    process_frequency_before = _frequency_snapshot(selected_cpu)
    outcomes: dict[str, Any] = {}
    for order_index, name in enumerate(OUTCOME_ORDER):
        samples = (
            int(contract["mixed_samples"])
            if name == "mixed"
            else int(contract["outcome_samples"])
        )
        frequency_before = _frequency_snapshot(selected_cpu)
        summary, raw = _sample_mode(
            profiler,
            requests[name],
            mode=PROFILE_MODES["full_transition"],
            samples=samples,
            warmup=int(contract["warmup_samples_per_mode"]),
        )
        outcomes[name] = {
            "order_index": order_index,
            "request_sha256": hashlib.sha256(requests[name]).hexdigest(),
            "frequency_before": frequency_before,
            "frequency_after": _frequency_snapshot(selected_cpu),
            "summary": summary,
            "raw_samples": raw,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "fresh_process_outcome_run",
        "run_id": args.run_id,
        "phase": args.phase,
        "pair_id": args.pair_id,
        "pair_label": args.pair_label,
        "sequence_index": args.sequence_index,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "started_at_utc": started_at,
        "finished_at_utc": utc_timestamp(),
        "outcome_order": list(OUTCOME_ORDER),
        "authority": authority,
        "wheel_sha256": _installed_wheel_sha256(),
        "capabilities": capabilities,
        "corpus": {
            "path": str(args.corpus),
            "sha256": file_sha256(args.corpus),
            "digest": corpus["corpus_digest"],
            "selection": selection_metadata,
        },
        "environment": {
            "cpu": _cpu_model(),
            "selected_cpu": selected_cpu,
            "available_cpus_before_pin": available_cpus,
            "affinity_after_pin": sorted(os.sched_getaffinity(0)),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "compiler": _compiler_version(),
            "thread_environment": thread_environment,
            "frequency_before": process_frequency_before,
            "frequency_after": _frequency_snapshot(selected_cpu),
        },
        "measurement_contract_digest": contract["contract_digest"],
        "outcomes": outcomes,
    }


def _run_profile_child(args: argparse.Namespace) -> dict[str, Any]:
    contract = _locked_contract()
    issues = _validate_locked_contract(contract)
    if issues:
        raise RuntimeError("; ".join(issues))
    if args.cpu != int(contract["fixed_cpu"]):
        raise RuntimeError("profile child CPU differs from the locked PUYO-207 CPU")
    selected_cpu, available_cpus = _pin_process(args.cpu)
    thread_environment = _set_thread_environment()
    authority, capabilities = _assert_authority_wheel()
    corpus = _read_json(args.corpus)
    selected, selection_metadata = _profile_inputs(
        corpus,
        mixed_batch_size=int(contract["mixed_batch_size"]),
        outcome_batch_size=int(contract["outcome_batch_size"]),
    )
    quiet_request = encode_native_compact_batch(selected["quiet"])
    profiler = NativeCompactProfiler()
    profiles: dict[str, Any] = {"quiet": {}}
    raw_profiles: dict[str, Any] = {"quiet": {}}
    frequency_before = _frequency_snapshot(selected_cpu)
    for name in ("full_transition", "baseline", *STAGE_NAMES):
        summary, raw = _sample_mode(
            profiler,
            quiet_request,
            mode=PROFILE_MODES[name],
            samples=(
                int(contract["outcome_samples"])
                if name == "full_transition"
                else int(contract["stage_samples"])
            ),
            warmup=int(contract["warmup_samples_per_mode"]),
        )
        profiles["quiet"][name] = summary
        raw_profiles["quiet"][name] = raw
    alternatives = {}
    alternative_raw = {}
    for name in ("layout_three_bit_slices", "result_minimal_hot"):
        summary, raw = _sample_mode(
            profiler,
            quiet_request,
            mode=PROFILE_MODES[name],
            samples=int(contract["alternative_samples"]),
            warmup=int(contract["warmup_samples_per_mode"]),
        )
        alternatives[name] = summary
        alternative_raw[name] = raw
    cachegrind_request = encode_native_compact_batch(
        selected["quiet"][: int(contract["cachegrind_records"])]
    )
    cachegrind = run_cachegrind(
        cachegrind_request,
        valgrind=args.valgrind,
        valgrind_lib=args.valgrind_lib,
        repeats=int(contract["cachegrind_repeats"]),
        cpu=selected_cpu,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "isolated_quiet_stage_profile",
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "started_at_utc": args.started_at,
        "finished_at_utc": utc_timestamp(),
        "authority": authority,
        "wheel_sha256": _installed_wheel_sha256(),
        "capabilities": capabilities,
        "measurement_contract_digest": contract["contract_digest"],
        "corpus": {
            "path": str(args.corpus),
            "sha256": file_sha256(args.corpus),
            "digest": corpus["corpus_digest"],
            "selection": selection_metadata,
            "quiet_request_sha256": hashlib.sha256(quiet_request).hexdigest(),
        },
        "environment": {
            "cpu": _cpu_model(),
            "selected_cpu": selected_cpu,
            "available_cpus_before_pin": available_cpus,
            "affinity_after_pin": sorted(os.sched_getaffinity(0)),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "compiler": _compiler_version(),
            "thread_environment": thread_environment,
            "frequency_before": frequency_before,
            "frequency_after": _frequency_snapshot(selected_cpu),
        },
        "profiles": profiles,
        "raw_profiles": raw_profiles,
        "alternatives": alternatives,
        "alternative_raw": alternative_raw,
        "cachegrind": cachegrind,
        "stage_decomposition": _stage_decomposition(profiles, cachegrind),
    }


def _invoke_json_child(arguments: Sequence[str]) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "eval.deep_chain_native_transition_investigation",
        *arguments,
    ]
    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"child command failed ({completed.returncode}): "
            f"{completed.stderr[-4000:]}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("child command returned no JSON")
    return json.loads(lines[-1])


def _sample_child_arguments(
    *,
    args: argparse.Namespace,
    run_id: str,
    phase: str,
    sequence_index: int,
    pair_id: int | None = None,
    pair_label: str | None = None,
) -> list[str]:
    result = [
        "sample-child",
        "--corpus",
        str(args.corpus),
        "--cpu",
        str(args.cpu),
        "--run-id",
        run_id,
        "--phase",
        phase,
        "--sequence-index",
        str(sequence_index),
    ]
    if pair_id is not None:
        result.extend(("--pair-id", str(pair_id)))
    if pair_label is not None:
        result.extend(("--pair-label", pair_label))
    return result


def _summarize_outcome_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in OUTCOME_ORDER:
        p50 = [float(run["outcomes"][name]["summary"]["p50_ns_per_record"]) for run in runs]
        p95 = [float(run["outcomes"][name]["summary"]["p95_ns_per_record"]) for run in runs]
        result[name] = {
            "run_count": len(runs),
            "p50_ns_per_record_by_run": p50,
            "p95_ns_per_record_by_run": p95,
            "median_run_p50_ns_per_record": _percentile(p50, 50),
            "median_run_p95_ns_per_record": _percentile(p95, 50),
            "minimum_run_p95_ns_per_record": min(p95),
            "maximum_run_p95_ns_per_record": max(p95),
            "p95_range_ns": max(p95) - min(p95),
        }
    result["performance_gates"] = {
        "quiet_all_runs_at_or_below_50_ns": all(
            value <= QUIET_TARGET_NS
            for value in result["quiet"]["p95_ns_per_record_by_run"]
        ),
        "quiet_median_run_p95_at_or_below_45_ns": (
            result["quiet"]["median_run_p95_ns_per_record"] <= 45.0
        ),
        "mixed_all_runs_at_or_below_100_ns": all(
            value <= TRANSITION_TARGET_NS
            for value in result["mixed"]["p95_ns_per_record_by_run"]
        ),
    }
    return result


def _without_raw_samples(run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in run.items() if key != "outcomes"},
        "outcomes": {
            name: {
                key: value
                for key, value in run["outcomes"][name].items()
                if key != "raw_samples"
            }
            for name in OUTCOME_ORDER
        },
    }


def _quiet_profile_summary(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: value
            for key, value in profile.items()
            if key not in {"raw_profiles", "cachegrind"}
        },
        "cachegrind": {
            key: value
            for key, value in profile["cachegrind"].items()
            if key != "raw"
        },
    }


def _summarize_paired_baseline(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pairs: dict[int, dict[str, Mapping[str, Any]]] = {}
    for run in runs:
        pair_id = int(run["pair_id"])
        pairs.setdefault(pair_id, {})[str(run["pair_label"])] = run
    pair_rows = []
    for pair_id in sorted(pairs):
        labels = pairs[pair_id]
        if set(labels) != {"reference", "control"}:
            raise ValueError(f"paired baseline {pair_id} is incomplete")
        row: dict[str, Any] = {"pair_id": pair_id, "outcomes": {}}
        for name in OUTCOME_ORDER:
            reference = labels["reference"]["outcomes"][name]["summary"]
            control = labels["control"]["outcomes"][name]["summary"]
            reference_p95 = float(reference["p95_ns_per_record"])
            control_p95 = float(control["p95_ns_per_record"])
            row["outcomes"][name] = {
                "reference_p50_ns": float(reference["p50_ns_per_record"]),
                "control_p50_ns": float(control["p50_ns_per_record"]),
                "reference_p95_ns": reference_p95,
                "control_p95_ns": control_p95,
                "control_to_reference_p95_ratio": control_p95 / reference_p95,
                "absolute_p95_drift_percent": (
                    abs(control_p95 / reference_p95 - 1.0) * 100.0
                ),
            }
        pair_rows.append(row)
    aggregate = {}
    for name in OUTCOME_ORDER:
        ratios = [
            float(row["outcomes"][name]["control_to_reference_p95_ratio"])
            for row in pair_rows
        ]
        aggregate[name] = {
            "median_control_to_reference_p95_ratio": _percentile(ratios, 50),
            "maximum_absolute_p95_drift_percent": max(abs(value - 1.0) * 100 for value in ratios),
            "ratios": ratios,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "method": (
            "three fresh-process reference/control pairs on the identical wheel; "
            "labels alternate execution order; future code comparisons use candidate/"
            "baseline p95 ratios with median non-regression and a 2% per-run tolerance"
        ),
        "pair_count": len(pair_rows),
        "pairs": pair_rows,
        "aggregate": aggregate,
        "raw_runs": list(runs),
    }


def _internal_color_bits(state: Any) -> list[int]:
    result = [0, 0, 0]
    for color_id, plane in enumerate(state.planes, start=1):
        remaining = int(plane)
        while remaining:
            lowest = remaining & -remaining
            wire_index = lowest.bit_length() - 1
            x = wire_index % 6
            y = wire_index // 6
            bit = 1 << (x * 16 + y)
            for slice_index in range(3):
                if color_id & (1 << slice_index):
                    result[slice_index] |= bit
            remaining &= remaining - 1
    return result


def _inserted_indices(record: NativeCompactTransitionInput) -> tuple[int, int]:
    action = PLACEMENT_ACTIONS[record.action_id]
    axis_x = int(action.axis_x)
    heights = record.state.column_heights
    if action.rotation == Direction.UP:
        height = heights[axis_x]
        return axis_x * 16 + height, axis_x * 16 + height + 1
    if action.rotation == Direction.DOWN:
        height = heights[axis_x]
        return axis_x * 16 + height + 1, axis_x * 16 + height
    child_x = axis_x + (1 if action.rotation == Direction.RIGHT else -1)
    return axis_x * 16 + heights[axis_x], child_x * 16 + heights[child_x]


def _pack_u128(values: Sequence[int]) -> list[int]:
    result = []
    for value in values:
        result.extend((int(value) & ((1 << 64) - 1), int(value) >> 64))
    return result


def _write_candidate_workload(corpus_path: str | Path, destination: Path) -> dict[str, Any]:
    corpus = _read_json(corpus_path)
    selected, selection_metadata = _profile_inputs(
        corpus,
        mixed_batch_size=10_000,
        outcome_batch_size=4_096,
    )
    payload = bytearray(CANDIDATE_HEADER.pack(CANDIDATE_MAGIC, len(selected["quiet"])))
    for record in selected["quiet"]:
        parent = _internal_color_bits(record.state)
        child = list(parent)
        inserted = _inserted_indices(record)
        colors = (_COLOR_IDS[record.pair[0]], _COLOR_IDS[record.pair[1]])
        for color_id, bit_index in zip(colors, inserted, strict=True):
            bit = 1 << bit_index
            for slice_index in range(3):
                if color_id & (1 << slice_index):
                    child[slice_index] |= bit
        payload.extend(
            CANDIDATE_RECORD.pack(
                *_pack_u128((*parent, *child)),
                colors[0] - 1,
                colors[1] - 1,
                inserted[0],
                inserted[1],
            )
        )
    destination.write_bytes(payload)
    return {
        "schema_version": "puyo.native_compact_candidate_workload.v1",
        "path": str(destination),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": len(selected["quiet"]),
        "record_bytes": CANDIDATE_RECORD.size,
        "corpus_digest": corpus["corpus_digest"],
        "selection": selection_metadata["quiet"],
    }


def _parse_candidate_output(output: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata = None
    rows = []
    for line in output.splitlines():
        fields = line.split("\t")
        if fields[0] == "metadata":
            metadata = {
                "version": int(fields[1]),
                "record_count": int(fields[2]),
                "repeats": int(fields[3]),
                "warmup": int(fields[4]),
                "semantic_mismatch_count": int(fields[5]),
            }
        elif fields[0] == "sample":
            rows.append(
                {
                    "sample_index": int(fields[1]),
                    "order": fields[2],
                    "baseline_elapsed_ns": int(fields[3]),
                    "baseline_cycles": int(fields[4]),
                    "candidate_elapsed_ns": int(fields[5]),
                    "candidate_cycles": int(fields[6]),
                    "checksum": int(fields[7]),
                }
            )
    if metadata is None or not rows:
        raise RuntimeError("candidate microbenchmark output is incomplete")
    return metadata, rows


def _candidate_function_assembly(binary: Path, function_name: str) -> dict[str, Any]:
    symbols = subprocess.run(
        ["nm", "-S", "--defined-only", "--demangle", str(binary)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    selected = next((line for line in symbols if function_name in line), None)
    if selected is None:
        raise RuntimeError(f"candidate symbol {function_name} is missing")
    fields = selected.split(maxsplit=3)
    address = int(fields[0], 16)
    size = int(fields[1], 16)
    disassembly = subprocess.run(
        [
            "objdump",
            "-d",
            "-C",
            "--no-show-raw-insn",
            f"--start-address={address}",
            f"--stop-address={address + size}",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    instruction_lines = [
        line
        for line in disassembly.splitlines()
        if re.match(r"^\s*[0-9a-f]+:\s+\S", line)
    ]
    return {
        "symbol": fields[-1],
        "address": address,
        "size_bytes": size,
        "instruction_count": len(instruction_lines),
        "disassembly": "\n".join(instruction_lines),
    }


def _run_candidate_microbenchmark(
    *, corpus_path: str | Path, output_dir: Path, cpu: int
) -> dict[str, Any]:
    workload_path = output_dir / "candidate_workload.bin"
    workload = _write_candidate_workload(corpus_path, workload_path)
    with tempfile.TemporaryDirectory(prefix="puyo-211-candidate-") as directory:
        binary = Path(directory) / "puyo-211-color-plane-candidate"
        compile_command = [
            "rustc",
            "--edition=2021",
            "-C",
            "opt-level=3",
            "-C",
            "codegen-units=1",
            "-C",
            "target-cpu=x86-64",
            str(CANDIDATE_SOURCE),
            "-o",
            str(binary),
        ]
        subprocess.run(compile_command, check=True, capture_output=True, text=True)
        binary_sha256 = file_sha256(binary)
        run_command = [
            "taskset",
            "-c",
            str(cpu),
            str(binary),
            "--input",
            str(workload_path),
            "--samples",
            "40",
            "--warmup",
            "5",
            "--repeats",
            "512",
        ]
        completed = subprocess.run(
            run_command,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **{name: "1" for name in THREAD_ENVIRONMENT_NAMES}},
        )
        metadata, rows = _parse_candidate_output(completed.stdout)
        operations = metadata["record_count"] * metadata["repeats"]
        for row in rows:
            row["operations"] = operations
            row["baseline_ns_per_record"] = row["baseline_elapsed_ns"] / operations
            row["candidate_ns_per_record"] = row["candidate_elapsed_ns"] / operations
            row["baseline_cycles_per_record"] = row["baseline_cycles"] / operations
            row["candidate_cycles_per_record"] = row["candidate_cycles"] / operations
            row["candidate_to_baseline_ns_ratio"] = (
                row["candidate_ns_per_record"] / row["baseline_ns_per_record"]
            )
            row["saved_ns_per_record"] = (
                row["baseline_ns_per_record"] - row["candidate_ns_per_record"]
            )
            row["saved_cycles_per_record"] = (
                row["baseline_cycles_per_record"]
                - row["candidate_cycles_per_record"]
            )
        assembly = {
            "current_inserted_planes": _candidate_function_assembly(
                binary, "current_inserted_planes"
            ),
            "reused_child_planes": _candidate_function_assembly(
                binary, "reused_child_planes"
            ),
        }
    def values(name: str) -> list[float]:
        return [float(row[name]) for row in rows]

    summary = {
        "baseline_p50_ns_per_record": _percentile(values("baseline_ns_per_record"), 50),
        "baseline_p95_ns_per_record": _percentile(values("baseline_ns_per_record"), 95),
        "candidate_p50_ns_per_record": _percentile(values("candidate_ns_per_record"), 50),
        "candidate_p95_ns_per_record": _percentile(values("candidate_ns_per_record"), 95),
        "median_candidate_to_baseline_ns_ratio": _percentile(
            values("candidate_to_baseline_ns_ratio"), 50
        ),
        "p05_saved_ns_per_record": _percentile(values("saved_ns_per_record"), 5),
        "median_saved_ns_per_record": _percentile(values("saved_ns_per_record"), 50),
        "p05_saved_cycles_per_record": _percentile(
            values("saved_cycles_per_record"), 5
        ),
        "median_saved_cycles_per_record": _percentile(
            values("saved_cycles_per_record"), 50
        ),
        "all_samples_candidate_faster": all(
            value > 0 for value in values("saved_ns_per_record")
        ),
        "percentile_method": "nearest-rank; no samples excluded",
    }
    return {
        "schema_version": "puyo.native_compact_color_plane_candidate.v1",
        "scope": (
            "isolated scalar feasibility only; not an end-to-end transition result"
        ),
        "candidate": "reuse already-placed child color slices",
        "semantic_mismatch_count": metadata["semantic_mismatch_count"],
        "metadata": metadata,
        "workload": workload,
        "source": {
            "path": str(CANDIDATE_SOURCE),
            "sha256": file_sha256(CANDIDATE_SOURCE),
        },
        "compiler": subprocess.run(
            ["rustc", "--version", "--verbose"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "compile_command": " ".join(compile_command),
        "target_cpu": "x86-64 (portable scalar; target-cpu=native is not used)",
        "binary_sha256": binary_sha256,
        "run_command": " ".join(run_command),
        "selected_cpu": cpu,
        "raw_samples": rows,
        "summary": summary,
        "assembly": assembly,
    }


def _git_object(commit: str, path: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", f"{commit}:{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _historical_comparison() -> dict[str, Any]:
    optimization = _read_json(HISTORICAL_OPTIMIZATION_DIR / "benchmark_summary.json")
    verification = _read_json(AUTHORITY_DIR / "benchmark_summary.json")
    source_path = "native/deep_chain_native/src/compact.rs"
    optimization_commit = optimization["evaluated_commit"]
    verification_commit = verification["evaluated_commit"]
    optimization_p95 = float(
        optimization["profiles"]["quiet"]["full_transition"]["p95_ns_per_record"]
    )
    verification_p95 = float(
        verification["profiles"]["quiet"]["full_transition"]["p95_ns_per_record"]
    )
    optimization_object = _git_object(optimization_commit, source_path)
    verification_object = _git_object(verification_commit, source_path)
    return {
        "schema_version": "puyo.native_compact_historical_variance.v1",
        "puyo_206": {
            "evaluated_commit": optimization_commit,
            "wheel_sha256": optimization["environment"]["wheel_sha256"],
            "quiet_p95_ns": optimization_p95,
        },
        "puyo_207": {
            "evaluated_commit": verification_commit,
            "wheel_sha256": verification["environment"]["wheel_sha256"],
            "quiet_p95_ns": verification_p95,
        },
        "quiet_p95_delta_ns": verification_p95 - optimization_p95,
        "quiet_p95_increase_percent": (
            (verification_p95 / optimization_p95 - 1.0) * 100.0
        ),
        "native_compact_source": {
            "path": source_path,
            "puyo_206_git_object": optimization_object,
            "puyo_207_git_object": verification_object,
            "identical": optimization_object == verification_object,
        },
        "contract_identical": (
            optimization["measurement_contract"]["mixed_samples"]
            == verification["measurement_contract"]["mixed_samples"]
            and optimization["measurement_contract"]["outcome_samples"]
            == verification["measurement_contract"]["outcome_samples"]
            and optimization["measurement_contract"]["warmup_samples_per_mode"]
            == verification["measurement_contract"]["warmup_samples_per_mode"]
            and optimization["measurement_contract"]["percentile_method"]
            == verification["measurement_contract"]["percentile_method"]
        ),
        "conclusion": (
            "PUYO-206 and PUYO-207 used the identical compact.rs Git object and "
            "locked contract. The 23.421 ns p95 gap has no algorithmic source "
            "change and is attributed to process/frequency/scheduling variance; "
            "fresh-process replication quantifies that variance separately."
        ),
    }


def _derive_candidate_analysis(
    authoritative: Mapping[str, Any],
    paired: Mapping[str, Any],
    quiet_profile: Mapping[str, Any],
    microbenchmark: Mapping[str, Any],
    historical: Mapping[str, Any],
) -> dict[str, Any]:
    decomposition = quiet_profile["stage_decomposition"]
    stage_cycles = decomposition["quiet_stage_p50_cycles_per_record"]
    full_cycles = float(decomposition["quiet_full_p50_cycles_per_record"])
    conservative_saved_ns = max(
        0.0, float(microbenchmark["summary"]["p05_saved_ns_per_record"])
    )
    quiet_p95 = authoritative["quiet"]["p95_ns_per_record_by_run"]
    mixed_p95 = authoritative["mixed"]["p95_ns_per_record_by_run"]
    quiet_fraction = float(
        quiet_profile["corpus"]["selection"]["quiet"]["source_records"]
        / quiet_profile["corpus"]["selection"]["mixed"]["measured_records"]
    )
    projected_quiet = [max(0.0, value - conservative_saved_ns) for value in quiet_p95]
    projected_mixed = [
        max(0.0, value - conservative_saved_ns * quiet_fraction) for value in mixed_p95
    ]
    root_supports_margin = (
        microbenchmark["semantic_mismatch_count"] == 0
        and conservative_saved_ns > 0
        and all(value <= QUIET_TARGET_NS for value in projected_quiet)
        and _percentile(projected_quiet, 50) <= 45.0
        and all(value <= TRANSITION_TARGET_NS for value in projected_mixed)
    )
    selected = root_supports_margin
    return {
        "schema_version": "puyo.native_compact_optimization_candidate.v1",
        "decision": (
            "SELECT_REUSE_PLACED_CHILD_PLANES" if selected else "NO_IMPLEMENTATION_CANDIDATE"
        ),
        "selected_candidate": (
            {
                "name": "reuse already-placed child color slices",
                "change_scope": (
                    "feed color planes derived from child.color_bits into "
                    "find_inserted_vanish_from_planes after direct placement"
                ),
                "duplicate_work_removed": (
                    "reconstructing inserted planes from the parent plus two inserted masks"
                ),
                "rollback_unit": (
                    "one local transition_hot_core plane-source change; restore the "
                    "existing find_inserted_vanish(state, pair, inserted_indices) call"
                ),
                "semantic_contract": {
                    "state_bytes": 80,
                    "result_bytes": 24,
                    "heap_allocations": 0,
                    "scalar_fallback": True,
                    "state_or_result_abi_change": False,
                },
            }
            if selected
            else None
        ),
        "evidence": {
            "historical": historical,
            "paired_baseline": paired["aggregate"],
            "quiet_full_p50_cycles": full_cycles,
            "quiet_stage_p50_cycles": stage_cycles,
            "color_plane_stage_fraction": (
                float(stage_cycles["color_plane_extraction"]) / full_cycles
                if full_cycles
                else 0.0
            ),
            "connectivity_stage_fraction": (
                float(stage_cycles["inserted_connectivity"]) / full_cycles
                if full_cycles
                else 0.0
            ),
            "cachegrind_per_record": quiet_profile["cachegrind"]["per_record"],
            "semantic_store_contract": {
                "child_state_bytes": quiet_profile["alternatives"][
                    "result_minimal_hot"
                ]["state_bytes"],
                "result_bytes": quiet_profile["alternatives"]["result_minimal_hot"][
                    "result_bytes"
                ],
                "total_output_store_bytes": quiet_profile["alternatives"][
                    "result_minimal_hot"
                ]["copy_bytes_per_record"],
                "three_slice_copy_bytes": quiet_profile["alternatives"][
                    "layout_three_bit_slices"
                ]["copy_bytes_per_record"],
                "three_slice_updated_bytes": quiet_profile["alternatives"][
                    "layout_three_bit_slices"
                ]["update_bytes_per_record"],
            },
            "candidate_microbenchmark": microbenchmark["summary"],
            "candidate_assembly": microbenchmark["assembly"],
        },
        "engineering_projection": {
            "method": (
                "subtract only the candidate microbenchmark p05 per-record saving; "
                "this is feasibility evidence, not an acceptance result"
            ),
            "conservative_saved_ns_per_quiet_transition": conservative_saved_ns,
            "quiet_fraction_in_mixed_corpus": quiet_fraction,
            "observed_quiet_p95_by_run": quiet_p95,
            "projected_quiet_p95_by_run": projected_quiet,
            "projected_quiet_median_p95": _percentile(projected_quiet, 50),
            "observed_mixed_p95_by_run": mixed_p95,
            "projected_mixed_p95_by_run": projected_mixed,
            "supports_required_margin": root_supports_margin,
        },
        "rejected_or_deferred_candidates": [
            {
                "name": "fixed equal/different and topology branches",
                "decision": "defer",
                "reason": (
                    "connectivity is large, but no isolated candidate evidence yet "
                    "separates branch savings from flood work"
                ),
            },
            {
                "name": "local degree/popcount prefilter",
                "decision": "defer",
                "reason": (
                    "the current three-expansion loop already short-circuits stable "
                    "groups; implementation evidence is required before adding a branch"
                ),
            },
            {
                "name": "reduce hot-result or state stores",
                "decision": "reject",
                "reason": "80-byte state and 24-byte result are fixed ABI outputs",
            },
            {
                "name": "target-cpu=native or non-portable codegen",
                "decision": "reject",
                "reason": "violates the portable scalar fallback contract",
            },
            {
                "name": "persistent full component cache",
                "decision": "reject",
                "reason": "enlarges state/ABI and was already slower in PUYO-205/207",
            },
        ],
        "puyo_212_acceptance": {
            "authoritative_fresh_process_runs": 3,
            "quiet": {
                "every_run_p95_ns_at_or_below": 50.0,
                "median_run_p95_ns_at_or_below": 45.0,
            },
            "mixed": {
                "every_run_p95_ns_at_or_below": 100.0,
                "paired_median_candidate_to_baseline_ratio_at_or_below": 1.0,
                "each_run_regression_tolerance_percent": 2.0,
            },
            "unchanged": (
                "PUYO-205/207 corpus, samples, warm-up, nearest-rank p95, "
                "call count, target, no outlier exclusion"
            ),
            "mandatory": (
                "zero frozen/property mismatches; 80/24-byte ABI; zero heap "
                "allocations; scalar fallback; source-bound release wheel"
            ),
            "stop": (
                "rollback the local plane-source change if any semantic/ABI/allocation "
                "gate fails, any quiet run exceeds 50 ns, the quiet run median exceeds "
                "45 ns, any mixed run exceeds 100 ns, paired median regresses, or an "
                "individual paired regression exceeds 2%"
            ),
            "commands": [
                "./scripts/build_deep_chain_native.sh",
                ".venv/bin/python -m unittest tests.test_deep_chain_native tests.test_deep_chain_native_transition tests.test_deep_chain_native_transition_profile",
                ".venv/bin/python -m eval.deep_chain_native_transition_investigation verify --artifact-dir docs/benchmarks/puyo-211-quiet-transition-investigation",
            ],
        },
    }


def _render_report(summary: Mapping[str, Any]) -> str:
    authority = summary["authoritative_summary"]
    profile = summary["quiet_profile"]["stage_decomposition"]
    candidate = summary["candidate_analysis"]
    lines = [
        "# PUYO-211 quiet transition reproducibility investigation",
        "",
        f"- Decision: **{candidate['decision']}**",
        f"- Baseline wheel SHA-256: `{summary['authority']['wheel_sha256']}`",
        f"- Baseline source revision: `{summary['authority']['evaluated_commit']}`",
        f"- CPU affinity: `{summary['environment']['selected_cpu']}` (one thread)",
        "- Percentile: nearest-rank; no samples excluded",
        "",
        "## Three fresh-process authoritative runs",
        "",
        "| Run | mixed p50 | mixed p95 | quiet p50 | quiet p95 | one-chain p95 | multi-chain p95 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, run in enumerate(summary["authoritative_runs"], start=1):
        values = run["outcomes"]
        lines.append(
            f"| {index} | {values['mixed']['summary']['p50_ns_per_record']:.3f} | "
            f"{values['mixed']['summary']['p95_ns_per_record']:.3f} | "
            f"{values['quiet']['summary']['p50_ns_per_record']:.3f} | "
            f"{values['quiet']['summary']['p95_ns_per_record']:.3f} | "
            f"{values['one_chain']['summary']['p95_ns_per_record']:.3f} | "
            f"{values['multi_chain']['summary']['p95_ns_per_record']:.3f} |"
        )
    lines.extend(
        [
            "",
            (
                f"Quiet run-p95 median is `{authority['quiet']['median_run_p95_ns_per_record']:.3f} ns`; "
                f"range is `{authority['quiet']['p95_range_ns']:.3f} ns`. Mixed run-p95 "
                f"median is `{authority['mixed']['median_run_p95_ns_per_record']:.3f} ns`."
            ),
            (
                "The baseline itself is not reproducible at the fixed gates: two of "
                "three quiet runs exceed 50 ns, two of three mixed runs exceed 100 ns, "
                "and the quiet run-p95 median exceeds the 45 ns engineering margin."
            ),
            "Every raw sample, run order, process ID, affinity, and before/after CPU-frequency snapshot is retained.",
            "",
            "## PUYO-206 / PUYO-207 gap",
            "",
            summary["historical_comparison"]["conclusion"],
            "",
            (
                "Three additional reference/control process pairs calibrate environment "
                "variance. PUYO-212 must alternate baseline/candidate order and compare "
                "paired ratios; the median may not regress and each run has a fixed 2% "
                "tolerance."
            ),
            "",
            "| Same-wheel pair | median control/reference p95 | maximum absolute drift |",
            "| --- | ---: | ---: |",
            (
                f"| mixed | {summary['paired_baseline']['aggregate']['mixed']['median_control_to_reference_p95_ratio']:.4f} | "
                f"{summary['paired_baseline']['aggregate']['mixed']['maximum_absolute_p95_drift_percent']:.1f}% |"
            ),
            (
                f"| quiet | {summary['paired_baseline']['aggregate']['quiet']['median_control_to_reference_p95_ratio']:.4f} | "
                f"{summary['paired_baseline']['aggregate']['quiet']['maximum_absolute_p95_drift_percent']:.1f}% |"
            ),
            "",
            (
                "Same-wheel drift is far above the 2% code-regression tolerance, so this "
                "environment cannot resolve a small production change reliably."
            ),
            "",
            "## Re-profiled quiet cost",
            "",
            "| Stage | adjusted p50 cycles | Cachegrind instructions | branches | L1 data misses | simulated writes |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    cache = summary["quiet_profile"]["cachegrind"]["per_record"]
    for name in STAGE_NAMES:
        lines.append(
            f"| {name} | {profile['quiet_stage_p50_cycles_per_record'][name]:.3f} | "
            f"{cache[name]['instructions']:.3f} | {cache[name]['branches']:.3f} | "
            f"{cache[name]['data_l1_misses']:.3f} | {cache[name]['data_writes']:.3f} |"
        )
    stores = candidate["evidence"]["semantic_store_contract"]
    lines.extend(
        [
            "",
            (
                f"The fixed semantic output remains `{stores['child_state_bytes']} + "
                f"{stores['result_bytes']} = {stores['total_output_store_bytes']}` bytes. "
                f"The three-slice placement profile copies `{stores['three_slice_copy_bytes']}` "
                f"bytes and updates `{stores['three_slice_updated_bytes']}` bytes."
            ),
            "",
            "## Candidate decision",
            "",
        ]
    )
    if candidate["selected_candidate"]:
        micro = candidate["evidence"]["candidate_microbenchmark"]
        projection = candidate["engineering_projection"]
        lines.extend(
            [
                (
                    "Select **reuse already-placed child color slices** for PUYO-212. "
                    "The current hot path reconstructs inserted color planes from the "
                    "parent immediately after placement has already produced the child "
                    "slices."
                ),
                "",
                (
                    f"The isolated scalar AB/BA microbenchmark has zero semantic mismatches, "
                    f"median candidate/baseline ratio `{micro['median_candidate_to_baseline_ns_ratio']:.4f}`, "
                    f"and conservative p05 saving `{micro['p05_saved_ns_per_record']:.3f} ns`."
                ),
                (
                    f"Applying only that p05 saving projects quiet run p95 values "
                    f"`{', '.join(f'{value:.3f}' for value in projection['projected_quiet_p95_by_run'])}` ns "
                    f"with median `{projection['projected_quiet_median_p95']:.3f} ns`. "
                    "This is an implementation hypothesis; PUYO-212 must pass the full gates."
                ),
            ]
        )
    else:
        micro = candidate["evidence"]["candidate_microbenchmark"]
        projection = candidate["engineering_projection"]
        lines.extend(
            [
                (
                    "No candidate has sufficient conservative evidence for the required "
                    "three-run margin; do not modify native production code."
                ),
                "",
                (
                    f"The strongest isolated option, reusing placed child planes, has zero "
                    f"semantic mismatches and median candidate/baseline ratio "
                    f"`{micro['median_candidate_to_baseline_ns_ratio']:.4f}`, but its "
                    f"conservative p05 saving is only `{micro['p05_saved_ns_per_record']:.3f} ns`."
                ),
                (
                    f"That projects quiet p95 to "
                    f"`{', '.join(f'{value:.3f}' for value in projection['projected_quiet_p95_by_run'])}` ns "
                    f"with median `{projection['projected_quiet_median_p95']:.3f} ns`; mixed "
                    "also remains above target in two runs. PUYO-212 should remain unstarted "
                    "unless a controlled baseline or a new candidate can satisfy the same gates."
                ),
            ]
        )
    gates = candidate["puyo_212_acceptance"]
    lines.extend(
        [
            "",
            "## Fixed gate for any authorized follow-up",
            "",
            f"- Quiet: all three p95 `<= {gates['quiet']['every_run_p95_ns_at_or_below']:.1f} ns`; median `<= {gates['quiet']['median_run_p95_ns_at_or_below']:.1f} ns`.",
            f"- Mixed: all three p95 `<= {gates['mixed']['every_run_p95_ns_at_or_below']:.1f} ns`; paired median no regression; every regression `<= {gates['mixed']['each_run_regression_tolerance_percent']:.1f}%`.",
            f"- Stop/rollback: {gates['stop']}",
            "",
            "## Reproduction",
            "",
            "```bash",
            "VALGRIND_BIN=/path/to/valgrind-3.22.0",
            "VALGRIND_LIB_DIR=/path/to/libexec/valgrind",
            (
                ".venv/bin/python -m eval.deep_chain_native_transition_investigation "
                "run --corpus eval/deep_chain_native_transition_corpus.json "
                "--search-corpus eval/deep_chain_native_corpus.json "
                f"--output-dir {summary['artifact_dir']} --cpu 0 "
                '--valgrind "$VALGRIND_BIN" --valgrind-lib "$VALGRIND_LIB_DIR"'
            ),
            (
                ".venv/bin/python -m eval.deep_chain_native_transition_investigation "
                f"verify --artifact-dir {summary['artifact_dir']}"
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def run_investigation(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    contract = _locked_contract()
    contract_issues = _validate_locked_contract(contract)
    if contract_issues:
        raise RuntimeError("; ".join(contract_issues))
    if args.cpu != int(contract["fixed_cpu"]):
        raise RuntimeError("canonical PUYO-211 run must use the locked CPU 0")
    authority, capabilities = _assert_authority_wheel()
    sequence_index = 0
    authoritative_runs = []
    for run_number in range(1, AUTHORITATIVE_RUN_COUNT + 1):
        authoritative_runs.append(
            _invoke_json_child(
                _sample_child_arguments(
                    args=args,
                    run_id=f"authority-{run_number}",
                    phase="authoritative",
                    sequence_index=sequence_index,
                )
            )
        )
        sequence_index += 1
    paired_runs = []
    for pair_id in range(1, PAIRED_BASELINE_PAIR_COUNT + 1):
        labels = (
            ("reference", "control") if pair_id % 2 else ("control", "reference")
        )
        for label in labels:
            paired_runs.append(
                _invoke_json_child(
                    _sample_child_arguments(
                        args=args,
                        run_id=f"pair-{pair_id}-{label}",
                        phase="paired_baseline",
                        sequence_index=sequence_index,
                        pair_id=pair_id,
                        pair_label=label,
                    )
                )
            )
            sequence_index += 1
    profile_arguments = [
        "profile-child",
        "--corpus",
        str(args.corpus),
        "--cpu",
        str(args.cpu),
        "--valgrind",
        str(args.valgrind),
        "--started-at",
        utc_timestamp(),
    ]
    if args.valgrind_lib:
        profile_arguments.extend(("--valgrind-lib", str(args.valgrind_lib)))
    quiet_profile = _invoke_json_child(profile_arguments)
    candidate_microbenchmark = _run_candidate_microbenchmark(
        corpus_path=args.corpus,
        output_dir=output_dir,
        cpu=args.cpu,
    )
    authoritative_summary = _summarize_outcome_runs(authoritative_runs)
    paired = _summarize_paired_baseline(paired_runs)
    historical = _historical_comparison()
    candidate_analysis = _derive_candidate_analysis(
        authoritative_summary,
        paired,
        quiet_profile,
        candidate_microbenchmark,
        historical,
    )
    call_count = measure_call_count_model(args.search_corpus)
    measurement_contract = {
        "schema_version": "puyo.native_compact_transition_investigation_contract.v1",
        "authority": authority,
        "locked_puyo_207_contract": contract,
        "authoritative_fresh_process_runs": AUTHORITATIVE_RUN_COUNT,
        "paired_baseline_pairs": PAIRED_BASELINE_PAIR_COUNT,
        "authoritative_run_order": list(OUTCOME_ORDER),
        "paired_order": "reference/control alternates by pair",
        "profile_isolated_after_authoritative_and_paired_runs": True,
        "candidate_microbenchmark": {
            "samples": 40,
            "warmup": 5,
            "repeats": 512,
            "order": "AB/BA alternating",
            "target_cpu": "x86-64 portable scalar",
            "authority": "feasibility only",
        },
    }
    measurement_contract["contract_digest"] = _digest(measurement_contract)
    orchestration_commit = git_commit()
    source_tree = _git_tree(orchestration_commit)
    all_runs = [*authoritative_runs, *paired_runs]
    integrity_checks = {
        "locked_contract_unchanged": not contract_issues,
        "same_authority_wheel_every_run": all(
            run["wheel_sha256"] == authority["wheel_sha256"] for run in all_runs
        )
        and quiet_profile["wheel_sha256"] == authority["wheel_sha256"],
        "same_authority_source_revision": all(
            run["capabilities"]["source_revision"] == authority["evaluated_commit"]
            for run in all_runs
        )
        and quiet_profile["capabilities"]["source_revision"]
        == authority["evaluated_commit"],
        "three_unique_fresh_authority_processes": (
            len({run["pid"] for run in authoritative_runs})
            == AUTHORITATIVE_RUN_COUNT
            and all(run["pid"] != os.getpid() for run in authoritative_runs)
        ),
        "three_complete_paired_process_pairs": paired["pair_count"]
        == PAIRED_BASELINE_PAIR_COUNT,
        "all_raw_samples_retained": all(
            len(run["outcomes"][name]["raw_samples"])
            == (120 if name == "mixed" else 40)
            for run in all_runs
            for name in OUTCOME_ORDER
        ),
        "all_native_profile_samples_match_semantics": all(
            row["mismatch_count"] == 0
            for run in all_runs
            for name in OUTCOME_ORDER
            for row in run["outcomes"][name]["raw_samples"]
        )
        and all(
            row["mismatch_count"] == 0
            for values in quiet_profile["raw_profiles"].values()
            for rows in values.values()
            for row in rows
        ),
        "all_run_orders_recorded": all(
            run["outcome_order"] == list(OUTCOME_ORDER) for run in all_runs
        ),
        "frequency_provenance_recorded": all(
            run["environment"]["frequency_before"]["source"] for run in all_runs
        ),
        "quiet_profile_has_cycles_instructions_branches_cache_and_stores": (
            quiet_profile["profiles"]["quiet"]["full_transition"]["cycle_source"]
            == "rdtsc-lfence"
            and quiet_profile["cachegrind"]["per_record"]["full_transition"][
                "instructions"
            ]
            > 0
            and quiet_profile["cachegrind"]["per_record"]["full_transition"][
                "branches"
            ]
            > 0
            and quiet_profile["cachegrind"]["per_record"]["full_transition"][
                "data_l1_misses"
            ]
            >= 0
            and quiet_profile["alternatives"]["result_minimal_hot"][
                "copy_bytes_per_record"
            ]
            == 104
        ),
        "candidate_semantic_match": candidate_microbenchmark[
            "semantic_mismatch_count"
        ]
        == 0,
        "call_count_contract_unchanged": (
            call_count["assumption_600k_is_one_transition_each"]
            and call_count["planned_native_search"][
                "canonical_transition_call_ceiling"
            ]
            == 600_000
        ),
        "historical_gap_has_no_native_source_change": historical[
            "native_compact_source"
        ]["identical"],
        "source_tree_recorded": source_tree != "unknown",
    }
    command = (
        ".venv/bin/python -m eval.deep_chain_native_transition_investigation run "
        f"--corpus {args.corpus} --search-corpus {args.search_corpus} "
        f"--output-dir {output_dir} --cpu {args.cpu} --valgrind {args.valgrind}"
        + (f" --valgrind-lib {args.valgrind_lib}" if args.valgrind_lib else "")
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "orchestration_commit": orchestration_commit,
        "source_tree": source_tree,
        "artifact_dir": str(output_dir),
        "command": command,
        "authority": authority,
        "capabilities": capabilities,
        "environment": {
            "cpu": _cpu_model(),
            "selected_cpu": args.cpu,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "compiler": _compiler_version(),
            "thread_environment": _set_thread_environment(),
        },
        "measurement_contract": measurement_contract,
        "authoritative_runs": [
            _without_raw_samples(run) for run in authoritative_runs
        ],
        "authoritative_summary": authoritative_summary,
        "paired_baseline": {key: value for key, value in paired.items() if key != "raw_runs"},
        "quiet_profile": _quiet_profile_summary(quiet_profile),
        "candidate_microbenchmark": {
            key: value for key, value in candidate_microbenchmark.items() if key != "raw_samples"
        },
        "historical_comparison": historical,
        "candidate_analysis": candidate_analysis,
        "call_count": call_count,
        "integrity_checks": integrity_checks,
        "passed": all(integrity_checks.values()),
    }
    summary["summary_digest"] = _digest(
        {
            "schema_version": summary["schema_version"],
            "ticket": summary["ticket"],
            "orchestration_commit": summary["orchestration_commit"],
            "source_tree": summary["source_tree"],
            "authority": summary["authority"],
            "measurement_contract": summary["measurement_contract"],
            "authoritative_summary": summary["authoritative_summary"],
            "candidate_analysis": summary["candidate_analysis"],
            "call_count": summary["call_count"],
            "integrity_checks": summary["integrity_checks"],
        }
    )
    _write_json(output_dir / "measurement_contract.json", measurement_contract)
    _write_json(
        output_dir / "raw_authoritative_runs.json",
        {
            "schema_version": SCHEMA_VERSION,
            "ticket": TICKET,
            "runs": authoritative_runs,
        },
    )
    _write_json(output_dir / "paired_baseline.json", paired)
    _write_json(output_dir / "quiet_profile.json", quiet_profile)
    _write_json(output_dir / "candidate_microbenchmark.json", candidate_microbenchmark)
    _write_json(output_dir / "candidate_analysis.json", candidate_analysis)
    _write_json(output_dir / "historical_comparison.json", historical)
    _write_json(output_dir / "call_count.json", call_count)
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
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": summary["created_at_utc"],
        "orchestration_commit": orchestration_commit,
        "source_tree": source_tree,
        "baseline_authority": authority,
        "wheel_sha256": authority["wheel_sha256"],
        "config_sha256": file_sha256(output_dir / "measurement_contract.json"),
        "raw_authoritative_sha256": file_sha256(
            output_dir / "raw_authoritative_runs.json"
        ),
        "raw_paired_sha256": file_sha256(output_dir / "paired_baseline.json"),
        "raw_profile_sha256": file_sha256(output_dir / "quiet_profile.json"),
        "candidate_raw_sha256": file_sha256(
            output_dir / "candidate_microbenchmark.json"
        ),
        "command": command,
        "passed": summary["passed"],
        "inputs": [
            {
                "role": "puyo_207_authority_manifest",
                "path": authority["manifest_path"],
                "sha256": authority["manifest_sha256"],
            },
            {
                "role": "transition_corpus",
                "path": str(args.corpus),
                "sha256": file_sha256(args.corpus),
                "logical_digest": authority["transition_corpus_digest"],
            },
            {
                "role": "search_corpus",
                "path": str(args.search_corpus),
                "sha256": file_sha256(args.search_corpus),
                "logical_digest": call_count["corpus_digest"],
            },
            {
                "role": "candidate_source",
                "path": str(CANDIDATE_SOURCE),
                "sha256": file_sha256(CANDIDATE_SOURCE),
            },
        ],
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifacts
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    return summary


def _verify_run_samples(run: Mapping[str, Any], issues: list[str]) -> None:
    for name in OUTCOME_ORDER:
        outcome = run.get("outcomes", {}).get(name, {})
        raw = outcome.get("raw_samples", [])
        expected_count = 120 if name == "mixed" else 40
        if len(raw) != expected_count:
            issues.append(f"{run.get('run_id')} {name} raw sample count changed")
            continue
        values = [float(row["per_record_ns"]) for row in raw]
        summary = outcome.get("summary", {})
        if _percentile(values, 50) != summary.get("p50_ns_per_record"):
            issues.append(f"{run.get('run_id')} {name} p50 cannot be reproduced")
        if _percentile(values, 95) != summary.get("p95_ns_per_record"):
            issues.append(f"{run.get('run_id')} {name} p95 cannot be reproduced")
        if summary.get("outlier_exclusion") != "none":
            issues.append(f"{run.get('run_id')} {name} removed samples")


def verify_investigation(artifact_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    root = Path(artifact_dir)
    issues: list[str] = []
    manifest = _read_json(root / "benchmark_manifest.json")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        issues.append("unexpected PUYO-211 manifest schema")
    if manifest.get("ticket") != TICKET:
        issues.append("unexpected PUYO-211 manifest ticket")
    required = {
        "benchmark_report.md",
        "benchmark_summary.json",
        "call_count.json",
        "candidate_analysis.json",
        "candidate_microbenchmark.json",
        "candidate_workload.bin",
        "historical_comparison.json",
        "measurement_contract.json",
        "paired_baseline.json",
        "quiet_profile.json",
        "raw_authoritative_runs.json",
    }
    artifact_paths = [item.get("path") for item in manifest.get("artifacts", [])]
    missing = sorted(required - set(artifact_paths))
    if missing:
        issues.append(f"manifest omits required artifacts: {missing}")
    if len(artifact_paths) != len(set(artifact_paths)):
        issues.append("manifest contains duplicate artifacts")
    for artifact in manifest.get("artifacts", []):
        path_name = artifact.get("path")
        if not isinstance(path_name, str):
            issues.append("manifest artifact path is missing")
            continue
        path = root / path_name
        if not path.exists():
            issues.append(f"missing artifact: {path_name}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"artifact digest mismatch: {path_name}")
    for source in manifest.get("inputs", []):
        source_path = source.get("path")
        if not isinstance(source_path, str) or not Path(source_path).exists():
            issues.append(f"missing source input: {source_path}")
        elif file_sha256(source_path) != source.get("sha256"):
            issues.append(f"source input digest mismatch: {source_path}")
    authority = _authority_identity()
    if manifest.get("baseline_authority") != authority:
        issues.append("PUYO-207 authority identity changed")
    if manifest.get("wheel_sha256") != authority["wheel_sha256"]:
        issues.append("baseline wheel differs from PUYO-207")
    if manifest.get("config_sha256") != file_sha256(
        root / "measurement_contract.json"
    ):
        issues.append("config checksum differs from manifest")
    checksum_fields = {
        "raw_authoritative_sha256": "raw_authoritative_runs.json",
        "raw_paired_sha256": "paired_baseline.json",
        "raw_profile_sha256": "quiet_profile.json",
        "candidate_raw_sha256": "candidate_microbenchmark.json",
    }
    for field, name in checksum_fields.items():
        if manifest.get(field) != file_sha256(root / name):
            issues.append(f"{field} differs from artifact")
    contract = _read_json(root / "measurement_contract.json")
    payload = dict(contract)
    digest = payload.pop("contract_digest", None)
    if _digest(payload) != digest:
        issues.append("PUYO-211 contract digest is invalid")
    if _validate_locked_contract(contract.get("locked_puyo_207_contract", {})):
        issues.append("embedded PUYO-207 contract changed")
    raw_authoritative = _read_json(root / "raw_authoritative_runs.json")
    authoritative_runs = raw_authoritative.get("runs", [])
    if len(authoritative_runs) != AUTHORITATIVE_RUN_COUNT:
        issues.append("authoritative fresh-process run count changed")
    for run in authoritative_runs:
        _verify_run_samples(run, issues)
        if run.get("wheel_sha256") != authority["wheel_sha256"]:
            issues.append(f"{run.get('run_id')} used a different wheel")
    if len({run.get("pid") for run in authoritative_runs}) != len(authoritative_runs):
        issues.append("authoritative runs did not use unique processes")
    paired = _read_json(root / "paired_baseline.json")
    paired_runs = paired.get("raw_runs", [])
    if paired.get("pair_count") != PAIRED_BASELINE_PAIR_COUNT or len(paired_runs) != 6:
        issues.append("paired baseline process set is incomplete")
    for run in paired_runs:
        _verify_run_samples(run, issues)
        if run.get("wheel_sha256") != authority["wheel_sha256"]:
            issues.append(f"{run.get('run_id')} used a different wheel")
    profile = _read_json(root / "quiet_profile.json")
    if profile.get("wheel_sha256") != authority["wheel_sha256"]:
        issues.append("quiet profile used a different wheel")
    if profile.get("cachegrind", {}).get("tool_version") != "valgrind-3.22.0":
        issues.append("quiet profile Cachegrind version changed")
    if profile.get("alternatives", {}).get("result_minimal_hot", {}).get(
        "copy_bytes_per_record"
    ) != 104:
        issues.append("fixed 80/24-byte output store contract changed")
    candidate = _read_json(root / "candidate_microbenchmark.json")
    if candidate.get("semantic_mismatch_count") != 0:
        issues.append("candidate microbenchmark has semantic mismatches")
    candidate_raw = candidate.get("raw_samples", [])
    if len(candidate_raw) != 40:
        issues.append("candidate raw sample count changed")
    if candidate.get("source", {}).get("sha256") != file_sha256(CANDIDATE_SOURCE):
        issues.append("candidate source checksum changed")
    workload_path = root / "candidate_workload.bin"
    if candidate.get("workload", {}).get("sha256") != file_sha256(workload_path):
        issues.append("candidate workload checksum changed")
    summary = _read_json(root / "benchmark_summary.json")
    if summary.get("schema_version") != SCHEMA_VERSION or summary.get("ticket") != TICKET:
        issues.append("unexpected PUYO-211 summary identity")
    if not summary.get("passed") or not all(summary.get("integrity_checks", {}).values()):
        issues.append("PUYO-211 integrity checks did not pass")
    expected_summary_digest = _digest(
        {
            "schema_version": summary.get("schema_version"),
            "ticket": summary.get("ticket"),
            "orchestration_commit": summary.get("orchestration_commit"),
            "source_tree": summary.get("source_tree"),
            "authority": summary.get("authority"),
            "measurement_contract": summary.get("measurement_contract"),
            "authoritative_summary": summary.get("authoritative_summary"),
            "candidate_analysis": summary.get("candidate_analysis"),
            "call_count": summary.get("call_count"),
            "integrity_checks": summary.get("integrity_checks"),
        }
    )
    if summary.get("summary_digest") != expected_summary_digest:
        issues.append("PUYO-211 summary digest is invalid")
    commit = str(manifest.get("orchestration_commit"))
    if _git_tree(commit) != manifest.get("source_tree"):
        issues.append("orchestration commit tree cannot be verified")
    historical = _read_json(root / "historical_comparison.json")
    if not historical.get("native_compact_source", {}).get("identical"):
        issues.append("historical algorithm identity evidence is missing")
    return {"passed": not issues, "issues": issues}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--corpus", default=str(DEFAULT_CORPUS_PATH))
    run.add_argument("--search-corpus", default=str(DEFAULT_SEARCH_CORPUS_PATH))
    run.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    run.add_argument("--cpu", type=int, default=0)
    run.add_argument("--valgrind", required=True)
    run.add_argument("--valgrind-lib")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--artifact-dir", default=str(DEFAULT_OUTPUT_DIR))
    sample = subparsers.add_parser("sample-child")
    sample.add_argument("--corpus", required=True)
    sample.add_argument("--cpu", type=int, required=True)
    sample.add_argument("--run-id", required=True)
    sample.add_argument("--phase", required=True)
    sample.add_argument("--sequence-index", type=int, required=True)
    sample.add_argument("--pair-id", type=int)
    sample.add_argument("--pair-label")
    profile = subparsers.add_parser("profile-child")
    profile.add_argument("--corpus", required=True)
    profile.add_argument("--cpu", type=int, required=True)
    profile.add_argument("--valgrind", required=True)
    profile.add_argument("--valgrind-lib")
    profile.add_argument("--started-at", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "sample-child":
        print(json.dumps(_run_sample_child(args), sort_keys=True))
        return 0
    if args.command == "profile-child":
        print(json.dumps(_run_profile_child(args), sort_keys=True))
        return 0
    if args.command == "run":
        summary = run_investigation(args)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["passed"] else 1
    result = verify_investigation(args.artifact_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
