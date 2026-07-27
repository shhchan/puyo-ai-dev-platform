import json
import tempfile
import unittest
from pathlib import Path

from eval.v1_7_future_sampling_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    run_benchmark,
    verify_benchmark,
)


class TestV17FutureSamplingBenchmark(unittest.TestCase):
    def test_benchmark_covers_sampling_reproducibility_and_proposal_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = run_benchmark(directory)
            verified = verify_benchmark(directory)
            records = json.loads(
                (Path(directory) / "sampling_records.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(summary["schema_version"], BENCHMARK_SCHEMA_VERSION)
        self.assertTrue(summary["checks"]["passed"])
        self.assertTrue(summary["checks"]["known_prefix_preserved"])
        self.assertTrue(summary["checks"]["authoritative_generator_match"])
        self.assertTrue(summary["checks"]["same_seed_queue_digest"])
        self.assertTrue(summary["checks"]["different_seed_changes_queue"])
        self.assertTrue(summary["checks"]["independent_samples"])
        self.assertTrue(summary["checks"]["canonical_has_no_two_pair_cycle"])
        self.assertTrue(summary["checks"]["canonical_profiles_are_seeded"])
        self.assertTrue(summary["checks"]["legacy_fixed_six_is_explicit"])
        self.assertTrue(
            summary["checks"]["same_seed_latency_free_proposal_digest"]
        )
        self.assertTrue(
            summary["checks"]["proposal_v2_k8_stable_ids_and_masks"]
        )
        self.assertTrue(
            summary["checks"]["sample_order_invariant_ranker_input"]
        )
        self.assertTrue(summary["checks"]["sample_id_not_ranker_feature"])
        self.assertEqual(
            records["same_seed_first"]["queue_digests"],
            records["same_seed_second"]["queue_digests"],
        )
        self.assertEqual(verified["status"], "passed")


if __name__ == "__main__":
    unittest.main()
