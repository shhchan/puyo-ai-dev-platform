"""Gym wrapper where actions select fixed search-worker profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

try:
    import gymnasium as gym
    import numpy as np
    from gymnasium import spaces
except ImportError:  # pragma: no cover - dependency guard
    gym = None
    np = None
    spaces = None

from agents.strategy_workers import (
    SearchProposal,
    StrategyOrchestrator,
    WorkerProfile,
    board_danger,
    default_worker_profiles,
    estimate_immediate_threat,
)
from puyo_env.obs import NORMAL_COLOR_CHANNELS, VISIBLE_PAIR_COUNT
from puyo_env.versus_env import VersusPuyoEnv, VersusRewardConfig

if TYPE_CHECKING:
    from selfplay.policies import Policy


MANAGER_FEATURE_DIM = 26
MANAGER_VECTOR_DIM = VISIBLE_PAIR_COUNT * 2 * len(NORMAL_COLOR_CHANNELS) + MANAGER_FEATURE_DIM
_BaseEnv = gym.Env if gym is not None else object


@dataclass
class ManagerState:
    last_profile_id: int = -1
    profile_duration: int = 0
    switch_count: int = 0
    last_proposal: SearchProposal | None = None
    profile_counts: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    total_decision_seconds: float = 0.0
    total_expanded_nodes: int = 0


def encode_manager_features(info: dict[str, Any], state: ManagerState, profile_count: int = 4):
    """Encode cheap tactical and strategy-history features."""

    if np is None:
        raise ImportError("manager features require numpy")
    simulator = info.get("simulator")
    opponent_simulator = info.get("opponent_simulator")
    own_chain, own_attack = estimate_immediate_threat(simulator)
    opponent_chain, opponent_attack = estimate_immediate_threat(opponent_simulator)
    one_hot = [0.0] * profile_count
    if 0 <= state.last_profile_id < profile_count:
        one_hot[state.last_profile_id] = 1.0
    proposal = state.last_proposal
    features = [
        min(float(info.get("pending_ojama", 0)) / 30.0, 1.0),
        min(float(info.get("opponent_pending_ojama", 0)) / 30.0, 1.0),
        min(float(info.get("score", 0)) / 100_000.0, 1.0),
        min(float(info.get("opponent_score", 0)) / 100_000.0, 1.0),
        min(float(info.get("sent_ojama_total", 0)) / 60.0, 1.0),
        min(float(info.get("opponent_sent_ojama_total", 0)) / 60.0, 1.0),
        min(float(info.get("received_ojama_total", 0)) / 60.0, 1.0),
        min(float(info.get("opponent_received_ojama_total", 0)) / 60.0, 1.0),
        board_danger(simulator.game) if simulator is not None else 1.0,
        board_danger(opponent_simulator.game) if opponent_simulator is not None else 1.0,
        min(float(own_chain) / 10.0, 1.0),
        min(float(own_attack) / 30.0, 1.0),
        min(float(opponent_chain) / 10.0, 1.0),
        min(float(opponent_attack) / 30.0, 1.0),
        *one_hot,
        min(float(state.profile_duration) / 20.0, 1.0),
        min(float(state.switch_count) / 50.0, 1.0),
        min(float(info.get("step_count", 0)) / max(1.0, float(info.get("max_steps", 1))), 1.0),
        min(float(proposal.predicted_chain_count) / 10.0, 1.0) if proposal else 0.0,
        min(float(proposal.predicted_attack) / 30.0, 1.0) if proposal else 0.0,
        float(proposal.danger) if proposal else 0.0,
        min(float(proposal.elapsed_seconds) / 2.0, 1.0) if proposal else 0.0,
        min(float(proposal.expanded_nodes) / 10_000.0, 1.0) if proposal else 0.0,
    ]
    if len(features) != MANAGER_FEATURE_DIM:
        raise RuntimeError(f"manager feature size changed: {len(features)}")
    return np.asarray(features, dtype=np.float32)


def build_manager_observation(observation: dict[str, Any], info: dict[str, Any], state: ManagerState):
    return {
        "board": observation["board"],
        "next_pairs": observation["next_pairs"],
        "manager_features": encode_manager_features(info, state),
    }


def manager_vector_features(observation: dict[str, Any]):
    return np.concatenate(
        [observation["next_pairs"].reshape(-1), observation["manager_features"]]
    ).astype(np.float32, copy=False)


class ManagerSelfPlayEnv(_BaseEnv):
    """Learner chooses a profile while the selected worker places the pair."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        seed: int | None = None,
        max_steps: int = 500,
        opponent_policy: Policy | None = None,
        reward_config: VersusRewardConfig | None = None,
        profiles: tuple[WorkerProfile, ...] | None = None,
        switch_penalty: float = 0.02,
        decision_time_penalty: float = 0.001,
    ):
        if gym is None or spaces is None or np is None:
            raise ImportError("ManagerSelfPlayEnv requires gymnasium and numpy")
        super().__init__()
        from selfplay.policies import RandomPolicy

        self.versus_env = VersusPuyoEnv(seed=seed, max_steps=max_steps, reward_config=reward_config)
        self.opponent_policy = opponent_policy or RandomPolicy(seed=seed)
        self.orchestrator = StrategyOrchestrator(profiles or default_worker_profiles())
        self.switch_penalty = float(switch_penalty)
        self.decision_time_penalty = float(decision_time_penalty)
        base_space = self.versus_env.observation_space("player_0")
        self.action_space = spaces.Discrete(len(self.orchestrator.profiles))
        self.observation_space = spaces.Dict(
            {
                "board": base_space["board"],
                "next_pairs": base_space["next_pairs"],
                "manager_features": spaces.Box(0.0, 1.0, shape=(MANAGER_FEATURE_DIM,), dtype=np.float32),
            }
        )
        self.manager_state = ManagerState(profile_counts=[0] * len(self.orchestrator.profiles))
        self._episode_return = 0.0
        self._last_observations = None
        self._last_infos = None

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed)
        observations, infos = self.versus_env.reset(seed=seed, options=options)
        self.manager_state = ManagerState(profile_counts=[0] * len(self.orchestrator.profiles))
        self._episode_return = 0.0
        self._last_observations = observations
        self._last_infos = infos
        return build_manager_observation(observations["player_0"], infos["player_0"], self.manager_state), self._info(infos["player_0"])

    def _info(self, info: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(info)
        enriched["action_mask"] = np.ones(len(self.orchestrator.profiles), dtype=np.bool_)
        enriched["manager_profile_id"] = self.manager_state.last_profile_id
        enriched["manager_switch_count"] = self.manager_state.switch_count
        enriched["manager_profile_counts"] = tuple(self.manager_state.profile_counts)
        enriched["search_proposal"] = self.manager_state.last_proposal
        return enriched

    def step(self, action: int):
        if self._last_observations is None or self._last_infos is None:
            raise RuntimeError("reset() must be called before step()")
        profile_id = int(action)
        if not self.action_space.contains(profile_id):
            raise ValueError(f"invalid manager action: {profile_id}")

        switched = self.manager_state.last_profile_id >= 0 and profile_id != self.manager_state.last_profile_id
        if switched:
            self.manager_state.switch_count += 1
            self.manager_state.profile_duration = 1
        elif profile_id == self.manager_state.last_profile_id:
            self.manager_state.profile_duration += 1
        else:
            self.manager_state.profile_duration = 1
        self.manager_state.last_profile_id = profile_id
        self.manager_state.profile_counts[profile_id] += 1

        proposal = self.orchestrator.propose(
            profile_id,
            self._last_observations["player_0"],
            self._last_infos["player_0"],
        )
        self.manager_state.last_proposal = proposal
        self.manager_state.total_decision_seconds += proposal.elapsed_seconds
        self.manager_state.total_expanded_nodes += proposal.expanded_nodes
        opponent_action = self.opponent_policy.select_action(
            self._last_observations["player_1"], self._last_infos["player_1"]
        )
        observations, rewards, terminations, truncations, infos = self.versus_env.step(
            {"player_0": proposal.action, "player_1": int(opponent_action)}
        )
        self._last_observations = observations
        self._last_infos = infos
        reward = float(rewards["player_0"])
        if switched:
            reward -= self.switch_penalty
        reward -= self.decision_time_penalty * proposal.elapsed_seconds
        self._episode_return += reward
        done = terminations["player_0"] or truncations["player_0"]
        info = self._info(infos["player_0"])
        info.update(
            {
                "worker_action": proposal.action,
                "opponent_action": int(opponent_action),
                "strategy_switched": switched,
            }
        )
        if done:
            episode = dict(info.get("episode", {}))
            decisions = max(1, sum(self.manager_state.profile_counts))
            episode.update(
                {
                    "r": self._episode_return,
                    "switches": self.manager_state.switch_count,
                    "profile_counts": tuple(self.manager_state.profile_counts),
                    "mean_decision_ms": self.manager_state.total_decision_seconds * 1000.0 / decisions,
                    "mean_expanded_nodes": self.manager_state.total_expanded_nodes / decisions,
                }
            )
            info["manager_episode"] = episode
        manager_observation = build_manager_observation(observations["player_0"], infos["player_0"], self.manager_state)
        return manager_observation, reward, terminations["player_0"], truncations["player_0"], info

    def close(self):
        self.versus_env.close()
