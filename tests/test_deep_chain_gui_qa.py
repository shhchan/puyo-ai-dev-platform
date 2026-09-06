import copy
import unittest

from agents.deep_chain_builder import DEEP_CHAIN_DECISION_INPUT_SCHEMA_VERSION
from eval.deep_chain_gui_qa import validate_policy_decision_history
from puyo_env.realtime_ai import POLICY_DECISION_REPLAY_SCHEMA_VERSION


def gui_evidence():
    """Three placements, sparse diagnostic ticks, and no controller recovery."""
    ticks = []
    for index in range(3):
        identity = {
            "schema_version": DEEP_CHAIN_DECISION_INPUT_SCHEMA_VERSION,
            "observation_digest": f"observation-{index}",
            "root_state_fingerprint": f"root-{index}",
        }
        reason = "initial_plan" if index == 0 else "new_observation"
        policy = {
            "policy_id": "deep_chain_builder",
            "profile": {"name": "reference"},
            "backend": {"backend": "native"},
            "target_chain_count": 6,
            "fallback": {"used": False},
            "decision_trace": {"decision_id": f"decision-{index}"},
            "decision_input": identity,
            "plan_id": f"plan-{index}",
            "replan_reason": reason,
            "selected_action": 4,
            "plan": {
                "plan_id": f"plan-{index}",
                "selected_root_action": 4,
                "root_state_fingerprint": f"root-{index}",
                "replan_reason": reason,
                "update_reason": reason,
                "steps": [{"action": 4, "root_state_fingerprint": f"root-{index}"}],
            },
        }
        controller = {
            "decisions_started": index + 1,
            "decision_requests": index + 1,
            "decisions_activated": index + 1,
            "placements_completed": index,
            "replans": 0,
            "stale_decisions": 0,
            "fallback_actions": 0,
            "last_decision": {
                "policy_decision_id": f"decision-{index}",
                "decision_input": copy.deepcopy(identity),
                "request_placement_count": index,
                "action_index": 4,
                "fallback": False,
                "outcome": "activated",
                "request_tick": index * 10,
                "activation_tick": index * 10 + 1,
            },
        }
        ticks.append({
            "tick": index * 10 + 1,
            "policy_diagnostics": {"player_0": policy},
            "controller_diagnostics": {"player_0": controller},
        })
        ticks.append({
            "tick": index * 10 + 2,
            "policy_diagnostics": {},
            "controller_diagnostics": {"player_0": copy.deepcopy(controller)},
        })
    result = {
        "schema_version": "puyo.gui_qa.v1",
        "models": {"player_0": {
            "deep_chain_profile": "reference",
            "deep_chain_backend": "native",
            "deep_chain_target_chain": 6,
        }},
        "diagnostics": {
            "policy": {"player_0": copy.deepcopy(policy)},
            "controller": {"player_0": copy.deepcopy(controller)},
        },
        "plan_overlay_player_0": True,
    }
    replay = {
        "format": "puyo-realtime-match-v1",
        "policy_decision_schema_version": POLICY_DECISION_REPLAY_SCHEMA_VERSION,
        "ticks": ticks,
    }
    return result, replay


