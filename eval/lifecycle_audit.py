"""Pure lifecycle audit helpers shared by realtime QA and benchmark evidence."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


LIFECYCLE_AUDIT_SCHEMA_VERSION = "puyo.lifecycle_audit.v1"
PLAYERS = ("player_0", "player_1")


def audit_realtime_lifecycle(
    *,
    initial_all_clear_diagnostics: Mapping[str, Any] | None,
    ticks: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit all-clear lifecycle transitions and bonus-inclusive attacks."""

    initial_players = (
        initial_all_clear_diagnostics.get("players", {})
        if isinstance(initial_all_clear_diagnostics, Mapping)
        else {}
    )
    per_player: dict[str, dict[str, Any]] = {}
    previous_pending: dict[str, bool] = {}
    for agent in PLAYERS:
        initial = initial_players.get(agent, {})
        board_empty = bool(initial.get("board_empty"))
        achieved = bool(initial.get("all_clear_achieved"))
        pending = bool(initial.get("all_clear_bonus_pending"))
        previous_pending[agent] = pending
        per_player[agent] = {
            "initial_empty": board_empty,
            "initial_empty_false_positives": int(board_empty and achieved),
            "achieved": achieved,
            "achieved_count": int(achieved),
            "pending": pending,
            "pending_ticks": int(pending),
            "consumed": False,
            "consumed_count": 0,
            "double_consumptions": 0,
            "bonus_attack_generated": 0,
            "bonus_attack_canceled": 0,
            "bonus_attack_outgoing": 0,
        }

    asymmetric_achieved = False
    tick_count = 0
    for tick in ticks:
        tick_count += 1
        all_clear_players = tick.get("all_clear_diagnostics", {}).get("players", {})
        attack_players = tick.get("attack_diagnostics", {})
        achieved_count = 0
        for agent in PLAYERS:
            state = all_clear_players.get(agent, {})
            attack = attack_players.get(agent, {})
            achieved = bool(state.get("all_clear_achieved"))
            pending = bool(state.get("all_clear_bonus_pending"))
            consumed = bool(
                state.get("all_clear_bonus_consumed")
                or attack.get("all_clear_bonus_consumed")
            )
            achieved_count += int(achieved)
            report = per_player[agent]
            report["achieved"] |= achieved
            report["achieved_count"] += int(achieved)
            report["pending"] |= pending
            report["pending_ticks"] += int(pending)
            report["consumed"] |= consumed
            report["consumed_count"] += int(consumed)
            if consumed:
                report["double_consumptions"] += int(not previous_pending[agent])
                report["bonus_attack_generated"] += int(attack.get("generated", 0))
                report["bonus_attack_canceled"] += int(attack.get("canceled", 0))
                report["bonus_attack_outgoing"] += int(attack.get("outgoing", 0))
            previous_pending[agent] = pending
        asymmetric_achieved |= achieved_count == 1

    return {
        "schema_version": LIFECYCLE_AUDIT_SCHEMA_VERSION,
        "initial_snapshot_present": bool(initial_players),
        "ticks_audited": tick_count,
        "players": per_player,
        "asymmetric_achieved": asymmetric_achieved,
    }
