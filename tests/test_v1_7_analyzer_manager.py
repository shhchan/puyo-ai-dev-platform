import json
import unittest
from dataclasses import replace

from agents.state_analyzer import AnalyzerConfig, StateAnalyzer
from agents.strategy_workers import smoke_worker_profiles
from agents.v1_7_analyzer_manager import (
    ANALYZER_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
    V17AnalyzerManagerPolicy,
    select_tactic,
)
from agents.v1_7_planner import build_planner_request
from agents.v1_7_tactics import load_tactic_registry
from eval.analyzer_scenarios import load_scenarios, scenario_input
from eval.arena import parse_args as parse_arena_args
from eval.realtime_arena import parse_args as parse_realtime_arena_args
from eval.spectate import parse_args as parse_spectate_args
from puyo_env.versus_env import VersusPuyoEnv
from selfplay.policies import make_policy
from src.core.constants import GRID_HEIGHT, GRID_WIDTH


class TestV17AnalyzerManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_tactic_registry()
        cls.scenarios = load_scenarios()
        cls.analyzer = StateAnalyzer()

    def scenario_input(self, name):
        scenario = next(item for item in self.scenarios if item["name"] == name)
        return scenario_input(scenario)

    def selection(self, analyzer_input, diagnostics=None):
        diagnostics = diagnostics or self.analyzer.analyze(analyzer_input)
        return select_tactic(self.registry, analyzer_input, diagnostics)

    def test_initial_empty_and_consumed_only_do_not_select_all_clear(self):
        initial = self.scenario_input("initial_empty_board_has_no_all_clear_event")
        consumed = self.scenario_input("consumed_bonus_is_not_applied_again")

        initial_selection = self.selection(initial)
        consumed_selection = self.selection(consumed)

        self.assertEqual(initial_selection.tactic_id, "build_main")
        self.assertEqual(consumed_selection.tactic_id, "build_main")
        all_clear = next(
            item for item in consumed_selection.candidates if item["tactic_id"] == "all_clear"
        )
        self.assertTrue(all_clear["registry_eligible"])
        self.assertFalse(all_clear["scoring"]["active"])

    def test_pending_achieved_and_renewed_select_all_clear(self):
        achieved = self.scenario_input("own_all_clear_state_is_independent")
        pending = self.scenario_input("pending_bonus_survives_non_clearing_turn")
        consumed = self.scenario_input("consumed_bonus_is_not_applied_again")
        renewed = replace(
            consumed,
            own=replace(consumed.own, all_clear_bonus_pending=True),
        )

        for analyzer_input in (achieved, pending, renewed):
            with self.subTest(state=analyzer_input.own):
                selection = self.selection(analyzer_input)
                self.assertEqual(selection.tactic_id, "all_clear")
                self.assertEqual(selection.priority_band, 4)

    def test_incoming_priority_distinguishes_counter_and_survival(self):
        counterable = self.scenario_input("deadline_one_exact_cancel")
        insufficient = self.scenario_input("deadline_one_return_is_insufficient")

        counter_selection = self.selection(counterable)
        survival_selection = self.selection(insufficient)

        self.assertEqual(counter_selection.tactic_id, "counter_or_return")
        self.assertEqual(counter_selection.reason_code, "incoming_counterable")
        self.assertEqual(survival_selection.tactic_id, "survive")
        self.assertEqual(survival_selection.reason_code, "incoming_uncancellable")

    def test_danger_threshold_precedes_all_clear_entitlement(self):
        analyzer_input = self.scenario_input("own_all_clear_state_is_independent")
        diagnostics = self.analyzer.analyze(analyzer_input)
        diagnostics = replace(
            diagnostics,
            own=replace(diagnostics.own, danger=0.82),
        )

        selection = self.selection(analyzer_input, diagnostics)

        self.assertEqual(selection.tactic_id, "survive")
        self.assertEqual(selection.priority_band, 3)

    def test_offensive_band_selects_lethal_fire_threat_and_pressure(self):
        pressure_input = self.scenario_input("short_tactical_sub_chain_keeps_main_distinct")
        pressure_diagnostics = self.analyzer.analyze(pressure_input)
        self.assertEqual(self.selection(pressure_input, pressure_diagnostics).tactic_id, "pressure")

        high_board = [list(row) for row in pressure_input.opponent.board]
        high_board[10][2] = "OJAMA"
        lethal_input = replace(
            pressure_input,
            opponent=replace(
                pressure_input.opponent,
                board=tuple(tuple(row) for row in high_board),
            ),
        )
        self.assertEqual(self.selection(lethal_input, pressure_diagnostics).tactic_id, "lethal_attack")

        fire_main = replace(
            pressure_diagnostics.own.forecast.main_chain,
            turns=1,
            chain_count=4,
            attack=12,
            is_immediate=True,
        )
        fire_diagnostics = replace(
            pressure_diagnostics,
            own=replace(
                pressure_diagnostics.own,
                forecast=replace(
                    pressure_diagnostics.own.forecast,
                    immediate_attack=12,
                    short_attack=12,
                    main_chain=fire_main,
                    turns_to_best=1,
                ),
            ),
        )
        empty_opponent_input = replace(
            pressure_input,
            opponent=replace(
                pressure_input.opponent,
                board=tuple(
                    tuple("EMPTY" for _ in range(GRID_WIDTH))
                    for _ in range(GRID_HEIGHT)
                ),
            ),
        )
        self.assertEqual(
            self.selection(empty_opponent_input, fire_diagnostics).tactic_id,
            "fire_main",
        )

        safe_input = self.scenario_input("initial_empty_board_has_no_all_clear_event")
        safe_diagnostics = self.analyzer.analyze(safe_input)
        threat_diagnostics = replace(
            safe_diagnostics,
            opponent=replace(
                safe_diagnostics.opponent,
                forecast=replace(
                    safe_diagnostics.opponent.forecast,
                    short_attack=8,
                    turns_to_best=2,
                ),
            ),
        )
        self.assertEqual(
            self.selection(safe_input, threat_diagnostics).tactic_id,
            "prepare_response",
        )

    def test_counter_margin_is_added_to_incoming_attack(self):
        analyzer_input = self.scenario_input("deadline_one_exact_cancel")
        diagnostics = self.analyzer.analyze(analyzer_input)

        request = build_planner_request(
            self.registry.tactic("counter_or_return"),
            analyzer_input,
            diagnostics,
        )

        self.assertEqual(request.incoming_attack, 5)
        self.assertEqual(request.target_attack, 6)
        self.assertEqual(request.parameters["objective"]["counter_margin"], 1)

    def test_policy_is_deterministic_legal_resettable_and_json_serializable(self):
        env = VersusPuyoEnv(seed=17, max_steps=4)
        observations, infos = env.reset(seed=17)
        overrides = {
            "build_main": {
                "planner": {
                    "beam_depth": 1,
                    "beam_width": 4,
                    "candidate_count": 2,
                    "latency_budget_ms": 500.0,
                }
            }
        }
        policy = V17AnalyzerManagerPolicy(
            analyzer=StateAnalyzer(
                AnalyzerConfig(max_depth=1, beam_width=6, max_attack_options=4)
            ),
            profiles=smoke_worker_profiles(),
            parameter_overrides=overrides,
        )

        first_action = policy.select_action(observations["player_0"], infos["player_0"])
        first_diagnostics = policy.tactical_diagnostics
        second_action = policy.select_action(observations["player_0"], infos["player_0"])
        second_diagnostics = policy.tactical_diagnostics

        self.assertEqual(first_action, second_action)
        self.assertTrue(bool(infos["player_0"]["action_mask"][first_action]))
        self.assertEqual(first_diagnostics, second_diagnostics)
        self.assertEqual(
            first_diagnostics["schema_version"],
            ANALYZER_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
        )
        self.assertEqual(len(first_diagnostics["tactic_candidates"]), 8)
        self.assertTrue(
            all("scoring" in candidate for candidate in first_diagnostics["tactic_candidates"])
        )
        self.assertEqual(
            first_diagnostics["model_metadata"]["lineage_node_id"],
            "model_version:v1.7.0",
        )
        json.dumps(first_diagnostics)

        policy.reset()
        self.assertIsNone(policy.current_profile_name)
        self.assertEqual(policy.tactical_diagnostics, {})
        self.assertIsNone(policy.last_proposal)
        self.assertIsNone(policy.last_plan)
        env.close()

    def test_pending_lifecycle_carry_budget_and_request_reach_worker_plan(self):
        env = VersusPuyoEnv(seed=23, max_steps=4)
        observations, infos = env.reset(seed=23)
        env.player_states["player_0"].score_carry = 69
        env.player_states["player_0"].simulator.game.all_clear_achieved = True
        env.player_states["player_0"].simulator.game.all_clear_bonus_pending = True
        observations, infos = env._observations_and_infos()
        policy = V17AnalyzerManagerPolicy(
            analyzer=StateAnalyzer(
                AnalyzerConfig(max_depth=1, beam_width=6, max_attack_options=4)
            ),
            profiles=smoke_worker_profiles(),
            parameter_overrides={
                "all_clear": {
                    "planner": {
                        "beam_depth": 1,
                        "beam_width": 4,
                        "candidate_count": 2,
                        "latency_budget_ms": 500.0,
                    }
                }
            },
        )

        policy.select_action(observations["player_0"], infos["player_0"])
        diagnostics = policy.tactical_diagnostics
        request = diagnostics["planner_request"]
        plan_request = diagnostics["plan"]["planner_request"]

        self.assertEqual(policy.current_profile_name, "all_clear")
        self.assertEqual(diagnostics["worker"]["profile_name"], "fire_max")
        self.assertEqual(request["runtime_context"]["score_carry"], 69)
        self.assertTrue(request["runtime_context"]["all_clear_achieved"])
        self.assertTrue(request["runtime_context"]["all_clear_bonus_pending"])
        self.assertFalse(request["runtime_context"]["all_clear_bonus_consumed"])
        self.assertEqual(request["search_budget"]["depth"], 1)
        self.assertEqual(request["search_budget"]["width"], 4)
        self.assertEqual(plan_request, request)
        env.close()

    def test_policy_registry_and_headless_clis_accept_checkpoint_free_type(self):
        self.assertIsInstance(make_policy("v1_7_analyzer_manager"), V17AnalyzerManagerPolicy)
        self.assertEqual(
            parse_arena_args(["--policy-a", "v1_7_analyzer_manager"]).policy_a,
            "v1_7_analyzer_manager",
        )
        self.assertEqual(
            parse_realtime_arena_args(
                ["--policy-a", "v1_7_analyzer_manager"]
            ).policy_a,
            "v1_7_analyzer_manager",
        )
        self.assertEqual(
            parse_spectate_args(["--policy-a", "v1_7_analyzer_manager"]).policy_a,
            "v1_7_analyzer_manager",
        )


if __name__ == "__main__":
    unittest.main()
