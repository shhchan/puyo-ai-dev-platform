import json
import tempfile
import unittest
from pathlib import Path

from eval.benchmark_suite import (
    MetricRecord,
    choose_recommended_variant,
    run_puyo79_suite,
    summarize_metric_records,
)
from train.lineage import build_registry, validate_registry


class TestBenchmarkSuite(unittest.TestCase):
    def test_metric_summary_includes_ci95(self):
        rows = summarize_metric_records(
            [
                MetricRecord("chain_search", "beam", "max_chain", 2.0, 1),
                MetricRecord("chain_search", "beam", "max_chain", 4.0, 2),
            ]
        )

        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["mean"], 3.0)
        self.assertLess(rows[0]["ci95_low"], rows[0]["mean"])
        self.assertGreater(rows[0]["ci95_high"], rows[0]["mean"])

    def test_recommendation_prefers_feasible_variant(self):
        summaries = [
            {"suite": "chain_search", "variant": "fast", "metric": "max_chain", "mean": 2.0},
            {"suite": "realtime_paired_arena", "variant": "fast", "metric": "score_rate", "mean": 0.5},
            {"suite": "realtime_paired_arena", "variant": "fast", "metric": "policy_elapsed_ms", "mean": 10.0},
            {"suite": "chain_search", "variant": "slow", "metric": "max_chain", "mean": 10.0},
            {"suite": "realtime_paired_arena", "variant": "slow", "metric": "score_rate", "mean": 1.0},
            {"suite": "realtime_paired_arena", "variant": "slow", "metric": "policy_elapsed_ms", "mean": 500.0},
        ]

        selected = choose_recommended_variant(summaries, latency_budget_ms=80.0)

        self.assertEqual(selected["variant"], "fast")
        self.assertTrue(selected["feasible"])

    def test_suite_writes_manifest_and_lineage_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "puyo79"

            manifest = run_puyo79_suite(
                output_dir=output_dir,
                seed=1,
                games=1,
                max_steps=2,
                max_ticks=40,
                beam_depth=2,
                beam_width=4,
            )

            manifest_path = output_dir / "benchmark_manifest.json"
            report_path = output_dir / "report.md"
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            registry = build_registry([output_dir])

            self.assertEqual(saved["schema_version"], "puyo.benchmark_suite.v1")
            self.assertEqual(saved["digest"], manifest["digest"])
            self.assertTrue(report_path.exists())
            self.assertTrue(any(node.node_type == "benchmark_suite" for node in registry.nodes.values()))
            self.assertEqual(validate_registry(registry), [])


if __name__ == "__main__":
    unittest.main()
