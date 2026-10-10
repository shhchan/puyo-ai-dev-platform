"""Bounded public-prefix survival envelope, independent of template objectives.

Witnesses cover geometric placements through known pieces only. Hidden rows,
future movement and cadence remain public estimates; this is never a guarantee
about private future pieces or real-time execution. Every transition/drop is
charged to the existing response quota before evaluation.
"""
from __future__ import annotations

from collections import deque
import heapq
from dataclasses import dataclass, replace

from agents import nextgen_contracts as c
from agents.compact_search import find_landing_y, legal_action_indices, transition
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, VISIBLE_HEIGHT

SURVIVAL_NODE_LIMIT = 128
SURVIVAL_STATUS = {"witness": 1, "fatal": 2, "unknown": 3, "cutoff": 4, "deadline_unreachable": 5}
_COLUMN_MASKS = tuple(sum(1 << (y * GRID_WIDTH + x) for y in range(GRID_HEIGHT))
                      for x in range(GRID_WIDTH))


def continuation_actions(state):
    """Exclude a provably impossible crossing, without adding control search.

    A fresh axis spawns in column 2; horizontal moves/side kicks change its
    column by at most one. It cannot occupy any height in a completely full
    column. This is a necessary condition only, not a reachability certificate.
    Row-14 cells can remain suspended after clears, so a top cell alone is NOT
    treated as a solid wall. Root reachability remains the scheduler's mask.
    """
    occupied = 0
    for plane in state.planes:
        occupied |= plane
    walls = {x for x, mask in enumerate(_COLUMN_MASKS) if occupied & mask == mask}
    return tuple(a for a in legal_action_indices(state)
                 if not any(x in walls for x in range(
                     min(2, PLACEMENT_ACTIONS[a].axis_x),
                     max(2, PLACEMENT_ACTIONS[a].axis_x) + 1)))


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


def probe(request, state, roots, budget, *, timing=None, board_complete=False, transition_cache=None, reuse_transitions=False):
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
    reused_nodes = 0
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
            nonlocal reused_nodes
            depth = len(node.plan)
            yield  # One placement including failed/fatal resolution.
            pair = tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in known[depth])
            key = node.state, pair, action
            if reuse_transitions and transition_cache is not None and key in transition_cache:
                result = transition_cache[key]
                reused_nodes += 1
            else:
                result = transition(node.state, pair, action)
                if transition_cache is not None:
                    transition_cache[key] = result
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
            for action in continuation_actions(node.state):
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
        **({"reused_transition_nodes": reused_nodes} if reuse_transitions else {}),
        "source": "visible_exact" if board_complete else "public_estimate",
        "scope": "known_prefix_geometric_continuations; replan_each_spawn",
        "safety_guarantee": False,
        "roots": [{"action": a, "status": v.status, "root_chain": v.root_chain,
                   "witness": list(v.witness)} for a, v in sorted(results.items())],
        "unreachable_roots": [a for a in roots if a not in reachable],
    }



def inferred_state(request, state):
    """Overlay only a request-bound, certain public deduction for this probe.

    The native/shared state and actor features retain the original visible
    input. Pending attack branches need a branching control proof; a single
    representative witness cannot certify those, so use the old path there.
    """
    inference = request.known_inference()
    if inference is None or any(p.amount and p.landed_tick is None
                                for p in request.public.own.attack_packets):
        return None
    visible_mask = (1 << (VISIBLE_HEIGHT * GRID_WIDTH)) - 1
    planes = [p & visible_mask for p in state.planes]
    public_plane = {1: 0, 2: 1, 3: 2, 4: 3, 5: 5}
    for y, row in enumerate(inference.hidden_rows, VISIBLE_HEIGHT):
        for x, cell in enumerate(row):
            if cell:
                planes[public_plane[cell]] |= 1 << (y * GRID_WIDTH + x)
    return replace(state, planes=tuple(planes))


class _ProofCutoff(Exception):
    pass


