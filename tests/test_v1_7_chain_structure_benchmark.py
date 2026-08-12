import argparse
import json
import tempfile
import unittest
from pathlib import Path

from eval.v1_7_chain_structure_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    build_summary,
    evaluate_corpora,
    verify_benchmark,
    write_artifacts,
)


class TestV17ChainStructureBenchmark(unittest.TestCase):
    def test_fixed_and_tuning_ablation_is_deterministic(self):
        first = evaluate_corpora()
        repeated = evaluate_corpora()
        records = {record["case_id"]: record for record in first["records"]}

        self.assertEqual(
            first["deterministic_digest"], repeated["deterministic_digest"]
        )
        self.assertEqual(first["corpus_counts"], {"fixed": 4, "tuning": 4})
        self.assertFalse(first["determinism_mismatches"])
        self.assertFalse(first["symmetry_mismatches"])
        self.assertFalse(first["budget_violations"])
        self.assertGreater(
            records["fixed-extendable-high"]["score"],
            records["fixed-unreachable-high"]["score"],
        )
        self.assertEqual(
            records["fixed-extendable-high"]["ranks"]["chain_structure"],
            1,
        )
        self.assertEqual(
            records["fixed-unreachable-high"]["ranks"]["chain_structure"],
            2,
        )

    def test_summary_and_artifacts_record_required_evidence(self):
        summary = build_summary(profile_repetitions=1)
        self.assertEqual(summary["schema_version"], BENCHMARK_SCHEMA_VERSION)
        self.assertTrue(summary["passed"])
        self.assertTrue(summary["rank_changes"])
        self.assertGreater(summary["profile"]["evaluation_count"], 0)
        self.assertGreater(summary["profile"]["node_throughput_per_second"], 0.0)
        self.assertIn("weight_version", summary)
        self.assertIn("config", summary)
        self.assertIn("git_commit", summary)
        self.assertIn("seed", summary)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_artifacts(summary, output, command="benchmark-test")
            expected = {
                "benchmark_manifest.json",
                "benchmark_report.md",
                "benchmark_summary.json",
                "configuration_results.json",
                "determinism.json",
            }
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            manifest = json.loads(
                (output / "benchmark_manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["passed"])
            self.assertEqual(manifest["weight_version"], summary["weight_version"])
            self.assertTrue(all(item["exists"] for item in manifest["artifacts"]))
            verified = verify_benchmark(argparse.Namespace(artifact_dir=str(output)))
            self.assertTrue(verified["passed"])

            records_path = output / "configuration_results.json"
            records_path.write_text("{}\n", encoding="utf-8")
            tampered = verify_benchmark(argparse.Namespace(artifact_dir=str(output)))
            self.assertFalse(tampered["passed"])
            self.assertIn(
                "artifact hash mismatch: configuration_results.json",
                tampered["issues"],
            )

    def test_committed_artifacts_verify(self):
        result = verify_benchmark(
            argparse.Namespace(
                artifact_dir="docs/benchmarks/puyo-v1-7-2-chain-structure"
            )
        )

        self.assertTrue(result["passed"], result["issues"])


if __name__ == "__main__":
    unittest.main()
