"""PUYO-166 BuildPotential v2 evidence on frozen PUYO-165 decisions.

The benchmark deliberately keeps the PUYO-165 decision artifact immutable.  It
replays each decision, reconstructs the board represented by every surviving
current-search root candidate, and evaluates the board-only BuildPotential v2
contract.  The bounded reference is evidence, not an oracle: predicted chain
count is the direct target, while the legacy candidate value is reported as a
separate auxiliary target because it contains the old board-shape heuristic.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import multiprocessing as mp
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import (
    BUILD_POTENTIAL_SCHEMA_VERSION,
    BUILD_POTENTIAL_V1_SCHEMA_VERSION,
    BuildPotential,
    BuildPotentialBudget,
    BuildPotentialSession,
    _ScenarioSequence,
    clone_simulator,
    migrate_build_potential_v1,
)
from puyo_env.actions import action_to_placement, legal_action_indices
from src.core.constants import NORMAL_PUYO_COLORS
from src.core.headless import HeadlessPuyoSimulator
from train.artifacts import (
    describe_artifact,
    file_sha256,
    git_commit,
    utc_timestamp,
)


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_build_potential_benchmark.v1"
FEATURE_RECORD_SCHEMA_VERSION = "puyo.v1_7_build_potential_feature_record.v1"
SOURCE_DECISION_SCHEMA_VERSION = "puyo.v1_7_search_diagnostics_decision.v1"
DEFAULT_SOURCE = "docs/benchmarks/puyo-v1-7-2-search-diagnostics/decision_records.json"
FROZEN_SOURCE_SHA256 = (
    "460f5fb26890d50117107269c342002750ecc84e0ab2d263044fe923502222c6"
)
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-build-potential-v2"
DEFAULT_REPETITIONS = 2
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_FILES = (
    "agents/beam_search.py",
    "eval/v1_7_build_potential_benchmark.py",
)
FEATURE_NAMES = (
    "v1_scalar",
    "v2_predicted_chain_potential",
    "v2_ignition_readiness",
    "v2_alternative_robustness",
    "v2_continuation_flexibility",
    "v2_danger_margin",
    "v2_composite",
)
TARGET_NAMES = (
    "reference_predicted_max_chain",
    "reference_candidate_value",
)
COMPOSITE_WEIGHTS = {
    "v2_predicted_chain_potential": 0.50,
    "v2_ignition_readiness": 0.15,
    "v2_alternative_robustness": 0.10,
    "v2_continuation_flexibility": 0.15,
    "v2_danger_margin": 0.10,
}


@dataclass(frozen=True)
class EvaluationConfig:
    budget: BuildPotentialBudget = BuildPotentialBudget()
    workers: int = 1
    repetitions: int = DEFAULT_REPETITIONS
    decision_limit: int | None = None

    def __post_init__(self) -> None:
        if self.workers <= 0 or self.repetitions <= 0:
            raise ValueError("workers and repetitions must be positive")
        if self.decision_limit is not None and self.decision_limit <= 0:
            raise ValueError("decision_limit must be positive when provided")

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget.to_dict(),
            "workers": int(self.workers),
            "repetitions": int(self.repetitions),
            "decision_limit": self.decision_limit,
            "cache_modes": [
                "enabled" if repetition % 2 == 0 else "disabled"
                for repetition in range(self.repetitions)
            ],
        }


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    else:
        serialized = json.dumps(payload, indent=2, sort_keys=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def implementation_evidence() -> list[dict[str, Any]]:
    """Hash uncommitted evaluator inputs in addition to recording git HEAD."""

    return [
        {
            "path": relative,
            "sha256": file_sha256(REPOSITORY_ROOT / relative),
            "size_bytes": (REPOSITORY_ROOT / relative).stat().st_size,
        }
        for relative in IMPLEMENTATION_FILES
    ]


def load_frozen_decisions(
    source: str | Path,
    *,
    decision_limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load a stable prefix without modifying the common PUYO-165 artifact."""

    path = Path(source)
    payload = _read_json(path)
    if payload.get("schema_version") != SOURCE_DECISION_SCHEMA_VERSION:
        raise ValueError("unexpected PUYO-165 decision artifact schema")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("PUYO-165 decision records are missing")
    records = raw_records[:decision_limit]
    previous: tuple[int, int] | None = None
    for record in records:
        if record.get("schema_version") != SOURCE_DECISION_SCHEMA_VERSION:
            raise ValueError("unexpected PUYO-165 decision record schema")
        identity = (int(record["seed"]), int(record["step"]))
        if previous is not None and identity <= previous:
            raise ValueError("PUYO-165 decisions must be ordered by seed and step")
        previous = identity
    evidence = {
        "path": str(path),
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
        "schema_version": payload["schema_version"],
        "available_decisions": len(raw_records),
        "selected_decisions": len(records),
        "selection_rule": (
            "all records in source order"
            if decision_limit is None
            else f"first {decision_limit} records in source order"
        ),
        "immutable_source": True,
    }
    return records, evidence


