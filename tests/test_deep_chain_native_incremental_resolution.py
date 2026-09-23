from __future__ import annotations

import copy
import unittest

from eval.deep_chain_native_evaluator_benchmark import _read_json
from eval.deep_chain_native_incremental_resolution import (
    BASELINE_MEASUREMENT_PATH,
    BASELINE_SEMANTIC_PATH,
    BASELINE_SUMMARY_PATH,
    BUDGET_SUMMARY_PATH,
    EXPANDED_NODE_COUNT,
    LOGICAL_COUNTERS,
    PROPERTY_COMPARISON_COUNT,
    STAGE_NAMES,
    derive_summary,
    parse_args,
    verify_incremental_resolution_artifacts,
)


class DeepChainNativeIncrementalResolutionTest(unittest.TestCase):
    def test_historical_verify_preserves_hash_checks_after_successor_changes(self):
        args = parse_args(["verify", "--historical"])

        self.assertTrue(args.historical)
        self.assertFalse(args.require_exact_wheel)
        self.assertFalse(args.skip_oracle_rerun)
        self.assertEqual(
            verify_incremental_resolution_artifacts(
                rerun_oracle=False,
                historical=True,
            ),
            [],
        )

    def _inputs(self):
        baseline = _read_json(BASELINE_SUMMARY_PATH)
        semantic = _read_json(BASELINE_SEMANTIC_PATH)
        measurement = _read_json(BASELINE_MEASUREMENT_PATH)
        budget = _read_json(BUDGET_SUMMARY_PATH)
        current = copy.deepcopy(baseline)
        current_measurement = copy.deepcopy(measurement)
        ledger = budget["stage_budget"]["stage_budget_ledger"]
        for stage_name in STAGE_NAMES:
            measured_ms = ledger[stage_name]["target_budget_600k_ms"] - 1.0
            current_stage = current["stage_decomposition"]["evaluator_stages"][
                stage_name
            ]
            current_stage["current_projected_600k_ms"] = measured_ms
            current_stage["current_ns_per_node"] = (
                measured_ms * 1_000_000.0 / EXPANDED_NODE_COUNT
            )
        current["decision"]["passed"] = True
        current["semantic"] = copy.deepcopy(semantic)
        current_measurement["config"]["production_max_added_puyos"] = 3
        oracle = {
            "passed": True,
            "comparison_count": PROPERTY_COMPARISON_COUNT,
            "mismatch_count": 0,
        }
        return (
            baseline,
            semantic,
            measurement,
            budget,
            current,
            current_measurement,
            oracle,
        )

    def _derive(self, inputs):
        (
            baseline,
            semantic,
            measurement,
            budget,
            current,
            current_measurement,
            oracle,
        ) = inputs
        return derive_summary(
            baseline_summary=baseline,
            baseline_semantic=semantic,
            baseline_measurement=measurement,
            budget_summary=budget,
            current_summary=current,
            current_measurement=current_measurement,
            oracle=oracle,
        )

    def test_derive_summary_accepts_exact_semantics_and_fixed_stage_budget(self):
        summary = self._derive(self._inputs())

        self.assertTrue(summary["decision"]["passed"])
        self.assertEqual(summary["decision"]["failed_checks"], [])
        self.assertGreaterEqual(summary["comparison"]["reduction_percent"], 70.0)
        self.assertTrue(summary["logical_counters"]["matches"])
        self.assertEqual(set(summary["logical_counters"]["after"]), set(LOGICAL_COUNTERS))

    def test_derive_summary_rejects_fixed_stage_budget_regression(self):
        inputs = self._inputs()
        budget = inputs[3]
        current = inputs[4]
        target = sum(
            budget["stage_budget"]["stage_budget_ledger"][stage_name][
                "target_budget_600k_ms"
            ]
            for stage_name in STAGE_NAMES
        )
        current["stage_decomposition"]["evaluator_stages"][STAGE_NAMES[0]][
            "current_projected_600k_ms"
        ] = target

        summary = self._derive(inputs)

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn("puyo_219_stage_budget_met", summary["decision"]["failed_checks"])

    def test_derive_summary_rejects_property_oracle_mismatch(self):
        inputs = self._inputs()
        inputs[-1]["mismatch_count"] = 1

        summary = self._derive(inputs)

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "property_oracle_256_zero_mismatches",
            summary["decision"]["failed_checks"],
        )


if __name__ == "__main__":
    unittest.main()
