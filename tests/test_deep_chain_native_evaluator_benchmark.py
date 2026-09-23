import unittest

from eval.deep_chain_native_evaluator_benchmark import (
    COMBINED_P95_MAX_MS,
    END_TO_END_P95_MAX_MS,
    EXPANDED_NODE_COUNT,
    NATIVE_TOTAL_P95_MAX_MS,
    PROFILE_SAMPLES,
    derive_decision,
    nearest_rank,
    parse_args,
)


def _semantic():
    return {
        "fixture": {"mismatch_count": 0},
        "transition_oracle": {"mismatch_count": 0},
        "python_native_evaluator": {
            "mismatch_count": 0,
            "invalid_selected_count": 0,
        },
        "determinism": {"mismatch_count": 0},
    }


def _profile(*, combined=800.0, native=850.0, end_to_end=950.0):
    return {
        "aggregate": {
            "operations_exact": True,
            "transition_evaluator_p95_ms": combined,
            "native_call_total_p95_ms": native,
            "end_to_end_p95_ms": end_to_end,
            "determinism_mismatch_count": 0,
        }
    }


def _source():
    return {
        "normal_hot_path_heap_allocations": 0,
        "child_state_bytes": 80,
        "hot_result_bytes": 24,
    }


class TestNativeChainStructureBenchmark(unittest.TestCase):
    def test_contract_locks_exact_operations_and_outer_envelopes(self):
        args = parse_args(["run"])

        self.assertEqual(EXPANDED_NODE_COUNT, 600_000)
        self.assertEqual(PROFILE_SAMPLES, 5)
        self.assertEqual(COMBINED_P95_MAX_MS, 820.625)
        self.assertEqual(NATIVE_TOTAL_P95_MAX_MS, 900.0)
        self.assertEqual(END_TO_END_P95_MAX_MS, 1_000.0)
        self.assertEqual(args.command, "run")

    def test_nearest_rank_retains_high_observation(self):
        values = [10, 20, 30, 40, 50]

        self.assertEqual(nearest_rank(values, 50), 30.0)
        self.assertEqual(nearest_rank(values, 95), 50.0)

    def test_decision_passes_only_when_every_gate_passes(self):
        result = derive_decision(_semantic(), _profile(), _source())

        self.assertTrue(result["passed"])
        self.assertEqual(result["decision"], "GO")
        self.assertTrue(all(result["gate_checks"].values()))

    def test_latency_failure_requires_unmerged_close_and_blocks_puyo_202(self):
        result = derive_decision(
            _semantic(),
            _profile(combined=821.0, native=901.0, end_to_end=1_001.0),
            _source(),
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["decision"], "NO_GO_CLOSE_PR_UNMERGED")
        self.assertEqual(
            result["failed_gates"],
            [
                "transition_evaluator_p95",
                "native_call_total_p95",
                "end_to_end_p95",
            ],
        )
        self.assertTrue(result["on_failure"]["close_implementation_pr_unmerged"])
        self.assertFalse(result["on_failure"]["puyo_202_may_start"])


if __name__ == "__main__":
    unittest.main()
