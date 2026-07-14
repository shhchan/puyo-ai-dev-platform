import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import yaml

from agents.state_analyzer import StateAnalyzer
from agents.v1_7_tactics import (
    DEFAULT_TACTIC_REGISTRY_PATH,
    TACTIC_ARTIFACT_SCHEMA_VERSION,
    TACTIC_DIAGNOSTICS_SCHEMA_VERSION,
    TACTIC_SCHEMA_VERSION,
    TacticRegistry,
    build_tactic_diagnostics,
    load_tactic_registry,
    write_tactic_registry_artifact,
)
from eval.analyzer_scenarios import load_scenarios, scenario_input
from src.core.constants import GRID_HEIGHT, GRID_WIDTH


class TestV17TacticRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_tactic_registry()
        cls.base_input = scenario_input(load_scenarios()[0])

    def test_default_registry_has_versioned_initial_intents_and_all_sections(self):
        self.assertEqual(self.registry.schema_version, TACTIC_SCHEMA_VERSION)
        self.assertEqual(self.registry.registry_version, "v1.7.0")
        self.assertEqual(
            [tactic.identity.tactic_id for tactic in self.registry.tactics],
            [
                "build_main",
                "prepare_response",
                "counter_or_return",
                "pressure",
                "lethal_attack",
                "all_clear",
                "fire_main",
                "survive",
            ],
        )
        kinds = {
            parameter.kind
            for tactic in self.registry.tactics
            for section in tactic.parameters.values()
            for parameter in section.values()
        }
        self.assertEqual(kinds, {"continuous", "integer", "discrete"})
        for tactic in self.registry.to_dict()["tactics"]:
            self.assertTrue(
                {
                    "identity",
                    "applicability",
                    "objective",
                    "constraints",
                    "planner",
                    "termination",
                    "fallback",
                    "diagnostics",
                }.issubset(tactic)
            )

        build_main = self.registry.tactic("build_main")
        defaults = build_main.resolve_parameters()
        self.assertEqual(build_main.identity.version, "1.1")
        self.assertEqual(defaults["objective"]["target_chain"], 10)
        self.assertEqual(
            defaults["constraints"]["trigger_preservation"],
            "required",
        )
        self.assertEqual(build_main.parameters["planner"]["beam_depth"].maximum, 10)

    def test_registry_rejects_unsupported_schema_version(self):
        payload = yaml.safe_load(DEFAULT_TACTIC_REGISTRY_PATH.read_text(encoding="utf-8"))
        payload["schema_version"] = "tactic-schema-v0"

        with self.assertRaisesRegex(ValueError, "unsupported tactic schema"):
            TacticRegistry.from_dict(payload)

    def test_diagnostics_preserve_schema_versions_and_parameter_values(self):
        diagnostics = StateAnalyzer().analyze(self.base_input)
        payload = build_tactic_diagnostics(
            self.registry,
            self.base_input,
            diagnostics,
            parameter_overrides={
                "build_main": {
                    "objective": {"target_chain": 8},
                    "constraints": {"danger_tolerance": 0.7},
                }
            },
        )

        self.assertEqual(payload["schema_version"], TACTIC_DIAGNOSTICS_SCHEMA_VERSION)
        self.assertEqual(payload["tactic_schema_version"], TACTIC_SCHEMA_VERSION)
        self.assertFalse(payload["selection_performed"])
        self.assertEqual(len(payload["candidates"]), 8)
        build_main = next(item for item in payload["candidates"] if item["tactic_id"] == "build_main")
        self.assertEqual(build_main["parameters"]["objective"]["target_chain"], 8)
        self.assertEqual(build_main["parameters"]["constraints"]["danger_tolerance"], 0.7)
        json.dumps(payload)

    def test_parameter_overrides_are_schema_validated(self):
        diagnostics = StateAnalyzer().analyze(self.base_input)

        with self.assertRaisesRegex(ValueError, "target_chain is above max"):
            build_tactic_diagnostics(
                self.registry,
                self.base_input,
                diagnostics,
                parameter_overrides={"build_main": {"objective": {"target_chain": 20}}},
            )

    def test_all_clear_distinguishes_initial_pending_consumed_and_renewed_contexts(self):
        empty_board = tuple(tuple("EMPTY" for _ in range(GRID_WIDTH)) for _ in range(GRID_HEIGHT))
        initial = replace(
            self.base_input,
            own=replace(
                self.base_input.own,
                board=empty_board,
                score_carry=69,
                all_clear_achieved=False,
                all_clear_bonus_pending=False,
                all_clear_bonus_consumed=False,
            ),
        )
        initial_payload = self._all_clear(initial)
        self.assertFalse(initial_payload["eligible"])
        self.assertIn("initial_empty", initial_payload["active_contexts"])
        self.assertEqual(initial_payload["analyzer_inputs"]["input.own.score_carry"], 69)

        pending = replace(initial, own=replace(initial.own, all_clear_achieved=True, all_clear_bonus_pending=True))
        pending_payload = self._all_clear(pending)
        self.assertTrue(pending_payload["eligible"])
        self.assertIn("achieved", pending_payload["active_contexts"])
        self.assertIn("bonus_pending", pending_payload["active_contexts"])
        self.assertNotIn("bonus_consumed", pending_payload["active_contexts"])

        consumed = replace(
            initial,
            own=replace(
                initial.own,
                all_clear_achieved=False,
                all_clear_bonus_pending=False,
                all_clear_bonus_consumed=True,
            ),
        )
        consumed_payload = self._all_clear(consumed)
        self.assertTrue(consumed_payload["eligible"])
        self.assertEqual(consumed_payload["active_contexts"], ["bonus_consumed"])

        renewed = replace(consumed, own=replace(consumed.own, all_clear_bonus_pending=True))
        renewed_payload = self._all_clear(renewed)
        self.assertIn("bonus_pending", renewed_payload["active_contexts"])
        self.assertIn("bonus_consumed", renewed_payload["active_contexts"])
        self.assertIn("bonus_consumed_and_renewed", renewed_payload["active_contexts"])

    def test_all_clear_objective_uses_runtime_attack_conversion_not_fixed_thirty_units(self):
        all_clear = self.registry.tactic("all_clear")

        self.assertEqual(all_clear.objective["all_clear_bonus_score"], 2100)
        self.assertEqual(all_clear.objective["attack_conversion"], "score_carry_plus_attack_score_delta")
        self.assertFalse(all_clear.objective["fixed_total_attack"])
        self.assertIn("input.own.score_carry", all_clear.objective["input_refs"])

    def test_registry_artifact_round_trips_with_diagnostics(self):
        diagnostics = build_tactic_diagnostics(
            self.registry,
            self.base_input,
            StateAnalyzer().analyze(self.base_input),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tactic-registry.json"
            artifact = write_tactic_registry_artifact(path, self.registry, diagnostics)
            loaded = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(artifact["schema_version"], TACTIC_ARTIFACT_SCHEMA_VERSION)
        self.assertEqual(loaded["tactic_schema_version"], TACTIC_SCHEMA_VERSION)
        self.assertEqual(
            loaded["diagnostics"]["candidates"][0]["parameters"],
            diagnostics["candidates"][0]["parameters"],
        )

    def _all_clear(self, analyzer_input):
        payload = build_tactic_diagnostics(
            self.registry,
            analyzer_input,
            StateAnalyzer().analyze(analyzer_input),
        )
        return next(item for item in payload["candidates"] if item["tactic_id"] == "all_clear")


if __name__ == "__main__":
    unittest.main()
