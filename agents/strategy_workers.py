"""Fixed search workers and tactical forecasts used by the strategy manager."""

from __future__ import annotations

import time
import hashlib
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from agents.beam_search import (
    BUILD_POTENTIAL_SCHEMA_VERSION,
    BUILD_POTENTIAL_V1_SCHEMA_VERSION,
    BUILD_SCORING_V2,
    DIVERSE_CANDIDATE_MODE,
    LEGACY_BUILD_SCORING,
    LEGACY_CANDIDATE_MODE,
    BeamSearchConfig,
    BeamSearchPolicy,
    BuildPotential,
    BuildPotentialBudget,
    BuildPotentialSession,
    DiverseBeamCandidate,
    TriggerRecoverability,
    clone_simulator,
    compare_build_potential_triggers,
    evaluate_board,
)
from agents.chain_styles import (
    ChainStyleEvaluator,
    ChainStyleProvider,
    ChainStyleRegistry,
    load_chain_style_registry,
)
from agents.v1_7_planner import PlannerRequest, resolve_preview_attack
from agents.worker_proposals import (
    WorkerProposalBatch,
    build_worker_proposal_batch,
    compatibility_action,
)
from puyo_env.actions import (
    NUM_ACTIONS,
    action_to_placement,
    legal_action_indices,
    legal_action_mask,
)
from src.core.constants import (
    GRID_HEIGHT,
    GRID_WIDTH,
    NORMAL_PUYO_COLORS,
    PuyoColor,
    VISIBLE_HEIGHT,
)


STRATEGY_NAMES = (
    "build_large",
    "build_budget",
    "punish",
    "counter",
    "fire_max",
    "survival",
    # PUYO-28 checkpoint compatibility.
    "large_chain",
    "quick_attack",
    "fire",
)
BUILD_STRATEGIES = {"build_large", "build_budget", "large_chain", "quick_attack"}


@dataclass(frozen=True)
class WorkerProfile:
    """One discrete manager action and its search budget."""

    profile_id: int
    name: str
    strategy: str
    depth: int = 1
    width: int = 22
    scenarios: int = 1
    minimum_chain_count: int = 1
    chain_weight: float = 100_000.0
    score_weight: float = 1.0
    premature_chain_penalty: float = 350.0
    safety_margin: int = 2
    danger_tolerance: float = 0.75
    fire_threshold: float = 1.0
    trigger_preservation: str = "ignore"
    potential_probe_width: int = 0
    scoring_mode: str = LEGACY_BUILD_SCORING
    future_potential_weight: float = 1.0
    chain_shape_weight: float = 1.0
    danger_weight: float = 1.0
    build_potential_schema_version: str = BUILD_POTENTIAL_V1_SCHEMA_VERSION
    potential_probe_budget: int = 64
    search_profile: str | None = None

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGY_NAMES:
            raise ValueError(f"unknown strategy: {self.strategy}")
        if self.profile_id < 0:
            raise ValueError("profile_id must be non-negative")
        if self.trigger_preservation not in {"required", "prefer", "ignore"}:
            raise ValueError(
                f"unsupported trigger preservation: {self.trigger_preservation}"
            )
        if self.potential_probe_width < 0:
            raise ValueError("potential probe width must be non-negative")
        if self.scoring_mode not in {LEGACY_BUILD_SCORING, BUILD_SCORING_V2}:
            raise ValueError(f"unsupported worker scoring mode: {self.scoring_mode}")
        if min(
            self.future_potential_weight,
            self.chain_shape_weight,
            self.danger_weight,
        ) < 0.0:
            raise ValueError("worker scoring weights must be non-negative")
        if self.build_potential_schema_version not in {
            BUILD_POTENTIAL_V1_SCHEMA_VERSION,
            BUILD_POTENTIAL_SCHEMA_VERSION,
        }:
            raise ValueError(
                "unsupported worker build-potential schema: "
                f"{self.build_potential_schema_version}"
            )
        if self.potential_probe_budget <= 0:
            raise ValueError("worker potential probe budget must be positive")
        if self.search_profile is not None and not self.search_profile:
            raise ValueError("worker search profile must be non-empty when set")


@dataclass(frozen=True)
class SearchControl:
    """Learnable search-parameter override applied on top of a profile."""

    control_id: int
    name: str
    mode: str
    depth_scale: float = 1.0
    width_scale: float = 1.0
    scenarios: int = 1
    chain_weight_scale: float = 1.0
    score_weight_scale: float = 1.0
    premature_chain_penalty_scale: float = 1.0
    fire_threshold: float = 1.0
    danger_tolerance_delta: float = 0.0
    latency_budget_ms: float = 40.0
    cost_penalty: float = 0.0
    parameter_vector: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if self.control_id < 0:
            raise ValueError("control_id must be non-negative")
        if self.mode not in {"discrete_profile", "continuous_parameter", "hybrid"}:
            raise ValueError(f"unknown search-control mode: {self.mode}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "search-control-v1",
            "control_id": int(self.control_id),
            "name": self.name,
            "mode": self.mode,
            "depth_scale": float(self.depth_scale),
            "width_scale": float(self.width_scale),
            "scenarios": int(self.scenarios),
            "chain_weight_scale": float(self.chain_weight_scale),
            "score_weight_scale": float(self.score_weight_scale),
            "premature_chain_penalty_scale": float(self.premature_chain_penalty_scale),
            "fire_threshold": float(self.fire_threshold),
            "danger_tolerance_delta": float(self.danger_tolerance_delta),
            "latency_budget_ms": float(self.latency_budget_ms),
            "cost_penalty": float(self.cost_penalty),
            "parameter_vector": [float(value) for value in self.parameter_vector],
        }


@dataclass(frozen=True)
class SearchControlDiagnostics:
    """Effective constrained parameters after mask / clamp."""

    control: SearchControl
    requested_profile: WorkerProfile
    effective_profile: WorkerProfile
    clamped_fields: tuple[str, ...] = ()
    latency_overrun: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.control.to_dict(),
            "requested": _profile_budget_dict(self.requested_profile),
            "effective": _profile_budget_dict(self.effective_profile),
            "clamped_fields": list(self.clamped_fields),
            "latency_overrun": bool(self.latency_overrun),
        }


@dataclass(frozen=True)
class TacticalOption:
    """Learnable non-fixed tactical intent mapped onto a worker at execution time."""

    option_id: int
    name: str
    base_profile_name: str
    strategy: str
    target_attack_delta: int = 0
    target_chain_delta: int = 0
    deadline_delta: int = 0
    danger_tolerance_delta: float = 0.0
    fire_threshold_scale: float = 1.0
    termination: str = "objective_or_timeout"
    latent_vector: tuple[float, ...] = ()
    fallback_profile_name: str = "survival"

    def __post_init__(self) -> None:
        if self.option_id < 0:
            raise ValueError("option_id must be non-negative")
        if self.strategy not in STRATEGY_NAMES:
            raise ValueError(f"unknown option strategy: {self.strategy}")
        if self.termination not in {"objective", "timeout", "danger", "objective_or_timeout"}:
            raise ValueError(f"unknown option termination: {self.termination}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "tactical-option-v1",
            "option_id": int(self.option_id),
            "name": self.name,
            "base_profile_name": self.base_profile_name,
            "strategy": self.strategy,
            "target_attack_delta": int(self.target_attack_delta),
            "target_chain_delta": int(self.target_chain_delta),
            "deadline_delta": int(self.deadline_delta),
            "danger_tolerance_delta": float(self.danger_tolerance_delta),
            "fire_threshold_scale": float(self.fire_threshold_scale),
            "termination": self.termination,
            "latent_vector": [float(value) for value in self.latent_vector],
            "fallback_profile_name": self.fallback_profile_name,
        }


@dataclass(frozen=True)
class TacticalOptionDiagnostics:
    """Resolved option details used by training, replay, and collapse analysis."""

    option: TacticalOption
    base_profile: WorkerProfile
    effective_profile: WorkerProfile
    termination_reason: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.option.to_dict(),
            "base_profile": _profile_budget_dict(self.base_profile),
            "effective_profile": _profile_budget_dict(self.effective_profile),
            "termination_reason": self.termination_reason,
        }


@dataclass(frozen=True)
class AttackForecast:
    immediate_chain: int = 0
    immediate_attack: int = 0
    short_attack: int = 0
    medium_attack: int = 0
    turns_to_best: int = 0


@dataclass(frozen=True)
class TacticalObjective:
    """Serializable contract that tells a worker what outcome to search for."""

    kind: str
    target_attack: int = 0
    target_score: int = 0
    target_chain: int = 0
    required_response_attack: int = 0
    trigger_preservation: str = "ignore"
    deadline: int = 0
    deadline_ticks: int = 0
    safety_margin: int = 0
    max_danger: float = 1.0
    fallback_strategy: str = "survival"
    source_profile_id: int = -1
    source_profile_name: str = ""
    reason: str = ""

    @property
    def allowed_danger(self) -> float:
        return self.max_danger

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "search-objective-v2",
            "kind": self.kind,
            "target_attack": int(self.target_attack),
            "target_score": int(self.target_score),
            "target_chain": int(self.target_chain),
            "required_response_attack": int(self.required_response_attack),
            "trigger_preservation": self.trigger_preservation,
            "deadline": int(self.deadline),
            "deadline_ticks": int(self.deadline_ticks),
            "safety_margin": int(self.safety_margin),
            "allowed_danger": float(self.max_danger),
            "fallback_strategy": self.fallback_strategy,
            "source_profile_id": int(self.source_profile_id),
            "source_profile_name": self.source_profile_name,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ObjectiveResult:
    """Outcome diagnostics for one objective-conditioned proposal."""

    achieved: bool
    possible_by_deadline: bool
    miss_reasons: tuple[str, ...] = ()
    surplus_attack: int = 0
    score_delta: int = 0
    chain_delta: int = 0
    deadline_missed: bool = False
    danger_excess: float = 0.0
    time_overrun_ticks: int = 0
    response_capacity: int = 0
    incoming_coverage: float = 0.0
    trigger_preserved: bool = False
    immediate_fire: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "achieved": bool(self.achieved),
            "possible_by_deadline": bool(self.possible_by_deadline),
            "miss_reasons": list(self.miss_reasons),
            "surplus_attack": int(self.surplus_attack),
            "score_delta": int(self.score_delta),
            "chain_delta": int(self.chain_delta),
            "deadline_missed": bool(self.deadline_missed),
            "danger_excess": float(self.danger_excess),
            "time_overrun_ticks": int(self.time_overrun_ticks),
            "response_capacity": int(self.response_capacity),
            "incoming_coverage": float(self.incoming_coverage),
            "trigger_preserved": bool(self.trigger_preserved),
            "immediate_fire": bool(self.immediate_fire),
        }


