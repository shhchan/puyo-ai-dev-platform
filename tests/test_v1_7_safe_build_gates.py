import unittest

from eval.v1_7_safe_build_gates import (
    CAPABILITY_GATE_INPUT_SCHEMA_VERSION,
    GATE_SUMMARY_SCHEMA_VERSION,
    PROMOTION_GATE_INPUT_SCHEMA_VERSION,
    CapabilityConfiguration,
    CapabilityThresholds,
    PromotionThresholds,
    assess_capability_gate,
    assess_promotion_gate,
    evaluate_capability_seed,
    select_capability_configuration,
    summarize_capability_configuration_selection,
    validate_gate_summary,
)
from eval.v1_7_search_diagnostics_benchmark import SearchBudget


def capability_metrics(**overrides):
    value = {
        "games": 30,
        "complete_games": 30,
        "decisions": 1200,
        "mean_max_chain": 10.25,
        "max_chain_max": 13,
        "best_reachable_chain_mean": 11.0,
        "best_reachable_chain_max": 14,
        "reference_best_chain_mean": 11.5,
        "reference_action_coverage_count": 1100,
        "reference_action_coverage_rate": 1100 / 1200,
        "reference_path_coverage_count": 900,
        "reference_path_coverage_rate": 0.75,
        "avoidable_candidate_gap_count": 0,
        "forced_game_over_gap_count": 0,
        "premature_classification_counts": {
            "avoidable": 0,
            "candidate_limited": 2,
            "none": 1198,
        },
        "game_over_before_limit": 0,
        "latency": {
            "mode": "test",
            "proposal_p50_ms": 30.0,
            "proposal_p95_ms": 55.0,
            "reference_p50_ms": 60.0,
            "reference_p95_ms": 90.0,
        },
    }
    value.update(overrides)
    return value


def promotion_input(**overrides):
    value = {
        "schema_version": PROMOTION_GATE_INPUT_SCHEMA_VERSION,
        "checkpoint": {
            "role": "post_training_candidate",
            "path": "candidate.pt",
            "sha256": "a" * 64,
        },
        "training_seeds": [101, 102, 103, 104, 105],
        "selected_policy_safe_build": {
            "games": 30,
            "moves_per_game": 40,
            "mean_max_chain": 10.0,
            "premature_fire_count": 0,
            "game_over_before_limit": 0,
        },
        "threat_scenarios": {"summary": {"scenarios": 6, "passed": 6, "failed": 0}},
        "lineage": {
            "parent_node_id": "model:v1.7.1",
            "training_run_id": "mixed-seed-suite",
            "git_commit": "test-commit",
        },
        "gui_replay": {"passed": True},
        "thresholds": PromotionThresholds().to_dict(),
    }
    value.update(overrides)
    return value


def gate_summary(capability_result, promotion_result):
    return {
        "schema_version": GATE_SUMMARY_SCHEMA_VERSION,
        "training_capability_gate_passed": capability_result[
            "training_capability_gate_passed"
        ],
        "promotion_gate_passed": promotion_result["promotion_gate_passed"],
        "puyo_130_long_run": capability_result["puyo_130_long_run"],
        "capability": {
            "input": {"schema_version": CAPABILITY_GATE_INPUT_SCHEMA_VERSION},
            "result": capability_result,
        },
        "promotion": {
            "input": {"schema_version": PROMOTION_GATE_INPUT_SCHEMA_VERSION},
            "result": promotion_result,
        },
    }


