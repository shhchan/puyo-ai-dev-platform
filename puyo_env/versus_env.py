"""Parallel two-player Puyo environment for flat self-play."""

from __future__ import annotations

import random
from dataclasses import dataclass
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

from src.core.constants import VISIBLE_HEIGHT
from src.core.headless import HeadlessPuyoSimulator

from .actions import NUM_ACTIONS, action_to_placement, legal_action_mask
from .obs import (
    BOARD_COLOR_CHANNELS,
    BOARD_ROWS,
    GRID_WIDTH,
    SCALAR_FEATURE_DIM,
    VISIBLE_PAIR_COUNT,
    encode_board,
    encode_next_pairs,
    encode_scalars,
)
from .rewards import score_to_ojama


AGENTS = ("player_0", "player_1")


@dataclass(frozen=True)
class VersusRewardConfig:
    """Reward weights for flat versus training."""

    target_score_per_ojama: int = 70
    score_reward: float = 0.25
    attack_reward: float = 0.5
    chain_bonus: float = 0.05
    survival_bonus: float = 0.01
    garbage_penalty: float = 0.02
    invalid_action_penalty: float = 5.0
    win_reward: float = 10.0
    loss_penalty: float = 10.0
    draw_penalty: float = 1.0


@dataclass
class VersusPlayerState:
    simulator: HeadlessPuyoSimulator
    pending_ojama: int = 0
    score_carry: int = 0
    sent_ojama_total: int = 0
    received_ojama_total: int = 0
    episode_return: float = 0.0


