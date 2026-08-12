"""PUYO-179 seeded hidden-future sampling contract benchmark."""

from __future__ import annotations

import argparse
import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import (
    BeamSearchConfig,
    BeamSearchPolicy,
    BuildPotentialBudget,
)
from agents.long_horizon_search import (
    FUTURE_QUEUE_GENERATOR,
    FUTURE_SAMPLING_LEGACY_FIXED_SIX,
    FUTURE_SAMPLING_SCHEMA_VERSION,
    FUTURE_SAMPLING_SEEDED_AUTHORITATIVE,
    LEGACY_FIXED_SIX_PROFILE,
    LONG_HORIZON_SEARCH_PROFILES,
    QUALITY_D12_PROFILE,
    QUALITY_D16_PROFILE,
    RUNTIME_PROFILE,
    SMOKE_PROFILE,
    build_scenario_sequences,
)
from agents.worker_proposals import (
    CANDIDATE_RANKER_FEATURE_NAMES,
    CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES,
    WORKER_PROPOSAL_SCHEMA_VERSION,
    build_worker_proposal_batch,
)
from puyo_env.actions import legal_action_mask
from src.core.constants import GRID_HEIGHT
from src.core.headless import HeadlessPuyoSimulator
from src.core.tsumo import PuyoSequence
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_future_sampling_benchmark.v1"
RECORD_SCHEMA_VERSION = "puyo.v1_7_future_sampling_records.v1"
MANIFEST_SCHEMA_VERSION = "puyo.v1_7_future_sampling_manifest.v1"
DEFAULT_FIXTURE_PATH = "tests/fixtures/future_sampling_cases.json"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-future-sampling"


class _FastEvaluation:
    def __init__(self, state):
        self.score = float(
            state.cell_count * 4
            - sum(height * height for height in state.column_heights)
        )
        self.danger = min(
            1.0,
            max(state.column_heights, default=0) / float(GRID_HEIGHT),
        )
        self.continuation_flexibility = max(0.0, 1.0 - self.danger)
        self.features = None
        self.action_features = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_status": "available",
            "score": float(self.score),
            "danger": float(self.danger),
            "continuation_flexibility": float(
                self.continuation_flexibility
            ),
        }


class _FastEvaluator:
    def evaluate(self, state, **_kwargs):
        return _FastEvaluation(state)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _fixture(path: str | Path) -> Mapping[str, Any]:
    payload = _read_json(path)
    if payload.get("schema_version") != "puyo.future_tsumo_sampling_fixtures.v1":
        raise ValueError("unsupported future-sampling fixture schema")
    return payload


def _pair_names(pairs) -> list[list[str]]:
    return [[pair[0].name, pair[1].name] for pair in pairs]


def _sampling_record(
    fixture: Mapping[str, Any],
    *,
    decision_seed: int,
) -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=int(fixture["simulator_seed"]))
    sequences = build_scenario_sequences(
        simulator,
        scenarios=int(fixture["sample_count"]),
        depth=int(fixture["depth"]),
        decision_seed=int(decision_seed),
    )
    game = simulator.game
    known = (
        (game.current_puyo_1.color, game.current_puyo_2.color),
        *tuple(
            tuple(puyo.color for puyo in pair)
            for pair in tuple(game.next_puyo_queue)[:2]
        ),
    )
    authoritative_matches = []
    nonperiodic = []
    for sequence in sequences:
        generator = PuyoSequence(seed=sequence.rollout_seed)
        authoritative = tuple(
            tuple(puyo.color for puyo in pair)
            for pair in generator.next_pairs(len(sequence.hidden_pairs))
        )
        authoritative_matches.append(sequence.hidden_pairs == authoritative)
        repeated_first_two = tuple(
            sequence.hidden_pairs[index % 2]
            for index in range(len(sequence.hidden_pairs))
        )
        nonperiodic.append(sequence.hidden_pairs != repeated_first_two)
    return {
        "decision_seed": int(decision_seed),
        "decision_seed_source": sequences[0].decision_seed_source,
        "known_pairs": _pair_names(known),
        "known_pair_count": sequences[0].known_pair_count,
        "unknown_boundary_cursor": sequences[0].known_pair_count,
        "sample_ids": [sequence.sample_id for sequence in sequences],
        "rollout_seeds": [sequence.rollout_seed for sequence in sequences],
        "queue_digests": [sequence.queue_digest for sequence in sequences],
        "sequence_digests": [
            sequence.sequence_digest for sequence in sequences
        ],
        "hidden_pairs": [
            _pair_names(sequence.hidden_pairs) for sequence in sequences
        ],
        "sampling_metadata": [
            sequence.to_dict()["sampling"] for sequence in sequences
        ],
        "checks": {
            "known_prefix_preserved": all(
                sequence.known_pairs == known for sequence in sequences
            ),
            "authoritative_generator_match": all(authoritative_matches),
            "unique_rollout_seeds": (
                len({sequence.rollout_seed for sequence in sequences})
                == len(sequences)
            ),
            "independent_queue_digests": (
                len({sequence.queue_digest for sequence in sequences})
                == len(sequences)
            ),
            "no_two_pair_cycle": all(nonperiodic),
        },
    }


