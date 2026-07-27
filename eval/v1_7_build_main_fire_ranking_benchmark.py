"""PUYO-178 build-main fire semantics and root-coverage benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import (
    BeamSearchConfig,
    BeamSearchPolicy,
    BuildPotentialBudget,
    clone_simulator,
)
from agents.compact_search import CompactSearchState, transition
from agents.long_horizon_search import (
    EXPECTED_CHAIN_RANKING_RULE_VERSION,
    FIRE_CLASS_PREMATURE,
    FIRE_CLASS_QUIET,
    FIRE_CLASS_TARGET,
    LongHorizonSearchConfig,
    classify_build_main_fire,
    run_long_horizon_search,
)
from agents.state_analyzer import StateAnalyzer
from agents.v1_7_strategy_manager import (
    V17StrategyFeatureEncoder,
    _response_guard_eligibility,
)
from agents.v1_7_tactics import load_tactic_registry
from agents.worker_proposals import (
    WORKER_PROPOSAL_SCHEMA_VERSION,
    build_worker_proposal_batch,
)
from eval.analyzer_scenarios import load_scenarios, scenario_input
from eval.v1_7_benchmark import load_response_scenarios
from puyo_env.actions import action_to_placement, legal_action_mask
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_build_main_fire_ranking_benchmark.v1"
RECORD_SCHEMA_VERSION = "puyo.v1_7_build_main_fire_ranking_records.v1"
MANIFEST_SCHEMA_VERSION = "puyo.v1_7_build_main_fire_ranking_manifest.v1"
DEFAULT_FIXTURE_PATH = "tests/fixtures/build_main_fire_cases.json"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-build-main-fire-ranking"

_FIXTURE_COLORS = {
    "R": PuyoColor.RED,
    "B": PuyoColor.BLUE,
    "G": PuyoColor.GREEN,
    "Y": PuyoColor.YELLOW,
    "P": PuyoColor.PURPLE,
    "O": PuyoColor.OJAMA,
}


class _CoverageEvaluation:
    def __init__(self, state: CompactSearchState):
        self.score = float(
            state.cell_count * 4
            - sum(height * height for height in state.column_heights)
        )
        self.danger = min(
            1.0,
            max(state.column_heights, default=0) / float(GRID_HEIGHT),
        )
        self.continuation_flexibility = max(0.0, 1.0 - self.danger)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_status": "available",
            "score": float(self.score),
            "danger": float(self.danger),
            "continuation_flexibility": float(
                self.continuation_flexibility
            ),
        }


class _CoverageEvaluator:
    def evaluate(self, state: CompactSearchState, **_kwargs: Any) -> _CoverageEvaluation:
        return _CoverageEvaluation(state)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(value: Any, *, prefix: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _fixture_payload(path: str | Path = DEFAULT_FIXTURE_PATH) -> Mapping[str, Any]:
    payload = _read_json(path)
    if payload.get("schema_version") != "puyo.build_main_fire_fixtures.v1":
        raise ValueError("unsupported build-main fire fixture schema")
    return payload


def _simulator_from_case(case: Mapping[str, Any]) -> HeadlessPuyoSimulator:
    rows = case["rows_bottom_up"]
    if len(rows) != GRID_HEIGHT or any(len(row) != GRID_WIDTH for row in rows):
        raise ValueError("build-main fixture board has the wrong shape")
    simulator = HeadlessPuyoSimulator(seed=178)
    game = simulator.game
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            game.field.grid[y][x] = Puyo(
                PuyoColor.EMPTY if char == "." else _FIXTURE_COLORS[char]
            )
    current = tuple(PuyoColor[name] for name in case["current_pair"])
    game.current_puyo_1 = Puyo(current[0])
    game.current_puyo_2 = Puyo(current[1])
    game.next_puyo_queue = deque(
        tuple(Puyo(PuyoColor[name]) for name in pair)
        for pair in case["next_pairs"]
    )
    game.state = "control"
    game.game_over = False
    return simulator


def _run_fixture_case(case: Mapping[str, Any]) -> dict[str, Any]:
    search = run_long_horizon_search(
        _simulator_from_case(case),
        LongHorizonSearchConfig(
            depth=1,
            width=24,
            scenarios=1,
            minimum_chain_count=int(case["target_chain_count"]),
            max_expanded_nodes=100,
            fire_context=str(case["fire_context"]),
        ),
    )
    evidence = search.evidence_by_action[int(case["action"])]
    return {
        "case_id": str(case["id"]),
        "action": int(case["action"]),
        "expected_fire_class": str(case["expected_fire_class"]),
        "observed_fire_class": evidence.fire_class,
        "candidate_value": float(evidence.candidate_value),
        "ranking_key_prefix": [
            int(evidence.ranking_key[0]),
            float(evidence.ranking_key[1]),
            int(evidence.ranking_key[2]),
            float(evidence.ranking_key[3]),
        ],
        "best_fire": (
            None if evidence.best_fire is None else evidence.best_fire.to_dict()
        ),
        "root_diagnostics": dict(
            search.root_diagnostics[int(case["action"])]
        ),
        "deterministic_digest": search.deterministic_digest,
    }


def _parity_record(case: Mapping[str, Any]) -> dict[str, Any]:
    simulator = _simulator_from_case(case)
    pair = (
        simulator.game.current_puyo_1.color,
        simulator.game.current_puyo_2.color,
    )
    action = int(case["action"])
    compact = transition(
        CompactSearchState.from_simulator(simulator),
        pair,
        action,
    )
    authoritative = clone_simulator(simulator)
    authoritative_result = authoritative.step(action_to_placement(action))
    authoritative_state = CompactSearchState.from_simulator(authoritative)
    checks = {
        "valid": bool(compact.valid),
        "planes": compact.state.planes == authoritative_state.planes,
        "chain_count": (
            int(compact.chain_count)
            == int(authoritative_result.chain_count)
        ),
        "score_delta": (
            int(compact.score_delta)
            == int(authoritative_result.score_delta)
        ),
        "game_over": (
            bool(compact.game_over)
            == bool(authoritative_result.game_over)
        ),
        "all_clear": (
            bool(compact.all_clear_achieved)
            == bool(authoritative_result.all_clear_achieved)
        ),
    }
    return {
        "case_id": str(case["id"]),
        "action": action,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _quota_record() -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=178)

    def run(width: int):
        return run_long_horizon_search(
            simulator,
            LongHorizonSearchConfig(
                depth=2 if width >= 22 else 1,
                width=width,
                scenarios=1,
                minimum_chain_count=10,
                max_expanded_nodes=5_000,
                root_survivor_quota=1,
            ),
            evaluator=_CoverageEvaluator(),
        )

    covered = run(24)
    constrained = run(8)
    covered_counts = {
        str(evidence.root_action): {
            str(depth): int(count)
            for depth, count in evidence.scenario_values[0].survivor_counts
        }
        for evidence in covered.root_evidence
    }
    shortfalls = {
        str(evidence.root_action): {
            str(depth): reason
            for depth, reason in evidence.scenario_values[0].survivor_shortfalls
        }
        for evidence in constrained.root_evidence
        if evidence.scenario_values[0].survivor_shortfalls
    }
    return {
        "quota": 1,
        "covered_width": 24,
        "covered_counts": covered_counts,
        "covered": all(
            counts.get("1", 0) >= 1 and counts.get("2", 0) >= 1
            for counts in covered_counts.values()
        ),
        "constrained_width": 8,
        "shortfalls": shortfalls,
        "shortfall_reason_recorded": any(
            "beam_width_below_root_quota" in reasons.values()
            for reasons in shortfalls.values()
        ),
        "deterministic_digest": covered.deterministic_digest,
    }


def _proposal_record() -> dict[str, Any]:
    def build():
        simulator = HeadlessPuyoSimulator(seed=178)
        legal = legal_action_mask(simulator)
        policy = BeamSearchPolicy(
            BeamSearchConfig.for_profile(
                "runtime",
                depth=2,
                width=24,
                max_expanded_nodes=1_100,
                candidate_limit=8,
                potential_probe_budget=8,
                build_potential_budget=BuildPotentialBudget(
                    max_added_puyos=1,
                    max_pattern_nodes=1,
                    max_resolution_nodes=1,
                    max_alternatives=1,
                    max_continuation_actions=1,
                    max_recovery_puyos=0,
                ),
                node_evaluator=_CoverageEvaluator(),
            )
        )
        candidates = policy.generate_candidates(
            {},
            {"simulator": simulator, "action_mask": legal},
        )
        diagnostics = policy.last_diagnostics
        batch = build_worker_proposal_batch(
            candidates,
            selected_action=candidates[0].action,
            candidate_limit=8,
            legal_action_mask=legal,
            profile_id=0,
            profile_name="puyo-178-runtime",
            strategy="build_large",
            simulator=simulator,
            search_latency_ms=0.0,
            expanded_nodes=diagnostics.expanded_nodes,
            scenario_budget=diagnostics.scenario_budget,
        )
        return batch

    first = build()
    second = build()
    candidates = [candidate for candidate in first.candidates if candidate is not None]
    return {
        "schema_version": first.schema_version,
        "candidate_limit": int(first.candidate_limit),
        "candidate_count": int(first.candidate_count),
        "candidate_mask": list(first.candidate_mask),
        "legal_roots": all(
            first.legal_action_mask[candidate.root_action]
            for candidate in candidates
        ),
        "candidate_ids": [candidate.candidate_id for candidate in candidates],
        "selected_index": first.selected_index,
        "rank_zero_compatibility": (
            first.selected_index == 0
            and first.selected_action == candidates[0].root_action
        ),
        "fire_classes": [
            candidate.evidence.trajectory["fire_class"]
            for candidate in candidates
        ],
        "root_diagnostics_present": all(
            bool(candidate.evidence.trajectory["root_diagnostics"])
            for candidate in candidates
        ),
        "deterministic_digest": first.deterministic_digest,
        "repeat_digest": second.deterministic_digest,
        "repeat_candidate_ids": [
            candidate.candidate_id
            for candidate in second.candidates
            if candidate is not None
        ],
    }


def _response_guard_record() -> dict[str, Any]:
    registry = load_tactic_registry()
    encoder = V17StrategyFeatureEncoder(registry)
    analyzer = StateAnalyzer()
    scenarios = {
        str(item["name"]): item for item in load_scenarios()
    }
    rows = []
    for definition in load_response_scenarios():
        analyzer_input = scenario_input(
            scenarios[str(definition["analyzer_scenario"])]
        )
        diagnostics = analyzer.analyze(analyzer_input)
        encoded = encoder.encode(analyzer_input, diagnostics)
        guarded = _response_guard_eligibility(
            encoder.contract,
            encoded.eligibility_mask,
            diagnostics,
        )
        selected = [
            tactic_id
            for tactic_id, allowed in zip(
                encoder.contract.tactic_ids,
                guarded,
            )
            if allowed
        ]
        expected = (
            "counter_or_return"
            if diagnostics.incoming.can_cancel
            else "survive"
        )
        rows.append(
            {
                "scenario": str(definition["name"]),
                "expected_tactic": expected,
                "eligible_tactics": selected,
                "build_main_blocked": "build_main" not in selected,
                "passed": selected == [expected],
            }
        )
    return {
        "scenarios": len(rows),
        "passed": sum(int(row["passed"]) for row in rows),
        "failed": sum(int(not row["passed"]) for row in rows),
        "records": rows,
    }


def _report(summary: Mapping[str, Any]) -> str:
    checks = summary["checks"]
    terminal = summary["terminal_score_trace"]
    response = summary["response_guard"]
    return "\n".join(
        [
            "# PUYO-178 Build-main fire ranking benchmark",
            "",
            f"- status: {'PASS' if checks['passed'] else 'FAIL'}",
            f"- ranking rule: `{summary['ranking_rule_version']}`",
            f"- fixture fire classes: {checks['fixture_fire_classes']}",
            f"- quiet > premature: {checks['quiet_over_premature']}",
            f"- target > quiet: {checks['target_over_quiet']}",
            f"- root quota covered: {checks['root_survivor_quota']}",
            f"- compact/authoritative parity: {checks['compact_authoritative_parity']}",
            f"- Proposal v2 K=8 compatibility: {checks['proposal_v2_compatibility']}",
            f"- latency-free 2-repeat determinism: {checks['determinism']}",
            f"- response guard: {response['passed']}/{response['scenarios']}",
            "",
            "## Premature terminal score trace",
            "",
            f"- target gap: {terminal['target_chain_gap']}",
            f"- target-gap penalty: {terminal['target_gap_penalty']}",
            f"- structural premature-fire penalty: {terminal['premature_fire_penalty']}",
            f"- trigger damage: {terminal['trigger_damage']}",
            f"- danger: {terminal['danger']}",
            f"- terminal score: {terminal['value']}",
            "",
            "Reproduce with:",
            "",
            "```bash",
            "python -m eval.v1_7_build_main_fire_ranking_benchmark run",
            "python -m eval.v1_7_build_main_fire_ranking_benchmark verify",
            "```",
            "",
        ]
    )


def run_benchmark(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
) -> dict[str, Any]:
    fixture = _fixture_payload(fixture_path)
    fixture_records = [
        _run_fixture_case(case) for case in fixture["cases"]
    ]
    by_id = {record["case_id"]: record for record in fixture_records}
    boundaries = [
        {
            **dict(boundary),
            "observed_fire_class": classify_build_main_fire(
                chain_count=int(boundary["chain_count"]),
                chain_score=int(boundary["chain_score"]),
                target_chain_count=int(boundary["target_chain_count"]),
                fire_context=str(boundary["fire_context"]),
                winning_score_threshold=(
                    None
                    if boundary.get("winning_score_threshold") is None
                    else int(boundary["winning_score_threshold"])
                ),
            ),
        }
        for boundary in fixture["classification_boundaries"]
    ]
    parity = [_parity_record(case) for case in fixture["cases"]]
    quota = _quota_record()
    proposal = _proposal_record()
    response_guard = _response_guard_record()

    premature = by_id["premature-fire"]
    quiet = by_id["quiet-continuation"]
    target = by_id["target-fire"]
    fire = premature["best_fire"]
    terminal = fire["terminal_score"]
    evaluation = terminal["evaluation"]
    checks = {
        "fixture_fire_classes": all(
            record["observed_fire_class"] == record["expected_fire_class"]
            for record in fixture_records
        )
        and all(
            boundary["observed_fire_class"]
            == boundary["expected_fire_class"]
            for boundary in boundaries
        ),
        "quiet_over_premature": (
            quiet["ranking_key_prefix"] > premature["ranking_key_prefix"]
        ),
        "target_over_quiet": (
            target["ranking_key_prefix"] > quiet["ranking_key_prefix"]
        ),
        "premature_terminal_score": bool(
            fire["fire_class"] == FIRE_CLASS_PREMATURE
            and not fire["allowed"]
            and terminal["breakdown"]["official_score"] == 0.0
            and terminal["breakdown"]["target_gap_penalty"] < 0.0
            and evaluation["score_breakdown"]["premature_fire"] < 0.0
            and evaluation["trigger_damage"] > 0
        ),
        "root_survivor_quota": bool(
            quota["covered"] and quota["shortfall_reason_recorded"]
        ),
        "compact_authoritative_parity": all(
            record["passed"] for record in parity
        ),
        "proposal_v2_compatibility": bool(
            proposal["schema_version"] == WORKER_PROPOSAL_SCHEMA_VERSION
            and proposal["candidate_limit"] == 8
            and proposal["candidate_count"] == 8
            and all(proposal["candidate_mask"])
            and proposal["legal_roots"]
            and proposal["rank_zero_compatibility"]
            and proposal["root_diagnostics_present"]
        ),
        "determinism": bool(
            proposal["deterministic_digest"] == proposal["repeat_digest"]
            and proposal["candidate_ids"] == proposal["repeat_candidate_ids"]
        ),
        "response_guard_6_of_6": bool(
            response_guard["scenarios"] == 6
            and response_guard["passed"] == 6
            and response_guard["failed"] == 0
        ),
    }
    checks["passed"] = all(checks.values())
    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "generated_at": utc_timestamp(),
        "git_commit": git_commit(),
        "ranking_rule_version": EXPECTED_CHAIN_RANKING_RULE_VERSION,
        "fixture_schema_version": fixture["schema_version"],
        "checks": checks,
        "terminal_score_trace": {
            "value": terminal["value"],
            "target_chain_gap": terminal["breakdown"]["target_chain_gap"],
            "target_gap_penalty": terminal["breakdown"]["target_gap_penalty"],
            "premature_fire_penalty": evaluation["score_breakdown"][
                "premature_fire"
            ],
            "trigger_damage": evaluation["trigger_damage"],
            "danger": evaluation["danger"],
        },
        "quiet_candidate_coverage": quiet["root_diagnostics"][
            "quiet_candidate_coverage"
        ],
        "root_survivor_quota": quota,
        "proposal_v2": proposal,
        "response_guard": response_guard,
        "compact_authoritative_parity": parity,
        "latency_free_digest": _digest(
            {
                "fixtures": fixture_records,
                "boundaries": boundaries,
                "quota": quota,
                "proposal": proposal,
                "response_guard": response_guard,
                "parity": parity,
            },
            prefix="build-main-fire-ranking",
        ),
    }
    records = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "fixture_records": fixture_records,
        "classification_boundaries": boundaries,
        "parity_records": parity,
        "quota": quota,
        "proposal_v2": proposal,
        "response_guard": response_guard,
    }

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "benchmark_summary.json"
    records_path = output / "fixture_records.json"
    report_path = output / "benchmark_report.md"
    manifest_path = output / "benchmark_manifest.json"
    _write_json(summary_path, summary)
    _write_json(records_path, records)
    report_path.write_text(_report(summary), encoding="utf-8")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": summary["generated_at"],
        "git_commit": summary["git_commit"],
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "fixture": {
            "path": str(fixture_path),
            "sha256": file_sha256(fixture_path),
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


def verify_benchmark(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    output = Path(output_dir)
    summary = _read_json(output / "benchmark_summary.json")
    records = _read_json(output / "fixture_records.json")
    manifest = _read_json(output / "benchmark_manifest.json")
    errors = []
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        errors.append("benchmark summary schema mismatch")
    if records.get("schema_version") != RECORD_SCHEMA_VERSION:
        errors.append("benchmark records schema mismatch")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append("benchmark manifest schema mismatch")
    if not bool(summary.get("checks", {}).get("passed")):
        errors.append("benchmark checks did not pass")
    fixture = manifest.get("fixture", {})
    fixture_path = Path(str(fixture.get("path", "")))
    if not fixture_path.is_file():
        errors.append(f"missing fixture: {fixture_path}")
    elif fixture.get("sha256") != file_sha256(fixture_path):
        errors.append("fixture checksum mismatch")
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
        "checks": dict(summary["checks"]),
        "latency_free_digest": summary["latency_free_digest"],
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
