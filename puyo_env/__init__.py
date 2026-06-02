"""Reinforcement-learning environment interfaces for the Puyo core."""

from .actions import NUM_ACTIONS, action_to_placement, legal_action_mask
from .rewards import RewardConfig

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


def __getattr__(name: str):
    if name == "SinglePuyoEnv":
        from .single_env import SinglePuyoEnv

        return SinglePuyoEnv
    if name in {"VersusPuyoEnv", "VersusRewardConfig"}:
        from .versus_env import VersusPuyoEnv, VersusRewardConfig

        return {"VersusPuyoEnv": VersusPuyoEnv, "VersusRewardConfig": VersusRewardConfig}[name]
    if name == "VersusSelfPlayEnv":
        from .selfplay_env import VersusSelfPlayEnv

        return VersusSelfPlayEnv
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
