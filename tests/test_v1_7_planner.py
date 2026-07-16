import json
import unittest
from dataclasses import replace

from agents.beam_search import (
    BUILD_POTENTIAL_SCHEMA_VERSION,
    BUILD_POTENTIAL_V1_SCHEMA_VERSION,
)
from agents.state_analyzer import StateAnalyzer
from agents.strategy_workers import (
    SearchControl,
    StrategyOrchestrator,
    smoke_worker_profiles,
)
from agents.v1_7_planner import (
    PLANNER_REQUEST_SCHEMA_VERSION,
    PlannerRequest,
    build_planner_request,
    resolve_preview_attack,
)
from agents.v1_7_tactics import load_tactic_registry
from eval.analyzer_scenarios import load_scenarios, scenario_input
from puyo_env.actions import legal_action_mask
from puyo_env.obs import encode_observation
from src.core.constants import PuyoColor
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo


class TestV17Planner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_tactic_registry()
        cls.scenarios = load_scenarios()

    def test_tactic_parameters_build_versioned_planner_request(self):
        analyzer_input = scenario_input(self.scenarios[0])
        diagnostics = StateAnalyzer().analyze(analyzer_input)

        request = build_planner_request(
            self.registry.tactic("build_main"),
            analyzer_input,
            diagnostics,
            parameter_overrides={
                "objective": {"target_chain": 8},
                "constraints": {"danger_tolerance": 0.7},
                "planner": {
                    "beam_depth": 4,
                    "beam_width": 40,
                    "latency_budget_ms": 75.0,
                },
            },
        )
        payload = request.to_dict()

        self.assertEqual(payload["schema_version"], PLANNER_REQUEST_SCHEMA_VERSION)
        self.assertEqual(payload["tactic_id"], "build_main")
        self.assertEqual(payload["objective"]["target_chain"], 8)
        self.assertEqual(payload["constraints"]["danger_tolerance"], 0.7)
        self.assertEqual(payload["search_budget"]["depth"], 4)
        self.assertEqual(payload["search_budget"]["width"], 40)
        self.assertEqual(payload["search_budget"]["candidate_count"], 8)
        self.assertEqual(payload["search_budget"]["latency_budget_ms"], 75.0)
        self.assertIn("chain_shape_weight", payload["objective"]["weights"])
        self.assertEqual(
            payload["build_potential_schema_version"],
            BUILD_POTENTIAL_SCHEMA_VERSION,
        )
        json.dumps(payload)

    def test_build_main_reuses_candidate_count_for_probe_and_candidate_set(self):
        analyzer_input = scenario_input(self.scenarios[0])
        diagnostics = StateAnalyzer().analyze(analyzer_input)
        simulator = HeadlessPuyoSimulator(seed=9)
        observation = encode_observation(simulator, step_count=0, max_steps=40)
        info = {
            "simulator": simulator,
            "action_mask": legal_action_mask(simulator),
        }
        build_request = build_planner_request(
            self.registry.tactic("build_main"),
            analyzer_input,
            diagnostics,
            parameter_overrides={
                "planner": {
                    "beam_depth": 1,
                    "beam_width": 4,
                    "candidate_count": 2,
                }
            },
        )
        fire_request = build_planner_request(
            self.registry.tactic("fire_main"),
            analyzer_input,
            diagnostics,
        )
        orchestrator = StrategyOrchestrator(smoke_worker_profiles())

        build = orchestrator.propose(
            0,
            observation,
            info,
            planner_request=build_request,
        )
        controlled = orchestrator.propose(
            0,
            observation,
            info,
            search_control=SearchControl(
                99,
                "two-scenario-probe-budget",
                "discrete_profile",
                scenarios=2,
            ),
            planner_request=build_request,
        )
        fire = orchestrator.propose(
            4,
            observation,
            info,
            planner_request=fire_request,
        )

        self.assertEqual(build.trigger_preservation, "required")
        self.assertEqual(build.potential_probe_width, 2)
        self.assertGreater(build.potential_probe_count, 0)
        self.assertEqual(build.build_potential_dict["probe_width"], 2)
        self.assertEqual(len(build.beam_candidates), 2)
        self.assertEqual(
            [candidate.rank for candidate in build.beam_candidates],
            [0, 1],
        )
        self.assertEqual(build.beam_candidates[0].action, build.action)
        self.assertTrue(
            all(
                candidate.build_potential.schema_version
                == BUILD_POTENTIAL_SCHEMA_VERSION
                for candidate in build.beam_candidates
            )
        )
        json.dumps(build.beam_candidate_dicts)
        self.assertEqual(
            controlled.search_control_dict["effective"]["potential_probe_budget"],
            5,
        )
        self.assertEqual(
            controlled.search_control_dict["effective"]["scenarios"],
            2,
        )
        self.assertLessEqual(controlled.potential_probe_count, 5)
        self.assertEqual(fire.trigger_preservation, "ignore")
        self.assertEqual(fire.potential_probe_width, 0)
        self.assertEqual(fire.potential_probe_count, 0)
        self.assertEqual(fire.beam_candidates, ())

    def test_build_main_falls_back_when_visible_pair_is_outside_compact_contract(self):
        analyzer_input = scenario_input(self.scenarios[0])
        diagnostics = StateAnalyzer().analyze(analyzer_input)
        simulator = HeadlessPuyoSimulator(seed=10)
        simulator.game.next_puyo_queue[0][0].color = PuyoColor.PURPLE
        request = build_planner_request(
            self.registry.tactic("build_main"),
            analyzer_input,
            diagnostics,
            parameter_overrides={
                "planner": {
                    "beam_depth": 1,
                    "beam_width": 4,
                    "candidate_count": 2,
                }
            },
        )
        proposal = StrategyOrchestrator(smoke_worker_profiles()).propose(
            0,
            encode_observation(simulator, step_count=0, max_steps=40),
            {
                "simulator": simulator,
                "action_mask": legal_action_mask(simulator),
            },
            planner_request=request,
        )

        self.assertTrue(legal_action_mask(simulator)[proposal.action])
        self.assertEqual(
            proposal.worker_proposal.shared_context.profile["search"]["name"],
            "runtime-fallback-legacy",
        )
        self.assertFalse(any(proposal.worker_proposal.ranker_input.candidate_mask))

    def test_fire_main_keeps_legacy_one_step_route(self):
        analyzer_input = scenario_input(self.scenarios[0])
        diagnostics = StateAnalyzer().analyze(analyzer_input)
        simulator = HeadlessPuyoSimulator(seed=9)
        observation = encode_observation(simulator, step_count=0, max_steps=40)
        info = {
            "simulator": simulator,
            "action_mask": legal_action_mask(simulator),
        }
        request = build_planner_request(
            self.registry.tactic("fire_main"),
            analyzer_input,
            diagnostics,
        )

        proposal = StrategyOrchestrator(smoke_worker_profiles()).propose(
            4,
            observation,
            info,
            planner_request=request,
        )

        self.assertEqual(proposal.action, 1)
        self.assertEqual(proposal.strategy, "fire_max")
        self.assertEqual(request.search_depth, 1)
        self.assertEqual(
            request.build_potential_schema_version,
            BUILD_POTENTIAL_V1_SCHEMA_VERSION,
        )
        self.assertEqual(proposal.potential_probe_count, 0)

    def test_all_initial_tactics_resolve_to_positive_search_budgets(self):
        analyzer_input = scenario_input(self.scenarios[0])
        diagnostics = StateAnalyzer().analyze(analyzer_input)

        requests = [
            build_planner_request(tactic, analyzer_input, diagnostics)
            for tactic in self.registry.tactics
            if tactic.identity.tactic_id != "prepare_response"
        ]

        self.assertEqual(len(requests), 7)
        self.assertTrue(all(request.search_depth > 0 for request in requests))
        self.assertTrue(all(request.search_width > 0 for request in requests))
        self.assertTrue(all(request.candidate_count > 0 for request in requests))
        self.assertTrue(all(request.latency_budget_ms > 0 for request in requests))

    def test_prepare_response_uses_positive_forecast_as_readiness_not_fire_target(self):
        analyzer_input = scenario_input(self.scenarios[0])
        diagnostics = StateAnalyzer().analyze(analyzer_input)
        threat = replace(
            diagnostics,
            opponent=replace(
                diagnostics.opponent,
                forecast=replace(
                    diagnostics.opponent.forecast,
                    short_attack=8,
                    turns_to_best=2,
                ),
            ),
        )

        request = build_planner_request(
            self.registry.tactic("prepare_response"),
            analyzer_input,
            threat,
        )

        self.assertEqual(request.objective_kind, "response_readiness")
        self.assertEqual(request.target_attack, 0)
        self.assertEqual(request.required_response_attack, 8)
        self.assertEqual(request.response_source, "opponent_forecast")
        self.assertEqual(request.deadline_turns, 2)
        self.assertEqual(request.to_dict()["objective"]["required_response_attack"], 8)

    def test_response_readiness_worker_avoids_available_immediate_fire(self):
        simulator = HeadlessPuyoSimulator(seed=91)
        game = simulator.game
        game.current_puyo_1 = Puyo(PuyoColor.BLUE)
        game.current_puyo_2 = Puyo(PuyoColor.BLUE)
        game.field.place_puyo(1, 0, Puyo(PuyoColor.BLUE))
        game.field.place_puyo(1, 1, Puyo(PuyoColor.BLUE))
        observation = encode_observation(simulator, step_count=0, max_steps=40)
        info = {"simulator": simulator, "action_mask": legal_action_mask(simulator)}
        request = PlannerRequest(
            tactic_id="prepare_response",
            tactic_version="2.0",
            objective_kind="response_readiness",
            target_chain=0,
            target_attack=0,
            deadline_turns=3,
            deadline_ticks=0,
            danger_tolerance=1.0,
            trigger_preservation="required",
            search_depth=2,
            search_width=8,
            candidate_count=4,
            latency_budget_ms=10_000.0,
            fallback_tactic="survive",
            objective_weights={},
            parameters={"objective": {}, "constraints": {}, "planner": {}},
            score_carry=0,
            incoming_attack=0,
            all_clear_achieved=False,
            all_clear_bonus_pending=False,
            all_clear_bonus_consumed=False,
            required_response_attack=1,
            response_source="opponent_forecast",
            build_potential_schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
        )

        proposal = StrategyOrchestrator(smoke_worker_profiles()).propose(
            3,
            observation,
            info,
            planner_request=request,
        )

        self.assertEqual(proposal.objective.kind, "response_readiness")
        self.assertEqual(proposal.predicted_attack, 0)
        self.assertFalse(proposal.immediate_fire)
        self.assertNotIn("immediate_fire", proposal.objective_result.miss_reasons)
        self.assertEqual(proposal.potential_probe_width, 4)
        self.assertLessEqual(proposal.potential_probe_count, 5)
        self.assertEqual(
            proposal.build_potential_dict["schema_version"],
            BUILD_POTENTIAL_SCHEMA_VERSION,
        )

        ignored = StrategyOrchestrator(smoke_worker_profiles()).propose(
            3,
            observation,
            info,
            search_control=SearchControl(
                100,
                "ignore-trigger-probe-budget",
                "discrete_profile",
            ),
            planner_request=replace(request, trigger_preservation="ignore"),
        )
        self.assertFalse(ignored.trigger_preserved)
        self.assertEqual(ignored.trigger_recoverability.status, "unknown")
        self.assertEqual(ignored.potential_probe_count, 0)
        self.assertEqual(
            ignored.search_control_dict["effective"][
                "build_potential_schema_version"
            ],
            BUILD_POTENTIAL_SCHEMA_VERSION,
        )

    def test_all_clear_request_preserves_lifecycle_and_does_not_fix_total_attack_to_thirty(self):
        base = scenario_input(self.scenarios[0])
        analyzer_input = replace(
            base,
            own=replace(
                base.own,
                score_carry=69,
                all_clear_achieved=True,
                all_clear_bonus_pending=True,
                all_clear_bonus_consumed=False,
            ),
        )
        diagnostics = StateAnalyzer().analyze(analyzer_input)

        request = build_planner_request(
            self.registry.tactic("all_clear"),
            analyzer_input,
            diagnostics,
        )

        self.assertEqual(request.score_carry, 69)
        self.assertTrue(request.all_clear_achieved)
        self.assertTrue(request.all_clear_bonus_pending)
        self.assertFalse(request.all_clear_bonus_consumed)
        self.assertEqual(request.target_attack, diagnostics.incoming.amount)
        self.assertNotEqual(request.target_attack, 30)

    def test_preview_matches_runtime_carry_boundaries_and_cancellation(self):
        self.assertEqual(resolve_preview_attack(69, 0, 0).to_dict()["generated"], 0)
        self.assertEqual(resolve_preview_attack(70, 0, 0).to_dict()["generated"], 1)
        boundary = resolve_preview_attack(71, 0, 0)
        self.assertEqual((boundary.generated, boundary.score_carry_after), (1, 1))

        exact = resolve_preview_attack(560, 0, 8)
        excess = resolve_preview_attack(560, 0, 5)
        deficit = resolve_preview_attack(560, 0, 10)
        self.assertEqual((exact.generated, exact.canceled, exact.outgoing), (8, 8, 0))
        self.assertEqual((excess.generated, excess.canceled, excess.outgoing), (8, 5, 3))
        self.assertEqual((deficit.generated, deficit.canceled, deficit.outgoing), (8, 8, 0))
        self.assertEqual(deficit.incoming_after, 2)

    def test_orchestrator_plan_reports_bonus_carry_and_attack_resolution(self):
        simulator = HeadlessPuyoSimulator(seed=123)
        game = simulator.game
        game.current_puyo_1 = Puyo(PuyoColor.BLUE)
        game.current_puyo_2 = Puyo(PuyoColor.BLUE)
        for y in (0, 1):
            game.field.place_puyo(1, y, Puyo(PuyoColor.BLUE))
        game.field.place_puyo(5, 0, Puyo(PuyoColor.RED))
        game.all_clear_bonus_pending = True
        observation = encode_observation(simulator, step_count=0, max_steps=40)
        info = {
            "simulator": simulator,
            "action_mask": legal_action_mask(simulator),
            "score_carry": 40,
            "incoming_ojama": 5,
            "incoming_turns": 1,
        }
        request = PlannerRequest(
            tactic_id="all_clear",
            tactic_version="1.0",
            objective_kind="fire_max",
            target_chain=1,
            target_attack=1,
            deadline_turns=1,
            deadline_ticks=0,
            danger_tolerance=1.0,
            trigger_preservation="prefer",
            search_depth=1,
            search_width=22,
            candidate_count=8,
            latency_budget_ms=10_000.0,
            fallback_tactic="build_main",
            objective_weights={},
            parameters={"objective": {}, "constraints": {}, "planner": {}},
            score_carry=40,
            incoming_attack=5,
            all_clear_achieved=False,
            all_clear_bonus_pending=True,
            all_clear_bonus_consumed=False,
            build_potential_schema_version=BUILD_POTENTIAL_V1_SCHEMA_VERSION,
        )
        orchestrator = StrategyOrchestrator(smoke_worker_profiles())

        proposal = orchestrator.propose(
            4,
            observation,
            info,
            planner_request=request,
        )
        plan = orchestrator.last_plan
        first = plan.steps[0]
        payload = plan.to_dict()

        self.assertEqual(plan.first_action, proposal.action)
        self.assertEqual(first.attack_score_delta, 2140)
        self.assertEqual((first.score_carry_before, first.score_carry_after), (40, 10))
        self.assertEqual(
            (first.attack_generated, first.attack_canceled, first.attack_outgoing),
            (31, 5, 26),
        )
        self.assertTrue(first.all_clear_bonus_consumed)
        self.assertEqual(first.all_clear_bonus_score, 2100)
        self.assertEqual(payload["planner_request"]["schema_version"], PLANNER_REQUEST_SCHEMA_VERSION)
        self.assertEqual(payload["attack_summary"]["generated"], 31)
        self.assertEqual(payload["attack_summary"]["canceled"], 5)
        self.assertEqual(payload["attack_summary"]["outgoing"], 26)
        self.assertEqual(payload["attack_summary"]["final_score_carry"], 10)


if __name__ == "__main__":
    unittest.main()
