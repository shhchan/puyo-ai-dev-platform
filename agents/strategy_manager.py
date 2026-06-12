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
    StrategyOrchestrator,
    WorkerProfile,
    build_tactical_context,
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


class StrategyManagerPolicy:
    """Load a manager checkpoint, select one profile, then run that worker."""

    def __init__(self, checkpoint_path: str | Path, device: str = "cpu", deterministic: bool = True):
        if torch is None or np is None:
            raise ImportError("StrategyManagerPolicy requires torch and numpy")
        self.device = torch.device(device)
        self.deterministic = deterministic
        checkpoint = torch.load(Path(checkpoint_path), map_location=self.device)
        if checkpoint.get("policy_type") != "strategy_manager":
            raise ValueError("checkpoint is not a strategy manager checkpoint")
        self.profiles = tuple(WorkerProfile(**item) for item in checkpoint["worker_profiles"])
        self.orchestrator = StrategyOrchestrator(self.profiles)
        board_shape = tuple(checkpoint["board_shape"])
        vector_dim = int(checkpoint.get("vector_dim", manager_vector_dim(len(self.profiles))))
        self.manager_feature_dim = int(
            checkpoint.get("manager_feature_dim", manager_feature_dim(len(self.profiles)))
        )
        self.agent = PuyoActorCritic(
            board_shape=board_shape,
            vector_dim=vector_dim,
            action_dim=len(self.profiles),
        ).to(self.device)
        self.agent.load_state_dict(checkpoint["model_state_dict"])
        self.agent.eval()
        self.manager_state = ManagerState(profile_counts=[0] * len(self.profiles))
        self.last_proposal = None
        self.last_profile_id = -1
        self._last_step_count = -1

    def reset(self) -> None:
        self.manager_state = ManagerState(profile_counts=[0] * len(self.profiles))
        self.last_proposal = None
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
            len(self.profiles),
            self.manager_feature_dim,
        )
        board = torch.as_tensor(manager_observation["board"][None, ...], dtype=torch.float32, device=self.device)
        vector = torch.as_tensor(manager_vector_features(manager_observation)[None, ...], dtype=torch.float32, device=self.device)
        mask = torch.ones((1, len(self.profiles)), dtype=torch.bool, device=self.device)
        with torch.no_grad():
            logits, _ = self.agent.forward(board, vector, action_mask=mask)
            if self.deterministic:
                profile_id = int(torch.argmax(logits, dim=1).item())
            else:
                profile_id = int(torch.distributions.Categorical(logits=logits).sample().item())
        self._record_selection(profile_id)
        self.last_proposal = self.orchestrator.propose(profile_id, observation, info)
        self.manager_state.last_proposal = self.last_proposal
        self.manager_state.total_decision_seconds += self.last_proposal.elapsed_seconds
        self.manager_state.total_expanded_nodes += self.last_proposal.expanded_nodes
        self._last_step_count = step_count
        return self.last_proposal.action

    def _record_selection(self, profile_id: int) -> None:
        if self.manager_state.last_profile_id >= 0 and profile_id != self.manager_state.last_profile_id:
            self.manager_state.switch_count += 1
            self.manager_state.profile_duration = 1
        elif profile_id == self.manager_state.last_profile_id:
            self.manager_state.profile_duration += 1
        else:
            self.manager_state.profile_duration = 1
        self.manager_state.last_profile_id = profile_id
        self.manager_state.profile_counts[profile_id] += 1
        self.last_profile_id = profile_id

    @property
    def current_profile_name(self) -> str | None:
        if self.last_profile_id < 0:
            return None
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
        }


class RuleBasedManagerPolicy:
    """Interpretable baseline router using the same worker profiles."""

    def __init__(self, profiles: tuple[WorkerProfile, ...] | None = None):
        self.profiles = profiles or default_worker_profiles()
        self.orchestrator = StrategyOrchestrator(self.profiles)
        self.last_proposal = None
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
        }


def manager_checkpoint_metadata(profiles: tuple[WorkerProfile, ...] | None = None) -> dict[str, Any]:
    selected = profiles or default_worker_profiles()
    return {
        "policy_type": "strategy_manager",
        "worker_profiles": [asdict(profile) for profile in selected],
        "vector_dim": manager_vector_dim(len(selected)),
        "manager_feature_dim": manager_feature_dim(len(selected)),
    }
