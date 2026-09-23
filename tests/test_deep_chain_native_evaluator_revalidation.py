from __future__ import annotations

import copy
import unittest

from eval.deep_chain_native_evaluator_profile import (
    COMBINED_BUDGET_MS,
    EXPANDED_NODE_COUNT,
    EXPECTED_CONFIG_SHA256,
    EXPECTED_CORPUS_DIGEST,
    EXPECTED_CORPUS_SHA256,
    PROFILE_SAMPLES,
)
from eval.deep_chain_native_evaluator_revalidation import (
    END_TO_END_P95_MAX_MS,
    NATIVE_TOTAL_P95_MAX_MS,
    _profile_issues,
    derive_decision,
    parse_args,
)


def _semantic():
    return {
        "fixture": {"record_count": 8, "mismatch_count": 0},
        "transition_oracle": {"record_count": 11_264, "mismatch_count": 0},
        "python_native_evaluator": {
            "record_count": 512,
            "mismatch_count": 0,
            "invalid_selected_count": 0,
        },
        "determinism": {"mismatch_count": 0},
    }


def _profile(*, combined=800.0, native=850.0, end_to_end=950.0):
    samples = []
    for index in range(PROFILE_SAMPLES):
        samples.append(
            {
                "sample": index,
                "operations": EXPANDED_NODE_COUNT,
                "record_count": 512,
                "transition_evaluator_ns": int(combined * 1_000_000),
                "native_call_total_ns": int(native * 1_000_000),
                "end_to_end_ns": int(end_to_end * 1_000_000),
                "checksum": 123,
            }
        )
    return {
        "samples": samples,
        "aggregate": {
            "sample_count": PROFILE_SAMPLES,
            "record_count": 512,
            "operations_per_sample": EXPANDED_NODE_COUNT,
            "operations_exact": True,
            "outlier_removal": "none",
            "transition_evaluator_p95_ms": combined,
            "native_call_total_p95_ms": native,
            "end_to_end_p95_ms": end_to_end,
            "determinism_mismatch_count": 0,
        },
    }


def _source():
    return {
        "normal_hot_path_heap_allocations": 0,
        "child_state_bytes": 80,
        "hot_result_bytes": 24,
    }


def _derive(profile=None):
    return derive_decision(
        semantic=_semantic(),
        profile=_profile() if profile is None else profile,
        source=_source(),
        wheel={"passed": True},
        source_tree_dirty=False,
        corpus_sha256=EXPECTED_CORPUS_SHA256,
        corpus_digest=EXPECTED_CORPUS_DIGEST,
        config_sha256=EXPECTED_CONFIG_SHA256,
    )


class NativeEvaluatorRevalidationTest(unittest.TestCase):
    def test_contract_is_the_puyo_201_five_by_600k_gate(self):
        args = parse_args(["run"])

        self.assertEqual(args.command, "run")
        self.assertEqual(EXPANDED_NODE_COUNT, 600_000)
        self.assertEqual(PROFILE_SAMPLES, 5)
        self.assertEqual(COMBINED_BUDGET_MS, 820.625)
        self.assertEqual(NATIVE_TOTAL_P95_MAX_MS, 900.0)
        self.assertEqual(END_TO_END_P95_MAX_MS, 1_000.0)

    def test_go_requires_every_gate_and_only_unblocks_puyo_202(self):
        result = _derive()

        self.assertTrue(result["passed"])
        self.assertEqual(result["decision"], "GO")
        self.assertTrue(all(result["checks"].values()))
        self.assertTrue(result["puyo_202_block_removal_candidate"])
        self.assertTrue(result["qa_pr_may_merge"])
        self.assertFalse(result["production_backend_promoted"])
        self.assertTrue(
            result["production_backend_promotion_requires_separate_decision"]
        )

    def test_latency_failure_is_no_go_and_requires_follow_up(self):
        result = _derive(
            _profile(
                combined=COMBINED_BUDGET_MS + 0.001,
                native=NATIVE_TOTAL_P95_MAX_MS + 0.001,
                end_to_end=END_TO_END_P95_MAX_MS + 0.001,
            )
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["decision"], "NO_GO")
        self.assertFalse(result["puyo_202_block_removal_candidate"])
        self.assertFalse(result["qa_pr_may_merge"])
        self.assertTrue(result["follow_up_required"])
        self.assertEqual(
            result["failed_checks"],
            [
                "transition_evaluator_p95",
                "native_call_total_p95",
                "end_to_end_p95",
            ],
        )

    def test_semantic_count_drift_rejects_revalidation(self):
        semantic = _semantic()
        semantic["transition_oracle"]["record_count"] -= 1
        result = derive_decision(
            semantic=semantic,
            profile=_profile(),
            source=_source(),
            wheel={"passed": True},
            source_tree_dirty=False,
            corpus_sha256=EXPECTED_CORPUS_SHA256,
            corpus_digest=EXPECTED_CORPUS_DIGEST,
            config_sha256=EXPECTED_CONFIG_SHA256,
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "transition_11264_zero_mismatches",
            result["failed_checks"],
        )

    def test_profile_verifier_recomputes_nearest_rank_p95(self):
        profile = _profile()
        profile["samples"][-1]["transition_evaluator_ns"] += 1_000_000
        profile["aggregate"]["transition_evaluator_p95_ms"] += 1.0

        self.assertEqual(_profile_issues(profile), [])

        drifted = copy.deepcopy(profile)
        drifted["aggregate"]["transition_evaluator_p95_ms"] -= 0.001
        self.assertIn(
            "transition_evaluator_p95_ms is not nearest-rank p95",
            _profile_issues(drifted),
        )


if __name__ == "__main__":
    unittest.main()
