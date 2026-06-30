"""Inference policies for selecting and executing strategy workers."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import torch
except ImportError:  # pragma: no cover - dependency guard
    np = None
    torch = None

from agents.networks import PuyoActorCritic
from agents.strategy_workers import (
    SearchControl,
    StrategyOrchestrator,
    TacticalOption,
    WorkerProfile,
    baseline_search_controls,
    build_tactical_context,
    default_tactical_options,
    default_worker_profiles,
    profile_id_by_name,
)
from puyo_env.manager_env import (
    ManagerState,
    build_manager_observation,
    manager_feature_dim,
    manager_vector_dim,
    manager_vector_features,
)
from puyo_env.obs import NORMAL_COLOR_CHANNELS, VISIBLE_PAIR_COUNT


class StrategyManagerPolicy:
    """Load a manager checkpoint, select one profile, then run that worker."""

    def __init__(self, checkpoint_path: str | Path, device: str = "cpu", deterministic: bool = True):
        if torch is None or np is None:
            raise ImportError("StrategyManagerPolicy requires torch and numpy")
        self.device = torch.device(device)
        self.deterministic = deterministic
        checkpoint = torch.load(Path(checkpoint_path), map_location=self.device, weights_only=False)
        if checkpoint.get("policy_type") != "strategy_manager":
            raise ValueError("checkpoint is not a strategy manager checkpoint")
        self.profiles = tuple(WorkerProfile(**item) for item in checkpoint["worker_profiles"])
        self.search_controls = tuple(
            _search_control_from_metadata(item)
            for item in checkpoint.get("search_controls", [baseline_search_controls()[0].to_dict()])
        )
        self.strategy_space = checkpoint.get("strategy_space", "profile")
        self.tactical_options = tuple(
            _tactical_option_from_metadata(item)
            for item in checkpoint.get("tactical_options", [option.to_dict() for option in default_tactical_options()])
        )
        self.decision_count = int(
            checkpoint.get(
                "decision_count",
                len(self.tactical_options) if self.strategy_space == "option" else len(self.profiles),
            )
        )
        self.orchestrator = StrategyOrchestrator(self.profiles, self.tactical_options)
        board_shape = tuple(checkpoint["board_shape"])
        state_dict = checkpoint["model_state_dict"]
        vector_dim = int(
            checkpoint.get("vector_dim", manager_vector_dim(self.decision_count, len(self.search_controls)))
        )
        if "trunk.0.weight" in state_dict:
            board_channels, board_rows, board_cols = board_shape
            probe = PuyoActorCritic(
                board_shape=(board_channels, board_rows, board_cols),
                vector_dim=1,
                action_dim=1,
            )
            with torch.no_grad():
                cnn_out_dim = probe.cnn(
                    torch.zeros(1, board_channels, board_rows, board_cols)
                ).shape[1]
            vector_dim = int(state_dict["trunk.0.weight"].shape[1] - cnn_out_dim)
        self.manager_feature_dim = int(
            checkpoint.get(
                "manager_feature_dim",
                manager_feature_dim(self.decision_count, len(self.search_controls)),
            )
        )
        pair_features = VISIBLE_PAIR_COUNT * 2 * len(NORMAL_COLOR_CHANNELS)
        self.manager_feature_dim = max(0, vector_dim - pair_features)
        self.action_dim = int(checkpoint.get("action_dim", len(self.profiles) * len(self.search_controls)))
        if "actor.weight" in state_dict:
            self.action_dim = int(state_dict["actor.weight"].shape[0])
        self.agent = PuyoActorCritic(
            board_shape=board_shape,
            vector_dim=vector_dim,
            action_dim=self.action_dim,
        ).to(self.device)
        self.agent.load_state_dict(state_dict)
        self.agent.eval()
        self.manager_state = ManagerState(
            profile_counts=[0] * self.decision_count,
            option_counts=[0] * len(self.tactical_options),
            search_control_counts=[0] * len(self.search_controls),
        )
        self.last_proposal = None
        self.last_plan = None
        self.last_profile_id = -1
        self._last_step_count = -1

    def reset(self) -> None:
        self.manager_state = ManagerState(
            profile_counts=[0] * self.decision_count,
            option_counts=[0] * len(self.tactical_options),
            search_control_counts=[0] * len(self.search_controls),
        )
        self.last_proposal = None
        self.last_plan = None
        self.last_profile_id = -1
        self._last_step_count = -1

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        step_count = int(info.get("step_count", 0))
        if step_count <= self._last_step_count:
            self.reset()
        manager_observation = build_manager_observation(
            observation,
            info,
            self.manager_state,
            self.decision_count,
            len(self.search_controls),
            self.manager_feature_dim,
        )
        board = torch.as_tensor(manager_observation["board"][None, ...], dtype=torch.float32, device=self.device)
        vector = torch.as_tensor(manager_vector_features(manager_observation)[None, ...], dtype=torch.float32, device=self.device)
        mask = torch.ones((1, self.action_dim), dtype=torch.bool, device=self.device)
        with torch.no_grad():
            logits, _ = self.agent.forward(board, vector, action_mask=mask)
            if self.deterministic:
                manager_action = int(torch.argmax(logits, dim=1).item())
            else:
                manager_action = int(torch.distributions.Categorical(logits=logits).sample().item())
        decision_id, control_id = self._decode_manager_action(manager_action)
        self._record_selection(decision_id, control_id)
        self.last_proposal = self.orchestrator.propose(
            decision_id if self.strategy_space == "profile" else 0,
            observation,
            info,
            self.search_controls[control_id],
            tactical_option_id=decision_id if self.strategy_space == "option" else None,
        )
        self.last_plan = self.orchestrator.last_plan
        self.manager_state.last_proposal = self.last_proposal
        self.manager_state.total_decision_seconds += self.last_proposal.elapsed_seconds
        self.manager_state.total_expanded_nodes += self.last_proposal.expanded_nodes
        self._last_step_count = step_count
        return self.last_proposal.action

    def _decode_manager_action(self, action: int) -> tuple[int, int]:
        action_id = int(action)
        return action_id // len(self.search_controls), action_id % len(self.search_controls)

    def _record_selection(self, profile_id: int, control_id: int) -> None:
        if (
            self.manager_state.last_profile_id >= 0
            and (
                profile_id != self.manager_state.last_profile_id
                or control_id != self.manager_state.last_search_control_id
            )
        ):
            self.manager_state.switch_count += 1
            self.manager_state.profile_duration = 1
        elif (
            profile_id == self.manager_state.last_profile_id
            and control_id == self.manager_state.last_search_control_id
        ):
            self.manager_state.profile_duration += 1
        else:
            self.manager_state.profile_duration = 1
        self.manager_state.last_profile_id = profile_id
        self.manager_state.last_search_control_id = control_id
        self.manager_state.profile_counts[profile_id] += 1
        if self.strategy_space == "option":
            self.manager_state.option_counts[profile_id] += 1
        self.manager_state.search_control_counts[control_id] += 1
        self.last_profile_id = profile_id

    @property
    def current_profile_name(self) -> str | None:
        if self.last_profile_id < 0:
            return None
        if self.strategy_space == "option":
            return self.tactical_options[self.last_profile_id].name
        return self.profiles[self.last_profile_id].name

    @property
    def tactical_diagnostics(self) -> dict[str, Any]:
        proposal = self.last_proposal
        if proposal is None:
            return {}
        return {
            "incoming_attack": proposal.incoming_attack,
            "target_attack": proposal.target_attack,
            "deadline": proposal.deadline,
            "reason": proposal.reason,
            "objective": proposal.objective_dict,
            "objective_result": proposal.objective_result_dict,
            "search_control": proposal.search_control_dict,
            "tactical_option": proposal.tactical_option_dict,
            "plan": self.plan_diagnostics,
            "plan_id": "" if self.last_plan is None else self.last_plan.plan_id,
            "plan_update_reason": "" if self.last_plan is None else self.last_plan.update_reason,
        }

    @property
    def plan_diagnostics(self) -> dict[str, Any]:
        return {} if self.last_plan is None else self.last_plan.to_dict()


class RuleBasedManagerPolicy:
    """Interpretable baseline router using the same worker profiles."""

    def __init__(self, profiles: tuple[WorkerProfile, ...] | None = None):
        self.profiles = profiles or default_worker_profiles()
        self.orchestrator = StrategyOrchestrator(self.profiles)
        self.last_proposal = None
        self.last_plan = None
        self.last_profile_id = -1
        self.last_tactical_context = None

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        tactical = build_tactical_context(info)
        self.last_tactical_context = tactical
        strategy = tactical.recommended_strategy
        aliases = {
            "build_large": ("build_large", "large_chain"),
            "build_budget": ("build_budget", "quick_attack"),
            "punish": ("punish", "fire_max", "fire"),
            "counter": ("counter", "fire_max", "fire"),
            "fire_max": ("fire_max", "fire"),
            "survival": ("survival",),
        }
        try:
            profile_id = profile_id_by_name(self.profiles, *aliases[strategy])
        except KeyError:
            profile_id = 0
        self.last_profile_id = profile_id
        self.last_proposal = self.orchestrator.propose(profile_id, observation, info)
        self.last_plan = self.orchestrator.last_plan
        return self.last_proposal.action

    @property
    def current_profile_name(self) -> str | None:
        return None if self.last_profile_id < 0 else self.profiles[self.last_profile_id].name

    @property
    def tactical_diagnostics(self) -> dict[str, Any]:
        proposal = self.last_proposal
        if proposal is None:
            return {}
        return {
            "incoming_attack": proposal.incoming_attack,
            "target_attack": proposal.target_attack,
            "deadline": proposal.deadline,
            "reason": proposal.reason,
            "objective": proposal.objective_dict,
            "objective_result": proposal.objective_result_dict,
            "plan": self.plan_diagnostics,
            "plan_id": "" if self.last_plan is None else self.last_plan.plan_id,
            "plan_update_reason": "" if self.last_plan is None else self.last_plan.update_reason,
        }

    @property
    def plan_diagnostics(self) -> dict[str, Any]:
        return {} if self.last_plan is None else self.last_plan.to_dict()


def _search_control_from_metadata(item: dict[str, Any]) -> SearchControl:
    allowed = {
        "control_id",
        "name",
        "mode",
        "depth_scale",
        "width_scale",
        "scenarios",
        "chain_weight_scale",
        "score_weight_scale",
        "premature_chain_penalty_scale",
        "fire_threshold",
        "danger_tolerance_delta",
        "latency_budget_ms",
        "cost_penalty",
        "parameter_vector",
    }
    values = {key: value for key, value in item.items() if key in allowed}
    if "parameter_vector" in values:
        values["parameter_vector"] = tuple(values["parameter_vector"])
    return SearchControl(**values)


def _tactical_option_from_metadata(item: dict[str, Any]) -> TacticalOption:
    allowed = {
        "option_id",
        "name",
        "base_profile_name",
        "strategy",
        "target_attack_delta",
        "target_chain_delta",
        "deadline_delta",
        "danger_tolerance_delta",
        "fire_threshold_scale",
        "termination",
        "latent_vector",
        "fallback_profile_name",
    }
    values = {key: value for key, value in item.items() if key in allowed}
    if "latent_vector" in values:
        values["latent_vector"] = tuple(values["latent_vector"])
    return TacticalOption(**values)


def manager_checkpoint_metadata(
    profiles: tuple[WorkerProfile, ...] | None = None,
    search_controls: tuple[SearchControl, ...] | None = None,
    *,
    tactical_options: tuple[TacticalOption, ...] | None = None,
    strategy_space: str = "profile",
    decision_count: int | None = None,
) -> dict[str, Any]:
    selected = profiles or default_worker_profiles()
    selected_controls = search_controls or baseline_search_controls()
    selected_options = tactical_options or default_tactical_options()
    resolved_decision_count = (
        int(decision_count)
        if decision_count is not None
        else len(selected_options) if strategy_space == "option" else len(selected)
    )
    return {
        "policy_type": "strategy_manager",
        "worker_profiles": [asdict(profile) for profile in selected],
        "search_controls": [control.to_dict() for control in selected_controls],
        "tactical_options": [option.to_dict() for option in selected_options],
        "strategy_space": strategy_space,
        "decision_count": resolved_decision_count,
        "action_dim": resolved_decision_count * len(selected_controls),
        "vector_dim": manager_vector_dim(resolved_decision_count, len(selected_controls)),
        "manager_feature_dim": manager_feature_dim(resolved_decision_count, len(selected_controls)),
    }
