import os
import unittest
from contextlib import redirect_stderr
from io import StringIO


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401

    from eval.realtime_versus_ui import (
        RealtimeVersusMatchController,
        RealtimeVersusUiConfig,
        parse_config,
        run_ui,
    )
    from selfplay.policies import legal_indices
    from src.ui.versus_renderer import live_active_pair_cells

    ENV_AVAILABLE = True
except (ImportError, OSError):
    ENV_AVAILABLE = False
    RealtimeVersusMatchController = None
    RealtimeVersusUiConfig = None
    parse_config = None
    run_ui = None
    legal_indices = None
    live_active_pair_cells = None

try:
    import pygame  # noqa: F401

    PYGAME_AVAILABLE = ENV_AVAILABLE
except (ImportError, OSError):
    PYGAME_AVAILABLE = False


@unittest.skipUnless(ENV_AVAILABLE, "gymnasium/numpy are not installed")
class TestRealtimeVersusUiConfig(unittest.TestCase):
    def test_realtime_policy_options_are_parsed(self):
        config = parse_config(
            [
                "--policy-a",
                "first",
                "--policy-b",
                "beam",
                "--seed",
                "54",
                "--max-ticks",
                "120",
                "--inference-latency-ticks",
                "2",
                "--timeout-ticks",
                "4",
                "--use-reachable-action-mask",
            ]
        )

        self.assertEqual(config.policy_a, "first")
        self.assertEqual(config.policy_b, "beam")
        self.assertEqual(config.max_ticks, 120)
        self.assertEqual(config.max_steps, 120)
        self.assertEqual(config.inference_latency_ticks, 2)
        self.assertTrue(config.use_reachable_action_mask)

    def test_checkpoint_path_is_required(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_config(["--policy-a", "checkpoint"])


@unittest.skipUnless(PYGAME_AVAILABLE, "pygame is not installed")
class TestRealtimeVersusMatchController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_controller_advances_realtime_ticks_and_exposes_diagnostics(self):
        class StubPolicy:
            def select_action(self, observation, info):
                return legal_indices(info)[0]

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="first",
                seed=54,
                max_ticks=80,
                start_paused=True,
            ),
            policy_factory=lambda policy_type, **kwargs: StubPolicy(),
        )

        for _ in range(16):
            controller.advance_tick()

        self.assertEqual(controller.env.match.tick, 16)
        self.assertGreater(controller.controllers["player_0"].diagnostics.decisions_started, 0)
        self.assertGreater(controller.controllers["player_0"].diagnostics.emitted_input_ticks, 0)
        self.assertTrue(live_active_pair_cells(controller.env.player_states["player_0"].simulator.game))
        diagnostics = controller.realtime_diagnostics("player_0")
        self.assertIn("input", diagnostics)
        self.assertIn("plan", diagnostics)


@unittest.skipUnless(PYGAME_AVAILABLE, "pygame is not installed")
class TestRealtimeVersusUiSmoke(unittest.TestCase):
    def test_initial_paused_frame_renders_before_first_tick(self):
        result = run_ui(
            RealtimeVersusUiConfig(
                policy_a="manager_rule",
                policy_b="beam",
                seed=54,
                max_ticks=80,
                beam_depth=2,
                beam_width=8,
                start_paused=True,
            ),
            max_frames=1,
        )

        self.assertEqual(result["ticks"], 0)
        self.assertEqual(result["decisions_player_0"], 0)

    def test_dummy_video_driver_smoke_advances_match_ticks(self):
        result = run_ui(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="random",
                seed=54,
                max_ticks=80,
                speed=4.0,
            ),
            max_frames=6,
        )

        self.assertGreater(result["ticks"], 0)
        self.assertGreater(result["decisions_player_0"], 0)


if __name__ == "__main__":
    unittest.main()
