"""PUYO-169 K-best proposal latency and memory comparison benchmark."""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import (
    BUILD_POTENTIAL_SCHEMA_VERSION,
    BUILD_SCORING_V2,
    DIVERSE_CANDIDATE_MODE,
    BeamSearchConfig,
    BeamSearchPolicy,
)
from agents.worker_proposals import (
    WORKER_PROPOSAL_V1_SCHEMA_VERSION,
    WorkerProposalBatch,
    build_worker_proposal_batch,
    compatibility_action,
)
from eval.v1_7_benchmark import percentile
from puyo_env.actions import legal_action_indices, legal_action_mask
from src.core.headless import HeadlessPuyoSimulator
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_worker_proposal_benchmark.v1"
RECORD_SCHEMA_VERSION = "puyo.v1_7_worker_proposal_benchmark_record.v1"
MANIFEST_SCHEMA_VERSION = "puyo.v1_7_worker_proposal_benchmark_manifest.v1"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-worker-proposals"


@dataclass(frozen=True)
class ProposalBenchmarkConfiguration:
    config_id: str
    candidate_limit: int
    preview_node_budget: int
    depth: int = 2
    width: int = 12

    def __post_init__(self) -> None:
        if not self.config_id:
            raise ValueError("proposal benchmark configuration id is required")
        if min(
            self.candidate_limit,
            self.preview_node_budget,
            self.depth,
            self.width,
        ) <= 0:
            raise ValueError("proposal benchmark budgets must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "candidate_limit": int(self.candidate_limit),
            "preview_node_budget": int(self.preview_node_budget),
            "preview_budget_kind": "max_expanded_nodes",
            "depth": int(self.depth),
            "width": int(self.width),
            "scenarios": 1,
            "candidate_mode": DIVERSE_CANDIDATE_MODE,
            "scoring_mode": BUILD_SCORING_V2,
            "build_potential_schema_version": BUILD_POTENTIAL_SCHEMA_VERSION,
        }

    def beam_config(self, *, scenario_seed: int) -> BeamSearchConfig:
        return BeamSearchConfig(
            depth=self.depth,
            width=self.width,
            scenarios=1,
            minimum_chain_count=6,
            scenario_seed=int(scenario_seed),
            trigger_preservation="required",
            probe_width=self.candidate_limit,
            scoring_mode=BUILD_SCORING_V2,
            build_potential_schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
            potential_probe_budget=self.candidate_limit * self.depth + 1,
            candidate_mode=DIVERSE_CANDIDATE_MODE,
            candidate_limit=self.candidate_limit,
            max_expanded_nodes=self.preview_node_budget,
        )


DEFAULT_CONFIGURATIONS = (
    ProposalBenchmarkConfiguration("k1-n96", 1, 96),
    ProposalBenchmarkConfiguration("k4-n96", 4, 96),
    ProposalBenchmarkConfiguration("k8-n96", 8, 96),
    ProposalBenchmarkConfiguration("k4-n48", 4, 48),
    ProposalBenchmarkConfiguration("k4-n192", 4, 192),
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _measure_record(
    config: ProposalBenchmarkConfiguration,
    *,
    seed: int,
    repetition: int,
) -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=seed)
    mask = legal_action_mask(simulator)
    policy = BeamSearchPolicy(config.beam_config(scenario_seed=seed))
    info = {"simulator": simulator, "action_mask": mask}

    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        raw_candidates = policy.generate_candidates({}, info)
        diagnostics = policy.last_diagnostics
        if diagnostics is None or not raw_candidates:
            raise RuntimeError("proposal benchmark search produced no candidates")
        batch = build_worker_proposal_batch(
            raw_candidates,
            selected_action=raw_candidates[0].action,
            candidate_limit=config.candidate_limit,
            legal_action_mask=mask,
            profile_id=0,
            profile_name="benchmark_build",
            strategy="build_large",
            simulator=simulator,
            search_latency_ms=diagnostics.elapsed_seconds * 1_000.0,
            expanded_nodes=diagnostics.expanded_nodes,
            scenario_budget=diagnostics.scenario_budget,
            schema_version=WORKER_PROPOSAL_V1_SCHEMA_VERSION,
        )
        serialized = json.dumps(
            batch.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        restored = WorkerProposalBatch.from_dict(json.loads(serialized))
        latency_ms = (time.perf_counter() - started) * 1_000.0
        _, peak_memory_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    legal_actions = legal_action_indices(simulator)
    selected_action = compatibility_action(batch)
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "config_id": config.config_id,
        "seed": int(seed),
        "repetition": int(repetition),
        "candidate_limit": int(config.candidate_limit),
        "preview_node_budget": int(config.preview_node_budget),
        "latency_ms": float(latency_ms),
        "peak_memory_bytes": int(peak_memory_bytes),
        "serialized_bytes": len(serialized.encode("utf-8")),
        "expanded_nodes": int(diagnostics.expanded_nodes),
        "candidate_count": int(batch.candidate_count),
        "candidate_mask": list(batch.candidate_mask),
        "selected_action": int(selected_action),
        "raw_rank_0_action": int(raw_candidates[0].action),
        "deterministic_digest": batch.deterministic_digest,
        "candidate_ids": [
            None if candidate is None else candidate.candidate_id
            for candidate in batch.candidates
        ],
        "telemetry": batch.telemetry().to_dict(),
        "checks": {
            "selected_action_legal": selected_action in legal_actions,
            "compatibility_rank_0": selected_action == raw_candidates[0].action,
            "fixed_shape": (
                len(batch.candidates)
                == len(batch.candidate_mask)
                == config.candidate_limit
            ),
            "round_trip": restored.to_dict() == batch.to_dict(),
            "all_candidates_legal": all(
                candidate is None or candidate.root_action in legal_actions
                for candidate in batch.candidates
            ),
        },
    }


