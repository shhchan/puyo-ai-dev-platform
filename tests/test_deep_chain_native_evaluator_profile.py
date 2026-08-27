import unittest

from eval.deep_chain_native_evaluator_profile import (
    COMBINED_BUDGET_MS,
    EVALUATOR_BUDGET_MS,
    EXPANDED_NODE_COUNT,
    EXPECTED_CONFIG_SHA256,
    EXPECTED_CORPUS_DIGEST,
    EXPECTED_CORPUS_SHA256,
    MIN_ATTRIBUTED_SHARE,
    PROFILE_SAMPLES,
    derive_profile_decision,
    derive_stage_budget,
    derive_stage_decomposition,
    parse_args,
    summarize_call_counts,
)


def _combined_profile():
    return {
        "aggregate": {
            "sample_count": 5,
            "operations_exact": True,
            "outlier_removal": "none",
            "transition_evaluator_p95_ms": 10_000.0,
            "native_call_total_p95_ms": 10_010.0,
            "end_to_end_p95_ms": 10_012.0,
            "determinism_mismatch_count": 0,
        }
    }


def _stage_profile(*, driver_share=0.01):
    evaluator_share = (1.0 - driver_share - 0.01) / 5.0
    shares = {
        "driver_unattributed": driver_share,
        "transition": 0.01,
        "base_feature_component_extraction": evaluator_share,
        "placement_enumeration_trigger_qualification": evaluator_share,
        "virtual_resolve_gravity": evaluator_share,
        "remaining_structure_scan": evaluator_share,
        "candidate_ranking_sha256": evaluator_share,
    }
    return {
        "aggregate": {
            "profile_sample_count": 5,
            "operations_exact": True,
            "outlier_removal": "none",
            "cycles_p50": 1_000_000.0,
            "determinism_mismatch_count": 0,
            "profiled_result_mismatch_count": 0,
            "sampler_sample_count": 10_000,
            "stage_sample_totals": {
                name: int(share * 10_000) for name, share in shares.items()
            },
            "stage_sample_shares": shares,
            "stage_entries_per_sample": {name: 600_000 for name in shares},
            "cycle_counter_available": True,
            "sampler_available": True,
        }
    }


class TestNativeEvaluatorHotPathProfile(unittest.TestCase):
    def test_contract_locks_source_bound_measurement(self):
        args = parse_args(["run"])

        self.assertEqual(args.command, "run")
        self.assertEqual(EXPANDED_NODE_COUNT, 600_000)
        self.assertEqual(PROFILE_SAMPLES, 5)
        self.assertEqual(COMBINED_BUDGET_MS, 820.625)
        self.assertAlmostEqual(EVALUATOR_BUDGET_MS, 774.353480)
        self.assertEqual(MIN_ATTRIBUTED_SHARE, 0.95)

    def test_call_count_summary_uses_nearest_rank_and_exact_total(self):
        rows = [
            {
                "pattern_nodes": value,
                "resolution_nodes": value + 1,
                "rank_comparison_calls": value + 2,
                "rank_tie_calls": value + 3,
                "sha256_calls": value + 4,
            }
            for value in (1, 2, 3, 4, 5)
        ]
        aggregate = {
            "pattern_nodes": 100,
            "resolution_nodes": 200,
            "rank_comparison_calls": 300,
            "rank_tie_calls": 400,
            "sha256_calls": 500,
        }

        result = summarize_call_counts(rows, aggregate)

        self.assertEqual(result["distribution"]["pattern_nodes"]["p50_per_node"], 3)
        self.assertEqual(result["distribution"]["pattern_nodes"]["p95_per_node"], 5)
        self.assertEqual(
            result["distribution"]["sha256_calls"]["exact_600k_total"],
            500,
        )

    def test_stage_budget_sums_exactly_and_orders_candidates_by_contribution(self):
        decomposition = derive_stage_decomposition(
            _combined_profile(),
            _stage_profile(),
        )
        budget = derive_stage_budget(decomposition)

        self.assertTrue(decomposition["attribution"]["passes"])
        self.assertAlmostEqual(
            budget["stage_budget_sum_600k_ms"],
            EVALUATOR_BUDGET_MS,
            places=9,
        )
        self.assertEqual(set(budget["follow_up_order"][:2]), {"PUYO-220", "PUYO-223"})
        self.assertEqual(budget["follow_up_order"][-2:], ["PUYO-222", "PUYO-221"])

    def test_profile_decision_rejects_low_stage_attribution(self):
        combined = _combined_profile()
        stage = _stage_profile(driver_share=0.06)
        decomposition = derive_stage_decomposition(combined, stage)
        budget = derive_stage_budget(decomposition)
        semantic = {
            "fixture": {"mismatch_count": 0},
            "transition_oracle": {"mismatch_count": 0},
            "python_native_evaluator": {
                "mismatch_count": 0,
                "invalid_selected_count": 0,
            },
            "determinism": {"mismatch_count": 0},
        }
        source = {
            "normal_hot_path_heap_allocations": 0,
            "child_state_bytes": 80,
            "hot_result_bytes": 24,
        }
        ablation = {
            "diagnostic_only": True,
            "production_semantics_changed": False,
            "production_max_added_puyos": 3,
        }

        decision = derive_profile_decision(
            semantic=semantic,
            source=source,
            combined=combined,
            stage=stage,
            decomposition=decomposition,
            budget=budget,
            ablation=ablation,
            source_commit_is_ancestor=True,
            corpus_sha256=EXPECTED_CORPUS_SHA256,
            corpus_digest=EXPECTED_CORPUS_DIGEST,
            config_sha256=EXPECTED_CONFIG_SHA256,
        )

        self.assertFalse(decision["passed"])
        self.assertEqual(decision["decision"], "PROFILE_INVALID")
        self.assertIn(
            "stage_attribution_at_least_95_percent", decision["failed_checks"]
        )


if __name__ == "__main__":
    unittest.main()
