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
    SearchControl,
    SearchProposal,
    StrategyOrchestrator,
    TacticalContext,
    WorkerProfile,
    board_danger,
    build_tactical_context,
    default_search_controls,
    default_worker_profiles,
    estimate_immediate_threat,
)
from puyo_env.obs import NORMAL_COLOR_CHANNELS, VISIBLE_PAIR_COUNT
from puyo_env.versus_env import VersusPuyoEnv, VersusRewardConfig

if TYPE_CHECKING:
    from selfplay.policies import Policy


MANAGER_BASE_FEATURE_DIM = 30
DEFAULT_MANAGER_PROFILE_COUNT = len(default_worker_profiles())
DEFAULT_MANAGER_SEARCH_CONTROL_COUNT = len(default_search_controls())


def manager_feature_dim(
    profile_count: int,
    search_control_count: int = DEFAULT_MANAGER_SEARCH_CONTROL_COUNT,
) -> int:
    return MANAGER_BASE_FEATURE_DIM + int(profile_count) + int(search_control_count)


def manager_vector_dim(
    profile_count: int,
    search_control_count: int = DEFAULT_MANAGER_SEARCH_CONTROL_COUNT,
) -> int:
    pair_features = VISIBLE_PAIR_COUNT * 2 * len(NORMAL_COLOR_CHANNELS)
    return pair_features + manager_feature_dim(profile_count, search_control_count)


MANAGER_FEATURE_DIM = manager_feature_dim(DEFAULT_MANAGER_PROFILE_COUNT, DEFAULT_MANAGER_SEARCH_CONTROL_COUNT)
MANAGER_VECTOR_DIM = manager_vector_dim(DEFAULT_MANAGER_PROFILE_COUNT, DEFAULT_MANAGER_SEARCH_CONTROL_COUNT)
_BaseEnv = gym.Env if gym is not None else object


@dataclass
class ManagerState:
    last_profile_id: int = -1
    last_search_control_id: int = -1
    profile_duration: int = 0
    switch_count: int = 0
    last_proposal: SearchProposal | None = None
    profile_counts: list[int] = field(default_factory=lambda: [0] * DEFAULT_MANAGER_PROFILE_COUNT)
    search_control_counts: list[int] = field(
        default_factory=lambda: [0] * DEFAULT_MANAGER_SEARCH_CONTROL_COUNT
    )
    total_decision_seconds: float = 0.0
    total_expanded_nodes: int = 0
    search_cost_penalty_total: float = 0.0
    latency_overruns: int = 0
    tactic_counts: dict[str, int] = field(default_factory=dict)
    search_control_mode_counts: dict[str, int] = field(default_factory=dict)
    search_control_by_tactic: dict[str, int] = field(default_factory=dict)
    objective_counts: dict[str, int] = field(default_factory=dict)
    objective_miss_reasons: dict[str, int] = field(default_factory=dict)
    objective_successes: int = 0
    missed_lethal: int = 0
    failed_counter: int = 0
    tactical_successes: int = 0


def _bounded(value: float, scale: float) -> float:
    return min(max(float(value) / max(scale, 1e-9), 0.0), 1.0)


def _centered(value: float, scale: float) -> float:
    return min(max(0.5 + float(value) / max(scale, 1e-9), 0.0), 1.0)