class _ControlProof:
    """Request-local geometry cache and shared, precharged work accounting."""

    def __init__(self, budget, start_nodes):
        self.budget, self.start_nodes = budget, start_nodes
        self.cache = {}
        self.charged = {"control": 0, "placement": 0, "terminal": 0}
        self.hits = 0

    def charge(self, kind):
        if self.budget.nodes - self.start_nodes >= SURVIVAL_NODE_LIMIT or not self.budget.consume():
            raise _ProofCutoff
        self.charged[kind] += 1

    def reachable(self, state, pair, action):
        from puyo_env.action_planner import PLANNER_ACTIONS, _transition_piece_state
        from src.core.game import GameState
        from src.core.puyo import Puyo

        # Collision/control rules depend on occupied cells, not their colors.
        # Fresh spawn and zero interpolation are fixed for every cache entry.
        key = state.occupied_mask, action
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        game = GameState(seed=0)
        game.next_puyo_queue.clear()
        game.puyo_sequence = None
        game.current_puyo_1, game.current_puyo_2 = map(Puyo, pair)
        for y, row in enumerate(state.to_color_grid()):
            for x, color in enumerate(row):
                game.field.grid[y][x] = Puyo(color)
        pose = PLACEMENT_ACTIONS[action]
        landing = game.find_landing_y(pose.axis_x, pose.rotation)
        if landing is None:
            self.cache[key] = False
            return False
        target = pose.axis_x, landing, pose.rotation

        def priority(value):
            return abs(value[0] - target[0]) + abs(value[1] - target[1]) + int(value[2] != target[2])

        start = game.puyo_x, game.puyo_y, game.puyo_rot, 0
        queue, seen, sequence = [(priority(start), 0, start)], {start}, 0
        while queue:
            self.charge("control")
            _, _, value = heapq.heappop(queue)
            if value[:3] == target:
                self.cache[key] = True
                return True
            for operation in PLANNER_ACTIONS:
                nxt = _transition_piece_state(game, value, operation)
                if nxt is not None and nxt not in seen:
                    seen.add(nxt)
                    sequence += 1
                    heapq.heappush(queue, (priority(nxt), sequence, nxt))
        self.cache[key] = False
        return False

    def terminal(self, state, pair):
        # Sufficient for ONE color-independent placement, not long-term safety.
        for action in _terminal_actions(state):
            if self.reachable(state, pair, action):
                self.charge("terminal")
                return action
        return None


def _terminal_actions(state):
    # Necessary conditions can reject a path before spending control nodes.
    if any(state.occupied_mask & (1 << (y * GRID_WIDTH + 2)) for y in (11, 12, 13)):
        return
    central = sum(bool(state.occupied_mask & (1 << (y * GRID_WIDTH + 2))) for y in range(13))
    from src.core.game import GameState
    for action in continuation_actions(state):
        pose = PLACEMENT_ACTIONS[action]
        y = find_landing_y(state, pose)
        dx, dy = GameState.get_sub_puyo_offset(None, pose.rotation)
        cells = ((pose.axis_x, y), (pose.axis_x + dx, y + dy))
        if central + sum(x == 2 and cy < 13 for x, cy in cells) <= 11:
            yield action


class _SavedWorkBudget:
    """Spend only omitted duplicate work; never reserve additional quota.

    Original logical debits stay reserved, preserving the old round-robin,
    cutoff and response-provider allowance. Cache hits did not execute work;
    their prepaid credits can fund new work, never refund executed work.
    """

    def __init__(self, budget, saved):
        self.saved = saved
        self.nodes = budget.nodes - saved

    def consume(self):
        if not self.saved:
            return False
        self.saved -= 1
        self.nodes += 1
        return True


def _alternate_witness(state, pairs, results, ordered, cache, proof):
    trials, certified, witness = [], None, ()

    def visit(current, path):
        if len(path) == len(pairs):
            if next(_terminal_actions(current), None) is None:
                return None
            current = state
            for depth, action in enumerate(path):
                if depth and not proof.reachable(current, pairs[depth], action):
                    return None
                current = cache[current, pairs[depth], action].state
            terminal = proof.terminal(current, pairs[0])
            return (path, terminal) if terminal is not None else None
        for action in ((root,) if not path else continuation_actions(current)):
            key = current, pairs[len(path)], action
            if key not in cache:
                proof.charge("placement")
                cache[key] = transition(*key)
            result = cache[key]
            if result.valid and not result.game_over:
                found = visit(result.state, path + (action,))
                if found is not None:
                    return found
        return None

    for root in ordered:
        if root not in results or results[root].status != "witness":
            continue
        try:
            found = visit(state, ())
        except _ProofCutoff:
            trials.append({"root": root, "status": "unknown_cutoff"})
            break
        trials.append({"root": root, "status": "certified" if found else "unknown",
                       "witness": list(found[0]) if found else [],
                       "terminal_action": found[1] if found else None})
        if found:
            certified, witness = root, found[0]
            break
    refined = dict(results)
    if certified is not None:
        for trial in trials:
            root = trial["root"]
            refined[root] = replace(results[root], witness=witness) if root == certified else replace(
                results[root], status="unknown", witness=())
    return refined, certified, trials


