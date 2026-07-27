import tempfile
import unittest
from pathlib import Path

from eval.v1_7_build_main_fire_ranking_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    run_benchmark,
    verify_benchmark,
)


class TestV17BuildMainFireRankingBenchmark(unittest.TestCase):
    def test_benchmark_covers_fire_ranking_quota_parity_and_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = run_benchmark(Path(directory))
            verified = verify_benchmark(Path(directory))

        self.assertEqual(summary["schema_version"], BENCHMARK_SCHEMA_VERSION)
        self.assertTrue(summary["checks"]["passed"])
        self.assertTrue(summary["checks"]["quiet_over_premature"])
        self.assertTrue(summary["checks"]["target_over_quiet"])
        self.assertTrue(summary["checks"]["premature_terminal_score"])
        self.assertTrue(summary["checks"]["root_survivor_quota"])
        self.assertTrue(summary["checks"]["compact_authoritative_parity"])
        self.assertTrue(summary["checks"]["proposal_v2_compatibility"])
        self.assertTrue(summary["checks"]["determinism"])
        self.assertTrue(summary["checks"]["response_guard_6_of_6"])
        self.assertEqual(summary["response_guard"]["passed"], 6)
        self.assertEqual(summary["proposal_v2"]["candidate_limit"], 8)
        self.assertEqual(verified["status"], "passed")


if __name__ == "__main__":
    unittest.main()
