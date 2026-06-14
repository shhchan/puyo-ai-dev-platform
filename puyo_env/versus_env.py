"""Parallel two-player Puyo environment for flat self-play."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class ScheduledAttack:
    """One deterministic incoming ojama packet."""

    amount: int
    arrival_step: int
    source_agent: str
    created_step: int


@dataclass
class VersusPlayerState:
    simulator: HeadlessPuyoSimulator
    incoming_attacks: list[ScheduledAttack] = field(default_factory=list)
    score_carry: int = 0
    sent_ojama_total: int = 0
    generated_ojama_total: int = 0
    canceled_ojama_total: int = 0
    received_ojama_total: int = 0
    episode_return: float = 0.0
    max_chain_count: int = 0

    @property
    def pending_ojama(self) -> int:
        """Compatibility aggregate for callers that predate scheduled attacks."""

        return sum(packet.amount for packet in self.incoming_attacks)

    @pending_ojama.setter
    def pending_ojama(self, amount: int) -> None:
        self.incoming_attacks.clear()
        if amount > 0:
            self.incoming_attacks.append(
                ScheduledAttack(
                    amount=int(amount),
                    arrival_step=0,
                    source_agent="legacy",
                    created_step=-1,
                )
            )


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
        attack_delay_steps: int = 1,
        capture_visuals: bool = False,
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
        self.attack_delay_steps = max(0, int(attack_delay_steps))
        self.capture_visuals = capture_visuals

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
            "incoming_ojama": state.pending_ojama,
            "incoming_turns": self._incoming_turns(agent),
            "incoming_arrival_step": self._next_arrival_step(agent),
            "incoming_attack_packets": tuple(
                {
                    "amount": packet.amount,
                    "arrival_step": packet.arrival_step,
                    "source_agent": packet.source_agent,
                    "created_step": packet.created_step,
                }
                for packet in sorted(state.incoming_attacks, key=lambda item: item.arrival_step)
            ),
            "sent_ojama_total": state.sent_ojama_total,
            "generated_ojama_total": state.generated_ojama_total,
            "canceled_ojama_total": state.canceled_ojama_total,
            "received_ojama_total": state.received_ojama_total,
            "max_chain_count": state.max_chain_count,
            "simulator": state.simulator,
            "opponent_pending_ojama": opponent_state.pending_ojama,
            "opponent_incoming_turns": self._incoming_turns(self._opponent(agent)),
            "opponent_sent_ojama_total": opponent_state.sent_ojama_total,
            "opponent_received_ojama_total": opponent_state.received_ojama_total,
            "opponent_max_chain_count": opponent_state.max_chain_count,
            "opponent_simulator": opponent_state.simulator,
            "step_count": self.step_count,
            "max_steps": self.max_steps,
        }

    def _observations_and_infos(self):
        observations = {agent: self._observation(agent) for agent in self.possible_agents}
        infos = {agent: self._info(agent) for agent in self.possible_agents}
        return observations, infos

    def _next_arrival_step(self, agent: str) -> int | None:
        attacks = self.player_states[agent].incoming_attacks
        if not attacks:
            return None
        return min(packet.arrival_step for packet in attacks)

    def _incoming_turns(self, agent: str) -> int:
        arrival_step = self._next_arrival_step(agent)
        if arrival_step is None:
            return 0
        return max(0, int(arrival_step) - self.step_count)

    def _consume_incoming(
        self,
        agent: str,
        amount: int,
        *,
        max_arrival_step: int | None = None,
    ) -> int:
        state = self.player_states[agent]
        remaining = max(0, int(amount))
        consumed = 0
        retained: list[ScheduledAttack] = []
        for packet in sorted(state.incoming_attacks, key=lambda item: (item.arrival_step, item.created_step)):
            if remaining <= 0 or (
                max_arrival_step is not None and packet.arrival_step > max_arrival_step
            ):
                retained.append(packet)
                continue
            used = min(packet.amount, remaining)
            consumed += used
            remaining -= used
            if packet.amount > used:
                retained.append(
                    ScheduledAttack(
                        amount=packet.amount - used,
                        arrival_step=packet.arrival_step,
                        source_agent=packet.source_agent,
                        created_step=packet.created_step,
                    )
                )
        state.incoming_attacks = retained
        return consumed

    def _schedule_attack(self, attacker: str, units: int) -> None:
        if units <= 0:
            return
        defender = self._opponent(attacker)
        self.player_states[defender].incoming_attacks.append(
            ScheduledAttack(
                amount=int(units),
                arrival_step=self.step_count + self.attack_delay_steps + 1,
                source_agent=attacker,
                created_step=self.step_count,
            )
        )

    def _apply_pending_ojama(self, agent: str, *, due_only: bool = False) -> int:
        state = self.player_states[agent]
        if state.pending_ojama <= 0 or state.simulator.game.game_over:
            return 0

        due_step = self.step_count if due_only else None
        eligible = sum(
            packet.amount
            for packet in state.incoming_attacks
            if due_step is None or packet.arrival_step <= due_step
        )
        drop_count = min(eligible, self.max_ojama_drop)
        if drop_count <= 0:
            return 0
        placed = state.simulator.game.field.drop_ojama(
            drop_count,
            rng=self._ojama_rngs[agent],
            max_per_drop=self.max_ojama_drop,
        )
        self._consume_incoming(agent, placed, max_arrival_step=due_step)
        state.received_ojama_total += placed
        if not state.simulator.game.field.get_puyo(2, VISIBLE_HEIGHT - 1).is_empty():
            state.simulator.game.game_over = True
            state.simulator.game.state = "gameover"
        return placed

    def _attack_units_from_score(self, agent: str, score_delta: int) -> int:
        state = self.player_states[agent]
        total = state.score_carry + max(0, int(score_delta))
        units = total // self.reward_config.target_score_per_ojama
        state.score_carry = total % self.reward_config.target_score_per_ojama
        return int(units)

    def _resolve_attacks(self, generated: dict[str, int]) -> dict[str, dict[str, int]]:
        """Resolve both attacks without player-order bias, then schedule excess."""

        remaining: dict[str, int] = {}
        diagnostics: dict[str, dict[str, int]] = {}
        for agent in self.possible_agents:
            units = max(0, int(generated.get(agent, 0)))
            canceled_incoming = self._consume_incoming(agent, units)
            remaining[agent] = units - canceled_incoming
            diagnostics[agent] = {
                "generated": units,
                "canceled": canceled_incoming,
                "outgoing": 0,
            }

        simultaneous_cancel = min(remaining["player_0"], remaining["player_1"])
        for agent in self.possible_agents:
            remaining[agent] -= simultaneous_cancel
            diagnostics[agent]["canceled"] += simultaneous_cancel
            diagnostics[agent]["outgoing"] = remaining[agent]
            state = self.player_states[agent]
            state.generated_ojama_total += diagnostics[agent]["generated"]
            state.canceled_ojama_total += diagnostics[agent]["canceled"]
            state.sent_ojama_total += diagnostics[agent]["outgoing"]
            self._schedule_attack(agent, diagnostics[agent]["outgoing"])
        return diagnostics

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

            result = state.simulator.step(
                action_to_placement(int(action)),
                capture_visuals=self.capture_visuals,
            )
            results[agent] = result
            components[agent]["score_delta"] = result.score_delta
            components[agent]["chain_count"] = result.chain_count
            state.max_chain_count = max(state.max_chain_count, int(result.chain_count))
            if not result.valid:
                state.simulator.game.game_over = True
                state.simulator.game.state = "gameover"
                components[agent]["invalid_action"] = True
                rewards[agent] -= self.reward_config.invalid_action_penalty

        generated_attacks = {agent: 0 for agent in self.possible_agents}
        for agent, result in results.items():
            if result is None or not result.valid:
                continue
            generated_attacks[agent] = self._attack_units_from_score(agent, result.score_delta)

        attacks = self._resolve_attacks(generated_attacks)
        for agent, result in results.items():
            if result is None or not result.valid:
                continue
            attack = attacks[agent]
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
        for agent in list(self.agents):
            placed = self._apply_pending_ojama(agent, due_only=True)
            components[agent]["garbage_received"] = placed
            rewards[agent] -= self.reward_config.garbage_penalty * float(placed)

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
                    "step_result": results.get(agent),
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
                    "generated_ojama": self.player_states[agent].generated_ojama_total,
                    "canceled_ojama": self.player_states[agent].canceled_ojama_total,
                    "received_ojama": self.player_states[agent].received_ojama_total,
                    "max_chain": self.player_states[agent].max_chain_count,
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
