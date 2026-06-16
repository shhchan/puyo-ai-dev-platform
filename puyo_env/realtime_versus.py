"""Tick-synchronous realtime versus match core."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Mapping

from src.core.constants import VISIBLE_HEIGHT
from src.core.realtime import (
    DEFAULT_REALTIME_TIMING,
    RealtimeHeadlessSimulator,
    RealtimeStepResult,
    RealtimeTimingConfig,
    TickInput,
)

REALTIME_AGENTS = ("player_0", "player_1")


@dataclass(frozen=True)
class RealtimeScheduledAttack:
    amount: int
    arrival_tick: int
    source_agent: str
    created_tick: int


@dataclass
class RealtimeVersusPlayerState:
    simulator: RealtimeHeadlessSimulator
    incoming_attacks: list[RealtimeScheduledAttack] = field(default_factory=list)
    score_carry: int = 0
    sent_ojama_total: int = 0
    generated_ojama_total: int = 0
    canceled_ojama_total: int = 0
    received_ojama_total: int = 0

    @property
    def pending_ojama(self) -> int:
        return sum(packet.amount for packet in self.incoming_attacks)


@dataclass(frozen=True)
class RealtimeMatchTickResult:
    tick: int
    player_results: Mapping[str, RealtimeStepResult]
    generated_attacks: Mapping[str, int]
    attack_diagnostics: Mapping[str, Mapping[str, int]]
    dropped_ojama: Mapping[str, int]
    winner: str | None
    snapshot_hash: str


class RealtimeVersusMatch:
    """Drive two realtime headless players with one deterministic match clock."""

    possible_agents = REALTIME_AGENTS

    def __init__(
        self,
        seed: int | None = None,
        timing: RealtimeTimingConfig | None = None,
        *,
        target_score_per_ojama: int = 70,
        max_ojama_drop: int = 30,
        attack_delay_ticks: int | None = None,
    ):
        self.seed = seed
        self.timing = timing or DEFAULT_REALTIME_TIMING
        self.target_score_per_ojama = int(target_score_per_ojama)
        if self.target_score_per_ojama <= 0:
            raise ValueError("target_score_per_ojama must be positive")
        self.max_ojama_drop = int(max_ojama_drop)
        self.attack_delay_ticks = (
            self.timing.attack_delay_ticks if attack_delay_ticks is None else int(attack_delay_ticks)
        )
        self.tick = 0
        self.player_states: dict[str, RealtimeVersusPlayerState] = {}
        self._ojama_rngs: dict[str, random.Random] = {}
        self._last_winner: str | None = None
        self.reset(seed=seed)

    def reset(self, seed: int | None = None) -> None:
        if seed is not None:
            self.seed = seed
        rng_seed = 0 if self.seed is None else self.seed
        self.tick = 0
        self._last_winner = None
        self.player_states = {
            agent: RealtimeVersusPlayerState(
                simulator=RealtimeHeadlessSimulator(seed=self.seed, timing=self.timing)
            )
            for agent in self.possible_agents
        }
        self._ojama_rngs = {
            "player_0": random.Random(rng_seed + 300_003),
            "player_1": random.Random(rng_seed + 400_003),
        }

    def step(
        self,
        inputs: Mapping[str, TickInput] | None = None,
    ) -> RealtimeMatchTickResult:
        inputs = inputs or {}
        current_tick = self.tick
        player_results = {
            agent: self.player_states[agent].simulator.step(inputs.get(agent))
            for agent in self.possible_agents
        }

        generated = {
            agent: self._attack_units_from_step(agent, player_results[agent])
            for agent in self.possible_agents
        }
        diagnostics = self.resolve_generated_attacks(generated)
        dropped = {
            agent: self._apply_due_ojama(agent)
            for agent in self.possible_agents
        }
        winner = self._winner_from_game_over()
        self._last_winner = winner

        self.tick += 1
        return RealtimeMatchTickResult(
            tick=current_tick,
            player_results=player_results,
            generated_attacks=generated,
            attack_diagnostics=diagnostics,
            dropped_ojama=dropped,
            winner=winner,
            snapshot_hash=self.state_hash(),
        )

    def advance_ticks(
        self,
        count: int,
        inputs_by_tick: Mapping[int, Mapping[str, TickInput]] | None = None,
    ) -> list[RealtimeMatchTickResult]:
        inputs_by_tick = inputs_by_tick or {}
        return [self.step(inputs_by_tick.get(self.tick)) for _ in range(int(count))]

    def schedule_attack(
        self,
        attacker: str,
        units: int,
        *,
        delay_ticks: int | None = None,
    ) -> None:
        if units <= 0:
            return
        defender = self._opponent(attacker)
        delay = self.attack_delay_ticks if delay_ticks is None else int(delay_ticks)
        self.player_states[defender].incoming_attacks.append(
            RealtimeScheduledAttack(
                amount=int(units),
                arrival_tick=self.tick + max(0, delay),
                source_agent=attacker,
                created_tick=self.tick,
            )
        )

    def resolve_generated_attacks(self, generated: Mapping[str, int]) -> dict[str, dict[str, int]]:
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
            self.schedule_attack(agent, diagnostics[agent]["outgoing"])
        return diagnostics

    def state_hash(self) -> str:
        payload = {
            "tick": self.tick,
            "players": {
                agent: {
                    "simulator": self.player_states[agent].simulator.state_hash(),
                    "incoming": [
                        {
                            "amount": packet.amount,
                            "arrival_tick": packet.arrival_tick,
                            "source_agent": packet.source_agent,
                            "created_tick": packet.created_tick,
                        }
                        for packet in sorted(
                            self.player_states[agent].incoming_attacks,
                            key=lambda item: (item.arrival_tick, item.created_tick, item.source_agent),
                        )
                    ],
                    "score_carry": self.player_states[agent].score_carry,
                    "sent_ojama_total": self.player_states[agent].sent_ojama_total,
                    "generated_ojama_total": self.player_states[agent].generated_ojama_total,
                    "canceled_ojama_total": self.player_states[agent].canceled_ojama_total,
                    "received_ojama_total": self.player_states[agent].received_ojama_total,
                }
                for agent in self.possible_agents
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _opponent(self, agent: str) -> str:
        if agent == "player_0":
            return "player_1"
        if agent == "player_1":
            return "player_0"
        raise KeyError(f"unknown agent: {agent}")

    def _attack_units_from_step(self, agent: str, result: RealtimeStepResult) -> int:
        score_delta = 0
        for event in result.events:
            if event.type == "resolution_complete":
                score_delta += int(event.data.get("score_delta", 0))
        if score_delta <= 0:
            return 0
        state = self.player_states[agent]
        total = state.score_carry + score_delta
        units = total // self.target_score_per_ojama
        state.score_carry = total % self.target_score_per_ojama
        return int(units)

    def _consume_incoming(
        self,
        agent: str,
        amount: int,
        *,
        max_arrival_tick: int | None = None,
    ) -> int:
        state = self.player_states[agent]
        remaining = max(0, int(amount))
        consumed = 0
        retained: list[RealtimeScheduledAttack] = []
        for packet in sorted(state.incoming_attacks, key=lambda item: (item.arrival_tick, item.created_tick)):
            if remaining <= 0 or (
                max_arrival_tick is not None and packet.arrival_tick > max_arrival_tick
            ):
                retained.append(packet)
                continue
            used = min(packet.amount, remaining)
            consumed += used
            remaining -= used
            if packet.amount > used:
                retained.append(
                    RealtimeScheduledAttack(
                        amount=packet.amount - used,
                        arrival_tick=packet.arrival_tick,
                        source_agent=packet.source_agent,
                        created_tick=packet.created_tick,
                    )
                )
        state.incoming_attacks = retained
        return consumed

    def _apply_due_ojama(self, agent: str) -> int:
        state = self.player_states[agent]
        game = state.simulator.game
        if game.game_over or game.state == "animate":
            return 0
        due = sum(packet.amount for packet in state.incoming_attacks if packet.arrival_tick <= self.tick)
        drop_count = min(due, self.max_ojama_drop)
        if drop_count <= 0:
            return 0
        placed = game.field.drop_ojama(
            drop_count,
            rng=self._ojama_rngs[agent],
            max_per_drop=self.max_ojama_drop,
        )
        self._consume_incoming(agent, placed, max_arrival_tick=self.tick)
        state.received_ojama_total += placed
        if not game.field.get_puyo(2, VISIBLE_HEIGHT - 1).is_empty():
            game.game_over = True
            game.state = "gameover"
        return placed

    def _winner_from_game_over(self) -> str | None:
        over_0 = self.player_states["player_0"].simulator.game.game_over
        over_1 = self.player_states["player_1"].simulator.game.game_over
        if over_0 and not over_1:
            return "player_1"
        if over_1 and not over_0:
            return "player_0"
        if over_0 and over_1:
            score_0 = self.player_states["player_0"].simulator.game.score
            score_1 = self.player_states["player_1"].simulator.game.score
            if score_0 > score_1:
                return "player_0"
            if score_1 > score_0:
                return "player_1"
        return None
