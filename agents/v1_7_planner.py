"""Parameterized planner contracts shared by v1.7 tactics and search workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agents.state_analyzer import (
    ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
    ANALYZER_INPUT_SCHEMA_VERSION,
    AnalyzerDiagnostics,
    AnalyzerInput,
)
from agents.v1_7_tactics import TacticSpec
from src.core.ojama import convert_score_to_ojama


PLANNER_REQUEST_SCHEMA_VERSION = "planner-schema-v2"
_OBJECTIVE_KINDS = {
    "build_main": "build",
    "prepare_response": "response_readiness",
    "counter_or_return": "counter",
    "pressure": "punish",
    "lethal_attack": "punish",
    "all_clear": "fire_max",
    "fire_main": "fire_max",
    "survive": "survival",
}


@dataclass(frozen=True)
class AttackPreview:
    """Runtime-compatible score conversion and incoming cancellation preview."""

    attack_score_delta: int
    score_carry_before: int
    score_carry_after: int
    generated: int
    canceled: int
    outgoing: int
    incoming_before: int
    incoming_after: int

    def to_dict(self) -> dict[str, int]:
        return {
            "attack_score_delta": int(self.attack_score_delta),
            "score_carry_before": int(self.score_carry_before),
            "score_carry_after": int(self.score_carry_after),
            "generated": int(self.generated),
            "canceled": int(self.canceled),
            "outgoing": int(self.outgoing),
            "incoming_before": int(self.incoming_before),
            "incoming_after": int(self.incoming_after),
        }


@dataclass(frozen=True)
class PlannerRequest:
    """Versioned tactic parameters consumed by an existing search worker."""

    tactic_id: str
    tactic_version: str
    objective_kind: str
    target_chain: int
    target_attack: int
    deadline_turns: int
    deadline_ticks: int
    danger_tolerance: float
    trigger_preservation: str
    search_depth: int
    search_width: int
    candidate_count: int
    latency_budget_ms: float
    fallback_tactic: str
    objective_weights: Mapping[str, float]
    parameters: Mapping[str, Mapping[str, Any]]
    score_carry: int
    incoming_attack: int
    all_clear_achieved: bool
    all_clear_bonus_pending: bool
    all_clear_bonus_consumed: bool
    required_response_attack: int = 0
    response_source: str = "none"
    analyzer_input_schema_version: str = ANALYZER_INPUT_SCHEMA_VERSION
    analyzer_diagnostics_schema_version: str = ANALYZER_DIAGNOSTICS_SCHEMA_VERSION
    schema_version: str = PLANNER_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PLANNER_REQUEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported planner request schema: {self.schema_version}")
        if not self.tactic_id or not self.tactic_version:
            raise ValueError("planner request tactic identity is required")
        if self.objective_kind not in {
            "build",
            "response_readiness",
            "counter",
            "punish",
            "fire_max",
            "survival",
        }:
            raise ValueError(f"unsupported planner objective: {self.objective_kind}")
        if min(
            self.target_chain,
            self.target_attack,
            self.deadline_turns,
            self.deadline_ticks,
            self.score_carry,
            self.incoming_attack,
            self.required_response_attack,
        ) < 0:
            raise ValueError("planner targets, deadlines, carry, and incoming must be non-negative")
        if not 0.0 <= self.danger_tolerance <= 1.0:
            raise ValueError("danger_tolerance must be in [0, 1]")
        if self.trigger_preservation not in {"required", "prefer", "ignore"}:
            raise ValueError(f"unsupported trigger preservation: {self.trigger_preservation}")
        if min(self.search_depth, self.search_width, self.candidate_count) <= 0:
            raise ValueError("planner search budgets must be positive")
        if self.latency_budget_ms <= 0.0:
            raise ValueError("planner latency budget must be positive")
        if self.response_source not in {"none", "incoming", "opponent_forecast", "combined"}:
            raise ValueError(f"unsupported response source: {self.response_source}")
        if self.objective_kind == "response_readiness" and self.required_response_attack <= 0:
            raise ValueError("response readiness requires positive response attack")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tactic_id": self.tactic_id,
            "tactic_version": self.tactic_version,
            "objective": {
                "kind": self.objective_kind,
                "target_chain": int(self.target_chain),
                "target_attack": int(self.target_attack),
                "required_response_attack": int(self.required_response_attack),
                "response_source": self.response_source,
                "deadline_turns": int(self.deadline_turns),
                "deadline_ticks": int(self.deadline_ticks),
                "weights": {key: float(value) for key, value in self.objective_weights.items()},
            },
            "constraints": {
                "danger_tolerance": float(self.danger_tolerance),
                "trigger_preservation": self.trigger_preservation,
            },
            "search_budget": {
                "depth": int(self.search_depth),
                "width": int(self.search_width),
                "candidate_count": int(self.candidate_count),
                "latency_budget_ms": float(self.latency_budget_ms),
            },
            "fallback_tactic": self.fallback_tactic,
            "parameters": {
                section: dict(values) for section, values in self.parameters.items()
            },
            "runtime_context": {
                "score_carry": int(self.score_carry),
                "incoming_attack": int(self.incoming_attack),
                "all_clear_achieved": bool(self.all_clear_achieved),
                "all_clear_bonus_pending": bool(self.all_clear_bonus_pending),
                "all_clear_bonus_consumed": bool(self.all_clear_bonus_consumed),
            },
            "analyzer_input_schema_version": self.analyzer_input_schema_version,
            "analyzer_diagnostics_schema_version": self.analyzer_diagnostics_schema_version,
        }


def resolve_preview_attack(
    attack_score_delta: int,
    score_carry: int,
    incoming_attack: int,
) -> AttackPreview:
    """Apply the same 70-point carry and cancellation order as versus runtime."""

    conversion = convert_score_to_ojama(attack_score_delta, score_carry)
    incoming = max(0, int(incoming_attack))
    canceled = min(conversion.units, incoming)
    return AttackPreview(
        attack_score_delta=max(0, int(attack_score_delta)),
        score_carry_before=max(0, int(score_carry)),
        score_carry_after=conversion.carry,
        generated=conversion.units,
        canceled=canceled,
        outgoing=conversion.units - canceled,
        incoming_before=incoming,
        incoming_after=incoming - canceled,
    )


def build_planner_request(
    tactic: TacticSpec,
    analyzer_input: AnalyzerInput | Mapping[str, Any],
    analyzer_diagnostics: AnalyzerDiagnostics | Mapping[str, Any],
    parameter_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> PlannerRequest:
    """Resolve one TacticSpec into an executable, versioned worker request."""

    input_payload = _payload(analyzer_input)
    diagnostics_payload = _payload(analyzer_diagnostics)
    if input_payload.get("schema_version") != ANALYZER_INPUT_SCHEMA_VERSION:
        raise ValueError("Analyzer input schema does not match planner schema")
    if diagnostics_payload.get("schema_version") != ANALYZER_DIAGNOSTICS_SCHEMA_VERSION:
        raise ValueError("Analyzer diagnostics schema does not match planner schema")
    parameters = tactic.resolve_parameters(parameter_overrides)
    objective = parameters["objective"]
    constraints = parameters["constraints"]
    planner = parameters["planner"]
    own = _mapping(input_payload.get("own"), "Analyzer own input")
    incoming = int(_path(diagnostics_payload, "incoming.amount", 0))
    opponent_threat = int(_path(diagnostics_payload, "opponent.forecast.short_attack", 0))
    target_attack = int(objective.get("target_attack", 0))
    required_response_attack = 0
    response_source = "none"
    if tactic.identity.tactic_id == "prepare_response":
        margin = int(objective.get("target_attack_margin", 0))
        required_response_attack = max(incoming, opponent_threat) + margin
        if incoming > 0 and opponent_threat > 0:
            response_source = "combined"
        elif incoming > 0:
            response_source = "incoming"
        elif opponent_threat > 0:
            response_source = "opponent_forecast"
        if required_response_attack <= 0:
            raise ValueError("prepare_response requires positive incoming or forecast attack")
        # Firing is deliberately not part of this objective. The dedicated
        # readiness worker evaluates whether this attack can be produced later.
        target_attack = 0
    elif tactic.identity.tactic_id in {"counter_or_return", "all_clear"}:
        margin_name = (
            "counter_margin"
            if tactic.identity.tactic_id == "counter_or_return"
            else "target_attack_margin"
        )
        target_attack = incoming + int(objective.get(margin_name, 0))
    if tactic.identity.tactic_id == "prepare_response":
        response_window = int(objective.get("response_window", planner.get("beam_depth", 1)))
        incoming_deadline = int(_path(diagnostics_payload, "incoming.deadline", 0))
        opponent_deadline = int(
            _path(diagnostics_payload, "opponent.forecast.turns_to_best", 0)
        )
        threat_deadlines = [
            value
            for value, active in (
                (incoming_deadline, incoming > 0),
                (opponent_deadline, opponent_threat > 0),
            )
            if active and value > 0
        ]
        deadline = min([response_window, *threat_deadlines]) if threat_deadlines else response_window
    else:
        deadline = _first_int(
            objective,
            "deadline_turns",
            "response_window",
            "survival_horizon",
            default=int(planner.get("beam_depth", 1)),
        )
    weights = {
        name: float(value)
        for section in parameters.values()
        for name, value in section.items()
        if name.endswith("_weight")
    }
    fallback = str(tactic.fallback.get("tactic_id") or tactic.fallback.get("safety_behavior"))
    return PlannerRequest(
        tactic_id=tactic.identity.tactic_id,
        tactic_version=tactic.identity.version,
        objective_kind=_OBJECTIVE_KINDS[tactic.identity.tactic_id],
        target_chain=int(objective.get("target_chain", 0)),
        target_attack=max(0, target_attack),
        deadline_turns=max(0, deadline),
        deadline_ticks=max(0, int(_path(input_payload, "policy_deadline", 0))),
        danger_tolerance=float(constraints.get("danger_tolerance", 1.0)),
        trigger_preservation=str(constraints.get("trigger_preservation", "ignore")),
        search_depth=int(planner.get("beam_depth", 1)),
        search_width=int(planner.get("beam_width", 1)),
        candidate_count=int(planner.get("candidate_count", 1)),
        latency_budget_ms=float(planner.get("latency_budget_ms", 40.0)),
        fallback_tactic=fallback,
        objective_weights=weights,
        parameters=parameters,
        score_carry=max(0, int(own.get("score_carry", 0))),
        incoming_attack=max(0, incoming),
        all_clear_achieved=bool(own.get("all_clear_achieved", False)),
        all_clear_bonus_pending=bool(own.get("all_clear_bonus_pending", False)),
        all_clear_bonus_consumed=bool(own.get("all_clear_bonus_consumed", False)),
        required_response_attack=max(0, required_response_attack),
        response_source=response_source,
    )


def _payload(value: Any) -> Mapping[str, Any]:
    payload = value.to_dict() if hasattr(value, "to_dict") else value
    return _mapping(payload, "Analyzer payload")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _path(value: Mapping[str, Any], path: str, default: Any) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def _first_int(value: Mapping[str, Any], *names: str, default: int) -> int:
    for name in names:
        if name in value:
            return int(value[name])
    return int(default)
