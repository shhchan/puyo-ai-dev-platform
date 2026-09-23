from __future__ import annotations

import copy
import unittest

from eval.deep_chain_native_evaluator_benchmark import _read_json
from eval.deep_chain_native_next_bottleneck import (
    BASELINE_MEASUREMENT_PATH,
    BASELINE_SEMANTIC_PATH,
    BASELINE_SUMMARY_PATH,
    COMBINED_BUDGET_MS,
    EXPANDED_NODE_COUNT,
    FOLLOW_UP_TICKET,
    MINIMUM_COMBINED_REDUCTION_PERCENT,
    MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT,
    NEXT_UNIMPROVED_STAGE,
    PROPERTY_COMPARISON_COUNT,
    TARGET_STAGE,
    derive_summary,
)


class DeepChainNativeNextBottleneckTest(unittest.TestCase):
    def _inputs(self):
        baseline = _read_json(BASELINE_SUMMARY_PATH)
        semantic = _read_json(BASELINE_SEMANTIC_PATH)
        measurement = _read_json(BASELINE_MEASUREMENT_PATH)
        current = copy.deepcopy(baseline)
        current_measurement = copy.deepcopy(measurement)
        current["semantic"] = copy.deepcopy(semantic)
        current["decision"]["passed"] = True
        current_measurement["config"]["production_max_added_puyos"] = 3

        target = current["stage_decomposition"]["evaluator_stages"][TARGET_STAGE]
        target["estimated_cycles_at_profile_p50"] *= 0.5
        target["current_projected_600k_ms"] *= 0.5
        target["current_ns_per_node"] = (
            target["current_projected_600k_ms"]
            * 1_000_000.0
            / EXPANDED_NODE_COUNT
        )
        baseline_combined = baseline["combined_profile"]["aggregate"][
            "transition_evaluator_p95_ms"
        ]
        current["combined_profile"]["aggregate"][
            "transition_evaluator_p95_ms"
        ] = baseline_combined * 0.75
        oracle = {
            "passed": True,
            "comparison_count": PROPERTY_COMPARISON_COUNT,
            "mismatch_count": 0,
        }
        return (
            baseline,
            semantic,
            measurement,
            current,
            current_measurement,
            oracle,
        )

    def _derive(self, inputs, *, follow_up_ticket=FOLLOW_UP_TICKET):
        (
            baseline,
            semantic,
            measurement,
            current,
            current_measurement,
            oracle,
        ) = inputs
        return derive_summary(
            baseline_summary=baseline,
            baseline_semantic=semantic,
            baseline_measurement=measurement,
            current_summary=current,
            current_measurement=current_measurement,
            oracle=oracle,
            follow_up_ticket=follow_up_ticket,
        )

    def test_derive_summary_accepts_reduced_largest_unimproved_stage(self):
        summary = self._derive(self._inputs())

        self.assertTrue(summary["decision"]["passed"])
        self.assertEqual(summary["decision"]["decision"], "PASS_FOLLOW_UP_REQUIRED")
        self.assertGreaterEqual(
            summary["comparison"]["stage_cycle_reduction_percent"],
            MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT,
        )
        self.assertGreaterEqual(
            summary["comparison"]["combined_reduction_percent"],
            MINIMUM_COMBINED_REDUCTION_PERCENT,
        )
        self.assertEqual(
            summary["bottleneck_ledger"]["baseline_largest_unimproved_stage"],
            TARGET_STAGE,
        )
        self.assertEqual(
            summary["bottleneck_ledger"]["current_largest_unimproved_stage"],
            NEXT_UNIMPROVED_STAGE,
        )
        self.assertEqual(
            summary["bottleneck_ledger"]["follow_up_ticket"],
            FOLLOW_UP_TICKET,
        )

    def test_derive_summary_rejects_target_stage_cycle_regression(self):
        inputs = self._inputs()
        baseline = inputs[0]
        current = inputs[3]
        current["stage_decomposition"]["evaluator_stages"][TARGET_STAGE][
            "estimated_cycles_at_profile_p50"
        ] = baseline["stage_decomposition"]["evaluator_stages"][TARGET_STAGE][
            "estimated_cycles_at_profile_p50"
        ]

        summary = self._derive(inputs)

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "minimum_30_percent_target_stage_cycle_reduction",
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
        current = inputs[3]
        current["combined_profile"]["aggregate"][
            "transition_evaluator_p95_ms"
        ] = COMBINED_BUDGET_MS

        summary = self._derive(inputs, follow_up_ticket="")

        self.assertTrue(summary["decision"]["passed"])
        self.assertEqual(summary["decision"]["decision"], "PASS_GATE_MET")
        self.assertIsNone(summary["bottleneck_ledger"]["follow_up_ticket"])


if __name__ == "__main__":
    unittest.main()
