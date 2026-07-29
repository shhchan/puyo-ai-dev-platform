import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

try:
    import pygame

    from agents.v1_7_analyzer_manager import V17AnalyzerManagerPolicy
    from eval.v1_7_orchestration_demo import (
        DEFAULT_DEMO_TUNING,
        DEMO_BUDGET_CAP,
        DEMO_DISCLAIMERS,
        DEMO_PRESETS,
        DemoRuntimeTuning,
        GifRecorder,
        build_demo_config,
        build_runtime_tuning,
        default_output_dir,
        make_demo_policy,
        parse_args,
        verify_replay,
    )

    ENV_AVAILABLE = True
except (ImportError, OSError):
    ENV_AVAILABLE = False
    pygame = None


@unittest.skipUnless(ENV_AVAILABLE, "demo dependencies are not installed")
class TestV17OrchestrationDemo(unittest.TestCase):
    def test_primary_and_fallback_presets_keep_evidence_boundary(self):
        primary = DEMO_PRESETS["primary"]
        fallback = DEMO_PRESETS["fallback"]

        self.assertEqual(primary.policy_a, "v1_7_bootstrap_manager")
        self.assertEqual(fallback.policy_a, "v1_7_analyzer_manager")
        self.assertEqual(fallback.policy_b, "manager_rule")
        self.assertEqual(DEMO_BUDGET_CAP.max_search_depth, 1)
        self.assertTrue(any("PUYO-176" in item for item in DEMO_DISCLAIMERS))

    def test_demo_factory_caps_checkpoint_free_analyzer_policy(self):
        policy = make_demo_policy("v1_7_analyzer_manager")

        self.assertIsInstance(policy, V17AnalyzerManagerPolicy)
        self.assertEqual(policy.planner_budget_cap, DEMO_BUDGET_CAP)

    def test_demo_factory_applies_custom_target_and_budget_to_analyzer(self):
        tuning = DemoRuntimeTuning(
            preview_top_k=2,
            planner_budget_cap=DEMO_BUDGET_CAP,
            target_chain=12,
            opponent="worker_large",
        )

        policy = make_demo_policy(
            "v1_7_analyzer_manager",
            tuning=tuning,
        )

        self.assertEqual(
            policy.parameter_overrides,
            {
                "build_main": {
                    "objective": {
                        "target_chain": 12,
                    }
                }
            },
        )

    def test_demo_config_enables_terminal_auto_exit_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = build_demo_config(
                DEMO_PRESETS["fallback"],
                seed=123,
                max_ticks=800,
                speed=1.0,
                result_path=root / "result.json",
                replay_path=root / "replay.json",
                qa_profile="playability",
                tuning=DemoRuntimeTuning(opponent="worker_large"),
            )

        self.assertEqual(config.policy_a, "v1_7_analyzer_manager")
        self.assertEqual(config.policy_b, "worker_large")
        self.assertEqual(config.qa_profile, "playability")
        self.assertEqual(config.exit_after_finish_frames, 90)
        self.assertTrue(config.plan_overlay)

    def test_checked_in_realtime_replay_verifies(self):
        verification = verify_replay(
            Path(__file__).parents[1]
            / "docs"
            / "benchmarks"
            / "puyo-v1-7-1-smoke"
            / "gui_qa_replay.json"
        )

        self.assertTrue(verification["valid"])
        self.assertEqual(
            verification["expected_final_hash"],
            verification["verified_final_hash"],
        )

    def test_gif_recorder_writes_bounded_animation(self):
        pygame.init()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.gif"
            recorder = GifRecorder(
                path,
                capture_fps=2,
                duration_seconds=1,
                render_fps=2,
                size=(64, 48),
            )
            surface = pygame.Surface((128, 96))
            for frame in range(3):
                surface.fill((frame * 50, 10, 20))
                recorder.capture(surface, frame)
            recorder.save()

            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 0)
        pygame.quit()

    def test_cli_defaults_to_primary_recording(self):
        args = parse_args(["qa"])
        tuning = build_runtime_tuning(args)

        self.assertEqual(args.preset, "primary")
        self.assertTrue(args.record_gif)
        self.assertEqual(args.record_seconds, 30)
        self.assertEqual(tuning, DEFAULT_DEMO_TUNING)
        json.dumps(vars(args))

    def test_cli_builds_auditable_custom_tuning(self):
        args = parse_args(
            [
                "live",
                "--target-chain",
                "12",
                "--opponent",
                "worker_large",
                "--preview-top-k",
                "3",
                "--planner-depth-cap",
                "2",
                "--planner-width-cap",
                "12",
                "--planner-candidate-cap",
                "4",
                "--planner-latency-cap-ms",
                "500",
            ]
        )

        tuning = build_runtime_tuning(args)

        self.assertEqual(tuning.target_chain, 12)
        self.assertEqual(tuning.opponent, "worker_large")
        self.assertEqual(tuning.preview_top_k, 3)
        self.assertEqual(tuning.planner_budget_cap.max_search_depth, 2)
        self.assertEqual(tuning.planner_budget_cap.max_search_width, 12)
        self.assertEqual(tuning.planner_budget_cap.max_candidate_count, 4)
        self.assertEqual(tuning.planner_budget_cap.max_latency_budget_ms, 500.0)
        json.dumps(tuning.to_dict())

    def test_custom_tuning_does_not_overwrite_default_artifacts(self):
        preset = DEMO_PRESETS["primary"]
        default_path = default_output_dir(
            preset=preset,
            seed=preset.seed,
            max_ticks=preset.max_ticks,
            speed=preset.speed,
            tuning=DEFAULT_DEMO_TUNING,
            qa=True,
        )
        custom_path = default_output_dir(
            preset=preset,
            seed=preset.seed,
            max_ticks=14_400,
            speed=preset.speed,
            tuning=DemoRuntimeTuning(target_chain=12),
            qa=True,
        )

        self.assertEqual(default_path.name, "qa")
        self.assertEqual(custom_path.parent.name, "qa")
        self.assertTrue(custom_path.name.startswith("custom-"))
        self.assertNotEqual(default_path, custom_path)

    def test_cli_can_disable_the_presentation_planner_cap(self):
        tuning = build_runtime_tuning(parse_args(["live", "--no-planner-cap"]))

        self.assertIsNone(tuning.planner_budget_cap)
        self.assertNotEqual(tuning, DEFAULT_DEMO_TUNING)


if __name__ == "__main__":
    unittest.main()
