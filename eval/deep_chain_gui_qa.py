"""Validate applied policy plans against their public realtime requests.

Replay diagnostics are sparse: carry them forward, but count only controller
activations. A controller retry is recovery telemetry, never proof of a policy
plan replacement. Old replays remain readable but cannot satisfy this contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents.deep_chain_builder import DEEP_CHAIN_DECISION_INPUT_SCHEMA_VERSION
from puyo_env.realtime_ai import POLICY_DECISION_REPLAY_SCHEMA_VERSION


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def validate_policy_decision_history(
    replay: Mapping[str, Any], result: Mapping[str, Any], *, agent: str = "player_0"
) -> dict[str, Any]:
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    policy: Mapping[str, Any] = {}
    activated = 0
    replacements = 0
    duplicate_queries = 0
    seen_ids: set[str] = set()
    seen_plans: set[str] = set()
    last_controller: Mapping[str, Any] = {}

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(
        replay.get("policy_decision_schema_version") == POLICY_DECISION_REPLAY_SCHEMA_VERSION,
        "missing_or_unsupported_policy_decision_schema",
    )
    ticks = replay.get("ticks", [])
    if not isinstance(ticks, list):
        ticks = []
        errors.append("invalid_replay_ticks")
    for tick in ticks:
        tick = _mapping(tick)
        updates = _mapping(tick.get("policy_diagnostics"))
        if agent in updates:
            policy = _mapping(updates[agent])
        controller = _mapping(_mapping(tick.get("controller_diagnostics")).get(agent))
        last_controller = controller
        count = controller.get("decisions_activated")
        if not isinstance(count, int) or isinstance(count, bool) or count < activated:
            errors.append(f"tick {tick.get('tick')}: invalid_activation_counter")
            continue
        if count == activated:
            continue
        prefix = f"tick {tick.get('tick')}: "
        require(count == activated + 1, prefix + "missing_activation_history")
        activated = count
        decision = _mapping(controller.get("last_decision"))
        trace = _mapping(policy.get("decision_trace"))
        plan = _mapping(policy.get("plan"))
        steps = plan.get("steps", [])
        first = _mapping(steps[0]) if isinstance(steps, list) and steps else {}
        identity = _mapping(policy.get("decision_input"))
        request = _mapping(decision.get("decision_input"))
        decision_id = trace.get("decision_id")
        plan_id = plan.get("plan_id")
        action = policy.get("selected_action")
        reason = policy.get("replan_reason")
        root = identity.get("root_state_fingerprint")
        digest = identity.get("observation_digest")
        placement = decision.get("request_placement_count")

        require(
            isinstance(decision_id, str) and bool(decision_id)
            and decision_id not in seen_ids
            and decision_id == decision.get("policy_decision_id"),
            prefix + "missing_or_stale_decision_id",
        )
        require(
            identity.get("schema_version") == DEEP_CHAIN_DECISION_INPUT_SCHEMA_VERSION
            and isinstance(digest, str) and bool(digest)
            and isinstance(root, str) and bool(root) and identity == request,
            prefix + "missing_or_mismatched_observation",
        )
        require(
            isinstance(plan_id, str) and bool(plan_id) and plan_id == policy.get("plan_id")
            and root == plan.get("root_state_fingerprint")
            and root == first.get("root_state_fingerprint"),
            prefix + "stale_or_unbound_plan",
        )
        require(
            isinstance(action, int) and not isinstance(action, bool)
            and action == first.get("action") == plan.get("selected_root_action")
            == decision.get("action_index"),
            prefix + "plan_step_one_action_mismatch",
        )
        require(
            decision.get("outcome") == "activated"
            and decision.get("fallback") is False
            and isinstance(decision.get("activation_tick"), int)
            and decision["activation_tick"] == tick.get("tick"),
            prefix + "decision_not_applied_without_fallback",
        )
        require(
            policy.get("policy_id") == "deep_chain_builder"
            and _mapping(policy.get("profile")).get("name") == "reference"
            and _mapping(policy.get("backend")).get("backend") == "native"
            and _mapping(policy.get("fallback")).get("used") is False
            and policy.get("target_chain_count") == 6,
            prefix + "noncanonical_policy_decision",
        )
        require(
            reason == plan.get("replan_reason") == plan.get("update_reason")
            and reason in {"initial_plan", "new_observation", "plan_unchanged"},
            prefix + "inconsistent_replan_reason",
        )
        require(
            isinstance(placement, int) and not isinstance(placement, bool) and placement >= 0,
            prefix + "missing_request_placement_count",
        )
        if records:
            previous = records[-1]
            if digest == previous["observation_digest"]:
                duplicate_queries += 1
                require(
                    plan_id == previous["plan_id"] and action == previous["selected_action"]
                    and reason == "plan_unchanged" and placement == previous["placement"],
                    prefix + "duplicate_observation_changed_plan",
                )
            elif isinstance(placement, int) and isinstance(previous["placement"], int):
                require(placement >= previous["placement"], prefix + "placement_counter_regressed")
                if placement > previous["placement"]:
                    replaced = (
                        root != previous["root_state_fingerprint"]
                        and plan_id not in seen_plans and reason == "new_observation"
                    )
                    require(replaced, prefix + "stale_plan_after_placement")
                    replacements += int(replaced)
        else:
            require(reason == "initial_plan", prefix + "missing_initial_plan")
        records.append({
            "tick": tick.get("tick"),
            "decision_id": decision_id,
            "observation_digest": digest,
            "root_state_fingerprint": root,
            "plan_id": plan_id,
            "replan_reason": reason,
            "selected_action": action,
            "plan_step_one_action": first.get("action"),
            "placement": placement,
            "controller_request_tick": decision.get("request_tick"),
            "controller_activation_tick": decision.get("activation_tick"),
        })
        if isinstance(decision_id, str):
            seen_ids.add(decision_id)
        if isinstance(plan_id, str):
            seen_plans.add(plan_id)

    diagnostics = _mapping(result.get("diagnostics"))
    final_controller = _mapping(_mapping(diagnostics.get("controller")).get(agent))
    final_policy = _mapping(_mapping(diagnostics.get("policy")).get(agent))
    require(activated == len(records) >= 3, "fewer_than_three_applied_decisions")
    require(replacements >= 2, "fewer_than_two_placement_plan_replacements")
    require(
        final_controller.get("decisions_activated") == activated
        and all(final_controller.get(key) == last_controller.get(key) for key in (
            "decision_requests", "decisions_started", "placements_completed", "replans",
            "stale_decisions", "fallback_actions",
        )),
        "result_replay_controller_mismatch",
    )
    require(
        bool(policy) and final_policy == policy,
        "result_replay_policy_mismatch",
    )
    return {
        "schema_version": POLICY_DECISION_REPLAY_SCHEMA_VERSION,
        "passed": not errors,
        "errors": errors,
        "applied_decisions": len(records),
        "placement_plan_replacements": replacements,
        "duplicate_observation_queries": duplicate_queries,
        "controller_retries": final_controller.get("replans"),
        "controller_stale_decisions": final_controller.get("stale_decisions"),
        "records": records,
    }
