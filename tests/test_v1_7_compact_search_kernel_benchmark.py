import argparse
import tempfile
import unittest
from pathlib import Path

from eval.v1_7_compact_search_kernel_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    DEFAULT_FIXTURE_PATH,
    DEFAULT_MAX_TURNS,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REPETITIONS,
    DEFAULT_SEED_COUNT,
    DEFAULT_SEED_START,
    MINIMUM_TRANSITIONS,
    evaluate_fixtures,
    evaluate_repetition,
    evaluate_seeded_corpus,
    parse_args,
    run_benchmark,
    verify_benchmark,
)


class TestCompactSearchKernelBenchmark(unittest.TestCase):
    def test_defaults_define_two_repeat_minimum_thousand_transition_corpus(self):
        args = parse_args(["run"])

        self.assertEqual(
            DEFAULT_OUTPUT_DIR,
            "docs/benchmarks/puyo-v1-7-2-compact-search-kernel",
        )
        self.assertEqual(args.fixture, DEFAULT_FIXTURE_PATH)
        self.assertEqual(args.seed_start, DEFAULT_SEED_START)
        self.assertEqual(args.seed_count, DEFAULT_SEED_COUNT)
        self.assertEqual(args.max_turns, DEFAULT_MAX_TURNS)
        self.assertEqual(args.repetitions, DEFAULT_REPETITIONS)
        self.assertEqual(args.minimum_transitions, MINIMUM_TRANSITIONS)

    def test_fixed_golden_fixtures_have_zero_mismatch(self):
        result = evaluate_fixtures()

        self.assertEqual(result["case_count"], 9)
        self.assertEqual(result["mismatch_count"], 0)
        self.assertTrue(all(record["passed"] for record in result["records"]))
        self.assertEqual(len(result["digest"]), 64)

    def test_seeded_reachable_corpus_checks_every_legal_transition(self):
        seeds = tuple(
            range(DEFAULT_SEED_START, DEFAULT_SEED_START + DEFAULT_SEED_COUNT)
        )

        result = evaluate_seeded_corpus(seeds, max_turns=DEFAULT_MAX_TURNS)

        self.assertGreaterEqual(result["transition_count"], MINIMUM_TRANSITIONS)
        self.assertEqual(result["mismatch_count"], 0)
        self.assertEqual(result["legal_mismatch_count"], 0)
        self.assertEqual(result["hash_collision_count"], 0)
        self.assertEqual(len(result["records"]), result["transition_count"])
        self.assertEqual(
            {record["seed"] for record in result["records"]},
            set(seeds),
        )

    def test_repeat_digest_and_mismatch_summary_are_deterministic(self):
        first = evaluate_repetition([123], max_turns=2)
        second = evaluate_repetition([123], max_turns=2)

        self.assertEqual(
            first["deterministic_fingerprint"],
            second["deterministic_fingerprint"],
        )
        self.assertEqual(first["fixture"]["mismatch_count"], 0)
        self.assertEqual(first["corpus"]["mismatch_count"], 0)

    def test_smoke_artifacts_record_environment_profile_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifacts"
            args = argparse.Namespace(
                output_dir=str(output),
                fixture=DEFAULT_FIXTURE_PATH,
                seed_start=123,
                seed_count=1,
                max_turns=2,
                repetitions=2,
                minimum_transitions=1,
            )

            summary = run_benchmark(args)
            verified = verify_benchmark(argparse.Namespace(artifact_dir=str(output)))

        self.assertEqual(summary["schema_version"], BENCHMARK_SCHEMA_VERSION)
        self.assertTrue(summary["passed"])
        self.assertTrue(summary["determinism"]["passed"])
        self.assertIn("python", summary["environment"])
        self.assertIn("cpu", summary["environment"])
        self.assertIn("state_size", summary["profile"])
        self.assertFalse(summary["performance_is_go_condition"])
        self.assertTrue(verified["passed"])

    def test_committed_canonical_artifacts_verify(self):
        artifact_dir = Path(DEFAULT_OUTPUT_DIR)
        result = verify_benchmark(argparse.Namespace(artifact_dir=str(artifact_dir)))

        self.assertTrue(result["passed"], result["issues"])


if __name__ == "__main__":
    unittest.main()
