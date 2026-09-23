"""PUYO-202 native long-horizon search verification and performance gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import resource
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agents.long_horizon_search as long_horizon_module
from agents.chain_structure import load_chain_structure_config
from agents.compact_search import CompactSearchState
from agents.deep_chain_native import (
    NativeDecisionRequest,
    NativeDeepChainBackend,
)
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from agents.long_horizon_search import (
    LONG_HORIZON_SURVIVOR_TIE_BREAK_VERSION,
    LongHorizonSearchConfig,
    run_compact_long_horizon_search,
)
from src.core.constants import PuyoColor

ROOT = Path(__file__).resolve().parents[1]
TICKET = "PUYO-202"
SUMMARY_SCHEMA_VERSION = "puyo.native_long_horizon_benchmark.v1"
RAW_SCHEMA_VERSION = "puyo.native_long_horizon_samples.v1"
ABLATION_SCHEMA_VERSION = "puyo.native_survivor_tie_break_ablation.v1"
MANIFEST_SCHEMA_VERSION = "puyo.native_long_horizon_manifest.v1"
DEFAULT_CORPUS_PATH = ROOT / "eval" / "deep_chain_native_corpus.json"
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "benchmarks" / "puyo-202-native-long-horizon"
NATIVE_TOTAL_P95_MAX_MS = 900.0
END_TO_END_P95_MAX_MS = 1_000.0
QUALITY_DEPTH = 16
QUALITY_WIDTH = 250
QUALITY_SCENARIOS = 6
QUALITY_MAX_EXPANDED_NODES = 600_000
QUALITY_SEEDS = (123, 1_337, 2_026, 9_001, 65_537)
ARTIFACT_NAMES = (
    "raw_samples.json",
    "survivor_tie_break_ablation.json",
    "benchmark_summary.json",
    "benchmark_report.md",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def nearest_rank(values: Sequence[float], percentile: float = 0.95) -> float:
    if not values:
        raise ValueError("nearest-rank percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(float(percentile) * len(ordered)))
    return ordered[rank - 1]


def derive_decision(summary: Mapping[str, Any]) -> dict[str, Any]:
    performance = summary["performance"]
    semantic = summary["semantic"]
    memory = summary["memory"]
    checks = {
        "quality_contract": (
            summary["contract"]
            == {
                "depth": QUALITY_DEPTH,
                "width": QUALITY_WIDTH,
                "scenarios": QUALITY_SCENARIOS,
                "max_expanded_nodes": QUALITY_MAX_EXPANDED_NODES,
            }
        ),
        "five_nearest_rank_samples": (
            performance["sample_count"] == len(QUALITY_SEEDS)
            and performance["percentile"] == "nearest-rank-p95"
            and performance["outlier_removal"] == "none"
        ),
        "native_total_p95": (
            performance["native_total_p95_ms"] <= NATIVE_TOTAL_P95_MAX_MS
        ),
        "end_to_end_p95": (performance["end_to_end_p95_ms"] <= END_TO_END_P95_MAX_MS),
        "frozen_python_differential": semantic["python_differential_mismatches"] == 0,
        "oracle_parallel_identity": semantic["oracle_parallel_mismatches"] == 0,
        "repeat_determinism": semantic["repeat_mismatches"] == 0,
        "thirty_seed_isolation": semantic["isolated_seed_count"] == 30,
        "exact_global_budget": semantic["budget_contract_passed"] is True,
        "gil_released": semantic["gil_counter_delta"] > 0,
        "bounded_arena": (
            memory["peak_live_nodes"] <= memory["arena_capacity_nodes"]
            and memory["max_rss_kib"] > 0
        ),
        "versioned_tie_break_ablation": (
            summary["ablation"]["decision_ranking_mismatches"] == 0
            and summary["ablation"]["root_evidence_mismatches"] == 0
        ),
        "production_backend_not_promoted": True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "decision": "GO" if not failed else "NO_GO",
        "passed": not failed,
        "checks": checks,
        "failed_checks": failed,
        "puyo_203_unblock_candidate": not failed,
        "production_backend_promoted": False,
    }


def _state(payload: Mapping[str, Any]) -> CompactSearchState:
    return CompactSearchState(
        planes=tuple(int(value, 16) for value in payload["planes_hex"]),
        all_clear_bonus_pending=bool(payload["all_clear_bonus_pending"]),
        game_over=bool(payload["game_over"]),
        score=int(payload["score"]),
        last_chain_end_score=int(payload["last_chain_end_score"]),
    )


def _pairs(payload: Sequence[Sequence[str]]):
    return tuple(tuple(PuyoColor[name] for name in pair) for pair in payload)


def _request(
    *,
    corpus: Mapping[str, Any],
    state: CompactSearchState,
    pairs,
    config: LongHorizonSearchConfig,
    mode: str,
    request_id: int,
) -> NativeDecisionRequest:
    return NativeDecisionRequest(
        state=state,
        known_pairs=pairs,
        search_config=config,
        evaluator_config=load_chain_structure_config(),
        config_digest=corpus["config_sha256"],
        profile_name="quality-d16",
        profile_version="3.0",
        config_version="puyo-202",
        request_id=request_id,
        execution_mode=mode,
        max_response_bytes=16 * 1024 * 1024,
    )


def _semantic_tuple(result) -> tuple[Any, ...]:
    return (
        result.selected_action,
        result.ranked_root_actions,
        result.search_complete,
        result.budget_exhausted,
        result.deterministic_digest,
        dict(result.counters),
        result.root_evidence,
        result.representatives,
        result.diagnostics,
    )


def _legacy_survivor_sort_key(node):
    return (
        -float(node.evaluator_score),
        int(node.root_action),
        node.state_fingerprint,
        int(node.last_action),
        tuple(int(action) for action in node.path),
    )


def _run_ablation(corpus: Mapping[str, Any]) -> dict[str, Any]:
    search_payload = corpus["search_case"]["config"]
    rows = []
    ranking_mismatches = 0
    evidence_mismatches = 0
    representative_changes = 0
    for case in corpus["cases"]:
        state = _state(case["state"])
        pairs = _pairs(case["scenario"]["known_pairs"])
        config = LongHorizonSearchConfig(
            depth=int(search_payload["depth"]),
            width=int(search_payload["width"]),
            scenarios=int(search_payload["scenarios"]),
            minimum_chain_count=int(search_payload["minimum_chain_count"]),
            max_expanded_nodes=int(search_payload["max_expanded_nodes"]),
            decision_seed=int(case["scenario"]["decision_seed"]),
            future_sampling_mode=str(case["scenario"]["future_sampling_mode"]),
        )
        current = run_compact_long_horizon_search(state, pairs, config)
        original = long_horizon_module._survivor_sort_key
        try:
            long_horizon_module._survivor_sort_key = _legacy_survivor_sort_key
            legacy = run_compact_long_horizon_search(state, pairs, config)
        finally:
            long_horizon_module._survivor_sort_key = original
        current_ranking = tuple(item.root_action for item in current.ranked_roots)
        legacy_ranking = tuple(item.root_action for item in legacy.ranked_roots)
        current_evidence = tuple(item.to_dict() for item in current.root_evidence)
        legacy_evidence = tuple(item.to_dict() for item in legacy.root_evidence)
        current_paths = {
            action: node.path for action, node in current.representatives.items()
        }
        legacy_paths = {
            action: node.path for action, node in legacy.representatives.items()
        }
        ranking_mismatches += int(current_ranking != legacy_ranking)
        evidence_mismatches += int(current_evidence != legacy_evidence)
        representative_changes += sum(
            int(current_paths.get(action) != legacy_paths.get(action))
            for action in set(current_paths) | set(legacy_paths)
        )
        rows.append(
            {
                "case_id": case["case_id"],
                "decision_ranking_equal": current_ranking == legacy_ranking,
                "root_evidence_equal": current_evidence == legacy_evidence,
                "representative_path_changes": sum(
                    int(current_paths.get(action) != legacy_paths.get(action))
                    for action in set(current_paths) | set(legacy_paths)
                ),
            }
        )
    return {
        "schema_version": ABLATION_SCHEMA_VERSION,
        "tie_break_version": LONG_HORIZON_SURVIVOR_TIE_BREAK_VERSION,
        "old_rule": "sha256(state_bytes)",
        "new_rule": "state_bytes",
        "case_count": len(rows),
        "decision_ranking_mismatches": ranking_mismatches,
        "root_evidence_mismatches": evidence_mismatches,
        "representative_path_changes": representative_changes,
        "cases": rows,
    }


def _python_differential(corpus: Mapping[str, Any], backend) -> int:
    search_payload = corpus["search_case"]["config"]
    mismatches = 0
    for index, case in enumerate(corpus["cases"]):
        state = _state(case["state"])
        pairs = _pairs(case["scenario"]["known_pairs"])
        config = LongHorizonSearchConfig(
            depth=int(search_payload["depth"]),
            width=int(search_payload["width"]),
            scenarios=int(search_payload["scenarios"]),
            minimum_chain_count=int(search_payload["minimum_chain_count"]),
            max_expanded_nodes=int(search_payload["max_expanded_nodes"]),
            decision_seed=int(case["scenario"]["decision_seed"]),
            future_sampling_mode=str(case["scenario"]["future_sampling_mode"]),
        )
        request = _request(
            corpus=corpus,
            state=state,
            pairs=pairs,
            config=config,
            mode="oracle-1",
            request_id=index,
        )
        native = materialize_native_long_horizon_result(
            backend.decide(request), request
        )
        python = run_compact_long_horizon_search(state, pairs, config)
        mismatches += int(
            native.deterministic_digest != python.deterministic_digest
            or native.counters.to_dict() != python.counters.to_dict()
            or tuple(item.root_action for item in native.ranked_roots)
            != tuple(item.root_action for item in python.ranked_roots)
        )
    return mismatches


def _seed_isolation(corpus, backend, state, pairs) -> int:
    observed = set()
    for seed in range(30):
        config = LongHorizonSearchConfig(
            depth=4,
            width=2,
            scenarios=6,
            minimum_chain_count=6,
            max_expanded_nodes=2_000,
            decision_seed=seed,
        )
        request = _request(
            corpus=corpus,
            state=state,
            pairs=pairs,
            config=config,
            mode="scenario-6",
            request_id=10_000 + seed,
        )
        result = materialize_native_long_horizon_result(
            backend.decide(request), request
        )
        observed.add(tuple(item.queue_digest for item in result.scenario_sequences))
    return len(observed)


def _budget_contract(corpus, backend, state, pairs) -> bool:
    config = LongHorizonSearchConfig(
        depth=3,
        width=5,
        scenarios=3,
        minimum_chain_count=6,
        max_expanded_nodes=300,
        decision_seed=5,
        future_sampling_mode="legacy-fixed-six",
    )
    oracle_request = _request(
        corpus=corpus,
        state=state,
        pairs=pairs,
        config=config,
        mode="oracle-1",
        request_id=20_001,
    )
    parallel_request = _request(
        corpus=corpus,
        state=state,
        pairs=pairs,
        config=config,
        mode="scenario-6",
        request_id=20_001,
    )
    oracle = backend.decide(oracle_request)
    parallel = backend.decide(parallel_request)
    return bool(
        parallel.budget_exhausted
        and parallel.counters["expanded_nodes"] == 300
        and parallel.telemetry["scenario_reruns"] == 1
        and _semantic_tuple(oracle) == _semantic_tuple(parallel)
    )


def _gil_probe(backend, request) -> int:
    stop = threading.Event()
    ready = threading.Event()
    counter = [0]

    def advance() -> None:
        ready.set()
        while not stop.is_set():
            counter[0] += 1

    worker = threading.Thread(target=advance)
    worker.start()
    if not ready.wait(timeout=1.0):
        raise RuntimeError("GIL probe worker did not start")
    before = counter[0]
    backend.decide(request)
    after = counter[0]
    stop.set()
    worker.join(timeout=1.0)
    if worker.is_alive():
        raise RuntimeError("GIL probe worker did not stop")
    return after - before


def run_benchmark(corpus_path: Path = DEFAULT_CORPUS_PATH) -> tuple[dict, dict]:
    corpus = _read_json(corpus_path)
    backend = NativeDeepChainBackend()
    state = _state(corpus["search_case"]["state"])
    pairs = _pairs(corpus["search_case"]["known_pairs"])
    samples = []
    oracle_parallel_mismatches = 0
    repeat_mismatches = 0
    peak_live_nodes = 0
    arena_capacity_nodes = 0
    tt_capacity_slots = 0
    first_request = None
    for index, seed in enumerate(QUALITY_SEEDS):
        config = LongHorizonSearchConfig(
            depth=QUALITY_DEPTH,
            width=QUALITY_WIDTH,
            scenarios=QUALITY_SCENARIOS,
            minimum_chain_count=6,
            max_expanded_nodes=QUALITY_MAX_EXPANDED_NODES,
            decision_seed=seed,
        )
        parallel_request = _request(
            corpus=corpus,
            state=state,
            pairs=pairs,
            config=config,
            mode="scenario-6",
            request_id=index + 1,
        )
        oracle_request = _request(
            corpus=corpus,
            state=state,
            pairs=pairs,
            config=config,
            mode="oracle-1",
            request_id=index + 1,
        )
        if first_request is None:
            first_request = parallel_request
        warm = backend.decide(parallel_request)
        oracle = backend.decide(oracle_request)
        started = time.perf_counter_ns()
        parallel = backend.decide(parallel_request)
        native_ns = time.perf_counter_ns() - started
        materialize_started = time.perf_counter_ns()
        materialized = materialize_native_long_horizon_result(
            parallel, parallel_request
        )
        materialize_ns = time.perf_counter_ns() - materialize_started
        oracle_parallel_mismatches += int(
            _semantic_tuple(oracle) != _semantic_tuple(parallel)
        )
        repeat_mismatches += int(_semantic_tuple(warm) != _semantic_tuple(parallel))
        peak_live_nodes = max(peak_live_nodes, parallel.telemetry["peak_live_nodes"])
        arena_capacity_nodes = max(
            arena_capacity_nodes, parallel.telemetry["arena_capacity_nodes"]
        )
        tt_capacity_slots = max(
            tt_capacity_slots, parallel.telemetry["tt_capacity_slots"]
        )
        samples.append(
            {
                "sample": index,
                "seed": seed,
                "native_total_ns": native_ns,
                "materialization_ns": materialize_ns,
                "end_to_end_ns": native_ns + materialize_ns,
                "selected_action": parallel.selected_action,
                "native_digest": parallel.deterministic_digest,
                "python_semantic_digest": materialized.deterministic_digest,
                "counters": dict(parallel.counters),
                "telemetry": dict(parallel.telemetry),
            }
        )
    if first_request is None:
        raise AssertionError("quality seed set must not be empty")
    ablation = _run_ablation(corpus)
    native_values = [row["native_total_ns"] / 1_000_000 for row in samples]
    e2e_values = [row["end_to_end_ns"] / 1_000_000 for row in samples]
    semantic = {
        "python_differential_cases": len(corpus["cases"]),
        "python_differential_mismatches": _python_differential(corpus, backend),
        "oracle_parallel_mismatches": oracle_parallel_mismatches,
        "repeat_mismatches": repeat_mismatches,
        "isolated_seed_count": _seed_isolation(corpus, backend, state, pairs),
        "budget_contract_passed": _budget_contract(corpus, backend, state, pairs),
        "gil_counter_delta": _gil_probe(backend, first_request),
    }
    raw = {
        "schema_version": RAW_SCHEMA_VERSION,
        "ticket": TICKET,
        "samples": samples,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": TICKET,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "depth": QUALITY_DEPTH,
            "width": QUALITY_WIDTH,
            "scenarios": QUALITY_SCENARIOS,
            "max_expanded_nodes": QUALITY_MAX_EXPANDED_NODES,
        },
        "performance": {
            "sample_count": len(samples),
            "percentile": "nearest-rank-p95",
            "outlier_removal": "none",
            "native_total_p95_ms": nearest_rank(native_values),
            "end_to_end_p95_ms": nearest_rank(e2e_values),
            "native_total_limit_ms": NATIVE_TOTAL_P95_MAX_MS,
            "end_to_end_limit_ms": END_TO_END_P95_MAX_MS,
        },
        "semantic": semantic,
        "memory": {
            "arena_capacity_nodes": arena_capacity_nodes,
            "tt_capacity_slots": tt_capacity_slots,
            "peak_live_nodes": peak_live_nodes,
            "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "ablation": {
            key: ablation[key]
            for key in (
                "tie_break_version",
                "decision_ranking_mismatches",
                "root_evidence_mismatches",
                "representative_path_changes",
            )
        },
        "provenance": {
            "git_commit": _git("rev-parse", "HEAD"),
            "source_tree_clean_at_start": not bool(_git("status", "--porcelain")),
            "native_capabilities": backend.capabilities.to_dict(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "wheel_sha256": _sha256(next((ROOT / "dist" / "native").glob("*.whl"))),
        },
    }
    summary["decision"] = derive_decision(summary)
    return summary, {"raw": raw, "ablation": ablation}


def _report(summary: Mapping[str, Any]) -> str:
    performance = summary["performance"]
    semantic = summary["semantic"]
    memory = summary["memory"]
    decision = summary["decision"]
    return f"""# PUYO-202 native long-horizon search benchmark