@dataclass(frozen=True)
class PlanStep:
    """One placement in an externally visible multi-turn plan."""

    step_index: int
    action: int
    axis_x: int
    rotation: str
    known_tsumo: bool
    predicted_chain_count: int
    predicted_score: int
    predicted_attack: int
    cumulative_score: int
    cumulative_attack: int
    danger: float
    objective_result: ObjectiveResult
    predicted_board: tuple[tuple[str, ...], ...]
    placement_cells: tuple[tuple[int, int, str], ...] = ()
    attack_score_delta: int = 0
    score_carry_before: int = 0
    score_carry_after: int = 0
    attack_generated: int = 0
    attack_canceled: int = 0
    attack_outgoing: int = 0
    incoming_remaining: int = 0
    all_clear_achieved: bool = False
    all_clear_bonus_pending: bool = False
    all_clear_bonus_consumed: bool = False
    all_clear_bonus_score: int = 0
    valid: bool = True
    scenario: str = "visible"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": int(self.step_index),
            "action": int(self.action),
            "axis_x": int(self.axis_x),
            "rotation": self.rotation,
            "known_tsumo": bool(self.known_tsumo),
            "scenario": self.scenario,
            "valid": bool(self.valid),
            "predicted_chain_count": int(self.predicted_chain_count),
            "predicted_score": int(self.predicted_score),
            "predicted_attack": int(self.predicted_attack),
            "cumulative_score": int(self.cumulative_score),
            "cumulative_attack": int(self.cumulative_attack),
            "attack_score_delta": int(self.attack_score_delta),
            "score_carry_before": int(self.score_carry_before),
            "score_carry_after": int(self.score_carry_after),
            "attack_generated": int(self.attack_generated),
            "attack_canceled": int(self.attack_canceled),
            "attack_outgoing": int(self.attack_outgoing),
            "incoming_remaining": int(self.incoming_remaining),
            "all_clear_achieved": bool(self.all_clear_achieved),
            "all_clear_bonus_pending": bool(self.all_clear_bonus_pending),
            "all_clear_bonus_consumed": bool(self.all_clear_bonus_consumed),
            "all_clear_bonus_score": int(self.all_clear_bonus_score),
            "danger": float(self.danger),
            "objective_result": self.objective_result.to_dict(),
            "predicted_board": [list(row) for row in self.predicted_board],
            "placement_cells": [
                {"x": int(x), "y": int(y), "color": color}
                for x, y, color in self.placement_cells
            ],
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReplanCondition:
    """Stable reasons consumers can use to discard stale plans."""

    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class NTurnPlan:
    """Stable DTO for visible-tsumo plan display and replay diagnostics."""

    plan_id: str
    profile_id: int
    profile_name: str
    strategy: str
    max_steps: int
    visible_steps: int
    steps: tuple[PlanStep, ...]
    objective: TacticalObjective | None
    search_control: SearchControlDiagnostics | None = None
    planner_request: PlannerRequest | None = None
    initial_score_carry: int = 0
    initial_incoming_attack: int = 0
    planner_latency_overrun: bool = False
    update_reason: str = "policy_decision"
    replan_conditions: tuple[ReplanCondition, ...] = ()

    @property
    def first_action(self) -> int | None:
        return None if not self.steps else self.steps[0].action

    def to_dict(self) -> dict[str, Any]:
        final_carry = self.initial_score_carry
        incoming_remaining = self.initial_incoming_attack
        if self.steps:
            final_carry = self.steps[-1].score_carry_after
            incoming_remaining = self.steps[-1].incoming_remaining
        return {
            "schema_version": "n-turn-plan-v1",
            "plan_id": self.plan_id,
            "profile_id": int(self.profile_id),
            "profile_name": self.profile_name,
            "strategy": self.strategy,
            "max_steps": int(self.max_steps),
            "visible_steps": int(self.visible_steps),
            "update_reason": self.update_reason,
            "objective": {} if self.objective is None else self.objective.to_dict(),
            "search_control": {} if self.search_control is None else self.search_control.to_dict(),
            "planner_request": (
                {} if self.planner_request is None else self.planner_request.to_dict()
            ),
            "planner_latency_overrun": bool(self.planner_latency_overrun),
            "attack_summary": {
                "initial_score_carry": int(self.initial_score_carry),
                "final_score_carry": int(final_carry),
                "initial_incoming_attack": int(self.initial_incoming_attack),
                "incoming_remaining": int(incoming_remaining),
                "generated": sum(step.attack_generated for step in self.steps),
                "canceled": sum(step.attack_canceled for step in self.steps),
                "outgoing": sum(step.attack_outgoing for step in self.steps),
            },
            "replan_conditions": [condition.to_dict() for condition in self.replan_conditions],
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class TacticalContext:
    own_forecast: AttackForecast
    opponent_forecast: AttackForecast
    own_danger: float
    opponent_danger: float
    opponent_capacity: int
    lethal_target: int
    lethal_margin: int
    incoming_attack: int
    incoming_deadline: int
    counter_target: int
    max_return_by_deadline: int
    counter_deficit: int
    build_potential: int
    build_safety: float
    recommended_strategy: str
    switch_reason: str
    incoming_deadline_ticks: int = 0
    score_carry: int = 0


@dataclass(frozen=True)
class SearchProposal:
    """Worker result consumed by policies, training, and diagnostics."""

    action: int
    profile_id: int
    profile_name: str
    strategy: str
    predicted_chain_count: int
    predicted_score: int
    predicted_attack: int
    danger: float
    elapsed_seconds: float
    expanded_nodes: int
    candidate_value: float
    target_attack: int = 0
    incoming_attack: int = 0
    deadline: int = 0
    max_return_attack: int = 0
    reason: str = ""
    objective: TacticalObjective | None = None
    objective_result: ObjectiveResult | None = None
    search_control: SearchControlDiagnostics | None = None
    tactical_option: TacticalOptionDiagnostics | None = None
    planner_request: PlannerRequest | None = None
    trigger_preservation: str = "ignore"
    potential_probe_width: int = 0
    root_build_potential: BuildPotential = BuildPotential(
        schema_version=BUILD_POTENTIAL_V1_SCHEMA_VERSION
    )
    selected_build_potential: BuildPotential = BuildPotential(
        schema_version=BUILD_POTENTIAL_V1_SCHEMA_VERSION
    )
    trigger_preserved: bool = False
    potential_probe_count: int = 0
    potential_cache_hits: int = 0
    trigger_recoverability: TriggerRecoverability = TriggerRecoverability()
    value_breakdown: Mapping[str, float] | None = None
    response_capacity: int = 0
    incoming_coverage: float = 0.0
    immediate_fire: bool = False
    chain_style_evaluation: Mapping[str, Any] | None = None
    beam_candidates: tuple[DiverseBeamCandidate, ...] = ()
    scenario_budget: Mapping[str, Any] | None = None
    worker_proposal: WorkerProposalBatch | None = None

    @property
    def objective_dict(self) -> dict[str, Any]:
        return {} if self.objective is None else self.objective.to_dict()

    @property
    def objective_result_dict(self) -> dict[str, Any]:
        return {} if self.objective_result is None else self.objective_result.to_dict()

    @property
    def search_control_dict(self) -> dict[str, Any]:
        return {} if self.search_control is None else self.search_control.to_dict()

    @property
    def tactical_option_dict(self) -> dict[str, Any]:
        return {} if self.tactical_option is None else self.tactical_option.to_dict()

    @property
    def build_potential_dict(self) -> dict[str, Any]:
        value_breakdown = {
            key: float(value)
            for key, value in (self.value_breakdown or {}).items()
        }
        return {
            "schema_version": self.selected_build_potential.schema_version,
            "preserve_mode": self.trigger_preservation,
            "probe_width": int(self.potential_probe_width),
            "root": self.root_build_potential.to_dict(),
            "selected": self.selected_build_potential.to_dict(),
            "trigger_preserved": bool(self.trigger_preserved),
            "trigger_recoverability": self.trigger_recoverability.to_dict(),
            "probe_count": int(self.potential_probe_count),
            "cache_hits": int(self.potential_cache_hits),
            "value_breakdown": value_breakdown,
            "metric_namespaces": {
                "generic_capability": {
                    "build_potential": self.selected_build_potential.to_dict(),
                    "score_contribution": sum(
                        value
                        for key, value in value_breakdown.items()
                        if key not in {"style_adherence", "total"}
                    ),
                },
                "style_adherence": dict(self.chain_style_evaluation or {}),
            },
        }

    @property
    def beam_candidate_dicts(self) -> tuple[dict[str, Any], ...]:
        return tuple(candidate.to_dict() for candidate in self.beam_candidates)

    @property
    def worker_proposal_dict(self) -> dict[str, Any]:
        return {} if self.worker_proposal is None else self.worker_proposal.to_dict()


@dataclass(frozen=True)
class SearchContext:
    observation: dict[str, Any]
    info: dict[str, Any]
    tactical: TacticalContext
    chain_style_evaluator: ChainStyleEvaluator | None = None

    @property
    def simulator(self):
        return self.info.get("simulator")


class SearchWorker(Protocol):
    def propose(
        self,
        context: SearchContext,
        profile: WorkerProfile,
        objective: TacticalObjective,
        search_control: SearchControlDiagnostics | None = None,
    ) -> SearchProposal:
        """Return one legal placement and its diagnostics."""


def default_worker_profiles() -> tuple[WorkerProfile, ...]:
    """Budgets suitable for repeated versus decisions in Python."""

    return (
        WorkerProfile(0, "build_large", "build_large", depth=6, width=32, minimum_chain_count=6),
        WorkerProfile(
            1,
            "build_budget",
            "build_budget",
            depth=3,
            width=16,
            minimum_chain_count=4,
            chain_weight=65_000.0,
        ),
        WorkerProfile(2, "punish", "punish", depth=3, width=18, safety_margin=0),
        WorkerProfile(3, "counter", "counter", depth=3, width=18, safety_margin=2),
        WorkerProfile(4, "fire_max", "fire_max"),
        WorkerProfile(5, "survival", "survival", danger_tolerance=0.55),
    )


def smoke_worker_profiles() -> tuple[WorkerProfile, ...]:
    """Small deterministic budgets for tests and pipeline smoke runs."""

    return (
        WorkerProfile(0, "build_large", "build_large", depth=2, width=8, minimum_chain_count=3),
        WorkerProfile(1, "build_budget", "build_budget", depth=2, width=8, minimum_chain_count=2),
        WorkerProfile(2, "punish", "punish", depth=2, width=8, safety_margin=0),
        WorkerProfile(3, "counter", "counter", depth=2, width=8, safety_margin=1),
        WorkerProfile(4, "fire_max", "fire_max"),
        WorkerProfile(5, "survival", "survival"),
    )


def default_search_controls() -> tuple[SearchControl, ...]:
    """Hybrid search-control candidates used by the learned manager."""

    return (
        SearchControl(
            0,
            "baseline",
            "discrete_profile",
            latency_budget_ms=40.0,
            parameter_vector=(0.5, 0.5, 0.5),
        ),
        SearchControl(
            1,
            "latency_saver",
            "continuous_parameter",
            depth_scale=0.65,
            width_scale=0.55,
            scenarios=1,
            chain_weight_scale=0.9,
            fire_threshold=0.85,
            latency_budget_ms=18.0,
            cost_penalty=0.02,
            parameter_vector=(0.2, 0.25, 0.35),
        ),
        SearchControl(
            2,
            "broad_value",
            "continuous_parameter",
            depth_scale=1.25,
            width_scale=1.35,
            scenarios=2,
            chain_weight_scale=1.15,
            score_weight_scale=1.1,
            fire_threshold=1.15,
            latency_budget_ms=70.0,
            cost_penalty=0.08,
            parameter_vector=(0.8, 0.75, 0.65),
        ),
        SearchControl(
            3,
            "urgent_fire",
            "hybrid",
            depth_scale=0.75,
            width_scale=0.75,
            scenarios=1,
            chain_weight_scale=0.8,
            fire_threshold=0.65,
            latency_budget_ms=22.0,
            cost_penalty=0.03,
            parameter_vector=(0.35, 0.3, 0.85),
        ),
        SearchControl(
            4,
            "safe_counter",
            "hybrid",
            depth_scale=1.0,
            width_scale=0.85,
            scenarios=1,
            chain_weight_scale=1.0,
            premature_chain_penalty_scale=1.2,
            danger_tolerance_delta=-0.15,
            latency_budget_ms=35.0,
            cost_penalty=0.04,
            parameter_vector=(0.45, 0.4, 0.25),
        ),
    )


def baseline_search_controls() -> tuple[SearchControl, ...]:
    """Single-control set for fixed-worker baselines and legacy comparisons."""

    return default_search_controls()[:1]


def default_tactical_options() -> tuple[TacticalOption, ...]:
    """Non-fixed tactical options layered on top of the six baseline workers."""

    return (
        TacticalOption(
            0,
            "steady_build",
            "build_large",
            "build_large",
            target_chain_delta=1,
            deadline_delta=1,
            danger_tolerance_delta=-0.05,
            termination="objective_or_timeout",
            latent_vector=(0.25, 0.75, 0.20),
        ),
        TacticalOption(
            1,
            "budget_probe",
            "build_budget",
            "build_budget",
            target_chain_delta=-1,
            deadline_delta=-1,
            danger_tolerance_delta=0.05,
            termination="timeout",
            latent_vector=(0.15, 0.45, 0.35),
        ),
        TacticalOption(
            2,
            "lethal_probe",
            "punish",
            "punish",
            target_attack_delta=2,
            deadline_delta=1,
            fire_threshold_scale=0.9,
            termination="objective",
            fallback_profile_name="fire_max",
            latent_vector=(0.85, 0.25, 0.80),
        ),
        TacticalOption(
            3,
            "safe_counter_window",
            "counter",
            "counter",
            target_attack_delta=1,
            danger_tolerance_delta=-0.15,
            termination="danger",
            latent_vector=(0.65, 0.35, 0.25),
        ),
        TacticalOption(
            4,
            "early_release",
            "fire_max",
            "fire_max",
            target_attack_delta=-1,
            fire_threshold_scale=0.75,
            termination="objective",
            latent_vector=(0.70, 0.20, 0.95),
        ),
        TacticalOption(
            5,
            "survival_stall",
            "survival",
            "survival",
            danger_tolerance_delta=-0.2,
            deadline_delta=1,
            termination="danger",
            latent_vector=(0.10, 0.15, 0.10),
        ),
    )


def scaled_worker_profiles(
    profiles: tuple[WorkerProfile, ...],
    *,
    depth_scale: float = 1.0,
    width_scale: float = 1.0,
) -> tuple[WorkerProfile, ...]:
    """Return execution-only budgets while preserving profile ids and semantics."""

    return tuple(
        WorkerProfile(
            **{
                **profile.__dict__,
                "depth": max(1, int(round(profile.depth * depth_scale))),
                "width": max(4, int(round(profile.width * width_scale))),
            }
        )
        for profile in profiles
    )


def _clamp_int(value: float, lower: int, upper: int) -> tuple[int, bool]:
    rounded = int(round(value))
    clamped = min(max(rounded, lower), upper)
    return clamped, clamped != rounded


def _clamp_float(value: float, lower: float, upper: float) -> tuple[float, bool]:
    clamped = min(max(float(value), lower), upper)
    return clamped, clamped != float(value)


def _profile_budget_dict(profile: WorkerProfile) -> dict[str, Any]:
    return {
        "depth": int(profile.depth),
        "width": int(profile.width),
        "scenarios": int(profile.scenarios),
        "minimum_chain_count": int(profile.minimum_chain_count),
        "chain_weight": float(profile.chain_weight),
        "score_weight": float(profile.score_weight),
        "premature_chain_penalty": float(profile.premature_chain_penalty),
        "danger_tolerance": float(profile.danger_tolerance),
        "fire_threshold": float(profile.fire_threshold),
        "trigger_preservation": profile.trigger_preservation,
        "potential_probe_width": int(profile.potential_probe_width),
        "scoring_mode": profile.scoring_mode,
        "future_potential_weight": float(profile.future_potential_weight),
        "chain_shape_weight": float(profile.chain_shape_weight),
        "danger_weight": float(profile.danger_weight),
        "build_potential_schema_version": (
            profile.build_potential_schema_version
        ),
        "potential_probe_budget": int(profile.potential_probe_budget),
        "search_profile": profile.search_profile,
    }


def apply_search_control(
    profile: WorkerProfile,
    control: SearchControl | None,
) -> tuple[WorkerProfile, SearchControlDiagnostics | None]:
    """Apply bounded learned parameters while preserving profile identity."""

    if control is None:
        return profile, None
    clamped_fields: list[str] = []
    depth_upper = 10 if profile.trigger_preservation != "ignore" else 8
    depth, clamped = _clamp_int(
        profile.depth * control.depth_scale,
        1,
        depth_upper,
    )
    if clamped:
        clamped_fields.append("depth")
    width, clamped = _clamp_int(profile.width * control.width_scale, 4, 64)
    if clamped:
        clamped_fields.append("width")
    scenarios, clamped = _clamp_int(control.scenarios, 1, 4)
    if clamped:
        clamped_fields.append("scenarios")
    chain_weight, clamped = _clamp_float(profile.chain_weight * control.chain_weight_scale, 1_000.0, 250_000.0)
    if clamped:
        clamped_fields.append("chain_weight")
    score_weight, clamped = _clamp_float(profile.score_weight * control.score_weight_scale, 0.05, 10.0)
    if clamped:
        clamped_fields.append("score_weight")
    premature_penalty, clamped = _clamp_float(
        profile.premature_chain_penalty * control.premature_chain_penalty_scale,
        0.0,
        5_000.0,
    )
    if clamped:
        clamped_fields.append("premature_chain_penalty")
    danger_tolerance, clamped = _clamp_float(profile.danger_tolerance + control.danger_tolerance_delta, 0.05, 1.0)
    if clamped:
        clamped_fields.append("danger_tolerance")
    fire_threshold, clamped = _clamp_float(profile.fire_threshold * control.fire_threshold, 0.25, 2.0)
    if clamped:
        clamped_fields.append("fire_threshold")
    effective = replace(
        profile,
        depth=depth,
        width=width,
        scenarios=scenarios,
        chain_weight=chain_weight,
        score_weight=score_weight,
        premature_chain_penalty=premature_penalty,
        danger_tolerance=danger_tolerance,
        fire_threshold=fire_threshold,
    )
    diagnostics = SearchControlDiagnostics(
        control=control,
        requested_profile=profile,
        effective_profile=effective,
        clamped_fields=tuple(clamped_fields),
    )
    return effective, diagnostics


def profile_id_by_name(profiles: tuple[WorkerProfile, ...], *names: str) -> int:
    for profile in profiles:
        if profile.name in names or profile.strategy in names:
            return profile.profile_id
    raise KeyError(f"worker profile not found: {names}")


class TacticalOptionController:
    """Resolve a learned option into executable profile and objective parameters."""

    def __init__(self, profiles: tuple[WorkerProfile, ...], options: tuple[TacticalOption, ...] | None = None):
        self.profiles = profiles
        self.options = options or default_tactical_options()
        expected = tuple(range(len(self.options)))
        actual = tuple(option.option_id for option in self.options)
        if actual != expected:
            raise ValueError(f"option ids must be contiguous from zero: {actual}")

    def resolve(
        self,
        option_id: int,
        tactical: TacticalContext,
    ) -> tuple[WorkerProfile, TacticalObjective, TacticalOptionDiagnostics]:
        option = self.options[int(option_id)]
        base = self.profiles[profile_id_by_name(self.profiles, option.base_profile_name, option.strategy)]
        effective = replace(
            base,
            name=option.name,
            strategy=option.strategy,
            minimum_chain_count=max(1, base.minimum_chain_count + option.target_chain_delta),
            danger_tolerance=min(max(base.danger_tolerance + option.danger_tolerance_delta, 0.05), 1.0),
            fire_threshold=min(max(base.fire_threshold * option.fire_threshold_scale, 0.25), 2.0),
        )
        objective = _objective_for_option(tactical, effective, option)
        diagnostics = TacticalOptionDiagnostics(
            option=option,
            base_profile=base,
            effective_profile=effective,
            termination_reason=_option_termination_reason(option, objective, tactical),
        )
        return effective, objective, diagnostics


def _objective_for_option(
    tactical: TacticalContext,
    profile: WorkerProfile,
    option: TacticalOption,
) -> TacticalObjective:
    objective = objective_for_profile(tactical, profile)
    return replace(
        objective,
        target_attack=max(0, objective.target_attack + option.target_attack_delta),
        target_score=max(0, objective.target_score + option.target_attack_delta * 70),
        target_chain=max(0, objective.target_chain + option.target_chain_delta),
        deadline=max(1, objective.deadline + option.deadline_delta) if objective.deadline else 0,
        max_danger=min(max(objective.max_danger + option.danger_tolerance_delta, 0.05), 1.0),
        fallback_strategy=option.fallback_profile_name,
        source_profile_id=profile.profile_id,
        source_profile_name=option.name,
        reason=f"{objective.reason}; option={option.name}; termination={option.termination}",
    )


def _option_termination_reason(
    option: TacticalOption,
    objective: TacticalObjective,
    tactical: TacticalContext,
) -> str:
    if option.termination == "danger" and tactical.own_danger > objective.max_danger:
        return "danger_threshold"
    if option.termination == "objective" and objective.target_attack <= tactical.own_forecast.immediate_attack:
        return "objective_reached"
    if option.termination == "timeout" and objective.deadline <= 1:
        return "timeout_window"
    return "active"


def board_danger(game) -> float:
    """Return a bounded height/ojama risk estimate."""

    heights = []
    ojama = 0
    for x in range(GRID_WIDTH):
        height = 0
        for y in range(GRID_HEIGHT - 1, -1, -1):
            puyo = game.field.grid[y][x]
            if puyo.color == PuyoColor.OJAMA:
                ojama += 1
            if height == 0 and not puyo.is_empty():
                height = y + 1
        heights.append(height)
    center = heights[2] / float(GRID_HEIGHT)
    peak = max(heights) / float(GRID_HEIGHT)
    nuisance = min(ojama / 30.0, 1.0)
    return min(1.0, center * 0.55 + peak * 0.35 + nuisance * 0.10)


def _supports_long_horizon_scenarios(simulator: Any) -> bool:
    """Return whether current/NEXT2 satisfy the compact four-color contract."""

    if simulator is None:
        return False
    game = simulator.game
    pairs = [
        (game.current_puyo_1, game.current_puyo_2),
        *tuple(game.next_puyo_queue)[:2],
    ]
    return all(
        puyo is not None and getattr(puyo, "color", None) in NORMAL_PUYO_COLORS
        for pair in pairs
        for puyo in pair
    )


def estimate_attack_forecast(
    simulator,
    *,
    max_depth: int = 2,
    width: int = 3,
    score_carry: int = 0,
) -> AttackForecast:
    """Bounded cloned rollout used for manager features, not full worker search."""

    if simulator is None:
        return AttackForecast()
    frontier = [(clone_simulator(simulator), 0, 0, max(0, int(score_carry)))]
    best_chain = 0
    best_by_depth = {1: 0, 2: 0, 3: 0}
    best_attack = 0
    best_turn = 0
    for depth in range(1, max(1, min(int(max_depth), 3)) + 1):
        candidates = []
        for parent, cumulative_attack, cumulative_chain, parent_carry in frontier:
            for action in legal_action_indices(parent):
                child = clone_simulator(parent)
                result = child.step(action_to_placement(action))
                if not result.valid or result.game_over:
                    continue
                preview = resolve_preview_attack(result.attack_score_delta, parent_carry, 0)
                attack = cumulative_attack + preview.generated
                chain = max(cumulative_chain, int(result.chain_count))
                best_chain = max(best_chain, chain)
                best_by_depth[depth] = max(best_by_depth[depth], attack)
                if attack > best_attack:
                    best_attack = attack
                    best_turn = depth
                heuristic = attack * 100_000.0 + chain * 10_000.0 + evaluate_board(child.game)
                candidates.append((heuristic, child, attack, chain, preview.score_carry_after))
        candidates.sort(key=lambda item: item[0], reverse=True)
        frontier = [
            (item[1], item[2], item[3], item[4])
            for item in candidates[: max(1, width)]
        ]
        if not frontier:
            break
    return AttackForecast(
        immediate_chain=best_chain if max_depth == 1 else _estimate_immediate_chain(simulator),
        immediate_attack=best_by_depth[1],
        short_attack=max(best_by_depth[1], best_by_depth[2]),
        medium_attack=max(best_by_depth.values()),
        turns_to_best=best_turn,
    )


def estimate_immediate_threat(simulator) -> tuple[int, int]:
    forecast = estimate_attack_forecast(simulator, max_depth=1, width=22)
    return forecast.immediate_chain, forecast.immediate_attack


def _estimate_immediate_chain(simulator) -> int:
    best_chain = 0
    for action in legal_action_indices(simulator):
        child = clone_simulator(simulator)
        result = child.step(action_to_placement(action))
        if result.valid:
            best_chain = max(best_chain, int(result.chain_count))
    return best_chain


def _opponent_capacity(simulator, pending: int) -> int:
    if simulator is None:
        return 0
    center_height = 0
    for y in range(VISIBLE_HEIGHT - 1, -1, -1):
        if not simulator.game.field.get_puyo(2, y).is_empty():
            center_height = y + 1
            break
    rows_to_choke = max(0, VISIBLE_HEIGHT - center_height)
    return max(0, rows_to_choke * GRID_WIDTH - max(0, int(pending)))


def build_tactical_context(info: dict[str, Any]) -> TacticalContext:
    cached = info.get("tactical_context")
    if isinstance(cached, TacticalContext):
        return cached
    own_simulator = info.get("simulator")
    opponent_simulator = info.get("opponent_simulator")
    score_carry = max(0, int(info.get("score_carry", 0)))
    opponent_score_carry = max(0, int(info.get("opponent_score_carry", 0)))
    own_forecast = estimate_attack_forecast(own_simulator, score_carry=score_carry)
    opponent_forecast = estimate_attack_forecast(
        opponent_simulator,
        score_carry=opponent_score_carry,
    )
    own_danger = board_danger(own_simulator.game) if own_simulator is not None else 1.0
    opponent_danger = board_danger(opponent_simulator.game) if opponent_simulator is not None else 1.0
    incoming = max(0, int(info.get("incoming_ojama", info.get("pending_ojama", 0))))
    deadline = max(0, int(info.get("incoming_turns", 0)))
    deadline_ticks = max(0, int(info.get("incoming_ticks", info.get("incoming_arrival_tick", 0)) or 0))
    opponent_pending = max(0, int(info.get("opponent_pending_ojama", 0)))
    capacity = _opponent_capacity(opponent_simulator, opponent_pending)
    lethal_target = max(1, min(30, capacity + 1))
    lethal_margin = own_forecast.immediate_attack - lethal_target
    counter_target = incoming + (2 if incoming > 0 else 0)
    if deadline <= 1:
        max_return = own_forecast.immediate_attack
    elif deadline == 2:
        max_return = own_forecast.short_attack
    else:
        max_return = own_forecast.medium_attack
    counter_deficit = counter_target - max_return
    build_potential = own_forecast.medium_attack
    build_safety = max(0.0, 1.0 - own_danger)

    incoming_dangerous = incoming > 0 and (incoming >= max(6, capacity // 2) or own_danger >= 0.6)
    if incoming_dangerous and counter_deficit <= 0:
        recommended = "counter"
        reason = "incoming attack is dangerous and can be canceled before arrival"
    elif incoming_dangerous and counter_deficit > 0:
        recommended = "survival"
        reason = "incoming attack exceeds the estimated return before deadline"
    elif lethal_margin >= 0:
        recommended = "punish"
        reason = "an immediate attack reaches the estimated lethal target"
    elif own_danger >= 0.82:
        recommended = "survival"
        reason = "board danger is above the survival threshold"
    elif own_forecast.immediate_attack >= 12 and build_safety < 0.35:
        recommended = "fire_max"
        reason = "banked immediate attack should be fired before board safety collapses"
    else:
        recommended = "build_large"
        reason = "no urgent lethal, counter, or survival condition is active"
    return TacticalContext(
        own_forecast=own_forecast,
        opponent_forecast=opponent_forecast,
        own_danger=own_danger,
        opponent_danger=opponent_danger,
        opponent_capacity=capacity,
        lethal_target=lethal_target,
        lethal_margin=lethal_margin,
        incoming_attack=incoming,
        incoming_deadline=deadline,
        counter_target=counter_target,
        max_return_by_deadline=max_return,
        counter_deficit=counter_deficit,
        build_potential=build_potential,
        build_safety=build_safety,
        recommended_strategy=recommended,
        switch_reason=reason,
        incoming_deadline_ticks=deadline_ticks,
        score_carry=score_carry,
    )


def objective_for_profile(tactical: TacticalContext, profile: WorkerProfile) -> TacticalObjective:
    strategy = profile.strategy
    if strategy == "punish":
        return TacticalObjective(
            kind="punish",
            target_attack=tactical.lethal_target,
            target_score=tactical.lethal_target * 70,
            target_chain=1,
            deadline=max(1, min(profile.depth, 3)),
            deadline_ticks=tactical.incoming_deadline_ticks,
            max_danger=profile.danger_tolerance,
            fallback_strategy="fire_max",
            source_profile_id=profile.profile_id,
            source_profile_name=profile.name,
            reason=tactical.switch_reason,
        )
    if strategy == "counter":
        deadline = max(1, min(profile.depth, tactical.incoming_deadline or 1))
        return TacticalObjective(
            kind="counter",
            target_attack=tactical.counter_target,
            target_score=tactical.counter_target * 70,
            target_chain=1,
            deadline=deadline,
            deadline_ticks=tactical.incoming_deadline_ticks,
            safety_margin=profile.safety_margin,
            max_danger=profile.danger_tolerance,
            fallback_strategy="survival",
            source_profile_id=profile.profile_id,
            source_profile_name=profile.name,
            reason=tactical.switch_reason,
        )
    if strategy in {"fire", "fire_max"}:
        target_attack = max(1, int(round(tactical.own_forecast.immediate_attack * profile.fire_threshold)))
        return TacticalObjective(
            kind="fire_max",
            target_attack=target_attack,
            target_score=max(70, target_attack * 70),
            target_chain=max(1, tactical.own_forecast.immediate_chain),
            deadline=1,
            deadline_ticks=tactical.incoming_deadline_ticks,
            fallback_strategy="survival",
            source_profile_id=profile.profile_id,
            source_profile_name=profile.name,
            reason=tactical.switch_reason,
        )
    if strategy == "survival":
        return TacticalObjective(
            kind="survival",
            deadline=1,
            deadline_ticks=tactical.incoming_deadline_ticks,
            max_danger=profile.danger_tolerance,
            fallback_strategy="survival",
            source_profile_id=profile.profile_id,
            source_profile_name=profile.name,
            reason=tactical.switch_reason,
        )
    return TacticalObjective(
        kind="build",
        target_attack=0,
        target_chain=profile.minimum_chain_count,
        deadline=max(1, profile.depth),
        max_danger=profile.danger_tolerance,
        fallback_strategy="survival",
        source_profile_id=profile.profile_id,
        source_profile_name=profile.name,
        reason=tactical.switch_reason,
    )


def objective_from_v1_profile(profile: WorkerProfile, tactical: TacticalContext) -> TacticalObjective:
    """Compatibility shim for the v1.0 fixed-profile manager contract."""

    return objective_for_profile(tactical, profile)


class BeamStrategyWorker:
    """Adapter that applies a build profile to the shared beam search engine."""

    def propose(
        self,
        context: SearchContext,
        profile: WorkerProfile,
        objective: TacticalObjective,
        search_control: SearchControlDiagnostics | None = None,
    ) -> SearchProposal:
        started = time.perf_counter()
        config_values = {
            "depth": profile.depth,
            "width": profile.width,
            "scenarios": profile.scenarios,
            "minimum_chain_count": profile.minimum_chain_count,
            "chain_weight": profile.chain_weight,
            "score_weight": profile.score_weight,
            "premature_chain_penalty": profile.premature_chain_penalty,
            "trigger_preservation": profile.trigger_preservation,
            "probe_width": profile.potential_probe_width,
            "scoring_mode": profile.scoring_mode,
            "future_potential_weight": profile.future_potential_weight,
            "chain_shape_weight": profile.chain_shape_weight,
            "danger_weight": profile.danger_weight,
            "danger_tolerance": profile.danger_tolerance,
            "build_potential_schema_version": (
                profile.build_potential_schema_version
            ),
            "potential_probe_budget": profile.potential_probe_budget,
            "chain_style_evaluator": context.chain_style_evaluator,
            "candidate_mode": (
                DIVERSE_CANDIDATE_MODE
                if profile.scoring_mode == BUILD_SCORING_V2
                else LEGACY_CANDIDATE_MODE
            ),
            "candidate_limit": (
                max(1, profile.potential_probe_width)
                if profile.scoring_mode == BUILD_SCORING_V2
                else 1
            ),
        }
        requested_long_horizon = (
            profile.search_profile is not None
            and context.chain_style_evaluator is None
        )
        if requested_long_horizon and _supports_long_horizon_scenarios(
            context.simulator
        ):
            beam_config = BeamSearchConfig.for_profile(
                profile.search_profile,
                depth=profile.depth,
                width=profile.width,
                scenarios=profile.scenarios,
                minimum_chain_count=profile.minimum_chain_count,
                trigger_preservation=profile.trigger_preservation,
                danger_weight=profile.danger_weight,
                danger_tolerance=profile.danger_tolerance,
                build_potential_schema_version=(
                    profile.build_potential_schema_version
                ),
                potential_probe_budget=profile.potential_probe_budget,
                candidate_limit=(
                    max(1, profile.potential_probe_width)
                    if profile.scoring_mode == BUILD_SCORING_V2
                    else 1
                ),
            )
        else:
            if requested_long_horizon:
                config_values.update(
                    {
                        "search_profile": (
                            f"{profile.search_profile}-fallback-legacy"
                        ),
                        "search_profile_version": None,
                    }
                )
            beam_config = BeamSearchConfig(**config_values)
        policy = BeamSearchPolicy(beam_config)
        action = policy.select_action(context.observation, context.info)
        diagnostics = policy.last_diagnostics
        result, danger = _preview_action(context.simulator, action)
        values = dict(diagnostics.candidate_values) if diagnostics is not None else {}
        attack = (
            resolve_preview_attack(
                result.attack_score_delta,
                context.tactical.score_carry,
                context.tactical.incoming_attack,
            ).generated
            if result is not None
            else 0
        )
        proposal = _proposal(
            profile,
            objective,
            context.tactical,
            action=action,
            chain=result.chain_count if result is not None else 0,
            score=result.score_delta if result is not None else 0,
            attack=attack,
            danger=danger,
            elapsed=(diagnostics.elapsed_seconds if diagnostics is not None else time.perf_counter() - started),
            expanded=diagnostics.expanded_nodes if diagnostics is not None else 0,
            value=float(values.get(action, 0.0)),
            search_control=search_control,
        )
        if diagnostics is None:
            return proposal
        selected_candidate = next(
            (
                candidate
                for candidate in diagnostics.candidates
                if candidate.action == action
            ),
            None,
        )
        return replace(
            proposal,
            trigger_preservation=diagnostics.trigger_preservation,
            potential_probe_width=diagnostics.probe_width,
            root_build_potential=diagnostics.root_potential,
            selected_build_potential=diagnostics.selected_potential,
            trigger_preserved=diagnostics.trigger_preserved,
            trigger_recoverability=diagnostics.trigger_recoverability,
            potential_probe_count=diagnostics.potential_probe_count,
            potential_cache_hits=diagnostics.potential_cache_hits,
            value_breakdown=(
                None
                if selected_candidate is None
                else selected_candidate.value_breakdown
            ),
            chain_style_evaluation=(
                None
                if selected_candidate is None
                or not selected_candidate.chain_style_evaluation
                else selected_candidate.chain_style_evaluation
            ),
            beam_candidates=diagnostics.proposals,
            scenario_budget=dict(diagnostics.scenario_budget),
        )


@dataclass(frozen=True)
class _ResponseCandidate:
    simulator: Any
    action: int
    chain: int
    score: int
    attack: int
    danger: float
    capacity: int
    coverage: float
    immediate_fire: bool
    selected_potential: BuildPotential
    trigger_preserved: bool
    trigger_recoverability: TriggerRecoverability
    value: float


class ResponseReadinessWorker:
    """Prepare a deadline-bounded response without firing the prepared chain."""

    def propose(
        self,
        context: SearchContext,
        profile: WorkerProfile,
        objective: TacticalObjective,
        search_control: SearchControlDiagnostics | None = None,
    ) -> SearchProposal:
        started = time.perf_counter()
        simulator = context.simulator
        legal = legal_action_indices(simulator) if simulator is not None else _legal_from_info(context.info)
        if simulator is None or not legal:
            return _proposal(
                profile,
                objective,
                context.tactical,
                action=legal[0] if legal else 0,
                danger=1.0,
                search_control=search_control,
            )

        preserve_trigger = objective.trigger_preservation != "ignore"
        probe_width = (
            min(len(legal), max(0, int(profile.potential_probe_width)))
            if preserve_trigger
            else 0
        )
        potential_budget = BuildPotentialBudget()
        potential_session = BuildPotentialSession(
            schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
            budget=potential_budget,
            max_evaluations=max(1, int(profile.potential_probe_budget)),
        )
        unknown_potential = BuildPotential(
            evaluation_status="not_evaluated",
            budget=potential_budget,
            schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
        )
        root_potential = (
            potential_session.evaluate(simulator)
            if preserve_trigger
            else unknown_potential
        )
        forecast_depth = max(1, min(3, int(objective.deadline) - 1))
        candidates: list[_ResponseCandidate] = []
        for action in legal:
            child = clone_simulator(simulator)
            result = child.step(action_to_placement(action))
            if not result.valid or result.game_over:
                continue
            preview = resolve_preview_attack(
                result.attack_score_delta,
                context.tactical.score_carry,
                context.tactical.incoming_attack,
            )
            immediate_fire = result.chain_count > 0 or preview.generated > 0
            forecast = estimate_attack_forecast(
                child,
                max_depth=forecast_depth,
                width=max(1, min(int(profile.width), 8)),
                score_carry=preview.score_carry_after,
            )
            capacity = max(0, int(forecast.medium_attack))
            required = max(1, int(objective.required_response_attack))
            coverage = min(1.0, capacity / required)
            danger = board_danger(child.game)
            value = (
                coverage * 1_000_000.0
                + capacity * 20_000.0
                + evaluate_board(child.game)
                - danger * 50_000.0
                - (10_000_000.0 if immediate_fire else 0.0)
            )
            candidates.append(
                _ResponseCandidate(
                    simulator=child,
                    action=action,
                    chain=int(result.chain_count),
                    score=max(0, int(result.score_delta)),
                    attack=int(preview.generated),
                    danger=float(danger),
                    capacity=capacity,
                    coverage=coverage,
                    immediate_fire=immediate_fire,
                    selected_potential=unknown_potential,
                    trigger_preserved=False,
                    trigger_recoverability=TriggerRecoverability(),
                    value=value,
                )
            )
        if not candidates:
            return _proposal(
                profile,
                objective,
                context.tactical,
                action=legal[0],
                danger=1.0,
                search_control=search_control,
            )
        probed_actions = {
            candidate.action
            for candidate in sorted(
                candidates,
                key=lambda item: (item.value, -item.action),
                reverse=True,
            )[:probe_width]
        }
        evaluated_candidates = []
        for candidate in candidates:
            selected_potential = (
                potential_session.evaluate(candidate.simulator)
                if candidate.action in probed_actions
                else unknown_potential
            )
            recoverability = compare_build_potential_triggers(
                root_potential,
                selected_potential,
            )
            trigger_preserved = (
                preserve_trigger and recoverability.policy_preserved
            )
            preserve_value = (
                (1.0 if trigger_preserved else -1.0)
                if preserve_trigger
                else 0.0
            )
            evaluated_candidates.append(
                replace(
                    candidate,
                    selected_potential=selected_potential,
                    trigger_preserved=trigger_preserved,
                    trigger_recoverability=recoverability,
                    value=candidate.value + preserve_value * 120_000.0,
                )
            )
        candidates = evaluated_candidates
        best = max(candidates, key=lambda item: (item.value, -item.action))
        proposal = _proposal(
            profile,
            objective,
            context.tactical,
            action=best.action,
            chain=best.chain,
            score=best.score,
            attack=best.attack,
            danger=best.danger,
            elapsed=time.perf_counter() - started,
            expanded=len(candidates),
            value=best.value,
            response_capacity=best.capacity,
            incoming_coverage=best.coverage,
            trigger_preserved=best.trigger_preserved,
            immediate_fire=best.immediate_fire,
            search_control=search_control,
        )
        return replace(
            proposal,
            trigger_preservation=objective.trigger_preservation,
            potential_probe_width=probe_width,
            root_build_potential=root_potential,
            selected_build_potential=best.selected_potential,
            trigger_recoverability=best.trigger_recoverability,
            potential_probe_count=(
                potential_session.evaluation_count if preserve_trigger else 0
            ),
            potential_cache_hits=(
                potential_session.cache_hits if preserve_trigger else 0
            ),
        )


def _same_build_trigger(left: BuildPotential, right: BuildPotential) -> bool:
    return compare_build_potential_triggers(left, right).policy_preserved


@dataclass
class _TacticalCandidate:
    simulator: Any
    first_action: int
    attack: int
    score: int
    chain: int
    danger: float
    depth: int
    value: float
    score_carry: int


class TacticalStrategyWorker:
    """Bounded objective search for punish, counter, fire, and survival."""

    def propose(
        self,
        context: SearchContext,
        profile: WorkerProfile,
        objective: TacticalObjective,
        search_control: SearchControlDiagnostics | None = None,
    ) -> SearchProposal:
        simulator = context.simulator
        legal = legal_action_indices(simulator) if simulator is not None else _legal_from_info(context.info)
        if not legal:
            return _proposal(
                profile,
                objective,
                context.tactical,
                action=0,
                danger=1.0,
                search_control=search_control,
            )

        started = time.perf_counter()
        max_depth = 1 if objective.kind in {"fire_max", "survival"} else max(1, objective.deadline)
        frontier: list[_TacticalCandidate] = []
        all_candidates: list[_TacticalCandidate] = []
        expanded = 0
        for depth in range(1, max_depth + 1):
            parents = frontier if depth > 1 else [None]
            next_frontier: list[_TacticalCandidate] = []
            for parent in parents:
                parent_simulator = simulator if parent is None else parent.simulator
                for action in legal_action_indices(parent_simulator):
                    child = clone_simulator(parent_simulator)
                    result = child.step(action_to_placement(action))
                    expanded += 1
                    if not result.valid:
                        continue
                    first_action = action if parent is None else parent.first_action
                    parent_carry = (
                        context.tactical.score_carry if parent is None else parent.score_carry
                    )
                    preview = resolve_preview_attack(
                        result.attack_score_delta,
                        parent_carry,
                        0,
                    )
                    attack = (0 if parent is None else parent.attack) + preview.generated
                    score = (0 if parent is None else parent.score) + max(0, int(result.score_delta))
                    chain = max(0 if parent is None else parent.chain, int(result.chain_count))
                    danger = board_danger(child.game)
                    value = _tactical_value(objective, attack, score, chain, danger, depth, child.game)
                    if result.game_over:
                        value -= 1_000_000.0
                    candidate = _TacticalCandidate(
                        child,
                        first_action,
                        attack,
                        score,
                        chain,
                        danger,
                        depth,
                        value,
                        preview.score_carry_after,
                    )
                    next_frontier.append(candidate)
                    all_candidates.append(candidate)
            next_frontier.sort(key=lambda item: item.value, reverse=True)
            frontier = next_frontier[: max(1, profile.width)]
            if not frontier:
                break
        if not all_candidates:
            return _proposal(
                profile,
                objective,
                context.tactical,
                action=legal[0],
                danger=1.0,
                search_control=search_control,
            )
        best = max(all_candidates, key=lambda item: item.value)
        return _proposal(
            profile,
            objective,
            context.tactical,
            action=best.first_action,
            chain=best.chain,
            score=best.score,
            attack=best.attack,
            danger=best.danger,
            elapsed=time.perf_counter() - started,
            expanded=expanded,
            value=best.value,
            depth=best.depth,
            search_control=search_control,
        )


def _tactical_value(
    objective: TacticalObjective,
    attack: int,
    score: int,
    chain: int,
    danger: float,
    depth: int,
    game,
) -> float:
    if objective.kind in {"punish", "counter"}:
        deficit = max(0, objective.target_attack - attack)
        excess = max(0, attack - objective.target_attack)
        reached = 1.0 if deficit == 0 else 0.0
        return (
            reached * 1_000_000.0
            + attack * 30_000.0
            - deficit * 50_000.0
            - excess * 1_500.0
            - depth * 8_000.0
            - danger * 25_000.0
        )
    if objective.kind == "fire_max":
        return attack * 100_000.0 + chain * 10_000.0 + score - danger * 20_000.0
    if objective.kind == "survival":
        return evaluate_board(game) - danger * 100_000.0 + attack * 500.0
    return evaluate_board(game) + chain * 10_000.0 - danger * 20_000.0


def _proposal(
    profile: WorkerProfile,
    objective: TacticalObjective,
    tactical: TacticalContext,
    *,
    action: int,
    chain: int = 0,
    score: int = 0,
    attack: int = 0,
    danger: float = 1.0,
    elapsed: float = 0.0,
    expanded: int = 0,
    value: float = 0.0,
    depth: int = 1,
    response_capacity: int = 0,
    incoming_coverage: float = 0.0,
    trigger_preserved: bool = False,
    immediate_fire: bool = False,
    search_control: SearchControlDiagnostics | None = None,
    tactical_option: TacticalOptionDiagnostics | None = None,
) -> SearchProposal:
    elapsed_seconds = float(elapsed)
    if search_control is not None:
        search_control = replace(
            search_control,
            latency_overrun=elapsed_seconds * 1000.0 > search_control.control.latency_budget_ms,
        )
    result = _evaluate_objective(
        objective,
        tactical,
        attack=int(attack),
        score=int(score),
        chain=int(chain),
        danger=float(danger),
        depth=int(depth),
        response_capacity=int(response_capacity),
        incoming_coverage=float(incoming_coverage),
        trigger_preserved=bool(trigger_preserved),
        immediate_fire=bool(immediate_fire),
    )
    return SearchProposal(
        action=action,
        profile_id=profile.profile_id,
        profile_name=profile.name,
        strategy=profile.strategy,
        predicted_chain_count=int(chain),
        predicted_score=int(score),
        predicted_attack=int(attack),
        danger=float(danger),
        elapsed_seconds=elapsed_seconds,
        expanded_nodes=int(expanded),
        candidate_value=float(value),
        target_attack=(
            objective.required_response_attack
            if objective.kind == "response_readiness"
            else objective.target_attack
        ),
        incoming_attack=tactical.incoming_attack,
        deadline=objective.deadline,
        max_return_attack=tactical.max_return_by_deadline,
        reason=objective.reason,
        objective=objective,
        objective_result=result,
        search_control=search_control,
        tactical_option=tactical_option,
        response_capacity=int(response_capacity),
        incoming_coverage=float(incoming_coverage),
        trigger_preserved=bool(trigger_preserved),
        immediate_fire=bool(immediate_fire),
    )


def build_n_turn_plan(
    proposal: SearchProposal,
    simulator,
    tactical: TacticalContext,
    *,
    max_steps: int = 3,
) -> NTurnPlan:
    """Adapt a worker proposal into a simulator-verified visible-tsumo plan."""

    visible_steps = _visible_pair_count(simulator)
    steps: list[PlanStep] = []
    cumulative_score = 0
    cumulative_attack = 0
    score_carry = max(0, int(tactical.score_carry))
    incoming_remaining = max(0, int(tactical.incoming_attack))
    cursor = clone_simulator(simulator) if simulator is not None else None
    objective = proposal.objective
    potential_session = BuildPotentialSession(
        schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
        budget=BuildPotentialBudget(),
        max_evaluations=max(8, int(max_steps) * 24),
    )
    response_root_potential = (
        potential_session.evaluate(simulator)
        if simulator is not None
        and objective is not None
        and objective.kind == "response_readiness"
        and objective.trigger_preservation != "ignore"
        else BuildPotential()
    )
    for step_index in range(max(0, int(max_steps))):
        if cursor is None or objective is None:
            break
        action = (
            proposal.action
            if step_index == 0
            else _choose_plan_continuation(
                cursor,
                objective,
                score_carry=score_carry,
                incoming_attack=incoming_remaining,
                potential_session=potential_session,
            )
        )
        if action is None:
            break
        placement = action_to_placement(action)
        pair_colors = (
            cursor.game.current_puyo_1.color,
            cursor.game.current_puyo_2.color,
        )
        landing_y = cursor.game.find_landing_y(placement.axis_x, placement.rotation)
        placement_cells = tuple(
            (int(x), int(y), color.name)
            for x, y, color in cursor.game.get_landing_cells(
                placement.axis_x,
                placement.rotation,
                pair_colors,
                axis_y=landing_y,
            )
        )
        result = cursor.step(placement)
        if not result.valid:
            steps.append(
                PlanStep(
                    step_index=step_index,
                    action=action,
                    axis_x=placement.axis_x,
                    rotation=placement.rotation.name,
                    known_tsumo=step_index < visible_steps,
                    scenario=_plan_scenario(step_index, visible_steps),
                    predicted_chain_count=0,
                    predicted_score=0,
                    predicted_attack=0,
                    cumulative_score=cumulative_score,
                    cumulative_attack=cumulative_attack,
                    danger=1.0,
                    objective_result=_evaluate_objective(
                        objective,
                        tactical,
                        attack=cumulative_attack,
                        score=cumulative_score,
                        chain=0,
                        danger=1.0,
                        depth=step_index + 1,
                    ),
                    predicted_board=_board_snapshot(cursor.game),
                    placement_cells=placement_cells,
                    score_carry_before=score_carry,
                    score_carry_after=score_carry,
                    incoming_remaining=incoming_remaining,
                    valid=False,
                    reason="invalid_action",
                )
            )
            break
        score = max(0, int(result.score_delta))
        attack_preview = resolve_preview_attack(
            result.attack_score_delta,
            score_carry,
            incoming_remaining,
        )
        attack = attack_preview.generated
        score_carry = attack_preview.score_carry_after
        incoming_remaining = attack_preview.incoming_after
        cumulative_score += score
        cumulative_attack += attack
        danger = board_danger(cursor.game)
        response_capacity = 0
        incoming_coverage = 0.0
        trigger_preserved = False
        immediate_fire = False
        if objective.kind == "response_readiness":
            remaining_depth = max(1, min(3, objective.deadline - step_index - 1))
            response_capacity = estimate_attack_forecast(
                cursor,
                max_depth=remaining_depth,
                width=8,
                score_carry=score_carry,
            ).medium_attack
            required = max(1, objective.required_response_attack)
            incoming_coverage = min(1.0, response_capacity / required)
            selected_potential = (
                potential_session.evaluate(cursor)
                if objective.trigger_preservation != "ignore"
                else BuildPotential()
            )
            trigger_preserved = (
                objective.trigger_preservation == "ignore"
                or _same_build_trigger(response_root_potential, selected_potential)
            )
            immediate_fire = result.chain_count > 0 or attack > 0
        objective_result = _evaluate_objective(
            objective,
            tactical,
            attack=cumulative_attack,
            score=cumulative_score,
            chain=int(result.chain_count),
            danger=danger,
            depth=step_index + 1,
            response_capacity=response_capacity,
            incoming_coverage=incoming_coverage,
            trigger_preserved=trigger_preserved,
            immediate_fire=immediate_fire,
        )
        steps.append(
            PlanStep(
                step_index=step_index,
                action=action,
                axis_x=placement.axis_x,
                rotation=placement.rotation.name,
                known_tsumo=step_index < visible_steps,
                scenario=_plan_scenario(step_index, visible_steps),
                predicted_chain_count=int(result.chain_count),
                predicted_score=score,
                predicted_attack=attack,
                cumulative_score=cumulative_score,
                cumulative_attack=cumulative_attack,
                danger=danger,
                objective_result=objective_result,
                predicted_board=_board_snapshot(cursor.game),
                placement_cells=placement_cells,
                attack_score_delta=attack_preview.attack_score_delta,
                score_carry_before=attack_preview.score_carry_before,
                score_carry_after=attack_preview.score_carry_after,
                attack_generated=attack_preview.generated,
                attack_canceled=attack_preview.canceled,
                attack_outgoing=attack_preview.outgoing,
                incoming_remaining=attack_preview.incoming_after,
                all_clear_achieved=bool(result.all_clear_achieved),
                all_clear_bonus_pending=bool(result.all_clear_bonus_pending),
                all_clear_bonus_consumed=bool(result.all_clear_bonus_consumed),
                all_clear_bonus_score=max(0, int(result.all_clear_bonus_score)),
            )
        )
        if result.game_over:
            break
    plan_id = _plan_id(proposal, steps, visible_steps)
    return NTurnPlan(
        plan_id=plan_id,
        profile_id=proposal.profile_id,
        profile_name=proposal.profile_name,
        strategy=proposal.strategy,
        max_steps=int(max_steps),
        visible_steps=visible_steps,
        steps=tuple(steps),
        objective=proposal.objective,
        search_control=proposal.search_control,
        planner_request=proposal.planner_request,
        initial_score_carry=max(0, int(tactical.score_carry)),
        initial_incoming_attack=max(0, int(tactical.incoming_attack)),
        planner_latency_overrun=(
            proposal.planner_request is not None
            and proposal.elapsed_seconds * 1000.0 > proposal.planner_request.latency_budget_ms
        ),
        update_reason="policy_decision",
        replan_conditions=default_replan_conditions(),
    )


def default_replan_conditions() -> tuple[ReplanCondition, ...]:
    return (
        ReplanCondition("opponent_event", "opponent score, chain, or incoming attack changed"),
        ReplanCondition("incoming_attack_landed", "reserved ojama landed before the plan was consumed"),
        ReplanCondition("input_failure", "the planned placement is no longer reachable"),
        ReplanCondition("search_result_changed", "a fresh search produced a different first action or plan id"),
    )


def should_replan(
    plan: NTurnPlan | None,
    *,
    current_plan: NTurnPlan | None = None,
    input_failed: bool = False,
    opponent_event: bool = False,
    incoming_attack_landed: bool = False,
) -> ReplanCondition | None:
    """Return the first condition that invalidates an old plan."""

    if plan is None:
        return ReplanCondition("missing_plan", "no active plan is available")
    if input_failed:
        return ReplanCondition("input_failure", "the planned placement is no longer reachable")
    if incoming_attack_landed:
        return ReplanCondition("incoming_attack_landed", "reserved ojama landed before the plan was consumed")
    if opponent_event:
        return ReplanCondition("opponent_event", "opponent state changed while the plan was active")
    if current_plan is not None and current_plan.plan_id != plan.plan_id:
        return ReplanCondition("search_result_changed", "fresh search produced a different plan id")
    return None


def _choose_plan_continuation(
    simulator,
    objective: TacticalObjective,
    *,
    score_carry: int = 0,
    incoming_attack: int = 0,
    potential_session: BuildPotentialSession | None = None,
) -> int | None:
    legal = legal_action_indices(simulator)
    if not legal:
        return None
    best_action = None
    best_value = float("-inf")
    selected_session = potential_session or BuildPotentialSession(
        schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
        budget=BuildPotentialBudget(),
        max_evaluations=len(legal) + 1,
    )
    response_root_potential = (
        selected_session.evaluate(simulator)
        if objective.kind == "response_readiness"
        and objective.trigger_preservation != "ignore"
        else BuildPotential()
    )
    for action in legal:
        child = clone_simulator(simulator)
        result = child.step(action_to_placement(action))
        if not result.valid:
            continue
        attack = resolve_preview_attack(
            result.attack_score_delta,
            score_carry,
            incoming_attack,
        ).generated
        score = max(0, int(result.score_delta))
        chain = int(result.chain_count)
        danger = board_danger(child.game)
        if objective.kind == "response_readiness":
            capacity = estimate_attack_forecast(
                child,
                max_depth=max(1, min(3, objective.deadline - 1)),
                width=8,
                score_carry=score_carry,
            ).medium_attack
            required = max(1, objective.required_response_attack)
            coverage = min(1.0, capacity / required)
            selected_potential = (
                selected_session.evaluate(child)
                if objective.trigger_preservation != "ignore"
                else BuildPotential()
            )
            preserved = (
                objective.trigger_preservation == "ignore"
                or _same_build_trigger(response_root_potential, selected_potential)
            )
            value = (
                coverage * 1_000_000.0
                + capacity * 20_000.0
                + (120_000.0 if preserved else -120_000.0)
                + evaluate_board(child.game)
                - danger * 50_000.0
                - (10_000_000.0 if chain > 0 or attack > 0 else 0.0)
            )
        else:
            value = _tactical_value(objective, attack, score, chain, danger, 1, child.game)
        if value > best_value:
            best_action = action
            best_value = value
    return best_action


def _visible_pair_count(simulator) -> int:
    if simulator is None:
        return 0
    count = len(simulator.game.next_puyo_queue)
    if simulator.game.current_puyo_1 is not None and simulator.game.current_puyo_2 is not None:
        count += 1
    return max(0, min(3, count))


def _plan_scenario(step_index: int, visible_steps: int) -> str:
    return "visible" if step_index < visible_steps else "unknown_scenario"


def _board_snapshot(game) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(game.field.grid[y][x].color.name for x in range(GRID_WIDTH))
        for y in range(GRID_HEIGHT)
    )


def _plan_id(proposal: SearchProposal, steps: list[PlanStep], visible_steps: int) -> str:
    digest = hashlib.sha1()
    digest.update(str(proposal.profile_id).encode("ascii"))
    digest.update(proposal.strategy.encode("ascii"))
    digest.update(str(visible_steps).encode("ascii"))
    if proposal.planner_request is not None:
        digest.update(proposal.planner_request.schema_version.encode("ascii"))
        digest.update(proposal.planner_request.tactic_id.encode("ascii"))
    for step in steps:
        digest.update(
            (
                f"{step.action}:{step.attack_score_delta}:{step.score_carry_before}:"
                f"{step.score_carry_after}:{step.attack_generated}:{step.attack_canceled}:"
                f"{step.attack_outgoing}:{int(step.all_clear_bonus_pending)}:"
            ).encode("ascii")
        )
        for x, y, color in step.placement_cells:
            digest.update(f"{x}:{y}:{color}:".encode("ascii"))
        for row in step.predicted_board:
            digest.update(",".join(row).encode("ascii"))
    return digest.hexdigest()[:16]


def _evaluate_objective(
    objective: TacticalObjective,
    tactical: TacticalContext,
    *,
    attack: int,
    score: int,
    chain: int,
    danger: float,
    depth: int,
    response_capacity: int = 0,
    incoming_coverage: float = 0.0,
    trigger_preserved: bool = False,
    immediate_fire: bool = False,
) -> ObjectiveResult:
    miss_reasons: list[str] = []
    deadline_missed = objective.deadline > 0 and depth > objective.deadline
    if (
        objective.kind == "response_readiness"
        and response_capacity < objective.required_response_attack
    ):
        miss_reasons.append("response_capacity")
    if objective.kind == "response_readiness" and immediate_fire:
        miss_reasons.append("immediate_fire")
    if (
        objective.kind == "response_readiness"
        and objective.trigger_preservation == "required"
        and not trigger_preserved
    ):
        miss_reasons.append("trigger_preservation")
    if objective.kind != "response_readiness" and objective.target_attack > 0 and attack < objective.target_attack:
        miss_reasons.append("target_attack")
    if objective.target_score > 0 and score < objective.target_score:
        miss_reasons.append("target_score")
    if objective.target_chain > 0 and chain < objective.target_chain:
        miss_reasons.append("target_chain")
    danger_excess = max(0.0, float(danger) - float(objective.max_danger))
    if danger_excess > 0.0:
        miss_reasons.append("allowed_danger")
    if deadline_missed:
        miss_reasons.append("deadline")

    possible_by_deadline = True
    if objective.kind == "response_readiness":
        possible_by_deadline = response_capacity >= objective.required_response_attack
        if not possible_by_deadline:
            miss_reasons.append("impossible_by_deadline")
    elif objective.deadline > 0 and objective.target_attack > 0:
        if objective.deadline <= max(1, tactical.incoming_deadline or objective.deadline):
            possible_by_deadline = objective.target_attack <= tactical.max_return_by_deadline
        else:
            possible_by_deadline = objective.target_attack <= tactical.build_potential
        if not possible_by_deadline:
            miss_reasons.append("impossible_by_deadline")

    return ObjectiveResult(
        achieved=not miss_reasons,
        possible_by_deadline=possible_by_deadline,
        miss_reasons=tuple(dict.fromkeys(miss_reasons)),
        surplus_attack=max(0, int(attack) - int(objective.target_attack)),
        score_delta=int(score) - int(objective.target_score),
        chain_delta=int(chain) - int(objective.target_chain),
        deadline_missed=deadline_missed,
        danger_excess=danger_excess,
        response_capacity=max(0, int(response_capacity)),
        incoming_coverage=max(0.0, min(1.0, float(incoming_coverage))),
        trigger_preserved=bool(trigger_preserved),
        immediate_fire=bool(immediate_fire),
    )


def _profile_for_planner_request(
    profile: WorkerProfile,
    request: PlannerRequest,
) -> WorkerProfile:
    weights = request.objective_weights
    trigger_scale = {"required": 1.5, "prefer": 1.2, "ignore": 1.0}[
        request.trigger_preservation
    ]
    build_potential_v2 = request.tactic_id == "build_main"
    uses_build_potential_v2 = request.tactic_id in {
        "build_main",
        "prepare_response",
    }
    if (
        request.tactic_id in {"build_main", "prepare_response"}
        and request.build_potential_schema_version
        != BUILD_POTENTIAL_SCHEMA_VERSION
    ):
        raise ValueError(
            f"{request.tactic_id} requires the BuildPotential v2 schema"
        )
    chain_shape_weight = max(
        0.0,
        float(weights.get("chain_shape_weight", 1.0)),
    )
    future_potential_weight = max(
        0.0,
        float(weights.get("future_potential_weight", 1.0)),
    )
    planner_parameters = request.parameters.get("planner", {})
    requested_search_profile = (
        planner_parameters.get("search_profile", "runtime")
        if isinstance(planner_parameters, Mapping)
        else "runtime"
    )
    if requested_search_profile not in {"runtime", "legacy"}:
        raise ValueError(
            "build_main planner search_profile must be runtime or legacy"
        )
    return replace(
        profile,
        depth=max(1, int(request.search_depth)),
        width=max(1, int(request.search_width)),
        minimum_chain_count=(
            max(1, int(request.target_chain))
            if request.target_chain > 0
            else profile.minimum_chain_count
        ),
        chain_weight=profile.chain_weight,
        score_weight=profile.score_weight,
        premature_chain_penalty=profile.premature_chain_penalty * trigger_scale,
        danger_tolerance=float(request.danger_tolerance),
        trigger_preservation=request.trigger_preservation,
        scoring_mode=(BUILD_SCORING_V2 if build_potential_v2 else profile.scoring_mode),
        future_potential_weight=(
            future_potential_weight
            if build_potential_v2
            else profile.future_potential_weight
        ),
        chain_shape_weight=(
            chain_shape_weight
            if build_potential_v2
            else profile.chain_shape_weight
        ),
        build_potential_schema_version=(
            request.build_potential_schema_version
            if uses_build_potential_v2
            else profile.build_potential_schema_version
        ),
        potential_probe_budget=max(
            1,
            int(request.candidate_count)
            * (
                1
                if request.tactic_id == "prepare_response"
                else max(1, int(request.search_depth)) * max(1, profile.scenarios)
            )
            + int(request.trigger_preservation != "ignore"),
        ),
        potential_probe_width=(
            int(request.candidate_count)
            if request.tactic_id == "build_main"
            or (
                request.tactic_id == "prepare_response"
                and request.trigger_preservation != "ignore"
            )
            else 0
        ),
        search_profile=(
            None
            if request.tactic_id == "build_main"
            and requested_search_profile == "legacy"
            else "runtime"
            if request.tactic_id == "build_main"
            else profile.search_profile
        ),
    )


def _with_decision_probe_budget(
    profile: WorkerProfile,
    *,
    objective_kind: str,
) -> WorkerProfile:
    """Size planner-owned probe budgets after search-control scaling."""

    if profile.potential_probe_width <= 0:
        return profile
    search_positions = (
        1
        if objective_kind == "response_readiness"
        else max(1, profile.depth) * max(1, profile.scenarios)
    )
    return replace(
        profile,
        potential_probe_budget=max(
            1,
            profile.potential_probe_width * search_positions
            + int(profile.trigger_preservation != "ignore"),
        ),
    )


def _objective_for_planner_request(
    request: PlannerRequest,
    profile: WorkerProfile,
) -> TacticalObjective:
    return TacticalObjective(
        kind=request.objective_kind,
        target_attack=max(0, int(request.target_attack)),
        required_response_attack=max(0, int(request.required_response_attack)),
        trigger_preservation=request.trigger_preservation,
        target_chain=max(0, int(request.target_chain)),
        deadline=max(0, int(request.deadline_turns)),
        deadline_ticks=max(0, int(request.deadline_ticks)),
        max_danger=float(request.danger_tolerance),
        fallback_strategy=request.fallback_tactic,
        source_profile_id=profile.profile_id,
        source_profile_name=profile.name,
        reason=(
            f"PlannerRequest {request.schema_version} from "
            f"{request.tactic_id}@{request.tactic_version}"
        ),
    )


class StrategyOrchestrator:
    """Execute exactly one worker selected by a manager action."""

    def __init__(
        self,
        profiles: tuple[WorkerProfile, ...] | None = None,
        tactical_options: tuple[TacticalOption, ...] | None = None,
        chain_style_registry: ChainStyleRegistry | None = None,
        chain_style_providers: Mapping[str, ChainStyleProvider] | None = None,
    ):
        self.profiles = profiles or default_worker_profiles()
        expected = tuple(range(len(self.profiles)))
        actual = tuple(profile.profile_id for profile in self.profiles)
        if actual != expected:
            raise ValueError(f"profile ids must be contiguous from zero: {actual}")
        self.option_controller = TacticalOptionController(self.profiles, tactical_options)
        self.chain_style_registry = chain_style_registry or load_chain_style_registry()
        self.chain_style_providers = chain_style_providers
        self._beam_worker = BeamStrategyWorker()
        self._response_worker = ResponseReadinessWorker()
        self._tactical_worker = TacticalStrategyWorker()
        self.last_proposal: SearchProposal | None = None
        self.last_plan: NTurnPlan | None = None
        self.last_tactical_context: TacticalContext | None = None

    def propose(
        self,
        profile_id: int,
        observation: dict[str, Any],
        info: dict[str, Any],
        search_control: SearchControl | None = None,
        tactical_option_id: int | None = None,
        planner_request: PlannerRequest | None = None,
    ) -> SearchProposal:
        tactical = build_tactical_context(info)
        if planner_request is not None:
            tactical = replace(
                tactical,
                score_carry=planner_request.score_carry,
                incoming_attack=planner_request.incoming_attack,
            )
        self.last_tactical_context = tactical
        option_diagnostics = None
        if planner_request is not None and tactical_option_id is not None:
            raise ValueError("planner_request and tactical_option_id are mutually exclusive")
        if planner_request is not None:
            profile = _profile_for_planner_request(
                self.profiles[int(profile_id)],
                planner_request,
            )
            objective = _objective_for_planner_request(planner_request, profile)
        elif tactical_option_id is None:
            profile = self.profiles[int(profile_id)]
            objective = objective_for_profile(tactical, profile)
        else:
            profile, objective, option_diagnostics = self.option_controller.resolve(
                int(tactical_option_id),
                tactical,
            )
        profile, control_diagnostics = apply_search_control(profile, search_control)
        if planner_request is not None:
            profile = _with_decision_probe_budget(
                profile,
                objective_kind=objective.kind,
            )
            if control_diagnostics is not None:
                control_diagnostics = replace(
                    control_diagnostics,
                    effective_profile=profile,
                )
        if tactical_option_id is not None:
            objective = replace(
                objective,
                source_profile_name=profile.name,
                max_danger=profile.danger_tolerance,
            )
        style_evaluator = None
        if planner_request is not None and planner_request.chain_style.enabled:
            style_evaluator = ChainStyleEvaluator(
                self.chain_style_registry,
                planner_request.chain_style.selected,
                providers=self.chain_style_providers,
                contribution_scale=profile.chain_weight,
            )
        context = SearchContext(
            observation=observation,
            info=info,
            tactical=tactical,
            chain_style_evaluator=style_evaluator,
        )
        if objective.kind == "response_readiness":
            worker = self._response_worker
        elif objective.kind == "build" and profile.strategy in BUILD_STRATEGIES:
            worker = self._beam_worker
        else:
            worker = self._tactical_worker
        self.last_proposal = worker.propose(context, profile, objective, control_diagnostics)
        if option_diagnostics is not None:
            self.last_proposal = replace(self.last_proposal, tactical_option=option_diagnostics)
        if planner_request is not None:
            self.last_proposal = replace(
                self.last_proposal,
                planner_request=planner_request,
            )
        candidate_limit = (
            int(planner_request.candidate_count)
            if planner_request is not None
            else max(1, len(self.last_proposal.beam_candidates))
        )
        proposal_batch = build_worker_proposal_batch(
            self.last_proposal.beam_candidates,
            selected_action=self.last_proposal.action,
            candidate_limit=candidate_limit,
            legal_action_mask=_proposal_legal_action_mask(info, context.simulator),
            profile_id=self.last_proposal.profile_id,
            profile_name=self.last_proposal.profile_name,
            strategy=self.last_proposal.strategy,
            simulator=context.simulator,
            score_carry=tactical.score_carry,
            incoming_attack=tactical.incoming_attack,
            search_latency_ms=self.last_proposal.elapsed_seconds * 1_000.0,
            expanded_nodes=self.last_proposal.expanded_nodes,
            scenario_budget=self.last_proposal.scenario_budget,
            fallback_preview={
                "predicted_chain_count": self.last_proposal.predicted_chain_count,
                "predicted_score": self.last_proposal.predicted_score,
                "candidate_value": self.last_proposal.candidate_value,
                "danger": self.last_proposal.danger,
                "build_potential": self.last_proposal.selected_build_potential.to_dict(),
                "trigger_recoverability": (
                    self.last_proposal.trigger_recoverability.to_dict()
                ),
                "continuation_flexibility": (
                    self.last_proposal.selected_build_potential.continuation_flexibility
                    or 0.0
                ),
                "value_breakdown": dict(self.last_proposal.value_breakdown or {}),
                "chain_style": dict(self.last_proposal.chain_style_evaluation or {}),
            },
            worker_deadline_status=(
                {
                    "status": (
                        "overrun"
                        if self.last_proposal.elapsed_seconds * 1_000.0
                        > planner_request.latency_budget_ms
                        else "within_budget"
                    ),
                    "budget_ms": float(planner_request.latency_budget_ms),
                    "overrun": bool(
                        self.last_proposal.elapsed_seconds * 1_000.0
                        > planner_request.latency_budget_ms
                    ),
                    "source": "planner_request",
                }
                if planner_request is not None
                else {
                    "status": "not_configured",
                    "budget_ms": None,
                    "overrun": False,
                    "source": "worker_profile",
                }
            ),
        )
        adapted_action = compatibility_action(
            proposal_batch,
            empty_action=self.last_proposal.action,
        )
        if adapted_action != self.last_proposal.action:
            raise RuntimeError(
                "worker proposal compatibility adapter changed the selected action"
            )
        self.last_proposal = replace(
            self.last_proposal,
            worker_proposal=proposal_batch,
        )
        self.last_plan = build_n_turn_plan(self.last_proposal, context.simulator, tactical)
        return self.last_proposal

    def select_action(self, profile_id: int, observation: dict[str, Any], info: dict[str, Any]) -> int:
        return self.propose(profile_id, observation, info).action


class FixedProfilePolicy:
    """Policy adapter used for worker baselines and smoke evaluation."""

    def __init__(self, profile_id: int, profiles: tuple[WorkerProfile, ...] | None = None):
        self.profile_id = int(profile_id)
        self.orchestrator = StrategyOrchestrator(profiles)
        self.last_proposal: SearchProposal | None = None
        self.last_plan: NTurnPlan | None = None

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        self.last_proposal = self.orchestrator.propose(self.profile_id, observation, info)
        self.last_plan = self.orchestrator.last_plan
        return self.last_proposal.action

    @property
    def plan_diagnostics(self) -> dict[str, Any]:
        return {} if self.last_plan is None else self.last_plan.to_dict()


def _preview_action(simulator, action: int):
    if simulator is None:
        return None, 1.0
    child = clone_simulator(simulator)
    result = child.step(action_to_placement(action))
    if not result.valid:
        return None, 1.0
    return result, board_danger(child.game)


def _proposal_legal_action_mask(info: Mapping[str, Any], simulator) -> tuple[bool, ...]:
    raw = info.get("action_mask")
    if raw is not None:
        values = tuple(bool(value) for value in raw)
        if len(values) == NUM_ACTIONS:
            return values
    if simulator is not None:
        return tuple(legal_action_mask(simulator))
    return tuple(False for _ in range(NUM_ACTIONS))


def _legal_from_info(info: dict[str, Any]) -> list[int]:
    mask = info.get("action_mask")
    if mask is None:
        return []
    return [index for index, allowed in enumerate(mask) if bool(allowed)]