def _configuration_summary(
    config: ProposalBenchmarkConfiguration,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    latency = [float(record["latency_ms"]) for record in records]
    memory = [int(record["peak_memory_bytes"]) for record in records]
    serialized = [int(record["serialized_bytes"]) for record in records]
    expanded = [int(record["expanded_nodes"]) for record in records]
    candidate_counts = [int(record["candidate_count"]) for record in records]
    return {
        **config.to_dict(),
        "samples": len(records),
        "latency_ms": {
            "p50": percentile(latency, 0.50),
            "p95": percentile(latency, 0.95),
            "mean": statistics.fmean(latency),
        },
        "peak_memory_bytes": {
            "p50": percentile(memory, 0.50),
            "p95": percentile(memory, 0.95),
            "mean": statistics.fmean(memory),
        },
        "serialized_bytes": {
            "p50": percentile(serialized, 0.50),
            "p95": percentile(serialized, 0.95),
            "mean": statistics.fmean(serialized),
        },
        "expanded_nodes": {
            "p50": percentile(expanded, 0.50),
            "p95": percentile(expanded, 0.95),
            "mean": statistics.fmean(expanded),
        },
        "candidate_count": {
            "minimum": min(candidate_counts),
            "maximum": max(candidate_counts),
            "mean": statistics.fmean(candidate_counts),
        },
    }


def _summarize(
    records: Sequence[Mapping[str, Any]],
    configurations: Sequence[ProposalBenchmarkConfiguration],
    *,
    seeds: Sequence[int],
    repetitions: int,
) -> dict[str, Any]:
    summaries = []
    for config in configurations:
        selected = [record for record in records if record["config_id"] == config.config_id]
        summaries.append(_configuration_summary(config, selected))

    check_names = (
        "selected_action_legal",
        "compatibility_rank_0",
        "fixed_shape",
        "round_trip",
        "all_candidates_legal",
    )
    checks = {
        name: all(bool(record["checks"][name]) for record in records)
        for name in check_names
    }
    deterministic = True
    for config in configurations:
        for seed in seeds:
            digests = {
                record["deterministic_digest"]
                for record in records
                if record["config_id"] == config.config_id
                and int(record["seed"]) == int(seed)
            }
            deterministic = deterministic and len(digests) == 1
    checks["deterministic_repetitions"] = deterministic
    checks["passed"] = all(checks.values())
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "worker_proposal_schema_version": WORKER_PROPOSAL_V1_SCHEMA_VERSION,
        "seeds": [int(seed) for seed in seeds],
        "repetitions": int(repetitions),
        "records": len(records),
        "configurations": summaries,
        "checks": checks,
        "measurement": {
            "latency": "observational_wall_clock_including_serialization",
            "memory": "tracemalloc_peak_bytes_including_serialization",
            "preview_budget": "deterministic max_expanded_nodes",
            "determinism_projection": "wall-clock latency fields neutralized",
        },
    }