def _legacy_scalar(value: Mapping[str, Any]) -> float:
    chain_count = max(0, int(value.get("chain_count", 0)))
    if chain_count == 0:
        return 0.0
    required = max(1, int(value.get("required_puyos", 1)))
    return float(chain_count) - 0.25 * float(required - 1)


def _ignition_readiness(
    potential: BuildPotential,
    budget: BuildPotentialBudget,
) -> float | None:
    if potential.predicted_chain_potential is None:
        return None
    if not potential.exists:
        return 0.0
    if budget.max_added_puyos <= 1:
        return 1.0
    return max(
        0.0,
        min(
            1.0,
            1.0 - (potential.required_puyos - 1) / float(budget.max_added_puyos - 1),
        ),
    )


def _alternative_robustness(
    potential: BuildPotential,
    budget: BuildPotentialBudget,
) -> float | None:
    if potential.predicted_chain_potential is None:
        return None
    return min(
        1.0,
        potential.equivalence_class_count / float(budget.max_alternatives),
    )


def _composite(features: Mapping[str, float | None]) -> float | None:
    values = {name: features.get(name) for name in COMPOSITE_WEIGHTS}
    if any(value is None for value in values.values()):
        return None
    return sum(COMPOSITE_WEIGHTS[name] * float(value) for name, value in values.items())


def _feature_projection(
    potential: BuildPotential,
    *,
    legacy: Mapping[str, Any],
    budget: BuildPotentialBudget,
) -> dict[str, float | None]:
    features: dict[str, float | None] = {
        "v1_scalar": _legacy_scalar(legacy),
        "v2_predicted_chain_potential": potential.predicted_chain_potential,
        "v2_ignition_readiness": _ignition_readiness(potential, budget),
        "v2_alternative_robustness": _alternative_robustness(
            potential,
            budget,
        ),
        "v2_continuation_flexibility": potential.continuation_flexibility,
        "v2_danger_margin": potential.danger_margin,
    }
    features["v2_composite"] = _composite(features)
    return features