def refine_inferred(request, state, results, diagnostics, budget, ranked_roots, transition_cache):
    """Check high-ranked bounded witnesses within the SAME survival quota.

    A failed witness is unknown, not a proof of death. If no terminal witness
    is certified (including cutoff), retain the original finite-horizon
    envelope; diagnostics distinguish that fallback from a control proof.
    """
    if not results:
        return results, diagnostics
    start_nodes = budget.nodes - diagnostics["nodes"]
    proof = _ControlProof(budget, start_nodes)
    pairs = tuple(tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in pair)
                  for pair in request.public.own.known_pieces)
    ordered = list(dict.fromkeys((*ranked_roots, *results)))
    ordered.sort(key=lambda a: 0 if a in results and results[a].status == "witness"
                 and not results[a].root_chain else 1)
    # Landed garbage remains pressure after the incoming packet disappears.
    # Only inspect already charged root transitions. Pending/unknown input
    # cannot establish this recovery preference, and a quiet witness is not
    # thereby declared fatal. The usual control/terminal proof is still needed.
    recovery = {}
    if (request.known_inference() is not None and state.planes[5]
            and not any(p.amount and p.landed_tick is None for p in request.public.own.attack_packets)
            and any(v.status == "fatal" and request.execution.reachable_mask[a]
                    for a, v in results.items())):
        for action, root in results.items():
            result = transition_cache.get((state, pairs[0], action))
            if (root.status == "witness" and root.root_chain
                    and request.execution.reachable_mask[action] and result is not None
                    and result.valid and not result.game_over and result.chain_count > 0
                    and result.garbage_cleared_count > 0
                    and result.state.planes[5].bit_count() < state.planes[5].bit_count()):
                recovery[action] = result
        rank = {action: index for index, action in enumerate(ordered)}
        preferred = sorted(recovery, key=lambda a: (
            -recovery[a].garbage_cleared_count, -recovery[a].chain_count, rank[a]))
        ordered = preferred + [a for a in ordered if a not in recovery]
    trials, failed, certified, cache_hits, cutoff = [], [], None, 0, False
    for action in ordered:
        root = results.get(action)
        if root is None or root.status != "witness":
            continue
        current, terminal, reachable = state, None, True
        try:
            for depth, step in enumerate(root.witness):
                if depth and not proof.reachable(current, pairs[depth], step):
                    reachable = False
                    break
                key = current, pairs[depth], step
                if key in transition_cache:
                    cache_hits += 1
                else:
                    proof.charge("placement")
                    transition_cache[key] = transition(current, pairs[depth], step)
                result = transition_cache[key]
                if not result.valid or result.game_over:
                    reachable = False
                    break
                current = result.state
            if reachable:
                terminal = proof.terminal(current, pairs[0])
        except _ProofCutoff:
            cutoff = True
            trials.append({"root": action, "status": "unknown_cutoff"})
            break
        status = "certified" if terminal is not None else "unknown_terminal" if reachable else "unknown_control"
        trials.append({"root": action, "status": status, "terminal_action": terminal})
        if terminal is not None:
            certified = action
            break
        failed.append(action)
    refined = dict(results)
    if certified is not None:
        for action in failed:
            refined[action] = replace(results[action], status="unknown", witness=())
    details = {
        "status": "certified" if certified is not None else "unknown_cutoff" if cutoff else "unknown",
        "certified_root": certified, "trials": trials, "charged": proof.charged,
        "placement_cache_hits": cache_hits, "placement_cache_entries": len(transition_cache),
        "control_cache_hits": proof.hits,
        "cache_scope": "request_local; full_state_pair_action; occupied_mask_target_for_geometry",
        "scope": "public_inferred_known_prefix_control_and_one_color_independent_placement",
        "safety_guarantee": False, "fallback": certified is None,
    }
    saved = diagnostics.get("reused_transition_nodes", 0)
    if saved and certified is None and request.known_inference() is not None and not any(
            p.amount and p.landed_tick is None for p in request.public.own.attack_packets):
        credit = _SavedWorkBudget(budget, saved)
        alternate = _ControlProof(credit, start_nodes)
        refined, extra_root, extra_trials = _alternate_witness(
            state, pairs, results,
            [a for a in ordered if request.execution.reachable_mask[a]],
            transition_cache, alternate)
        details["alternate_prefix"] = {
            "certified_root": extra_root, "trials": extra_trials,
            "charged": alternate.charged, "actual_nodes": credit.nodes - start_nodes,
            "logical_nodes": budget.nodes - start_nodes, "unused_saved_nodes": credit.saved,
            "scope": "same_public_state_pair_action_cache; first_lock_reachable_mask",
        }
        if extra_root is not None:
            certified = extra_root
            failed = [v["root"] for v in extra_trials if v["root"] != certified]
            details.update(status="certified", certified_root=certified, fallback=False)
    if recovery:
        recovered = recovery.get(certified)
        details["landed_garbage_recovery"] = {
            "eligible_roots": preferred,
            "preferred_root": certified if recovered is not None else None,
            "garbage_before": state.planes[5].bit_count(),
            "garbage_removed": recovered.garbage_cleared_count if recovered is not None else None,
            "scope": "build_main_only; bounded_control_and_terminal; not_long_term_safety",
        }
    return refined, {**diagnostics, "source": "public_history_inference",
                     "nodes": budget.nodes - start_nodes, "control_proof": details,
                     "active": diagnostics.get("active", False) or (certified is not None and bool(failed)),
                     "roots": [{"action": a, "status": v.status, "root_chain": v.root_chain,
                                "witness": list(v.witness)} for a, v in sorted(refined.items())]}

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
