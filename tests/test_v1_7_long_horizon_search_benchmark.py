import json
import tempfile
import unittest
from pathlib import Path

from eval.v1_7_long_horizon_search_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    AblationConfiguration,
    run_benchmark,
)


class TestLongHorizonSearchBenchmark(unittest.TestCase):
    def test_configuration_rejects_invalid_backend_and_budget(self):
        with self.assertRaises(ValueError):
            AblationConfiguration(
                "invalid",
                "invalid",
                "unknown",
                "legacy",
                1,
                1,
                1,
                1,
                False,
            )
        with self.assertRaises(ValueError):
            AblationConfiguration(
                "invalid",
                "invalid",
                "compact",
                "height",
                0,
                1,
                1,
                1,
                False,
            )

    def test_smoke_writes_manifest_ablation_and_determinism_artifacts(self):
        matrix = (
            AblationConfiguration(
                "baseline",
                "baseline",
                "legacy_simulator",
                "legacy",
                1,
                4,
                1,
                30,
                False,
            ),
            AblationConfiguration(
                "compact-tt",
                "transposition_table",
                "compact",
                "height",
                2,
                4,
                1,
                220,
                True,
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_benchmark(
                output_dir=directory,
                seeds=(174,),
                matrix=matrix,
            )
            target = Path(directory)
            manifest = json.loads(
                (target / "benchmark_manifest.json").read_text(encoding="utf-8")
            )
            records = json.loads(
                (target / "seed_results.json").read_text(encoding="utf-8")
            )

            self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA_VERSION)
            self.assertTrue(manifest["count_budget_authoritative"])
            self.assertEqual(manifest["wall_clock_mode"], "observational")
            self.assertFalse(manifest["canonical_gate_reused"])
            self.assertEqual(len(records), 2)
            self.assertEqual(records[1]["schema_version"], BENCHMARK_SCHEMA_VERSION)
            self.assertEqual(records[1]["known_pair_count"], 3)
            self.assertTrue(records[1]["scenario_sequence_digests"])
            self.assertTrue(result["determinism"]["match"])
            self.assertEqual(
                [row["stage"] for row in result["ablation"]["summaries"]],
                ["baseline", "transposition_table"],
            )
            report = (target / "benchmark_report.md").read_text(encoding="utf-8")
            self.assertIn("Stop / Go", report)
            self.assertIn("expanded-node counts are authoritative", report)
            self.assertIn("STOP for evaluator/pruning diagnosis", report)


if __name__ == "__main__":
    unittest.main()