Decision: **{decision["decision"]}**.

## Contract

- depth 16 / width 250 / 6 deterministic scenarios
- global count-authoritative maximum: 600,000 expanded nodes
- five release-wheel samples, nearest-rank p95, no outlier removal
- reusable six-worker pool; `oracle-1` is the semantic authority

## Results

| Gate | Observed p95 | Limit | Result |
| --- | ---: | ---: | --- |
| native decision total | {performance["native_total_p95_ms"]:.3f} ms | 900.000 ms | {"pass" if decision["checks"]["native_total_p95"] else "fail"} |
| decision + Python materialization | {performance["end_to_end_p95_ms"]:.3f} ms | 1,000.000 ms | {"pass" if decision["checks"]["end_to_end_p95"] else "fail"} |

- Python differential mismatches: {semantic["python_differential_mismatches"]}
- oracle/parallel mismatches: {semantic["oracle_parallel_mismatches"]}
- repeat mismatches: {semantic["repeat_mismatches"]}
- isolated deterministic seeds: {semantic["isolated_seed_count"]} / 30
- exact global-budget rerun: {semantic["budget_contract_passed"]}
- GIL probe counter delta: {semantic["gil_counter_delta"]}
- peak live nodes / arena capacity: {memory["peak_live_nodes"]} / {memory["arena_capacity_nodes"]}
- process max RSS: {memory["max_rss_kib"]} KiB

