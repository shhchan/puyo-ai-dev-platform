import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

try:
    import torch
except (ImportError, OSError):  # pragma: no cover - optional dependency guard
    torch = None

from agents.beam_search import BUILD_POTENTIAL_SCHEMA_VERSION
from agents.state_analyzer import AnalyzerConfig, StateAnalyzer
from agents.strategy_workers import smoke_worker_profiles
from agents.v1_7_strategy_manager import (
    PREVIEW_FEATURE_NAMES,
    STRATEGY_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
    V17StrategyFeatureEncoder,
    V17StrategyManagerNetwork,
    V17StrategyManagerPolicy,
    build_v1_7_checkpoint_metadata,
    decode_tactic_parameters,
    encode_preview_features,
)
from agents.v1_7_tactics import load_tactic_registry
from eval.analyzer_scenarios import load_scenarios, scenario_input
from puyo_env.versus_env import VersusPuyoEnv


class TestV17StrategyManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_tactic_registry()
        cls.encoder = V17StrategyFeatureEncoder(cls.registry)
        cls.analyzer = StateAnalyzer()
        cls.scenarios = load_scenarios()

    def scenario_input(self, name):
        scenario = next(item for item in self.scenarios if item["name"] == name)
        return scenario_input(scenario)

    def encode(self, analyzer_input):
        return self.encoder.encode(
            analyzer_input,
            self.analyzer.analyze(analyzer_input),
        )

    def test_feature_contract_distinguishes_empty_lifecycle_and_carry(self):
        initial = self.encode(
            self.scenario_input("initial_empty_board_has_no_all_clear_event")
        ).context_values
        achieved = self.encode(
            self.scenario_input("own_all_clear_state_is_independent")
        ).context_values
        pending = self.encode(
            self.scenario_input("pending_bonus_survives_non_clearing_turn")
        ).context_values
        consumed_input = self.scenario_input("consumed_bonus_is_not_applied_again")
        consumed = self.encode(consumed_input).context_values
        carry = self.encode(
            replace(consumed_input, own=replace(consumed_input.own, score_carry=69))
        ).context_values

        self.assertEqual(initial["own.diagnostics.board_empty"], 1.0)
        self.assertEqual(initial["own.input.all_clear_achieved"], 0.0)
        self.assertEqual(achieved["own.input.all_clear_achieved"], 1.0)
        self.assertEqual(pending["own.input.all_clear_bonus_pending"], 1.0)
        self.assertEqual(consumed["own.input.all_clear_bonus_consumed"], 1.0)
        self.assertEqual(
            consumed["own.input.score_carry"],
            consumed_input.own.score_carry / 69,
        )
        self.assertEqual(carry["own.input.score_carry"], 1.0)
        self.assertEqual(self.encoder.contract.dtype, "float32")
        self.assertEqual(len(initial), self.encoder.contract.context_dim)

    def test_feature_contract_rejects_shape_mismatch(self):
        metadata = self.encoder.contract.to_metadata()
        metadata["context_dim"] += 1

        with self.assertRaisesRegex(
            ValueError,
            "strategy feature contract mismatch for context_dim",
        ):
            self.encoder.contract.validate_metadata(metadata)

    def test_checkpoint_metadata_adds_build_potential_without_feature_growth(self):
        metadata = build_v1_7_checkpoint_metadata(self.registry, run_id="metadata-test")

        self.assertEqual(
            metadata["schemas"]["build_potential"],
            BUILD_POTENTIAL_SCHEMA_VERSION,
        )
        self.assertEqual(self.encoder.contract.context_dim, 77)
        self.assertNotIn(
            "build_potential",
            self.encoder.contract.context_feature_names,
        )

    @unittest.skipIf(torch is None, "torch is required")
    def test_network_masks_ineligible_tactics_and_arbitrates_previews(self):
        encoded = self.encode(
            self.scenario_input("initial_empty_board_has_no_all_clear_event")
        )
        model = V17StrategyManagerNetwork(self.encoder.contract, hidden_dim=32)
        context = torch.tensor([encoded.context], dtype=torch.float32)
        tactics = torch.tensor([encoded.tactics], dtype=torch.float32)
        eligibility = torch.tensor([encoded.eligibility_mask], dtype=torch.bool)

        lightweight = model.forward_lightweight(context, tactics, eligibility)
        previews = torch.zeros(
            (1, len(self.registry.tactics), self.encoder.contract.preview_dim),
            dtype=torch.float32,
        )
        preview_mask = torch.zeros((1, len(self.registry.tactics)), dtype=torch.bool)
        preview_mask[0, 0] = True
        scores = model.forward_arbitration(lightweight, previews, preview_mask)

        self.assertEqual(lightweight.proposal_logits.shape, (1, 8))
        self.assertEqual(lightweight.values.shape, (1, 8))
        self.assertEqual(lightweight.risks.shape, (1, 8))
        self.assertEqual(
            lightweight.parameter_logits.shape,
            (1, 8, self.encoder.contract.max_parameter_logits),
        )
        self.assertTrue(torch.all(lightweight.risks >= 0.0))
        self.assertTrue(torch.all(lightweight.risks <= 1.0))
        self.assertGreater(scores[0, 0].item(), -1.0e9)
        self.assertEqual(scores[0, 1].item(), -1.0e9)
        all_clear_index = self.encoder.contract.tactic_ids.index("all_clear")
        self.assertFalse(encoded.eligibility_mask[all_clear_index])
        self.assertEqual(
            lightweight.proposal_logits[0, all_clear_index].item(),
            -1.0e9,
        )

    def test_parameter_decoder_respects_registry_bounds_and_choices(self):
        tactic = self.registry.tactic("build_main")
        low = [-100.0] * self.encoder.contract.max_parameter_logits
        low[6] = 100.0
        low_values = decode_tactic_parameters(tactic, low)
        high_values = decode_tactic_parameters(
            tactic,
            [100.0] * self.encoder.contract.max_parameter_logits,
        )

        self.assertEqual(low_values["objective"]["target_chain"], 1)
        self.assertEqual(low_values["constraints"]["trigger_preservation"], "ignore")
        self.assertEqual(low_values["planner"]["beam_depth"], 1)
        self.assertEqual(low_values["planner"]["beam_width"], 1)
        self.assertEqual(high_values["objective"]["target_chain"], 19)
        self.assertEqual(high_values["planner"]["beam_depth"], 16)
        self.assertEqual(high_values["planner"]["beam_width"], 250)

    def test_preview_features_include_bonus_aware_attack_fields(self):
        objective = SimpleNamespace(
            achieved=True,
            possible_by_deadline=True,
            surplus_attack=5,
            deadline_missed=False,
            danger_excess=0.0,
        )
        proposal = SimpleNamespace(
            predicted_chain_count=2,
            predicted_score=2_100,
            predicted_attack=30,
            danger=0.25,
            elapsed_seconds=0.01,
            expanded_nodes=20,
            candidate_value=100.0,
            target_attack=25,
            incoming_attack=5,
            deadline=1,
            objective_result=objective,
        )
        first_step = SimpleNamespace(
            attack_generated=30,
            attack_canceled=5,
            attack_outgoing=25,
            incoming_remaining=0,
            all_clear_achieved=True,
            all_clear_bonus_pending=False,
            all_clear_bonus_consumed=True,
        )
        plan = SimpleNamespace(steps=(first_step,))
        request = SimpleNamespace(latency_budget_ms=20.0)

        values = dict(
            zip(
                PREVIEW_FEATURE_NAMES,
                encode_preview_features(proposal, plan, request),
            )
        )

        self.assertEqual(values["first_step.attack_generated"], 30 / 180)
        self.assertEqual(values["first_step.attack_canceled"], 5 / 180)
        self.assertEqual(values["first_step.attack_outgoing"], 25 / 180)
        self.assertEqual(values["first_step.all_clear_achieved"], 1.0)
        self.assertEqual(values["first_step.all_clear_bonus_consumed"], 1.0)

    @unittest.skipIf(torch is None, "torch is required")
    def test_policy_previews_top_three_and_preserves_runtime_diagnostics(self):
        model = V17StrategyManagerNetwork(self.encoder.contract, hidden_dim=32)
        with torch.no_grad():
            for parameter in model.parameters():
                parameter.zero_()
            for index, head in enumerate(model.proposal_heads):
                head.bias[0] = 10.0 - index
                head.bias[3:] = -20.0
        env = VersusPuyoEnv(seed=17, max_steps=4)
        observations, infos = env.reset(seed=17)
        policy = V17StrategyManagerPolicy(
            model,
            registry=self.registry,
            analyzer=StateAnalyzer(
                AnalyzerConfig(max_depth=1, beam_width=6, max_attack_options=4)
            ),
            profiles=smoke_worker_profiles(),
        )

        action = policy.select_action(observations["player_0"], infos["player_0"])
        diagnostics = policy.tactical_diagnostics
        candidates = diagnostics["tactic_candidates"]

        self.assertTrue(bool(infos["player_0"]["action_mask"][action]))
        self.assertEqual(policy.current_profile_name, "build_main")
        self.assertEqual(
            diagnostics["schema_version"],
            STRATEGY_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
        )
        self.assertEqual(sum(candidate["previewed"] for candidate in candidates), 3)
        self.assertEqual(sum(candidate["selected"] for candidate in candidates), 1)
        self.assertTrue(
            all(
                {"logit", "value", "risk", "parameters"}.issubset(candidate)
                for candidate in candidates
            )
        )
        self.assertEqual(
            diagnostics["reason_code"],
            "learned_final_arbitration",
        )
        self.assertEqual(
            diagnostics["selected_tactic"]["tactic_id"],
            "build_main",
        )
        self.assertEqual(
            diagnostics["plan_id"],
            diagnostics["plan"]["plan_id"],
        )
        build_potential = diagnostics["worker"]["result"]["build_potential"]
        self.assertEqual(
            build_potential["schema_version"],
            BUILD_POTENTIAL_SCHEMA_VERSION,
        )
        self.assertEqual(
            diagnostics["worker"]["result"]["value_breakdown"],
            build_potential["value_breakdown"],
        )
        self.assertEqual(
            diagnostics["worker"]["result"]["trigger_recoverability"],
            build_potential["trigger_recoverability"],
        )
        worker_proposal = diagnostics["worker"]["proposal_batch"]
        self.assertEqual(
            worker_proposal["selection"]["selected_action"],
            action,
        )
        self.assertEqual(
            worker_proposal["candidate_count"],
            sum(worker_proposal["masks"]["candidate"]),
        )
        self.assertEqual(
            worker_proposal["ranker_input"]["selected_index"],
            worker_proposal["selection"]["selected_index"],
        )
        selected_preview = next(
            candidate["preview"]
            for candidate in candidates
            if candidate["selected"]
        )
        self.assertEqual(
            selected_preview["worker"]["build_potential"]["schema_version"],
            BUILD_POTENTIAL_SCHEMA_VERSION,
        )
        json.dumps(diagnostics)

        policy.reset()
        self.assertIsNone(policy.current_profile_name)
        self.assertEqual(policy.tactical_diagnostics, {})
        self.assertIsNone(policy.last_plan)
        env.close()

    @unittest.skipIf(torch is None, "torch is required")
    def test_evaluation_override_forces_build_main_after_preview(self):
        model = V17StrategyManagerNetwork(self.encoder.contract, hidden_dim=32)
        with torch.no_grad():
            for parameter in model.parameters():
                parameter.zero_()
            build_main_index = self.encoder.contract.tactic_ids.index("build_main")
            model.proposal_heads[build_main_index].bias[0] = -100.0
        env = VersusPuyoEnv(seed=132, max_steps=4)
        observations, infos = env.reset(seed=132)
        policy = V17StrategyManagerPolicy(
            model,
            registry=self.registry,
            analyzer=StateAnalyzer(
                AnalyzerConfig(max_depth=1, beam_width=6, max_attack_options=4)
            ),
            profiles=smoke_worker_profiles(),
            preview_top_k=1,
            forced_tactic_id="build_main",
        )

        action = policy.select_action(observations["player_0"], infos["player_0"])
        diagnostics = policy.tactical_diagnostics
        selected = diagnostics["selected_tactic"]

        self.assertTrue(bool(infos["player_0"]["action_mask"][action]))
        self.assertEqual(policy.current_profile_name, "build_main")
        self.assertEqual(diagnostics["reason_code"], "evaluation_forced_tactic")
        self.assertEqual(
            diagnostics["evaluation_override"],
            {"enabled": True, "forced_tactic_id": "build_main"},
        )
        self.assertEqual(selected["tactic_id"], "build_main")
        self.assertTrue(
            next(
                candidate
                for candidate in diagnostics["tactic_candidates"]
                if candidate["tactic_id"] == "build_main"
            )["previewed"]
        )
        self.assertEqual(
            selected["parameters"],
            decode_tactic_parameters(
                self.registry.tactic("build_main"),
                [0.0] * self.encoder.contract.max_parameter_logits,
            ),
        )
        env.close()

    @unittest.skipIf(torch is None, "torch is required")
    def test_evaluation_override_rejects_unknown_tactic(self):
        model = V17StrategyManagerNetwork(self.encoder.contract, hidden_dim=32)

        with self.assertRaisesRegex(ValueError, "unknown forced tactic"):
            V17StrategyManagerPolicy(
                model,
                registry=self.registry,
                forced_tactic_id="missing",
            )


if __name__ == "__main__":
    unittest.main()
