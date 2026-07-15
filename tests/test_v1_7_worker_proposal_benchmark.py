import json
import tempfile
import unittest
from pathlib import Path

from eval.v1_7_worker_proposal_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    ProposalBenchmarkConfiguration,
    run_benchmark,
    verify_benchmark,
)


class TestV17WorkerProposalBenchmark(unittest.TestCase):
    def test_configuration_rejects_invalid_budgets(self):
        with self.assertRaises(ValueError):
            ProposalBenchmarkConfiguration("invalid", 0, 24)
        with self.assertRaises(ValueError):
            ProposalBenchmarkConfiguration("invalid", 2, 0)

    def test_smoke_records_latency_memory_and_contract_checks(self):
        configurations = (
            ProposalBenchmarkConfiguration("smoke-k2-n24", 2, 24, depth=1, width=4),
        )
        with tempfile.TemporaryDirectory() as directory:
            summary = run_benchmark(
                directory,
                seeds=(169,),
                repetitions=2,
                configurations=configurations,
            )
            verified = verify_benchmark(directory)
            records = json.loads(
                (Path(directory) / "benchmark_records.json").read_text(
                    encoding="utf-8"
                )
            )["records"]

        self.assertEqual(summary["schema_version"], BENCHMARK_SCHEMA_VERSION)
        self.assertTrue(summary["checks"]["passed"])
        self.assertEqual(verified["status"], "passed")
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["latency_ms"] > 0.0 for record in records))
        self.assertTrue(all(record["peak_memory_bytes"] > 0 for record in records))
        self.assertEqual(
            len({record["deterministic_digest"] for record in records}),
            1,
        )
        self.assertTrue(all(record["checks"]["round_trip"] for record in records))

    def test_verify_rejects_modified_artifact(self):
        configurations = (
            ProposalBenchmarkConfiguration("smoke-k1-n24", 1, 24, depth=1, width=4),
        )
        with tempfile.TemporaryDirectory() as directory:
            run_benchmark(
                directory,
                seeds=(170,),
                repetitions=1,
                configurations=configurations,
            )
            summary_path = Path(directory) / "benchmark_summary.json"
            summary_path.write_text(
                summary_path.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_benchmark(directory)


if __name__ == "__main__":
    unittest.main()
