"""Receipt based nextgen labels shared by live versus and replay views."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


TACTIC_LABELS = {
    "build_main": "大連鎖構築",
    "build_template": "土台構築",
    "cancel": "相殺",
    "counter": "カウンター",
    "decisive_short_attack": "短期攻撃",
    "fire_main": "本線発火",
}
TEMPLATE_LABELS = {"gtr": "GTR", "daa": "だぁ積み", "persian": "ペルシャ式"}


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def nextgen_receipt_summary(
    policy_diagnostics: Mapping[str, Any] | None,
    controller_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Read the scheduler's final receipt; ignore a worker's tentative result."""
    policy = _map(policy_diagnostics)
    decision = _map(_map(controller_diagnostics).get("last_decision"))
    if decision and decision.get("nextgen_diagnostics") is None and decision.get("outcome") in {"fallback", "timeout", "stale"}:
        return None
    diagnostics = _map(decision.get("nextgen_diagnostics")) or _map(policy.get("nextgen"))
    receipt = _map(diagnostics.get("receipt"))
    if not receipt:
        return None
    request = _map(diagnostics.get("request"))
    identity = _map(request.get("identity"))
    phase = _map(_map(request.get("control")).get("phase"))
    selection = _map(diagnostics.get("selection"))
    template_selection = _map(policy.get("template_selection"))
    candidate = _map(template_selection.get("candidate"))
    phase_after = _map(policy.get("template_phase"))
    own = _map(_map(request.get("public")).get("own"))
    packets = own.get("attack_packets", ())
    incoming = sum(int(_map(packet).get("amount", 0)) for packet in packets if isinstance(packet, Mapping))
    tactics = _map(diagnostics.get("batch")).get("tactics", ())
    response_margin = None
    for tactic_row in tactics if isinstance(tactics, (list, tuple)) else ():
        row = _map(tactic_row)
        if row.get("tactic_id") not in ("cancel", "counter"):
            continue
        evidence = {
            item.get("name"): _map(item.get("evidence")).get("value")
            for item in row.get("evidence", ())
            if isinstance(item, Mapping)
        }
        if evidence.get("fire_end_upper") is not None and evidence.get("deadline_lower") is not None:
            response_margin = float(evidence["deadline_lower"]) - float(evidence["fire_end_upper"])
            break
    tactic = selection.get("selected_tactic_id")
    active_phase = bool(phase.get("active") or phase_after.get("phase_id"))
    template = (phase.get("template_id") or candidate.get("template_id")) if active_phase else None
    remaining = phase_after.get("decision_limit")
    if remaining is not None:
        remaining = int(remaining) - int(phase_after.get("consumed_decisions", 0))
    else:
        remaining = phase.get("remaining_decisions", 0)
    return {
        "episode_id": identity.get("episode_id"),
        "request_id": identity.get("request_id"),
        "decision_id": identity.get("decision_id"),
        "tactic_id": tactic,
        "tactic": TACTIC_LABELS.get(tactic, str(tactic or "-")),
        "template_id": template,
        "template": TEMPLATE_LABELS.get(template, str(template or "自由構築")),
        "variant": candidate.get("variant_id") if template else None,
        "phase_id": phase.get("phase_id"),
        "remaining": max(0, int(remaining)),
        "reason": selection.get("reason") or "-",
        "switch_reason": phase_after.get("exit_reason"),
        "outcome": receipt.get("outcome"),
        "receipt_reason": receipt.get("reason"),
        "incoming": incoming,
        "response_margin": response_margin,
        "requested_action": receipt.get("requested_action"),
        "executed_action": receipt.get("executed_action"),
        "request_tick": receipt.get("request_tick"),
        "activation_tick": receipt.get("activation_tick"),
    }


def history_entries_for_tick(tick: Mapping[str, Any], seen: dict[str, Any]) -> list[dict[str, Any]]:
    """Append each receipt and public event once, using replay-safe tick data."""
    entries: list[dict[str, Any]] = []
    number = int(tick.get("tick", 0))
    previous_tick = seen.get("last_tick")
    if previous_tick is not None and number > previous_tick + 1:
        entries.append({"tick": previous_tick + 1, "to_tick": number - 1, "agent": "all", "kind": "gap"})
    seen["last_tick"] = number
    policies = _map(tick.get("policy_diagnostics"))
    controllers = _map(tick.get("controller_diagnostics"))
    nextgen_agents = set(tick.get("nextgen_agents", ()))
    for agent, controller in controllers.items():
        summary = nextgen_receipt_summary(_map(policies.get(agent)), _map(controller))
        if summary is None:
            decision = _map(_map(controller).get("last_decision"))
            if agent in nextgen_agents and decision.get("outcome") in {"fallback", "timeout", "stale"}:
                token = (decision.get("request_tick"), decision.get("completion_tick"), decision.get("reason"))
                if seen.get(f"fallback:{agent}") != token:
                    entries.append({
                        "tick": number,
                        "agent": agent,
                        "kind": "fallback",
                        "outcome": decision.get("outcome"),
                        "reason": decision.get("reason"),
                        "requested_action": decision.get("requested_action"),
                        "executed_action": decision.get("executed_action"),
                    })
                    seen[f"fallback:{agent}"] = token
            continue
        token = (summary["episode_id"], summary["request_id"], summary["outcome"])
        if seen.get(f"receipt:{agent}") == token:
            continue
        previous = seen.get(f"tactic:{agent}")
        previous_phase = seen.get(f"phase:{agent}")
        entries.append({
            "tick": number,
            "agent": agent,
            "kind": "decision",
            "previous_tactic": previous,
            **summary,
            "reselected": previous_phase is not None and previous_phase != summary["phase_id"],
        })
        seen[f"receipt:{agent}"] = token
        if summary["outcome"] == "activated":
            seen[f"tactic:{agent}"] = summary["tactic_id"]
            seen[f"phase:{agent}"] = summary["phase_id"]
    for agent, events in _map(tick.get("public_events")).items():
        for event in events if isinstance(events, list) else ():
            event = _map(event)
            kind = event.get("type")
            if kind in {"resolution_complete", "arrival", "cancel", "drop", "lock"}:
                entries.append({
                    "tick": number,
                    "agent": agent,
                    "kind": "event",
                    "event": kind,
                    "event_data": dict(_map(event.get("data"))),
                })
    return entries
