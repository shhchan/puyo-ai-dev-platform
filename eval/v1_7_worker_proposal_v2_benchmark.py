"""PUYO-175 proposal-v2 coverage, compatibility, and serialization benchmark."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import (
    BeamSearchConfig,
    BeamSearchPolicy,
    BuildPotentialBudget,
)
from agents.worker_proposals import (
    CANDIDATE_RANKER_INPUT_SCHEMA_VERSION,
    CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION,
    CANDIDATE_RANKER_V1_FEATURE_NAMES,
    EvidenceStatus,
    MaskedNumeric,
    WORKER_PROPOSAL_SCHEMA_VERSION,
    WorkerProposalBatch,
    build_worker_proposal_batch,
    candidate_ranker_schema_metadata,
    compatibility_action,
    project_worker_proposal_v1,
)
from eval.v1_7_benchmark import percentile
from puyo_env.actions import legal_action_mask
from src.core.headless import HeadlessPuyoSimulator
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_worker_proposal_v2_benchmark.v1"
RECORD_SCHEMA_VERSION = "puyo.v1_7_worker_proposal_v2_record.v1"
MANIFEST_SCHEMA_VERSION = "puyo.v1_7_worker_proposal_v2_manifest.v1"
FIELD_DICTIONARY_SCHEMA_VERSION = "puyo.worker_proposal_v2_field_dictionary.v1"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-worker-proposals-v2"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _base_batch(seed: int) -> WorkerProposalBatch:
    simulator = HeadlessPuyoSimulator(seed=int(seed))
    legal = legal_action_mask(simulator)
    policy = BeamSearchPolicy(
        BeamSearchConfig.for_profile(
            "quality-d12",
            depth=1,
            width=24,
            max_expanded_nodes=132,
            candidate_limit=8,
            potential_probe_budget=8,
            build_potential_budget=BuildPotentialBudget(
                max_added_puyos=1,
                max_pattern_nodes=2,
                max_resolution_nodes=2,
                max_alternatives=1,
                max_continuation_actions=1,
                max_recovery_puyos=0,
            ),
        )
    )
    candidates = policy.generate_candidates(
        {},
        {"simulator": simulator, "action_mask": legal},
    )
    diagnostics = policy.last_diagnostics
    if diagnostics is None or len(candidates) < 8:
        raise RuntimeError("proposal-v2 benchmark requires eight candidates")
    return build_worker_proposal_batch(
        candidates,
        selected_action=candidates[0].action,
        candidate_limit=8,
        legal_action_mask=legal,
        profile_id=0,
        profile_name="quality-six-scenario",
        strategy="build_large",
        simulator=simulator,
        search_latency_ms=diagnostics.elapsed_seconds * 1_000.0,
        expanded_nodes=diagnostics.expanded_nodes,
        scenario_budget=diagnostics.scenario_budget,
        worker_deadline_status={
            "status": "offline_quality",
            "budget_ms": None,
            "overrun": False,
            "source": "benchmark",
        },
    )


def _status_corpus(batch: WorkerProposalBatch) -> WorkerProposalBatch:
    statuses = (
        EvidenceStatus.EVALUATED,
        EvidenceStatus.NOT_EVALUATED,
        EvidenceStatus.BUDGET_EXHAUSTED,
        EvidenceStatus.LEGACY_MISSING,
    )
    candidates = list(batch.candidates)
    for index, status in enumerate(statuses):
        candidate = candidates[index]
        if candidate is None:  # pragma: no cover - guarded by _base_batch
            raise RuntimeError("status corpus encountered padding")
        evidence = candidate.evidence
        fields = dict(evidence.numeric_fields)
        present = status in {
            EvidenceStatus.EVALUATED,
            EvidenceStatus.BUDGET_EXHAUSTED,
        }
        fields["build_potential.predicted_chain_potential"] = MaskedNumeric(
            value=0.0 if present else None,
            is_present=present,
            evaluated=present,
            status=status,
        )
        candidates[index] = replace(
            candidate,
            evidence=replace(
                evidence,
                status=status,
                numeric_fields=fields,
            ),
        )
    return replace(batch, candidates=tuple(candidates))


def _measure_record(seed: int, repetition: int) -> dict[str, Any]:
    batch = _status_corpus(_base_batch(seed))
    payload = batch.to_dict()

    serialization_started = time.perf_counter()
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    restored = WorkerProposalBatch.from_dict(json.loads(serialized))
    serialization_ms = (time.perf_counter() - serialization_started) * 1_000.0

    projection_started = time.perf_counter()
    projection = batch.compatibility_projection
    projected_batch = project_worker_proposal_v1(batch)
    json.dumps(projection, sort_keys=True, separators=(",", ":"), allow_nan=False)
    projection_ms = (time.perf_counter() - projection_started) * 1_000.0

    ranker = batch.ranker_input
    actual = [candidate for candidate in batch.candidates if candidate is not None]
    status_counts = Counter(
        candidate.evidence.status.value for candidate in actual
    )
    present_features = sum(
        sum(row)
        for row, candidate in zip(ranker.feature_mask, batch.candidates)
        if candidate is not None
    )
    possible_features = len(actual) * len(ranker.features[0])
    potential_index = CANDIDATE_RANKER_V1_FEATURE_NAMES.index(
        "build_potential.predicted_chain_potential"
    )
    v1_confusion_count = sum(
        projected_batch.ranker_input.features[index][potential_index] == 0.0
        and candidate.evidence.status != EvidenceStatus.EVALUATED
        for index, candidate in enumerate(actual)
    )
    candidate_ids = [candidate.candidate_id for candidate in actual]
    projected_ids = [
        candidate.candidate_id
        for candidate in projected_batch.candidates
        if candidate is not None
    ]
    checks = {
        "round_trip": restored.to_dict() == payload,
        "rank_zero_compatibility": (
            compatibility_action(batch) == batch.selected_action
        ),
        "fixed_k8": len(batch.candidates) == 8 and all(batch.candidate_mask),
        "six_scenario_mask": (
            batch.shared_context.scenario_count == 6
            and all(batch.shared_context.scenario_mask)
        ),
        "candidate_ids_preserved_in_v1_projection": candidate_ids == projected_ids,
        "candidate_mask_preserved_in_v1_projection": (
            batch.candidate_mask == projected_batch.candidate_mask
        ),
        "selected_action_preserved_in_v1_projection": (
            batch.selected_action == projected_batch.selected_action
        ),
        "shared_cost_not_in_v2_candidate_features": (
            "search_latency_ms" not in payload["ranker_input"]["feature_names"]
            and "expanded_nodes" not in payload["ranker_input"]["feature_names"]
        ),
        "projection_is_explicitly_lossy": not bool(projection["lossless"]),
        "status_masks_invalid_candidates": ranker.candidate_mask[:4]
        == (True, False, True, False)
        and batch.compatibility_ranker_input.candidate_mask[:4]
        == (True, False, True, False),
    }
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "seed": int(seed),
        "repetition": int(repetition),
        "candidate_count": int(batch.candidate_count),
        "scenario_count": int(batch.shared_context.scenario_count),
        "status_counts": dict(sorted(status_counts.items())),
        "feature_coverage": (
            0.0
            if possible_features <= 0
            else present_features / float(possible_features)
        ),
        "scenario_feature_coverage": (
            sum(sum(row) for row in ranker.candidate_scenario_mask)
            / float(len(actual) * 6)
        ),
        "serialized_bytes": len(serialized.encode("utf-8")),
        "serialization_ms": float(serialization_ms),
        "projection_ms": float(projection_ms),
        "v1_zero_missingness_confusion_count": int(v1_confusion_count),
        "serialized_digest": batch.deterministic_digest,
        "ranker_input_digest": ranker.deterministic_digest,
        "compatibility_projection_digest": projection["deterministic_digest"],
        "checks": checks,
    }


def _summarize(
    records: Sequence[Mapping[str, Any]],
    *,
    seeds: Sequence[int],
    repetitions: int,
) -> dict[str, Any]:
    serialization = [float(record["serialization_ms"]) for record in records]
    projection = [float(record["projection_ms"]) for record in records]
    payload_sizes = [int(record["serialized_bytes"]) for record in records]
    feature_coverage = [float(record["feature_coverage"]) for record in records]
    scenario_coverage = [
        float(record["scenario_feature_coverage"]) for record in records
    ]
    status_counts: Counter[str] = Counter()
    for record in records:
        status_counts.update(record["status_counts"])
    check_names = tuple(records[0]["checks"])
    checks = {
        name: all(bool(record["checks"][name]) for record in records)
        for name in check_names
    }
    checks["deterministic_serialized_digest"] = all(
        len(
            {
                record["serialized_digest"]
                for record in records
                if int(record["seed"]) == int(seed)
            }
        )
        == 1
        for seed in seeds
    )
    checks["deterministic_ranker_input_digest"] = all(
        len(
            {
                record["ranker_input_digest"]
                for record in records
                if int(record["seed"]) == int(seed)
            }
        )
        == 1
        for seed in seeds
    )
    checks["all_four_missingness_statuses"] = {
        status.value for status in EvidenceStatus
    }.issubset(status_counts)
    checks["passed"] = all(checks.values())
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "worker_proposal_schema_version": WORKER_PROPOSAL_SCHEMA_VERSION,
        "seeds": [int(seed) for seed in seeds],
        "repetitions": int(repetitions),
        "records": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "feature_coverage": {
            "mean": statistics.fmean(feature_coverage),
            "minimum": min(feature_coverage),
        },
        "scenario_feature_coverage": {
            "mean": statistics.fmean(scenario_coverage),
            "minimum": min(scenario_coverage),
        },
        "serialized_bytes": {
            "p50": percentile(payload_sizes, 0.50),
            "p95": percentile(payload_sizes, 0.95),
        },
        "serialization_ms": {
            "p50": percentile(serialization, 0.50),
            "p95": percentile(serialization, 0.95),
        },
        "projection_ms": {
            "p50": percentile(projection, 0.50),
            "p95": percentile(projection, 0.95),
        },
        "v1_zero_missingness_confusion_count": sum(
            int(record["v1_zero_missingness_confusion_count"])
            for record in records
        ),
        "checks": checks,
        "measurement": {
            "search_budget": "six scenarios, depth 1, 132 expanded nodes",
            "serialization": "json encode plus v2 decode/readback",
            "projection": "explicit v2-to-v1 compatibility projection",
            "timing": "observational wall clock",
        },
    }


def _field_dictionary() -> dict[str, Any]:
    return {
        "schema_version": FIELD_DICTIONARY_SCHEMA_VERSION,
        "candidate_local": {
            "ranker": candidate_ranker_schema_metadata(
                CANDIDATE_RANKER_INPUT_SCHEMA_VERSION
            ),
            "lossless_namespaces": [
                "expected_chain",
                "structural_chain",
                "trajectory",
                "build_potential_status",
                "scenario_vector",
            ],
            "status_enum": [status.value for status in EvidenceStatus],
            "optional_numeric": ["value", "is_present", "evaluated", "status"],
        },
        "decision_shared": {
            "namespaces": [
                "profile",
                "known_queue_length",
                "scenarios",
                "search_config",
                "search_totals",
                "latency",
                "worker_deadline",
            ],
            "candidate_row_replication": False,
        },
        "compatibility_projection": {
            "target": candidate_ranker_schema_metadata(
                CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION
            ),
            "lossless": False,
            "feature_mask_semantics": "true means present",
            "missing_feature_mask_semantics": "true means absent",
        },
    }


def _report(summary: Mapping[str, Any]) -> str:
    checks = "\n".join(
        f"- `{name}`: {'PASS' if passed else 'FAIL'}"
        for name, passed in summary["checks"].items()
    )
    statuses = "\n".join(
        f"- `{name}`: {count}"
        for name, count in summary["status_counts"].items()
    )
    return "\n".join(
        [
            "# PUYO-175 Worker Proposal v2 Benchmark",
            "",
            "The fixed corpus uses K=8 and six canonical scenario slots. Timing is observational;",
            "serialized and ranker digests neutralize wall-clock fields.",
            "",
            "## Coverage and size",
            "",
            f"- candidate feature coverage mean: {summary['feature_coverage']['mean']:.4f}",
            f"- scenario feature coverage mean: {summary['scenario_feature_coverage']['mean']:.4f}",
            f"- serialized payload p50/p95: {summary['serialized_bytes']['p50']:.0f} / {summary['serialized_bytes']['p95']:.0f} bytes",
            f"- serialization p50/p95: {summary['serialization_ms']['p50']:.3f} / {summary['serialization_ms']['p95']:.3f} ms",
            f"- compatibility projection p50/p95: {summary['projection_ms']['p50']:.3f} / {summary['projection_ms']['p95']:.3f} ms",
            f"- v1 zero/missingness confusion count: {summary['v1_zero_missingness_confusion_count']}",
            "",
            "## Status counts",
            "",
            statuses,
            "",
            "## Checks",
            "",
            checks,
            "",
            "Reproduce with:",
            "",
            "```bash",
            "python -m eval.v1_7_worker_proposal_v2_benchmark run",
            "python -m eval.v1_7_worker_proposal_v2_benchmark verify",
            "```",
            "",
        ]
    )


def run_benchmark(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    seeds: Sequence[int] = (175, 176),
    repetitions: int = 2,
) -> dict[str, Any]:
    if not seeds or repetitions <= 0:
        raise ValueError("proposal-v2 benchmark requires seeds and repetitions")
    records = [
        _measure_record(int(seed), repetition)
        for seed in seeds
        for repetition in range(int(repetitions))
    ]
    summary = _summarize(records, seeds=seeds, repetitions=repetitions)
    summary.update({"generated_at": utc_timestamp(), "git_commit": git_commit()})

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records_path = output / "benchmark_records.json"
    summary_path = output / "benchmark_summary.json"
    fields_path = output / "field_dictionary.json"
    report_path = output / "benchmark_report.md"
    manifest_path = output / "benchmark_manifest.json"
    _write_json(
        records_path,
        {"schema_version": RECORD_SCHEMA_VERSION, "records": records},
    )
    _write_json(summary_path, summary)
    _write_json(fields_path, _field_dictionary())
    report_path.write_text(_report(summary), encoding="utf-8")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": summary["generated_at"],
        "git_commit": summary["git_commit"],
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "artifacts": [
            describe_artifact(path, run_dir=output, role=role)
            for path, role in (
                (summary_path, "summary"),
                (records_path, "records"),
                (fields_path, "field_dictionary"),
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
    fields = _read_json(output / "field_dictionary.json")
    manifest = _read_json(output / "benchmark_manifest.json")
    errors = []
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        errors.append("benchmark summary schema mismatch")
    if records_payload.get("schema_version") != RECORD_SCHEMA_VERSION:
        errors.append("benchmark records schema mismatch")
    if fields.get("schema_version") != FIELD_DICTIONARY_SCHEMA_VERSION:
        errors.append("field dictionary schema mismatch")
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
        elif artifact.get("sha256") != file_sha256(path):
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
    run.add_argument("--seeds", default="175,176")
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
