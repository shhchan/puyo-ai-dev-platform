import argparse
import json
import tempfile
import unittest
from pathlib import Path

from agents.beam_search import (
    DIVERSE_CANDIDATE_MODE,
    LEGACY_CANDIDATE_MODE,
)
from eval.v1_7_diverse_beam_benchmark import (
    BASELINE_CONFIGURATION,
    BENCHMARK_SCHEMA_VERSION,
    DECISION_SCHEMA_VERSION,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SOURCE,
    DIVERSE_CONFIGURATION,
    RAW_SCALE_CONFIGURATION,
    CandidateConfiguration,
    _quality_gate,
    evaluate_repetition,
    load_frozen_decisions,
    parse_args,
    run_benchmark,
    verify_benchmark,
)


class TestV17DiverseBeamBenchmark(unittest.TestCase):
    @staticmethod
    def _smoke_configurations():
        return (
            CandidateConfiguration(
                BASELINE_CONFIGURATION.config_id,
                LEGACY_CANDIDATE_MODE,
                1,
                4,
                2,
                2,
            ),
            CandidateConfiguration(
                DIVERSE_CONFIGURATION.config_id,
                DIVERSE_CANDIDATE_MODE,
                1,
                4,
                2,
                2,
            ),
            CandidateConfiguration(
                RAW_SCALE_CONFIGURATION.config_id,
                LEGACY_CANDIDATE_MODE,
                2,
                8,
                4,
                2,
            ),
        )

    def test_defaults_compare_equal_budget_diversity_with_raw_scale(self):
        args = parse_args(["run"])

        self.assertEqual(
            DEFAULT_OUTPUT_DIR,
            "docs/benchmarks/puyo-v1-7-2-diverse-beam",
        )
        self.assertEqual(BASELINE_CONFIGURATION.candidate_mode, "legacy")
        self.assertEqual(DIVERSE_CONFIGURATION.candidate_mode, "diverse")
        self.assertEqual(
            (
                DIVERSE_CONFIGURATION.depth,
                DIVERSE_CONFIGURATION.width,
                DIVERSE_CONFIGURATION.probe_width,
            ),
            (
                BASELINE_CONFIGURATION.depth,
                BASELINE_CONFIGURATION.width,
                BASELINE_CONFIGURATION.probe_width,
            ),
        )
        self.assertGreater(
            RAW_SCALE_CONFIGURATION.depth,
            DIVERSE_CONFIGURATION.depth,
        )
        self.assertEqual((args.games, args.max_steps, args.repetitions), (30, 40, 2))

    def test_loads_stable_puyo_165_decision_prefix(self):
        decisions, evidence = load_frozen_decisions(
            DEFAULT_SOURCE,
            games=1,
            max_steps=2,
        )

        self.assertEqual(list(decisions), [123])
        self.assertEqual(len(decisions[123]), 2)
        self.assertEqual(evidence["available_decisions"], 1200)
        self.assertEqual(evidence["selected_decisions"], 2)
        self.assertEqual(len(evidence["sha256"]), 64)
        self.assertTrue(evidence["immutable_source"])

    def test_smoke_records_ranker_contract_and_is_deterministic(self):
        decisions, _ = load_frozen_decisions(
            DEFAULT_SOURCE,
            games=1,
            max_steps=1,
        )
        configurations = self._smoke_configurations()

        first = evaluate_repetition(
            decisions,
            configurations=configurations,
            workers=1,
        )
        second = evaluate_repetition(
            decisions,
            configurations=configurations,
            workers=1,
        )
        record = first["records"][0]
        diverse = record["configurations"][DIVERSE_CONFIGURATION.config_id]
        candidate = diverse["candidates"][0]

        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(record["schema_version"], DECISION_SCHEMA_VERSION)
        self.assertEqual(candidate["schema_version"], "puyo.diverse_beam_candidate.v1")
        self.assertEqual(candidate["rank"], 0)
        self.assertEqual(candidate["plan"][0], candidate["root_action"])
        self.assertEqual(
            candidate["build_potential"]["schema_version"],
            "puyo.build_potential.v2",
        )
        self.assertIn("trigger_recoverability", candidate)
        self.assertIn("value_breakdown", candidate)
        self.assertIn("retained", candidate["reasons"])
        self.assertEqual(diverse["illegal_actions"], [])
        self.assertEqual(diverse["game_over_actions"], [])
        self.assertEqual(diverse["scenario_budget"]["known_pair_count"], 3)

    def test_failed_quality_gate_remains_explicit(self):
        aggregate = {
            BASELINE_CONFIGURATION.config_id: {
                "reference_action_coverage": 0.8,
                "reference_path_coverage": 0.7,
                "long_chain_action_coverage": 0.8,
                "max_candidate_chain_mean": 2.0,
                "illegal_candidate_actions": 0,
                "game_over_candidate_actions": 0,
            },
            DIVERSE_CONFIGURATION.config_id: {
                "reference_action_coverage": 0.7,
                "reference_path_coverage": 0.6,
                "long_chain_action_coverage": 0.7,
                "max_candidate_chain_mean": 1.0,
                "illegal_candidate_actions": 0,
                "game_over_candidate_actions": 0,
            },
            RAW_SCALE_CONFIGURATION.config_id: {
                "reference_action_coverage": 0.9,
                "reference_path_coverage": 0.8,
                "long_chain_action_coverage": 0.9,
                "max_candidate_chain_mean": 3.0,
                "illegal_candidate_actions": 0,
                "game_over_candidate_actions": 0,
            },
        }

        gate = _quality_gate(aggregate, deterministic=True)

        self.assertFalse(gate["passed"])
        self.assertFalse(
            gate["checks"]["reference_action_coverage_non_regression"]
        )
        self.assertTrue(gate["failure_artifact_persisted"])

    def test_smoke_artifacts_verify_hashes_schemas_and_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifact"
            args = argparse.Namespace(
                source=DEFAULT_SOURCE,
                expected_source_sha256=(
                    "460f5fb26890d50117107269c342002750ecc84e0ab2d263044fe923502222c6"
                ),
                output_dir=str(output),
                games=1,
                max_steps=1,
                repetitions=2,
                workers=1,
            )

            summary = run_benchmark(
                args,
                configurations=self._smoke_configurations(),
            )
            verified = verify_benchmark(
                argparse.Namespace(artifact_dir=str(output))
            )

        self.assertEqual(summary["schema_version"], BENCHMARK_SCHEMA_VERSION)
        self.assertTrue(summary["determinism"]["passed"])
        self.assertTrue(summary["quality_gate"]["passed"])
        self.assertTrue(verified["passed"])
        json.dumps(summary)


if __name__ == "__main__":
    unittest.main()
