"""Display projection is bounded, public, and independent from selection."""

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents.nextgen_plan_preview import build_plan_preview
from agents.nextgen_shared_search import SharedSearchBatchBuilder
from puyo_env.realtime_ai import RealtimePolicyController
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.ui.versus_renderer import plan_step_placement_cells
from tests.test_nextgen_shared_search import request
from tests.test_nextgen_tactic_manager import policy


class PlanPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = policy()
        cls.request = request(
            cls.policy.search_config, quota=(160, 8, 7), cat=cls.policy.catalog
        )
        cls.search = SharedSearchBatchBuilder(
            cls.policy.backend,
            cls.policy.search_config,
            template_catalog=cls.policy.catalog,
        ).build(cls.request)
        cls.candidate = cls.search.batch.candidates[0]
        cls.selection = SimpleNamespace(selected_tactic_id="build_main")

    def preview(self, **values):
        return build_plan_preview(
            values.get("request", self.request),
            values.get("candidate", self.candidate),
            values.get("search", self.search),
            self.selection,
        )

    def test_representative_is_three_public_steps_without_changing_candidate(self):
        before = self.search.batch.to_dict()
        with patch.object(
            self.policy.backend, "search", side_effect=AssertionError("search")
        ):
            payload = self.preview()
        plan = payload["plan"]
        self.assertEqual(len(plan["steps"]), 3)
        self.assertEqual(plan["steps"][0]["action"], self.candidate.root_action)
        self.assertEqual(plan["candidate_id"], self.candidate.candidate_id)
        self.assertEqual(plan["plan_id"], payload["plan_id"])
        self.assertEqual(plan["request_identity"], self.request.identity.to_dict())
        self.assertFalse(plan["execution_queue"])
        self.assertTrue(plan["reference_only"])
        self.assertFalse(plan["continuation_guaranteed"])
        self.assertEqual(len(self.candidate.plan), 1)
        self.assertEqual(self.search.batch.to_dict(), before)
        for index, step in enumerate(plan["steps"]):
            self.assertTrue(step["known_tsumo"])
            self.assertEqual(
                step["tsumo"],
                [
                    c.PUBLIC_CELL_TO_COLOR[v].name
                    for v in self.request.public.own.known_pieces[index]
                ],
            )
            self.assertEqual(len(plan_step_placement_cells([], step)), 2)
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_preview_is_stable_and_cannot_be_mutated_through_diagnostics(self):
        self.assertEqual(self.preview(), self.preview())
        policy_instance = policy()
        match = RealtimeVersusMatch(seed=7)
        controller = RealtimePolicyController(policy_instance)
        obs, info = controller.nextgen_scheduler.prepare(
            match, "player_0", controller.config
        )
        action = policy_instance.select_action(obs, info)
        first = policy_instance.tactical_diagnostics
        first["plan"]["steps"].clear()
        with patch(
            "agents.nextgen_tactic_manager.build_plan_preview",
            side_effect=AssertionError("repeat"),
        ):
            second = policy_instance.tactical_diagnostics
        self.assertEqual(second["plan"]["steps"][0]["action"], action)
        self.assertEqual(
            second["nextgen"]["selection"]["candidate_id"],
            second["plan"]["candidate_id"],
        )
        policy_instance.reset()
        self.assertEqual(policy_instance.tactical_diagnostics, {})

    def test_missing_search_does_not_invent_future_steps(self):
        payload = self.preview(search=replace(self.search, shared_result=None))
        self.assertEqual(len(payload["plan"]["steps"]), 1)
        self.assertEqual(payload["plan_preview"]["reason"], "search_unavailable")
        self.assertEqual(payload["plan_preview"]["status"], "partial")

    def test_representative_root_mismatch_falls_back_to_selected_root(self):
        shared = self.search.shared_result
        node = shared.representatives[self.candidate.root_action]
        wrong = replace(node, path=((self.candidate.root_action + 1) % 22,))
        modified = replace(shared, representatives={self.candidate.root_action: wrong})
        payload = self.preview(search=replace(self.search, shared_result=modified))
        self.assertEqual(
            payload["plan_preview"]["reason"], "representative_root_mismatch"
        )
        self.assertEqual(
            payload["plan"]["steps"][0]["action"], self.candidate.root_action
        )

    def test_private_future_and_queue_never_enter_preview(self):
        # Mutating sampled colors beyond current/NEXT/NEXT2 has no display effect.
        shared = self.search.shared_result
        changed = tuple(
            replace(seq, hidden_pairs=tuple(reversed(seq.hidden_pairs)))
            for seq in shared.scenario_sequences
        )
        other = replace(
            self.search, shared_result=replace(shared, scenario_sequences=changed)
        )
        self.assertEqual(self.preview(), self.preview(search=other))
        own = replace(
            self.request.public.own,
            known_pieces=self.request.public.own.known_pieces[:1],
        )
        public = replace(self.request.public, own=own)
        req = replace(
            self.request,
            public=public,
            identity=replace(self.request.identity, snapshot_digest=public.digest),
        )
        payload = self.preview(request=req)
        self.assertEqual(len(payload["plan"]["steps"]), 1)
        self.assertEqual(payload["plan_preview"]["reason"], "public_prefix_exhausted")

    def test_incoming_drop_does_not_get_omitted_from_future_projection(self):
        own = replace(
            self.request.public.own,
            attack_packets=(c.PublicAttackPacket("p", 1, 30, None),),
        )
        public = replace(self.request.public, own=own)
        req = replace(
            self.request,
            public=public,
            identity=replace(self.request.identity, snapshot_digest=public.digest),
        )
        payload = self.preview(request=req)
        self.assertEqual(len(payload["plan"]["steps"]), 1)
        self.assertEqual(
            payload["plan_preview"]["reason"], "incoming_event_requires_replan"
        )

    def test_existing_selected_multistep_candidate_takes_precedence(self):
        public_plan = tuple(
            c.PlanStep(self.candidate.root_action, pair, "public_known")
            for pair in self.request.public.own.known_pieces
        )
        candidate = replace(
            self.candidate,
            plan=public_plan,
            candidate_id=c.candidate_id(
                self.candidate.identity, public_plan, self.candidate.assumptions
            ),
        )
        payload = self.preview(candidate=candidate)
        self.assertEqual(payload["plan_preview"]["source"], "selected_candidate")
        self.assertEqual(
            [s["action"] for s in payload["plan"]["steps"]],
            [self.candidate.root_action] * 3,
        )


if __name__ == "__main__":
    unittest.main()