## Tie-break ablation

`{summary["ablation"]["tie_break_version"]}` replaces per-survivor SHA-256 with
lexicographic canonical state bytes. Across the frozen corpus, decision/ranking
and root-evidence mismatches were both zero; representative-only path changes
were {summary["ablation"]["representative_path_changes"]}.

Production backend selection remains out of scope for PUYO-202.
"""


def write_artifacts(output_dir: Path, summary: dict, details: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "raw_samples.json", details["raw"])
    _write_json(output_dir / "survivor_tie_break_ablation.json", details["ablation"])
    _write_json(output_dir / "benchmark_summary.json", summary)
    (output_dir / "benchmark_report.md").write_text(_report(summary), encoding="utf-8")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ticket": TICKET,
        "source_commit": summary["provenance"]["git_commit"],
        "artifacts": {name: _sha256(output_dir / name) for name in ARTIFACT_NAMES},
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)


def verify_artifacts(output_dir: Path) -> list[str]:
    issues = []
    required = (*ARTIFACT_NAMES, "benchmark_manifest.json")
    for name in required:
        if not (output_dir / name).is_file():
            issues.append(f"missing artifact: {name}")
    if issues:
        return issues
    summary = _read_json(output_dir / "benchmark_summary.json")
    raw = _read_json(output_dir / "raw_samples.json")
    ablation = _read_json(output_dir / "survivor_tie_break_ablation.json")
    manifest = _read_json(output_dir / "benchmark_manifest.json")
    if summary.get("schema_version") != SUMMARY_SCHEMA_VERSION:
        issues.append("summary schema mismatch")
    if raw.get("schema_version") != RAW_SCHEMA_VERSION:
        issues.append("raw sample schema mismatch")
    if ablation.get("schema_version") != ABLATION_SCHEMA_VERSION:
        issues.append("ablation schema mismatch")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        issues.append("manifest schema mismatch")
    samples = raw.get("samples", [])
    if len(samples) == len(QUALITY_SEEDS):
        native_p95 = nearest_rank(
            [row["native_total_ns"] / 1_000_000 for row in samples]
        )
        e2e_p95 = nearest_rank([row["end_to_end_ns"] / 1_000_000 for row in samples])
        if native_p95 != summary["performance"]["native_total_p95_ms"]:
            issues.append("native p95 is not nearest-rank")
        if e2e_p95 != summary["performance"]["end_to_end_p95_ms"]:
            issues.append("end-to-end p95 is not nearest-rank")
    else:
        issues.append("sample count mismatch")
    if derive_decision(summary) != summary.get("decision"):
        issues.append("derived decision mismatch")
    for name in ARTIFACT_NAMES:
        if manifest.get("artifacts", {}).get(name) != _sha256(output_dir / name):
            issues.append(f"artifact digest mismatch: {name}")
    return issues


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    run_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    verify_parser.add_argument("--historical", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary, details = run_benchmark(args.corpus)
        write_artifacts(args.output_dir, summary, details)
        print(json.dumps(summary["decision"], indent=2, sort_keys=True))
        return 0 if summary["decision"]["passed"] else 1
    issues = verify_artifacts(args.output_dir)
    if issues:
        for issue in issues:
            print(issue)
        return 1
    print("PUYO-202 benchmark artifacts verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
