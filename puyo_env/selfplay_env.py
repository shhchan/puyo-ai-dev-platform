"""Single-agent Gym wrapper around the two-player versus environment."""

from __future__ import annotations

from typing import Any

try:
    import gymnasium as gym
except ImportError:  # pragma: no cover - dependency guard
    gym = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency guard
    np = None

from selfplay.policies import Policy, RandomPolicy

from .actions import NUM_ACTIONS
from .versus_env import VersusPuyoEnv, VersusRewardConfig


_BaseEnv = gym.Env if gym is not None else object


class VersusSelfPlayEnv(_BaseEnv):
    """Learner controls player_0 while a supplied policy controls player_1."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        seed: int | None = None,
        max_steps: int = 500,
        opponent_policy: Policy | None = None,
        reward_config: VersusRewardConfig | None = None,
    ):
        if gym is None or np is None:
            raise ImportError(
                "VersusSelfPlayEnv requires gymnasium and numpy. Install dependencies with "
                "`pip install -r requirements.txt`."
            )
        super().__init__()
        self.versus_env = VersusPuyoEnv(seed=seed, max_steps=max_steps, reward_config=reward_config)
        self.opponent_policy = opponent_policy or RandomPolicy(seed=seed)
        self.action_space = self.versus_env.action_space("player_0")
        self.observation_space = self.versus_env.observation_space("player_0")
        self._last_observations: dict[str, dict[str, Any]] | None = None
        self._last_infos: dict[str, dict[str, Any]] | None = None

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        if gym is not None:
            super().reset(seed=seed)
        observations, infos = self.versus_env.reset(seed=seed, options=options)
        self._last_observations = observations
        self._last_infos = infos
        return observations["player_0"], infos["player_0"]

    def action_mask(self):
        if self._last_infos is None:
            return np.zeros(NUM_ACTIONS, dtype=np.bool_)
        return self._last_infos["player_0"]["action_mask"]

    def step(self, action: int):
        if self._last_observations is None or self._last_infos is None:
            raise RuntimeError("reset() must be called before step()")

        opponent_action = self.opponent_policy.select_action(
            self._last_observations["player_1"],
            self._last_infos["player_1"],
        )
        observations, rewards, terminations, truncations, infos = self.versus_env.step(
            {
                "player_0": int(action),
                "player_1": int(opponent_action),
            }
        )
        self._last_observations = observations
        self._last_infos = infos

        info = dict(infos["player_0"])
        info.update(
            {
                "opponent_action": int(opponent_action),
                "opponent_reward": rewards["player_1"],
                "opponent_episode": infos["player_1"].get("episode"),
            }
        )
        return (
            observations["player_0"],
            rewards["player_0"],
            terminations["player_0"],
            truncations["player_0"],
            info,
        )

    def close(self):
        self.versus_env.close()
