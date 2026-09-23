"""Versioned placement parity: public projection never substitutes for state."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from agents.compact_search import CompactSearchState
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor

PARITY_CONTRACT_VERSION = "puyo.simulator_parity.v2"
BOARD_STATE_CONTRACT_VERSION = "puyo.board_state.ghost_row.v1"


def state_fingerprint(state: CompactSearchState) -> str:
    return hashlib.sha256(state.to_bytes()).hexdigest()[:24]


def board_names(state: CompactSearchState) -> list[list[str]]:
    return [[color.name for color in row] for row in state.to_color_grid()]


def _valid_board(board: Any) -> bool:
    colors = {color.name for color in PuyoColor if color != PuyoColor.WALL}
    return (
        isinstance(board, (list, tuple))
        and len(board) == GRID_HEIGHT
        and all(
            isinstance(row, (list, tuple))
            and len(row) == GRID_WIDTH
            and all(isinstance(cell, str) and cell in colors for cell in row)
            for row in board
        )
    )


def compare_transition(
    *,
    action: int,
    predicted: Mapping[str, Any] | None,
    actual: Any,
    root_state: CompactSearchState,
    actual_state: CompactSearchState,
    board_after: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Compare a plan's first transition against complete authoritative state.

    Missing/malformed evidence fails closed. Row 14 color/occupancy, lifecycle
    state and root identity are required even when the 13-row projection agrees.
    This covers a placement boundary, not realtime movement/timing or queues.
    """

    step = predicted or {}
    expected_board = step.get("predicted_board")
    boards_valid = _valid_board(expected_board) and _valid_board(board_after)
    cells = []
    if boards_valid:
        cells = [
            {"x": x, "y": y, "predicted": expected_board[y][x], "actual": color}
            for y, row in enumerate(board_after)
            for x, color in enumerate(row)
            if expected_board[y][x] != color
        ]
    components = {
        "selected_plan_step": predicted is not None,
        "board_shape": boards_valid,
        "public_board": boards_valid
        and not any(cell["y"] < GRID_HEIGHT - 1 for cell in cells),
        "ghost_row": boards_valid
        and not any(cell["y"] == GRID_HEIGHT - 1 for cell in cells),
        "action": step.get("action") == int(action),
        "valid": step.get("valid") == bool(actual.valid),
        "chain_count": step.get("predicted_chain_count") == int(actual.chain_count),
        "score_delta": step.get("predicted_score") == int(actual.score_delta),
        "game_over": step.get("game_over") == bool(actual.game_over),
        "root_state": step.get("root_state_fingerprint")
        == state_fingerprint(root_state),
        "complete_state": step.get("state_fingerprint")
        == state_fingerprint(actual_state),
        "authoritative_board": _valid_board(board_after)
        and list(map(list, board_after)) == board_names(actual_state),
    }
    mismatches = [key for key, matched in components.items() if not matched]
    return {
        "contract_version": PARITY_CONTRACT_VERSION,
        "board_state_contract_version": BOARD_STATE_CONTRACT_VERSION,
        "passed": not mismatches,
        "mismatches": mismatches,
        "components": components,
        "cell_differences": cells,
        "predicted_state_fingerprint": str(step.get("state_fingerprint", "")),
        "actual_state_fingerprint": state_fingerprint(actual_state),
    }
