"""Public-only realtime boundary and versioned timing summaries.

The adapter is trusted boundary code; its returned values contain no runtime objects.
Hidden rows are deliberately unknown. Timing witnesses belong to the planner's
public search sidecar, never to the actor's input.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from itertools import islice
from typing import Literal

from agents.nextgen_contracts import (
    Contract, ExecutionContext, NumericEvidence, PUBLIC_CELL_TO_COLOR,
    PublicAttackPacket, PublicEvent, PublicPlayerState, PublicSnapshot,
    semantic_digest,
)
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, VISIBLE_HEIGHT
from src.core.realtime import RealtimeTimingConfig

PUBLIC_ADAPTER_SCHEMA = "puyo.nextgen.public_adapter.v1"
TIMING_SCHEMA = "puyo.nextgen.public_timing.v1"
_COLOR_TO_CELL = {color: index for index, color in enumerate(PUBLIC_CELL_TO_COLOR)}


@dataclass(frozen=True)
class TickInterval(Contract):
    """Absolute ticks or durations, with explicit public provenance.

    Both null bounds mean unknown. A null upper bound means no finite upper
    estimate. Even finite public estimates are conditional, not guarantees.
    """
    lower: int | None
    upper: int | None
    source: Literal["visible_exact", "public_estimate", "sampled_future"]
    provenance: str

    def _validate(self):
        if not self.provenance.strip():
            raise ValueError("interval provenance is required")
        if self.lower is None:
            if self.upper is not None:
                raise ValueError("unknown lower bound requires unknown upper bound")
        elif self.lower < 0 or (self.upper is not None and self.upper < self.lower):
            raise ValueError("invalid tick interval")


@dataclass(frozen=True)
class TimingProfile(Contract):
    runtime: RealtimeTimingConfig
    attack_delay_ticks: int
    garbage_drop_ticks: int
    max_ojama_drop: int
    target_score_per_ojama: int
    latency_mode: Literal["configured", "measured"]
    inference_latency_ticks: int
    timeout_ticks: int
    operation_cadence: TickInterval
    schema_version: Literal["puyo.nextgen.public_timing.v1"] = TIMING_SCHEMA

    def _validate(self):
        if min(self.attack_delay_ticks, self.garbage_drop_ticks,
               self.inference_latency_ticks, self.timeout_ticks) < 0:
            raise ValueError("negative timing configuration")
        if min(self.max_ojama_drop, self.target_score_per_ojama) <= 0:
            raise ValueError("positive match rules required")
        if self.operation_cadence.lower is not None and self.operation_cadence.lower < 1:
            raise ValueError("operation cadence must be positive")
        if any(type(getattr(self.runtime, f.name)) is not int or
               getattr(self.runtime, f.name) < (0 if f.name == "attack_delay_ticks" else 1)
               for f in fields(self.runtime)):
            raise ValueError("invalid runtime timing")

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        runtime = value.get("runtime")
        if isinstance(runtime, dict):
            if set(runtime) != {f.name for f in fields(RealtimeTimingConfig)}:
                raise ValueError("missing/unknown runtime timing fields")
            value["runtime"] = RealtimeTimingConfig(**runtime)
        return super().from_dict(value)

    @property
    def digest(self) -> str:
        return semantic_digest(self)

    @classmethod
    def from_match(cls, match, *, latency_mode="configured", inference_latency_ticks=0,
                   timeout_ticks=120, operation_cadence=None):
        return cls(match.timing, match.attack_delay_ticks, match.garbage_drop_ticks,
                   match.max_ojama_drop, match.target_score_per_ojama, latency_mode,
                   inference_latency_ticks, timeout_ticks,
                   operation_cadence or TickInterval(None, None, "public_estimate", "cadence_unavailable"))

    def execution_context(self, reachable_mask, request_tick: int) -> ExecutionContext:
        return ExecutionContext(tuple(reachable_mask), request_tick,
                                request_tick + self.timeout_ticks, TIMING_SCHEMA,
                                self.digest, self.latency_mode)


@dataclass(frozen=True)
class PublicPacketEvent(Contract):
    event_id: str
    player_id: int
    tick: int
    kind: Literal["arrival", "cancel", "drop"]
    packet_id: str
    amount: int


@dataclass(frozen=True)
class PublicResolutionEvent(Contract):
    event_id: str
    player_id: int
    tick: int
    chain_count: int
    generated: int
    canceled: int
    outgoing: int


@dataclass(frozen=True)
class PublicTimingHistory(Contract):
    """Public diagnostic/control sidecar. Never passed to build_features()."""
    packets: tuple[PublicPacketEvent, ...]
    resolutions: tuple[PublicResolutionEvent, ...]
    schema_version: Literal["puyo.nextgen.public_history.v1"] = "puyo.nextgen.public_history.v1"


class PublicVersusSnapshotAdapter:
    """Opt-in per-match public history, observed after authoritative tick resolution.

    Installed lazily by match.public_snapshot(). History starts at installation;
    no pre-install events are fabricated. Reset creates a fresh adapter. IDs are
    unique within this installation/episode, ordered by tick, player, event kind.
    """
    def __init__(self):
        self._events: list[PublicEvent] = []
        self._arrivals: set[str] = set()
        self._packet_events: list[PublicPacketEvent] = []
        self._resolutions: list[PublicResolutionEvent] = []
        self._before_attacks = {}
        self._before_drop = {}

    def timing_history(self) -> PublicTimingHistory:
        return PublicTimingHistory(tuple(self._packet_events), tuple(self._resolutions))

    def before_tick(self, match) -> None:
        self._before_attacks = {agent: self._packets(match, agent) for agent in match.possible_agents}

    def _packet_event(self, player_id, tick, kind, packet, amount):
        self._packet_events.append(PublicPacketEvent(
            f"{tick}:{player_id}:{kind}:{packet.packet_id}", player_id, tick,
            kind, packet.packet_id, amount,
        ))

    def _reductions(self, match, previous, tick, kind):
        for player_id, agent in enumerate(match.possible_agents):
            current = {p.packet_id: p.amount for p in self._packets(match, agent)}
            for packet in previous.get(agent, ()):
                reduction = packet.amount - current.get(packet.packet_id, 0)
                if reduction > 0:
                    self._packet_event(player_id, tick, kind, packet, reduction)

    @staticmethod
    def _packets(match, agent):
        # Same source/creation/arrival packets are one public batch. No private
        # packet object identity, queue index, RNG or engine hash enters the ID.
        batches = {}
        for packet in match.player_states[agent].incoming_attacks:
            key = (packet.source_agent, packet.created_tick, packet.arrival_tick)
            batches[key] = batches.get(key, 0) + packet.amount
        return tuple(PublicAttackPacket(
            f"{agent}:{source}:{created}:{arrival}", amount, arrival, None,
        ) for (source, created, arrival), amount in sorted(batches.items()) if amount > 0)

    @staticmethod
    def _player(match, agent):
        state = match.player_states[agent]
        game = state.simulator.game
        if not 0 <= state.score_carry < match.target_score_per_ojama:
            raise ValueError("score carry outside configured conversion threshold")
        # During tween/garbage the engine already stores landing destinations.
        # They are not yet the displayed board, and may depend on hidden rows or
        # private garbage RNG. Suppress them until the public animation finishes.
        obscured = game.state == "garbage" or (
            game.state == "animate" and game.animation_state == "drop_tween")
        board = tuple(
            tuple(None if y >= VISIBLE_HEIGHT or obscured else
                  _COLOR_TO_CELL[game.field.get_puyo(x, y).color]
                  for x in range(GRID_WIDTH))
            for y in reversed(range(GRID_HEIGHT))
        )
        pieces = []
        if game.current_puyo_1 is not None and game.current_puyo_2 is not None:
            pieces.append((_COLOR_TO_CELL[game.current_puyo_1.color],
                           _COLOR_TO_CELL[game.current_puyo_2.color]))
        # Queue may be larger in a fixture/future runtime: only NEXT/NEXT2 are public.
        pieces.extend((_COLOR_TO_CELL[a.color], _COLOR_TO_CELL[b.color])
                      for a, b in islice(game.next_puyo_queue, 2))
        return PublicPlayerState(board, tuple(pieces), game.state,
                                 PublicVersusSnapshotAdapter._packets(match, agent),
                                 state.score_carry, game.all_clear_bonus_pending,
                                 game.all_clear_achieved, game.all_clear_bonus_consumed)

    def snapshot(self, match, player_id: int = 0) -> PublicSnapshot:
        if type(player_id) is not int or player_id not in (0, 1):
            raise ValueError("player_id must be 0 or 1")
        return PublicSnapshot(self._player(match, f"player_{player_id}"),
                              self._player(match, f"player_{1 - player_id}"),
                              tuple(self._events))

    def observe_tick(self, match, result) -> None:
        self._reductions(match, self._before_drop, result.tick, "drop")
        def emit(player_id, kind):
            self._events.append(PublicEvent(
                f"{result.tick}:{player_id}:{kind}:{len(self._events)}",
                player_id, kind, result.tick, None, (),
            ))
        for player_id, agent in enumerate(match.possible_agents):
            step = result.player_results[agent]
            for event in step.events:
                if event.type == "lock":
                    emit(player_id, "placement")
                elif event.type == "resolution_complete":
                    diagnostic = result.attack_diagnostics[agent]
                    self._resolutions.append(PublicResolutionEvent(
                        f"{result.tick}:{player_id}:resolution", player_id, result.tick,
                        int(event.data.get("chain_count", 0)), int(diagnostic["generated"]),
                        int(diagnostic["canceled"]), int(diagnostic["outgoing"]),
                    ))
                    if event.data.get("chain_count", 0) > 0:
                        emit(player_id, "clear")
            if step.state_before != step.state_after:
                emit(player_id, "phase")
            if result.generated_attacks[agent]:
                emit(player_id, "attack")
            if result.dropped_ojama[agent]:
                emit(player_id, "drop")

    def observe_arrivals(self, match, tick) -> None:
        """Observe after cancellation and before drop removes incoming packets."""
        self._reductions(match, self._before_attacks, tick, "cancel")
        self._before_drop = {agent: self._packets(match, agent) for agent in match.possible_agents}
        for player_id, agent in enumerate(match.possible_agents):
            for packet in self._packets(match, agent):
                if packet.arrival_tick <= tick and packet.packet_id not in self._arrivals:
                    self._arrivals.add(packet.packet_id)
                    self._packet_event(player_id, tick, "arrival", packet, packet.amount)
                    self._events.append(PublicEvent(
                        f"{tick}:{player_id}:arrival:{len(self._events)}",
                        player_id, "arrival", tick, None, (),
                    ))


# The adapter produces exactly the PUYO-244 transport type.
PublicVersusSnapshot = PublicSnapshot


@dataclass(frozen=True)
class ResponseWitness(Contract):
    fire_start: TickInterval
    fire_complete: TickInterval
    generated_ojama: NumericEvidence
    required_ojama: NumericEvidence
    known_prefix: bool

    def _validate(self):
        if self.fire_start.lower is not None and self.fire_complete.lower is not None:
            if self.fire_complete.lower < self.fire_start.lower:
                raise ValueError("fire completion precedes start")
        for evidence in (self.generated_ojama, self.required_ojama):
            if evidence.value is not None and evidence.value < 0:
                raise ValueError("negative attack amount")


@dataclass(frozen=True)
class TimingSummary(Contract):
    timing_schema: str
    timing_digest: str
    latency_mode: Literal["configured", "measured"]
    deadline: TickInterval
    response: Literal["possible", "marginal", "impossible", "unknown"]
    threat: Literal["none", "potential", "pressing", "immediate"] | None
    first_fire_lower: NumericEvidence
    first_fire_upper: NumericEvidence
    response_witness: ResponseWitness | None
    opponent_next_operation: TickInterval | None

    def feature_summaries(self) -> dict[str, NumericEvidence]:
        """Only names admitted by PUYO-244; raw tick sidecar stays outside RL."""
        result = {}
        for name in ("none", "potential", "pressing", "immediate"):
            result[f"threat.{name}"] = NumericEvidence(
                None if self.threat is None else float(self.threat == name),
                "not_evaluated" if self.threat is None else "evaluated", "public_estimate")
        for name in ("possible", "marginal", "impossible", "unknown"):
            result[f"response.{name}"] = NumericEvidence(
                float(self.response == name), "evaluated", "public_estimate")
        result["first_fire_loss.lower"] = self.first_fire_lower
        result["first_fire_loss.upper"] = self.first_fire_upper
        return result


def _response(witness, deadline):
    if witness is None:
        return "unknown"
    generated, required = witness.generated_ojama, witness.required_ojama
    complete = witness.fire_complete
    if generated.value is None or required.value is None or complete.lower is None:
        return "unknown"
    conditional = (not witness.known_prefix or
                   any(x.source == "sampled_future" for x in (
                       generated, required, witness.fire_start, complete, deadline)) or
                   generated.status == "partial" or required.status == "partial")
    if conditional:
        return "marginal"
    if generated.value < required.value:
        return "impossible"
    if deadline.upper is not None and complete.lower > deadline.upper:
        return "impossible"
    if deadline.lower is None:
        return "unknown"
    if complete.upper is not None and complete.upper <= deadline.lower:
        return "possible"
    return "marginal"


def _first_fire(witness, next_operation, cadence):
    missing = NumericEvidence(None, "not_evaluated", "public_estimate")
    if witness is None or next_operation is None:
        return missing, missing
    start, end = witness.fire_start, witness.fire_complete
    intervals = (start, end, next_operation, cadence)
    if any(i.lower is None or i.upper is None for i in intervals):
        return missing, missing
    if cadence.lower < 1:
        raise ValueError("cadence must be positive")

    def count(tick, first, period):
        return 0 if tick < first else 1 + (tick - first) // period

    # Count completed opponent placements in (fire_start, fire_complete].
    lower = max(0, count(end.lower, next_operation.upper, cadence.upper)
                - count(start.upper, next_operation.lower, cadence.lower))
    upper = max(0, count(end.upper, next_operation.lower, cadence.lower)
                - count(start.lower, next_operation.upper, cadence.upper))
    source = "sampled_future" if any(i.source == "sampled_future" for i in intervals) else "public_estimate"
    return (NumericEvidence(float(lower), "evaluated", source),
            NumericEvidence(float(upper), "evaluated", source))


def derive_timing_summary(snapshot: PublicSnapshot, profile: TimingProfile, *,
                          request_tick: int, opponent_attack_candidate: bool | None = None,
                          landing_deadline: TickInterval | None = None,
                          response_witness: ResponseWitness | None = None,
                          opponent_next_operation: TickInterval | None = None) -> TimingSummary:
    """Derive summaries from public values, never from a simulator/future queue.

    Arrival is only a conservative lower bound on landing. A tighter landing
    interval must come from a public placement/response witness. Potential attack
    absence must be explicitly evaluated; None means unsearched, not safe.
    """
    if type(request_tick) is not int or request_tick < 0:
        raise ValueError("request_tick must be non-negative")
    packets = tuple(p for p in snapshot.own.attack_packets if p.amount and p.landed_tick is None)
    arrivals = [p.arrival_tick for p in packets if p.arrival_tick is not None]
    arrival_lower = max(request_tick, min(arrivals)) if arrivals and len(arrivals) == len(packets) else None
    deadline = landing_deadline or TickInterval(arrival_lower, None, "public_estimate",
                                                "arrival_is_only_landing_lower_bound")
    if arrival_lower is not None and deadline.lower is not None and deadline.lower < arrival_lower:
        raise ValueError("landing deadline precedes arrival/request")
    cadence = profile.operation_cadence
    if not packets:
        threat = None if opponent_attack_candidate is None else ("potential" if opponent_attack_candidate else "none")
    elif deadline.lower is None or cadence.upper is None:
        threat = None
    else:
        operations = max(0, deadline.lower - request_tick) // cadence.upper
        threat = "immediate" if operations <= 1 else "pressing" if operations <= 2 else "potential"
    lower, upper = _first_fire(response_witness, opponent_next_operation, cadence)
    return TimingSummary(TIMING_SCHEMA, profile.digest, profile.latency_mode, deadline,
                         _response(response_witness, deadline), threat, lower, upper,
                         response_witness, opponent_next_operation)