class TestDeepChainGuiQa(unittest.TestCase):
    def test_normal_placements_pass_with_zero_retries_and_sparse_diagnostics(self):
        result, replay = gui_evidence()
        summary = validate_policy_decision_history(replay, result)
        self.assertTrue(summary["passed"], summary["errors"])
        self.assertEqual(summary["applied_decisions"], 3)
        self.assertEqual(summary["placement_plan_replacements"], 2)
        self.assertEqual(summary["controller_retries"], 0)

    def test_bad_intermediate_decision_fails_even_with_valid_final_plan(self):
        cases = (
            ("observation", lambda p, c: p.pop("decision_input"), "mismatched_observation"),
            ("request", lambda p, c: c["last_decision"].pop("decision_input"), "mismatched_observation"),
            ("root", lambda p, c: p["plan"].update(root_state_fingerprint="stale"), "unbound_plan"),
            ("step", lambda p, c: p["plan"]["steps"][0].update(action=5), "action_mismatch"),
            ("applied", lambda p, c: c["last_decision"].update(action_index=5), "action_mismatch"),
            ("id", lambda p, c: c["last_decision"].update(policy_decision_id="old"), "decision_id"),
            ("fallback", lambda p, c: p["fallback"].update(used=True), "noncanonical"),
            ("backend", lambda p, c: p["backend"].update(backend="python"), "noncanonical"),
            ("scheduled", lambda p, c: c["last_decision"].update(outcome="scheduled"), "not_applied"),
            ("placement", lambda p, c: c["last_decision"].pop("request_placement_count"), "placement_count"),
        )
        for name, corrupt, expected in cases:
            with self.subTest(name=name):
                result, replay = gui_evidence()
                tick = replay["ticks"][2]
                corrupt(tick["policy_diagnostics"]["player_0"], tick["controller_diagnostics"]["player_0"])
                summary = validate_policy_decision_history(replay, result)
                self.assertFalse(summary["passed"])
                self.assertTrue(any(expected in error for error in summary["errors"]), summary)

    def test_reused_plan_id_after_placement_fails_even_if_roots_are_updated(self):
        result, replay = gui_evidence()
        policy = replay["ticks"][2]["policy_diagnostics"]["player_0"]
        policy["plan_id"] = policy["plan"]["plan_id"] = "plan-0"
        summary = validate_policy_decision_history(replay, result)
        self.assertIn("tick 11: stale_plan_after_placement", summary["errors"])

    def test_duplicate_query_is_distinct_from_placement_plan_replacement(self):
        result, replay = gui_evidence()
        duplicate = copy.deepcopy(replay["ticks"][0])
        duplicate["tick"] = 4
        policy = duplicate["policy_diagnostics"]["player_0"]
        policy["decision_trace"]["decision_id"] = "duplicate-query"
        policy["replan_reason"] = "plan_unchanged"
        policy["plan"].update(replan_reason="plan_unchanged", update_reason="plan_unchanged")
        controller = duplicate["controller_diagnostics"]["player_0"]
        controller["decisions_activated"] = 2
        controller["last_decision"].update(policy_decision_id="duplicate-query", activation_tick=4)
        for tick in replay["ticks"][2:]:
            tick["controller_diagnostics"]["player_0"]["decisions_activated"] += 1
        result["diagnostics"]["controller"]["player_0"]["decisions_activated"] += 1
        replay["ticks"].insert(2, duplicate)
        summary = validate_policy_decision_history(replay, result)
        self.assertTrue(summary["passed"], summary["errors"])
        self.assertEqual(summary["duplicate_observation_queries"], 1)
        self.assertEqual(summary["placement_plan_replacements"], 2)
        policy["selected_action"] = 5
        summary = validate_policy_decision_history(replay, result)
        self.assertIn("tick 4: duplicate_observation_changed_plan", summary["errors"])

    def test_missing_history_and_final_result_mismatch_fail_closed(self):
        for mode in ("old_schema", "empty", "missing_activation", "final_action", "final_count"):
            with self.subTest(mode=mode):
                result, replay = gui_evidence()
                if mode == "old_schema":
                    replay.pop("policy_decision_schema_version")
                elif mode == "empty":
                    replay["ticks"] = []
                elif mode == "missing_activation":
                    del replay["ticks"][2:4]
                elif mode == "final_action":
                    result["diagnostics"]["policy"]["player_0"]["selected_action"] = 5
                else:
                    result["diagnostics"]["controller"]["player_0"]["decisions_activated"] += 1
                self.assertFalse(validate_policy_decision_history(replay, result)["passed"])


if __name__ == "__main__":
    unittest.main()
