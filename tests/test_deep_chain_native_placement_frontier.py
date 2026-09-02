from __future__ import annotations

import copy
import unittest

from eval.deep_chain_native_evaluator_benchmark import _read_json
from eval.deep_chain_native_placement_frontier import (
    BASELINE_MEASUREMENT_PATH,
    BASELINE_SEMANTIC_PATH,
    BASELINE_SUMMARY_PATH,
    CACHE_PROFILE_COMPARISON_COUNT,
    CATALOG_ORBIT_COUNT,
    CATALOG_PATTERN_COUNT,
    COMBINED_BUDGET_MS,
    COMPACT_PROPERTY_COMPARISON_COUNT,
    COMPONENT_METADATA_PROPERTY_COUNT,
    FOLLOW_UP_TICKET,
    FRONTIER_PROPERTY_COMPARISON_COUNT,
    MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT,
    NEXT_UNIMPROVED_STAGE,
    PLACEMENT_SUBSTAGES,
    PROTECTION_PROPERTY_COMPARISON_COUNT,
    TARGET_STAGE,
    derive_summary,
)


class DeepChainNativePlacementFrontierTest(unittest.TestCase):
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
        substage_ms = target["current_projected_600k_ms"] / len(PLACEMENT_SUBSTAGES)
        for name in PLACEMENT_SUBSTAGES:
            stages[name] = {
                "current_projected_600k_ms": substage_ms,
                "current_ns_per_node": substage_ms * 1_000_000.0 / 600_000,
                "estimated_cycles_at_profile_p50": (
                    target["estimated_cycles_at_profile_p50"] / len(PLACEMENT_SUBSTAGES)
                ),
            }
        current["combined_profile"]["aggregate"]["transition_evaluator_p95_ms"] = (
            baseline["combined_profile"]["aggregate"]["transition_evaluator_p95_ms"]
            * 0.75
        )
        for name in (
            "single_component_frontiers",
            "multi_component_frontiers",
            "frontier_state_visits",
            "qualified_candidates",
            "resolution_group_comparisons",
            "resolution_groups",
            "precomputed_resolution_groups",
            "precomputed_candidate_hits",
            "resolution_cache_hits",
        ):
            current["call_counts"]["distribution"][name] = {
                "p50_per_node": 1,
                "p95_per_node": 2,
                "maximum_per_node": 3,
                "exact_600k_total": 600_000,
            }
        oracle = {
            "passed": True,
            "mismatch_count": 0,
            "frontier_property_comparison_count": (FRONTIER_PROPERTY_COMPARISON_COUNT),
            "catalog_pattern_count": CATALOG_PATTERN_COUNT,
            "catalog_orbit_count": CATALOG_ORBIT_COUNT,
            "compact_property_comparison_count": (COMPACT_PROPERTY_COMPARISON_COUNT),
            "protection_property_comparison_count": (
                PROTECTION_PROPERTY_COMPARISON_COUNT
            ),
            "component_metadata_property_count": COMPONENT_METADATA_PROPERTY_COUNT,
            "cache_profile_comparison_count": CACHE_PROFILE_COMPARISON_COUNT,
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

    def test_derive_summary_accepts_exact_placement_reduction(self):
        summary = self._derive(self._inputs())

        self.assertTrue(summary["decision"]["passed"])
        self.assertEqual(summary["decision"]["decision"], "PASS_GATE_MET")
        self.assertGreaterEqual(
            summary["comparison"]["stage_cycle_reduction_percent"],
            MINIMUM_STAGE_CYCLE_REDUCTION_PERCENT,
        )
        self.assertLessEqual(
            summary["comparison"]["current_combined_p95_600k_ms"],
            COMBINED_BUDGET_MS,
        )
        self.assertEqual(
            summary["bottleneck_ledger"]["current_largest_unimproved_stage"],
            NEXT_UNIMPROVED_STAGE,
        )
        self.assertIsNone(summary["bottleneck_ledger"]["follow_up_ticket"])

    def test_derive_summary_rejects_placement_cycle_regression(self):
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
            "minimum_30_percent_stage_cycle_reduction",
            summary["decision"]["failed_checks"],
        )

    def test_derive_summary_rejects_logical_counter_drift(self):
        inputs = self._inputs()
        current = inputs[3]
        current["call_counts"]["distribution"]["resolution_nodes"][
            "exact_600k_total"
        ] += 1

        summary = self._derive(inputs)

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "logical_budget_and_rank_counters_match",
            summary["decision"]["failed_checks"],
        )

    def test_derive_summary_requires_follow_up_when_gate_is_unmet(self):
        inputs = self._inputs()
        inputs[3]["combined_profile"]["aggregate"]["transition_evaluator_p95_ms"] = (
            COMBINED_BUDGET_MS + 10.0
        )
        summary = self._derive(inputs, follow_up_ticket="")

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "follow_up_recorded_when_gate_unmet",
            summary["decision"]["failed_checks"],
        )

    def test_derive_summary_accepts_final_gate_without_follow_up(self):
        inputs = self._inputs()
        current = inputs[3]
        current["combined_profile"]["aggregate"]["transition_evaluator_p95_ms"] = (
            COMBINED_BUDGET_MS
        )

        summary = self._derive(inputs, follow_up_ticket="")

        self.assertTrue(summary["decision"]["passed"])
        self.assertEqual(summary["decision"]["decision"], "PASS_GATE_MET")
        self.assertIsNone(summary["bottleneck_ledger"]["follow_up_ticket"])

    def test_derive_summary_rejects_placement_oracle_mismatch(self):
        inputs = self._inputs()
        inputs[5]["mismatch_count"] = 1

        summary = self._derive(inputs)

        self.assertFalse(summary["decision"]["passed"])
        self.assertIn(
            "placement_component_and_cache_oracle_zero_mismatches",
            summary["decision"]["failed_checks"],
        )


if __name__ == "__main__":
    unittest.main()
