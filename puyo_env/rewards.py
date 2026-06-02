"""Reward shaping for single-player Puyo reinforcement learning."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.headless import HeadlessStepResult


@dataclass(frozen=True)
class RewardConfig:
    """Reward weights for the Phase 1 single-player environment."""

    target_score_per_ojama: float = 70.0
    chain_bonus: float = 0.05
    survival_bonus: float = 0.01
    game_over_penalty: float = 10.0
    invalid_action_penalty: float = 5.0


def score_to_ojama(score_delta: int, target_score_per_ojama: float = 70.0) -> float:
    """Convert a score delta to attack-point style reward units."""

    if target_score_per_ojama <= 0:
        raise ValueError("target_score_per_ojama must be positive")
    return float(score_delta) / float(target_score_per_ojama)


def single_player_reward(result: HeadlessStepResult, config: RewardConfig | None = None) -> float:
    """Compute shaped reward from a headless placement result."""

    reward_config = config or RewardConfig()
    if not result.valid:
        return -reward_config.invalid_action_penalty

    reward = score_to_ojama(result.score_delta, reward_config.target_score_per_ojama)
    reward += reward_config.chain_bonus * float(result.chain_count)
    if not result.game_over:
        reward += reward_config.survival_bonus
    else:
        reward -= reward_config.game_over_penalty
    return reward


def reward_components(result: HeadlessStepResult, config: RewardConfig | None = None) -> dict[str, float]:
    """Return reward components for logging/debugging."""

    reward_config = config or RewardConfig()
    if not result.valid:
        return {
            "score_reward": 0.0,
            "chain_reward": 0.0,
            "survival_reward": 0.0,
            "terminal_penalty": 0.0,
            "invalid_penalty": -reward_config.invalid_action_penalty,
        }

    score_reward = score_to_ojama(result.score_delta, reward_config.target_score_per_ojama)
    chain_reward = reward_config.chain_bonus * float(result.chain_count)
    survival_reward = reward_config.survival_bonus if not result.game_over else 0.0
    terminal_penalty = -reward_config.game_over_penalty if result.game_over else 0.0
    return {
        "score_reward": score_reward,
        "chain_reward": chain_reward,
        "survival_reward": survival_reward,
        "terminal_penalty": terminal_penalty,
        "invalid_penalty": 0.0,
    }
