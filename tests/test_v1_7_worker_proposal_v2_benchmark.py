import json
import tempfile
import unittest
from pathlib import Path

from eval.v1_7_worker_proposal_v2_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    run_benchmark,
    verify_benchmark,
)


class TestV17WorkerProposalV2Benchmark(unittest.TestCase):
    def test_smoke_records_status_coverage_size_projection_and_digests(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = run_benchmark(
                directory,
                seeds=(175,),
                repetitions=2,
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
        self.assertEqual(
            len({record["serialized_digest"] for record in records}),
            1,
        )
        self.assertEqual(
            len({record["ranker_input_digest"] for record in records}),
            1,
        )
        self.assertGreater(summary["serialized_bytes"]["p50"], 0)
        self.assertGreater(summary["projection_ms"]["p50"], 0.0)
        self.assertGreater(
            summary["v1_zero_missingness_confusion_count"],
            0,
        )
        self.assertEqual(
            set(summary["status_counts"]),
            {
                "evaluated",
                "not_evaluated",
                "budget_exhausted",
                "legacy_missing",
            },
        )

    def test_verify_rejects_modified_field_dictionary(self):
        with tempfile.TemporaryDirectory() as directory:
            run_benchmark(directory, seeds=(176,), repetitions=1)
            fields_path = Path(directory) / "field_dictionary.json"
            fields_path.write_text(
                fields_path.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_benchmark(directory)


if __name__ == "__main__":
    unittest.main()
