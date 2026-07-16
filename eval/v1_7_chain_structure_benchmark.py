"""PUYO-173 fixed/tuning corpus ablation for structural chain evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.chain_structure import (
    CHAIN_STRUCTURE_FEATURE_VERSION,
    ChainStructureConfig,
    ChainStructureEvaluator,
    load_chain_structure_config,
    mirror_state,
)
from agents.compact_search import CompactSearchState
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.chain_structure_benchmark.v1"
FIXTURE_SCHEMA_VERSION = "puyo.chain_structure_fixtures.v1"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-chain-structure"
DEFAULT_FIXTURE_PATH = "tests/fixtures/chain_structure_cases.json"
DEFAULT_PROFILE_REPETITIONS = 50
AMA_REFERENCE_COMMIT = "dea210bcd92965ae08fbc311f23565b0fab6dbbb"
CHAR_TO_PLANE = {
    "R": 0,
    "B": 1,
    "G": 2,
    "Y": 3,
    "P": 4,
    "O": 5,
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_cases(path: str | Path) -> tuple[int, list[dict[str, Any]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError("unsupported chain-structure fixture schema")
    cases = [dict(case) for case in payload.get("cases", ())]
    if not cases or {case.get("corpus") for case in cases} != {"fixed", "tuning"}:
        raise ValueError("chain-structure fixtures require fixed and tuning corpora")
    return int(payload.get("seed", 0)), cases


def _state(case: Mapping[str, Any]) -> CompactSearchState:
    rows = case.get("rows_bottom_up", ())
    if len(rows) != 14 or any(len(row) != 6 for row in rows):
        raise ValueError(f"fixture {case.get('id')} must contain a 6x14 board")
    planes = [0] * 6
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char == ".":
                continue
            if char not in CHAR_TO_PLANE:
                raise ValueError(f"unsupported fixture cell: {char!r}")
            planes[CHAR_TO_PLANE[char]] |= 1 << (y * 6 + x)
    return CompactSearchState(tuple(planes))


def _ablation_scores(result) -> dict[str, float]:
    breakdown = result.score_breakdown
    baseline = breakdown.shape + breakdown.danger + breakdown.nuisance
    cheap_quiescence = (
        baseline
        + breakdown.quiescence_chain
        + breakdown.key_cost
        + breakdown.trigger_position
        + breakdown.remaining_links
    )
    return {
        "baseline": float(baseline),
        "cheap_quiescence": float(cheap_quiescence),
        "chain_structure": float(result.score),
    }


def _attach_ranks(records: list[dict[str, Any]]) -> None:
    modes = ("baseline", "cheap_quiescence", "chain_structure")
    groups = sorted({str(record["rank_group"]) for record in records})
    for group in groups:
        selected = [record for record in records if record["rank_group"] == group]
        for mode in modes:
            ordered = sorted(
                selected,
                key=lambda record: (
                    -float(record["ablation_scores"][mode]),
                    str(record["case_id"]),
                ),
            )
            for rank, record in enumerate(ordered, start=1):
                record.setdefault("ranks", {})[mode] = rank
        for record in selected:
            record["rank_change"] = int(record["ranks"]["baseline"]) - int(
                record["ranks"]["chain_structure"]
            )


def evaluate_corpora(
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
    *,
    config: ChainStructureConfig | None = None,
) -> dict[str, Any]:
    selected_config = config or load_chain_structure_config()
    seed, cases = _load_cases(fixture_path)
    evaluator = ChainStructureEvaluator(selected_config)
    records: list[dict[str, Any]] = []
    determinism_mismatches = []
    symmetry_mismatches = []
    budget_violations = []

    for case in cases:
        state = _state(case)
        first = evaluator.evaluate(state)
        repeated = evaluator.evaluate(state)
        reflected = evaluator.evaluate(mirror_state(state))
        if first.to_dict() != repeated.to_dict():
            determinism_mismatches.append(str(case["id"]))
        if (
            first.score != reflected.score
            or first.features.to_dict() != reflected.features.to_dict()
            or first.tie_break_digest != reflected.tie_break_digest
        ):
            symmetry_mismatches.append(str(case["id"]))
        search = first.quiescence
        if (
            search.pattern_nodes > selected_config.budget.max_pattern_nodes
            or search.resolution_nodes > selected_config.budget.max_resolution_nodes
            or len(search.candidates) > selected_config.budget.max_candidates
        ):
            budget_violations.append(str(case["id"]))
        records.append(
            {
                "case_id": str(case["id"]),
                "corpus": str(case["corpus"]),
                "rank_group": str(case["rank_group"]),
                "evaluation_status": first.evaluation_status,
                "score": float(first.score),
                "tie_break_digest": first.tie_break_digest,
                "ablation_scores": _ablation_scores(first),
                "features": first.features.to_dict(),
                "quiescence": {
                    "chain_count": first.features.potential_chain_count,
                    "chain_score": first.features.potential_chain_score,
                    "required_key_count": first.features.required_key_count,
                    "pattern_nodes": search.pattern_nodes,
                    "resolution_nodes": search.resolution_nodes,
                    "search_complete": search.search_complete,
                    "truncation_reason": search.truncation_reason,
                },
                "score_breakdown": first.score_breakdown.to_dict(),
            }
        )

    _attach_ranks(records)
    deterministic_payload = [
        {
            "case_id": record["case_id"],
            "score": record["score"],
            "digest": record["tie_break_digest"],
            "ablation_scores": record["ablation_scores"],
            "ranks": record["ranks"],
        }
        for record in records
    ]
    return {
        "fixture_path": str(fixture_path),
        "fixture_sha256": file_sha256(fixture_path),
        "seed": seed,
        "feature_version": CHAIN_STRUCTURE_FEATURE_VERSION,
        "weight_version": selected_config.weight_version,
        "config": selected_config.to_dict(),
        "corpus_counts": {
            corpus: sum(record["corpus"] == corpus for record in records)
            for corpus in ("fixed", "tuning")
        },
        "records": records,
        "determinism_mismatches": determinism_mismatches,
        "symmetry_mismatches": symmetry_mismatches,
        "budget_violations": budget_violations,
        "deterministic_digest": _digest(deterministic_payload),
    }


def profile_evaluator(
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
    *,
    config: ChainStructureConfig | None = None,
    repetitions: int = DEFAULT_PROFILE_REPETITIONS,
) -> dict[str, Any]:
    if repetitions <= 0:
        raise ValueError("profile repetitions must be positive")
    selected_config = config or load_chain_structure_config()
    _, cases = _load_cases(fixture_path)
    states = [_state(case) for case in cases]
    evaluator = ChainStructureEvaluator(selected_config)
    evaluation_count = len(states) * int(repetitions)
    pattern_nodes = 0
    resolution_nodes = 0
    started = time.perf_counter()
    for _ in range(repetitions):
        for state in states:
            result = evaluator.evaluate(state)
            pattern_nodes += result.quiescence.pattern_nodes
            resolution_nodes += result.quiescence.resolution_nodes
    elapsed = time.perf_counter() - started
    return {
        "evaluation_count": evaluation_count,
        "elapsed_seconds": elapsed,
        "node_throughput_per_second": (evaluation_count / elapsed if elapsed else None),
        "pattern_nodes": pattern_nodes,
        "resolution_nodes": resolution_nodes,
        "profile_repetitions": int(repetitions),
        "wall_clock_excluded_from_deterministic_digest": True,
    }


def build_summary(
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
    *,
    config_path: str | Path | None = None,
    profile_repetitions: int = DEFAULT_PROFILE_REPETITIONS,
) -> dict[str, Any]:
    config = load_chain_structure_config(
        config_path or Path("train/config/v1_7_chain_structure.yaml")
    )
    first = evaluate_corpora(fixture_path, config=config)
    repeated = evaluate_corpora(fixture_path, config=config)
    records = {record["case_id"]: record for record in first["records"]}
    checks = {
        "deterministic_re_evaluation": not first["determinism_mismatches"],
        "deterministic_corpus_repeat": (
            first["deterministic_digest"] == repeated["deterministic_digest"]
        ),
        "mirror_symmetry": not first["symmetry_mismatches"],
        "budget_bounds": not first["budget_violations"],
        "fixed_and_tuning_corpora": all(
            first["corpus_counts"][name] > 0 for name in ("fixed", "tuning")
        ),
        "extendable_above_unreachable": (
            records["fixed-extendable-high"]["score"]
            > records["fixed-unreachable-high"]["score"]
        ),
        "evaluated_zero_is_explicit": (
            records["fixed-empty"]["evaluation_status"] == "not_found"
            and records["fixed-empty"]["score"] == 0.0
        ),
    }
    rank_changes = [
        {
            "case_id": record["case_id"],
            "rank_group": record["rank_group"],
            "baseline_rank": record["ranks"]["baseline"],
            "chain_structure_rank": record["ranks"]["chain_structure"],
            "rank_change": record["rank_change"],
        }
        for record in first["records"]
        if record["rank_change"] != 0
    ]
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "name": "puyo-v1-7-2-chain-structure",
        "created_at_utc": utc_timestamp(),
        "git_commit": git_commit(),
        "seed": first["seed"],
        "fixture_path": first["fixture_path"],
        "fixture_sha256": first["fixture_sha256"],
        "feature_version": first["feature_version"],
        "weight_version": first["weight_version"],
        "config": first["config"],
        "corpus_counts": first["corpus_counts"],
        "checks": checks,
        "passed": all(checks.values()),
        "determinism": {
            "passed": checks["deterministic_re_evaluation"]
            and checks["deterministic_corpus_repeat"],
            "digest": first["deterministic_digest"],
            "repeat_digest": repeated["deterministic_digest"],
            "mismatches": first["determinism_mismatches"],
            "excluded_fields": [
                "created_at_utc",
                "profile.elapsed_seconds",
                "profile.node_throughput_per_second",
            ],
        },
        "symmetry": {
            "passed": checks["mirror_symmetry"],
            "mismatches": first["symmetry_mismatches"],
        },
        "rank_changes": rank_changes,
        "records": first["records"],
        "profile": profile_evaluator(
            fixture_path,
            config=config,
            repetitions=profile_repetitions,
        ),
        "references": {
            "ama_commit": AMA_REFERENCE_COMMIT,
            "ama_files": [
                "ai/search/beam/quiet.cpp",
                "ai/search/beam/eval.cpp",
                "config.json",
            ],
            "copied_code": "none",
            "copied_weights": "none",
        },
    }


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    profile = summary["profile"]
    lines = [
        "# PUYO-173 structural chain evaluator ablation",
        "",
        f"- result: **{'PASS' if summary['passed'] else 'FAIL'}**",
        f"- feature version: `{summary['feature_version']}`",
        f"- weight version: `{summary['weight_version']}`",
        f"- corpus: {summary['corpus_counts']['fixed']} fixed / {summary['corpus_counts']['tuning']} tuning boards",
        f"- deterministic digest: `{summary['determinism']['digest']}`",
        f"- observed node throughput: {profile['node_throughput_per_second']:.2f} evaluations/s",
        "",
        "## Checks",
        "",
    ]
    lines.extend(
        f"- `{name}`: **{'PASS' if passed else 'FAIL'}**"
        for name, passed in summary["checks"].items()
    )
    lines.extend(["", "## Ablation rank changes", ""])
    if summary["rank_changes"]:
        lines.extend(
            "- `{case_id}` ({rank_group}): baseline {baseline_rank} -> structure {chain_structure_rank} ({rank_change:+d})".format(
                **record
            )
            for record in summary["rank_changes"]
        )
    else:
        lines.append("- no rank changes in the checked corpus")
    lines.extend(
        [
            "",
            "The wall-clock profile is observational and excluded from the deterministic digest.",
            "BuildPotential v2 remains the authoritative final-candidate diagnostic; this benchmark exercises only compact generic ordering features.",
            "",
            "## Provenance",
            "",
            f"- git commit: `{summary['git_commit']}`",
            f"- fixture: `{summary['fixture_path']}`",
            f"- seed: `{summary['seed']}`",
            f"- Ama analysis reference: `{AMA_REFERENCE_COMMIT}`",
            "- copied Ama source or numeric weights: none",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_artifacts(
    summary: Mapping[str, Any],
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    command: str,
) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records_path = output / "configuration_results.json"
    determinism_path = output / "determinism.json"
    summary_path = output / "benchmark_summary.json"
    report_path = output / "benchmark_report.md"
    _write_json(
        records_path,
        {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "seed": summary["seed"],
            "feature_version": summary["feature_version"],
            "weight_version": summary["weight_version"],
            "records": summary["records"],
        },
    )
    _write_json(determinism_path, summary["determinism"])
    _write_json(summary_path, summary)
    _write_report(report_path, summary)
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "name": summary["name"],
        "created_at_utc": summary["created_at_utc"],
        "git_commit": summary["git_commit"],
        "evaluation_completed": True,
        "passed": bool(summary["passed"]),
        "command": command,
        "seed": summary["seed"],
        "feature_version": summary["feature_version"],
        "weight_version": summary["weight_version"],
        "config": summary["config"],
        "environment": {
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "artifacts": [
            describe_artifact(
                path,
                run_dir=output,
                role=role,
            )
            for path, role in (
                (report_path, "benchmark_report"),
                (summary_path, "benchmark_summary"),
                (determinism_path, "determinism"),
                (records_path, "configuration_results"),
            )
        ],
    }
    _write_json(output / "benchmark_manifest.json", manifest)


def _command(args: argparse.Namespace) -> str:
    return (
        "python -m eval.v1_7_chain_structure_benchmark run "
        f"--output-dir {args.output_dir} --fixture {args.fixture} "
        f"--config {args.config} "
        f"--profile-repetitions {args.profile_repetitions}"
    )


def verify_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    artifact_dir = Path(args.artifact_dir)
    manifest = _read_json(artifact_dir / "benchmark_manifest.json")
    issues = []
    expected_artifacts = {
        "benchmark_report.md",
        "benchmark_summary.json",
        "configuration_results.json",
        "determinism.json",
    }
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected manifest schema")
    if manifest.get("name") != "puyo-v1-7-2-chain-structure":
        issues.append("unexpected benchmark name")
    if not manifest.get("evaluation_completed"):
        issues.append("benchmark evaluation is incomplete")
    if not manifest.get("passed"):
        issues.append("recorded benchmark checks failed")
    artifacts = manifest.get("artifacts", [])
    recorded_artifacts = {artifact.get("path") for artifact in artifacts}
    if recorded_artifacts != expected_artifacts:
        issues.append("benchmark artifact set mismatch")
    for artifact in artifacts:
        path = artifact_dir / artifact["path"]
        if not path.exists():
            issues.append(f"missing artifact: {artifact['path']}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"artifact hash mismatch: {artifact['path']}")

    summary = _read_json(artifact_dir / "benchmark_summary.json")
    records = _read_json(artifact_dir / "configuration_results.json")
    determinism = _read_json(artifact_dir / "determinism.json")
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected summary schema")
    if records.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected configuration-results schema")
    if not summary.get("passed") or not all(summary.get("checks", {}).values()):
        issues.append("benchmark quality checks failed")
    if not determinism.get("passed"):
        issues.append("determinism checks failed")
    if determinism.get("digest") != summary.get("determinism", {}).get("digest"):
        issues.append("determinism digest mismatch")
    if records.get("feature_version") != manifest.get("feature_version"):
        issues.append("feature version mismatch")
    if records.get("weight_version") != manifest.get("weight_version"):
        issues.append("weight version mismatch")
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "passed": not issues,
        "issues": issues,
        "checks": summary.get("checks", {}),
        "deterministic_digest": determinism.get("digest"),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--fixture", default=DEFAULT_FIXTURE_PATH)
    run.add_argument(
        "--config",
        default="train/config/v1_7_chain_structure.yaml",
    )
    run.add_argument(
        "--profile-repetitions",
        type=int,
        default=DEFAULT_PROFILE_REPETITIONS,
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--artifact-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    if hasattr(args, "profile_repetitions") and args.profile_repetitions <= 0:
        parser.error("--profile-repetitions must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        result = build_summary(
            args.fixture,
            config_path=args.config,
            profile_repetitions=args.profile_repetitions,
        )
        write_artifacts(result, args.output_dir, command=_command(args))
    else:
        result = verify_benchmark(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