def encode_manager_features(
    info: dict[str, Any],
    state: ManagerState,
    profile_count: int = DEFAULT_MANAGER_PROFILE_COUNT,
    search_control_count: int = DEFAULT_MANAGER_SEARCH_CONTROL_COUNT,
    feature_dim: int | None = None,
):
    """Encode tactical forecasts and strategy history without running every worker."""

    if np is None:
        raise ImportError("manager features require numpy")
    if feature_dim == 22 + profile_count:
        return _encode_legacy_manager_features(info, state, profile_count)
    tactical = info.get("tactical_context")
    if not isinstance(tactical, TacticalContext):
        tactical = build_tactical_context(info)
        info["tactical_context"] = tactical
    one_hot = [0.0] * profile_count
    if 0 <= state.last_profile_id < profile_count:
        one_hot[state.last_profile_id] = 1.0
    control_one_hot = [0.0] * search_control_count
    if 0 <= state.last_search_control_id < search_control_count:
        control_one_hot[state.last_search_control_id] = 1.0
    proposal = state.last_proposal
    features = [
        _bounded(tactical.incoming_attack, 30.0),
        _bounded(info.get("opponent_pending_ojama", 0), 30.0),
        _bounded(tactical.incoming_deadline, 5.0),
        _bounded(info.get("opponent_incoming_turns", 0), 5.0),
        _bounded(info.get("score", 0), 100_000.0),
        _bounded(info.get("opponent_score", 0), 100_000.0),
        tactical.own_danger,
        tactical.opponent_danger,
        _bounded(tactical.own_forecast.immediate_chain, 10.0),
        _bounded(tactical.own_forecast.immediate_attack, 30.0),
        _bounded(tactical.own_forecast.short_attack, 30.0),
        _bounded(tactical.own_forecast.medium_attack, 30.0),
        _bounded(tactical.opponent_forecast.immediate_chain, 10.0),
        _bounded(tactical.opponent_forecast.immediate_attack, 30.0),
        _bounded(tactical.opponent_forecast.short_attack, 30.0),
        _bounded(tactical.lethal_target, 30.0),
        _centered(tactical.lethal_margin, 60.0),
        _bounded(tactical.counter_target, 30.0),
        _centered(tactical.counter_deficit, 60.0),
        _bounded(tactical.max_return_by_deadline, 30.0),
        _bounded(tactical.build_potential, 30.0),
        tactical.build_safety,
        *one_hot,
        *control_one_hot,
        _bounded(state.profile_duration, 20.0),
        _bounded(state.switch_count, 50.0),
        _bounded(info.get("step_count", 0), max(1.0, float(info.get("max_steps", 1)))),
        _bounded(proposal.predicted_chain_count if proposal else 0, 10.0),
        _bounded(proposal.predicted_attack if proposal else 0, 30.0),
        float(proposal.danger) if proposal else 0.0,
        _bounded(proposal.elapsed_seconds if proposal else 0.0, 2.0),
        _bounded(proposal.expanded_nodes if proposal else 0, 10_000.0),
    ]
    expected = int(feature_dim) if feature_dim is not None else manager_feature_dim(
        profile_count,
        search_control_count,
    )
    if len(features) < expected:
        features.extend([0.0] * (expected - len(features)))
    elif len(features) > expected and feature_dim is not None:
        features = features[:expected]
    if len(features) != expected:
        raise RuntimeError(f"manager feature size changed: {len(features)} != {expected}")
    return np.asarray(features, dtype=np.float32)


def _encode_legacy_manager_features(
    info: dict[str, Any],
    state: ManagerState,
    profile_count: int,
):
    simulator = info.get("simulator")
    opponent_simulator = info.get("opponent_simulator")
    own_chain, own_attack = estimate_immediate_threat(simulator)
    opponent_chain, opponent_attack = estimate_immediate_threat(opponent_simulator)
    one_hot = [0.0] * profile_count
    if 0 <= state.last_profile_id < profile_count:
        one_hot[state.last_profile_id] = 1.0
    proposal = state.last_proposal
    features = [
        _bounded(info.get("pending_ojama", 0), 30.0),
        _bounded(info.get("opponent_pending_ojama", 0), 30.0),
        _bounded(info.get("score", 0), 100_000.0),
        _bounded(info.get("opponent_score", 0), 100_000.0),
        _bounded(info.get("sent_ojama_total", 0), 60.0),
        _bounded(info.get("opponent_sent_ojama_total", 0), 60.0),
        _bounded(info.get("received_ojama_total", 0), 60.0),
        _bounded(info.get("opponent_received_ojama_total", 0), 60.0),
        board_danger(simulator.game) if simulator is not None else 1.0,
        board_danger(opponent_simulator.game) if opponent_simulator is not None else 1.0,
        _bounded(own_chain, 10.0),
        _bounded(own_attack, 30.0),
        _bounded(opponent_chain, 10.0),
        _bounded(opponent_attack, 30.0),
        *one_hot,
        _bounded(state.profile_duration, 20.0),
        _bounded(state.switch_count, 50.0),
        _bounded(info.get("step_count", 0), max(1.0, float(info.get("max_steps", 1)))),
        _bounded(proposal.predicted_chain_count if proposal else 0, 10.0),
        _bounded(proposal.predicted_attack if proposal else 0, 30.0),
        float(proposal.danger) if proposal else 0.0,
        _bounded(proposal.elapsed_seconds if proposal else 0.0, 2.0),
        _bounded(proposal.expanded_nodes if proposal else 0, 10_000.0),
    ]
    return np.asarray(features, dtype=np.float32)


