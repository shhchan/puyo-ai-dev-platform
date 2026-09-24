"""Bounded response witnesses from public packets, pieces and timing only.

A packet arrival enables a drop at a placement boundary; it is never a hard
fire deadline. Resolve -> convert/cancel -> first drop -> NEXT is modeled here.
Unknown remainder columns are enumerated without reading the runtime RNG.
Plans are conditional estimates and must be replanned after the observed drop.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from itertools import combinations

from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState, legal_action_indices, transition
from agents.nextgen_shared_search import (
    ResponseDropOutcome,
    ResponseProposal,
    ResponseSearchResult,
    ResponseTrace,
)
from puyo_env.nextgen_public_snapshot import TimingProfile
from src.core.constants import VISIBLE_HEIGHT


@dataclass(frozen=True)
class _Node:
    state: CompactSearchState
    plan: tuple[c.PlanStep, ...]
    packets: tuple[c.PublicAttackPacket, ...]
    carry: int
    ready: tuple[int | None, int | None]
    conditional: bool
    first_drop_count: int = 0
    first_drop_columns: tuple[int, ...] = ()


def _consume(packets, amount, eligible=None):
    """Retain public packet identity; equal arrivals retain public queue order."""
    remaining = []
    for packet in sorted(
        packets, key=lambda p: (p.arrival_tick is None, p.arrival_tick or 0)
    ):
        used = (
            min(packet.amount, amount)
            if eligible is None or packet.packet_id in eligible
            else 0
        )
        amount -= used
        if packet.amount > used:
            remaining.append(replace(packet, amount=packet.amount - used))
    return tuple(remaining)


def _sum(packets):
    return sum(p.amount for p in packets)


def _plus(interval, lower, upper):
    return (
        None if interval[0] is None or lower is None else interval[0] + lower,
        None if interval[1] is None or upper is None else interval[1] + upper,
    )


def _due_options(packets, end):
    """Enumerate all arrival thresholds compatible with the timing estimate."""
    known = sorted({p.arrival_tick for p in packets if p.arrival_tick is not None})
    ticks = {end[0] if end[0] is not None else 0}
    ticks.update(
        t
        for t in known
        if (end[0] is None or t >= end[0]) and (end[1] is None or t <= end[1])
    )
    unknown = tuple(p.packet_id for p in packets if p.arrival_tick is None)
    options = set()
    for tick in ticks:
        due = tuple(
            p.packet_id
            for p in packets
            if p.arrival_tick is not None and p.arrival_tick <= tick
        )
        options.add(due)
        if unknown:
            # Unknown public arrival times cannot yield a guaranteed witness.
            options.add(due + unknown)
    return tuple(sorted(options))


def drop_distributions(count):
    """At most C(6,3)=20 equipossible remainder sets; no private random draws."""
    rows, remainder = divmod(count, 6)
    return tuple(
        tuple(rows + int(x in columns) for x in range(6))
        for columns in combinations(range(6), remainder)
    )


def drop_public_garbage(state, columns):
    """Mirror Field.drop_ojama, including visible holes and saturated columns."""
    planes = list(state.planes)
    occupied = state.occupied_mask
    placed = 0
    for x, count in enumerate(columns):
        for _ in range(count):
            y = next(
                (y for y in range(VISIBLE_HEIGHT) if not occupied & (1 << (y * 6 + x))),
                None,
            )
            if y is None:
                continue
            bit = 1 << (y * 6 + x)
            planes[-1] |= bit
            occupied |= bit
            placed += 1
    return replace(
        state,
        planes=tuple(planes),
        game_over=state.game_over
        or bool(occupied & (1 << ((VISIBLE_HEIGHT - 1) * 6 + 2))),
    ), placed


class PublicResponseProvider:
    """Enumerate public prefix (default three pairs) within the response quota.

    Cadence is a configured public estimate for placement lock, not a low-level
    movement guarantee. Resolution bounds add flash/drop animation estimates.
    Only known root reachability is authoritative at request time. The scheduler
    still owns measured completion, activation, timeout and stale rejection.
    """

    def __init__(self, timing: TimingProfile, *, horizon: int = 3):
        if type(horizon) is not int or not 1 <= horizon <= 3:
            raise ValueError("response horizon must be within public three pairs")
        self.timing = timing
        self.horizon = horizon

    def _times(self, node, result):
        cadence, runtime = self.timing.operation_cadence, self.timing.runtime
        start = _plus(node.ready, cadence.lower, cadence.upper)
        # Include initial settling and the final no-clear resolution boundary.
        lower = result.chain_count * runtime.vanish_flash_ticks + 1
        upper = lower + (result.chain_count + 1) * (runtime.chain_drop_tween_ticks + 2)
        return start, _plus(start, lower, upper)

    def search(self, context, budget):
        request = context.request
        if (
            request.execution.timing_digest != self.timing.digest
            or request.execution.timing_schema != self.timing.schema_version
            or request.execution.latency_mode != self.timing.latency_mode
        ):
            raise ValueError("response timing profile mismatch")
        packets = tuple(
            p
            for p in request.public.own.attack_packets
            if p.amount and p.landed_tick is None
        )
        threat = bool(packets)
        known = request.public.own.known_pieces[: self.horizon]
        roots = tuple(
            a for a in context.legal_roots if request.execution.reachable_mask[a]
        )
        reason = "not_found_within_budget" if threat else "no_public_threat"
        if not roots or not known:
            reason = "no_reachable_root" if context.legal_roots else "no_legal_root"
            return ResponseSearchResult(
                status="not_evaluated", cancel_reason=reason, counter_reason=reason
            )
        if (
            request.execution.latency_mode == "configured"
            and request.execution.request_tick + self.timing.inference_latency_ticks
            > request.execution.timeout_tick
        ):
            return ResponseSearchResult(
                status="evaluated",
                cancel_reason="deadline_unreachable",
                counter_reason="deadline_unreachable",
            )
        ready = request.execution.request_tick + self.timing.inference_latency_ticks
        queue = deque(
            [
                _Node(
                    context.root_state,
                    (),
                    packets,
                    request.public.own.score_carry,
                    (
                        ready,
                        ready
                        if request.execution.latency_mode == "configured"
                        else None,
                    ),
                    not context.board_complete,
                )
            ]
        )
        proposals, traces = [], []
        cutoff = False
        drop_attempts = 0
        uncertain_drop = False
        undropped_leaf = False
        while queue and not cutoff:
            node = queue.popleft()
            depth = len(node.plan)
            actions = roots if not depth else legal_action_indices(node.state)
            pair = tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in known[depth])
            for action in actions:
                if not budget.consume():
                    cutoff = True
                    break
                result = transition(node.state, pair, action)
                if not result.valid or result.game_over:
                    continue
                budget.feature_evaluations += 1
                plan = node.plan + (c.PlanStep(action, known[depth], "public_known"),)
                generated, carry = divmod(
                    result.attack_score_delta + node.carry,
                    self.timing.target_score_per_ojama,
                )
                remaining = _consume(node.packets, generated)
                canceled = _sum(node.packets) - _sum(remaining)
                start, end = self._times(node, result)
                options = _due_options(remaining, end)
                following_drops = []
                if node.first_drop_count and result.chain_count:
                    for eligible in options:
                        count = min(
                            self.timing.max_ojama_drop,
                            sum(p.amount for p in remaining if p.packet_id in eligible),
                        )
                        for columns in drop_distributions(count):
                            if not budget.consume():
                                cutoff = True
                                break
                            after, placed = drop_public_garbage(result.state, columns)
                            following_drops.append(
                                ResponseDropOutcome(
                                    columns,
                                    placed,
                                    _consume(remaining, placed, eligible),
                                    after.game_over,
                                    node.conditional
                                    or len(options) > 1
                                    or count % 6 != 0,
                                )
                            )
                        if cutoff:
                            break
                if result.chain_count:
                    tactics = ["decisive_short_attack"]
                    if threat and node.packets and not node.first_drop_count:
                        tactics.append("cancel")
                    if node.first_drop_count:
                        tactics.append("counter")
                    conditional = node.conditional
                    status = "partial" if conditional else "evaluated"
                    source = "public_estimate" if conditional else "visible_exact"
                    evidence = []
                    values = {
                        "chain_count": result.chain_count,
                        "score": result.attack_score_delta,
                        "generated": generated,
                        "canceled": canceled,
                        "outgoing": generated - canceled,
                        "fire_depth": len(plan),
                        "response_surplus": generated - _sum(node.packets),
                        "trigger_survives": 1,
                        "counter_after_first_drop": int(bool(node.first_drop_count)),
                    }
                    for name, value in values.items():
                        evidence.append(
                            c.NamedEvidence(
                                name, c.NumericEvidence(value, status, source)
                            )
                        )
                    if following_drops:
                        evidence.append(
                            c.NamedEvidence(
                                "fatal_rate",
                                c.NumericEvidence(
                                    sum(d.game_over for d in following_drops)
                                    / len(following_drops),
                                    "partial",
                                    "public_estimate",
                                ),
                            )
                        )
                    # The current resolution is the cancellation boundary, even
                    # when packet arrival precedes fire start. Never use arrival
                    # as a hard deadline or claim a measured activation receipt.
                    for name, value in zip(
                        (
                            "fire_start_lower",
                            "fire_start_upper",
                            "fire_end_lower",
                            "fire_end_upper",
                            "deadline_lower",
                            "deadline_upper",
                        ),
                        (*start, *end, *end),
                    ):
                        evidence.append(
                            c.NamedEvidence(
                                name,
                                c.NumericEvidence(
                                    value,
                                    "partial" if value is not None else "not_evaluated",
                                    "public_estimate",
                                ),
                            )
                        )
                    proposals.append(
                        ResponseProposal(
                            plan,
                            tuple(tactics),
                            tuple(evidence),
                            generated * 1000 - len(plan),
                        )
                    )
                    for tactic in tactics:
                        traces.append(
                            ResponseTrace(
                                plan,
                                tactic,
                                node.first_drop_count,
                                node.first_drop_columns,
                                remaining,
                                max(
                                    (
                                        min(
                                            self.timing.max_ojama_drop,
                                            sum(
                                                p.amount
                                                for p in remaining
                                                if p.packet_id in opt
                                            ),
                                        )
                                        for opt in options
                                    ),
                                    default=0,
                                ),
                                conditional,
                                start,
                                end,
                                tuple(following_drops),
                            )
                        )
                # A counter is exactly one move after the first drop. Further
                # packets remain in the trace; never drop the total queue first.
                if node.first_drop_count or len(plan) >= len(known):
                    undropped_leaf |= not node.first_drop_count
                    if cutoff:
                        break
                    continue
                for eligible in options:
                    count = min(
                        self.timing.max_ojama_drop,
                        sum(p.amount for p in remaining if p.packet_id in eligible),
                    )
                    uncertain = node.conditional or len(options) > 1
                    if not count:
                        queue.append(
                            _Node(result.state, plan, remaining, carry, end, uncertain)
                        )
                        continue
                    distributions = drop_distributions(count)
                    uncertain_drop |= uncertain or len(distributions) > 1
                    for columns in distributions:
                        if not budget.consume():
                            cutoff = True
                            break
                        dropped, placed = drop_public_garbage(result.state, columns)
                        drop_attempts += 1
                        if dropped.game_over:
                            continue
                        queue.append(
                            _Node(
                                dropped,
                                plan,
                                _consume(remaining, placed, eligible),
                                carry,
                                _plus(
                                    end,
                                    self.timing.garbage_drop_ticks,
                                    self.timing.garbage_drop_ticks,
                                ),
                                uncertain or len(distributions) > 1,
                                placed,
                                columns,
                            )
                        )
                    if cutoff:
                        break
                if cutoff:
                    break
        # Same action plan can have several conditional remainder witnesses.
        # Keep a conservative representative, with all branches in the sidecar.
        grouped = {}
        for proposal in proposals:
            old = grouped.get(proposal.plan)
            if old is None:
                grouped[proposal.plan] = proposal
            else:
                merged = tuple(
                    t for t in c.TACTIC_IDS if t in old.tactics or t in proposal.tactics
                )
                chosen = min((old, proposal), key=lambda p: p.priority)
                grouped[proposal.plan] = replace(chosen, tactics=merged)
        counter_reason = reason
        if (
            drop_attempts
            and not cutoff
            and not uncertain_drop
            and not undropped_leaf
            and not any("counter" in p.tactics for p in proposals)
        ):
            counter_reason = "trigger_blocked"
        unknown_arrival = any(p.arrival_tick is None for p in packets)
        return ResponseSearchResult(
            tuple(grouped.values()),
            "partial"
            if cutoff or unknown_arrival or not context.board_complete
            else "evaluated",
            "response_quota"
            if cutoff
            else "public_arrival_unknown"
            if unknown_arrival
            else None,
            reason,
            counter_reason,
            tuple(traces),
        )
