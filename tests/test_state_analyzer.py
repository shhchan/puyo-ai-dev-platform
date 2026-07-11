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

    def test_two_chain_attack_uses_generic_option_schema(self):
        diagnostics = StateAnalyzer().analyze(scenario_input(self.scenarios[0])).to_dict()

        self.assertGreaterEqual(diagnostics["own"]["forecast"]["main_chain"]["chain_count"], 2)
        self.assertTrue(
            any(2 in option["chain_group_counts"] for option in diagnostics["own"]["attack_options"])
        )
        self.assertNotIn("kind", diagnostics["own"]["attack_options"][0])
        self.assertNotIn("two_double", json.dumps(diagnostics))

    def test_scenario_suite_and_report_pass(self):
        results = evaluate_scenarios(self.scenarios)
        report = build_report(results)

        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(report["schema_version"], SCENARIO_REPORT_SCHEMA_VERSION)
        self.assertEqual(report["summary"]["failed"], 0)

    def test_cli_writes_versioned_json_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analyzer-report.json"

            exit_code = main(["--json", str(path)])
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], SCENARIO_REPORT_SCHEMA_VERSION)
        self.assertEqual(payload["summary"]["failed"], 0)


if __name__ == "__main__":
    unittest.main()