def build_manager_observation(
    observation: dict[str, Any],
    info: dict[str, Any],
    state: ManagerState,
    profile_count: int = DEFAULT_MANAGER_PROFILE_COUNT,
    search_control_count: int = DEFAULT_MANAGER_SEARCH_CONTROL_COUNT,
    feature_dim: int | None = None,
):
    return {
        "board": observation["board"],
        "next_pairs": observation["next_pairs"],
        "manager_features": encode_manager_features(
            info,
            state,
            profile_count,
            search_control_count,
            feature_dim,
        ),
    }


def manager_vector_features(observation: dict[str, Any]):
    return np.concatenate(
        [observation["next_pairs"].reshape(-1), observation["manager_features"]]
    ).astype(np.float32, copy=False)


def _canonical_strategy(strategy: str) -> str:
    return {
        "large_chain": "build_large",
        "quick_attack": "build_budget",
        "fire": "fire_max",
    }.get(strategy, strategy)


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
        search_controls: tuple[SearchControl, ...] | None = None,
        switch_penalty: float = 0.02,
        decision_time_penalty: float = 0.001,
        search_cost_reward_scale: float = 1.0,
        max_search_latency_ms: float = 80.0,
        auxiliary_reward_scale: float = 0.25,
        curriculum_stage: str = "full",
    ):
        if gym is None or spaces is None or np is None:
            raise ImportError("ManagerSelfPlayEnv requires gymnasium and numpy")
        super().__init__()
        from selfplay.policies import RandomPolicy

        self.versus_env = VersusPuyoEnv(seed=seed, max_steps=max_steps, reward_config=reward_config)
        self.opponent_policy = opponent_policy or RandomPolicy(seed=seed)
        self.orchestrator = StrategyOrchestrator(profiles or default_worker_profiles())
        self.search_controls = search_controls or default_search_controls()
        self.switch_penalty = float(switch_penalty)
        self.decision_time_penalty = float(decision_time_penalty)
        self.search_cost_reward_scale = float(search_cost_reward_scale)
        self.max_search_latency_ms = float(max_search_latency_ms)
        self.auxiliary_reward_scale = float(auxiliary_reward_scale)
        self.curriculum_stage = str(curriculum_stage)
        self.profile_count = len(self.orchestrator.profiles)
        self.search_control_count = len(self.search_controls)
        self.manager_feature_dim = manager_feature_dim(self.profile_count, self.search_control_count)
        self.manager_vector_dim = manager_vector_dim(self.profile_count, self.search_control_count)
        base_space = self.versus_env.observation_space("player_0")
        self.action_space = spaces.Discrete(self.profile_count * self.search_control_count)
        self.observation_space = spaces.Dict(
            {
                "board": base_space["board"],
                "next_pairs": base_space["next_pairs"],
                "manager_features": spaces.Box(
                    0.0,
                    1.0,
                    shape=(self.manager_feature_dim,),
                    dtype=np.float32,
                ),
            }
        )
        self.manager_state = ManagerState(
            profile_counts=[0] * self.profile_count,
            search_control_counts=[0] * self.search_control_count,
        )
        self._episode_return = 0.0
        self._last_observations = None
        self._last_infos = None

    def set_curriculum_stage(self, stage: str, auxiliary_reward_scale: float | None = None) -> None:
        self.curriculum_stage = str(stage)
        if auxiliary_reward_scale is not None:
            self.auxiliary_reward_scale = float(auxiliary_reward_scale)

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed)
        observations, infos = self.versus_env.reset(seed=seed, options=options)
        self.manager_state = ManagerState(
            profile_counts=[0] * self.profile_count,
            search_control_counts=[0] * self.search_control_count,
        )
        self._episode_return = 0.0
        self._last_observations = observations
        self._last_infos = infos
        info = self._info(infos["player_0"])
        observation = build_manager_observation(
            observations["player_0"],
            info,
            self.manager_state,
            self.profile_count,
            self.search_control_count,
        )
        return observation, info

    def _info(self, info: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(info)
        enriched["tactical_context"] = build_tactical_context(enriched)
        enriched["action_mask"] = self._manager_action_mask()
        enriched["manager_profile_id"] = self.manager_state.last_profile_id
        enriched["manager_search_control_id"] = self.manager_state.last_search_control_id
        enriched["manager_switch_count"] = self.manager_state.switch_count
        enriched["manager_profile_counts"] = tuple(self.manager_state.profile_counts)
        enriched["manager_search_control_counts"] = tuple(self.manager_state.search_control_counts)
        enriched["search_proposal"] = self.manager_state.last_proposal
        if self.manager_state.last_proposal is not None:
            enriched["search_objective"] = self.manager_state.last_proposal.objective_dict
            enriched["search_objective_result"] = self.manager_state.last_proposal.objective_result_dict
            enriched["search_control"] = self.manager_state.last_proposal.search_control_dict
        else:
            enriched["search_objective"] = {}
            enriched["search_objective_result"] = {}
            enriched["search_control"] = {}
        enriched["curriculum_stage"] = self.curriculum_stage
        return enriched

    def _manager_action_mask(self):
        if self.curriculum_stage == "safe_build":
            allowed = {"build_large", "build_budget", "survival"}
        elif self.curriculum_stage == "punish":
            allowed = {"build_large", "build_budget", "punish", "fire_max", "survival"}
        else:
            allowed = {
                "build_large",
                "build_budget",
                "punish",
                "counter",
                "fire_max",
                "survival",
            }
        profile_allowed = [
            _canonical_strategy(profile.strategy) in allowed for profile in self.orchestrator.profiles
        ]
        mean_decision_ms = 0.0
        decisions = max(1, sum(self.manager_state.profile_counts))
        if decisions > 0:
            mean_decision_ms = self.manager_state.total_decision_seconds * 1000.0 / decisions
        mask = []
        for profile_is_allowed in profile_allowed:
            for control in self.search_controls:
                control_allowed = control.latency_budget_ms <= self.max_search_latency_ms
                if mean_decision_ms > self.max_search_latency_ms and control.cost_penalty > 0.04:
                    control_allowed = False
                mask.append(profile_is_allowed and control_allowed)
        return np.asarray(mask, dtype=np.bool_)

    def _decode_manager_action(self, action: int) -> tuple[int, int, SearchControl]:
        action_id = int(action)
        profile_id = action_id // self.search_control_count
        control_id = action_id % self.search_control_count
        return profile_id, control_id, self.search_controls[control_id]

    def _tactical_reward(
        self,
        before: TacticalContext,
        after: TacticalContext,
        proposal: SearchProposal,
        reward_components: dict[str, Any],
    ) -> tuple[float, bool]:
        chosen = _canonical_strategy(proposal.strategy)
        recommended = before.recommended_strategy
        outgoing = int(reward_components.get("attack_outgoing", 0))
        canceled = int(reward_components.get("attack_canceled", 0))
        reward = 0.0
        success = False

        if recommended == "build_large":
            reward += 0.05 * (after.build_potential - before.build_potential)
            if outgoing > 0 and before.lethal_margin < 0:
                reward -= 0.25
            success = chosen in {"build_large", "build_budget"}
        elif recommended == "punish":
            success = outgoing >= max(1, before.lethal_target)
            reward += 1.0 if success else -0.5
            if not success:
                self.manager_state.missed_lethal += 1
        elif recommended == "counter":
            success = canceled >= before.incoming_attack or after.incoming_attack == 0
            reward += min(canceled, before.counter_target) / max(1.0, before.counter_target)
            reward += 0.5 if success else -0.5
            if not success:
                self.manager_state.failed_counter += 1
        elif recommended == "survival":
            success = after.own_danger < before.own_danger or after.own_danger < 0.65
            reward += (before.own_danger - after.own_danger) * 2.0
            reward += 0.25 if success else -0.25
        elif recommended == "fire_max":
            success = outgoing > 0
            reward += min(outgoing, max(1, before.own_forecast.immediate_attack)) / max(
                1.0, before.own_forecast.immediate_attack
            )
        if chosen == recommended or (
            recommended == "build_large" and chosen in {"build_large", "build_budget"}
        ):
            reward += 0.05
        if success:
            self.manager_state.tactical_successes += 1
        return reward * self.auxiliary_reward_scale, success

    def step(self, action: int):
        if self._last_observations is None or self._last_infos is None:
            raise RuntimeError("reset() must be called before step()")
        action_id = int(action)
        if not self.action_space.contains(action_id):
            raise ValueError(f"invalid manager action: {action_id}")
        profile_id, control_id, search_control = self._decode_manager_action(action_id)
        if not bool(self._manager_action_mask()[action_id]):
            raise ValueError(
                f"manager action {action_id} is unavailable in curriculum stage {self.curriculum_stage}"
            )

        before = build_tactical_context(self._last_infos["player_0"])
        self._last_infos["player_0"]["tactical_context"] = before
        switched = (
            self.manager_state.last_profile_id >= 0
            and (
                profile_id != self.manager_state.last_profile_id
                or control_id != self.manager_state.last_search_control_id
            )
        )
        if switched:
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
        self.manager_state.search_control_counts[control_id] += 1
        self.manager_state.tactic_counts[before.recommended_strategy] = (
            self.manager_state.tactic_counts.get(before.recommended_strategy, 0) + 1
        )
        self.manager_state.search_control_mode_counts[search_control.mode] = (
            self.manager_state.search_control_mode_counts.get(search_control.mode, 0) + 1
        )
        tactic_key = f"{before.recommended_strategy}:{search_control.name}"
        self.manager_state.search_control_by_tactic[tactic_key] = (
            self.manager_state.search_control_by_tactic.get(tactic_key, 0) + 1
        )

        proposal = self.orchestrator.propose(
            profile_id,
            self._last_observations["player_0"],
            self._last_infos["player_0"],
            search_control,
        )
        self.manager_state.last_proposal = proposal
        self.manager_state.total_decision_seconds += proposal.elapsed_seconds
        self.manager_state.total_expanded_nodes += proposal.expanded_nodes
        search_cost_penalty = self.search_cost_reward_scale * search_control.cost_penalty
        self.manager_state.search_cost_penalty_total += search_cost_penalty
        if proposal.search_control is not None and proposal.search_control.latency_overrun:
            self.manager_state.latency_overruns += 1
        if proposal.objective is not None:
            kind = proposal.objective.kind
            self.manager_state.objective_counts[kind] = self.manager_state.objective_counts.get(kind, 0) + 1
        if proposal.objective_result is not None:
            if proposal.objective_result.achieved:
                self.manager_state.objective_successes += 1
            for reason in proposal.objective_result.miss_reasons:
                self.manager_state.objective_miss_reasons[reason] = (
                    self.manager_state.objective_miss_reasons.get(reason, 0) + 1
                )
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
        reward -= search_cost_penalty
        if proposal.search_control is not None and proposal.search_control.latency_overrun:
            reward -= self.search_cost_reward_scale * 0.05
        after = build_tactical_context(infos["player_0"])
        tactical_reward, tactical_success = self._tactical_reward(
            before,
            after,
            proposal,
            infos["player_0"].get("reward_components", {}),
        )
        reward += tactical_reward
        self._episode_return += reward
        done = terminations["player_0"] or truncations["player_0"]
        info = self._info(infos["player_0"])
        info.update(
            {
                "worker_action": proposal.action,
                "opponent_action": int(opponent_action),
                "strategy_switched": switched,
                "manager_action": action_id,
                "search_control_id": control_id,
                "search_control_name": search_control.name,
                "search_cost_penalty": search_cost_penalty,
                "tactical_reward": tactical_reward,
                "tactical_success": tactical_success,
                "recommended_strategy": before.recommended_strategy,
                "switch_reason": before.switch_reason,
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
                    "search_control_counts": tuple(self.manager_state.search_control_counts),
                    "search_control_mode_counts": dict(self.manager_state.search_control_mode_counts),
                    "search_control_by_tactic": dict(self.manager_state.search_control_by_tactic),
                    "search_cost_penalty_total": self.manager_state.search_cost_penalty_total,
                    "latency_overruns": self.manager_state.latency_overruns,
                    "tactic_counts": dict(self.manager_state.tactic_counts),
                    "objective_counts": dict(self.manager_state.objective_counts),
                    "objective_miss_reasons": dict(self.manager_state.objective_miss_reasons),
                    "objective_success_rate": self.manager_state.objective_successes / decisions,
                    "missed_lethal": self.manager_state.missed_lethal,
                    "failed_counter": self.manager_state.failed_counter,
                    "tactical_success_rate": self.manager_state.tactical_successes / decisions,
                    "mean_decision_ms": self.manager_state.total_decision_seconds * 1000.0 / decisions,
                    "mean_expanded_nodes": self.manager_state.total_expanded_nodes / decisions,
                }
            )
            info["manager_episode"] = episode
        manager_observation = build_manager_observation(
            observations["player_0"],
            info,
            self.manager_state,
            self.profile_count,
            self.search_control_count,
        )
        return manager_observation, reward, terminations["player_0"], truncations["player_0"], info

    def close(self):
        self.versus_env.close()
