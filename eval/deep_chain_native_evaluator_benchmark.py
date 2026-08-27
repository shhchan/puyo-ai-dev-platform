"""PUYO-201 bounded native transition-plus-evaluator prototype evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import platform
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agents.chain_structure import (
    ChainStructureAction,
    ChainStructureEvaluator,
    load_chain_structure_config,
)
from agents.compact_search import CompactSearchState, transition
from agents.deep_chain_native import InvalidNativeInputError
from agents.deep_chain_native_evaluator import (
    NativeChainStructureBatchClient,
    NativeChainStructureInput,
    decode_native_combined_profile,
    encode_native_chain_structure_batch,
    materialize_native_chain_structure_result,
)
from agents.deep_chain_native_transition import NativeCompactBatchClient
from eval.deep_chain_native_transition_benchmark import (
    _pair_from_names,
    _state_from_hex,
    evaluate_native_parity,
)
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-201"
SUMMARY_SCHEMA_VERSION = "puyo.native_chain_structure_benchmark.v1"
MANIFEST_SCHEMA_VERSION = "puyo.native_chain_structure_benchmark_manifest.v1"
MEASUREMENT_SCHEMA_VERSION = "puyo.native_chain_structure_measurement.v1"
SEMANTIC_SCHEMA_VERSION = "puyo.native_chain_structure_semantic_verification.v1"
PROFILE_SCHEMA_VERSION = "puyo.native_chain_structure_raw_profile.v1"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_PATH = ROOT / "eval" / "deep_chain_native_transition_corpus.json"
DEFAULT_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "chain_structure_cases.json"
DEFAULT_CONFIG_PATH = ROOT / "train" / "config" / "v1_7_chain_structure.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "benchmarks" / "puyo-201-native-chain-structure"
NATIVE_MANIFEST_PATH = ROOT / "native" / "deep_chain_native" / "Cargo.toml"

EXPANDED_NODE_COUNT = 600_000
PROFILE_SAMPLES = 5
WARMUP_OPERATIONS = 10_000
COMBINED_P95_MAX_MS = 820.625
NATIVE_TOTAL_P95_MAX_MS = 900.0
END_TO_END_P95_MAX_MS = 1_000.0
REQUIRED_CHILD_STATE_BYTES = 80
REQUIRED_HOT_RESULT_BYTES = 24

_CHAR_TO_PLANE = {"R": 0, "B": 1, "G": 2, "Y": 3, "P": 4, "O": 5}


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def nearest_rank(values: Sequence[int | float], percentile: int) -> float:
    if not values or not 1 <= percentile <= 100:
        raise ValueError("nearest-rank percentile input is invalid")
    ordered = sorted(float(value) for value in values)
    return ordered[math.ceil(percentile / 100.0 * len(ordered)) - 1]


def _fixture_states(path: str | Path) -> dict[str, CompactSearchState]:
    payload = _read_json(path)
    if payload.get("schema_version") != "puyo.chain_structure_fixtures.v1":
        raise ValueError("unsupported chain-structure fixture schema")
    states = {}
    for case in payload["cases"]:
        planes = [0] * 6
        rows = case["rows_bottom_up"]
        if len(rows) != 14 or any(len(row) != 6 for row in rows):
            raise ValueError("chain-structure fixture board is not 6x14")
        for y, row in enumerate(rows):
            for x, value in enumerate(row):
                if value != ".":
                    planes[_CHAR_TO_PLANE[value]] |= 1 << (y * 6 + x)
        states[str(case["id"])] = CompactSearchState(tuple(planes))
    return states


def _selected_source_inputs(
    corpus: Mapping[str, Any],
) -> tuple[NativeChainStructureInput, ...]:
    return tuple(
        NativeChainStructureInput(
            _state_from_hex(record["state"]),
            pair=_pair_from_names(record["pair"]),
            action_id=int(record["selected_action"]),
        )
        for record in corpus["records"]
    )


def _semantic_verification(
    *,
    corpus: Mapping[str, Any],
    fixture_path: str | Path,
    module: Any,
    ticket: str = TICKET,
) -> tuple[dict[str, Any], tuple[NativeChainStructureInput, ...]]:
    config = load_chain_structure_config()
    python_evaluator = ChainStructureEvaluator(config)
    evaluator_client = NativeChainStructureBatchClient(module)
    transition_parity = evaluate_native_parity(
        NativeCompactBatchClient(module),
        corpus,
    )

    fixtures = _fixture_states(fixture_path)
    fixture_inputs = tuple(
        NativeChainStructureInput(state) for state in fixtures.values()
    )
    fixture_first = evaluator_client.evaluate_batch(fixture_inputs, config)
    fixture_second = evaluator_client.evaluate_batch(fixture_inputs, config)
    fixture_mismatches = []
    for (case_id, state), native_record in zip(
        fixtures.items(),
        fixture_first.records,
        strict=True,
    ):
        expected = python_evaluator.evaluate(state)
        try:
            actual = materialize_native_chain_structure_result(
                native_record,
                state=state,
                config=config,
            )
            if actual.to_dict() != expected.to_dict():
                fixture_mismatches.append({"case": case_id, "reason": "result"})
        except (InvalidNativeInputError, ValueError) as exc:
            fixture_mismatches.append(
                {"case": case_id, "reason": f"{type(exc).__name__}: {exc}"}
            )

    source_inputs = _selected_source_inputs(corpus)
    child_inputs = []
    child_contexts = []
    invalid_selected = []
    for stored, source_input in zip(
        corpus["records"],
        source_inputs,
        strict=True,
    ):
        result = transition(
            source_input.state,
            source_input.pair,
            source_input.action_id,
        )
        if not result.valid:
            invalid_selected.append(
                {
                    "seed": stored["seed"],
                    "turn": stored["turn"],
                    "action": source_input.action_id,
                }
            )
            continue
        action = ChainStructureAction.from_result(result)
        child_inputs.append(
            NativeChainStructureInput(
                result.state,
                parent_state=source_input.state,
                action=action,
                pair=source_input.pair,
                action_id=source_input.action_id,
            )
        )
        child_contexts.append((stored, source_input.state, result.state, action))

    child_first = evaluator_client.evaluate_batch(child_inputs, config)
    child_second = evaluator_client.evaluate_batch(child_inputs, config)
    evaluator_mismatches = []
    for context, native_record in zip(
        child_contexts,
        child_first.records,
        strict=True,
    ):
        stored, parent_state, child_state, action = context
        parent = python_evaluator.evaluate(parent_state)
        expected = python_evaluator.evaluate(
            child_state,
            parent=parent,
            action=action,
        )
        try:
            actual = materialize_native_chain_structure_result(
                native_record,
                state=child_state,
                config=config,
            )
            if actual.to_dict() != expected.to_dict():
                evaluator_mismatches.append(
                    {
                        "seed": stored["seed"],
                        "turn": stored["turn"],
                        "reason": "result",
                    }
                )
        except (InvalidNativeInputError, ValueError) as exc:
            evaluator_mismatches.append(
                {
                    "seed": stored["seed"],
                    "turn": stored["turn"],
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )

    oracle_mismatch_count = int(transition_parity["mismatch_count"]) + int(
        transition_parity["action_mismatch_count"]
    )
    deterministic_responses = (
        transition_parity["deterministic_response"]
        and fixture_first.response_bytes == fixture_second.response_bytes
        and child_first.response_bytes == child_second.response_bytes
    )
    payload = {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "ticket": ticket,
        "fixture": {
            "record_count": len(fixtures),
            "mismatch_count": len(fixture_mismatches),
            "mismatches": fixture_mismatches[:100],
            "response_sha256": hashlib.sha256(fixture_first.response_bytes).hexdigest(),
        },
        "transition_oracle": {
            "record_count": transition_parity["transition_count"],
            "mismatch_count": oracle_mismatch_count,
            "transition_mismatch_count": transition_parity["mismatch_count"],
            "action_mismatch_count": transition_parity["action_mismatch_count"],
            "response_sha256": transition_parity["response_sha256"],
        },
        "python_native_evaluator": {
            "record_count": len(child_contexts),
            "invalid_selected_count": len(invalid_selected),
            "invalid_selected": invalid_selected[:100],
            "mismatch_count": len(evaluator_mismatches),
            "mismatches": evaluator_mismatches[:100],
            "response_sha256": hashlib.sha256(child_first.response_bytes).hexdigest(),
        },
        "determinism": {
            "mismatch_count": 0 if deterministic_responses else 1,
            "byte_identical_repeats": bool(deterministic_responses),
        },
        "passed": bool(
            not fixture_mismatches
            and not oracle_mismatch_count
            and not evaluator_mismatches
            and not invalid_selected
            and deterministic_responses
        ),
    }
    return payload, source_inputs


def _source_verification(module: Any) -> dict[str, Any]:
    test_name = (
        "chain_structure::tests::"
        "combined_transition_and_evaluator_hot_path_allocates_nothing"
    )
    completed = subprocess.run(
        [
            "cargo",
            "test",
            "--locked",
            "--manifest-path",
            str(NATIVE_MANIFEST_PATH),
            test_name,
            "--",
            "--exact",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    allocation_passed = completed.returncode == 0 and f"test {test_name} ... ok" in (
        completed.stdout + completed.stderr
    )
    return {
        "allocation_test": test_name,
        "allocation_test_passed": allocation_passed,
        "normal_hot_path_heap_allocations": 0 if allocation_passed else None,
        "child_state_bytes": int(module.COMPACT_HOT_CHILD_STATE_BYTES),
        "hot_result_bytes": int(module.COMPACT_HOT_RESULT_BYTES),
        "evaluator_abi_version": int(module.CHAIN_STRUCTURE_EVALUATOR_ABI_VERSION),
        "test_output_sha256": hashlib.sha256(
            (completed.stdout + completed.stderr).encode("utf-8")
        ).hexdigest(),
    }


def _measure_profile(
    *,
    source_inputs: Sequence[NativeChainStructureInput],
    module: Any,
) -> dict[str, Any]:
    config = load_chain_structure_config()
    warmup_request = encode_native_chain_structure_batch(
        source_inputs,
        config,
        include_evidence=False,
    )
    module._chain_structure_combined_profile(warmup_request, WARMUP_OPERATIONS)

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

    combined = [sample["transition_evaluator_ns"] for sample in samples]
    native_total = [sample["native_call_total_ns"] for sample in samples]
    end_to_end = [sample["end_to_end_ns"] for sample in samples]
    checksums = {sample["checksum"] for sample in samples}
    operations_exact = all(
        sample["operations"] == EXPANDED_NODE_COUNT for sample in samples
    )
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "ticket": TICKET,
        "samples": samples,
        "aggregate": {
            "sample_count": len(samples),
            "record_count": len(source_inputs),
            "operations_per_sample": EXPANDED_NODE_COUNT,
            "operations_exact": operations_exact,
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


def derive_decision(
    semantic: Mapping[str, Any],
    profile: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    aggregate = profile["aggregate"]
    checks = {
        "expanded_node_authority": bool(aggregate["operations_exact"]),
        "transition_evaluator_p95": (
            aggregate["transition_evaluator_p95_ms"] <= COMBINED_P95_MAX_MS
        ),
        "native_call_total_p95": (
            aggregate["native_call_total_p95_ms"] <= NATIVE_TOTAL_P95_MAX_MS
        ),
        "end_to_end_p95": aggregate["end_to_end_p95_ms"] <= END_TO_END_P95_MAX_MS,
        "fixture_parity": semantic["fixture"]["mismatch_count"] == 0,
        "oracle_parity": semantic["transition_oracle"]["mismatch_count"] == 0,
        "python_native_parity": (
            semantic["python_native_evaluator"]["mismatch_count"] == 0
            and semantic["python_native_evaluator"]["invalid_selected_count"] == 0
        ),
        "determinism": (
            semantic["determinism"]["mismatch_count"] == 0
            and aggregate["determinism_mismatch_count"] == 0
        ),
        "normal_hot_path_allocations": (
            source["normal_hot_path_heap_allocations"] == 0
        ),
        "child_state_abi": source["child_state_bytes"] == REQUIRED_CHILD_STATE_BYTES,
        "hot_result_abi": source["hot_result_bytes"] == REQUIRED_HOT_RESULT_BYTES,
        "production_backend_not_promoted": True,
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "decision": "GO" if not failures else "NO_GO_CLOSE_PR_UNMERGED",
        "passed": not failures,
        "gate_checks": checks,
        "failed_gates": failures,
        "on_failure": {
            "close_implementation_pr_unmerged": True,
            "merge_implementation_pr": False,
            "puyo_202_may_start": False,
        },
        "production_backend_promoted": False,
    }


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def _command_version(command: Sequence[str]) -> str:
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.stdout or completed.stderr).strip().splitlines()[0]


def _source_state(output_dir: Path) -> dict[str, Any]:
    tracked = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--"],
        cwd=ROOT,
        check=False,
    ).returncode
    untracked_result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        output_prefix = str(output_dir.resolve().relative_to(ROOT.resolve())) + "/"
    except ValueError:
        output_prefix = ""
    untracked = [
        path
        for path in untracked_result.stdout.splitlines()
        if not output_prefix or not path.startswith(output_prefix)
    ]
    return {
        "dirty": tracked != 0 or bool(untracked),
        "tracked_diff": tracked != 0,
        "untracked_paths": untracked,
    }


def _report(summary: Mapping[str, Any]) -> str:
    aggregate = summary["profile"]["aggregate"]
    decision = summary["decision"]
    semantic = summary["semantic"]
    return "\n".join(
        [
            "# PUYO-201 native chain-structure prototype",
            "",
            f"Decision: **{decision['decision']}**.",
            "",
            "The bounded transition-plus-evaluator prototype preserves exact semantics",
            "but fails every latency envelope. It is not routed into production.",
            "",
            "| Gate | Observed | Target | Result |",
            "| --- | ---: | ---: | --- |",
            (
                f"| exact operations | {aggregate['operations_per_sample']:,} | "
                f"{EXPANDED_NODE_COUNT:,} | "
                f"{'pass' if decision['gate_checks']['expanded_node_authority'] else 'fail'} |"
            ),
            (
                f"| transition + evaluator p95 | "
                f"{aggregate['transition_evaluator_p95_ms']:.3f} ms | "
                f"<= {COMBINED_P95_MAX_MS:.3f} ms | "
                f"{'pass' if decision['gate_checks']['transition_evaluator_p95'] else 'fail'} |"
            ),
            (
                f"| native call total p95 | {aggregate['native_call_total_p95_ms']:.3f} ms | "
                f"<= {NATIVE_TOTAL_P95_MAX_MS:.3f} ms | "
                f"{'pass' if decision['gate_checks']['native_call_total_p95'] else 'fail'} |"
            ),
            (
                f"| end-to-end p95 | {aggregate['end_to_end_p95_ms']:.3f} ms | "
                f"<= {END_TO_END_P95_MAX_MS:.3f} ms | "
                f"{'pass' if decision['gate_checks']['end_to_end_p95'] else 'fail'} |"
            ),
            (
                f"| fixture mismatches | {semantic['fixture']['mismatch_count']} | 0 | "
                f"{'pass' if decision['gate_checks']['fixture_parity'] else 'fail'} |"
            ),
            (
                f"| transition oracle mismatches | "
                f"{semantic['transition_oracle']['mismatch_count']} | 0 | "
                f"{'pass' if decision['gate_checks']['oracle_parity'] else 'fail'} |"
            ),
            (
                f"| evaluator Python/native mismatches | "
                f"{semantic['python_native_evaluator']['mismatch_count']} | 0 | "
                f"{'pass' if decision['gate_checks']['python_native_parity'] else 'fail'} |"
            ),
            "",
            "Nearest-rank p95 retains every sample; no outlier was removed. The profile",
            "uses all 512 source states and their selected legal actions from the frozen",
            "11,264-transition corpus. Each measured sample executes exactly 600,000",
            "combined operations.",
            "",
            "Per PUYO-213, the implementation PR must close unmerged and PUYO-202 must",
            "remain To Do. Further implementation requires a new reviewed decision.",
            "",
        ]
    )


def run_benchmark(
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
    semantic, source_inputs = _semantic_verification(
        corpus=corpus,
        fixture_path=fixture_path,
        module=module,
    )
    source = _source_verification(module)
    profile = _measure_profile(source_inputs=source_inputs, module=module)
    decision = derive_decision(semantic, profile, source)
    measurement = {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "ticket": TICKET,
        "corpus": {
            "path": str(Path(corpus_path).relative_to(ROOT)),
            "sha256": file_sha256(corpus_path),
            "corpus_digest": corpus["corpus_digest"],
            "state_count": corpus["state_count"],
            "transition_count": corpus["transition_count"],
            "selected_action_count": len(source_inputs),
        },
        "profile": {
            "sample_count": PROFILE_SAMPLES,
            "operations_per_sample": EXPANDED_NODE_COUNT,
            "warmup_operations": WARMUP_OPERATIONS,
            "percentile": "nearest-rank sorted[ceil(p/100*N)-1]",
            "outlier_removal": "none",
            "timeout_substitution": False,
            "fallback_timing": False,
        },
        "gates": {
            "transition_evaluator_p95_ms_max": COMBINED_P95_MAX_MS,
            "native_total_p95_ms_max": NATIVE_TOTAL_P95_MAX_MS,
            "end_to_end_p95_ms_max": END_TO_END_P95_MAX_MS,
            "fixture_oracle_native_mismatches_required": 0,
            "determinism_mismatches_required": 0,
            "normal_hot_path_heap_allocations_required": 0,
            "child_state_bytes_required": REQUIRED_CHILD_STATE_BYTES,
            "hot_result_bytes_required": REQUIRED_HOT_RESULT_BYTES,
            "production_backend_promotion_allowed": False,
        },
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "source_commit": git_commit(ROOT),
        "config": {
            "path": str(DEFAULT_CONFIG_PATH.relative_to(ROOT)),
            "sha256": file_sha256(DEFAULT_CONFIG_PATH),
        },
        "semantic": semantic,
        "source_verification": source,
        "profile": profile,
        "decision": decision,
    }
    artifact_payloads = {
        "measurement_contract.json": measurement,
        "semantic_verification.json": semantic,
        "raw_profile.json": profile,
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
    wheels = sorted((ROOT / "dist" / "native").glob("puyo_deep_chain_native-*.whl"))
    wheel_path = wheels[-1] if wheels else None
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "source_commit": summary["source_commit"],
        "source_tree_dirty_before_run": source_state["dirty"],
        "source_tracked_diff_before_run": source_state["tracked_diff"],
        "source_untracked_paths_before_run": source_state["untracked_paths"],
        "environment": {
            "platform": platform.platform(),
            "cpu": _cpu_model(),
            "python": sys.version.split()[0],
            "rustc": _command_version(["rustc", "--version"]),
            "thread_count": 1,
            "native_module_path": str(module_path),
            "native_module_sha256": file_sha256(module_path),
            "release_wheel_path": (
                None if wheel_path is None else str(wheel_path.relative_to(ROOT))
            ),
            "release_wheel_sha256": (
                None if wheel_path is None else file_sha256(wheel_path)
            ),
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


def verify_benchmark(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> list[str]:
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
    for artifact in manifest.get("artifacts", []):
        path = destination / artifact["path"]
        if not path.is_file():
            issues.append(f"missing artifact: {artifact['path']}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"artifact hash mismatch: {artifact['path']}")
    if (
        measurement.get("profile", {}).get("operations_per_sample")
        != EXPANDED_NODE_COUNT
    ):
        issues.append("expanded-node authority drifted")
    if measurement.get("profile", {}).get("outlier_removal") != "none":
        issues.append("outlier policy drifted")
    decision = summary.get("decision", {})
    if manifest.get("decision") != decision.get("decision"):
        issues.append("manifest and summary decisions differ")
    if decision.get("passed") != all(decision.get("gate_checks", {}).values()):
        issues.append("decision does not match independent gate checks")
    return issues


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_benchmark(output_dir=args.output_dir)
        print(json.dumps(summary["decision"], indent=2, sort_keys=True))
        return 0
    issues = verify_benchmark(args.output_dir)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print(f"verified: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
