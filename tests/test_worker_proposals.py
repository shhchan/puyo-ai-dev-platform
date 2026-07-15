import json
import unittest

from agents.state_analyzer import StateAnalyzer
from agents.strategy_workers import StrategyOrchestrator, smoke_worker_profiles
from agents.v1_7_planner import build_planner_request
from agents.v1_7_tactics import load_tactic_registry
from agents.worker_proposals import (
    CANDIDATE_RANKER_FEATURE_NAMES,
    LEGACY_WORKER_PROPOSAL_SCHEMA_VERSION,
    WORKER_PROPOSAL_SCHEMA_VERSION,
    WorkerProposalBatch,
    build_worker_proposal_batch,
    compatibility_action,
    masked_candidate_distribution,
    select_ranked_candidate,
)
from eval.analyzer_scenarios import load_scenarios, scenario_input
from puyo_env.actions import NUM_ACTIONS, legal_action_mask
from puyo_env.obs import encode_observation
from src.core.headless import HeadlessPuyoSimulator


class TestWorkerProposals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_tactic_registry()
        cls.analyzer_input = scenario_input(load_scenarios()[0])
        cls.analyzer_diagnostics = StateAnalyzer().analyze(cls.analyzer_input)

    def _proposal(self, tactic_id="build_main", *, candidate_count=4, seed=9):
        request = build_planner_request(
            self.registry.tactic(tactic_id),
            self.analyzer_input,
            self.analyzer_diagnostics,
            parameter_overrides={
                "planner": {
                    "beam_depth": 1,
                    "beam_width": 8,
                    "candidate_count": candidate_count,
                }
            },
        )
        simulator = HeadlessPuyoSimulator(seed=seed)
        mask = legal_action_mask(simulator)
        proposal = StrategyOrchestrator(smoke_worker_profiles()).propose(
            0 if tactic_id == "build_main" else 4,
            encode_observation(simulator, step_count=0, max_steps=40),
            {"simulator": simulator, "action_mask": mask},
            planner_request=request,
        )
        return proposal, mask

    def test_build_worker_returns_fixed_k_legal_deterministic_candidates(self):
        first, legal = self._proposal()
        second, _ = self._proposal()
        batch = first.worker_proposal

        self.assertIsNotNone(batch)
        self.assertEqual(batch.schema_version, WORKER_PROPOSAL_SCHEMA_VERSION)
        self.assertEqual(batch.candidate_limit, 4)
        self.assertEqual(batch.candidate_count, 4)
        self.assertEqual(batch.candidate_mask, (True, True, True, True))
        self.assertEqual(batch.selected_action, first.action)
        self.assertEqual(compatibility_action(batch), first.action)
        self.assertEqual(batch.deterministic_digest, second.worker_proposal.deterministic_digest)
        self.assertEqual(
            [candidate.rank for candidate in batch.candidates],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            len({candidate.candidate_id for candidate in batch.candidates}),
            4,
        )
        self.assertTrue(
            all(legal[candidate.root_action] for candidate in batch.candidates)
        )
        self.assertTrue(
            all(candidate.action_sequence[0] == candidate.root_action for candidate in batch.candidates)
        )

        preview = batch.to_dict()["candidates"][0]["preview"]
        self.assertIn("build_potential", preview)
        self.assertIn("ignition_cost", preview)
        self.assertIn("trigger_recoverability", preview)
        self.assertIn("continuation_flexibility", preview)
        self.assertIn("scenario_uncertainty", preview)
        self.assertIn("latency_ms", preview["search_cost"])
        self.assertIn("expanded_nodes", preview["search_cost"])

    def test_serialization_round_trip_preserves_selection_and_value_breakdown(self):
        proposal, _ = self._proposal()
        payload = proposal.worker_proposal.to_dict()

        restored = WorkerProposalBatch.from_dict(
            json.loads(json.dumps(payload))
        )

        self.assertEqual(restored.to_dict(), payload)
        self.assertEqual(restored.selected_action, proposal.action)
        self.assertEqual(
            restored.selected_candidate.value_breakdown,
            proposal.worker_proposal.selected_candidate.value_breakdown,
        )
        self.assertEqual(
            restored.ranker_input.candidate_ids[restored.selected_index],
            restored.selected_candidate.candidate_id,
        )

    def test_legacy_raw_candidate_envelope_migrates_explicitly(self):
        proposal, legal = self._proposal()
        legacy = {
            "schema_version": LEGACY_WORKER_PROPOSAL_SCHEMA_VERSION,
            "profile": {
                "id": proposal.profile_id,
                "name": proposal.profile_name,
                "strategy": proposal.strategy,
            },
            "candidate_limit": 4,
            "selected_action": proposal.action,
            "legal_action_mask": legal,
            "candidates": list(proposal.beam_candidate_dicts),
        }

        migrated = WorkerProposalBatch.from_dict(legacy)

        self.assertEqual(migrated.schema_version, WORKER_PROPOSAL_SCHEMA_VERSION)
        self.assertEqual(migrated.selected_action, proposal.action)
        self.assertEqual(migrated.candidate_count, 4)
        self.assertTrue(
            all(
                candidate.preview_status == "unavailable"
                for candidate in migrated.candidates
            )
        )

    def test_legacy_single_best_uses_fallback_and_masked_padding(self):
        proposal, legal = self._proposal("fire_main", candidate_count=4)
        batch = proposal.worker_proposal
        payload = batch.to_dict()

        self.assertEqual(compatibility_action(batch), proposal.action)
        self.assertEqual(batch.candidate_mask, (True, False, False, False))
        self.assertEqual(batch.candidate_count, 1)
        self.assertTrue(batch.selected_candidate.fallback)
        self.assertEqual(payload["candidates"][1:], [None, None, None])
        self.assertEqual(payload["masks"]["legal_action"], legal)
        self.assertGreater(batch.telemetry().candidate_collapse_ratio, 0.0)

        selection = select_ranked_candidate(batch, [0.0, 100.0, 100.0, 100.0])
        self.assertEqual(selection.index, 0)
        self.assertEqual(selection.action, proposal.action)
        self.assertEqual(selection.distribution.probabilities[1:], (0.0, 0.0, 0.0))

    def test_empty_batch_has_no_selection_and_explicit_empty_semantics(self):
        batch = build_worker_proposal_batch(
            (),
            selected_action=0,
            candidate_limit=3,
            legal_action_mask=[False] * NUM_ACTIONS,
            profile_id=0,
            profile_name="empty",
            strategy="survival",
        )

        self.assertEqual(batch.candidate_mask, (False, False, False))
        self.assertIsNone(batch.selected_index)
        self.assertIsNone(batch.selected_action)
        self.assertEqual(batch.selection_mode, "empty")
        self.assertEqual(compatibility_action(batch, empty_action=7), 7)
        distribution = masked_candidate_distribution(
            [1.0, 2.0, 3.0],
            batch.ranker_input,
        )
        self.assertEqual(distribution.probabilities, (0.0, 0.0, 0.0))
        self.assertEqual(distribution.entropy, 0.0)

    def test_ranker_interface_masks_padding_and_reports_regret(self):
        proposal, _ = self._proposal()
        batch = proposal.worker_proposal
        selected_index = batch.candidate_count - 1
        logits = [-10.0] * batch.candidate_limit
        logits[selected_index] = 10.0

        selection = select_ranked_candidate(batch, logits)
        candidate = batch.candidates[selected_index]
        expected_regret = max(
            item.candidate_value for item in batch.candidates if item is not None
        ) - candidate.candidate_value

        self.assertEqual(selection.index, selected_index)
        self.assertEqual(selection.action, candidate.root_action)
        self.assertTrue(batch.legal_action_mask[selection.action])
        self.assertAlmostEqual(selection.telemetry.selection_regret, expected_regret)
        self.assertLessEqual(selection.distribution.selected_log_probability, 0.0)
        self.assertGreaterEqual(selection.distribution.entropy, 0.0)
        self.assertEqual(
            len(batch.ranker_input.features[0]),
            len(CANDIDATE_RANKER_FEATURE_NAMES),
        )
        self.assertNotIn(
            "chain_style",
            CANDIDATE_RANKER_FEATURE_NAMES,
        )


if __name__ == "__main__":
    unittest.main()
