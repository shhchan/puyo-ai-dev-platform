"""Reinforcement-learning environment interfaces for the Puyo core."""

from .actions import NUM_ACTIONS, action_to_placement, legal_action_mask
from .rewards import RewardConfig
from .selfplay_env import VersusSelfPlayEnv
from .single_env import SinglePuyoEnv
from .versus_env import VersusPuyoEnv, VersusRewardConfig

__all__ = [
    "NUM_ACTIONS",
    "RewardConfig",
    "SinglePuyoEnv",
    "VersusPuyoEnv",
    "VersusRewardConfig",
    "VersusSelfPlayEnv",
    "action_to_placement",
    "legal_action_mask",
]
