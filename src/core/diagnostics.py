"""Stable runtime diagnostics shared by environments, replays, and UIs."""

from __future__ import annotations

from typing import Any


ALL_CLEAR_DIAGNOSTICS_SCHEMA_VERSION = "puyo.all_clear_diagnostics.v1"
ALL_CLEAR_DIAGNOSTIC_FIELDS = (
    "board_empty",
    "all_clear_achieved",
    "all_clear_bonus_pending",
    "all_clear_bonus_consumed",
)


def build_all_clear_diagnostics(game) -> dict[str, bool]:
    """Return the authoritative all-clear state for one player."""

    return {
        "board_empty": bool(game.is_board_empty()),
        "all_clear_achieved": bool(game.all_clear_achieved),
        "all_clear_bonus_pending": bool(game.all_clear_bonus_pending),
        "all_clear_bonus_consumed": bool(game.all_clear_bonus_consumed),
    }


def build_all_clear_runtime_info(game, opponent_game) -> dict[str, Any]:
    """Flatten own/opponent all-clear diagnostics into an environment info."""

    own = build_all_clear_diagnostics(game)
    opponent = build_all_clear_diagnostics(opponent_game)
    return {
        "all_clear_diagnostics_schema_version": ALL_CLEAR_DIAGNOSTICS_SCHEMA_VERSION,
        **own,
        **{f"opponent_{name}": opponent[name] for name in ALL_CLEAR_DIAGNOSTIC_FIELDS},
    }
