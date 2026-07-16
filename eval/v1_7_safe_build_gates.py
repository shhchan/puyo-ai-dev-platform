"""PUYO-170 two-stage safe-build capability and promotion gates.

The pre-training gate evaluates whether the K-best worker proposal contains a
safe path to a large chain.  The post-training gate evaluates the action that a
learned policy actually selects.  Keeping these inputs and results in separate
schemas prevents a strong bounded-reference candidate from being reported as a
learned-policy success.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import multiprocessing as mp
import os
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import (
    BUILD_POTENTIAL_SCHEMA_VERSION,
    BUILD_SCORING_V2,
    DIVERSE_CANDIDATE_MODE,
    BeamSearchConfig,
    BeamSearchPolicy,
    clone_simulator,
)
from agents.v1_7_strategy_manager import POLICY_TYPE
from agents.v1_7_tactics import load_tactic_registry
from agents.worker_proposals import (
    WORKER_PROPOSAL_V1_SCHEMA_VERSION,
    WorkerProposalBatch,
    WorkerProposalCandidate,
    build_worker_proposal_batch,
)
from eval.v1_7_benchmark import (
    _observation,
    _policy_spec,
    _runtime_info,
    evaluate_response_scenarios,
    percentile,
    run_safe_suite,
)
from eval.v1_7_bootstrap_benchmark import load_checkpoint_evidence
from eval.v1_7_search_diagnostics_benchmark import (
    DEFAULT_REFERENCE_BUDGET,
    DEFAULT_SEED_SOURCE,
    DEFAULT_SOURCE_CONFIG_ID,
    SearchBudget,
    load_seed_manifest,
)
from puyo_env.actions import (
    action_to_placement,
    legal_action_indices,
    legal_action_mask,
)
from src.core.headless import HeadlessPuyoSimulator
from src.core.ojama import convert_score_to_ojama
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


CAPABILITY_GATE_INPUT_SCHEMA_VERSION = "puyo.v1_7_training_capability_gate_input.v1"
CAPABILITY_GATE_RESULT_SCHEMA_VERSION = "puyo.v1_7_training_capability_gate_result.v1"
CAPABILITY_CANDIDATE_ARTIFACT_SCHEMA_VERSION = (
    "puyo.v1_7_training_capability_candidates.v1"
)
CAPABILITY_DECISION_SCHEMA_VERSION = "puyo.v1_7_training_capability_decision.v1"
PROMOTION_GATE_INPUT_SCHEMA_VERSION = "puyo.v1_7_promotion_gate_input.v1"
PROMOTION_GATE_RESULT_SCHEMA_VERSION = "puyo.v1_7_promotion_gate_result.v1"
GATE_SUMMARY_SCHEMA_VERSION = "puyo.v1_7_safe_build_gates.v1"
GATE_MANIFEST_SCHEMA_VERSION = "puyo.v1_7_safe_build_gate_manifest.v1"

DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-safe-build-gates"
DEFAULT_PROMOTION_CHECKPOINT = (
    "runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/checkpoints/"
    "bootstrap-v1-7-2-migrated.pt"
)
LATENCY_MODE = "observational_wall_clock_including_proposal_projection"
TARGET_CHAIN = 10
MINIMUM_GAMES = 30
MOVES_PER_GAME = 40
MINIMUM_TRAINING_SEEDS = 5
REQUIRED_THREAT_SCENARIOS = 6
PREMATURE_CLASSES = ("avoidable", "candidate_limited", "none")
REQUIRED_INTEGRATION_COMMITS = {
    "PUYO-158": "d1f8a0a23c483c83ffea4ffac926e2bc9c9bba40",
    "PUYO-129": "2a1e2d59d0d9f538be3c6053c9c4cd79134d17c5",
    "PUYO-169": "d55951ee2574a2c938efce4af43a840ff6ab7379",
}


@dataclass(frozen=True)
class CapabilityConfiguration:
    """One runtime candidate-generator configuration under gate evaluation."""

    config_id: str
    depth: int
    width: int
    probe_width: int
    candidate_limit: int

    def __post_init__(self) -> None:
        if not self.config_id:
            raise ValueError("capability configuration id is required")
        if min(self.depth, self.width, self.probe_width, self.candidate_limit) <= 0:
            raise ValueError("capability search budgets must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "depth": int(self.depth),
            "width": int(self.width),
            "probe_width": int(self.probe_width),
            "candidate_limit": int(self.candidate_limit),
            "candidate_mode": DIVERSE_CANDIDATE_MODE,
            "scoring_mode": BUILD_SCORING_V2,
            "build_potential_schema_version": BUILD_POTENTIAL_SCHEMA_VERSION,
            "minimum_chain_count": TARGET_CHAIN,
            "trigger_preservation": "required",
        }

    def beam_config(self) -> BeamSearchConfig:
        return BeamSearchConfig(
            depth=self.depth,
            width=self.width,
            scenarios=1,
            minimum_chain_count=TARGET_CHAIN,
            premature_chain_penalty=525.0,
            trigger_preservation="required",
            probe_width=self.probe_width,
            trace_paths=True,
            scoring_mode=BUILD_SCORING_V2,
            future_potential_weight=1.0,
            chain_shape_weight=1.0,
            danger_tolerance=0.65,
            build_potential_schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
            potential_probe_budget=self.probe_width * self.depth + 1,
            candidate_mode=DIVERSE_CANDIDATE_MODE,
            candidate_limit=self.candidate_limit,
        )


@dataclass(frozen=True)
class CapabilityThresholds:
    minimum_games: int = MINIMUM_GAMES
    moves_per_game: int = MOVES_PER_GAME
    minimum_mean_max_chain: float = float(TARGET_CHAIN)
    maximum_avoidable_candidate_gaps: int = 0
    maximum_forced_game_over_gaps: int = 0
    maximum_p95_latency_ms: float = 60.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_games": int(self.minimum_games),
            "moves_per_game": int(self.moves_per_game),
            "minimum_mean_max_chain": float(self.minimum_mean_max_chain),
            "maximum_avoidable_candidate_gaps": int(
                self.maximum_avoidable_candidate_gaps
            ),
            "maximum_forced_game_over_gaps": int(self.maximum_forced_game_over_gaps),
            "maximum_p95_latency_ms": float(self.maximum_p95_latency_ms),
        }


@dataclass(frozen=True)
class PromotionThresholds:
    minimum_games: int = MINIMUM_GAMES
    moves_per_game: int = MOVES_PER_GAME
    minimum_mean_max_chain: float = float(TARGET_CHAIN)
    maximum_safe_premature_fires: int = 0
    maximum_early_game_overs: int = 0
    required_threat_scenarios: int = REQUIRED_THREAT_SCENARIOS
    minimum_training_seeds: int = MINIMUM_TRAINING_SEEDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_games": int(self.minimum_games),
            "moves_per_game": int(self.moves_per_game),
            "minimum_mean_max_chain": float(self.minimum_mean_max_chain),
            "maximum_safe_premature_fires": int(self.maximum_safe_premature_fires),
            "maximum_early_game_overs": int(self.maximum_early_game_overs),
            "required_threat_scenarios": int(self.required_threat_scenarios),
            "minimum_training_seeds": int(self.minimum_training_seeds),
        }


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        sort_keys=True,
    )
    path.write_text(serialized + "\n", encoding="utf-8")


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mean(values: Sequence[float | int]) -> float:
    return 0.0 if not values else sum(float(value) for value in values) / len(values)


def _deterministic_projection(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _deterministic_projection(item)
            for key, item in value.items()
            if "latency" not in str(key)
        }
    if isinstance(value, list):
        return [_deterministic_projection(item) for item in value]
    return value


def _root_outcomes(simulator: HeadlessPuyoSimulator) -> dict[int, dict[str, Any]]:
    outcomes: dict[int, dict[str, Any]] = {}
    for action in legal_action_indices(simulator):
        child = clone_simulator(simulator)
        result = child.step(action_to_placement(action))
        outcomes[int(action)] = {
            "action": int(action),
            "valid": bool(result.valid),
            "game_over": bool(result.game_over),
            "chain_count": int(result.chain_count),
            "score_delta": int(result.score_delta),
            "attack_score_delta": int(result.attack_score_delta),
        }
    return outcomes


def _candidate_potential(candidate: WorkerProposalCandidate) -> float:
    value = candidate.build_potential.get("predicted_chain_potential")
    return -1.0 if value is None else float(value)


def _candidate_selection_key(
    candidate: WorkerProposalCandidate,
    outcome: Mapping[str, Any],
) -> tuple[Any, ...]:
    return (
        int(outcome.get("chain_count", 0)),
        int(candidate.predicted_chain_count),
        _candidate_potential(candidate),
        bool(candidate.trigger_recoverability.get("recoverable")),
        float(candidate.continuation_flexibility),
        float(candidate.candidate_value),
        -int(candidate.rank),
    )


def select_capability_candidate(
    batch: WorkerProposalBatch,
    root_outcomes: Mapping[int, Mapping[str, Any]],
    *,
    max_chain_so_far: int,
    target_chain: int = TARGET_CHAIN,
) -> int | None:
    """Select only for gate evaluation; this is never a runtime ranker."""

    indexed = [
        (index, candidate, root_outcomes.get(candidate.root_action, {}))
        for index, candidate in enumerate(batch.candidates)
        if candidate is not None
    ]
    surviving = [
        item
        for item in indexed
        if item[2].get("valid") and not item[2].get("game_over")
    ]
    pool = surviving or indexed
    if not pool:
        return None
    if max_chain_so_far < target_chain:
        target_fires = [
            item for item in pool if int(item[2].get("chain_count", 0)) >= target_chain
        ]
        if target_fires:
            pool = target_fires
        else:
            quiet = [item for item in pool if int(item[2].get("chain_count", 0)) == 0]
            if quiet:
                pool = quiet
    return max(pool, key=lambda item: _candidate_selection_key(item[1], item[2]))[0]


def _compact_candidate(candidate: WorkerProposalCandidate) -> dict[str, Any]:
    potential = candidate.build_potential
    return {
        "schema_version": candidate.schema_version,
        "candidate_id": candidate.candidate_id,
        "rank": int(candidate.rank),
        "source_rank": int(candidate.source_rank),
        "root_action": int(candidate.root_action),
        "action_sequence": [int(action) for action in candidate.action_sequence],
        "candidate_value": float(candidate.candidate_value),
        "predicted_chain_count": int(candidate.predicted_chain_count),
        "predicted_score": int(candidate.predicted_score),
        "predicted_attack": {
            key: int(value) for key, value in candidate.attack_preview.items()
        },
        "build_potential": {
            "schema_version": potential.get("schema_version"),
            "evaluation_status": potential.get("evaluation_status"),
            "predicted_chain_potential": potential.get("predicted_chain_potential"),
            "chain_count": potential.get("chain_count"),
            "ignition_cost": potential.get("ignition_cost"),
            "truncation_reason": potential.get("truncation_reason"),
        },
        "trigger_recoverability": dict(candidate.trigger_recoverability),
        "continuation_flexibility": float(candidate.continuation_flexibility),
        "danger": float(candidate.danger),
        "scenario_uncertainty": dict(candidate.scenario_uncertainty),
        "expanded_nodes": int(candidate.expanded_nodes),
        "value_breakdown": {
            key: float(value) for key, value in candidate.value_breakdown.items()
        },
        "fallback": bool(candidate.fallback),
    }


def _compact_batch(
    batch: WorkerProposalBatch,
    *,
    capability_selected_index: int | None,
) -> dict[str, Any]:
    return {
        "schema_version": batch.schema_version,
        "proposal_id": batch.proposal_id,
        "decision_id": batch.decision_id,
        "profile": {
            "id": int(batch.profile_id),
            "name": batch.profile_name,
            "strategy": batch.strategy,
        },
        "candidate_limit": int(batch.candidate_limit),
        "candidate_mask": list(batch.candidate_mask),
        "legal_action_mask": list(batch.legal_action_mask),
        "compatibility_selected_index": batch.selected_index,
        "capability_selected_index": capability_selected_index,
        "selection_mode": batch.selection_mode,
        "fallback_reason": batch.fallback_reason,
        "expanded_nodes": int(batch.expanded_nodes),
        "candidates": [
            None if candidate is None else _compact_candidate(candidate)
            for candidate in batch.candidates
        ],
        "capability_selection_telemetry": batch.telemetry(
            capability_selected_index
        ).to_dict(),
        "deterministic_digest": batch.deterministic_digest,
    }


def _evaluate_capability_decision(
    simulator: HeadlessPuyoSimulator,
    opponent: HeadlessPuyoSimulator,
    *,
    step_count: int,
    max_steps: int,
    score_carry: int,
    sent_ojama: int,
    max_chain_so_far: int,
    current_policy: BeamSearchPolicy,
    reference_policy: BeamSearchPolicy,
    configuration: CapabilityConfiguration,
) -> tuple[int, dict[str, Any]]:
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
    current_candidates = current_policy.generate_candidates(observation, info)
    current_diagnostics = current_policy.last_diagnostics
    if current_diagnostics is None or not current_candidates:
        raise RuntimeError("capability search produced no candidate diagnostics")
    batch = build_worker_proposal_batch(
        current_candidates,
        selected_action=current_candidates[0].action,
        candidate_limit=configuration.candidate_limit,
        legal_action_mask=legal_action_mask(simulator),
        profile_id=0,
        profile_name="capability_build_main",
        strategy="build_large",
        simulator=simulator,
        score_carry=score_carry,
        incoming_attack=0,
        search_latency_ms=current_diagnostics.elapsed_seconds * 1_000.0,
        expanded_nodes=current_diagnostics.expanded_nodes,
        scenario_budget=current_diagnostics.scenario_budget,
        schema_version=WORKER_PROPOSAL_V1_SCHEMA_VERSION,
    )
    root_outcomes = _root_outcomes(simulator)
    selected_index = select_capability_candidate(
        batch,
        root_outcomes,
        max_chain_so_far=max_chain_so_far,
    )
    compact_batch = _compact_batch(
        batch,
        capability_selected_index=selected_index,
    )
    current_latency_ms = (time.perf_counter() - started) * 1_000.0

    started = time.perf_counter()
    reference_candidates = reference_policy.generate_candidates(observation, info)
    reference_diagnostics = reference_policy.last_diagnostics
    reference_latency_ms = (time.perf_counter() - started) * 1_000.0
    if reference_diagnostics is None or not reference_candidates:
        raise RuntimeError("bounded reference produced no candidate diagnostics")
    reference_best = reference_candidates[0]
    reference_path = tuple(int(action) for action in reference_best.plan)
    comparison_depth = min(configuration.depth, len(reference_path))
    path_trace = current_policy.candidate_path_diagnostics(
        reference_path[:comparison_depth]
    )
    batch_actions = {
        candidate.root_action for candidate in batch.candidates if candidate is not None
    }

    surviving_legal = [
        action
        for action, outcome in root_outcomes.items()
        if outcome["valid"] and not outcome["game_over"]
    ]
    quiet_legal = [
        action
        for action in surviving_legal
        if int(root_outcomes[action]["chain_count"]) == 0
    ]
    surviving_candidates = [
        action for action in batch_actions if action in surviving_legal
    ]
    quiet_candidates = [
        action
        for action in surviving_candidates
        if int(root_outcomes[action]["chain_count"]) == 0
    ]
    target_not_reached = max_chain_so_far < TARGET_CHAIN
    avoidable_candidate_gap = bool(
        target_not_reached and quiet_legal and not quiet_candidates
    )
    forced_game_over_gap = bool(surviving_legal and not surviving_candidates)
    selected_candidate = (
        None if selected_index is None else batch.candidates[selected_index]
    )
    if selected_candidate is None:
        selected_action = next(iter(sorted(root_outcomes)), 0)
    else:
        selected_action = int(selected_candidate.root_action)
    selected_outcome = root_outcomes.get(
        selected_action,
        {
            "valid": False,
            "game_over": True,
            "chain_count": 0,
            "score_delta": 0,
            "attack_score_delta": 0,
        },
    )
    selected_chain = int(selected_outcome["chain_count"])
    if not 0 < selected_chain < TARGET_CHAIN:
        premature_classification = "none"
    elif quiet_candidates:
        premature_classification = "avoidable"
    else:
        premature_classification = "candidate_limited"

    best_reachable_chain = max(
        (
            int(candidate.predicted_chain_count)
            for candidate in batch.candidates
            if candidate is not None
        ),
        default=0,
    )
    record = {
        "schema_version": CAPABILITY_DECISION_SCHEMA_VERSION,
        "step": int(step_count),
        "target_not_reached": target_not_reached,
        "proposal_latency_ms": float(current_latency_ms),
        "reference_latency_ms": float(reference_latency_ms),
        "proposal": compact_batch,
        "reference": {
            "semantics": "PUYO-165 bounded reference; not an oracle",
            "selected_action": int(reference_best.action),
            "best_path": list(reference_path),
            "best_reachable_chain": int(reference_best.predicted_max_chain),
            "expanded_nodes": int(reference_diagnostics.expanded_nodes),
        },
        "coverage": {
            "reference_action_covered": int(reference_best.action) in batch_actions,
            "reference_path_covered": bool(
                path_trace and path_trace[-1].get("final_prune")
            ),
            "reference_path_trace": list(path_trace),
            "candidate_count": int(batch.candidate_count),
            "legal_action_count": len(root_outcomes),
            "best_reachable_chain": int(best_reachable_chain),
        },
        "candidate_gaps": {
            "avoidable_no_fire_gap": avoidable_candidate_gap,
            "forced_game_over_gap": forced_game_over_gap,
            "quiet_legal_actions": quiet_legal,
            "quiet_candidate_actions": quiet_candidates,
            "surviving_legal_actions": surviving_legal,
            "surviving_candidate_actions": surviving_candidates,
        },
        "selection": {
            "responsibility": "evaluation_only_capability_selector",
            "selected_index": selected_index,
            "selected_action": int(selected_action),
            "premature_classification": premature_classification,
        },
        "root_outcomes": [root_outcomes[action] for action in sorted(root_outcomes)],
    }
    return selected_action, record


def evaluate_capability_seed(
    seed: int,
    *,
    max_steps: int,
    configuration: CapabilityConfiguration,
    reference_budget: SearchBudget = DEFAULT_REFERENCE_BUDGET,
    include_decisions: bool = True,
) -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=seed)
    opponent = HeadlessPuyoSimulator(seed=seed + 1_000_003)
    current_policy = BeamSearchPolicy(configuration.beam_config())
    reference_policy = BeamSearchPolicy(reference_budget.beam_config())
    carry = 0
    sent = 0
    max_chain = 0
    decisions: list[dict[str, Any]] = []
    for step_count in range(max_steps):
        action, record = _evaluate_capability_decision(
            simulator,
            opponent,
            step_count=step_count,
            max_steps=max_steps,
            score_carry=carry,
            sent_ojama=sent,
            max_chain_so_far=max_chain,
            current_policy=current_policy,
            reference_policy=reference_policy,
            configuration=configuration,
        )
        result = simulator.step(action_to_placement(action))
        conversion = convert_score_to_ojama(result.attack_score_delta, carry)
        carry = conversion.carry
        sent += conversion.units
        max_chain = max(max_chain, int(result.chain_count))
        record["seed"] = int(seed)
        record["outcome"] = {
            "valid": bool(result.valid),
            "chain_count": int(result.chain_count),
            "score_delta": int(result.score_delta),
            "attack_score_delta": int(result.attack_score_delta),
            "game_over": bool(result.game_over),
            "max_chain_so_far": int(max_chain),
        }
        decisions.append(record)
        if result.game_over:
            break

    classifications = Counter(
        str(record["selection"]["premature_classification"]) for record in decisions
    )
    proposal_latencies = [float(record["proposal_latency_ms"]) for record in decisions]
    reference_latencies = [
        float(record["reference_latency_ms"]) for record in decisions
    ]
    summary = {
        "seed": int(seed),
        "decisions": len(decisions),
        "moves_per_game": int(max_steps),
        "actual_max_chain": int(max_chain),
        "best_reachable_chain_max": max(
            (int(record["coverage"]["best_reachable_chain"]) for record in decisions),
            default=0,
        ),
        "reference_best_chain_max": max(
            (int(record["reference"]["best_reachable_chain"]) for record in decisions),
            default=0,
        ),
        "reference_action_coverage_count": sum(
            bool(record["coverage"]["reference_action_covered"]) for record in decisions
        ),
        "reference_path_coverage_count": sum(
            bool(record["coverage"]["reference_path_covered"]) for record in decisions
        ),
        "avoidable_candidate_gap_count": sum(
            bool(record["candidate_gaps"]["avoidable_no_fire_gap"])
            for record in decisions
        ),
        "forced_game_over_gap_count": sum(
            bool(record["candidate_gaps"]["forced_game_over_gap"])
            for record in decisions
        ),
        "premature_classification_counts": {
            name: int(classifications.get(name, 0)) for name in PREMATURE_CLASSES
        },
        "game_over_before_limit": bool(
            decisions
            and decisions[-1]["outcome"]["game_over"]
            and len(decisions) < max_steps
        ),
        "proposal_latency_p50_ms": percentile(proposal_latencies, 0.50),
        "proposal_latency_p95_ms": percentile(proposal_latencies, 0.95),
        "reference_latency_p50_ms": percentile(reference_latencies, 0.50),
        "reference_latency_p95_ms": percentile(reference_latencies, 0.95),
        "_proposal_latencies_ms": proposal_latencies,
        "_reference_latencies_ms": reference_latencies,
    }
    result_payload = {
        "summary": summary,
        "deterministic_digest": _digest(_deterministic_projection(decisions)),
    }
    if include_decisions:
        result_payload["decisions"] = decisions
    return result_payload


def _evaluate_capability_seed_task(
    task: tuple[int, int, CapabilityConfiguration, SearchBudget, bool],
) -> dict[str, Any]:
    seed, max_steps, configuration, reference_budget, include_decisions = task
    return evaluate_capability_seed(
        seed,
        max_steps=max_steps,
        configuration=configuration,
        reference_budget=reference_budget,
        include_decisions=include_decisions,
    )


def aggregate_capability_suite(
    seed_summaries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    decisions = sum(int(summary["decisions"]) for summary in seed_summaries)
    proposal_latencies = [
        float(value)
        for summary in seed_summaries
        for value in summary.get("_proposal_latencies_ms", ())
    ]
    reference_latencies = [
        float(value)
        for summary in seed_summaries
        for value in summary.get("_reference_latencies_ms", ())
    ]
    premature = {
        name: sum(
            int(summary["premature_classification_counts"].get(name, 0))
            for summary in seed_summaries
        )
        for name in PREMATURE_CLASSES
    }
    return {
        "games": len(seed_summaries),
        "complete_games": sum(
            int(summary["decisions"]) == int(summary["moves_per_game"])
            for summary in seed_summaries
        ),
        "decisions": int(decisions),
        "mean_max_chain": _mean(
            [int(summary["actual_max_chain"]) for summary in seed_summaries]
        ),
        "max_chain_max": max(
            (int(summary["actual_max_chain"]) for summary in seed_summaries),
            default=0,
        ),
        "best_reachable_chain_mean": _mean(
            [int(summary["best_reachable_chain_max"]) for summary in seed_summaries]
        ),
        "best_reachable_chain_max": max(
            (int(summary["best_reachable_chain_max"]) for summary in seed_summaries),
            default=0,
        ),
        "reference_best_chain_mean": _mean(
            [int(summary["reference_best_chain_max"]) for summary in seed_summaries]
        ),
        "reference_action_coverage_count": sum(
            int(summary["reference_action_coverage_count"])
            for summary in seed_summaries
        ),
        "reference_action_coverage_rate": (
            0.0
            if decisions == 0
            else sum(
                int(summary["reference_action_coverage_count"])
                for summary in seed_summaries
            )
            / decisions
        ),
        "reference_path_coverage_count": sum(
            int(summary["reference_path_coverage_count"]) for summary in seed_summaries
        ),
        "reference_path_coverage_rate": (
            0.0
            if decisions == 0
            else sum(
                int(summary["reference_path_coverage_count"])
                for summary in seed_summaries
            )
            / decisions
        ),
        "avoidable_candidate_gap_count": sum(
            int(summary["avoidable_candidate_gap_count"]) for summary in seed_summaries
        ),
        "forced_game_over_gap_count": sum(
            int(summary["forced_game_over_gap_count"]) for summary in seed_summaries
        ),
        "premature_classification_counts": premature,
        "game_over_before_limit": sum(
            bool(summary["game_over_before_limit"]) for summary in seed_summaries
        ),
        "latency": {
            "mode": LATENCY_MODE,
            "proposal_p50_ms": percentile(proposal_latencies, 0.50),
            "proposal_p95_ms": percentile(proposal_latencies, 0.95),
            "reference_p50_ms": percentile(reference_latencies, 0.50),
            "reference_p95_ms": percentile(reference_latencies, 0.95),
        },
    }


def evaluate_capability_repetition(
    seeds: Sequence[int],
    *,
    max_steps: int,
    configuration: CapabilityConfiguration,
    reference_budget: SearchBudget,
    workers: int,
    include_decisions: bool,
) -> dict[str, Any]:
    tasks = [
        (
            int(seed),
            int(max_steps),
            configuration,
            reference_budget,
            include_decisions,
        )
        for seed in seeds
    ]
    if workers == 1:
        results = [_evaluate_capability_seed_task(task) for task in tasks]
    else:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context("spawn"),
        ) as executor:
            results = list(
                executor.map(_evaluate_capability_seed_task, tasks, chunksize=1)
            )
    results.sort(key=lambda item: int(item["summary"]["seed"]))
    summaries = [result["summary"] for result in results]
    aggregate = aggregate_capability_suite(summaries)
    digest_payload = {
        "configuration": configuration.to_dict(),
        "seed_digests": [
            {
                "seed": int(result["summary"]["seed"]),
                "digest": result["deterministic_digest"],
            }
            for result in results
        ],
        "aggregate": {
            key: value for key, value in aggregate.items() if key != "latency"
        },
    }
    return {
        "digest": _digest(digest_payload),
        "seed_digests": digest_payload["seed_digests"],
        "seed_summaries": summaries,
        "aggregate": aggregate,
        "decisions": [
            decision for result in results for decision in result.get("decisions", ())
        ],
    }


def _check(passed: bool, actual: Any, expected: str) -> dict[str, Any]:
    return {"passed": bool(passed), "actual": actual, "expected": expected}


def assess_capability_gate(
    aggregate: Mapping[str, Any],
    thresholds: CapabilityThresholds,
    *,
    determinism_passed: bool,
) -> dict[str, Any]:
    games = int(aggregate.get("games", 0))
    complete_games = int(aggregate.get("complete_games", 0))
    checks = {
        "fixed_suite": _check(
            games >= thresholds.minimum_games and complete_games == games,
            {"games": games, "complete_games": complete_games},
            (
                f">= {thresholds.minimum_games} games with exactly "
                f"{thresholds.moves_per_game} moves"
            ),
        ),
        "mean_max_chain": _check(
            float(aggregate.get("mean_max_chain", 0.0))
            >= thresholds.minimum_mean_max_chain,
            float(aggregate.get("mean_max_chain", 0.0)),
            f">= {thresholds.minimum_mean_max_chain}",
        ),
        "avoidable_candidate_gap": _check(
            int(aggregate.get("avoidable_candidate_gap_count", 0))
            <= thresholds.maximum_avoidable_candidate_gaps,
            int(aggregate.get("avoidable_candidate_gap_count", 0)),
            f"<= {thresholds.maximum_avoidable_candidate_gaps}",
        ),
        "forced_game_over_gap": _check(
            int(aggregate.get("forced_game_over_gap_count", 0))
            <= thresholds.maximum_forced_game_over_gaps,
            int(aggregate.get("forced_game_over_gap_count", 0)),
            f"<= {thresholds.maximum_forced_game_over_gaps}",
        ),
        "registry_latency_budget": _check(
            float(aggregate.get("latency", {}).get("proposal_p95_ms", 0.0))
            <= thresholds.maximum_p95_latency_ms,
            float(aggregate.get("latency", {}).get("proposal_p95_ms", 0.0)),
            f"<= {thresholds.maximum_p95_latency_ms} ms p95",
        ),
        "deterministic_repetition": _check(
            determinism_passed,
            bool(determinism_passed),
            "true",
        ),
    }
    passed = all(check["passed"] for check in checks.values())
    return {
        "schema_version": CAPABILITY_GATE_RESULT_SCHEMA_VERSION,
        "responsibility": (
            "pre-training candidate-generator capability; does not evaluate "
            "learned candidate selection"
        ),
        "checks": checks,
        "metrics": dict(aggregate),
        "training_capability_gate_passed": passed,
        "puyo_130_long_run": {
            "status": "UNBLOCKED" if passed else "BLOCKED",
            "reason": (
                "all capability checks passed"
                if passed
                else "pre-training capability gate did not pass"
            ),
        },
    }


def select_capability_configuration(
    evaluated: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    passing = [
        item
        for item in evaluated
        if item.get("result", {}).get("training_capability_gate_passed")
    ]
    if not passing:
        return None
    return min(
        passing,
        key=lambda item: (
            float(item["result"]["metrics"]["latency"]["proposal_p95_ms"]),
            int(item["configuration"]["depth"]),
            int(item["configuration"]["width"]),
            int(item["configuration"]["probe_width"]),
            int(item["configuration"]["candidate_limit"]),
        ),
    )


def summarize_capability_configuration_selection(
    evaluated: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    selected = select_capability_configuration(evaluated)
    return {
        "rule": "minimum_proposal_p95_ms_among_passing_configurations",
        "tie_break": ["depth", "width", "probe_width", "candidate_limit"],
        "status": "SELECTED" if selected is not None else "NO_PASSING_CONFIGURATION",
        "selected_config_id": (
            selected["configuration"]["config_id"] if selected is not None else None
        ),
        "evaluated": [
            {
                "config_id": item["configuration"]["config_id"],
                "passed": bool(item["result"].get("training_capability_gate_passed")),
                "proposal_p95_ms": float(
                    item["result"]["metrics"]["latency"]["proposal_p95_ms"]
                ),
            }
            for item in evaluated
        ],
    }


def assess_promotion_gate(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != PROMOTION_GATE_INPUT_SCHEMA_VERSION:
        raise ValueError("unexpected promotion gate input schema")
    thresholds = PromotionThresholds(**dict(payload.get("thresholds", {})))
    checkpoint = payload.get("checkpoint", {})
    safe = payload.get("selected_policy_safe_build", {})
    scenarios = payload.get("threat_scenarios", {}).get("summary", {})
    training_seeds = [int(seed) for seed in payload.get("training_seeds", ())]
    lineage = payload.get("lineage", {})
    gui_replay = payload.get("gui_replay", {})
    post_training = checkpoint.get("role") == "post_training_candidate"
    lineage_complete = bool(
        lineage.get("parent_node_id")
        and lineage.get("training_run_id")
        and lineage.get("git_commit")
    )
    checks = {
        "post_training_candidate": _check(
            post_training,
            checkpoint.get("role"),
            "post_training_candidate",
        ),
        "fixed_training_seeds": _check(
            len(set(training_seeds)) >= thresholds.minimum_training_seeds,
            sorted(set(training_seeds)),
            f">= {thresholds.minimum_training_seeds} version-controlled seeds",
        ),
        "fixed_safe_build_suite": _check(
            int(safe.get("games", 0)) >= thresholds.minimum_games
            and int(safe.get("moves_per_game", 0)) == thresholds.moves_per_game,
            {
                "games": int(safe.get("games", 0)),
                "moves_per_game": int(safe.get("moves_per_game", 0)),
            },
            (
                f">= {thresholds.minimum_games} games x "
                f"{thresholds.moves_per_game} moves"
            ),
        ),
        "selected_policy_mean_max_chain": _check(
            float(safe.get("mean_max_chain", 0.0)) >= thresholds.minimum_mean_max_chain,
            float(safe.get("mean_max_chain", 0.0)),
            f">= {thresholds.minimum_mean_max_chain}",
        ),
        "selected_policy_safe_premature_fire": _check(
            int(safe.get("premature_fire_count", 0))
            <= thresholds.maximum_safe_premature_fires,
            int(safe.get("premature_fire_count", 0)),
            f"<= {thresholds.maximum_safe_premature_fires}",
        ),
        "selected_policy_early_game_over": _check(
            int(safe.get("game_over_before_limit", 0))
            <= thresholds.maximum_early_game_overs,
            int(safe.get("game_over_before_limit", 0)),
            f"<= {thresholds.maximum_early_game_overs}",
        ),
        "threat_scenarios": _check(
            int(scenarios.get("scenarios", 0)) == thresholds.required_threat_scenarios
            and int(scenarios.get("failed", 0)) == 0,
            {
                "passed": int(scenarios.get("passed", 0)),
                "scenarios": int(scenarios.get("scenarios", 0)),
            },
            (
                f"{thresholds.required_threat_scenarios}/"
                f"{thresholds.required_threat_scenarios}"
            ),
        ),
        "lineage": _check(lineage_complete, lineage_complete, "complete"),
        "gui_replay_qa": _check(
            bool(gui_replay.get("passed")),
            bool(gui_replay.get("passed")),
            "true",
        ),
    }
    passed = all(check["passed"] for check in checks.values())
    status = (
        "PASS" if passed else ("BLOCKED" if post_training else "PENDING_POST_TRAINING")
    )
    return {
        "schema_version": PROMOTION_GATE_RESULT_SCHEMA_VERSION,
        "responsibility": (
            "post-training learned selected-policy promotion; candidate-set "
            "capability alone cannot satisfy this gate"
        ),
        "status": status,
        "checks": checks,
        "promotion_gate_passed": passed,
        "registry_registration": "ELIGIBLE" if passed else "BLOCKED",
    }


def validate_gate_summary(summary: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if summary.get("schema_version") != GATE_SUMMARY_SCHEMA_VERSION:
        issues.append("unexpected safe-build gate summary schema")
    if "training_gate_passed" in summary:
        issues.append(
            "legacy training_gate_passed must not appear in two-stage summary"
        )
    capability = summary.get("capability", {})
    promotion = summary.get("promotion", {})
    if (
        capability.get("input", {}).get("schema_version")
        != CAPABILITY_GATE_INPUT_SCHEMA_VERSION
    ):
        issues.append("capability input schema mismatch")
    if (
        capability.get("result", {}).get("schema_version")
        != CAPABILITY_GATE_RESULT_SCHEMA_VERSION
    ):
        issues.append("capability result schema mismatch")
    if (
        promotion.get("input", {}).get("schema_version")
        != PROMOTION_GATE_INPUT_SCHEMA_VERSION
    ):
        issues.append("promotion input schema mismatch")
    if (
        promotion.get("result", {}).get("schema_version")
        != PROMOTION_GATE_RESULT_SCHEMA_VERSION
    ):
        issues.append("promotion result schema mismatch")
    capability_passed = bool(
        capability.get("result", {}).get("training_capability_gate_passed")
    )
    if bool(summary.get("training_capability_gate_passed")) != capability_passed:
        issues.append("capability pass flag disagrees with result")
    promotion_passed = bool(promotion.get("result", {}).get("promotion_gate_passed"))
    if bool(summary.get("promotion_gate_passed")) != promotion_passed:
        issues.append("promotion pass flag disagrees with result")
    expected_status = "UNBLOCKED" if capability_passed else "BLOCKED"
    if summary.get("puyo_130_long_run", {}).get("status") != expected_status:
        issues.append("PUYO-130 long-run status disagrees with capability result")
    return issues


def _public_seed_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if not key.startswith("_")}


def _write_safe_records_csv(
    path: Path,
    records: Sequence[Mapping[str, Any]],
) -> None:
    fieldnames = [
        "policy",
        "seed",
        "steps",
        "max_chain",
        "premature_fire_count",
        "trigger_opportunities",
        "trigger_loss_count",
        "game_over_before_limit",
        "score_carry",
        "sent_ojama",
        "decision_p50_ms",
        "decision_p95_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def _git_contains(commit: str, *, head: str = "HEAD") -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, head],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _canonical_integration_evidence() -> dict[str, Any]:
    commit = git_commit()
    required = {
        issue: {
            "merge_commit": merge_commit,
            "contained": _git_contains(merge_commit, head=commit),
        }
        for issue, merge_commit in REQUIRED_INTEGRATION_COMMITS.items()
    }
    return {
        "branch": "integration/puyo-113-v1-7-2",
        "commit": commit,
        "required_merges": required,
        "all_required_merges_contained": all(
            item["contained"] for item in required.values()
        ),
    }


def _load_gui_evidence(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "status": "not_evaluated",
            "passed": False,
            "reason": "post-training GUI/replay QA belongs to PUYO-133",
        }
    payload = _read_json(path)
    passed = bool(
        payload.get("passed") or payload.get("quality_gate", {}).get("passed")
    )
    return {
        "status": "evaluated",
        "passed": passed,
        "path": str(path),
        "sha256": file_sha256(path),
    }


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    capability = summary["capability"]["result"]
    metrics = capability["metrics"]
    promotion = summary["promotion"]["result"]
    scenarios = summary["promotion"]["input"]["threat_scenarios"]["summary"]
    premature = metrics["premature_classification_counts"]
    lines = [
        "# PUYO-170 two-stage safe-build gates",
        "",
        f"- canonical integration: `{summary['canonical_integration']['commit']}`",
        f"- training capability gate: **{'PASS' if summary['training_capability_gate_passed'] else 'BLOCKED'}**",
        f"- post-training promotion gate: **{promotion['status']}**",
        f"- PUYO-130 long-run: **{summary['puyo_130_long_run']['status']}**",
        "",
        "The capability selector is evaluation-only. It proves candidate-set capacity; it does not stand in for a learned ranker.",
        "",
        "## Pre-training capability",
        "",
        f"- fixed suite: {metrics['complete_games']}/{metrics['games']} games x {summary['capability']['input']['config']['moves_per_game']} moves",
        f"- actual capability-path mean maximum chain: {metrics['mean_max_chain']:.3f}",
        f"- candidate best-reachable chain mean/max: {metrics['best_reachable_chain_mean']:.3f}/{metrics['best_reachable_chain_max']}",
        f"- bounded-reference root/path coverage: {metrics['reference_action_coverage_rate']:.3f}/{metrics['reference_path_coverage_rate']:.3f}",
        f"- premature fire avoidable/candidate-limited: {premature['avoidable']}/{premature['candidate_limited']}",
        f"- avoidable no-fire candidate gaps: {metrics['avoidable_candidate_gap_count']}",
        f"- forced game-over candidate gaps: {metrics['forced_game_over_gap_count']}",
        f"- proposal latency p50/p95 ({metrics['latency']['mode']}): {metrics['latency']['proposal_p50_ms']:.2f}/{metrics['latency']['proposal_p95_ms']:.2f} ms",
        f"- configuration selection: {capability['configuration_selection']['status']} ({capability['configuration_selection']['selected_config_id'] or 'none'})",
        "",
        "### Checks",
        "",
    ]
    for name, check in capability["checks"].items():
        lines.append(f"- `{name}`: {'PASS' if check['passed'] else 'FAIL'}")
    lines.extend(
        [
            "",
            "## Post-training promotion",
            "",
            f"- checkpoint role: `{summary['promotion']['input']['checkpoint']['role']}`",
            f"- selected-policy mean max chain: {summary['promotion']['input']['selected_policy_safe_build']['mean_max_chain']:.3f}",
            f"- selected-policy premature/game-over: {summary['promotion']['input']['selected_policy_safe_build']['premature_fire_count']}/{summary['promotion']['input']['selected_policy_safe_build']['game_over_before_limit']}",
            f"- post-PUYO-158 threat scenarios: {scenarios['passed']}/{scenarios['scenarios']}",
            "",
            "A pre-training reference checkpoint intentionally remains in `PENDING_POST_TRAINING`; PUYO-133 must provide five training seeds, lineage, and GUI/replay QA for registration.",
            "",
            "## Reproduce",
            "",
            "```bash",
            "python3 -m eval.v1_7_safe_build_gates verify \\",
            f"  --artifact-dir {DEFAULT_OUTPUT_DIR}",
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_gates(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    registry = load_tactic_registry()
    build_defaults = registry.tactic("build_main").parameter_defaults
    planner_defaults = build_defaults["planner"]
    latency_budget_ms = float(planner_defaults["latency_budget_ms"])
    configuration = CapabilityConfiguration(
        config_id=(
            f"registry-d{args.current_depth}-w{args.current_width}-"
            f"p{args.current_probe_width}-k{args.candidate_limit}"
        ),
        depth=args.current_depth,
        width=args.current_width,
        probe_width=args.current_probe_width,
        candidate_limit=args.candidate_limit,
    )
    reference_budget = SearchBudget(
        args.reference_depth,
        args.reference_width,
        args.reference_probe_width,
    )
    seed_manifest = load_seed_manifest(
        args.seed_source,
        source_config_id=args.source_config_id,
        games=args.games,
        max_steps=args.max_steps,
    )
    thresholds = CapabilityThresholds(
        minimum_games=args.games,
        moves_per_game=args.max_steps,
        maximum_p95_latency_ms=latency_budget_ms,
    )
    capability_input = {
        "schema_version": CAPABILITY_GATE_INPUT_SCHEMA_VERSION,
        "responsibility": (
            "candidate-generator capability before PUYO-130 long-run training"
        ),
        "config": {
            "games": int(args.games),
            "moves_per_game": int(args.max_steps),
            "workers": int(args.workers),
            "repetitions": int(args.repetitions),
            "configuration": configuration.to_dict(),
            "reference": reference_budget.to_dict(),
            "latency_mode": LATENCY_MODE,
        },
        "thresholds": thresholds.to_dict(),
        "seed_manifest": seed_manifest,
        "schemas": {
            "worker_proposal": WORKER_PROPOSAL_V1_SCHEMA_VERSION,
            "build_potential": BUILD_POTENTIAL_SCHEMA_VERSION,
            "tactic_registry": registry.schema_version,
            "tactic_registry_version": registry.registry_version,
        },
        "reference": {
            "semantics": "PUYO-165 bounded reference; not an oracle",
            "source_artifact": (
                "docs/benchmarks/puyo-v1-7-2-search-diagnostics/decision_records.json"
            ),
            "source_artifact_sha256": file_sha256(
                "docs/benchmarks/puyo-v1-7-2-search-diagnostics/decision_records.json"
            ),
        },
    }
    repetitions = []
    for repetition in range(args.repetitions):
        result = evaluate_capability_repetition(
            seed_manifest["seeds"],
            max_steps=args.max_steps,
            configuration=configuration,
            reference_budget=reference_budget,
            workers=args.workers,
            include_decisions=repetition == 0,
        )
        repetitions.append(result)
        print(
            f"capability repetition {repetition + 1}/{args.repetitions}: "
            f"digest={result['digest']} decisions={result['aggregate']['decisions']}",
            flush=True,
        )
    digests = [result["digest"] for result in repetitions]
    determinism = {
        "passed": len(set(digests)) == 1,
        "repetitions": len(digests),
        "digests": digests,
        "excluded_fields": ["proposal_latency_ms", "reference_latency_ms"],
        "scope": [
            "capability-selected actions",
            "K-best candidate IDs, masks, and previews",
            "bounded-reference coverage",
            "premature and candidate-gap classification",
            "latency-free seed and aggregate metrics",
        ],
    }
    first = repetitions[0]
    capability_result = assess_capability_gate(
        first["aggregate"],
        thresholds,
        determinism_passed=determinism["passed"],
    )
    capability_result["configuration_selection"] = (
        summarize_capability_configuration_selection(
            [
                {
                    "configuration": configuration.to_dict(),
                    "result": capability_result,
                }
            ]
        )
    )

    checkpoint_path = Path(args.promotion_checkpoint)
    checkpoint_evidence, _ = load_checkpoint_evidence(checkpoint_path)
    promotion_safe, promotion_records = run_safe_suite(
        args.checkpoint_role,
        _policy_spec(
            POLICY_TYPE,
            seed=args.promotion_seed,
            checkpoint_path=str(checkpoint_path),
        ),
        games=args.games,
        seed=args.promotion_seed,
        max_steps=args.max_steps,
        workers=args.workers,
    )
    threat_scenarios = evaluate_response_scenarios(checkpoint_path)
    checkpoint_lineage = checkpoint_evidence.get("checkpoint_metadata", {}).get(
        "lineage", {}
    )
    promotion_input = {
        "schema_version": PROMOTION_GATE_INPUT_SCHEMA_VERSION,
        "responsibility": ("learned selected-policy quality after PUYO-130 training"),
        "checkpoint": {
            "path": str(checkpoint_path),
            "sha256": checkpoint_evidence["sha256"],
            "role": args.checkpoint_role,
            "schema_version": checkpoint_evidence.get("schema_version"),
            "model_version": checkpoint_evidence.get("model_version"),
        },
        "training_seeds": [
            int(value) for value in args.training_seeds.split(",") if value.strip()
        ],
        "selected_policy_safe_build": promotion_safe,
        "threat_scenarios": threat_scenarios,
        "lineage": {
            **dict(checkpoint_lineage),
            "git_commit": checkpoint_evidence.get("git_commit"),
        },
        "gui_replay": _load_gui_evidence(args.gui_evidence),
        "thresholds": PromotionThresholds().to_dict(),
    }
    promotion_result = assess_promotion_gate(promotion_input)
    canonical = _canonical_integration_evidence()
    summary = {
        "schema_version": GATE_SUMMARY_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "canonical_integration": canonical,
        "training_capability_gate_passed": bool(
            capability_result["training_capability_gate_passed"]
        ),
        "promotion_gate_passed": bool(promotion_result["promotion_gate_passed"]),
        "puyo_130_long_run": dict(capability_result["puyo_130_long_run"]),
        "capability": {
            "input": capability_input,
            "result": capability_result,
            "determinism": determinism,
        },
        "promotion": {
            "input": promotion_input,
            "result": promotion_result,
        },
    }
    schema_issues = validate_gate_summary(summary)
    if schema_issues:
        raise ValueError("; ".join(schema_issues))
    if not canonical["all_required_merges_contained"]:
        raise RuntimeError(
            "canonical commit does not contain every required PUYO merge"
        )

    capability_input_path = output_dir / "capability_gate_input.json"
    capability_result_path = output_dir / "capability_gate_result.json"
    candidate_path = output_dir / "capability_candidates.json"
    seed_results_path = output_dir / "capability_seed_results.json"
    determinism_path = output_dir / "capability_determinism.json"
    promotion_input_path = output_dir / "promotion_gate_input.json"
    promotion_result_path = output_dir / "promotion_gate_result.json"
    safe_records_path = output_dir / "promotion_safe_build_games.csv"
    outcome_path = output_dir / "outcome_scenarios.json"
    summary_path = output_dir / "gate_summary.json"
    report_path = output_dir / "gate_report.md"
    manifest_path = output_dir / "gate_manifest.json"
    _write_json(capability_input_path, capability_input)
    _write_json(capability_result_path, capability_result)
    _write_json(
        candidate_path,
        {
            "schema_version": CAPABILITY_CANDIDATE_ARTIFACT_SCHEMA_VERSION,
            "worker_proposal_schema_version": WORKER_PROPOSAL_V1_SCHEMA_VERSION,
            "records": first["decisions"],
        },
        compact=True,
    )
    _write_json(
        seed_results_path,
        {
            "schema_version": CAPABILITY_GATE_RESULT_SCHEMA_VERSION,
            "seeds": [_public_seed_summary(seed) for seed in first["seed_summaries"]],
        },
    )
    _write_json(determinism_path, determinism)
    _write_json(promotion_input_path, promotion_input)
    _write_json(promotion_result_path, promotion_result)
    _write_safe_records_csv(
        safe_records_path,
        [{"policy": args.checkpoint_role, **record} for record in promotion_records],
    )
    _write_json(outcome_path, threat_scenarios)
    _write_json(summary_path, summary)
    _write_report(report_path, summary)
    artifacts = [
        capability_input_path,
        capability_result_path,
        candidate_path,
        seed_results_path,
        determinism_path,
        promotion_input_path,
        promotion_result_path,
        safe_records_path,
        outcome_path,
        summary_path,
        report_path,
    ]
    manifest = {
        "schema_version": GATE_MANIFEST_SCHEMA_VERSION,
        "created_at_utc": summary["created_at_utc"],
        "canonical_integration": canonical,
        "training_capability_gate_passed": summary["training_capability_gate_passed"],
        "promotion_gate_passed": summary["promotion_gate_passed"],
        "puyo_130_long_run": summary["puyo_130_long_run"],
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifacts
        ],
    }
    _write_json(manifest_path, manifest)
    return summary


def verify_gate_artifacts(
    artifact_dir: str | Path,
    *,
    require_capability_gate: bool = False,
    require_promotion_gate: bool = False,
) -> dict[str, Any]:
    root = Path(artifact_dir)
    manifest = _read_json(root / "gate_manifest.json")
    summary = _read_json(root / "gate_summary.json")
    candidate_artifact = _read_json(root / "capability_candidates.json")
    issues = validate_gate_summary(summary)
    if manifest.get("schema_version") != GATE_MANIFEST_SCHEMA_VERSION:
        issues.append("unexpected gate manifest schema")
    for artifact in manifest.get("artifacts", ()):
        path = root / str(artifact.get("path", ""))
        if not path.is_file():
            issues.append(f"missing gate artifact: {path}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"gate artifact checksum mismatch: {path}")
    if (
        candidate_artifact.get("schema_version")
        != CAPABILITY_CANDIDATE_ARTIFACT_SCHEMA_VERSION
    ):
        issues.append("capability candidate artifact schema mismatch")
    records = candidate_artifact.get("records", ())
    if not isinstance(records, list) or not records:
        issues.append("capability candidate records are missing")
    elif any(
        record.get("schema_version") != CAPABILITY_DECISION_SCHEMA_VERSION
        for record in records
    ):
        issues.append("capability decision schema mismatch")
    canonical = summary.get("canonical_integration", {})
    commit = str(canonical.get("commit", ""))
    if not commit or not _git_contains(commit):
        issues.append("canonical integration commit is not an ancestor of HEAD")
    if not canonical.get("all_required_merges_contained"):
        issues.append("canonical integration evidence is incomplete")
    if require_capability_gate and not summary.get("training_capability_gate_passed"):
        issues.append("training capability gate is blocked")
    if require_promotion_gate and not summary.get("promotion_gate_passed"):
        issues.append("promotion gate is blocked")
    return {
        "schema_version": GATE_SUMMARY_SCHEMA_VERSION,
        "passed": not issues,
        "issues": issues,
        "training_capability_gate_passed": bool(
            summary.get("training_capability_gate_passed")
        ),
        "promotion_gate_passed": bool(summary.get("promotion_gate_passed")),
        "puyo_130_long_run": summary.get("puyo_130_long_run"),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--seed-source", default=DEFAULT_SEED_SOURCE)
    run.add_argument("--source-config-id", default=DEFAULT_SOURCE_CONFIG_ID)
    run.add_argument("--games", type=int, default=MINIMUM_GAMES)
    run.add_argument("--max-steps", type=int, default=MOVES_PER_GAME)
    run.add_argument(
        "--workers",
        type=int,
        default=max(1, min(12, os.cpu_count() or 1)),
    )
    run.add_argument("--repetitions", type=int, default=2)
    run.add_argument("--current-depth", type=int, default=3)
    run.add_argument("--current-width", type=int, default=24)
    run.add_argument("--current-probe-width", type=int, default=8)
    run.add_argument("--candidate-limit", type=int, default=8)
    run.add_argument(
        "--reference-depth", type=int, default=DEFAULT_REFERENCE_BUDGET.depth
    )
    run.add_argument(
        "--reference-width", type=int, default=DEFAULT_REFERENCE_BUDGET.width
    )
    run.add_argument(
        "--reference-probe-width",
        type=int,
        default=DEFAULT_REFERENCE_BUDGET.probe_width,
    )
    run.add_argument("--promotion-checkpoint", default=DEFAULT_PROMOTION_CHECKPOINT)
    run.add_argument(
        "--checkpoint-role",
        choices=("pretraining_reference", "post_training_candidate"),
        default="pretraining_reference",
    )
    run.add_argument("--promotion-seed", type=int, default=123)
    run.add_argument("--training-seeds", default="")
    run.add_argument("--gui-evidence", default=None)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--artifact-dir", default=DEFAULT_OUTPUT_DIR)
    verify.add_argument("--require-capability-gate", action="store_true")
    verify.add_argument("--require-promotion-gate", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "run":
        positive = (
            args.games,
            args.max_steps,
            args.workers,
            args.repetitions,
            args.current_depth,
            args.current_width,
            args.current_probe_width,
            args.candidate_limit,
            args.reference_depth,
            args.reference_width,
            args.reference_probe_width,
        )
        if any(value <= 0 for value in positive):
            parser.error("gate counts and search budgets must be positive")
        if args.games < MINIMUM_GAMES or args.max_steps != MOVES_PER_GAME:
            parser.error(
                f"canonical capability gate requires >= {MINIMUM_GAMES} games "
                f"and exactly {MOVES_PER_GAME} moves"
            )
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        result = run_gates(args)
    else:
        result = verify_gate_artifacts(
            args.artifact_dir,
            require_capability_gate=args.require_capability_gate,
            require_promotion_gate=args.require_promotion_gate,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command == "run":
        return 0
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