def audit_v1_projection(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Prove that field-less migration does not invent a v2 evaluated zero."""

    statuses: Counter[str] = Counter()
    positive = 0
    zero = 0
    probed = 0
    errors: list[str] = []
    candidates = 0
    for record in records:
        for candidate in record["current"]["candidates"]:
            candidates += 1
            legacy = candidate["potential"]
            if int(candidate["stages"].get("potential_probe_depth", 0)) > 0:
                probed += 1
            migrated = migrate_build_potential_v1(legacy)
            statuses[migrated.evaluation_status] += 1
            chain_count = max(0, int(legacy.get("chain_count", 0)))
            if chain_count > 0:
                positive += 1
                expected = "legacy_partial"
            else:
                zero += 1
                expected = "unknown"
            if migrated.evaluation_status != expected:
                errors.append(
                    f"{record['seed']}:{record['step']}:{candidate['action']}:status"
                )
            if migrated.chain_count != chain_count or migrated.required_puyos != max(
                0, int(legacy.get("required_puyos", 0))
            ):
                errors.append(
                    f"{record['seed']}:{record['step']}:{candidate['action']}:value"
                )
    return {
        "source_schema_version": BUILD_POTENTIAL_V1_SCHEMA_VERSION,
        "target_schema_version": BUILD_POTENTIAL_SCHEMA_VERSION,
        "candidate_records": candidates,
        "legacy_positive_records": positive,
        "legacy_zero_records": zero,
        "legacy_probe_evidence_records": probed,
        "status_counts": dict(sorted(statuses.items())),
        "semantics": {
            "positive": "legacy_partial; scalar fields preserved",
            "zero": "unknown; v1 cannot distinguish not-found from not-probed",
            "board_available": "recompute v2 under the configured bounded budget",
        },
        "errors": errors[:20],
        "error_count": len(errors),
        "passed": not errors,
    }


def _candidate_board(
    simulator: HeadlessPuyoSimulator,
    path: Sequence[int],
) -> tuple[HeadlessPuyoSimulator | None, str | None]:
    candidate = clone_simulator(simulator)
    # PUYO-165 used one deterministic unknown-future scenario.  The current
    # pair and visible next queue remain cloned; only the hidden generator is
    # replaced, exactly as BeamSearchPolicy does before expanding candidates.
    candidate.game.puyo_sequence = _ScenarioSequence(0, NORMAL_PUYO_COLORS)
    for action in path:
        result = candidate.step(action_to_placement(int(action)))
        if not result.valid:
            return None, f"invalid candidate path action {action}"
        if result.game_over:
            return None, f"candidate path action {action} caused game over"
    return candidate, None


def _evaluate_seed(
    seed: int,
    records: Sequence[Mapping[str, Any]],
    *,
    budget: BuildPotentialBudget,
    use_cache: bool,
) -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=seed)
    rows: list[dict[str, Any]] = []
    replay_issues: list[str] = []
    cache_hits = 0
    cache_probe_count = 0
    unique_evaluations = 0
    recompute_checks = 0
    recompute_matches = 0
    budget_violations = 0
    status_counts: Counter[str] = Counter()

    for expected_step, record in enumerate(records):
        step = int(record["step"])
        if step != expected_step:
            replay_issues.append(
                f"seed {seed}: expected step {expected_step}, found {step}"
            )
            break
        legal = [int(action) for action in legal_action_indices(simulator)]
        recorded_legal = [int(action) for action in record["legal_root_actions"]]
        if legal != recorded_legal:
            replay_issues.append(f"seed {seed} step {step}: legal actions differ")
            break
        current_by_action = {
            int(candidate["action"]): candidate
            for candidate in record["current"]["candidates"]
        }
        reference_by_action = {
            int(candidate["action"]): candidate
            for candidate in record["reference"]["candidates"]
        }
        eligible = [
            action
            for action in legal
            if current_by_action[action].get("candidate_value") is not None
            and reference_by_action[action].get("candidate_value") is not None
            and current_by_action[action].get("best_path")
        ]
        session = BuildPotentialSession(
            schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
            budget=budget,
            max_evaluations=max(1, len(eligible)),
            use_cache=use_cache,
        )
        first_board: HeadlessPuyoSimulator | None = None
        first_potential: BuildPotential | None = None
        first_legacy: Mapping[str, Any] | None = None
        for action in eligible:
            current = current_by_action[action]
            reference = reference_by_action[action]
            candidate, issue = _candidate_board(simulator, current["best_path"])
            if issue is not None or candidate is None:
                replay_issues.append(
                    f"seed {seed} step {step} action {action}: {issue}"
                )
                continue
            potential = session.evaluate(candidate)
            status_counts[potential.evaluation_status] += 1
            if (
                potential.pattern_nodes > budget.max_pattern_nodes
                or potential.resolution_nodes > budget.max_resolution_nodes
                or len(potential.alternatives) > budget.max_alternatives
            ):
                budget_violations += 1
            legacy = current["potential"]
            features = _feature_projection(
                potential,
                legacy=legacy,
                budget=budget,
            )
            rows.append(
                {
                    "seed": int(seed),
                    "step": step,
                    "action": int(action),
                    "path_depth": len(current["best_path"]),
                    "features": features,
                    "targets": {
                        "reference_predicted_max_chain": int(
                            reference["predicted_max_chain"]
                        ),
                        "reference_candidate_value": float(
                            reference["candidate_value"]
                        ),
                    },
                    "v2": {
                        "schema_version": potential.schema_version,
                        "evaluation_status": potential.evaluation_status,
                        "exists": potential.exists,
                        "predicted_chain_count": potential.to_dict()[
                            "predicted_chain_count"
                        ],
                        "ignition_cost_puyos": (
                            int(potential.required_puyos) if potential.exists else None
                        ),
                        "alternative_count": len(potential.alternatives),
                        "equivalence_class_count": (potential.equivalence_class_count),
                        "search_complete": potential.search_complete,
                        "pattern_nodes": int(potential.pattern_nodes),
                        "resolution_nodes": int(potential.resolution_nodes),
                        "truncation_reason": potential.truncation_reason,
                    },
                    "v1": {
                        "chain_count": max(
                            0,
                            int(legacy.get("chain_count", 0)),
                        ),
                        "required_puyos": max(
                            0,
                            int(legacy.get("required_puyos", 0)),
                        ),
                        "probe_evidence": int(
                            current["stages"].get("potential_probe_depth", 0)
                        )
                        > 0,
                    },
                }
            )
            if first_board is None:
                first_board = candidate
                first_potential = potential
                first_legacy = legacy

        if first_board is not None and first_potential is not None:
            repeated = session.evaluate(first_board)
            cache_probe_count += 1
            if repeated.to_dict() != first_potential.to_dict():
                replay_issues.append(
                    f"seed {seed} step {step}: repeated evaluation differs"
                )
            if first_legacy is not None:
                recomputed = migrate_build_potential_v1(
                    first_legacy,
                    simulator=first_board,
                    budget=budget,
                )
                recompute_checks += 1
                if recomputed.to_dict() == first_potential.to_dict():
                    recompute_matches += 1
        cache_hits += session.cache_hits
        unique_evaluations += session.evaluation_count

        selected_action = int(record["current"]["selected_action"])
        result = simulator.step(action_to_placement(selected_action))
        outcome = record["outcome"]
        if (
            not result.valid
            or bool(result.game_over) != bool(outcome["game_over"])
            or int(result.chain_count) != int(outcome["chain_count"])
            or int(result.score_delta) != int(outcome["score_delta"])
        ):
            replay_issues.append(f"seed {seed} step {step}: outcome differs")
            break

    return {
        "seed": int(seed),
        "rows": rows,
        "replay_issues": replay_issues,
        "cache": {
            "enabled": bool(use_cache),
            "hits": int(cache_hits),
            "probe_count": int(cache_probe_count),
            "unique_evaluations": int(unique_evaluations),
        },
        "migration_recompute": {
            "checks": int(recompute_checks),
            "matches": int(recompute_matches),
        },
        "budget_violations": int(budget_violations),
        "status_counts": dict(sorted(status_counts.items())),
    }


def _evaluate_seed_task(
    task: tuple[
        int,
        list[dict[str, Any]],
        BuildPotentialBudget,
        bool,
    ],
) -> dict[str, Any]:
    seed, records, budget, use_cache = task
    return _evaluate_seed(
        seed,
        records,
        budget=budget,
        use_cache=use_cache,
    )


def evaluate_records(
    records: Sequence[Mapping[str, Any]],
    *,
    budget: BuildPotentialBudget,
    workers: int,
    use_cache: bool,
) -> dict[str, Any]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[int(record["seed"])].append(dict(record))
    tasks = [(seed, grouped[seed], budget, use_cache) for seed in sorted(grouped)]
    if workers == 1:
        seed_results = [_evaluate_seed_task(task) for task in tasks]
    else:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context("spawn"),
        ) as executor:
            seed_results = list(executor.map(_evaluate_seed_task, tasks, chunksize=1))
    seed_results.sort(key=lambda item: int(item["seed"]))
    rows = [row for result in seed_results for row in result["rows"]]
    rows.sort(key=lambda row: (row["seed"], row["step"], row["action"]))
    status_counts: Counter[str] = Counter()
    for result in seed_results:
        status_counts.update(result["status_counts"])
    return {
        "rows": rows,
        "digest": _digest(rows),
        "replay_issues": [
            issue for result in seed_results for issue in result["replay_issues"]
        ],
        "cache": {
            "enabled": bool(use_cache),
            "hits": sum(result["cache"]["hits"] for result in seed_results),
            "probe_count": sum(
                result["cache"]["probe_count"] for result in seed_results
            ),
            "unique_evaluations": sum(
                result["cache"]["unique_evaluations"] for result in seed_results
            ),
        },
        "migration_recompute": {
            "checks": sum(
                result["migration_recompute"]["checks"] for result in seed_results
            ),
            "matches": sum(
                result["migration_recompute"]["matches"] for result in seed_results
            ),
        },
        "budget_violations": sum(
            result["budget_violations"] for result in seed_results
        ),
        "status_counts": dict(sorted(status_counts.items())),
    }


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[order[position]] = rank
        start = end
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    return None if denominator == 0.0 else numerator / denominator


def spearman_correlation(
    xs: Sequence[float],
    ys: Sequence[float],
) -> float | None:
    """Spearman rho with average ranks for exact ties."""

    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return _pearson(_average_ranks(xs), _average_ranks(ys))


def kendall_tau_b(
    xs: Sequence[float],
    ys: Sequence[float],
) -> float | None:
    """O(n log n) Kendall tau-b with ties in either variable."""

    if len(xs) < 2 or len(xs) != len(ys):
        return None
    pairs = sorted(zip(xs, ys), key=lambda item: (item[0], item[1]))
    y_values = sorted(set(ys))
    y_rank = {value: index + 1 for index, value in enumerate(y_values)}
    tree = [0] * (len(y_values) + 1)

    def add(index: int) -> None:
        while index < len(tree):
            tree[index] += 1
            index += index & -index

    def prefix(index: int) -> int:
        total = 0
        while index > 0:
            total += tree[index]
            index -= index & -index
        return total

    concordant = 0
    discordant = 0
    processed = 0
    start = 0
    while start < len(pairs):
        end = start + 1
        while end < len(pairs) and pairs[end][0] == pairs[start][0]:
            end += 1
        for _, y in pairs[start:end]:
            rank = y_rank[y]
            less = prefix(rank - 1)
            less_or_equal = prefix(rank)
            concordant += less
            discordant += processed - less_or_equal
        for _, y in pairs[start:end]:
            add(y_rank[y])
        processed += end - start
        start = end

    total_pairs = len(pairs) * (len(pairs) - 1) // 2
    x_counts = Counter(xs)
    y_counts = Counter(ys)
    tied_x = sum(count * (count - 1) // 2 for count in x_counts.values())
    tied_y = sum(count * (count - 1) // 2 for count in y_counts.values())
    denominator = math.sqrt(float(total_pairs - tied_x) * float(total_pairs - tied_y))
    if denominator == 0.0:
        return None
    return (concordant - discordant) / denominator


def _correlation_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    feature_name: str,
    target_name: str,
) -> dict[str, Any]:
    pairs = [
        (
            row["features"].get(feature_name),
            row["targets"].get(target_name),
        )
        for row in rows
    ]
    selected = [
        (float(feature), float(target))
        for feature, target in pairs
        if feature is not None and target is not None
    ]
    xs = [feature for feature, _ in selected]
    ys = [target for _, target in selected]
    return {
        "candidate_pairs": len(selected),
        "coverage_rate": (0.0 if not rows else len(selected) / float(len(rows))),
        "feature_unique_values": len(set(xs)),
        "target_unique_values": len(set(ys)),
        "spearman_rho": spearman_correlation(xs, ys),
        "kendall_tau_b": kendall_tau_b(xs, ys),
    }


def _top1_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    feature_name: str,
    target_name: str,
) -> dict[str, Any]:
    grouped: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["seed"]), int(row["step"]))].append(row)
    evaluated = 0
    informative = 0
    deterministic_hits = 0
    informative_deterministic_hits = 0
    overlap_hits = 0
    informative_overlap_hits = 0
    feature_ties = 0
    target_ties = 0
    for candidates in grouped.values():
        eligible = [
            row
            for row in candidates
            if row["features"].get(feature_name) is not None
            and row["targets"].get(target_name) is not None
        ]
        if not eligible:
            continue
        evaluated += 1
        feature_best = max(float(row["features"][feature_name]) for row in eligible)
        target_best = max(float(row["targets"][target_name]) for row in eligible)
        feature_actions = {
            int(row["action"])
            for row in eligible
            if float(row["features"][feature_name]) == feature_best
        }
        target_actions = {
            int(row["action"])
            for row in eligible
            if float(row["targets"][target_name]) == target_best
        }
        deterministic_action = min(feature_actions)
        deterministic_hit = deterministic_action in target_actions
        overlap_hit = bool(feature_actions.intersection(target_actions))
        deterministic_hits += int(deterministic_hit)
        overlap_hits += int(overlap_hit)
        feature_ties += int(len(feature_actions) > 1)
        target_ties += int(len(target_actions) > 1)
        target_values = {float(row["targets"][target_name]) for row in eligible}
        if len(target_values) > 1:
            informative += 1
            informative_deterministic_hits += int(deterministic_hit)
            informative_overlap_hits += int(overlap_hit)
    return {
        "eligible_decisions": evaluated,
        "informative_decisions": informative,
        "deterministic_tie_break": "lowest action index",
        "deterministic_top1_hits": deterministic_hits,
        "deterministic_top1_accuracy": (
            0.0 if evaluated == 0 else deterministic_hits / float(evaluated)
        ),
        "tie_aware_top_set_overlap_hits": overlap_hits,
        "tie_aware_top_set_overlap_rate": (
            0.0 if evaluated == 0 else overlap_hits / float(evaluated)
        ),
        "informative_deterministic_top1_hits": informative_deterministic_hits,
        "informative_deterministic_top1_accuracy": (
            0.0
            if informative == 0
            else informative_deterministic_hits / float(informative)
        ),
        "informative_tie_aware_top_set_overlap_hits": informative_overlap_hits,
        "informative_tie_aware_top_set_overlap_rate": (
            0.0 if informative == 0 else informative_overlap_hits / float(informative)
        ),
        "feature_tie_decisions": feature_ties,
        "target_tie_decisions": target_ties,
    }


def build_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        target: {
            feature: {
                "correlation": _correlation_summary(
                    rows,
                    feature_name=feature,
                    target_name=target,
                ),
                "ranking": _top1_summary(
                    rows,
                    feature_name=feature,
                    target_name=target,
                ),
            }
            for feature in FEATURE_NAMES
        }
        for target in TARGET_NAMES
    }


def _status_coverage(
    rows: Sequence[Mapping[str, Any]],
    status_counts: Mapping[str, Any],
    *,
    source_candidate_records: int,
) -> dict[str, Any]:
    predicted_available = sum(
        row["features"]["v2_predicted_chain_potential"] is not None for row in rows
    )
    complete = sum(bool(row["v2"]["search_complete"]) for row in rows)
    positive = sum(bool(row["v2"]["exists"]) for row in rows)
    return {
        "source_candidate_records": int(source_candidate_records),
        "eligible_candidate_records": len(rows),
        "excluded_candidate_records": max(0, source_candidate_records - len(rows)),
        "candidate_eligibility_rate": (
            0.0
            if source_candidate_records == 0
            else len(rows) / float(source_candidate_records)
        ),
        "evaluation_status_counts": dict(status_counts),
        "predicted_feature_available_records": predicted_available,
        "predicted_feature_coverage_rate": (
            0.0 if not rows else predicted_available / float(len(rows))
        ),
        "search_complete_records": complete,
        "search_complete_rate": (0.0 if not rows else complete / float(len(rows))),
        "positive_potential_records": positive,
    }


def _feature_contract(budget: BuildPotentialBudget) -> dict[str, Any]:
    return {
        "schema_version": BUILD_POTENTIAL_SCHEMA_VERSION,
        "style_neutral": True,
        "named_chain_shape_features": [],
        "candidate_cohort": {
            "board_reconstruction": (
                "replay each seed using frozen current.selected_action between "
                "decisions, then apply each current candidate.best_path"
            ),
            "eligibility": (
                "non-empty current best_path and non-null current/reference "
                "candidate values"
            ),
            "target_source": "same-action bounded-reference candidate",
        },
        "fields": {
            "v2_predicted_chain_potential": "normalized latent chain/cost/alternative strength; higher is better",
            "v2_ignition_readiness": "1 - normalized minimal ignition cost; higher is better",
            "v2_alternative_robustness": "bounded trigger-equivalence class count; higher is better",
            "v2_continuation_flexibility": "bounded two-cell column headroom; higher is better",
            "v2_danger_margin": "1 - board danger ratio; higher is better",
            "v2_composite": {
                "weights": dict(COMPOSITE_WEIGHTS),
                "semantics": "fixed diagnostic projection; not a trained score or quality gate",
            },
            "v1_scalar": "legacy chain_count - 0.25 * (required_puyos - 1); diagnostic baseline only",
        },
        "missing_value_semantics": (
            "null means the bounded v2 search exhausted its budget before a "
            "potential estimate; it is excluded rather than coerced to zero"
        ),
        "budget": budget.to_dict(),
    }


def _build_summary(
    *,
    source: Mapping[str, Any],
    config: EvaluationConfig,
    first: Mapping[str, Any],
    repetitions: Sequence[Mapping[str, Any]],
    migration: Mapping[str, Any],
    implementation_files: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = first["rows"]
    digests = [str(result["digest"]) for result in repetitions]
    replay_issues = [
        issue for result in repetitions for issue in result["replay_issues"]
    ]
    recompute_checks = sum(
        int(result["migration_recompute"]["checks"]) for result in repetitions
    )
    recompute_matches = sum(
        int(result["migration_recompute"]["matches"]) for result in repetitions
    )
    budget_violations = sum(int(result["budget_violations"]) for result in repetitions)
    determinism = {
        "passed": len(set(digests)) == 1,
        "repetitions": len(repetitions),
        "digests": digests,
        "cache_modes": [
            "enabled" if result["cache"]["enabled"] else "disabled"
            for result in repetitions
        ],
        "cache_evidence": [result["cache"] for result in repetitions],
        "scope": "all compact feature records; no wall-clock fields",
    }
    evaluation_completed = (
        bool(rows)
        and not replay_issues
        and budget_violations == 0
        and recompute_checks == recompute_matches
        and bool(migration["passed"])
        and determinism["passed"]
    )
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "git_commit": git_commit(),
        "evaluation_completed": evaluation_completed,
        "quality_gate": None,
        "interpretation": {
            "reference_predicted_max_chain": (
                "direct bounded-search target; sparse ties are reported explicitly"
            ),
            "reference_candidate_value": (
                "auxiliary legacy target containing the old board-shape heuristic; "
                "negative correlations are a measured design difference, not a failure"
            ),
            "claims": "observational evidence only; no superiority claim",
            "correlation_unit": (
                "eligible candidate rows pooled across frozen decisions; exact "
                "ties use average ranks for Spearman and tau-b for Kendall"
            ),
            "ranking_unit": (
                "one frozen decision; lowest action is the deterministic tie-break, "
                "with tie-aware top-set overlap reported separately"
            ),
        },
        "source": dict(source),
        "implementation_files": [dict(item) for item in implementation_files],
        "config": config.to_dict(),
        "compatibility": {
            "common_puyo_165_artifact_schema_changed": False,
            "learned_analyzer_tensor_changed": False,
            "learned_analyzer_feature_count": 77,
            "build_potential_schema_version": BUILD_POTENTIAL_SCHEMA_VERSION,
        },
        "feature_contract": _feature_contract(config.budget),
        "coverage": _status_coverage(
            rows,
            first["status_counts"],
            source_candidate_records=int(migration["candidate_records"]),
        ),
        "metrics": build_metrics(rows),
        "migration": {
            "fieldless_projection": dict(migration),
            "board_recompute": {
                "checks": recompute_checks,
                "matches": recompute_matches,
                "passed": recompute_checks == recompute_matches,
            },
        },
        "budget": {
            "violations": budget_violations,
            "passed": budget_violations == 0,
        },
        "replay": {
            "issues": replay_issues[:20],
            "issue_count": len(replay_issues),
            "passed": not replay_issues,
        },
        "determinism": determinism,
        "feature_records_sha256": first["digest"],
    }


def _format_metric(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.4f}"


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    coverage = summary["coverage"]
    determinism = summary["determinism"]
    lines = [
        "# PUYO-166 BuildPotential v2 benchmark",
        "",
        f"- frozen source: `{summary['source']['path']}` (`{summary['source']['sha256']}`)",
        f"- decisions/candidates: {summary['source']['selected_decisions']}/{coverage['eligible_candidate_records']}",
        f"- v2 feature coverage: {coverage['predicted_feature_coverage_rate']:.3f}",
        f"- overall evaluation: **{'PASS' if summary['evaluation_completed'] else 'FAIL'}**",
        f"- frozen replay: **{'PASS' if summary['replay']['passed'] else 'FAIL'}** ({summary['replay']['issue_count']} issues)",
        f"- deterministic cache on/off replay: **{'PASS' if determinism['passed'] else 'FAIL'}**",
        f"- bounded budget: **{'PASS' if summary['budget']['passed'] else 'FAIL'}**",
        f"- migration: **{'PASS' if summary['migration']['fieldless_projection']['passed'] and summary['migration']['board_recompute']['passed'] else 'FAIL'}**",
        "",
        "This is observational evidence, not a quality gate. The bounded reference is not an oracle. In particular, `reference_candidate_value` includes the legacy board-shape heuristic, so negative correlations with the new danger/flexibility contract are reported as design differences rather than improvements or regressions.",
    ]
    for target in TARGET_NAMES:
        lines.extend(
            [
                "",
                f"## {target}",
                "",
                "| feature | pairs | rho | tau-b | top1 | informative top1 | feature/target tie decisions |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for feature in FEATURE_NAMES:
            metric = summary["metrics"][target][feature]
            correlation = metric["correlation"]
            ranking = metric["ranking"]
            lines.append(
                f"| `{feature}` | {correlation['candidate_pairs']} | "
                f"{_format_metric(correlation['spearman_rho'])} | "
                f"{_format_metric(correlation['kendall_tau_b'])} | "
                f"{ranking['deterministic_top1_accuracy']:.4f} | "
                f"{ranking['informative_deterministic_top1_accuracy']:.4f} "
                f"({ranking['informative_decisions']}) | "
                f"{ranking['feature_tie_decisions']}/{ranking['target_tie_decisions']} |"
            )
    status = coverage["evaluation_status_counts"]
    lines.extend(
        [
            "",
            "## Coverage and migration semantics",
            "",
            f"- evaluation status counts: `{json.dumps(status, sort_keys=True)}`",
            f"- field-less v1 positive values: `{summary['migration']['fieldless_projection']['legacy_positive_records']}` -> `legacy_partial`",
            f"- field-less v1 zeros: `{summary['migration']['fieldless_projection']['legacy_zero_records']}` -> `unknown` (not coerced to evaluated zero)",
            f"- board-backed recomputations: {summary['migration']['board_recompute']['matches']}/{summary['migration']['board_recompute']['checks']} exact matches",
            "",
            "Ranking uses the lowest action index as the deterministic tie-break. Tie-aware top-set overlap and target/feature tie counts remain available in `benchmark_summary.json`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    budget = BuildPotentialBudget(
        max_added_puyos=args.max_added_puyos,
        max_pattern_nodes=args.max_pattern_nodes,
        max_resolution_nodes=args.max_resolution_nodes,
        max_alternatives=args.max_alternatives,
        max_continuation_actions=args.max_continuation_actions,
        max_recovery_puyos=args.max_recovery_puyos,
    )
    config = EvaluationConfig(
        budget=budget,
        workers=args.workers,
        repetitions=args.repetitions,
        decision_limit=args.decision_limit,
    )
    records, source = load_frozen_decisions(
        args.source,
        decision_limit=args.decision_limit,
    )
    if source["sha256"] != args.expected_source_sha256:
        raise ValueError(
            "frozen PUYO-165 source hash differs: "
            f"expected {args.expected_source_sha256}, found {source['sha256']}"
        )
    source["expected_sha256"] = args.expected_source_sha256
    source["hash_guard_passed"] = True
    migration = audit_v1_projection(records)
    implementation_files = implementation_evidence()
    repetitions = []
    for repetition in range(config.repetitions):
        result = evaluate_records(
            records,
            budget=budget,
            workers=config.workers,
            use_cache=repetition % 2 == 0,
        )
        repetitions.append(result)
        print(
            f"repetition {repetition + 1}/{config.repetitions}: "
            f"cache={'on' if result['cache']['enabled'] else 'off'} "
            f"records={len(result['rows'])} digest={result['digest']}",
            flush=True,
        )
    summary = _build_summary(
        source=source,
        config=config,
        first=repetitions[0],
        repetitions=repetitions,
        migration=migration,
        implementation_files=implementation_files,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "source_evidence.json", source)
    _write_json(output_dir / "determinism.json", summary["determinism"])
    _write_json(
        output_dir / "feature_records.json",
        {
            "schema_version": FEATURE_RECORD_SCHEMA_VERSION,
            "source_sha256": source["sha256"],
            "records": repetitions[0]["rows"],
        },
        compact=True,
    )
    _write_json(output_dir / "benchmark_summary.json", summary)
    _write_report(output_dir / "benchmark_report.md", summary)
    artifact_paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "name": "puyo-v1-7-2-build-potential-v2",
        "created_at_utc": summary["created_at_utc"],
        "git_commit": summary["git_commit"],
        "evaluation_completed": summary["evaluation_completed"],
        "quality_gate": None,
        "source": source,
        "implementation_files": implementation_files,
        "selection_rule": source["selection_rule"],
        "config": config.to_dict(),
        "coverage": summary["coverage"],
        "feature_records_sha256": summary["feature_records_sha256"],
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifact_paths
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    return summary


def verify_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.artifact_dir)
    manifest = _read_json(output_dir / "benchmark_manifest.json")
    issues: list[str] = []
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected benchmark manifest schema")
    for artifact in manifest.get("artifacts", ()):
        path = output_dir / str(artifact.get("path", ""))
        if not path.is_file():
            issues.append(f"missing artifact: {path}")
        elif artifact.get("sha256") != file_sha256(path):
            issues.append(f"artifact hash mismatch: {path}")
    source = manifest.get("source", {})
    source_path = Path(str(source.get("path", "")))
    if not source_path.is_file():
        issues.append("frozen PUYO-165 source is missing")
    elif file_sha256(source_path) != source.get("sha256"):
        issues.append("frozen PUYO-165 source hash differs")
    if source.get("sha256") != source.get("expected_sha256"):
        issues.append("frozen PUYO-165 source hash guard differs")
    if not source.get("hash_guard_passed"):
        issues.append("frozen PUYO-165 source hash guard did not pass")
    for implementation in manifest.get("implementation_files", ()):
        relative = str(implementation.get("path", ""))
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            issues.append(f"implementation file is missing: {relative}")
        elif file_sha256(path) != implementation.get("sha256"):
            issues.append(f"implementation file hash differs: {relative}")
    summary = _read_json(output_dir / "benchmark_summary.json")
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected benchmark summary schema")
    if not summary.get("evaluation_completed"):
        issues.append("benchmark evaluation is incomplete")
    if summary.get("quality_gate") is not None:
        issues.append("observational benchmark must not define a quality gate")
    feature_payload = _read_json(output_dir / "feature_records.json")
    if feature_payload.get("schema_version") != FEATURE_RECORD_SCHEMA_VERSION:
        issues.append("unexpected feature record schema")
    rows = feature_payload.get("records")
    if not isinstance(rows, list) or not rows:
        issues.append("feature records are missing")
        rows = []
    if rows and _digest(rows) != summary.get("feature_records_sha256"):
        issues.append("feature record digest differs")
    if summary.get("budget", {}).get("violations") != 0:
        issues.append("BuildPotential budget bounds were violated")
    if not summary.get("migration", {}).get("fieldless_projection", {}).get("passed"):
        issues.append("v1 projection migration failed")
    if not summary.get("migration", {}).get("board_recompute", {}).get("passed"):
        issues.append("board-backed migration recompute failed")
    result = {
        "passed": not issues,
        "issues": issues,
        "evaluation_completed": bool(summary.get("evaluation_completed")),
        "decisions": int(source.get("selected_decisions", 0)),
        "candidate_records": len(rows),
        "source_sha256": source.get("sha256"),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or verify the PUYO-166 BuildPotential v2 benchmark."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser(
        "run",
        help="replay frozen PUYO-165 decisions and write v2 evidence",
    )
    run.add_argument("--source", default=DEFAULT_SOURCE)
    run.add_argument(
        "--expected-source-sha256",
        default=FROZEN_SOURCE_SHA256,
        help="pin the exact frozen PUYO-165 decision artifact",
    )
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    run.add_argument("--decision-limit", type=int)
    defaults = BuildPotentialBudget()
    run.add_argument("--max-added-puyos", type=int, default=defaults.max_added_puyos)
    run.add_argument(
        "--max-pattern-nodes",
        type=int,
        default=defaults.max_pattern_nodes,
    )
    run.add_argument(
        "--max-resolution-nodes",
        type=int,
        default=defaults.max_resolution_nodes,
    )
    run.add_argument(
        "--max-alternatives",
        type=int,
        default=defaults.max_alternatives,
    )
    run.add_argument(
        "--max-continuation-actions",
        type=int,
        default=defaults.max_continuation_actions,
    )
    run.add_argument(
        "--max-recovery-puyos",
        type=int,
        default=defaults.max_recovery_puyos,
    )
    verify = subparsers.add_parser(
        "verify",
        help="verify source/artifact hashes, schemas, and bounded evidence",
    )
    verify.add_argument("--artifact-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    if args.command == "run":
        positive = (
            args.workers,
            args.repetitions,
            args.max_added_puyos,
            args.max_pattern_nodes,
            args.max_resolution_nodes,
            args.max_alternatives,
            args.max_continuation_actions,
        )
        if any(value <= 0 for value in positive):
            parser.error("benchmark counts and positive budgets must be positive")
        if args.max_recovery_puyos < 0:
            parser.error("max recovery puyos must be non-negative")
        if args.decision_limit is not None and args.decision_limit <= 0:
            parser.error("decision limit must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_benchmark(args)
        return 0 if summary["evaluation_completed"] else 1
    result = verify_benchmark(args)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
