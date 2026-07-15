import argparse
import csv
import tempfile
import unittest
from pathlib import Path

from agents.beam_search import BeamSearchPolicy
from eval.v1_7_benchmark import _observation, _runtime_info
from eval.v1_7_build_main_benchmark import (
    BuildMainConfiguration,
    ForcedBuildMainPolicy,
)
from eval.v1_7_search_diagnostics_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    DECISION_SCHEMA_VERSION,
    DEFAULT_CURRENT_BUDGET,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REFERENCE_BUDGET,
    SearchBudget,
    evaluate_repetition,
    evaluate_seed,
    load_seed_manifest,
    parse_args,
    run_benchmark,
    validate_decision_record,
    verify_benchmark,
)
from puyo_env.actions import action_to_placement
from src.core.headless import HeadlessPuyoSimulator


class TestV17SearchDiagnosticsBenchmark(unittest.TestCase):
    def test_defaults_compare_d6_w48_p16_with_dominating_reference(self):
        args = parse_args(["run"])

        self.assertEqual(
            DEFAULT_OUTPUT_DIR,
            "docs/benchmarks/puyo-v1-7-2-search-diagnostics-v2",
        )
        self.assertEqual(DEFAULT_CURRENT_BUDGET.config_id, "d6-w48-p16")
        self.assertEqual(DEFAULT_REFERENCE_BUDGET.config_id, "d8-w64-p32")
        self.assertEqual((args.games, args.max_steps, args.repetitions), (30, 40, 2))
        self.assertGreater(args.reference_depth, args.current_depth)
        self.assertGreaterEqual(args.reference_width, args.current_width)
        self.assertGreaterEqual(args.reference_probe_width, args.current_probe_width)

    def test_seed_manifest_reuses_selected_build_main_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "seed_results.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["config_id", "seed"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"config_id": "d6-w48-p16", "seed": 125},
                        {"config_id": "other", "seed": 999},
                        {"config_id": "d6-w48-p16", "seed": 123},
                        {"config_id": "d6-w48-p16", "seed": 124},
                    ]
                )

            manifest = load_seed_manifest(source, games=3, max_steps=40)

        self.assertEqual(manifest["seeds"], [123, 124, 125])
        self.assertEqual(manifest["moves_per_seed"], 40)
        self.assertEqual(len(manifest["source_sha256"]), 64)

    def test_diagnostic_current_search_matches_production_selection(self):
        production_simulator = HeadlessPuyoSimulator(seed=123)
        diagnostic_simulator = HeadlessPuyoSimulator(seed=123)
        opponent = HeadlessPuyoSimulator(seed=1_000_126)
        production = ForcedBuildMainPolicy(BuildMainConfiguration(6, 48, 16))
        diagnostic = BeamSearchPolicy(DEFAULT_CURRENT_BUDGET.beam_config())
        production_actions = []
        diagnostic_actions = []
        for step in range(3):
            production_info = _runtime_info(
                production_simulator,
                opponent,
                step_count=step,
                max_steps=40,
            )
            production_observation = _observation(
                production_simulator,
                opponent,
                step_count=step,
                max_steps=40,
            )
            diagnostic_info = _runtime_info(
                diagnostic_simulator,
                opponent,
                step_count=step,
                max_steps=40,
            )
            diagnostic_observation = _observation(
                diagnostic_simulator,
                opponent,
                step_count=step,
                max_steps=40,
            )
            production_action = production.select_action(
                production_observation,
                production_info,
            )
            diagnostic_action = diagnostic.select_action(
                diagnostic_observation,
                diagnostic_info,
            )
            production_actions.append(production_action)
            diagnostic_actions.append(diagnostic_action)
            production_simulator.step(action_to_placement(production_action))
            diagnostic_simulator.step(action_to_placement(diagnostic_action))

        self.assertEqual(diagnostic_actions, production_actions)

    def test_smoke_records_candidate_stages_regret_and_schema(self):
        result = evaluate_seed(
            123,
            max_steps=2,
            current_budget=SearchBudget(1, 4, 2),
            reference_budget=SearchBudget(2, 8, 4),
        )

        self.assertEqual(result["summary"]["decisions"], 2)
        self.assertEqual(len(result["deterministic_digest"]), 64)
        for record in result["decisions"]:
            self.assertEqual(record["schema_version"], DECISION_SCHEMA_VERSION)
            self.assertEqual(validate_decision_record(record), [])
            self.assertIn(
                record["comparison"]["failure_class"],
                {
                    "candidate_coverage",
                    "ranking",
                    "horizon_or_uncertainty",
                    "safety_constraint",
                    "none",
                },
            )
            candidate = record["current"]["candidates"][0]
            self.assertIn("base_prune_depth", candidate["stages"])
            self.assertIn("potential_probe_depth", candidate["stages"])
            self.assertIn("final_prune_depth", candidate["stages"])
            self.assertIn("fire_cost", candidate)

    def test_repeated_smoke_has_identical_latency_free_digest(self):
        kwargs = {
            "max_steps": 2,
            "current_budget": SearchBudget(1, 4, 2),
            "reference_budget": SearchBudget(2, 8, 4),
            "workers": 1,
            "include_decisions": False,
        }

        first = evaluate_repetition([123], **kwargs)
        second = evaluate_repetition([123], **kwargs)

        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(
            first["aggregate"]["failure_class_counts"],
            second["aggregate"]["failure_class_counts"],
        )

    def test_smoke_artifacts_verify_hashes_and_schemas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["config_id", "seed"])
                writer.writeheader()
                writer.writerow({"config_id": "smoke", "seed": 123})
            output = root / "artifacts"
            args = argparse.Namespace(
                output_dir=str(output),
                seed_source=str(source),
                source_config_id="smoke",
                games=1,
                max_steps=1,
                workers=1,
                repetitions=2,
                current_depth=1,
                current_width=4,
                current_probe_width=2,
                reference_depth=2,
                reference_width=8,
                reference_probe_width=4,
            )

            summary = run_benchmark(args)
            verified = verify_benchmark(argparse.Namespace(artifact_dir=str(output)))

        self.assertEqual(summary["schema_version"], BENCHMARK_SCHEMA_VERSION)
        self.assertTrue(summary["determinism"]["passed"])
        self.assertTrue(verified["passed"])


if __name__ == "__main__":
    unittest.main()