class TestV17SafeBuildGates(unittest.TestCase):
    def test_capability_pass_and_failure_fixtures_are_explicit(self):
        passing = assess_capability_gate(
            capability_metrics(),
            CapabilityThresholds(),
            determinism_passed=True,
        )
        failing = assess_capability_gate(
            capability_metrics(
                mean_max_chain=9.9,
                avoidable_candidate_gap_count=1,
                forced_game_over_gap_count=1,
                latency={
                    "mode": "test",
                    "proposal_p50_ms": 40.0,
                    "proposal_p95_ms": 61.0,
                    "reference_p50_ms": 70.0,
                    "reference_p95_ms": 100.0,
                },
            ),
            CapabilityThresholds(),
            determinism_passed=True,
        )

        self.assertTrue(passing["training_capability_gate_passed"])
        self.assertEqual(passing["puyo_130_long_run"]["status"], "UNBLOCKED")
        self.assertFalse(failing["training_capability_gate_passed"])
        self.assertEqual(failing["puyo_130_long_run"]["status"], "BLOCKED")
        self.assertFalse(failing["checks"]["mean_max_chain"]["passed"])
        self.assertFalse(failing["checks"]["avoidable_candidate_gap"]["passed"])
        self.assertFalse(failing["checks"]["registry_latency_budget"]["passed"])

    def test_promotion_requires_selected_policy_10_0_0_and_scenarios_6_of_6(self):
        passing = assess_promotion_gate(promotion_input())
        failing_payload = promotion_input()
        failing_payload["selected_policy_safe_build"] = {
            **failing_payload["selected_policy_safe_build"],
            "premature_fire_count": 1,
        }
        failing_payload["threat_scenarios"] = {
            "summary": {"scenarios": 6, "passed": 5, "failed": 1}
        }
        failing = assess_promotion_gate(failing_payload)

        self.assertTrue(passing["promotion_gate_passed"])
        self.assertEqual(passing["registry_registration"], "ELIGIBLE")
        self.assertFalse(failing["promotion_gate_passed"])
        self.assertEqual(failing["status"], "BLOCKED")
        self.assertFalse(
            failing["checks"]["selected_policy_safe_premature_fire"]["passed"]
        )
        self.assertFalse(failing["checks"]["threat_scenarios"]["passed"])

    def test_pretraining_checkpoint_cannot_satisfy_promotion(self):
        payload = promotion_input()
        payload["checkpoint"] = {
            **payload["checkpoint"],
            "role": "pretraining_reference",
        }

        result = assess_promotion_gate(payload)

        self.assertFalse(result["promotion_gate_passed"])
        self.assertEqual(result["status"], "PENDING_POST_TRAINING")

    def test_fastest_passing_capability_configuration_is_selected(self):
        def evaluated(config_id, latency, passed):
            depth = 3 if config_id == "fast" else 4
            return {
                "configuration": {
                    "config_id": config_id,
                    "depth": depth,
                    "width": 24,
                    "probe_width": 8,
                    "candidate_limit": 8,
                },
                "result": {
                    "training_capability_gate_passed": passed,
                    "metrics": {"latency": {"proposal_p95_ms": latency}},
                },
            }

        selected = select_capability_configuration(
            [
                evaluated("failed", 10.0, False),
                evaluated("slow", 50.0, True),
                evaluated("fast", 40.0, True),
            ]
        )

        self.assertEqual(selected["configuration"]["config_id"], "fast")

        selection = summarize_capability_configuration_selection(
            [
                evaluated("failed", 10.0, False),
                evaluated("slow", 50.0, True),
                evaluated("fast", 40.0, True),
            ]
        )
        self.assertEqual(selection["status"], "SELECTED")
        self.assertEqual(selection["selected_config_id"], "fast")
        self.assertEqual(
            selection["rule"],
            "minimum_proposal_p95_ms_among_passing_configurations",
        )

        blocked = summarize_capability_configuration_selection(
            [evaluated("failed", 10.0, False)]
        )
        self.assertEqual(blocked["status"], "NO_PASSING_CONFIGURATION")
        self.assertIsNone(blocked["selected_config_id"])

    def test_summary_schema_keeps_gate_responsibilities_separate(self):
        capability = assess_capability_gate(
            capability_metrics(),
            CapabilityThresholds(),
            determinism_passed=True,
        )
        promotion = assess_promotion_gate(promotion_input())
        summary = gate_summary(capability, promotion)

        self.assertEqual(validate_gate_summary(summary), [])
        summary["training_gate_passed"] = True
        self.assertIn(
            "legacy training_gate_passed must not appear in two-stage summary",
            validate_gate_summary(summary),
        )

    def test_smoke_evaluator_emits_k_best_candidate_schema(self):
        result = evaluate_capability_seed(
            123,
            max_steps=1,
            configuration=CapabilityConfiguration("smoke", 1, 4, 2, 2),
            reference_budget=SearchBudget(2, 6, 3),
        )
        decision = result["decisions"][0]

        self.assertEqual(result["summary"]["decisions"], 1)
        self.assertEqual(
            decision["schema_version"],
            "puyo.v1_7_training_capability_decision.v1",
        )
        self.assertEqual(
            decision["proposal"]["schema_version"],
            "puyo.worker_proposal_batch.v1",
        )
        self.assertEqual(len(decision["proposal"]["candidate_mask"]), 2)
        self.assertIn(
            decision["selection"]["premature_classification"],
            {"avoidable", "candidate_limited", "none"},
        )


if __name__ == "__main__":
    unittest.main()
