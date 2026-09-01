from __future__ import annotations

import copy
import unittest

from eval.deep_chain_native_evaluator_benchmark import _read_json
from eval.deep_chain_native_quiescence_frontier import (
    BASELINE_MEASUREMENT_PATH,
    BASELINE_SEMANTIC_PATH,
    BASELINE_SUMMARY_PATH,
    EXPANDED_NODE_COUNT,
    LOGICAL_COUNTERS,
    PROPERTY_COMPARISON_COUNT,
    derive_summary,
)


class DeepChainNativeQuiescenceFrontierTest(unittest.TestCase):
    def _inputs(self):
        baseline = _read_json(BASELINE_SUMMARY_PATH)
        semantic = _read_json(BASELINE_SEMANTIC_PATH)
        measurement = _read_json(BASELINE_MEASUREMENT_PATH)
        current = copy.deepcopy(baseline)
        current_measurement = copy.deepcopy(measurement)
        stage_name = "placement_enumeration_trigger_qualification"
        target = baseline["stage_budget"]["stage_budget_ledger"][stage_name][
            "target_budget_600k_ms"
        ]
        current_stage = current["stage_decomposition"]["evaluator_stages"][
            stage_name
        ]
        current_stage["current_projected_600k_ms"] = target - 1.0
        current_stage["current_ns_per_node"] = (
            (target - 1.0) * 1_000_000.0 / EXPANDED_NODE_COUNT
        )
        current["call_counts"]["distribution"]["executed_pattern_probes"] = {
            "p50_per_node": 18,
            "p95_per_node": 25,
            "maximum_per_node": 30,
            "mean_per_node": 17.4,
            "exact_600k_total": 10_444_966,
        }
        current["decision"]["passed"] = True
        current["semantic"] = copy.deepcopy(semantic)
        current_measurement["config"]["production_max_added_puyos"] = 3
        oracle = {
            "passed": True,
            "comparison_count": PROPERTY_COMPARISON_COUNT,
            "mismatch_count": 0,
        }
        return baseline, semantic, measurement, current, current_measurement, oracle

    def test_derive_summary_accepts_preserved_semantics_and_stage_budget(self):
        (
            baseline,
            semantic,
            measurement,
            current,
            current_measurement,
            oracle,
        ) = self._inputs()

        summary = derive_summary(
            baseline_summary=baseline,
            baseline_semantic=semantic,
            baseline_measurement=measurement,
            current_summary=current,
            current_measurement=current_measurement,
            oracle=oracle,
        )

        self.assertTrue(summary["decision"]["passed"])
        self.assertEqual(summary["decision"]["failed_checks"], [])
        self.assertTrue(summary["logical_counters"]["matches"])
        self.assertEqual(
            set(summary["logical_counters"]["after"]),
            set(LOGICAL_COUNTERS),
        )

    def test_derive_summary_rejects_stage_budget_regression(self):
        (
            baseline,
            semantic,
            measurement,
            current,
            current_measurement,
            oracle,
        ) = self._inputs()
        stage_name = "placement_enumeration_trigger_qualification"
        target = baseline["stage_budget"]["stage_budget_ledger"][stage_name][
            "target_budget_600k_ms"
        ]
        current["stage_decomposition"]["evaluator_stages"][stage_name][
            "current_projected_600k_ms"
        ] = target + 0.001

        summary = derive_summary(
            baseline_summary=baseline,
            baseline_semantic=semantic,
            baseline_measurement=measurement,
            current_summary=current,
            current_measurement=current_measurement,
            oracle=oracle,
        )

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "placement_stage_budget_met",
            summary["decision"]["failed_checks"],
        )


if __name__ == "__main__":
    unittest.main()
