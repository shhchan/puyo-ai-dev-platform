"""Display-only public-prefix projection of the selected nextgen root.

The candidate remains the execution contract. A representative continuation is
one search witness, not a promise to execute the next two placements.
"""

from __future__ import annotations

from agents import nextgen_contracts as c
from agents.compact_search import transition
from agents.nextgen_shared_search import _public_state
from src.core.constants import PuyoColor

PREVIEW_STEPS = 3


def build_plan_preview(request, candidate, search, selection):
    """Replay at most three already-searched public placements; never search."""
    known = request.public.own.known_pieces
    actions = [step.action for step in candidate.plan]
    pieces = [step.piece for step in candidate.plan]
    public_steps = [step.provenance == "public_known" for step in candidate.plan]
    source = "selected_candidate"
    reason = "candidate_prefix_short"
    scenario_id = None
    shared = search.shared_result
    if len(actions) == 1 and shared is not None:
        node = shared.representatives.get(candidate.root_action)
        sequence = next(
            (
                seq
                for seq in shared.scenario_sequences
                if node is not None and seq.scenario_id == node.scenario_id
            ),
            None,
        )
        if node is None or sequence is None:
            reason = "representative_unavailable"
        elif not node.path or node.path[0] != candidate.root_action:
            reason = "representative_root_mismatch"
        else:
            inverse = {color: cell for cell, color in enumerate(c.PUBLIC_CELL_TO_COLOR)}
            actions = list(node.path[:PREVIEW_STEPS])
            pieces = [
                tuple(inverse[color] for color in sequence.pair_at(i))
                for i in range(min(len(actions), len(known)))
            ]
            public_steps = [i < sequence.known_pair_count for i in range(len(actions))]
            source = "selected_root_representative"
            reason = "representative_prefix_short"
            scenario_id = node.scenario_id
    elif len(actions) == 1:
        reason = "search_unavailable"

    # Response witnesses can include an intervening garbage drop. A plain
    # placement replay cannot reconstruct that event from action IDs alone.
    if any(
        packet.amount and packet.landed_tick is None
        for packet in request.public.own.attack_packets
    ):
        actions, pieces = actions[:1], pieces[:1]
        reason = "incoming_event_requires_replan"

    state, complete = _public_state(request)
    steps = []
    for index, action in enumerate(actions[:PREVIEW_STEPS]):
        if index >= len(known):
            reason = "public_prefix_exhausted"
            break
        if not public_steps[index]:
            reason = "public_prefix_exhausted"
            break
        if index >= len(pieces) or pieces[index] != known[index]:
            reason = "public_piece_mismatch"
            break
        pair = tuple(c.PUBLIC_CELL_TO_COLOR[cell] for cell in pieces[index])
        result = transition(state, pair, action, capture_visuals=True)
        if not result.valid:
            reason = "invalid_public_projection"
            break
        cells = [
            {"x": x, "y": y, "color": color.name}
            for y, row in enumerate(result.placement_board)
            for x, color in enumerate(row)
            if color != PuyoColor.EMPTY and state.color_at(x, y) == PuyoColor.EMPTY
        ]
        steps.append(
            {
                "step_index": index,
                "action": action,
                "axis_x": result.action.axis_x,
                "axis_y": result.axis_y,
                "rotation": result.action.rotation.name,
                "known_tsumo": True,
                "scenario": "visible",
                "scenario_id": scenario_id,
                "tsumo": [color.name for color in pair],
                "placement_cells": cells,
                "predicted_board": [
                    [cell.name for cell in row] for row in result.state.to_color_grid()
                ],
                "predicted_chain_count": result.chain_count,
                "predicted_score": result.score_delta,
                "state_fingerprint": c.semantic_digest(result.state.to_bytes().hex()),
                "reference_only": True,
                "continuation_guaranteed": False,
                "board_complete": complete,
            }
        )
        state = result.state
        if result.game_over:
            reason = "projected_game_over"
            break

    if len(steps) == PREVIEW_STEPS and reason != "projected_game_over":
        reason = "public_prefix_reference"
    plan_id = (
        "nextgen-preview:"
        + c.semantic_digest(
            {
                "candidate_id": candidate.candidate_id,
                "steps": steps,
                "source": source,
            }
        )[:24]
    )
    metadata = {
        "status": "available"
        if len(steps) == PREVIEW_STEPS
        else "partial"
        if steps
        else "unavailable",
        "reason": reason,
        "requested_steps": PREVIEW_STEPS,
        "available_steps": len(steps),
        "source": source,
        "reference_only": True,
        "continuation_guaranteed": False,
        "board_complete": complete,
    }
    plan = {
        "schema_version": "n-turn-plan-v1",
        "plan_id": plan_id,
        "candidate_id": candidate.candidate_id,
        "request_identity": request.identity.to_dict(),
        "public_snapshot_digest": request.public.digest,
        "root_action": candidate.root_action,
        "strategy": "nextgen_tactic_manager",
        "selected_tactic_id": selection.selected_tactic_id,
        "max_steps": PREVIEW_STEPS,
        "visible_steps": len(steps),
        "steps": steps,
        "execution_queue": False,
        "replan_reason": "public_decision_projection",
        **metadata,
    }
    return {
        "plan": plan,
        "plan_id": plan_id,
        "replan_reason": plan["replan_reason"],
        "plan_preview": metadata,
    }
