"""Playable Analyzer-driven manager policy for the v1.7.0 baseline."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any, Mapping

from agents.state_analyzer import AnalyzerDiagnostics, AnalyzerInput, StateAnalyzer
from agents.strategy_workers import (
    StrategyOrchestrator,
    WorkerProfile,
    default_worker_profiles,
    profile_id_by_name,
)
from agents.v1_7_planner import PlannerRequest, build_planner_request
from agents.v1_7_tactics import (
    TacticRegistry,
    TacticSpec,
    build_tactic_diagnostics,
    load_tactic_registry,
)
from src.core.constants import GRID_WIDTH, VISIBLE_HEIGHT


ANALYZER_MANAGER_DIAGNOSTICS_SCHEMA_VERSION = (
    "puyo.v1_7_analyzer_manager.diagnostics.v1"
)
MODEL_VERSION = "v1.7.0"
MODEL_FAMILY = "Adaptive Chain Manager"
POLICY_TYPE = "v1_7_analyzer_manager"
LINEAGE_NODE_ID = "model_version:v1.7.0"
DANGER_THRESHOLD = 0.82

_TACTIC_TO_WORKER = {
    "build_main": "build_large",
    "prepare_response": "counter",
    "counter_or_return": "counter",
    "pressure": "punish",
    "lethal_attack": "punish",
    "all_clear": "fire_max",
    "fire_main": "fire_max",
    "survive": "survival",
}

_REASONS = {
    "incoming_uncancellable": "incoming attack cannot be canceled before its deadline",
    "incoming_counterable": "incoming attack can be canceled or returned before its deadline",
    "danger_threshold": "own board danger is at or above the survival threshold",
    "all_clear_entitlement": "an achieved or pending all-clear entitlement is active",
    "lethal_window": "available attack reaches the opponent's estimated lethal capacity",
    "immediate_main_fire": "the analyzed main chain can fire immediately",
    "opponent_short_threat": "the opponent has a short-horizon attack option",
    "pressure_window": "a safe short-horizon pressure attack is available",
    "safe_build": "no higher-priority tactical condition is active",
}


@dataclass(frozen=True)
class TacticSelection:
    """Deterministic arbitration result and its complete score breakdown."""

    tactic_id: str
    tactic_name: str
    tactic_version: str
    reason_code: str
    reason: str
    priority_band: int
    parameters: Mapping[str, Mapping[str, Any]]
    candidates: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tactic_id": self.tactic_id,
            "name": self.tactic_name,
            "version": self.tactic_version,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "priority_band": int(self.priority_band),
            "parameters": {
                section: dict(values) for section, values in self.parameters.items()
            },
        }


def select_tactic(
    registry: TacticRegistry,
    analyzer_input: AnalyzerInput,
    analyzer_diagnostics: AnalyzerDiagnostics,
    parameter_overrides: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> TacticSelection:
    """Score every registry tactic and select one by stable tactical bands."""

    tactic_payload = build_tactic_diagnostics(
        registry,
        analyzer_input,
        analyzer_diagnostics,
        parameter_overrides=parameter_overrides,
    )
    scored: list[dict[str, Any]] = []
    rank_keys: list[tuple[float, ...] | None] = []
    for registry_order, (tactic, candidate) in enumerate(
        zip(registry.tactics, tactic_payload["candidates"])
    ):
        activation = _activation_for_tactic(
            tactic,
            candidate["parameters"],
            analyzer_input,
            analyzer_diagnostics,
        )
        active = bool(candidate["eligible"] and activation is not None)
        rank_key: tuple[float, ...] | None = None
        if active and activation is not None:
            priority_band, attack, deadline, danger, reason_code, signals = activation
            rank_key = (
                float(7 - priority_band),
                float(attack),
                float(-deadline),
                float(danger),
                float(-registry_order),
            )
            scoring = {
                "active": True,
                "priority_band": int(priority_band),
                "priority_score": int(7 - priority_band),
                "attack": int(attack),
                "deadline": int(deadline),
                "danger": float(danger),
                "registry_order": int(registry_order),
                "rank_key": list(rank_key),
                "reason_code": reason_code,
                "reason": _REASONS[reason_code],
                "signals": signals,
            }
        else:
            scoring = {
                "active": False,
                "priority_band": None,
                "priority_score": 0,
                "attack": 0,
                "deadline": 0,
                "danger": float(analyzer_diagnostics.own.danger),
                "registry_order": int(registry_order),
                "rank_key": None,
                "reason_code": "condition_not_active",
                "reason": "the tactic's manager activation condition is not active",
                "signals": {},
            }
        scored.append(
            {
                **candidate,
                "registry_eligible": bool(candidate["eligible"]),
                "scoring": scoring,
                "selected": False,
            }
        )
        rank_keys.append(rank_key)

    active_indices = [index for index, rank_key in enumerate(rank_keys) if rank_key is not None]
    if not active_indices:
        raise RuntimeError("tactic registry did not produce an active fallback tactic")
    selected_index = max(active_indices, key=lambda index: rank_keys[index])
    selected_candidate = scored[selected_index]
    selected_candidate["selected"] = True
    selected_scoring = selected_candidate["scoring"]
    return TacticSelection(
        tactic_id=str(selected_candidate["tactic_id"]),
        tactic_name=str(selected_candidate["name"]),
        tactic_version=str(selected_candidate["version"]),
        reason_code=str(selected_scoring["reason_code"]),
        reason=str(selected_scoring["reason"]),
        priority_band=int(selected_scoring["priority_band"]),
        parameters=selected_candidate["parameters"],
        candidates=tuple(scored),
    )


class V17AnalyzerManagerPolicy:
    """Connect Analyzer diagnostics, tactic arbitration, and existing workers."""

    def __init__(
        self,
        *,
        analyzer: StateAnalyzer | None = None,
        registry: TacticRegistry | None = None,
        profiles: tuple[WorkerProfile, ...] | None = None,
        parameter_overrides: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    ):
        self.analyzer = analyzer or StateAnalyzer()
        self.registry = registry or load_tactic_registry()
        self.profiles = profiles or default_worker_profiles()
        self.parameter_overrides = parameter_overrides
        self.orchestrator = StrategyOrchestrator(self.profiles)
        self._last_step_count = -1
        self.last_analyzer_input: AnalyzerInput | None = None
        self.last_analyzer_diagnostics: AnalyzerDiagnostics | None = None
        self.last_selection: TacticSelection | None = None
        self.last_planner_request: PlannerRequest | None = None
        self.last_proposal = None
        self.last_plan = None
        self._tactical_diagnostics: dict[str, Any] = {}

    def reset(self) -> None:
        self.orchestrator = StrategyOrchestrator(self.profiles)
        self._last_step_count = -1
        self.last_analyzer_input = None
        self.last_analyzer_diagnostics = None
        self.last_selection = None
        self.last_planner_request = None
        self.last_proposal = None
        self.last_plan = None
        self._tactical_diagnostics = {}

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        step_count = int(info.get("step_count", info.get("tick_count", 0)))
        if step_count <= self._last_step_count:
            self.reset()

        analyzer_input = AnalyzerInput.from_runtime_info(info)
        analyzer_diagnostics = self.analyzer.analyze(analyzer_input)
        selection = select_tactic(
            self.registry,
            analyzer_input,
            analyzer_diagnostics,
            self.parameter_overrides,
        )
        tactic = self.registry.tactic(selection.tactic_id)
        overrides = (
            None
            if self.parameter_overrides is None
            else self.parameter_overrides.get(selection.tactic_id)
        )
        planner_request = build_planner_request(
            tactic,
            analyzer_input,
            analyzer_diagnostics,
            parameter_overrides=overrides,
        )
        profile_id = profile_id_by_name(
            self.profiles,
            _TACTIC_TO_WORKER[selection.tactic_id],
        )
        proposal = self.orchestrator.propose(
            profile_id,
            observation,
            info,
            planner_request=planner_request,
        )
        objective = replace(proposal.objective, reason=selection.reason)
        proposal = replace(proposal, reason=selection.reason, objective=objective)
        plan = replace(self.orchestrator.last_plan, objective=objective)
        self.orchestrator.last_proposal = proposal
        self.orchestrator.last_plan = plan

        self._last_step_count = step_count
        self.last_analyzer_input = analyzer_input
        self.last_analyzer_diagnostics = analyzer_diagnostics
        self.last_selection = selection
        self.last_planner_request = planner_request
        self.last_proposal = proposal
        self.last_plan = plan
        self._tactical_diagnostics = self._build_diagnostics(
            analyzer_input,
            analyzer_diagnostics,
            selection,
            tactic,
            planner_request,
        )
        return int(proposal.action)

    @property
    def current_profile_name(self) -> str | None:
        return None if self.last_selection is None else self.last_selection.tactic_id

    @property
    def tactical_diagnostics(self) -> dict[str, Any]:
        return copy.deepcopy(self._tactical_diagnostics)

    @property
    def plan_diagnostics(self) -> dict[str, Any]:
        return {} if self.last_plan is None else self.last_plan.to_dict()

    def _build_diagnostics(
        self,
        analyzer_input: AnalyzerInput,
        analyzer_diagnostics: AnalyzerDiagnostics,
        selection: TacticSelection,
        tactic: TacticSpec,
        planner_request: PlannerRequest,
    ) -> dict[str, Any]:
        proposal = self.last_proposal
        plan = self.last_plan
        selected = selection.to_dict()
        selected["worker_profile"] = proposal.profile_name
        selected["worker_strategy"] = proposal.strategy
        worker_result = {
            "action": int(proposal.action),
            "predicted_chain_count": int(proposal.predicted_chain_count),
            "predicted_score": int(proposal.predicted_score),
            "predicted_attack": int(proposal.predicted_attack),
            "danger": float(proposal.danger),
            "expanded_nodes": int(proposal.expanded_nodes),
            "candidate_value": float(proposal.candidate_value),
            "response_capacity": int(proposal.response_capacity),
            "incoming_coverage": float(proposal.incoming_coverage),
            "trigger_preserved": bool(proposal.trigger_preserved),
            "immediate_fire": bool(proposal.immediate_fire),
        }
        return {
            "schema_version": ANALYZER_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
            "model_metadata": {
                "policy_type": POLICY_TYPE,
                "model_family": MODEL_FAMILY,
                "model_version": MODEL_VERSION,
                "checkpoint_required": False,
                "lineage_node_id": LINEAGE_NODE_ID,
            },
            "analyzer": {
                "input": analyzer_input.to_dict(),
                "diagnostics": analyzer_diagnostics.to_dict(),
            },
            "tactic_registry": {
                "schema_version": self.registry.schema_version,
                "registry_version": self.registry.registry_version,
                "source_path": self.registry.source_path,
            },
            "tactic_candidates": [copy.deepcopy(item) for item in selection.candidates],
            "selected_tactic": selected,
            "planner_request": planner_request.to_dict(),
            "worker": {
                "profile_id": int(proposal.profile_id),
                "profile_name": proposal.profile_name,
                "strategy": proposal.strategy,
                "objective": proposal.objective_dict,
                "objective_result": proposal.objective_result_dict,
                "build_potential": proposal.build_potential_dict,
                "result": worker_result,
            },
            "plan": {} if plan is None else plan.to_dict(),
            "lineage": {"node_id": LINEAGE_NODE_ID},
            # Compatibility fields consumed by existing arena/UI diagnostic readers.
            "incoming_attack": int(proposal.incoming_attack),
            "target_attack": int(proposal.target_attack),
            "deadline": int(proposal.deadline),
            "reason": selection.reason,
            "reason_code": selection.reason_code,
            "objective": proposal.objective_dict,
            "objective_result": proposal.objective_result_dict,
            "profile_name": proposal.profile_name,
            "strategy": proposal.strategy,
            "plan_id": "" if plan is None else plan.plan_id,
            "plan_update_reason": "" if plan is None else plan.update_reason,
        }


def _activation_for_tactic(
    tactic: TacticSpec,
    parameters: Mapping[str, Mapping[str, Any]],
    analyzer_input: AnalyzerInput,
    diagnostics: AnalyzerDiagnostics,
) -> tuple[int, int, int, float, str, dict[str, Any]] | None:
    tactic_id = tactic.identity.tactic_id
    incoming = diagnostics.incoming
    own = diagnostics.own
    opponent = diagnostics.opponent
    own_short = int(own.forecast.short_attack)
    own_deadline = max(1, int(own.forecast.turns_to_best or 1))
    common_signals = {
        "incoming_attack": int(incoming.amount),
        "max_return_by_deadline": int(incoming.max_return_by_deadline),
        "can_cancel": bool(incoming.can_cancel),
        "own_danger": float(own.danger),
        "own_short_attack": own_short,
        "opponent_short_attack": int(opponent.forecast.short_attack),
    }

    if tactic_id == "survive":
        if incoming.amount > 0 and not incoming.can_cancel:
            return (
                1,
                int(incoming.amount),
                max(0, int(incoming.deadline)),
                float(own.danger),
                "incoming_uncancellable",
                common_signals,
            )
        if own.danger >= DANGER_THRESHOLD:
            return (
                3,
                own_short,
                1,
                float(own.danger),
                "danger_threshold",
                common_signals,
            )
        return None

    if tactic_id == "counter_or_return":
        if incoming.amount > 0 and incoming.can_cancel:
            return (
                2,
                int(incoming.max_return_by_deadline),
                max(0, int(incoming.deadline)),
                float(own.danger),
                "incoming_counterable",
                common_signals,
            )
        return None

    if tactic_id == "all_clear":
        entitlement = bool(
            analyzer_input.own.all_clear_achieved
            or analyzer_input.own.all_clear_bonus_pending
        )
        if entitlement:
            return (
                4,
                own_short,
                own_deadline,
                float(own.danger),
                "all_clear_entitlement",
                {
                    **common_signals,
                    "board_empty": bool(own.board_empty),
                    "all_clear_achieved": bool(analyzer_input.own.all_clear_achieved),
                    "all_clear_bonus_pending": bool(
                        analyzer_input.own.all_clear_bonus_pending
                    ),
                    "all_clear_bonus_consumed": bool(
                        analyzer_input.own.all_clear_bonus_consumed
                    ),
                },
            )
        return None

    if tactic_id == "lethal_attack":
        lethal_target = _opponent_lethal_target(analyzer_input)
        if own_short > 0 and own_short >= lethal_target:
            deadline = int(parameters["objective"].get("deadline_turns", own_deadline))
            return (
                5,
                own_short,
                max(1, min(deadline, own_deadline)),
                float(opponent.danger),
                "lethal_window",
                {**common_signals, "lethal_target": lethal_target},
            )
        return None

    if tactic_id == "fire_main":
        main = own.forecast.main_chain
        target_chain = int(parameters["objective"].get("target_chain", 1))
        if (
            main is not None
            and main.is_immediate
            and main.attack > 0
            and main.chain_count >= target_chain
        ):
            return (
                5,
                int(main.attack),
                max(1, int(main.turns)),
                float(own.danger),
                "immediate_main_fire",
                {
                    **common_signals,
                    "main_chain_count": int(main.chain_count),
                    "main_attack": int(main.attack),
                    "target_chain": target_chain,
                },
            )
        return None

    if tactic_id == "prepare_response":
        threat = int(opponent.forecast.short_attack)
        if threat > 0:
            deadline = max(1, int(opponent.forecast.turns_to_best or 1))
            return (
                5,
                threat,
                deadline,
                float(max(own.danger, opponent.vulnerability)),
                "opponent_short_threat",
                common_signals,
            )
        return None

    if tactic_id == "pressure":
        target_attack = int(parameters["objective"].get("target_attack", 1))
        danger_tolerance = float(parameters["constraints"].get("danger_tolerance", 1.0))
        if own_short >= target_attack and own.danger <= danger_tolerance:
            deadline = int(parameters["objective"].get("deadline_turns", own_deadline))
            return (
                5,
                target_attack,
                max(1, deadline),
                float(max(own.danger, opponent.vulnerability)),
                "pressure_window",
                {
                    **common_signals,
                    "target_attack": target_attack,
                    "danger_tolerance": danger_tolerance,
                },
            )
        return None

    if tactic_id == "build_main":
        deadline = int(parameters["planner"].get("beam_depth", own_deadline))
        return (
            6,
            own_short,
            max(1, deadline),
            float(own.danger),
            "safe_build",
            common_signals,
        )

    return None


def _opponent_lethal_target(analyzer_input: AnalyzerInput) -> int:
    center_height = 0
    center_x = min(2, GRID_WIDTH - 1)
    board = analyzer_input.opponent.board
    for y in range(min(VISIBLE_HEIGHT, len(board)) - 1, -1, -1):
        if board[y][center_x] != "EMPTY":
            center_height = y + 1
            break
    rows_to_choke = max(0, VISIBLE_HEIGHT - center_height)
    pending = sum(packet.amount for packet in analyzer_input.opponent.incoming)
    capacity = max(0, rows_to_choke * GRID_WIDTH - pending)
    return max(1, min(30, capacity + 1))