class VersusPuyoEnv:
    """Two-player turn-synchronous environment with action masks.

    The API follows PettingZoo's ParallelEnv shape:
    ``reset() -> (observations, infos)`` and
    ``step(actions) -> (observations, rewards, terminations, truncations, infos)``.
    Each joint step places one pair for each live player and resolves all chains.
    """

    metadata = {"name": "puyo_versus_v0", "render_modes": []}
    possible_agents = AGENTS

    def __init__(
        self,
        seed: int | None = None,
        max_steps: int = 500,
        reward_config: VersusRewardConfig | None = None,
        include_action_mask_in_observation: bool = False,
        max_ojama_drop: int = 30,
    ):
        if gym is None or spaces is None or np is None:
            raise ImportError(
                "VersusPuyoEnv requires gymnasium and numpy. Install dependencies with "
                "`pip install -r requirements.txt`."
            )
        self.base_seed = seed
        self.max_steps = max_steps
        self.reward_config = reward_config or VersusRewardConfig()
        self.include_action_mask_in_observation = include_action_mask_in_observation
        self.max_ojama_drop = max_ojama_drop

        self._action_spaces = {agent: spaces.Discrete(NUM_ACTIONS) for agent in self.possible_agents}
        self._observation_spaces = {
            agent: make_versus_observation_space(spaces, include_action_mask_in_observation)
            for agent in self.possible_agents
        }
        self.action_spaces = self._action_spaces
        self.observation_spaces = self._observation_spaces

        self.agents: list[str] = []
        self.player_states: dict[str, VersusPlayerState] = {}
        self.step_count = 0
        self._episode_index = 0
        self._ojama_rngs: dict[str, random.Random] = {}
        self._last_winner: str | None = None

    def observation_space(self, agent: str):
        return self._observation_spaces[agent]

    def action_space(self, agent: str):
        return self._action_spaces[agent]

    def _effective_seed(self, seed: int | None) -> int | None:
        if seed is not None:
            return seed
        if self.base_seed is None:
            return None
        return self.base_seed + self._episode_index

    def _opponent(self, agent: str) -> str:
        if agent == "player_0":
            return "player_1"
        if agent == "player_1":
            return "player_0"
        raise KeyError(f"unknown agent: {agent}")

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        _ = options
        effective_seed = self._effective_seed(seed)
        self.agents = list(self.possible_agents)
        self.step_count = 0
        self._last_winner = None

        self.player_states = {
            agent: VersusPlayerState(simulator=HeadlessPuyoSimulator(seed=effective_seed))
            for agent in self.possible_agents
        }
        rng_seed = 0 if effective_seed is None else effective_seed
        self._ojama_rngs = {
            "player_0": random.Random(rng_seed + 100_003),
            "player_1": random.Random(rng_seed + 200_003),
        }
        self._episode_index += 1
        return self._observations_and_infos()

    def action_mask(self, agent: str):
        if agent not in self.player_states:
            return np.zeros(NUM_ACTIONS, dtype=np.bool_)
        state = self.player_states[agent]
        if state.simulator.game.game_over or agent not in self.agents:
            return np.zeros(NUM_ACTIONS, dtype=np.bool_)
        return np.asarray(legal_action_mask(state.simulator), dtype=np.bool_)

    def _observation(self, agent: str) -> dict[str, Any]:
        state = self.player_states[agent]
        opponent_state = self.player_states[self._opponent(agent)]
        own_board = encode_board(state.simulator.game)
        opponent_board = encode_board(opponent_state.simulator.game)
        observation = {
            "board": np.concatenate([own_board, opponent_board], axis=0).astype(np.float32, copy=False),
            "own_board": own_board,
            "opponent_board": opponent_board,
            "next_pairs": encode_next_pairs(state.simulator.game),
            "scalars": encode_scalars(
                state.simulator.game,
                step_count=self.step_count,
                max_steps=self.max_steps,
                pending_ojama=state.pending_ojama,
                sent_ojama=state.sent_ojama_total,
            ),
        }
        if self.include_action_mask_in_observation:
            observation["action_mask"] = self.action_mask(agent).astype(np.int8)
        return observation

    def _info(self, agent: str) -> dict[str, Any]:
        state = self.player_states[agent]
        opponent_state = self.player_states[self._opponent(agent)]
        return {
            "action_mask": self.action_mask(agent),
            "score": state.simulator.game.score,
            "opponent_score": opponent_state.simulator.game.score,
            "pending_ojama": state.pending_ojama,
            "sent_ojama_total": state.sent_ojama_total,
            "received_ojama_total": state.received_ojama_total,
            "simulator": state.simulator,
        }

    def _observations_and_infos(self):
        observations = {agent: self._observation(agent) for agent in self.possible_agents}
        infos = {agent: self._info(agent) for agent in self.possible_agents}
        return observations, infos

    def _apply_pending_ojama(self, agent: str) -> int:
        state = self.player_states[agent]
        if state.pending_ojama <= 0 or state.simulator.game.game_over:
            return 0

        drop_count = min(state.pending_ojama, self.max_ojama_drop)
        placed = state.simulator.game.field.drop_ojama(
            drop_count,
            rng=self._ojama_rngs[agent],
            max_per_drop=self.max_ojama_drop,
        )
        state.pending_ojama -= placed
        state.received_ojama_total += placed
        if placed < drop_count or not state.simulator.game.field.get_puyo(2, VISIBLE_HEIGHT - 1).is_empty():
            state.simulator.game.game_over = True
            state.simulator.game.state = "gameover"
        return placed

    def _attack_units_from_score(self, agent: str, score_delta: int) -> int:
        state = self.player_states[agent]
        total = state.score_carry + max(0, int(score_delta))
        units = total // self.reward_config.target_score_per_ojama
        state.score_carry = total % self.reward_config.target_score_per_ojama
        return int(units)

    def _queue_attack(self, attacker: str, units: int) -> dict[str, int]:
        if units <= 0:
            return {"generated": 0, "canceled": 0, "outgoing": 0}
        attacker_state = self.player_states[attacker]
        defender_state = self.player_states[self._opponent(attacker)]

        canceled = min(units, attacker_state.pending_ojama)
        outgoing = units - canceled
        attacker_state.pending_ojama -= canceled
        defender_state.pending_ojama += outgoing
        attacker_state.sent_ojama_total += outgoing
        return {"generated": units, "canceled": canceled, "outgoing": outgoing}

    def _winner_from_scores(self) -> str | None:
        score_0 = self.player_states["player_0"].simulator.game.score
        score_1 = self.player_states["player_1"].simulator.game.score
        if score_0 > score_1:
            return "player_0"
        if score_1 > score_0:
            return "player_1"
        return None

    def _winner_from_game_over(self) -> str | None:
        over_0 = self.player_states["player_0"].simulator.game.game_over
        over_1 = self.player_states["player_1"].simulator.game.game_over
        if over_0 and not over_1:
            return "player_1"
        if over_1 and not over_0:
            return "player_0"
        if over_0 and over_1:
            return self._winner_from_scores()
        return None

    def _terminal_reward(self, agent: str, winner: str | None) -> float:
        if winner is None:
            return -self.reward_config.draw_penalty
        if winner == agent:
            return self.reward_config.win_reward
        return -self.reward_config.loss_penalty

    def step(self, actions: dict[str, int]):
        if not self.agents:
            raise RuntimeError("step() was called after episode termination")

        rewards = {agent: 0.0 for agent in self.possible_agents}
        components: dict[str, dict[str, Any]] = {
            agent: {
                "garbage_received": 0,
                "score_delta": 0,
                "chain_count": 0,
                "attack_generated": 0,
                "attack_canceled": 0,
                "attack_outgoing": 0,
                "invalid_action": False,
            }
            for agent in self.possible_agents
        }

        for agent in list(self.agents):
            placed = self._apply_pending_ojama(agent)
            components[agent]["garbage_received"] = placed
            rewards[agent] -= self.reward_config.garbage_penalty * float(placed)

        results = {}
        for agent in list(self.agents):
            state = self.player_states[agent]
            if state.simulator.game.game_over:
                results[agent] = None
                continue

            mask = self.action_mask(agent)
            action = actions.get(agent)
            invalid_index = action is None or int(action) < 0 or int(action) >= NUM_ACTIONS
            masked_out = (not invalid_index) and (not bool(mask[int(action)]))
            if invalid_index or masked_out:
                state.simulator.game.game_over = True
                state.simulator.game.state = "gameover"
                components[agent]["invalid_action"] = True
                rewards[agent] -= self.reward_config.invalid_action_penalty
                results[agent] = None
                continue

            result = state.simulator.step(action_to_placement(int(action)))
            results[agent] = result
            components[agent]["score_delta"] = result.score_delta
            components[agent]["chain_count"] = result.chain_count
            if not result.valid:
                state.simulator.game.game_over = True
                state.simulator.game.state = "gameover"
                components[agent]["invalid_action"] = True
                rewards[agent] -= self.reward_config.invalid_action_penalty

        for agent, result in results.items():
            if result is None or not result.valid:
                continue
            attack_units = self._attack_units_from_score(agent, result.score_delta)
            attack = self._queue_attack(agent, attack_units)
            components[agent].update(
                {
                    "attack_generated": attack["generated"],
                    "attack_canceled": attack["canceled"],
                    "attack_outgoing": attack["outgoing"],
                }
            )
            rewards[agent] += self.reward_config.score_reward * score_to_ojama(
                result.score_delta,
                self.reward_config.target_score_per_ojama,
            )
            rewards[agent] += self.reward_config.attack_reward * float(attack["outgoing"])
            rewards[agent] += self.reward_config.chain_bonus * float(result.chain_count)
            if not result.game_over:
                rewards[agent] += self.reward_config.survival_bonus

        self.step_count += 1
        terminal = any(self.player_states[agent].simulator.game.game_over for agent in self.possible_agents)
        truncated = self.step_count >= self.max_steps and not terminal
        winner = self._winner_from_game_over() if terminal else None
        if truncated:
            winner = self._winner_from_scores()
        self._last_winner = winner

        episode_done = terminal or truncated
        if episode_done:
            for agent in self.possible_agents:
                rewards[agent] += self._terminal_reward(agent, winner)

        for agent in self.possible_agents:
            self.player_states[agent].episode_return += rewards[agent]

        observations, infos = self._observations_and_infos()
        terminations = {agent: bool(terminal) for agent in self.possible_agents}
        truncations = {agent: bool(truncated) for agent in self.possible_agents}
        for agent in self.possible_agents:
            infos[agent].update(
                {
                    "reward_components": components[agent],
                    "winner": winner,
                    "step_count": self.step_count,
                }
            )
            if episode_done:
                opponent = self._opponent(agent)
                infos[agent]["episode"] = {
                    "r": self.player_states[agent].episode_return,
                    "l": self.step_count,
                    "score": self.player_states[agent].simulator.game.score,
                    "opponent_score": self.player_states[opponent].simulator.game.score,
                    "winner": winner,
                    "win": 0.5 if winner is None else float(winner == agent),
                    "sent_ojama": self.player_states[agent].sent_ojama_total,
                    "received_ojama": self.player_states[agent].received_ojama_total,
                }

        if episode_done:
            self.agents = []
        return observations, rewards, terminations, truncations, infos

    def close(self):
        return None


