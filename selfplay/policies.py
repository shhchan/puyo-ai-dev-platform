"""Policies used by versus training, league play, and arena evaluation."""

from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Any, Protocol

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency guard
    np = None

try:
    import torch
except (ImportError, OSError):  # pragma: no cover - dependency guard
    torch = None

from agents.networks import PuyoActorCritic, VECTOR_FEATURE_DIM
from agents.beam_search import BeamSearchConfig, BeamSearchPolicy
from puyo_env.actions import NUM_ACTIONS, action_to_placement
from puyo_env.obs import BOARD_COLOR_CHANNELS, BOARD_ROWS, GRID_WIDTH


class Policy(Protocol):
    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        """Return a discrete placement action."""


def legal_indices(info: dict[str, Any]) -> list[int]:
    mask = info.get("action_mask")
    if mask is None:
        return list(range(NUM_ACTIONS))
    if np is None:
        return [index for index, allowed in enumerate(mask) if bool(allowed)]
    return [int(index) for index in np.flatnonzero(mask)]


class FirstLegalPolicy:
    """Deterministic fallback policy useful for smoke tests."""

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        _ = observation
        choices = legal_indices(info)
        if not choices:
            return 0
        return choices[0]


class RandomPolicy:
    """Uniform random legal-action policy."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        _ = observation
        choices = legal_indices(info)
        if not choices:
            return 0
        return self.rng.choice(choices)


class GreedyScorePolicy:
    """One-step lookahead policy that maximizes immediate score and chains."""

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        _ = observation
        choices = legal_indices(info)
        if not choices:
            return 0
        simulator = info.get("simulator")
        if simulator is None:
            return choices[0]

        best_action = choices[0]
        best_value = float("-inf")
        for action in choices:
            sim_copy = copy.deepcopy(simulator)
            result = sim_copy.step(action_to_placement(action))
            if not result.valid:
                value = float("-inf")
            else:
                value = float(result.score_delta) + 70.0 * float(result.chain_count)
                if result.game_over:
                    value -= 10_000.0
            if value > best_value:
                best_value = value
                best_action = action
        return best_action


class CheckpointPolicy:
    """Torch actor-critic checkpoint policy."""

    def __init__(
        self,
        checkpoint_path: str | Path,
        device: str = "cpu",
        deterministic: bool = True,
        board_key: str | None = None,
    ):
        if torch is None or np is None:
            raise ImportError("CheckpointPolicy requires torch and numpy. Install requirements.txt.")
        self.device = torch.device(device)
        self.deterministic = deterministic
        checkpoint = torch.load(Path(checkpoint_path), map_location=self.device)
        state_dict = checkpoint.get("model_state_dict", checkpoint)
        board_channels = self._infer_board_channels(state_dict)
        self.board_key = board_key or ("own_board" if board_channels == len(BOARD_COLOR_CHANNELS) else "board")
        self.agent = PuyoActorCritic(
            board_shape=(board_channels, BOARD_ROWS, GRID_WIDTH),
            vector_dim=VECTOR_FEATURE_DIM,
        ).to(self.device)
        self.agent.load_state_dict(state_dict)
        self.agent.eval()

    def _infer_board_channels(self, state_dict: dict[str, Any]) -> int:
        for key, value in state_dict.items():
            if key.endswith("cnn.0.weight"):
                return int(value.shape[1])
        return len(BOARD_COLOR_CHANNELS)

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        mask = info.get("action_mask")
        if mask is None:
            mask = [True] * NUM_ACTIONS
        with torch.no_grad():
            board_source = observation[self.board_key]
            board = torch.as_tensor(np.asarray(board_source)[None, ...], dtype=torch.float32, device=self.device)
            next_pairs = torch.as_tensor(
                np.asarray(observation["next_pairs"])[None, ...],
                dtype=torch.float32,
                device=self.device,
            )
            scalars = torch.as_tensor(
                np.asarray(observation["scalars"])[None, ...],
                dtype=torch.float32,
                device=self.device,
            )
            action_mask = torch.as_tensor(np.asarray(mask)[None, ...], dtype=torch.bool, device=self.device)
            obs = {"board": board, "next_pairs": next_pairs, "scalars": scalars}
            if self.deterministic:
                logits, _ = self.agent.forward(
                    obs["board"],
                    torch.cat([next_pairs.reshape(1, -1), scalars], dim=1),
                    action_mask=action_mask,
                )
                return int(torch.argmax(logits, dim=1).item())
            action = self.agent.get_action_and_value(obs, action_mask=action_mask)[0]
            return int(action.item())


def make_policy(
    policy_type: str,
    *,
    seed: int | None = None,
    checkpoint_path: str | Path | None = None,
    device: str = "cpu",
    deterministic: bool = True,
    beam_depth: int = 10,
    beam_width: int = 48,
    beam_scenarios: int = 1,
    beam_minimum_chain: int = 6,
) -> Policy:
    if policy_type == "first":
        return FirstLegalPolicy()
    if policy_type == "random":
        return RandomPolicy(seed=seed)
    if policy_type == "greedy":
        return GreedyScorePolicy()
    if policy_type == "beam":
        return BeamSearchPolicy(
            BeamSearchConfig(
                depth=beam_depth,
                width=beam_width,
                scenarios=beam_scenarios,
                minimum_chain_count=beam_minimum_chain,
                scenario_seed=seed,
            )
        )
    if policy_type == "checkpoint":
        if checkpoint_path is None:
            raise ValueError("checkpoint_path is required for checkpoint policy")
        return CheckpointPolicy(checkpoint_path, device=device, deterministic=deterministic)
    raise ValueError(f"unknown policy_type: {policy_type}")
