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
        DEMO_BUDGET_CAP,
        DEMO_DISCLAIMERS,
        DEMO_PRESETS,
        GifRecorder,
        build_demo_config,
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
            )

        self.assertEqual(config.policy_a, "v1_7_analyzer_manager")
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

        self.assertEqual(args.preset, "primary")
        self.assertTrue(args.record_gif)
        self.assertEqual(args.record_seconds, 30)
        json.dumps(vars(args))


if __name__ == "__main__":
    unittest.main()