def make_versus_observation_space(spaces_module: Any, include_action_mask: bool = False):
    """Create the observation space used by each versus player."""

    entries = {
        "board": spaces_module.Box(
            low=0.0,
            high=1.0,
            shape=(len(BOARD_COLOR_CHANNELS) * 2, BOARD_ROWS, GRID_WIDTH),
            dtype=np.float32,
        ),
        "own_board": spaces_module.Box(
            low=0.0,
            high=1.0,
            shape=(len(BOARD_COLOR_CHANNELS), BOARD_ROWS, GRID_WIDTH),
            dtype=np.float32,
        ),
        "opponent_board": spaces_module.Box(
            low=0.0,
            high=1.0,
            shape=(len(BOARD_COLOR_CHANNELS), BOARD_ROWS, GRID_WIDTH),
            dtype=np.float32,
        ),
        "next_pairs": spaces_module.Box(
            low=0.0,
            high=1.0,
            shape=(VISIBLE_PAIR_COUNT, 2, 5),
            dtype=np.float32,
        ),
        "scalars": spaces_module.Box(
            low=0.0,
            high=1.0,
            shape=(SCALAR_FEATURE_DIM,),
            dtype=np.float32,
        ),
    }
    if include_action_mask:
        entries["action_mask"] = spaces_module.MultiBinary(NUM_ACTIONS)
    return spaces_module.Dict(entries)