def _report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# PUYO-169 Worker Proposal Benchmark",
        "",
        "Latency is observational. Preview budget is the deterministic expanded-node cap,",
        "and peak memory is measured with `tracemalloc` through JSON serialization.",
        "",
        "| configuration | K | node budget | candidates | expanded nodes | latency p50 / p95 ms | memory p50 / p95 KiB | JSON p50 bytes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for config in summary["configurations"]:
        lines.append(
            "| {config_id} | {candidate_limit} | {preview_node_budget} | "
            "{candidate_mean:.2f} | {expanded_mean:.2f} | {latency_p50:.2f} / "
            "{latency_p95:.2f} | {memory_p50:.2f} / {memory_p95:.2f} | "
            "{serialized_p50:.0f} |".format(
                config_id=config["config_id"],
                candidate_limit=config["candidate_limit"],
                preview_node_budget=config["preview_node_budget"],
                candidate_mean=config["candidate_count"]["mean"],
                expanded_mean=config["expanded_nodes"]["mean"],
                latency_p50=config["latency_ms"]["p50"],
                latency_p95=config["latency_ms"]["p95"],
                memory_p50=config["peak_memory_bytes"]["p50"] / 1024.0,
                memory_p95=config["peak_memory_bytes"]["p95"] / 1024.0,
                serialized_p50=config["serialized_bytes"]["p50"],
            )
        )
    lines.extend(
        [
            "",
            "## Checks",
            "",
            *(
                f"- `{name}`: {'PASS' if passed else 'FAIL'}"
                for name, passed in summary["checks"].items()
            ),
            "",
            "Reproduce with:",
            "",
            "```bash",
            "python -m eval.v1_7_worker_proposal_benchmark run",
            "python -m eval.v1_7_worker_proposal_benchmark verify",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    seeds: Sequence[int] = (169, 170, 171, 172),
    repetitions: int = 2,
    configurations: Sequence[ProposalBenchmarkConfiguration] = DEFAULT_CONFIGURATIONS,
) -> dict[str, Any]:
    if repetitions <= 0:
        raise ValueError("proposal benchmark repetitions must be positive")
    if not seeds or not configurations:
        raise ValueError("proposal benchmark requires seeds and configurations")
    if len({config.config_id for config in configurations}) != len(configurations):
        raise ValueError("proposal benchmark configuration ids must be unique")

    records = [
        _measure_record(
            config,
            seed=int(seed),
            repetition=repetition,
        )
        for config in configurations
        for seed in seeds
        for repetition in range(int(repetitions))
    ]
    summary = _summarize(
        records,
        configurations,
        seeds=seeds,
        repetitions=repetitions,
    )
    summary.update(
        {
            "generated_at": utc_timestamp(),
            "git_commit": git_commit(),
        }
    )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records_path = output / "benchmark_records.json"
    summary_path = output / "benchmark_summary.json"
    report_path = output / "benchmark_report.md"
    manifest_path = output / "benchmark_manifest.json"
    _write_json(
        records_path,
        {
            "schema_version": RECORD_SCHEMA_VERSION,
            "records": records,
        },
    )
    _write_json(summary_path, summary)
    report_path.write_text(_report(summary), encoding="utf-8")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": summary["generated_at"],
        "git_commit": summary["git_commit"],
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "artifacts": [
            describe_artifact(
                path,
                run_dir=output,
                role=role,
            )
            for path, role in (
                (summary_path, "summary"),
                (records_path, "records"),
                (report_path, "report"),
            )
        ],
    }
    _write_json(manifest_path, manifest)
    return summary


def verify_benchmark(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    output = Path(output_dir)
    summary = _read_json(output / "benchmark_summary.json")
    records_payload = _read_json(output / "benchmark_records.json")
    manifest = _read_json(output / "benchmark_manifest.json")
    errors = []
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        errors.append("benchmark summary schema mismatch")
    if records_payload.get("schema_version") != RECORD_SCHEMA_VERSION:
        errors.append("benchmark records schema mismatch")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append("benchmark manifest schema mismatch")
    records = records_payload.get("records", ())
    if len(records) != int(summary.get("records", -1)):
        errors.append("benchmark record count mismatch")
    if not bool(summary.get("checks", {}).get("passed")):
        errors.append("benchmark checks did not pass")
    for artifact in manifest.get("artifacts", ()):
        path = output / str(artifact.get("path", ""))
        if not path.is_file():
            errors.append(f"missing benchmark artifact: {path}")
            continue
        if artifact.get("sha256") != file_sha256(path):
            errors.append(f"benchmark artifact checksum mismatch: {path}")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "status": "passed",
        "records": len(records),
        "checks": dict(summary["checks"]),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--seeds", default="169,170,171,172")
    run.add_argument("--repetitions", type=int, default=2)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        seeds = tuple(int(value) for value in args.seeds.split(",") if value.strip())
        summary = run_benchmark(
            args.output_dir,
            seeds=seeds,
            repetitions=args.repetitions,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["checks"]["passed"] else 1
    result = verify_benchmark(args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
