import os
import threading
import time
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
    from src.core.constants import Action, PuyoColor
    from src.ui.versus_renderer import (
        ACTIVE_GHOST_SCALE,
        VersusRenderer,
        animation_progress,
        live_active_pair_cells,
        plan_step_delta_cells,
        settle_scale,
    )

    ENV_AVAILABLE = True
except (ImportError, OSError):
    ENV_AVAILABLE = False
    RealtimeVersusMatchController = None
    RealtimeVersusUiConfig = None
    parse_config = None
    run_ui = None
    legal_indices = None
    PuyoColor = None
    Action = None
    ACTIVE_GHOST_SCALE = None
    VersusRenderer = None
    animation_progress = None
    live_active_pair_cells = None
    plan_step_delta_cells = None

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
        self.assertTrue(config.plan_overlay)

    def test_plan_overlay_can_be_disabled_from_cli(self):
        config = parse_config(["--no-plan-overlay"])

        self.assertFalse(config.plan_overlay)

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

    def test_controller_exposes_policy_plan_overlay_when_enabled(self):
        plan = {
            "schema_version": "n-turn-plan-v1",
            "plan_id": "plan-123",
            "update_reason": "policy_decision",
            "objective": {"reason": "attack"},
            "steps": [],
        }

        class StubPolicy:
            tactical_diagnostics = {"plan": plan, "plan_id": "plan-123"}

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

        self.assertEqual(controller.plan_overlay("player_0")["plan_id"], "plan-123")
        controller.plan_overlay_enabled["player_0"] = False
        self.assertEqual(controller.plan_overlay("player_0"), {})

    def test_plan_step_delta_cells_excludes_existing_board_cells(self):
        base_board = [[PuyoColor.EMPTY for _ in range(6)] for _ in range(12)]
        base_board[0][0] = PuyoColor.RED
        step = {
            "predicted_board": [
                ["RED", "BLUE", "EMPTY", "EMPTY", "EMPTY", "EMPTY"],
                ["EMPTY", "EMPTY", "GREEN", "EMPTY", "EMPTY", "EMPTY"],
            ]
        }

        self.assertEqual(
            plan_step_delta_cells(base_board, step),
            ((1, 0, "BLUE"), (2, 1, "GREEN")),
        )

    def test_slow_policy_does_not_block_other_player_or_render_tick(self):
        release = threading.Event()

        class StubPolicy:
            def __init__(self, slow=False):
                self.slow = slow

            def select_action(self, observation, info):
                if self.slow:
                    release.wait(1.0)
                return legal_indices(info)[0]

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="manager_rule", policy_b="first", max_ticks=80),
            policy_factory=lambda policy_type, **kwargs: StubPolicy(policy_type == "manager_rule"),
        )
        started = time.perf_counter()
        controller.advance_tick()
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.2)
        self.assertEqual(controller.env.match.tick, 1)
        self.assertEqual(controller.controllers["player_0"].diagnostics.last_event, "thinking")
        self.assertGreater(controller.controllers["player_1"].diagnostics.emitted_input_ticks, 0)

        release.set()
        for _ in range(30):
            controller.advance_tick()
            if controller.controllers["player_0"].diagnostics.decisions_activated:
                break
            time.sleep(0.005)
        self.assertGreater(controller.controllers["player_0"].diagnostics.decisions_activated, 0)
        controller.shutdown()

    def test_stale_async_decision_is_rejected_after_active_pair_changes(self):
        release = threading.Event()

        class SlowPolicy:
            def select_action(self, observation, info):
                release.wait(1.0)
                return legal_indices(info)[0]

        class FastPolicy:
            def select_action(self, observation, info):
                return legal_indices(info)[0]

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="beam", policy_b="first", max_ticks=80),
            policy_factory=lambda policy_type, **kwargs: SlowPolicy() if policy_type == "beam" else FastPolicy(),
        )
        controller.advance_tick()
        controller.env.player_states["player_0"].simulator.game.spawn_puyo()
        release.set()
        for _ in range(20):
            controller.advance_tick()
            if controller.controllers["player_0"].diagnostics.stale_decisions:
                break
            time.sleep(0.005)
        self.assertEqual(controller.controllers["player_0"].diagnostics.stale_decisions, 1)
        controller.shutdown()

    def test_human_soft_drop_edges_do_not_pause_ai(self):
        class StubPolicy:
            def select_action(self, observation, info):
                return legal_indices(info)[0]

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="human", policy_b="first", max_ticks=80),
            policy_factory=lambda policy_type, **kwargs: StubPolicy(),
        )
        controller.handle_keydown(pygame.K_w)
        controller.advance_tick()
        self.assertIn(Action.DOWN, controller.last_inputs["player_0"].press)
        self.assertGreater(controller.controllers["player_1"].diagnostics.decisions_started, 0)

        controller.advance_tick()
        controller.advance_tick()
        self.assertIn(Action.DOWN, controller.last_inputs["player_0"].press)
        self.assertIn(Action.DOWN, controller.last_inputs["player_0"].release)

        controller.handle_keyup(pygame.K_w)
        controller.advance_tick()
        self.assertIn(Action.DOWN, controller.last_inputs["player_0"].release)
        controller.shutdown()

    def test_plan_ghost_is_full_size_color_outline_without_center_label(self):
        surface = pygame.Surface((160, 160))
        surface.fill((1, 2, 3))
        renderer = VersusRenderer(surface)
        field = pygame.Rect(32, 32, 6 * 32, 12 * 32)
        renderer._draw_plan_cell(field, 0, 11, "RED", alpha=255)
        sx, sy = renderer._grid_position(field, 0, 11)
        center = (int(sx + 16), int(sy + 16))
        radius = int(32 * 0.38)
        outline_colors = {
            surface.get_at((int(sx + 16 + offset), int(sy + 16)))[:3]
            for offset in range(radius - 2, radius + 2)
        }

        self.assertEqual(surface.get_at(center)[:3], (1, 2, 3))
        self.assertIn(renderer.colors[PuyoColor.RED], outline_colors)

    def test_active_ghost_has_no_white_outline(self):
        surface = pygame.Surface((64, 64))
        surface.fill((1, 2, 3))
        renderer = VersusRenderer(surface)
        renderer._draw_puyo(16, 16, renderer.colors[PuyoColor.BLUE], alpha=150, scale=ACTIVE_GHOST_SCALE)
        radius = int(32 * 0.38 * ACTIVE_GHOST_SCALE)
        outside = surface.get_at((32 + radius + 2, 32))[:3]

        self.assertEqual(outside, (1, 2, 3))

    def test_visual_timeline_is_elapsed_time_based_and_active_ghost_is_half_size(self):
        self.assertEqual(animation_progress(0.2, 0.4), animation_progress(0.1 + 0.1, 0.4))
        self.assertEqual(settle_scale(1.0), (1.0, 1.0))
        self.assertEqual(ACTIVE_GHOST_SCALE, 0.5)


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
