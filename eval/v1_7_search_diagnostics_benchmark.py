"""PUYO-165 deterministic safe-build candidate coverage diagnostics."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import multiprocessing as mp
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import (
    BeamCandidateDiagnostics,
    BeamSearchConfig,
    BeamSearchDiagnostics,
    BeamSearchPolicy,
    clone_simulator,
)
from eval.v1_7_benchmark import _observation, _runtime_info, percentile
from puyo_env.actions import action_to_placement, legal_action_indices
from src.core.headless import HeadlessPuyoSimulator
from src.core.ojama import convert_score_to_ojama
from train.artifacts import (
    describe_artifact,
    file_sha256,
    git_commit,
    utc_timestamp,
)


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_search_diagnostics_benchmark.v1"
DECISION_SCHEMA_VERSION = "puyo.v1_7_search_diagnostics_decision.v1"
SEED_MANIFEST_SCHEMA_VERSION = "puyo.v1_7_safe_build_seed_manifest.v1"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-search-diagnostics"
DEFAULT_SEED_SOURCE = "docs/benchmarks/puyo-v1-7-2-build-main/seed_results.csv"
DEFAULT_SOURCE_CONFIG_ID = "d6-w48-p16"
LATENCY_MODE = "offline_wall_clock"
MINIMUM_CHAIN_COUNT = 10
FAILURE_CLASSES = (
    "candidate_coverage",
    "ranking",
    "horizon_or_uncertainty",
    "safety_constraint",
    "none",
)
PREMATURE_CLASSES = ("avoidable", "candidate_limited", "none")


@dataclass(frozen=True)
class SearchBudget:
    depth: int
    width: int
    probe_width: int

    def __post_init__(self) -> None:
        if min(self.depth, self.width, self.probe_width) <= 0:
            raise ValueError("search budgets must be positive")

    @property
    def config_id(self) -> str:
        return f"d{self.depth}-w{self.width}-p{self.probe_width}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "depth": int(self.depth),
            "width": int(self.width),
            "probe_width": int(self.probe_width),
            "scenarios": 1,
            "minimum_chain_count": MINIMUM_CHAIN_COUNT,
            "trigger_preservation": "required",
            "trace_paths": True,
        }

    def beam_config(self) -> BeamSearchConfig:
        return BeamSearchConfig(
            depth=self.depth,
            width=self.width,
            scenarios=1,
            minimum_chain_count=MINIMUM_CHAIN_COUNT,
            trigger_preservation="required",
            probe_width=self.probe_width,
            trace_paths=True,
        )


DEFAULT_CURRENT_BUDGET = SearchBudget(6, 48, 16)
DEFAULT_REFERENCE_BUDGET = SearchBudget(8, 64, 32)


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        serialized = json.dumps(payload, indent=2, sort_keys=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_seed_manifest(
    source: str | Path,
    *,
    source_config_id: str = DEFAULT_SOURCE_CONFIG_ID,
    games: int = 30,
    max_steps: int = 40,
) -> dict[str, Any]:
    """Reuse the PUYO-157 fixed safe-build seeds for a single configuration."""

    path = Path(source)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    seeds = sorted(
        {int(row["seed"]) for row in rows if row.get("config_id") == source_config_id}
    )
    if len(seeds) < games:
        raise ValueError(
            f"seed source has {len(seeds)} seeds for {source_config_id}; "
            f"expected at least {games}"
        )
    selected = seeds[:games]
    return {
        "schema_version": SEED_MANIFEST_SCHEMA_VERSION,
        "source": str(path),
        "source_sha256": file_sha256(path),
        "source_config_id": source_config_id,
        "games": len(selected),
        "moves_per_seed": int(max_steps),
        "seeds": selected,
    }


def _candidate_map(
    diagnostics: BeamSearchDiagnostics,
) -> dict[int, BeamCandidateDiagnostics]:
    return {candidate.action: candidate for candidate in diagnostics.candidates}


def _root_outcomes(simulator: HeadlessPuyoSimulator) -> list[dict[str, Any]]:
    outcomes = []
    for action in legal_action_indices(simulator):
        child = clone_simulator(simulator)
        result = child.step(action_to_placement(action))
        outcomes.append(
            {
                "action": int(action),
                "valid": bool(result.valid),
                "game_over": bool(result.game_over),
                "chain_count": int(result.chain_count),
                "score_delta": int(result.score_delta),
            }
        )
    return outcomes


def _search_payload(
    *,
    action: int,
    diagnostics: BeamSearchDiagnostics,
    latency_ms: float,
) -> dict[str, Any]:
    return {
        "selected_action": int(action),
        "latency_ms": float(latency_ms),
        "expanded_nodes": int(diagnostics.expanded_nodes),
        "potential_probe_count": int(diagnostics.potential_probe_count),
        "potential_cache_hits": int(diagnostics.potential_cache_hits),
        "candidates": [candidate.to_dict() for candidate in diagnostics.candidates],
    }


def _candidate_value(candidate: BeamCandidateDiagnostics | None) -> float | None:
    if candidate is None or candidate.candidate_value is None:
        return None
    return float(candidate.candidate_value)


def _failure_class(
    *,
    current_action: int,
    reference_action: int,
    reference_best_chain: int,
    reference_best_chain_depth: int,
    current_selected_chain: int,
    current_depth: int,
    reference_candidate_covered: bool,
    reference_path_trace: Sequence[Mapping[str, Any]],
) -> str:
    if reference_best_chain < MINIMUM_CHAIN_COUNT:
        return "horizon_or_uncertainty"
    if not reference_candidate_covered:
        if any(bool(item.get("safety_suppressed")) for item in reference_path_trace):
            return "safety_constraint"
        return "candidate_coverage"
    if reference_best_chain_depth > current_depth:
        return "horizon_or_uncertainty"
    if current_action == reference_action:
        if reference_best_chain > current_selected_chain:
            return "horizon_or_uncertainty"
        return "none"
    return "ranking"


def diagnose_decision(
    simulator: HeadlessPuyoSimulator,
    opponent: HeadlessPuyoSimulator,
    *,
    step_count: int,
    max_steps: int,
    current_policy: BeamSearchPolicy,
    reference_policy: BeamSearchPolicy,
    score_carry: int = 0,
    sent_ojama: int = 0,
) -> tuple[int, dict[str, Any]]:
    """Compare current and bounded-reference searches without mutating the board."""

    info = _runtime_info(
        simulator,
        opponent,
        step_count=step_count,
        max_steps=max_steps,
        score_carry=score_carry,
        sent_ojama=sent_ojama,
    )
    observation = _observation(
        simulator,
        opponent,
        step_count=step_count,
        max_steps=max_steps,
        sent_ojama=sent_ojama,
    )
    started = time.perf_counter()
    current_action = current_policy.select_action(observation, info)
    current_latency_ms = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    reference_action = reference_policy.select_action(observation, info)
    reference_latency_ms = (time.perf_counter() - started) * 1000.0
    current_diagnostics = current_policy.last_diagnostics
    reference_diagnostics = reference_policy.last_diagnostics
    if current_diagnostics is None or reference_diagnostics is None:
        raise RuntimeError("beam search did not produce diagnostics")

    current_candidates = _candidate_map(current_diagnostics)
    reference_candidates = _candidate_map(reference_diagnostics)
    reference_best = reference_candidates[reference_action]
    current_selected = current_candidates[current_action]
    reference_selected = reference_candidates.get(current_action)
    reference_in_current = current_candidates[reference_action]
    reference_best_value = _candidate_value(reference_best)
    reference_selected_value = _candidate_value(reference_selected)
    value_regret = (
        None
        if reference_best_value is None or reference_selected_value is None
        else max(0.0, reference_best_value - reference_selected_value)
    )
    reference_selected_chain = (
        0 if reference_selected is None else reference_selected.predicted_max_chain
    )
    chain_regret = max(
        0,
        reference_best.predicted_max_chain - reference_selected_chain,
    )
    root_outcomes = _root_outcomes(simulator)
    safe_no_fire_actions = [
        int(item["action"])
        for item in root_outcomes
        if item["valid"] and not item["game_over"] and item["chain_count"] == 0
    ]
    comparison_depth = min(
        current_policy.config.depth,
        len(reference_best.best_path),
    )
    reference_prefix = reference_best.best_path[:comparison_depth]
    reference_path_trace = current_policy.candidate_path_diagnostics(reference_prefix)
    reference_candidate_covered = bool(
        reference_path_trace and reference_path_trace[-1]["final_prune"]
    )
    failure_class = _failure_class(
        current_action=current_action,
        reference_action=reference_action,
        reference_best_chain=reference_best.predicted_max_chain,
        reference_best_chain_depth=reference_best.best_chain_depth,
        current_selected_chain=current_selected.predicted_max_chain,
        current_depth=current_policy.config.depth,
        reference_candidate_covered=reference_candidate_covered,
        reference_path_trace=reference_path_trace,
    )
    record = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "step": int(step_count),
        "latency_mode": LATENCY_MODE,
        "legal_root_actions": [int(item["action"]) for item in root_outcomes],
        "root_outcomes": root_outcomes,
        "safe_no_fire_actions": safe_no_fire_actions,
        "current": _search_payload(
            action=current_action,
            diagnostics=current_diagnostics,
            latency_ms=current_latency_ms,
        ),
        "reference": _search_payload(
            action=reference_action,
            diagnostics=reference_diagnostics,
            latency_ms=reference_latency_ms,
        ),
        "comparison": {
            "actions_differ": current_action != reference_action,
            "reference_action_covered": reference_in_current.final_prune_depth > 0,
            "reference_candidate_covered": reference_candidate_covered,
            "reference_best_path": list(reference_best.best_path),
            "reference_best_chain_depth": int(reference_best.best_chain_depth),
            "reference_path_trace_in_current": list(reference_path_trace),
            "reference_best_chain": int(reference_best.predicted_max_chain),
            "current_selected_chain": int(current_selected.predicted_max_chain),
            "selected_chain_under_reference": int(reference_selected_chain),
            "chain_regret": int(chain_regret),
            "value_regret": value_regret,
            "failure_class": failure_class,
        },
    }
    return int(current_action), record


def validate_decision_record(record: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if record.get("schema_version") != DECISION_SCHEMA_VERSION:
        issues.append("unexpected decision schema")
    legal = record.get("legal_root_actions")
    if not isinstance(legal, list) or not legal:
        issues.append("legal_root_actions must be a non-empty list")
        legal = []
    for search_name in ("current", "reference"):
        search = record.get(search_name)
        if not isinstance(search, Mapping):
            issues.append(f"{search_name} search payload is missing")
            continue
        candidates = search.get("candidates")
        if not isinstance(candidates, list):
            issues.append(f"{search_name}.candidates must be a list")
            continue
        actions = []
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                issues.append(f"{search_name} candidate must be an object")
                continue
            actions.append(candidate.get("action"))
            stages = candidate.get("stages")
            if not isinstance(stages, Mapping) or any(
                key not in stages
                for key in (
                    "base_prune_depth",
                    "potential_probe_depth",
                    "final_prune_depth",
                    "safety_suppressed_depth",
                )
            ):
                issues.append(f"{search_name} candidate stages are incomplete")
            if any(
                key not in candidate
                for key in (
                    "predicted_max_chain",
                    "best_chain_depth",
                    "best_path",
                    "fire_cost",
                )
            ):
                issues.append(f"{search_name} candidate outcome is incomplete")
        if actions != legal:
            issues.append(f"{search_name} candidate actions do not match legal actions")
    comparison = record.get("comparison")
    if not isinstance(comparison, Mapping):
        issues.append("comparison is missing")
    elif comparison.get("failure_class") not in FAILURE_CLASSES:
        issues.append("unsupported failure_class")
    elif not isinstance(comparison.get("reference_path_trace_in_current"), list):
        issues.append("reference candidate path trace is missing")
    outcome = record.get("outcome")
    if not isinstance(outcome, Mapping):
        issues.append("outcome is missing")
    elif outcome.get("premature_classification") not in PREMATURE_CLASSES:
        issues.append("unsupported premature_classification")
    return issues


def _deterministic_projection(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _deterministic_projection(item)
            for key, item in value.items()
            if key != "latency_ms"
        }
    if isinstance(value, list):
        return [_deterministic_projection(item) for item in value]
    return value


def _digest(value: Any) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _mean(total: float, count: int) -> float:
    return 0.0 if count == 0 else float(total) / float(count)


def _count_map(values: Sequence[str], keys: Sequence[str]) -> dict[str, int]:
    counter = Counter(values)
    return {key: int(counter.get(key, 0)) for key in keys}


def _aggregate_seed(
    seed: int,
    decisions: Sequence[Mapping[str, Any]],
    *,
    max_steps: int,
) -> dict[str, Any]:
    comparisons = [item["comparison"] for item in decisions]
    outcomes = [item["outcome"] for item in decisions]
    current_latencies = [float(item["current"]["latency_ms"]) for item in decisions]
    reference_latencies = [float(item["reference"]["latency_ms"]) for item in decisions]
    value_regrets = [
        float(item["value_regret"])
        for item in comparisons
        if item.get("value_regret") is not None
    ]
    best_chains = [int(item["reference_best_chain"]) for item in comparisons]
    selected_chains = [int(item["current_selected_chain"]) for item in comparisons]
    chain_regrets = [int(item["chain_regret"]) for item in comparisons]
    failure_classes = [str(item["failure_class"]) for item in comparisons]
    premature_classes = [str(item["premature_classification"]) for item in outcomes]
    coverage_count = sum(
        bool(item["reference_candidate_covered"]) for item in comparisons
    )
    root_coverage_count = sum(
        bool(item["reference_action_covered"]) for item in comparisons
    )
    game_over = bool(outcomes and outcomes[-1]["game_over"])
    return {
        "seed": int(seed),
        "decisions": len(decisions),
        "moves_per_seed": int(max_steps),
        "candidate_coverage_count": int(coverage_count),
        "candidate_coverage_rate": _mean(coverage_count, len(decisions)),
        "root_action_coverage_count": int(root_coverage_count),
        "root_action_coverage_rate": _mean(root_coverage_count, len(decisions)),
        "best_reachable_chain_mean": _mean(sum(best_chains), len(best_chains)),
        "best_reachable_chain_max": max(best_chains, default=0),
        "selected_chain_mean": _mean(sum(selected_chains), len(selected_chains)),
        "selected_chain_max": max(selected_chains, default=0),
        "chain_regret_mean": _mean(sum(chain_regrets), len(chain_regrets)),
        "chain_regret_max": max(chain_regrets, default=0),
        "value_regret_mean": _mean(sum(value_regrets), len(value_regrets)),
        "failure_class_counts": _count_map(failure_classes, FAILURE_CLASSES),
        "premature_classification_counts": _count_map(
            premature_classes,
            PREMATURE_CLASSES,
        ),
        "actual_max_chain": max(
            (int(item["chain_count"]) for item in outcomes),
            default=0,
        ),
        "game_over_before_limit": game_over and len(decisions) < max_steps,
        "current_latency_p50_ms": percentile(current_latencies, 0.50),
        "current_latency_p95_ms": percentile(current_latencies, 0.95),
        "reference_latency_p50_ms": percentile(reference_latencies, 0.50),
        "reference_latency_p95_ms": percentile(reference_latencies, 0.95),
        "_best_chain_sum": sum(best_chains),
        "_selected_chain_sum": sum(selected_chains),
        "_chain_regret_sum": sum(chain_regrets),
        "_value_regret_sum": sum(value_regrets),
        "_value_regret_count": len(value_regrets),
        "_current_latencies_ms": current_latencies,
        "_reference_latencies_ms": reference_latencies,
    }


def _public_seed_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if not key.startswith("_")}


def evaluate_seed(
    seed: int,
    *,
    max_steps: int,
    current_budget: SearchBudget,
    reference_budget: SearchBudget,
    include_decisions: bool = True,
) -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=seed)
    opponent = HeadlessPuyoSimulator(seed=seed + 1_000_003)
    current_policy = BeamSearchPolicy(current_budget.beam_config())
    reference_policy = BeamSearchPolicy(reference_budget.beam_config())
    carry = 0
    sent = 0
    decisions: list[dict[str, Any]] = []
    for step_count in range(max_steps):
        action, record = diagnose_decision(
            simulator,
            opponent,
            step_count=step_count,
            max_steps=max_steps,
            current_policy=current_policy,
            reference_policy=reference_policy,
            score_carry=carry,
            sent_ojama=sent,
        )
        result = simulator.step(action_to_placement(action))
        conversion = convert_score_to_ojama(result.attack_score_delta, carry)
        carry = conversion.carry
        sent += conversion.units
        premature = 0 < int(result.chain_count) < MINIMUM_CHAIN_COUNT
        if not premature:
            premature_classification = "none"
        elif record["safe_no_fire_actions"]:
            premature_classification = "avoidable"
        else:
            premature_classification = "candidate_limited"
        record["seed"] = int(seed)
        record["outcome"] = {
            "chain_count": int(result.chain_count),
            "score_delta": int(result.score_delta),
            "attack_score_delta": int(result.attack_score_delta),
            "game_over": bool(result.game_over),
            "premature_fire": premature,
            "premature_classification": premature_classification,
        }
        decisions.append(record)
        if result.game_over:
            break
    summary = _aggregate_seed(seed, decisions, max_steps=max_steps)
    deterministic_decisions = _deterministic_projection(decisions)
    result = {
        "summary": summary,
        "deterministic_digest": _digest(deterministic_decisions),
    }
    if include_decisions:
        result["decisions"] = decisions
    return result


def _evaluate_seed_task(
    task: tuple[int, int, SearchBudget, SearchBudget, bool],
) -> dict[str, Any]:
    seed, max_steps, current_budget, reference_budget, include_decisions = task
    return evaluate_seed(
        seed,
        max_steps=max_steps,
        current_budget=current_budget,
        reference_budget=reference_budget,
        include_decisions=include_decisions,
    )


def _merge_counts(
    summaries: Sequence[Mapping[str, Any]],
    key: str,
    values: Sequence[str],
) -> dict[str, int]:
    return {
        value: sum(int(summary[key].get(value, 0)) for summary in summaries)
        for value in values
    }


def aggregate_run(seed_summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    decisions = sum(int(item["decisions"]) for item in seed_summaries)
    coverage = sum(int(item["candidate_coverage_count"]) for item in seed_summaries)
    root_coverage = sum(
        int(item["root_action_coverage_count"]) for item in seed_summaries
    )
    best_sum = sum(int(item["_best_chain_sum"]) for item in seed_summaries)
    selected_sum = sum(int(item["_selected_chain_sum"]) for item in seed_summaries)
    chain_regret_sum = sum(int(item["_chain_regret_sum"]) for item in seed_summaries)
    value_regret_sum = sum(float(item["_value_regret_sum"]) for item in seed_summaries)
    value_regret_count = sum(
        int(item["_value_regret_count"]) for item in seed_summaries
    )
    current_latencies = [
        value for item in seed_summaries for value in item["_current_latencies_ms"]
    ]
    reference_latencies = [
        value for item in seed_summaries for value in item["_reference_latencies_ms"]
    ]
    premature_counts = _merge_counts(
        seed_summaries,
        "premature_classification_counts",
        PREMATURE_CLASSES,
    )
    early_game_overs = sum(
        bool(item["game_over_before_limit"]) for item in seed_summaries
    )
    mean_game_max_chain = _mean(
        sum(int(item["actual_max_chain"]) for item in seed_summaries),
        len(seed_summaries),
    )
    original_gate = {
        "mean_max_chain": mean_game_max_chain >= 10.0,
        "premature_fire": (
            premature_counts["avoidable"] + premature_counts["candidate_limited"]
        )
        == 0,
        "game_over": early_game_overs == 0,
    }
    return {
        "games": len(seed_summaries),
        "decisions": decisions,
        "candidate_coverage_count": coverage,
        "candidate_coverage_rate": _mean(coverage, decisions),
        "root_action_coverage_count": root_coverage,
        "root_action_coverage_rate": _mean(root_coverage, decisions),
        "best_reachable_chain_mean": _mean(best_sum, decisions),
        "best_reachable_chain_max": max(
            (int(item["best_reachable_chain_max"]) for item in seed_summaries),
            default=0,
        ),
        "selected_chain_mean": _mean(selected_sum, decisions),
        "selected_chain_max": max(
            (int(item["selected_chain_max"]) for item in seed_summaries),
            default=0,
        ),
        "chain_regret_mean": _mean(chain_regret_sum, decisions),
        "chain_regret_max": max(
            (int(item["chain_regret_max"]) for item in seed_summaries),
            default=0,
        ),
        "value_regret_mean": _mean(value_regret_sum, value_regret_count),
        "failure_class_counts": _merge_counts(
            seed_summaries,
            "failure_class_counts",
            FAILURE_CLASSES,
        ),
        "premature_classification_counts": premature_counts,
        "mean_game_max_chain": mean_game_max_chain,
        "game_over_before_limit": early_game_overs,
        "latency": {
            "mode": LATENCY_MODE,
            "current_p50_ms": percentile(current_latencies, 0.50),
            "current_p95_ms": percentile(current_latencies, 0.95),
            "reference_p50_ms": percentile(reference_latencies, 0.50),
            "reference_p95_ms": percentile(reference_latencies, 0.95),
        },
        "original_build_main_gate": {
            **original_gate,
            "passed": all(original_gate.values()),
        },
    }


def _deterministic_aggregate(aggregate: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in aggregate.items() if key != "latency"}


def evaluate_repetition(
    seeds: Sequence[int],
    *,
    max_steps: int,
    current_budget: SearchBudget,
    reference_budget: SearchBudget,
    workers: int,
    include_decisions: bool,
) -> dict[str, Any]:
    tasks = [
        (
            int(seed),
            int(max_steps),
            current_budget,
            reference_budget,
            include_decisions,
        )
        for seed in seeds
    ]
    if workers == 1:
        results = [_evaluate_seed_task(task) for task in tasks]
    else:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context("spawn"),
        ) as executor:
            results = list(executor.map(_evaluate_seed_task, tasks, chunksize=1))
    results.sort(key=lambda item: int(item["summary"]["seed"]))
    seed_summaries = [item["summary"] for item in results]
    aggregate = aggregate_run(seed_summaries)
    digest_payload = {
        "seed_digests": [
            {
                "seed": int(item["summary"]["seed"]),
                "digest": item["deterministic_digest"],
            }
            for item in results
        ],
        "aggregate": _deterministic_aggregate(aggregate),
    }
    decisions = [decision for item in results for decision in item.get("decisions", ())]
    return {
        "digest": _digest(digest_payload),
        "seed_digests": digest_payload["seed_digests"],
        "seed_summaries": seed_summaries,
        "aggregate": aggregate,
        "decisions": decisions,
    }


def _dominant_failure(counts: Mapping[str, Any]) -> tuple[str, int]:
    failures = [
        (name, int(counts.get(name, 0))) for name in FAILURE_CLASSES if name != "none"
    ]
    return max(failures, key=lambda item: (item[1], -FAILURE_CLASSES.index(item[0])))


def _write_report(
    path: Path,
    *,
    summary: Mapping[str, Any],
    seed_summaries: Sequence[Mapping[str, Any]],
) -> None:
    aggregate = summary["aggregate"]
    dominant, dominant_count = _dominant_failure(aggregate["failure_class_counts"])
    explanations = {
        "candidate_coverage": "the current beam drops the bounded-reference path prefix before final coverage",
        "ranking": "the bounded-reference root survives, but current ranking selects another root",
        "horizon_or_uncertainty": "both searches agree or the reference remains below target within its bounded horizon",
        "safety_constraint": "the bounded-reference root is rejected or suppressed by the safety rule",
    }
    premature = aggregate["premature_classification_counts"]
    determinism = summary["determinism"]
    lines = [
        "# PUYO-165 safe-build search diagnostics",
        "",
        f"- current: `{summary['config']['current']['config_id']}`",
        f"- reference: `{summary['config']['reference']['config_id']}` (bounded reference, not an oracle)",
        f"- deterministic replay: **{'PASS' if determinism['passed'] else 'FAIL'}** ({determinism['repetitions']} runs)",
        f"- original build_main gate: **{'PASS' if aggregate['original_build_main_gate']['passed'] else 'BLOCKED'}**",
        f"- dominant diagnosis: **{dominant}** — {dominant_count} decisions; {explanations[dominant]}",
        "",
        "## Aggregate",
        "",
        f"- reference-path candidate coverage: {aggregate['candidate_coverage_rate']:.3f}",
        f"- reference root-action coverage: {aggregate['root_action_coverage_rate']:.3f}",
        f"- mean best reachable chain: {aggregate['best_reachable_chain_mean']:.3f}",
        f"- mean selected chain: {aggregate['selected_chain_mean']:.3f}",
        f"- mean chain regret: {aggregate['chain_regret_mean']:.3f}",
        f"- mean game maximum chain: {aggregate['mean_game_max_chain']:.3f}",
        f"- premature fire: avoidable={premature['avoidable']}, candidate-limited={premature['candidate_limited']}",
        f"- early game-over: {aggregate['game_over_before_limit']}",
        f"- latency ({LATENCY_MODE}): current p50/p95={aggregate['latency']['current_p50_ms']:.2f}/{aggregate['latency']['current_p95_ms']:.2f} ms, reference p50/p95={aggregate['latency']['reference_p50_ms']:.2f}/{aggregate['latency']['reference_p95_ms']:.2f} ms",
        "",
        "## Failure classification",
        "",
        "| class | decisions |",
        "|---|---:|",
    ]
    for name in FAILURE_CLASSES:
        lines.append(f"| `{name}` | {aggregate['failure_class_counts'][name]} |")
    lines.extend(
        [
            "",
            "## Seed summary",
            "",
            "| seed | decisions | coverage | best chain | selected chain | regret | premature A/C | game over | current p95 ms | reference p95 ms |",
            "|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|",
        ]
    )
    for seed in seed_summaries:
        seed_premature = seed["premature_classification_counts"]
        lines.append(
            f"| {seed['seed']} | {seed['decisions']} | {seed['candidate_coverage_rate']:.3f} | "
            f"{seed['best_reachable_chain_mean']:.2f} | {seed['selected_chain_mean']:.2f} | "
            f"{seed['chain_regret_mean']:.2f} | {seed_premature['avoidable']}/{seed_premature['candidate_limited']} | "
            f"{'yes' if seed['game_over_before_limit'] else 'no'} | "
            f"{seed['current_latency_p95_ms']:.2f} | {seed['reference_latency_p95_ms']:.2f} |"
        )
    lines.extend(
        [
            "",
            "Determinism covers actions, candidate stages, predicted outcomes, regret, classifications, and latency-free aggregates. Wall-clock latency is intentionally excluded.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    current_budget = SearchBudget(
        args.current_depth,
        args.current_width,
        args.current_probe_width,
    )
    reference_budget = SearchBudget(
        args.reference_depth,
        args.reference_width,
        args.reference_probe_width,
    )
    if (
        reference_budget.depth < current_budget.depth
        or reference_budget.width < current_budget.width
        or reference_budget.probe_width < current_budget.probe_width
        or reference_budget == current_budget
    ):
        raise ValueError("reference budget must dominate and exceed current budget")
    seed_manifest = load_seed_manifest(
        args.seed_source,
        source_config_id=args.source_config_id,
        games=args.games,
        max_steps=args.max_steps,
    )
    repetitions = []
    for repetition in range(args.repetitions):
        result = evaluate_repetition(
            seed_manifest["seeds"],
            max_steps=args.max_steps,
            current_budget=current_budget,
            reference_budget=reference_budget,
            workers=args.workers,
            include_decisions=repetition == 0,
        )
        repetitions.append(result)
        print(
            f"repetition {repetition + 1}/{args.repetitions}: "
            f"digest={result['digest']} decisions={result['aggregate']['decisions']}",
            flush=True,
        )
    first = repetitions[0]
    digests = [item["digest"] for item in repetitions]
    determinism = {
        "repetitions": len(repetitions),
        "passed": len(set(digests)) == 1,
        "digests": digests,
        "scope": [
            "selected actions",
            "candidate survival stages",
            "predicted chains and fire cost",
            "regret and failure classification",
            "latency-free seed and configuration aggregates",
        ],
        "excluded_fields": ["current.latency_ms", "reference.latency_ms"],
    }
    public_seed_summaries = [
        _public_seed_summary(item) for item in first["seed_summaries"]
    ]
    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "git_commit": git_commit(),
        "evaluation_completed": (
            first["aggregate"]["games"] == args.games
            and first["aggregate"]["decisions"] > 0
        ),
        "latency_mode": LATENCY_MODE,
        "reference_semantics": "deterministic bounded reference; not an oracle",
        "metric_definitions": {
            "candidate_coverage": (
                "the bounded-reference best action-path prefix through the current "
                "depth survives the current final prune"
            ),
            "root_action_coverage": (
                "the bounded-reference best root action reaches any current final beam"
            ),
            "avoidable_premature_fire": (
                "a valid non-game-over no-fire legal root action existed"
            ),
            "candidate_limited_premature_fire": (
                "no valid non-game-over no-fire legal root action existed"
            ),
        },
        "config": {
            "current": current_budget.to_dict(),
            "reference": reference_budget.to_dict(),
            "games": int(args.games),
            "max_steps": int(args.max_steps),
            "workers": int(args.workers),
        },
        "seed_manifest": {
            "schema_version": seed_manifest["schema_version"],
            "source": seed_manifest["source"],
            "source_sha256": seed_manifest["source_sha256"],
            "source_config_id": seed_manifest["source_config_id"],
            "games": seed_manifest["games"],
        },
        "determinism": determinism,
        "aggregate": first["aggregate"],
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "seed_manifest.json", seed_manifest)
    _write_json(
        output_dir / "decision_records.json",
        {
            "schema_version": DECISION_SCHEMA_VERSION,
            "records": first["decisions"],
        },
        compact=True,
    )
    _write_json(
        output_dir / "seed_results.json",
        {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "seeds": public_seed_summaries,
        },
    )
    _write_json(output_dir / "determinism.json", determinism)
    _write_json(output_dir / "benchmark_summary.json", summary)
    _write_report(
        output_dir / "benchmark_report.md",
        summary=summary,
        seed_summaries=public_seed_summaries,
    )
    artifact_paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "name": "puyo-v1-7-2-search-diagnostics",
        "created_at_utc": summary["created_at_utc"],
        "git_commit": summary["git_commit"],
        "evaluation_completed": summary["evaluation_completed"],
        "determinism_passed": determinism["passed"],
        "config": summary["config"],
        "seed_manifest": summary["seed_manifest"],
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
    summary = _read_json(output_dir / "benchmark_summary.json")
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected benchmark summary schema")
    if not summary.get("evaluation_completed"):
        issues.append("benchmark evaluation is incomplete")
    if not summary.get("determinism", {}).get("passed"):
        issues.append("deterministic repetitions do not match")
    decisions_payload = _read_json(output_dir / "decision_records.json")
    records = decisions_payload.get("records", ())
    if decisions_payload.get("schema_version") != DECISION_SCHEMA_VERSION:
        issues.append("unexpected decision artifact schema")
    if not isinstance(records, list) or not records:
        issues.append("decision records are missing")
    else:
        for index, record in enumerate(records):
            record_issues = validate_decision_record(record)
            issues.extend(f"decision {index}: {issue}" for issue in record_issues)
    seed_manifest = _read_json(output_dir / "seed_manifest.json")
    if seed_manifest.get("schema_version") != SEED_MANIFEST_SCHEMA_VERSION:
        issues.append("unexpected seed manifest schema")
    result = {
        "passed": not issues,
        "issues": issues,
        "evaluation_completed": bool(summary.get("evaluation_completed")),
        "determinism_passed": bool(summary.get("determinism", {}).get("passed")),
        "games": int(summary.get("aggregate", {}).get("games", 0)),
        "decisions": int(summary.get("aggregate", {}).get("decisions", 0)),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or verify the PUYO-165 safe-build search diagnostics."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser(
        "run", help="run deterministic current/reference comparisons"
    )
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--seed-source", default=DEFAULT_SEED_SOURCE)
    run.add_argument("--source-config-id", default=DEFAULT_SOURCE_CONFIG_ID)
    run.add_argument("--games", type=int, default=30)
    run.add_argument("--max-steps", type=int, default=40)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--repetitions", type=int, default=2)
    run.add_argument("--current-depth", type=int, default=DEFAULT_CURRENT_BUDGET.depth)
    run.add_argument("--current-width", type=int, default=DEFAULT_CURRENT_BUDGET.width)
    run.add_argument(
        "--current-probe-width",
        type=int,
        default=DEFAULT_CURRENT_BUDGET.probe_width,
    )
    run.add_argument(
        "--reference-depth",
        type=int,
        default=DEFAULT_REFERENCE_BUDGET.depth,
    )
    run.add_argument(
        "--reference-width",
        type=int,
        default=DEFAULT_REFERENCE_BUDGET.width,
    )
    run.add_argument(
        "--reference-probe-width",
        type=int,
        default=DEFAULT_REFERENCE_BUDGET.probe_width,
    )
    verify = subparsers.add_parser("verify", help="verify artifact hashes and schemas")
    verify.add_argument("--artifact-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    if args.command == "run" and any(
        value <= 0
        for value in (
            args.games,
            args.max_steps,
            args.workers,
            args.repetitions,
            args.current_depth,
            args.current_width,
            args.current_probe_width,
            args.reference_depth,
            args.reference_width,
            args.reference_probe_width,
        )
    ):
        parser.error("benchmark counts and budgets must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        result = run_benchmark(args)
        return (
            0
            if result["evaluation_completed"] and result["determinism"]["passed"]
            else 1
        )
    result = verify_benchmark(args)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
