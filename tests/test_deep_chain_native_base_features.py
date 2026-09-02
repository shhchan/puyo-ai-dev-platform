from __future__ import annotations

import copy
import unittest

from eval.deep_chain_native_base_features import (
    BASE_SUBSTAGE_NAMES,
    BASELINE_MEASUREMENT_PATH,
    BASELINE_SEMANTIC_PATH,
    BASELINE_SUMMARY_PATH,
    COMBINED_BUDGET_MS,
    COMPONENT_METADATA_PROPERTY_COUNT,
    FOLLOW_UP_TICKET,
    FRONTIER_PROPERTY_COMPARISON_COUNT,
    MINIMUM_COMBINED_REDUCTION_PERCENT,
    MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT,
    NEXT_UNIMPROVED_STAGE,
    TARGET_STAGE,
    derive_summary,
)
from eval.deep_chain_native_evaluator_benchmark import _read_json


class DeepChainNativeBaseFeaturesTest(unittest.TestCase):
    def _inputs(self):
        baseline = _read_json(BASELINE_SUMMARY_PATH)
        semantic = _read_json(BASELINE_SEMANTIC_PATH)
        measurement = _read_json(BASELINE_MEASUREMENT_PATH)
        current = copy.deepcopy(baseline)
        current_measurement = copy.deepcopy(measurement)
        current["semantic"] = copy.deepcopy(semantic)
        current["decision"]["passed"] = True
        current_measurement["config"]["production_max_added_puyos"] = 3

        stages = current["stage_decomposition"]["evaluator_stages"]
        target = stages[TARGET_STAGE]
        target["estimated_cycles_at_profile_p50"] *= 0.5
        target["current_projected_600k_ms"] *= 0.5
        target["current_ns_per_node"] = (
            target["current_projected_600k_ms"] * 1_000_000.0 / 600_000
        )
        substage_ms = target["current_projected_600k_ms"] / len(
            BASE_SUBSTAGE_NAMES
        )
        for name in BASE_SUBSTAGE_NAMES:
            stages[name] = {
                "current_projected_600k_ms": substage_ms,
                "current_ns_per_node": substage_ms * 1_000_000.0 / 600_000,
                "estimated_cycles_at_profile_p50": (
                    target["estimated_cycles_at_profile_p50"]
                    / len(BASE_SUBSTAGE_NAMES)
                ),
            }
        current["combined_profile"]["aggregate"][
            "transition_evaluator_p95_ms"
        ] = (
            baseline["combined_profile"]["aggregate"][
                "transition_evaluator_p95_ms"
            ]
            * 0.75
        )
        oracle = {
            "passed": True,
            "mismatch_count": 0,
            "component_metadata_property_count": COMPONENT_METADATA_PROPERTY_COUNT,
            "frontier_property_comparison_count": FRONTIER_PROPERTY_COMPARISON_COUNT,
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
        baseline, semantic, measurement, current, current_measurement, oracle = (
            inputs
        )
        return derive_summary(
            baseline_summary=baseline,
            baseline_semantic=semantic,
            baseline_measurement=measurement,
            current_summary=current,
            current_measurement=current_measurement,
            oracle=oracle,
            follow_up_ticket=follow_up_ticket,
        )

    def test_derive_summary_accepts_exact_base_reduction(self):
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
            summary["bottleneck_ledger"]["current_largest_unimproved_stage"],
            NEXT_UNIMPROVED_STAGE,
        )
        self.assertEqual(
            summary["bottleneck_ledger"]["follow_up_ticket"], FOLLOW_UP_TICKET
        )

    def test_derive_summary_rejects_base_cycle_regression(self):
        inputs = self._inputs()
        baseline, current = inputs[0], inputs[3]
        current["stage_decomposition"]["evaluator_stages"][TARGET_STAGE][
            "estimated_cycles_at_profile_p50"
        ] = baseline["stage_decomposition"]["evaluator_stages"][TARGET_STAGE][
            "estimated_cycles_at_profile_p50"
        ]

        summary = self._derive(inputs)

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "minimum_30_percent_stage_cycle_reduction",
            summary["decision"]["failed_checks"],
        )

    def test_derive_summary_rejects_logical_counter_drift(self):
        inputs = self._inputs()
        inputs[3]["call_counts"]["distribution"]["resolution_nodes"][
            "exact_600k_total"
        ] += 1

        summary = self._derive(inputs)

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "logical_budget_and_rank_counters_match",
            summary["decision"]["failed_checks"],
        )

    def test_derive_summary_requires_follow_up_when_gate_is_unmet(self):
        summary = self._derive(self._inputs(), follow_up_ticket="")

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "follow_up_recorded_when_gate_unmet",
            summary["decision"]["failed_checks"],
        )

    def test_derive_summary_accepts_final_gate_without_follow_up(self):
        inputs = self._inputs()
        inputs[3]["combined_profile"]["aggregate"][
            "transition_evaluator_p95_ms"
        ] = COMBINED_BUDGET_MS

        summary = self._derive(inputs, follow_up_ticket="")

        self.assertTrue(summary["decision"]["passed"])
        self.assertEqual(summary["decision"]["decision"], "PASS_GATE_MET")
        self.assertIsNone(summary["bottleneck_ledger"]["follow_up_ticket"])

    def test_derive_summary_rejects_component_oracle_mismatch(self):
        inputs = self._inputs()
        inputs[5]["mismatch_count"] = 1

        summary = self._derive(inputs)

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "base_component_oracle_zero_mismatches",
            summary["decision"]["failed_checks"],
        )


if __name__ == "__main__":
    unittest.main()
