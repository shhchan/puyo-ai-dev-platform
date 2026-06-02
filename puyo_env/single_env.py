"""Gymnasium-compatible single-player Puyo environment."""

from __future__ import annotations

from typing import Any

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:  # pragma: no cover - dependency guard
    gym = None
    spaces = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency guard
    np = None

from src.core.headless import HeadlessPuyoSimulator

from .actions import NUM_ACTIONS, action_to_placement, legal_action_mask
from .obs import encode_observation, make_observation_space
from .rewards import RewardConfig, reward_components, single_player_reward


_BaseEnv = gym.Env if gym is not None else object


class SinglePuyoEnv(_BaseEnv):
    """One action places one pair and resolves all chains synchronously."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        seed: int | None = None,
        max_steps: int = 500,
        reward_config: RewardConfig | None = None,
        include_action_mask_in_observation: bool = False,
    ):
        if gym is None or spaces is None or np is None:
            raise ImportError(
                "SinglePuyoEnv requires gymnasium and numpy. Install dependencies with "
                "`pip install -r requirements.txt`."
            )
        super().__init__()
        self.base_seed = seed
        self.max_steps = max_steps
        self.reward_config = reward_config or RewardConfig()
        self.include_action_mask_in_observation = include_action_mask_in_observation

        self.action_space = spaces.Discrete(NUM_ACTIONS)
        self.observation_space = make_observation_space(
            spaces,
            include_action_mask=include_action_mask_in_observation,
            action_count=NUM_ACTIONS,
        )

        self.simulator: HeadlessPuyoSimulator | None = None
        self.step_count = 0
        self.episode_return = 0.0
        self._episode_index = 0

    def _effective_seed(self, seed: int | None) -> int | None:
        if seed is not None:
            return seed
        if self.base_seed is None:
            return None
        return self.base_seed + self._episode_index

    def action_mask(self):
        if self.simulator is None or self.simulator.game.game_over:
            return np.zeros(NUM_ACTIONS, dtype=np.bool_)
        return np.asarray(legal_action_mask(self.simulator), dtype=np.bool_)

    def _observation_and_info(self) -> tuple[dict[str, Any], dict[str, Any]]:
        mask = self.action_mask()
        observation = encode_observation(
            self.simulator,
            step_count=self.step_count,
            max_steps=self.max_steps,
            action_mask=mask,
            include_action_mask=self.include_action_mask_in_observation,
        )
        return observation, {"action_mask": mask}

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        if gym is not None:
            super().reset(seed=seed)
        _ = options
        effective_seed = self._effective_seed(seed)
        self.simulator = HeadlessPuyoSimulator(seed=effective_seed)
        self.step_count = 0
        self.episode_return = 0.0
        self._episode_index += 1
        return self._observation_and_info()

    def step(self, action: int):
        if self.simulator is None:
            raise RuntimeError("reset() must be called before step()")
        if self.simulator.game.game_over:
            raise RuntimeError("step() was called after episode termination")

        mask_before = self.action_mask()
        action_int = int(action)
        invalid_index = action_int < 0 or action_int >= NUM_ACTIONS
        masked_out = (not invalid_index) and (not bool(mask_before[action_int]))
        if invalid_index or masked_out:
            self.step_count += 1
            reward = -self.reward_config.invalid_action_penalty
            self.episode_return += reward
            observation, info = self._observation_and_info()
            info.update(
                {
                    "valid": False,
                    "invalid_action": True,
                    "score": self.simulator.game.score,
                    "score_delta": 0,
                    "chain_count": 0,
                    "episode": {
                        "r": self.episode_return,
                        "l": self.step_count,
                        "score": self.simulator.game.score,
                    },
                }
            )
            return observation, reward, True, False, info

        placement = action_to_placement(action_int)
        result = self.simulator.step(placement)
        self.step_count += 1

        reward = single_player_reward(result, self.reward_config)
        self.episode_return += reward
        terminated = bool(result.game_over or not result.valid)
        truncated = bool(self.step_count >= self.max_steps and not terminated)

        observation, info = self._observation_and_info()
        info.update(
            {
                "valid": result.valid,
                "invalid_action": not result.valid,
                "score": self.simulator.game.score,
                "score_delta": result.score_delta,
                "chain_count": result.chain_count,
                "reward_components": reward_components(result, self.reward_config),
            }
        )
        if terminated or truncated:
            info["episode"] = {
                "r": self.episode_return,
                "l": self.step_count,
                "score": self.simulator.game.score,
            }
        return observation, reward, terminated, truncated, info
