"""Bounded public-prefix survival envelope, independent of template objectives.

Witnesses cover geometric placements through known pieces only. Hidden rows,
future movement and cadence remain public estimates; this is never a guarantee
about private future pieces or real-time execution. Every transition/drop is
charged to the existing response quota before evaluation.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from src.core.constants import VISIBLE_HEIGHT

SURVIVAL_NODE_LIMIT = 128
SURVIVAL_STATUS = {"witness": 1, "fatal": 2, "unknown": 3, "cutoff": 4, "deadline_unreachable": 5}


@dataclass(frozen=True)
class RootSurvival:
    action: int
    status: str
    root_chain: int | None = None
    witness: tuple[int, ...] = ()


def needs_probe(request):
    """Conservative no-choke bound, not a height-based choice of action.

    Within H known placements the central column gains at most 2H colored
    cells plus all public incoming garbage and occupied/unknown hidden
    central cells (an intentionally loose bound).
    Resolution only removes cells or moves them downward. Unknown visible
    cells cannot establish this bound. Hidden cells are not inferred empty.
    """
    own = request.public.own
    visible = own.visible_board[-VISIBLE_HEIGHT:]
    if any(row[2] is None for row in visible):
        return True
    height = max((y + 1 for y, row in enumerate(reversed(visible)) if row[2]), default=0)
    hidden = sum(row[2] != 0 for row in own.visible_board[:-VISIBLE_HEIGHT])
    hidden += max(0, 14 - len(own.visible_board))
    incoming = sum(p.amount for p in own.attack_packets if p.landed_tick is None)
    return height + hidden + 2 * len(own.known_pieces) + incoming >= VISIBLE_HEIGHT


def probe(request, state, roots, budget, *, timing=None, board_complete=False):
    # Local import keeps the shared-search/provider injection boundary acyclic.
    from agents.nextgen_response_search import (
        PublicResponseProvider, _Node, _consume, _due_options, _plus,
        drop_distributions, drop_public_garbage,
    )

    reachable = tuple(a for a in roots if request.execution.reachable_mask[a])
    known = request.public.own.known_pieces
    packets = tuple(p for p in request.public.own.attack_packets if p.amount and p.landed_tick is None)
    if not known or not reachable or not needs_probe(request):
        return {}, {"status": "not_needed", "nodes": 0, "horizon": len(known)}
    start_nodes = budget.nodes
    visible_complete = all(v is not None for row in request.public.own.visible_board[-VISIBLE_HEIGHT:] for v in row)
    reason = None
    if not visible_complete or (packets and timing is None):
        reason = "unknown"
    elif timing is not None and request.execution.latency_mode == "configured" and (
        request.execution.request_tick + timing.inference_latency_ticks > request.execution.timeout_tick
    ):
        reason = "deadline_unreachable"
    if reason:
        results = {a: RootSurvival(a, reason) for a in reachable}
    else:
        provider = PublicResponseProvider(timing) if timing is not None else None
        ready = request.execution.request_tick + (timing.inference_latency_ticks if timing else 0)
        initial = _Node(state, (), packets, request.public.own.score_carry,
                        (ready, ready if request.execution.latency_mode == "configured" else None),
                        not board_complete)

        def move(node, action):
            depth = len(node.plan)
            yield  # One placement including failed/fatal resolution.
            pair = tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in known[depth])
            result = transition(node.state, pair, action)
            if depth == 0:
                root_chains[action] = result.chain_count
            if not result.valid or result.game_over:
                return "fatal", ()
            plan = node.plan + (c.PlanStep(action, known[depth], "public_known"),)
            remaining, carry, end = node.packets, node.carry, node.ready
            if provider:
                budget.feature_evaluations += 1
                generated, carry = divmod(result.attack_score_delta + carry, timing.target_score_per_ojama)
                remaining = _consume(remaining, generated)
                _, end = provider._times(node, result)
            options = _due_options(remaining, end) if remaining else ((),)
            outcomes, representative = [], ()
            for eligible in options:
                count = min(timing.max_ojama_drop, sum(p.amount for p in remaining if p.packet_id in eligible)) if remaining else 0
                for columns in drop_distributions(count):
                    after, placed = result.state, 0
                    if count:
                        yield  # Each possible public remainder distribution.
                        after, placed = drop_public_garbage(after, columns)
                    if after.game_over or not legal_action_indices(after):
                        status, path = "fatal", ()
                    elif len(plan) == len(known):
                        status, path = "witness", tuple(s.action for s in plan)
                    else:
                        child = _Node(after, plan, _consume(remaining, placed, eligible), carry,
                                      _plus(end, timing.garbage_drop_ticks, timing.garbage_drop_ticks) if count else end,
                                      node.conditional)
                        status, path = yield from continuation(child)
                    outcomes.append(status)
                    if path and not representative:
                        representative = path
            # For unknown drop/cadence, require a surviving continuation for
            # EVERY possible outcome. Mixed outcomes are unknown, never death.
            if all(s == "witness" for s in outcomes):
                return "witness", representative
            if all(s == "fatal" for s in outcomes):
                return "fatal", ()
            return "unknown", ()

        def continuation(node):
            outcomes = []
            for action in legal_action_indices(node.state):
                status, path = yield from move(node, action)
                if status == "witness":
                    return status, path
                outcomes.append(status)
            return ("fatal" if all(s == "fatal" for s in outcomes) else "unknown"), ()

        root_chains, results = {}, {}
        pending = deque((a, move(initial, a)) for a in reachable)
        # Round robin: every root is checked before one root consumes its NEXT
        # search budget. Yield precedes work; finishing a charged step is free.
        limit = min(SURVIVAL_NODE_LIMIT, budget.quota - budget.nodes)
        while pending:
            action, iterator = pending.popleft()
            try:
                next(iterator)
            except StopIteration as done:
                status, path = done.value
                results[action] = RootSurvival(action, status, root_chains.get(action), path)
                continue
            if budget.nodes - start_nodes >= limit or not budget.consume():
                results[action] = RootSurvival(action, "cutoff", root_chains.get(action))
                iterator.close()
            else:
                pending.append((action, iterator))
    active = any(v.status == "fatal" for v in results.values()) and any(v.status == "witness" for v in results.values())
    statuses = {v.status for v in results.values()}
    summary = "bounded_witness" if "witness" in statuses else (
        "unavoidable" if statuses == {"fatal"} and board_complete else
        "cutoff" if "cutoff" in statuses else "unknown"
    )
    return results, {
        "status": summary, "active": active, "nodes": budget.nodes - start_nodes,
        "horizon": len(known), "board_complete": board_complete,
        "source": "visible_exact" if board_complete else "public_estimate",
        "scope": "known_prefix_geometric_continuations; replan_each_spawn",
        "safety_guarantee": False,
        "roots": [{"action": a, "status": v.status, "root_chain": v.root_chain,
                   "witness": list(v.witness)} for a, v in sorted(results.items())],
        "unreachable_roots": [a for a in roots if a not in reachable],
    }


def evidence_for(result, *, board_complete):
    source = "visible_exact" if board_complete else "public_estimate"
    safe = 1 if result.status == "witness" else 0 if result.status == "fatal" else None
    values = {
        "survival_safe": safe, "survival_status": SURVIVAL_STATUS[result.status],
        "survival_root_chain": result.root_chain,
        "survival_depth": len(result.witness) if result.witness else None,
    }
    return tuple(c.NamedEvidence(name, c.NumericEvidence(
        value, "not_evaluated" if value is None else "evaluated" if board_complete else "partial", source,
    )) for name, value in values.items())


def value(candidate, name):
    return next((e.evidence.value for e in candidate.evidence if e.name == name), None)


def apply_envelope(batch, selection):
    """Keep learned/teacher choices unless the bounded crisis requires repair.

    The batch owns candidate order. Overrides are rule behavior, so no learned
    log-probability is misattributed to the actually requested action.
    """
    selected = selection.validate_batch(batch)
    if not any(value(v, "survival_safe") == 0 for v in batch.candidates if v.root_reachable):
        return selection
    row = batch.tactics[c.TACTIC_IDS.index("build_main")]
    preferred = next((v for v in batch.candidates if v.candidate_id == row.best_id), None)
    if preferred is None or value(preferred, "survival_safe") != 1:
        return selection
    if value(selected, "survival_safe") == 1:
        # Existing deliberate fire/cancel/counter judgments remain policy choices.
        # Safety does not veto a safe large chain merely because quiet play exists.
        if selection.selected_tactic_id not in ("build_main", "build_template"):
            return selection
        if not value(selected, "survival_root_chain"):
            return replace(selection, reason="survival_safe_nonfire")
        if value(preferred, "survival_root_chain"):
            return replace(selection, reason="legitimate_survival_exception")
    reason = "legitimate_survival_exception" if value(preferred, "survival_root_chain") else "survival_safe_nonfire"
    return c.Selection("build_main", preferred.candidate_id, batch.digest, "rule", None, None, None, reason)
