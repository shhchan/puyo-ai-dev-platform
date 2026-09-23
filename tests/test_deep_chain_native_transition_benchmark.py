import unittest

from eval.deep_chain_native_transition_benchmark import (
    CANONICAL_MAX_EXPANDED_NODES,
    DEFAULT_CORPUS_PATH,
    DEFAULT_OUTPUT_DIR,
    MINIMUM_TRANSITIONS,
    TRANSITION_DECISION_P95_BUDGET_MS,
    parse_args,
    verify_benchmark,
    verify_frozen_corpus,
)


class TestNativeCompactTransitionBenchmark(unittest.TestCase):
    def test_defaults_lock_canonical_budget_and_measurement_shape(self):
        args = parse_args(["run"])

        self.assertEqual(args.corpus, str(DEFAULT_CORPUS_PATH))
        self.assertEqual(args.output_dir, str(DEFAULT_OUTPUT_DIR))
        self.assertEqual(args.single_samples, 200)
        self.assertEqual(args.batch_samples, 12)
        self.assertEqual(args.batch_size, MINIMUM_TRANSITIONS)
        self.assertEqual(CANONICAL_MAX_EXPANDED_NODES, 600_000)
        self.assertEqual(TRANSITION_DECISION_P95_BUDGET_MS, 10.596)

    def test_frozen_corpus_replays_authoritative_and_python_oracles(self):
        result = verify_frozen_corpus()

        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(result["state_count"], 512)
        self.assertEqual(result["transition_count"], 11_264)
        self.assertEqual(result["checked_transition_count"], 11_264)
        self.assertEqual(result["mismatch_count"], 0)

    @unittest.skipUnless(DEFAULT_OUTPUT_DIR.exists(), "benchmark evidence is not checked in")
    def test_checked_in_evidence_is_integral_and_records_the_no_go(self):
        result = verify_benchmark()

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["issues"],
            ["native compact transition checks did not pass"],
        )


if __name__ == "__main__":
    unittest.main()
