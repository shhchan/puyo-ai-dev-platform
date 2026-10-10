"""Offline gate evidence must not turn a candidate/receipt into an actual clear."""

import copy
import os
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from agents.nextgen_shared_search import ResponseSearchResult
from eval.nextgen_attack_response_gate import (
    assess,
    audit_replay,
    confirm_public_resolution,
    original_cell_flow,
    public_trace,
    registered_cases,
    replay_run,
    run_case,
    setup_match,
    shape_preserved,
    unavoidable_oracle,
)
from eval.nextgen_response_fixtures import make_request

SOURCE = Path(os.environ.get("PUYO277_SOURCE", "/tmp/puyo275-haipuyo.txt"))


def request_for(case):
    codes = {color.name: code for code, color in enumerate(c.PUBLIC_CELL_TO_COLOR)}
    board = tuple(
        reversed(
            [tuple(codes[v] for v in row) for row in case["board_bottom_up"]]
            + [(0,) * 6] * 2
        )
    )
    return make_request(
        board=board,
        pieces=tuple(tuple(codes[v] for v in pair) for pair in case["public_prefix"]),
        incoming=0,
    )


class PublicAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = registered_cases()

    def case(self, name):
        return next(c for c in self.config["cases"] if c["id"] == name)

    def test_frozen_cohort_and_unavoidable_excludes_success(self):
        self.assertEqual(self.config["pattern_ids"], [0, 1, 32768, 65535])
        self.assertEqual(len(self.config["cases"]), 28)
        for pid in self.config["pattern_ids"]:
            proof = unavoidable_oracle(self.case(f"unavoidable_loss-{pid}"))
            self.assertEqual(proof["status"], "unavoidable")
            self.assertEqual(len(proof["roots"]), 22)
        self.assertEqual(
            unavoidable_oracle(self.case("cancel_boundary-0"))["status"], "not_proven"
        )

    def test_prepared_fireable_and_unreachable_are_separate(self):
        case = self.case("independent_subchain-0")
        req = request_for(case)
        search = SimpleNamespace(
            response_result=ResponseSearchResult(), diagnostics={"survival": {}}
        )
        first = public_trace(req, case, search)
        self.assertTrue(first["prepared"])
        self.assertTrue(first["fireable_current_actions"])
        blocked = replace(
            req, execution=replace(req.execution, reachable_mask=(False,) * 22)
        )
        second = public_trace(blocked, case, search)
        self.assertTrue(second["prepared"])
        self.assertEqual(second["preparation_status"], "prepared")
        self.assertEqual(second["fireable_current_actions"], [])
        self.assertTrue(
            any(
                r["uses_secondary"] and r["mainline_shape_preserved"]
                for r in first["public_roots"]
            )
        )

    def test_extended_observations_preserve_every_original_input(self):
        extended = registered_cases(extended=True)
        self.assertEqual(len(extended["cases"]), 8)
        for case in extended["cases"]:
            original = self.case(case["id"])
            self.assertEqual(case["max_resolutions"], 8)
            self.assertEqual(case["max_ticks"], 1800)
            changed = {k for k in case if case[k] != original.get(k)}
            expected = {"max_resolutions"}
            if case["id"].startswith("preserve_mainline-"):
                expected.add("minimum_attack_observation_tick")
                self.assertEqual(case["minimum_attack_observation_tick"], 240)
                result = assess(case, "attack", [], [], False, {"status": "not_proven"})
                self.assertIn("minimum_arrival_observation_window_incomplete", result["issues"])
            self.assertEqual(changed, expected)

    def test_missing_key_is_a_counterfactual_not_an_available_piece(self):
        search = SimpleNamespace(
            response_result=ResponseSearchResult(), diagnostics={"survival": {}}
        )
        for pid in self.config["pattern_ids"]:
            case = self.case(f"mainline_without_key-{pid}")
            trace = public_trace(request_for(case), case, search)
            self.assertTrue(
                any(
                    r["uses_secondary"]
                    and r["chain_count"] == 1
                    and r["key_counterfactual_chain"] == 2
                    for r in trace["public_roots"]
                )
            )

    def test_original_identity_follows_later_chain_and_gravity(self):
        case = self.case("independent_subchain-0")
        from agents.nextgen_shared_search import _public_state

        state, _ = _public_state(request_for(case))
        from src.core.constants import PuyoColor

        pair = (PuyoColor.BLUE, PuyoColor.YELLOW)
        results = [(a, transition(state, pair, a)) for a in legal_action_indices(state)]
        action, result = next((a, r) for a, r in results if r.chain_count >= 2)
        flow = original_cell_flow(state, pair, result)
        self.assertTrue(flow["consistent"], action)
        self.assertTrue(
            any(v["chain"] == 2 and v["at"] != v["origin"] for v in flow["cleared"])
        )
        self.assertFalse(shape_preserved(case["mainline_cells"], flow))
        self.assertTrue(shape_preserved(case["secondary_cells"], flow))

    def test_empty_or_unobserved_run_cannot_pass(self):
        case = self.case("cancel_boundary-0")
        result = assess(case, "attack", [], [], False, {"status": "not_proven"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("required_cancel_missing", result["issues"])

    def test_search_cutoff_is_separate_from_positive_execution_proof(self):
        case = copy.deepcopy(self.case("no_secondary_chain-0"))
        case["max_resolutions"] = 1
        event = {"type": "resolution_complete", "tick": 30, "data": {"chain_count": 0}}
        row = {
            "diagnostics": {
                "receipt": {"outcome": "activated"},
                "selection": {"reason": "rule_priority_build_main"},
            },
            "lock": {"tick": 20},
            "lock_matches": True,
            "within_deadline": True,
            "quota_ok": True,
            "resolution": event,
            "observed_trace_matches": True,
            "public_root_confirmed": True,
            "trace": {
                "response_status": "partial",
                "response_cutoff": "response_quota",
                "certainty": "conditional_hidden_rows",
                "prepared": False,
                "preparation_status": "absent",
                "public_roots": [],
            },
        }
        ticks = [
            {
                "result": {
                    "events": {"player_0": [event]},
                    "tick": 30,
                    "dropped": {},
                    "attack": {"player_0": {"canceled": 0}},
                },
                "injected_attacks": [],
            }
        ]
        report = assess(
            case, "no_attack", [row], ticks, False, {"status": "not_proven"}
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["search_completeness"][0]["cutoff"], "response_quota")
        row["public_root_confirmed"] = False
        report = assess(
            case, "no_attack", [row], ticks, False, {"status": "not_proven"}
        )
        self.assertEqual(report["status"], "fail")
        self.assertIn("selected_public_root_not_confirmed", report["issues"])

    def test_delayed_attack_without_boundary_observation_is_failure(self):
        case = copy.deepcopy(self.case("preserve_mainline-0"))
        tick = {
            "result": {
                "events": {"player_0": []},
                "tick": 1,
                "dropped": {},
                "attack": {"player_0": {"canceled": 0}},
            },
            "injected_attacks": case["attack_script"],
        }
        result = assess(case, "attack", [], [tick], False, {"status": "not_proven"})
        self.assertIn("attack_boundary_outcome_unobserved", result["issues"])
        self.assertIn("resolution_window_incomplete", result["issues"])

    def test_animation_unknown_board_waits_for_public_visibility(self):
        public = make_request(board=((0,) * 6,) * 14).public.own
        row = {
            "diagnostics": {"receipt": {"executed_action": 0}},
            "lock_matches": True,
            "resolution": {"tick": 10, "data": {"chain_count": 0}},
            "resolution_drop_amount": 6,
            "trace": {
                "public_roots": [
                    {
                        "action": 0,
                        "chain_count": 0,
                        "remaining_board_bottom_up": [["EMPTY"] * 6 for _ in range(14)],
                        "reachable": True,
                        "valid": True,
                        "original_cell_flow": {"consistent": True},
                    }
                ]
            },
        }
        hidden = replace(public, visible_board=((None,) * 6,) * 14)
        confirm_public_resolution(row, hidden, 11)
        self.assertEqual(
            row["public_validation_status"], "pending_animation_visibility"
        )
        self.assertNotIn("public_root_confirmed", row)
        # The actual six garbage cells become public after animation.
        visible = replace(public, visible_board=((0,) * 6,) * 13 + ((5,) * 6,))
        confirm_public_resolution(row, visible, 32)
        self.assertTrue(row["public_root_confirmed"])
        self.assertEqual(row["public_validation_tick"], 32)

    def test_saturated_terminal_drop_is_excluded_not_a_response_success(self):
        case = self.case("unavoidable_loss-0")
        event = {"type": "resolution_complete", "tick": 38, "data": {"chain_count": 0}}
        row = {
            "diagnostics": {
                "receipt": {"outcome": "activated"},
                "selection": {"reason": "build"},
            },
            "lock": {},
            "lock_matches": True,
            "within_deadline": True,
            "quota_ok": True,
            "resolution": event,
            "observed_trace_matches": True,
            "public_root_confirmed": True,
            "trace": {
                "response_status": "evaluated",
                "response_cutoff": None,
                "certainty": "conditional_hidden_rows",
                "prepared": False,
                "preparation_status": "absent",
                "public_roots": [],
            },
        }
        ticks = [
            {
                "injected_attacks": case["attack_script"],
                "result": {
                    "tick": 38,
                    "events": {"player_0": [event]},
                    "attack": {"player_0": {"canceled": 0}},
                    "dropped": {"player_0": 4},
                },
            }
        ]
        result = assess(case, "attack", [row], ticks, True, unavoidable_oracle(case))
        self.assertEqual(result["status"], "excluded_unavoidable")
        self.assertTrue(result["excluded_from_response_success"])
        self.assertNotEqual(result["status"], "pass")


@unittest.skipUnless(SOURCE.exists(), "PUYO277_SOURCE verified corpus required")
class RuntimeEvidenceTests(unittest.TestCase):
    def test_provider_prefix_and_private_sequence_are_outside_public_request(self):
        config = registered_cases()
        match = setup_match(config, config["cases"][0], SOURCE)
        before = match.public_snapshot()
        self.assertEqual(match.public_board_inference().status, "unknown")
        for state in match.player_states.values():
            state.simulator.game.puyo_sequence = object()
        match.seed = -1
        after = match.public_snapshot()
        self.assertEqual(before, after)
        self.assertNotIn("pattern_id", before.to_json())
        self.assertNotIn("source_path", before.to_json())

    def test_short_replay_is_not_pass_and_hash_corruption_is_rejected(self):
        config = copy.deepcopy(registered_cases())
        config["policy"]["profile"].update(
            shared_quota=0, template_quota=0, response_quota=0
        )
        config["policy"]["template_binding_budget"] = 1
        result, replay = run_case(
            config, config["cases"][0], "attack", SOURCE, backend="python", max_ticks=1
        )
        self.assertEqual(result["assessment"]["status"], "fail")
        self.assertEqual(replay_run(replay, SOURCE), result["replay_verified_hash"])
        audit = audit_replay(result, replay, SOURCE)
        self.assertEqual((audit["policy_calls"], audit["ticks_added"]), (0, 0))
        self.assertEqual(audit["assessment"]["status"], "fail")
        altered = copy.deepcopy(replay)
        altered["ticks"][0]["result"]["snapshot_hash"] = "bad"
        with self.assertRaisesRegex(ValueError, "event/hash"):
            replay_run(altered, SOURCE)
        altered = copy.deepcopy(replay)
        altered["case"]["attack_script"][0]["units"] += 1
        with self.assertRaisesRegex(ValueError, "mismatch"):
            replay_run(altered, SOURCE)


if __name__ == "__main__":
    unittest.main()
