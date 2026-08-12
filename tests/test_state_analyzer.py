import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agents.state_analyzer import (
    ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
    ANALYZER_INPUT_SCHEMA_VERSION,
    AnalyzerInput,
    StateAnalyzer,
)
from agents.beam_search import BUILD_POTENTIAL_SCHEMA_VERSION
from eval.analyzer_scenarios import (
    SCENARIO_REPORT_SCHEMA_VERSION,
    build_report,
    evaluate_scenarios,
    load_scenarios,
    main,
    scenario_input,
)
from puyo_env.versus_env import VersusPuyoEnv


class TestStateAnalyzer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = load_scenarios()

    def scenario(self, name):
        return next(scenario for scenario in self.scenarios if scenario["name"] == name)

    def test_input_schema_round_trips_through_json(self):
        analyzer_input = scenario_input(self.scenarios[0])

        payload = json.loads(json.dumps(analyzer_input.to_dict()))
        restored = AnalyzerInput.from_dict(payload)

        self.assertEqual(restored, analyzer_input)
        self.assertEqual(payload["schema_version"], ANALYZER_INPUT_SCHEMA_VERSION)

    def test_analysis_is_deterministic_and_json_compatible(self):
        analyzer = StateAnalyzer()
        analyzer_input = scenario_input(self.scenarios[0])

        first = analyzer.analyze(analyzer_input).to_dict()
        second = analyzer.analyze(analyzer_input).to_dict()

        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], ANALYZER_DIAGNOSTICS_SCHEMA_VERSION)
        self.assertIn(first["own"]["forecast"]["main_chain"], first["own"]["attack_options"])
        for player in ("own", "opponent"):
            potential = first[player]["build_potential"]
            self.assertEqual(
                potential["schema_version"],
                BUILD_POTENTIAL_SCHEMA_VERSION,
            )
            self.assertIn(
                potential["evaluation_status"],
                {"available", "not_found", "budget_exhausted"},
            )
            self.assertEqual(potential["exists"], potential["chain_count"] > 0)
            self.assertIn("trigger_alternatives", potential)
            self.assertIn("trigger_recoverability", potential)
            self.assertIn("budget", potential["search"])
            for field in (
                "predicted_chain_potential",
                "continuation_flexibility",
                "danger_margin",
            ):
                value = potential[field]
                if value is not None:
                    self.assertGreaterEqual(value, 0.0)
                    self.assertLessEqual(value, 1.0)
        json.dumps(first)

    def test_runtime_snapshot_does_not_mutate_environment(self):
        env = VersusPuyoEnv(seed=17, max_steps=10)
        _, infos = env.reset(seed=17)
        before = tuple(
            tuple(puyo.color.name for puyo in row)
            for row in env.player_states["player_0"].simulator.game.field.grid
        )

        analyzer_input = AnalyzerInput.from_runtime_info(infos["player_0"])
        StateAnalyzer().analyze(analyzer_input)

        after = tuple(
            tuple(puyo.color.name for puyo in row)
            for row in env.player_states["player_0"].simulator.game.field.grid
        )
        self.assertEqual(after, before)
        env.close()

    def test_runtime_tick_packet_uses_normalized_turn_deadline(self):
        env = VersusPuyoEnv(seed=19, max_steps=10)
        _, infos = env.reset(seed=19)
        info = dict(infos["player_0"])
        info["incoming_attack_packets"] = ({"amount": 4, "arrival_tick": 60},)
        info["incoming_turns"] = 2

        analyzer_input = AnalyzerInput.from_runtime_info(info)

        self.assertEqual(analyzer_input.own.incoming[0].deadline, 2)
        env.close()

    def test_runtime_snapshot_preserves_score_carry_and_all_clear_state_per_player(self):
        env = VersusPuyoEnv(seed=23, max_steps=10)
        env.reset(seed=23)
        env.player_states["player_0"].score_carry = 69
        env.player_states["player_1"].score_carry = 31
        env.player_states["player_0"].simulator.game.all_clear_bonus_pending = True
        env.player_states["player_1"].simulator.game.all_clear_bonus_consumed = True

        analyzer_input = AnalyzerInput.from_runtime_info(env._info("player_0"))

        self.assertEqual(analyzer_input.own.score_carry, 69)
        self.assertEqual(analyzer_input.opponent.score_carry, 31)
        self.assertTrue(analyzer_input.own.all_clear_bonus_pending)
        self.assertTrue(analyzer_input.opponent.all_clear_bonus_consumed)
        env.close()

    def test_two_chain_attack_uses_generic_option_schema(self):
        diagnostics = StateAnalyzer().analyze(scenario_input(self.scenarios[0])).to_dict()

        self.assertGreaterEqual(diagnostics["own"]["forecast"]["main_chain"]["chain_count"], 2)
        self.assertTrue(
            any(2 in option["chain_group_counts"] for option in diagnostics["own"]["attack_options"])
        )
        self.assertNotIn("kind", diagnostics["own"]["attack_options"][0])
        self.assertNotIn("two_double", json.dumps(diagnostics))

    def test_sub_chain_and_rush_scenarios_are_semantically_distinct(self):
        sub_chain = StateAnalyzer().analyze(
            scenario_input(self.scenario("short_tactical_sub_chain_keeps_main_distinct"))
        ).to_dict()
        rush = StateAnalyzer().analyze(
            scenario_input(self.scenario("immediate_rush_is_not_sub_chain"))
        ).to_dict()

        self.assertTrue(any(option["is_sub_chain"] for option in sub_chain["own"]["attack_options"]))
        self.assertFalse(any(option["is_sub_chain"] for option in rush["own"]["attack_options"]))

    def test_trigger_cells_exclude_preexisting_vanished_cells(self):
        diagnostics = StateAnalyzer().analyze(
            scenario_input(self.scenario("trigger_cell_is_the_added_ignition_puyo"))
        ).to_dict()
        main = diagnostics["own"]["forecast"]["main_chain"]

        self.assertEqual(main["trigger_cells"], ((0, 1),))
        self.assertEqual(len(main["first_chain_vanished_cells"]), 4)

    def test_mixed_incoming_packets_are_evaluated_at_each_deadline(self):
        diagnostics = StateAnalyzer().analyze(
            scenario_input(self.scenario("mixed_incoming_packets_keep_deadlines_separate"))
        ).to_dict()

        self.assertEqual(
            [(window["deadline"], window["amount_due"]) for window in diagnostics["incoming"]["windows"]],
            [(1, 3), (3, 15)],
        )
        self.assertTrue(diagnostics["incoming"]["can_cancel"])

    def test_scenario_suite_and_report_pass(self):
        results = evaluate_scenarios(self.scenarios)
        report = build_report(results)

        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(report["schema_version"], SCENARIO_REPORT_SCHEMA_VERSION)
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertGreaterEqual(report["summary"]["scenarios"], 24)
        self.assertIn("input", report["results"][0])
        self.assertTrue(report["results"][0]["non_goals"])

    def test_cli_writes_versioned_json_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analyzer-report.json"

            exit_code = main(["--json", str(path)])
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], SCENARIO_REPORT_SCHEMA_VERSION)
        self.assertEqual(payload["summary"]["failed"], 0)
        self.assertIn("board", payload["results"][0]["input"]["own"])

    def test_cli_can_render_reproducible_board_diagrams(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = main(["--show-boards"])

        self.assertEqual(exit_code, 0)
        self.assertIn("own (top -> bottom):", output.getvalue())
        self.assertIn("|RGGYGG|", output.getvalue())


if __name__ == "__main__":
    unittest.main()
