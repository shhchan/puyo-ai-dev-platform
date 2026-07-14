import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.v1_7_build_main_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    FALLBACK_BUDGET,
    BuildMainConfiguration,
    ForcedBuildMainPolicy,
    build_selection,
    configuration_grid,
    parse_args,
    select_configuration,
    verify_benchmark,
)
from puyo_env.actions import legal_action_mask
from puyo_env.obs import encode_observation
from src.core.headless import HeadlessPuyoSimulator
from train.artifacts import file_sha256


class TestV17BuildMainBenchmark(unittest.TestCase):
    def test_default_grid_contains_all_eighteen_configurations(self):
        configurations = configuration_grid()

        self.assertEqual(len(configurations), 18)
        self.assertEqual(configurations[0].config_id, "d6-w24-p8")
        self.assertEqual(configurations[-1].config_id, "d10-w48-p16")
        args = parse_args(["run"])
        self.assertEqual((args.games, args.max_steps, args.workers), (30, 40, 8))

    def test_selection_uses_p95_then_budget_tie_breaks(self):
        summaries = [
            {
                "config_id": "slow",
                "passed": True,
                "decision_p95_ms": 20.0,
                "depth": 6,
                "width": 24,
                "probe_width": 8,
            },
            {
                "config_id": "wide",
                "passed": True,
                "decision_p95_ms": 10.0,
                "depth": 8,
                "width": 48,
                "probe_width": 16,
            },
            {
                "config_id": "selected",
                "passed": True,
                "decision_p95_ms": 10.0,
                "depth": 8,
                "width": 32,
                "probe_width": 16,
            },
        ]

        selected = select_configuration(summaries)
        selection = build_selection(summaries)

        self.assertEqual(selected["config_id"], "selected")
        self.assertEqual(selection["selected_config_id"], "selected")
        self.assertEqual(
            selection["adopted_budget"],
            {"depth": 8, "width": 32, "probe_width": 16},
        )

    def test_all_failed_configurations_keep_existing_budget(self):
        selection = build_selection(
            [
                {
                    "config_id": "failed",
                    "passed": False,
                    "decision_p95_ms": 1.0,
                    "depth": 10,
                    "width": 48,
                    "probe_width": 16,
                }
            ]
        )

        self.assertIsNone(selection["selected_configuration"])
        self.assertEqual(selection["adopted_budget"], FALLBACK_BUDGET)
        self.assertTrue(selection["all_configurations_failed"])
        self.assertFalse(selection["puyo_130_may_start"])

    def test_forced_evaluator_applies_build_main_v1_1_budget_and_diagnostics(self):
        configuration = BuildMainConfiguration(1, 4, 2)
        policy = ForcedBuildMainPolicy(configuration)
        simulator = HeadlessPuyoSimulator(seed=41)
        observation = encode_observation(simulator, step_count=0, max_steps=40)
        info = {
            "simulator": simulator,
            "action_mask": legal_action_mask(simulator),
            "step_count": 0,
            "policy_deadline": 1,
        }

        action = policy.select_action(observation, info)
        request = policy.last_proposal.planner_request

        self.assertTrue(info["action_mask"][action])
        self.assertEqual(request.tactic_id, "build_main")
        self.assertEqual(request.tactic_version, "1.1")
        self.assertEqual(request.target_chain, 10)
        self.assertEqual(request.trigger_preservation, "required")
        self.assertEqual(request.candidate_count, 2)
        self.assertEqual(policy.last_proposal.potential_probe_width, 2)
        self.assertGreater(policy.last_proposal.potential_probe_count, 0)

    def test_verify_detects_manifest_artifact_tampering(self):
        summaries = [
            {
                "config_id": "failed",
                "passed": False,
                "decision_p95_ms": 1.0,
                "depth": 6,
                "width": 24,
                "probe_width": 8,
            }
        ]
        selection = build_selection(summaries)
        checkpoint = {"sha256": "a" * 64}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            summary = {
                "evaluation_completed": True,
                "quality_gate_passed": False,
                "selection": selection,
                "configurations": summaries,
            }
            (root / "benchmark_summary.json").write_text(
                json.dumps(summary),
                encoding="utf-8",
            )
            manifest = {
                "schema_version": BENCHMARK_SCHEMA_VERSION,
                "checkpoint": checkpoint,
                "artifacts": [
                    {"path": evidence.name, "sha256": file_sha256(evidence)}
                ],
            }
            (root / "benchmark_manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                artifact_dir=str(root),
                checkpoint="checkpoint.pt",
                require_quality_gate=False,
            )
            with patch(
                "eval.v1_7_build_main_benchmark.checkpoint_evidence",
                return_value=checkpoint,
            ):
                self.assertTrue(verify_benchmark(args)["passed"])
                evidence.write_text('{"tampered": true}\n', encoding="utf-8")
                result = verify_benchmark(args)

        self.assertFalse(result["passed"])
        self.assertIn("artifact hash mismatch", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