def _proposal_run(
    fixture: Mapping[str, Any],
    *,
    decision_seed: int,
):
    simulator = HeadlessPuyoSimulator(seed=int(fixture["simulator_seed"]))
    legal = legal_action_mask(simulator)
    policy = BeamSearchPolicy(
        BeamSearchConfig.for_profile(
            QUALITY_D12_PROFILE,
            depth=4,
            width=24,
            scenarios=int(fixture["sample_count"]),
            max_expanded_nodes=12_000,
            candidate_limit=8,
            potential_probe_budget=8,
            decision_seed=int(decision_seed),
            node_evaluator=_FastEvaluator(),
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
    if diagnostics is None or len(candidates) != 8:
        raise RuntimeError("future-sampling benchmark requires K=8 candidates")
    batch = build_worker_proposal_batch(
        candidates,
        selected_action=candidates[0].action,
        candidate_limit=8,
        legal_action_mask=legal,
        profile_id=0,
        profile_name="quality-seeded-future",
        strategy="build_large",
        simulator=simulator,
        search_latency_ms=0.0,
        expanded_nodes=diagnostics.expanded_nodes,
        scenario_budget=diagnostics.scenario_budget,
        worker_deadline_status={
            "status": "offline_quality",
            "budget_ms": None,
            "overrun": False,
            "source": "puyo-179-benchmark",
        },
    )
    return simulator, legal, policy, candidates, diagnostics, batch


def _permuted_ranker_digest(
    simulator,
    legal,
    candidates,
    diagnostics,
) -> str:
    reordered_candidates = []
    for candidate in candidates:
        expected = copy.deepcopy(dict(candidate.expected_chain_evidence))
        expected["scenario_values"] = list(
            reversed(expected.get("scenario_values", ()))
        )
        reordered_candidates.append(
            replace(candidate, expected_chain_evidence=expected)
        )
    budget = copy.deepcopy(dict(diagnostics.scenario_budget))
    budget["scenario_sequences"] = list(
        reversed(budget.get("scenario_sequences", ()))
    )
    batch = build_worker_proposal_batch(
        tuple(reordered_candidates),
        selected_action=reordered_candidates[0].action,
        candidate_limit=8,
        legal_action_mask=legal,
        profile_id=0,
        profile_name="quality-seeded-future",
        strategy="build_large",
        simulator=simulator,
        search_latency_ms=0.0,
        expanded_nodes=diagnostics.expanded_nodes,
        scenario_budget=budget,
        worker_deadline_status={
            "status": "offline_quality",
            "budget_ms": None,
            "overrun": False,
            "source": "puyo-179-benchmark",
        },
    )
    return batch.ranker_input.deterministic_digest


def _report(summary: Mapping[str, Any]) -> str:
    checks = summary["checks"]
    lines = [
        "# PUYO-179 Seeded Hidden-Future Sampling",
        "",
        "Canonical runtime, smoke, and quality profiles sample only the hidden",
        "future after current + NEXT 2 through the production `PuyoSequence`",
        "distribution. `legacy-fixed-six` remains an explicit regression profile.",
        "",
        "## Checks",
        "",
    ]
    for name, passed in checks.items():
        lines.append(f"- {name}: {'PASS' if passed else 'FAIL'}")
    lines.extend(
        [
            "",
            f"- verdict: {'PASS' if checks['passed'] else 'FAIL'}",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
) -> dict[str, Any]:
    fixture = _fixture(fixture_path)
    decision_seed = int(fixture["decision_seed"])
    first_sampling = _sampling_record(fixture, decision_seed=decision_seed)
    repeated_sampling = _sampling_record(fixture, decision_seed=decision_seed)
    changed_sampling = _sampling_record(
        fixture,
        decision_seed=int(fixture["different_decision_seed"]),
    )
    first = _proposal_run(fixture, decision_seed=decision_seed)
    second = _proposal_run(fixture, decision_seed=decision_seed)
    first_policy, first_batch = first[2], first[5]
    second_policy, second_batch = second[2], second[5]
    permuted_ranker_digest = _permuted_ranker_digest(
        first[0],
        first[1],
        first[3],
        first[4],
    )

    legacy = build_scenario_sequences(
        HeadlessPuyoSimulator(seed=int(fixture["simulator_seed"])),
        scenarios=6,
        depth=int(fixture["depth"]),
        decision_seed=decision_seed,
        sampling_mode=FUTURE_SAMPLING_LEGACY_FIXED_SIX,
    )
    canonical_profiles = (
        RUNTIME_PROFILE,
        SMOKE_PROFILE,
        QUALITY_D12_PROFILE,
        QUALITY_D16_PROFILE,
    )
    checks = {
        "known_prefix_preserved": first_sampling["checks"][
            "known_prefix_preserved"
        ],
        "authoritative_generator_match": first_sampling["checks"][
            "authoritative_generator_match"
        ],
        "same_seed_queue_digest": (
            first_sampling["queue_digests"]
            == repeated_sampling["queue_digests"]
        ),
        "different_seed_changes_queue": (
            first_sampling["queue_digests"]
            != changed_sampling["queue_digests"]
        ),
        "independent_samples": (
            first_sampling["checks"]["unique_rollout_seeds"]
            and first_sampling["checks"]["independent_queue_digests"]
        ),
        "canonical_has_no_two_pair_cycle": first_sampling["checks"][
            "no_two_pair_cycle"
        ],
        "canonical_profiles_are_seeded": all(
            LONG_HORIZON_SEARCH_PROFILES[name].future_sampling_mode
            == FUTURE_SAMPLING_SEEDED_AUTHORITATIVE
            for name in canonical_profiles
        ),
        "profile_sample_and_count_budgets_recorded": all(
            profile.scenarios > 0 and profile.max_expanded_nodes > 0
            for profile in LONG_HORIZON_SEARCH_PROFILES.values()
        ),
        "legacy_fixed_six_is_explicit": (
            LONG_HORIZON_SEARCH_PROFILES[
                LEGACY_FIXED_SIX_PROFILE
            ].future_sampling_mode
            == FUTURE_SAMPLING_LEGACY_FIXED_SIX
            and all(sequence.repeats_hidden_pairs for sequence in legacy)
        ),
        "same_seed_latency_free_proposal_digest": (
            first_policy.last_diagnostics.expected_chain_evidence[
                "proposal_digest"
            ]
            == second_policy.last_diagnostics.expected_chain_evidence[
                "proposal_digest"
            ]
        ),
        "proposal_v2_k8_stable_ids_and_masks": (
            first_batch.schema_version == WORKER_PROPOSAL_SCHEMA_VERSION
            and first_batch.candidate_limit == 8
            and first_batch.ranker_input.candidate_ids
            == second_batch.ranker_input.candidate_ids
            and first_batch.candidate_mask == second_batch.candidate_mask
            and first_batch.shared_context.scenario_mask
            == second_batch.shared_context.scenario_mask
        ),
        "sample_order_invariant_ranker_input": (
            first_batch.ranker_input.deterministic_digest
            == permuted_ranker_digest
        ),
        "sample_id_not_ranker_feature": (
            all(
                "sample_id" not in name
                for name in (
                    *CANDIDATE_RANKER_FEATURE_NAMES,
                    *CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES,
                )
            )
        ),
    }
    checks["passed"] = all(checks.values())
    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "checks": checks,
        "sampling_schema_version": FUTURE_SAMPLING_SCHEMA_VERSION,
        "generator": FUTURE_QUEUE_GENERATOR,
        "decision_seed": decision_seed,
        "queue_digests": first_sampling["queue_digests"],
        "rollout_seeds": first_sampling["rollout_seeds"],
        "sample_ids": first_sampling["sample_ids"],
        "latency_free_proposal_digest": (
            first_policy.last_diagnostics.expected_chain_evidence[
                "proposal_digest"
            ]
        ),
        "worker_proposal_digest": first_batch.deterministic_digest,
        "ranker_input_digest": first_batch.ranker_input.deterministic_digest,
        "permuted_ranker_input_digest": permuted_ranker_digest,
        "profiles": {
            name: profile.to_dict()
            for name, profile in LONG_HORIZON_SEARCH_PROFILES.items()
        },
    }
    records = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "same_seed_first": first_sampling,
        "same_seed_second": repeated_sampling,
        "different_seed": changed_sampling,
        "legacy_fixed_six": [
            sequence.to_dict() for sequence in legacy
        ],
    }
    output = Path(output_dir)
    summary_path = output / "benchmark_summary.json"
    records_path = output / "sampling_records.json"
    report_path = output / "benchmark_report.md"
    manifest_path = output / "benchmark_manifest.json"
    _write_json(summary_path, summary)
    _write_json(records_path, records)
    report_path.write_text(_report(summary), encoding="utf-8")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": utc_timestamp(),
        "commit": git_commit(),
        "fixture": {
            "path": str(fixture_path),
            "sha256": file_sha256(fixture_path),
        },
        "profiles": {
            name: profile.to_dict()
            for name, profile in LONG_HORIZON_SEARCH_PROFILES.items()
        },
        "artifacts": [
            describe_artifact(path, run_dir=output, role=role)
            for path, role in (
                (summary_path, "summary"),
                (records_path, "records"),
                (report_path, "report"),
            )
        ],
    }
    _write_json(manifest_path, manifest)
    return summary


