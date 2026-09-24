"""Tick-synchronous realtime versus match core."""

from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import dataclass, field, replace
from typing import Mapping

from src.core.constants import VISIBLE_HEIGHT
from src.core.diagnostics import (
    ALL_CLEAR_DIAGNOSTICS_SCHEMA_VERSION,
    build_all_clear_diagnostics,
)
from src.core.ojama import convert_score_to_ojama
from src.core.realtime import (
    DEFAULT_REALTIME_TIMING,
    RealtimeHeadlessSimulator,
    RealtimeStepResult,
    RealtimeTimingConfig,
    TickInput,
)

REALTIME_AGENTS = ("player_0", "player_1")
DEFAULT_GARBAGE_DROP_TICKS = 21


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
    garbage_ticks_remaining: int = 0

    @property
    def pending_ojama(self) -> int:
        return sum(packet.amount for packet in self.incoming_attacks)


@dataclass(frozen=True)
class RealtimeMatchTickResult:
    tick: int
    player_results: Mapping[str, RealtimeStepResult]
    generated_attacks: Mapping[str, int]
    attack_diagnostics: Mapping[str, Mapping[str, int | bool]]
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
        garbage_drop_ticks: int = DEFAULT_GARBAGE_DROP_TICKS,
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
        self.garbage_drop_ticks = int(garbage_drop_ticks)
        if self.garbage_drop_ticks < 0:
            raise ValueError("garbage_drop_ticks must be non-negative")
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
        self._public_snapshot_adapter = None
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
        ending = self.ending
        if self._public_snapshot_adapter is not None:
            self._public_snapshot_adapter.before_tick(self)
        player_results = {
            agent: self.player_states[agent].simulator.step(
                inputs.get(agent), resolution_only=ending, spawn_next=False,
            )
            for agent in self.possible_agents
        }
        # Keep the garbage visual clock moving while a terminal chain resolves.
        # Spawning remains gated on the match-wide top-out checks below.
        for state in self.player_states.values():
            if state.garbage_ticks_remaining:
                state.garbage_ticks_remaining -= 1
                if not state.garbage_ticks_remaining and not state.simulator.game.game_over:
                    state.simulator.game.state = "ready"

        # Resolve both players before checking top-out or spawning either next pair.
        # This keeps simultaneous boundaries independent of player iteration order.
        for state in self.player_states.values():
            game = state.simulator.game
            if game.state == "ready" and not game.field.get_puyo(2, VISIBLE_HEIGHT - 1).is_empty():
                game.game_over = True
                game.state = "gameover"

        attack_metadata = {
            agent: self._attack_metadata_from_step(player_results[agent])
            for agent in self.possible_agents
        }
        generated = {
            agent: self._attack_units_from_score(
                agent,
                int(attack_metadata[agent]["attack_score_delta"]),
            )
            if int(attack_metadata[agent]["attack_score_delta"]) > 0
            else 0
            for agent in self.possible_agents
        }
        diagnostics = self.resolve_generated_attacks(generated)
        for agent in self.possible_agents:
            diagnostics[agent].update(attack_metadata[agent])
        # Once a player tops out, only the already running resolution may finish.
        # Remaining incoming packets are notices, never another garbage drop.
        ending = self.ending
        if self._public_snapshot_adapter is not None:
            self._public_snapshot_adapter.observe_arrivals(self, current_tick)
        dropped = {
            agent: 0 if ending else self._apply_due_ojama(
                agent,
                placement_boundary=self._completed_placement(player_results[agent]),
            )
            for agent in self.possible_agents
        }
        for agent, state in self.player_states.items():
            game = state.simulator.game
            if not self.ending and game.state == "ready":
                game.spawn_puyo()
            result = player_results[agent]
            player_results[agent] = replace(
                result,
                state_after=game.state,
                snapshot_hash=state.simulator.state_hash(),
                events=tuple(
                    replace(event, data={**event.data, "game_over": game.game_over})
                    if event.type == "resolution_complete" else event
                    for event in result.events
                ),
            )
        winner = self._winner_from_game_over()
        self._last_winner = winner

        self.tick += 1
        result = RealtimeMatchTickResult(
            tick=current_tick,
            player_results=player_results,
            generated_attacks=generated,
            attack_diagnostics=diagnostics,
            dropped_ojama=dropped,
            winner=winner,
            snapshot_hash=self.state_hash(),
        )
        if self._public_snapshot_adapter is not None:
            self._public_snapshot_adapter.observe_tick(self, result)
        return result

    def public_snapshot(self, player_id: int = 0):
        """Enable public event observation and return an immutable nextgen value.

        Call before the first step to retain the complete episode history.
        Reset discards the observer; pre-install history is never synthesized.
        """
        if self._public_snapshot_adapter is None:
            from puyo_env.nextgen_public_snapshot import PublicVersusSnapshotAdapter

            self._public_snapshot_adapter = PublicVersusSnapshotAdapter()
        return self._public_snapshot_adapter.snapshot(self, player_id)

    def public_timing_history(self):
        """Return public packet/resolution event IDs for control and diagnostics."""
        if self._public_snapshot_adapter is None:
            self.public_snapshot()
        return self._public_snapshot_adapter.timing_history()

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

    def resolve_generated_attacks(
        self,
        generated: Mapping[str, int],
    ) -> dict[str, dict[str, int | bool]]:
        remaining: dict[str, int] = {}
        diagnostics: dict[str, dict[str, int | bool]] = {}
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
                    **(
                        {"garbage_ticks_remaining": self.player_states[agent].garbage_ticks_remaining}
                        if self.player_states[agent].garbage_ticks_remaining else {}
                    ),
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
                    "last_chain_end_score": (
                        self.player_states[agent].simulator.game.last_chain_end_score
                    ),
                    "last_chain_score_delta": (
                        self.player_states[agent].simulator.game.last_chain_score_delta
                    ),
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

    def replay_rules(self) -> dict[str, int]:
        return {
            "garbage_drop_ticks": self.garbage_drop_ticks,
            "attack_delay_ticks": self.attack_delay_ticks,
        }

    def all_clear_diagnostics(self) -> dict[str, object]:
        """Return versioned per-player diagnostics for runtime and replay consumers."""

        return {
            "schema_version": ALL_CLEAR_DIAGNOSTICS_SCHEMA_VERSION,
            "players": {
                agent: build_all_clear_diagnostics(
                    self.player_states[agent].simulator.game
                )
                for agent in self.possible_agents
            },
        }

    def _opponent(self, agent: str) -> str:
        if agent == "player_0":
            return "player_1"
        if agent == "player_1":
            return "player_0"
        raise KeyError(f"unknown agent: {agent}")

    def _attack_units_from_step(self, agent: str, result: RealtimeStepResult) -> int:
        score_delta = int(self._attack_metadata_from_step(result)["attack_score_delta"])
        if score_delta <= 0:
            return 0
        return self._attack_units_from_score(agent, score_delta)

    @staticmethod
    def _attack_metadata_from_step(result: RealtimeStepResult) -> dict[str, int | bool]:
        score_delta = 0
        all_clear_bonus_consumed = False
        all_clear_bonus_score = 0
        for event in result.events:
            if (
                event.type == "resolution_complete"
                and int(event.data.get("chain_count", 0)) > 0
            ):
                score_delta += int(event.data.get("attack_score_delta", 0))
                all_clear_bonus_consumed = (
                    all_clear_bonus_consumed
                    or bool(event.data.get("all_clear_bonus_consumed", False))
                )
                all_clear_bonus_score += int(event.data.get("all_clear_bonus_score", 0))
        return {
            "attack_score_delta": score_delta,
            "all_clear_bonus_consumed": all_clear_bonus_consumed,
            "all_clear_bonus_score": all_clear_bonus_score,
        }

    def _attack_units_from_score(self, agent: str, score_delta: int) -> int:
        state = self.player_states[agent]
        conversion = convert_score_to_ojama(
            score_delta,
            state.score_carry,
            self.target_score_per_ojama,
        )
        state.score_carry = conversion.carry
        return conversion.units

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

    @staticmethod
    def _completed_placement(result: RealtimeStepResult) -> bool:
        return any(event.type == "resolution_complete" for event in result.events)

    def _apply_due_ojama(self, agent: str, *, placement_boundary: bool) -> int:
        state = self.player_states[agent]
        game = state.simulator.game
        if game.game_over or not placement_boundary:
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
        if placed and self.garbage_drop_ticks:
            state.garbage_ticks_remaining = self.garbage_drop_ticks
            game.state = "garbage"
        if not game.field.get_puyo(2, VISIBLE_HEIGHT - 1).is_empty():
            game.game_over = True
            game.state = "gameover"
        return placed

    @property
    def ending(self) -> bool:
        """Whether top-out has frozen new play (including a completed match)."""
        return any(state.simulator.game.game_over for state in self.player_states.values())

    @property
    def resolution_pending(self) -> bool:
        if not self.ending:
            return False
        for state in self.player_states.values():
            game = state.simulator.game
            if game.game_over or game.state != "animate":
                continue
            if game.chain_count or game.animation_state == "vanish_flash":
                return True
            # A just-locked pair may still be falling into its first clear.
            # Pure placement/drop animation without a clear must not delay end.
            settled = copy.deepcopy(game.field)
            settled.drop_puyo()
            if settled.get_vanish_groups():
                return True
        return False

    @property
    def finished(self) -> bool:
        # Derive this from authoritative player state so replay/clone need no latch.
        return self.ending and not self.resolution_pending

    def _winner_from_game_over(self) -> str | None:
        if not self.finished:
            return None
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
