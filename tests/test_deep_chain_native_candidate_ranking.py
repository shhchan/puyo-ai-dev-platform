from __future__ import annotations

import copy
import unittest

from eval.deep_chain_native_candidate_ranking import (
    COMBINED_BUDGET_MS,
    EXPANDED_NODE_COUNT,
    FOLLOW_UP_TICKET,
    HISTORICAL_BASELINE_SUMMARY_PATH,
    MINIMUM_COMBINED_REDUCTION_PERCENT,
    MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT,
    NEXT_UNIMPROVED_STAGE,
    ORDERING_PROPERTY_COMPARISON_COUNT,
    PAIRED_BASELINE_MEASUREMENT_PATH,
    PAIRED_BASELINE_SEMANTIC_PATH,
    PAIRED_BASELINE_SUMMARY_PATH,
    PROTECTION_PROPERTY_COMPARISON_COUNT,
    TARGET_STAGE,
    derive_summary,
)
from eval.deep_chain_native_evaluator_benchmark import _read_json


class DeepChainNativeCandidateRankingTest(unittest.TestCase):
    def _inputs(self):
        historical = _read_json(HISTORICAL_BASELINE_SUMMARY_PATH)
        paired = _read_json(PAIRED_BASELINE_SUMMARY_PATH)
        semantic = _read_json(PAIRED_BASELINE_SEMANTIC_PATH)
        measurement = _read_json(PAIRED_BASELINE_MEASUREMENT_PATH)
        current = copy.deepcopy(paired)
        current_measurement = copy.deepcopy(measurement)
        current["semantic"] = copy.deepcopy(semantic)
        current["decision"]["passed"] = True
        current_measurement["config"]["production_max_added_puyos"] = 3

        target = current["stage_decomposition"]["evaluator_stages"][TARGET_STAGE]
        target["estimated_cycles_at_profile_p50"] *= 0.5
        target["current_projected_600k_ms"] *= 0.5
        target["current_ns_per_node"] = (
            target["current_projected_600k_ms"] * 1_000_000.0 / EXPANDED_NODE_COUNT
        )
        paired_combined = paired["combined_profile"]["aggregate"][
            "transition_evaluator_p95_ms"
        ]
        current["combined_profile"]["aggregate"]["transition_evaluator_p95_ms"] = (
            paired_combined * 0.75
        )
        oracle = {
            "passed": True,
            "mismatch_count": 0,
            "canonical_vector_count": 1,
            "ordering_property_comparison_count": (ORDERING_PROPERTY_COMPARISON_COUNT),
            "protection_property_comparison_count": (
                PROTECTION_PROPERTY_COMPARISON_COUNT
            ),
        }
        return (
            historical,
            paired,
            semantic,
            measurement,
            current,
            current_measurement,
            oracle,
        )

    def _derive(self, inputs, *, follow_up_ticket=FOLLOW_UP_TICKET):
        (
            historical,
            paired,
            semantic,
            measurement,
            current,
            current_measurement,
            oracle,
        ) = inputs
        return derive_summary(
            historical_summary=historical,
            paired_summary=paired,
            paired_semantic=semantic,
            paired_measurement=measurement,
            current_summary=current,
            current_measurement=current_measurement,
            oracle=oracle,
            follow_up_ticket=follow_up_ticket,
        )

    def test_derive_summary_accepts_exact_candidate_ranking_reduction(self):
        summary = self._derive(self._inputs())

        self.assertTrue(summary["decision"]["passed"])
        self.assertEqual(summary["decision"]["decision"], "PASS_FOLLOW_UP_REQUIRED")
        self.assertGreaterEqual(
            summary["comparison"]["historical_stage_cycle_reduction_percent"],
            MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT,
        )
        self.assertGreaterEqual(
            summary["comparison"]["paired_combined_reduction_percent"],
            MINIMUM_COMBINED_REDUCTION_PERCENT,
        )
        self.assertEqual(
            summary["bottleneck_ledger"]["current_largest_unimproved_stage"],
            NEXT_UNIMPROVED_STAGE,
        )
        self.assertEqual(
            summary["bottleneck_ledger"]["follow_up_ticket"],
            FOLLOW_UP_TICKET,
        )

    def test_derive_summary_rejects_candidate_ranking_cycle_regression(self):
        inputs = self._inputs()
        paired = inputs[1]
        current = inputs[4]
        current["stage_decomposition"]["evaluator_stages"][TARGET_STAGE][
            "estimated_cycles_at_profile_p50"
        ] = paired["stage_decomposition"]["evaluator_stages"][TARGET_STAGE][
            "estimated_cycles_at_profile_p50"
        ]

        summary = self._derive(inputs)

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "minimum_30_percent_paired_stage_cycle_reduction",
            summary["decision"]["failed_checks"],
        )

    def test_derive_summary_requires_follow_up_when_gate_is_unmet(self):
        summary = self._derive(self._inputs(), follow_up_ticket="")

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "follow_up_recorded_when_gate_unmet",
            summary["decision"]["failed_checks"],
        )

    def test_derive_summary_accepts_gate_without_follow_up(self):
        inputs = self._inputs()
        current = inputs[4]
        current["combined_profile"]["aggregate"]["transition_evaluator_p95_ms"] = (
            COMBINED_BUDGET_MS
        )

        summary = self._derive(inputs, follow_up_ticket="")

        self.assertTrue(summary["decision"]["passed"])
        self.assertEqual(summary["decision"]["decision"], "PASS_GATE_MET")
        self.assertIsNone(summary["bottleneck_ledger"]["follow_up_ticket"])


if __name__ == "__main__":
    unittest.main()