def verify_benchmark(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    output = Path(output_dir)
    summary = _read_json(output / "benchmark_summary.json")
    records = _read_json(output / "sampling_records.json")
    manifest = _read_json(output / "benchmark_manifest.json")
    errors = []
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        errors.append("benchmark summary schema mismatch")
    if records.get("schema_version") != RECORD_SCHEMA_VERSION:
        errors.append("benchmark records schema mismatch")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append("benchmark manifest schema mismatch")
    if not bool(summary.get("checks", {}).get("passed")):
        errors.append("future-sampling checks did not pass")
    fixture = manifest.get("fixture", {})
    fixture_target = Path(str(fixture.get("path", "")))
    if not fixture_target.is_file():
        errors.append(f"missing fixture: {fixture_target}")
    elif fixture.get("sha256") != file_sha256(fixture_target):
        errors.append("fixture checksum mismatch")
    for artifact in manifest.get("artifacts", ()):
        target = output / str(artifact.get("path", ""))
        if not target.is_file():
            errors.append(f"missing benchmark artifact: {target}")
        elif artifact.get("sha256") != file_sha256(target):
            errors.append(f"benchmark artifact checksum mismatch: {target}")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "status": "passed",
        "checks": dict(summary["checks"]),
        "latency_free_proposal_digest": summary[
            "latency_free_proposal_digest"
        ],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--fixture", default=DEFAULT_FIXTURE_PATH)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_benchmark(
            args.output_dir,
            fixture_path=args.fixture,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["checks"]["passed"] else 1
    result = verify_benchmark(args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
