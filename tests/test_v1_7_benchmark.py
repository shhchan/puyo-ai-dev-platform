import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.v1_7_benchmark import (
    ARENA_BASELINES,
    SAFE_POLICIES,
    aggregate_safe_suite,
    assess_outcome,
    build_completion,
    load_response_scenarios,
    percentile,
    verify_benchmark,
)
from train.artifacts import file_sha256


class TestV17Benchmark(unittest.TestCase):
    def test_percentile_and_safe_build_gate_are_deterministic(self):
        records = [
            {
                "seed": 123 + index,
                "steps": 40,
                "max_chain": chain,
                "premature_fire_count": 0,
                "trigger_opportunities": 2,
                "trigger_loss_count": index % 2,
                "game_over_before_limit": False,
                "_decision_latencies_ms": [1.0 + index, 2.0 + index],
            }
            for index, chain in enumerate((9, 10, 11, 12))
        ]

        summary = aggregate_safe_suite("candidate", records, max_steps=40)

        self.assertEqual(percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(summary["mean_max_chain"], 10.5)
        self.assertEqual(summary["max_chain_p90"], 11.7)
        self.assertTrue(summary["passed"])
        self.assertEqual(summary["trigger_loss_count"], 2)

    def test_safe_build_gate_rejects_premature_fire_and_early_game_over(self):
        summary = aggregate_safe_suite(
            "candidate",
            [
                {
                    "steps": 12,
                    "max_chain": 12,
                    "premature_fire_count": 1,
                    "trigger_opportunities": 1,
                    "trigger_loss_count": 1,
                    "game_over_before_limit": True,
                    "_decision_latencies_ms": [3.0],
                }
            ],
            max_steps=40,
        )

        self.assertFalse(summary["passed"])
        self.assertFalse(summary["gates"]["premature_fire"]["passed"])
        self.assertFalse(summary["gates"]["game_over"]["passed"])

    def test_outcome_checks_use_actual_attack_and_followup_results(self):
        full_cancel, failures = assess_outcome(
            "full_cancel",
            {"incoming": 5, "canceled": 5, "received": 0},
        )
        resume, resume_failures = assess_outcome(
            "resume_build",
            {"followup_tactic": "prepare_response"},
        )

        self.assertTrue(full_cancel)
        self.assertEqual(failures, [])
        self.assertFalse(resume)
        self.assertIn("resume", resume_failures[0])
        self.assertEqual(len(load_response_scenarios()), 6)

    def test_evaluation_completion_is_independent_from_training_gate(self):
        safe = [
            {"label": label, "games": 30, "passed": label != "v1_7_1"}
            for label in SAFE_POLICIES
        ]
        completion = build_completion(
            checkpoint={"validation_errors": []},
            champion={"hash_matches_registry": True},
            analyzer_report={"summary": {"scenarios": 24, "failed": 0}},
            safe_summaries=safe,
            outcome_report={"summary": {"scenarios": 6, "failed": 2}},
            lifecycle_report={"passed": True},
            arena_summaries=[{} for _ in ARENA_BASELINES],
            gui_qa={
                "result": {"execution_completed": True},
                "quality_gate": {"passed": True},
            },
            gui_verification={"verified": True},
        )

        self.assertTrue(completion["evaluation_completed"])
        self.assertFalse(completion["training_gate_passed"])

    def test_verify_detects_manifest_artifact_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "evidence.json"
            artifact.write_text("{}\n", encoding="utf-8")
            (root / "benchmark_summary.json").write_text(
                json.dumps(
                    {
                        "evaluation_completed": True,
                        "training_gate_passed": False,
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "schema_version": "puyo.v1_7_benchmark.v1",
                "checkpoint": {"sha256": "a" * 64},
                "existing_checkpoint": {"sha256": "b" * 64},
                "artifacts": [{"path": artifact.name, "sha256": file_sha256(artifact)}],
            }
            (root / "benchmark_manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                artifact_dir=str(root),
                checkpoint="checkpoint.pt",
                model_registry="model_registry.json",
                require_training_gate=False,
            )
            with (
                patch(
                    "eval.v1_7_benchmark.load_checkpoint_evidence",
                    return_value=({"sha256": "a" * 64}, {}),
                ),
                patch(
                    "eval.v1_7_benchmark.load_champion_evidence",
                    return_value={"sha256": "b" * 64},
                ),
            ):
                self.assertTrue(verify_benchmark(args)["passed"])
                artifact.write_text('{"tampered": true}\n', encoding="utf-8")
                result = verify_benchmark(args)

        self.assertFalse(result["passed"])
        self.assertIn("artifact hash mismatch", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
