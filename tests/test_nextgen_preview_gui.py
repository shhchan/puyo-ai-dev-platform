"""Live preview adoption gates and cheap receipt reads."""

import copy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pygame

from eval.realtime_versus_ui import RealtimeVersusMatchController, RealtimeVersusUiConfig
from src.ui.nextgen_display import live_nextgen_receipt_summary, nextgen_receipt_summary


class NextgenPreviewGuiTests(unittest.TestCase):
    def setUp(self):
        self.controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="first", policy_b="first", plan_overlay=True)
        )
        self.addCleanup(self.controller.shutdown)
        self.runtime = self.controller.controllers["player_0"]
        self.controller.config = replace(self.controller.config, policy_a="nextgen_tactic_manager")
        self.public = self.controller.env.match.public_snapshot(0)
        digest = self.public.digest
        self.identity = {"episode_id": "episode-1", "player_id": 0, "decision_id": 1,
                         "request_id": "request-1", "snapshot_digest": digest}
        receipt = {"requested_candidate_id": "candidate-1", "requested_action": 2,
                   "executed_action": 2, "outcome": "activated"}
        diagnostics = {"request": {"identity": self.identity}, "receipt": receipt,
                       "selection": {"selected_tactic_id": "build_main"}}
        self.last = SimpleNamespace(nextgen_diagnostics=diagnostics, outcome="activated",
                                    executed_action=2)
        self.runtime.diagnostics.last_decision = self.last
        self.runtime._active_action_index = 2
        self.runtime.nextgen_scheduler = SimpleNamespace(data={
            "identity": SimpleNamespace(to_dict=lambda: self.identity), "public": self.public,
        })
        self.plan = {"schema_version": "n-turn-plan-v1", "plan_id": "plan-1",
                     "candidate_id": "candidate-1", "root_action": 2,
                     "request_identity": self.identity, "public_snapshot_digest": digest,
                     "steps": [{"action": 2}, {"action": 3}, {"action": 4}]}
        self.payload = {"plan": self.plan, "plan_id": "plan-1", "nextgen": diagnostics,
                        "plan_preview": {"status": "available", "available_steps": 3}}
        self.runtime.latest_policy_diagnostics = self.payload

    def test_adopted_reference_is_visible_and_o_toggles_only_overlay(self):
        before = copy.deepcopy(self.payload)
        self.assertEqual(self.controller.plan_overlay("player_0"), self.plan)
        self.controller.handle_keydown(pygame.K_o)
        self.assertEqual(self.controller.plan_overlay("player_0"), {})
        self.assertEqual(self.controller.plan_preview_status("player_0")["reason"], "overlay_off")
        self.controller.handle_keydown(pygame.K_o)
        self.assertEqual(self.controller.plan_overlay("player_0"), self.plan)
        self.assertEqual(self.payload, before)

    def test_nonadopted_and_mismatched_receipts_never_show_future_steps(self):
        for outcome in ("stale", "fallback", "timeout"):
            with self.subTest(outcome=outcome):
                self.last.outcome = outcome
                self.assertEqual(self.controller.plan_overlay("player_0"), {})
        self.last.outcome = "activated"
        for key, value in (("plan_id", "wrong"), ("candidate_id", "wrong"),
                           ("root_action", 4), ("request_identity", {}),
                           ("public_snapshot_digest", "wrong")):
            with self.subTest(key=key), patch.dict(self.plan, {key: value}):
                self.assertEqual(self.controller.plan_overlay("player_0"), {})
        with patch.dict(self.plan["steps"][0], {"action": 4}):
            self.assertEqual(self.controller.plan_overlay("player_0"), {})

    def test_changed_decision_piece_or_public_state_hides_old_projection(self):
        with patch.object(self.runtime.nextgen_scheduler, "data", None):
            self.assertEqual(self.controller.plan_preview_status("player_0")["reason"], "decision_changed")
        self.runtime._active_action_index = None
        self.assertEqual(self.controller.plan_preview_status("player_0")["reason"], "piece_finished")
        self.runtime._active_action_index = 2
        with patch.object(self.controller.env.match, "public_snapshot",
                          return_value=replace(self.public, own=replace(self.public.own, score_carry=1))):
            self.assertEqual(self.controller.plan_preview_status("player_0")["reason"], "public_state_changed")

    def test_opponent_progress_alone_keeps_adopted_own_preview(self):
        changed = replace(self.public, opponent=replace(self.public.opponent, phase="resolving"))
        self.assertNotEqual(changed.digest, self.public.digest)
        with patch.object(self.controller.env.match, "public_snapshot", return_value=changed):
            self.assertEqual(self.controller.plan_overlay("player_0"), self.plan)

    def test_live_summary_matches_full_read_without_returning_mutable_diagnostics(self):
        before = copy.deepcopy(self.payload)
        expected = nextgen_receipt_summary(self.payload, {"last_decision": vars(self.last)})
        summary = live_nextgen_receipt_summary(self.payload, self.last)
        self.assertEqual(summary, expected)
        summary["tactic"] = "changed by consumer"
        self.assertEqual(self.payload, before)
        self.last.nextgen_diagnostics = None
        self.last.outcome = "stale"
        self.assertIsNone(live_nextgen_receipt_summary(self.payload, self.last))


if __name__ == "__main__":
    unittest.main()
