import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from agents.state_analyzer import StateAnalyzer
from agents.beam_search import (
    BeamSearchConfig,
    BeamSearchPolicy,
    BuildPotentialBudget,
)
from agents.strategy_workers import StrategyOrchestrator, smoke_worker_profiles
from agents.v1_7_planner import build_planner_request
from agents.v1_7_tactics import load_tactic_registry
from agents.worker_proposals import (
    CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION,
    CANDIDATE_RANKER_SCHEMA_HASH,
    CANDIDATE_RANKER_V1_SCHEMA_HASH,
    CANDIDATE_RANKER_FEATURE_NAMES,
    EvidenceStatus,
    LEGACY_WORKER_PROPOSAL_SCHEMA_VERSION,
    MaskedNumeric,
    WORKER_PROPOSAL_V1_SCHEMA_VERSION,
    WORKER_PROPOSAL_SCHEMA_VERSION,
    WorkerProposalBatch,
    build_worker_proposal_batch,
    candidate_ranker_schema_metadata,
    compatibility_action,
    masked_candidate_distribution,
    migrate_worker_proposal_payload,
    project_worker_proposal_v1,
    ranker_input_for_model,
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
        self.assertNotIn("search_cost", preview)
        shared = batch.to_dict()["shared_context"]
        self.assertIn("elapsed_ms", shared["latency"])
        self.assertIn("expanded_nodes", shared["search_totals"])
        self.assertNotIn("search_latency_ms", CANDIDATE_RANKER_FEATURE_NAMES)
        self.assertNotIn("expanded_nodes", CANDIDATE_RANKER_FEATURE_NAMES)

        changed_deadline = replace(
            batch,
            shared_context=replace(
                batch.shared_context,
                worker_deadline={
                    "status": "overrun",
                    "budget_ms": 0.01,
                    "overrun": True,
                    "source": "planner_request",
                },
            ),
        )
        same_deadline_budget = replace(
            changed_deadline,
            shared_context=replace(
                changed_deadline.shared_context,
                worker_deadline={
                    "status": "within_budget",
                    "budget_ms": 0.01,
                    "overrun": False,
                    "source": "planner_request",
                },
            ),
        )
        self.assertEqual(
            changed_deadline.deterministic_digest,
            same_deadline_budget.deterministic_digest,
        )

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

        self.assertEqual(migrated.schema_version, WORKER_PROPOSAL_V1_SCHEMA_VERSION)
        self.assertEqual(migrated.selected_action, proposal.action)
        self.assertEqual(migrated.candidate_count, 4)
        self.assertTrue(
            all(
                candidate.preview_status == "unavailable"
                for candidate in migrated.candidates
            )
        )

        upgraded = WorkerProposalBatch.from_dict(
            migrate_worker_proposal_payload(
                migrated.to_dict(),
                target_schema_version=WORKER_PROPOSAL_SCHEMA_VERSION,
            )
        )
        self.assertEqual(upgraded.schema_version, WORKER_PROPOSAL_SCHEMA_VERSION)
        self.assertEqual(upgraded.selected_action, migrated.selected_action)
        self.assertEqual(upgraded.candidate_mask, migrated.candidate_mask)
        self.assertEqual(
            [candidate.candidate_id for candidate in upgraded.candidates],
            [candidate.candidate_id for candidate in migrated.candidates],
        )
        self.assertTrue(
            all(
                candidate.evidence.status == EvidenceStatus.LEGACY_MISSING
                for candidate in upgraded.candidates
            )
        )
        self.assertFalse(any(upgraded.ranker_input.candidate_mask))

    def test_checked_in_v1_fixture_keeps_identity_rank_mask_and_action(self):
        fixture = json.loads(
            Path("tests/fixtures/worker_proposal_v1.json").read_text(
                encoding="utf-8"
            )
        )
        v1 = WorkerProposalBatch.from_dict(fixture)
        v2 = WorkerProposalBatch.from_dict(
            migrate_worker_proposal_payload(
                fixture,
                target_schema_version=WORKER_PROPOSAL_SCHEMA_VERSION,
            )
        )

        self.assertEqual(v1.to_dict(), fixture)
        self.assertEqual(v1.selected_action, v2.selected_action)
        self.assertEqual(v1.candidate_mask, v2.candidate_mask)
        self.assertEqual(v1.candidates[0].rank, v2.candidates[0].rank)
        self.assertEqual(
            v1.candidates[0].candidate_id,
            v2.candidates[0].candidate_id,
        )
        self.assertEqual(compatibility_action(v1), compatibility_action(v2))

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

        with self.assertRaisesRegex(ValueError, "cannot rank"):
            select_ranked_candidate(batch, [0.0, 100.0, 100.0, 100.0])
        v1_projection = project_worker_proposal_v1(batch)
        selection = select_ranked_candidate(
            v1_projection,
            [0.0, 100.0, 100.0, 100.0],
        )
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

    def test_v2_evidence_round_trip_preserves_expected_structural_and_scenarios(self):
        first, _ = self._proposal(candidate_count=8, seed=175)
        second, _ = self._proposal(candidate_count=8, seed=175)
        batch = first.worker_proposal
        payload = batch.to_dict()
        restored = WorkerProposalBatch.from_dict(json.loads(json.dumps(payload)))

        evidence = restored.selected_candidate.evidence
        self.assertIsNotNone(evidence.expected_chain)
        self.assertIsNotNone(evidence.structural_chain)
        self.assertEqual(evidence.expected_chain["root_action"], restored.selected_action)
        self.assertEqual(
            evidence.scenario_digest,
            restored.shared_context.scenario_digest,
        )
        self.assertEqual(
            sum(evidence.scenario_mask),
            evidence.expected_chain["evaluated_scenarios"],
        )
        self.assertEqual(restored.to_dict(), payload)
        self.assertEqual(batch.deterministic_digest, second.worker_proposal.deterministic_digest)
        self.assertEqual(
            batch.ranker_input.deterministic_digest,
            second.worker_proposal.ranker_input.deterministic_digest,
        )

    def test_v2_distinguishes_zero_not_evaluated_budget_and_legacy_missing(self):
        proposal, _ = self._proposal(candidate_count=4, seed=176)
        batch = proposal.worker_proposal
        statuses = (
            EvidenceStatus.EVALUATED,
            EvidenceStatus.NOT_EVALUATED,
            EvidenceStatus.BUDGET_EXHAUSTED,
            EvidenceStatus.LEGACY_MISSING,
        )
        candidates = []
        for candidate, status in zip(batch.candidates, statuses):
            fields = dict(candidate.evidence.numeric_fields)
            present = status in {
                EvidenceStatus.EVALUATED,
                EvidenceStatus.BUDGET_EXHAUSTED,
            }
            fields["build_potential.predicted_chain_potential"] = MaskedNumeric(
                value=0.0 if present else None,
                is_present=present,
                evaluated=present,
                status=status,
            )
            evidence = replace(
                candidate.evidence,
                status=status,
                numeric_fields=fields,
            )
            candidates.append(replace(candidate, evidence=evidence))
        categorized = replace(batch, candidates=tuple(candidates))
        restored = WorkerProposalBatch.from_dict(categorized.to_dict())

        values = [
            candidate.evidence.numeric(
                "build_potential.predicted_chain_potential"
            )
            for candidate in restored.candidates
        ]
        self.assertEqual(values[0].value, 0.0)
        self.assertTrue(values[0].is_present)
        self.assertIsNone(values[1].value)
        self.assertFalse(values[1].is_present)
        self.assertEqual(values[2].status, EvidenceStatus.BUDGET_EXHAUSTED)
        self.assertEqual(values[3].status, EvidenceStatus.LEGACY_MISSING)
        self.assertEqual(
            restored.ranker_input.candidate_mask,
            (True, False, True, False),
        )
        self.assertEqual(
            restored.compatibility_ranker_input.candidate_mask,
            (True, False, True, False),
        )
        projection = restored.compatibility_projection
        potential_index = projection["feature_names"].index(
            "build_potential.predicted_chain_potential"
        )
        self.assertFalse(
            projection["missing_feature_mask"][0][potential_index]
        )
        self.assertTrue(
            projection["missing_feature_mask"][1][potential_index]
        )

    def test_six_scenario_ids_digests_and_masks_are_canonical(self):
        simulator = HeadlessPuyoSimulator(seed=179)
        legal = legal_action_mask(simulator)
        policy = BeamSearchPolicy(
            BeamSearchConfig.for_profile(
                "quality-d12",
                depth=1,
                width=24,
                max_expanded_nodes=132,
                candidate_limit=8,
                potential_probe_budget=8,
                build_potential_budget=BuildPotentialBudget(
                    max_added_puyos=1,
                    max_pattern_nodes=2,
                    max_resolution_nodes=2,
                    max_alternatives=1,
                    max_continuation_actions=1,
                    max_recovery_puyos=0,
                ),
            )
        )
        candidates = policy.generate_candidates(
            {},
            {"simulator": simulator, "action_mask": legal},
        )
        diagnostics = policy.last_diagnostics

        def build(raw_candidates):
            return build_worker_proposal_batch(
                raw_candidates,
                selected_action=raw_candidates[0].action,
                candidate_limit=8,
                legal_action_mask=legal,
                profile_id=0,
                profile_name="quality-six-scenario",
                strategy="build_large",
                simulator=simulator,
                expanded_nodes=diagnostics.expanded_nodes,
                scenario_budget=diagnostics.scenario_budget,
            )

        batch = build(candidates)
        self.assertEqual(batch.shared_context.scenario_count, 6)
        self.assertTrue(all(batch.shared_context.scenario_mask))
        self.assertEqual(
            list(batch.shared_context.scenario_ids),
            sorted(batch.shared_context.scenario_ids),
        )
        self.assertTrue(all(batch.shared_context.scenario_digests))
        self.assertTrue(
            all(sum(candidate.evidence.scenario_mask) == 6 for candidate in batch.candidates)
        )

        reordered_expected = copy.deepcopy(
            dict(candidates[0].expected_chain_evidence)
        )
        reordered_expected["scenario_values"] = list(
            reversed(reordered_expected["scenario_values"])
        )
        reordered_candidates = (
            replace(
                candidates[0],
                expected_chain_evidence=reordered_expected,
            ),
            *candidates[1:],
        )
        reordered = build(reordered_candidates)
        self.assertEqual(
            batch.shared_context.scenario_digest,
            reordered.shared_context.scenario_digest,
        )
        self.assertEqual(
            batch.ranker_input.scenario_features[0],
            reordered.ranker_input.scenario_features[0],
        )
        self.assertNotEqual(
            batch.selected_candidate.evidence.expected_chain["scenario_values"],
            reordered.selected_candidate.evidence.expected_chain["scenario_values"],
        )

    def test_ranker_schema_hash_requires_error_or_explicit_v1_projection(self):
        proposal, _ = self._proposal(candidate_count=4, seed=177)
        batch = proposal.worker_proposal
        direct_contract = candidate_ranker_schema_metadata(
            batch.ranker_input.schema_version
        )
        self.assertEqual(
            ranker_input_for_model(batch, direct_contract),
            batch.ranker_input,
        )
        v1_contract = {
            "schema_version": CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION,
            "schema_hash": CANDIDATE_RANKER_V1_SCHEMA_HASH,
        }
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            ranker_input_for_model(batch, v1_contract)
        projected = ranker_input_for_model(
            batch,
            v1_contract,
            allow_compatibility_projection=True,
        )
        self.assertEqual(
            projected.schema_version,
            CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION,
        )
        self.assertEqual(batch.ranker_input.schema_hash, CANDIDATE_RANKER_SCHEMA_HASH)
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            ranker_input_for_model(
                batch,
                {
                    "schema_version": batch.ranker_input.schema_version,
                    "schema_hash": "tampered",
                },
            )

    def test_v2_serialization_rejects_non_finite_candidate_values(self):
        proposal, _ = self._proposal(candidate_count=4, seed=178)
        with self.assertRaisesRegex(ValueError, "non-finite"):
            replace(
                proposal.worker_proposal.selected_candidate,
                candidate_value=float("nan"),
            )


if __name__ == "__main__":
    unittest.main()
