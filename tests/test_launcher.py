import os
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401

    from eval.realtime_versus_ui import parse_config as parse_realtime_config
    from eval.versus_ui import parse_config as parse_versus_config
    from src.ui.launcher import LauncherController, LauncherService, UI_ASSET_FONT, _font, run_launcher

    ENV_AVAILABLE = True
except (ImportError, OSError):
    ENV_AVAILABLE = False
    parse_realtime_config = None
    parse_versus_config = None
    LauncherController = None
    LauncherService = None
    UI_ASSET_FONT = None
    _font = None
    run_launcher = None

try:
    import pygame  # noqa: F401

    PYGAME_AVAILABLE = ENV_AVAILABLE
except (ImportError, OSError):
    PYGAME_AVAILABLE = False


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None if not self.terminated else -15

    def terminate(self):
        self.terminated = True


@unittest.skipUnless(ENV_AVAILABLE, "gymnasium/numpy are not installed")
class TestLauncherService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.preset_store_path = Path(self.temp_dir.name) / "presets.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_service(self, **kwargs):
        kwargs.setdefault("python_executable", "python3")
        kwargs.setdefault("preset_store_path", self.preset_store_path)
        return LauncherService(**kwargs)

    def test_play_command_round_trips_through_realtime_parser(self):
        service = self.make_service()
        config = parse_realtime_config(service.command_for("play")[3:])

        self.assertEqual(config.policy_a, "human")
        self.assertEqual(config.policy_b, "greedy")
        self.assertEqual(config.seed, 57)
        self.assertEqual(config.max_ticks, 10_000)
        self.assertTrue(config.start_paused)

    def test_spectate_command_round_trips_through_existing_realtime_parser(self):
        service = self.make_service()
        config = parse_realtime_config(service.command_for("spectate")[3:])

        self.assertEqual(config.policy_a, "first")
        self.assertEqual(config.policy_b, "random")
        self.assertEqual(config.max_ticks, 600)
        self.assertTrue(config.start_paused)

    def test_spectate_exposes_and_round_trips_realtime_arguments(self):
        service = self.make_service()
        service.update_setting("spectate", "max_ticks", 720)
        service.update_setting("spectate", "inference_latency_ticks", 2)
        service.update_setting("spectate", "timeout_ticks", 5)
        service.update_setting("spectate", "action_deadline_ticks", 4)
        service.update_setting("spectate", "use_reachable_action_mask", True)
        service.update_setting("spectate", "max_frames", 9)
        service.update_setting("spectate", "result_json", "/tmp/result.json")

        fields = service.settings.editable_fields("spectate")
        for field in (
            "max_ticks",
            "inference_latency_ticks",
            "timeout_ticks",
            "action_deadline_ticks",
            "use_reachable_action_mask",
            "result_json",
            "max_frames",
        ):
            self.assertIn(field, fields)

        config = parse_realtime_config(service.command_for("spectate")[3:])

        self.assertEqual(config.max_ticks, 720)
        self.assertEqual(config.inference_latency_ticks, 2)
        self.assertEqual(config.timeout_ticks, 5)
        self.assertEqual(config.action_deadline_ticks, 4)
        self.assertTrue(config.use_reachable_action_mask)
        self.assertEqual(config.max_frames, 9)
        self.assertEqual(config.result_json, "/tmp/result.json")

    def test_setting_help_describes_cli_argument(self):
        service = self.make_service()

        self.assertIn("--max-ticks", service.settings.field_help("spectate", "max_ticks"))
        self.assertIn("realtime", service.settings.field_help("spectate", "max_ticks"))

    def test_gui_policy_seed_and_beam_settings_round_trip_to_play_command(self):
        service = self.make_service()
        service.update_setting("play", "policy_b", "beam")
        service.update_setting("play", "seed", 57)
        service.update_setting("play", "seed_a", 101)
        service.update_setting("play", "seed_b", 202)
        service.update_setting("play", "beam_depth_a", 8)
        service.update_setting("play", "beam_depth_b", 10)
        service.update_setting("play", "speed", 2.0)

        config = parse_realtime_config(service.command_for("play")[3:])

        self.assertEqual(config.policy_a, "human")
        self.assertEqual(config.policy_b, "beam")
        self.assertEqual(config.seed, 57)
        self.assertEqual((config.seed_a, config.seed_b), (101, 202))
        self.assertEqual((config.beam_depth_a, config.beam_depth_b), (8, 10))
        self.assertEqual(config.speed, 2.0)

    def test_checkpoint_policy_is_rejected_before_process_start_without_path(self):
        started = []

        def fake_popen(command, cwd=None):
            started.append(command)
            return FakeProcess()

        service = self.make_service(popen_factory=fake_popen)
        service.update_setting("play", "policy_b", "checkpoint")

        self.assertFalse(service.start("play"))
        self.assertFalse(started)
        self.assertIn("checkpoint_b is required", service.message)

    def test_incompatible_manager_checkpoint_is_rejected_for_checkpoint_policy(self):
        import torch

        checkpoint_path = Path(self.temp_dir.name) / "manager.pt"
        torch.save(
            {
                "policy_type": "strategy_manager",
                "model_state_dict": {"actor.weight": torch.zeros((1, 1))},
            },
            checkpoint_path,
        )
        service = self.make_service()
        service.update_setting("play", "policy_b", "checkpoint")
        service.update_setting("play", "checkpoint_b", str(checkpoint_path))

        errors = service.validate_action("play")

        self.assertTrue(any("use policy_b=manager" in error for error in errors))

    def test_training_config_path_is_validated(self):
        service = self.make_service()
        service.update_setting("training", "config_path", "missing.yaml")

        self.assertIn("config_path does not exist", service.validate_action("training")[0])

    def test_models_command_launches_model_viewer(self):
        service = self.make_service()
        command = service.command_for("models")

        self.assertEqual(command[:3], ("python3", "-m", "eval.model_viewer"))
        self.assertIn("--lineage-root", command)
        self.assertIn("--report-json", command)

    def test_named_preset_persists_and_loads_for_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "presets.json"
            service = LauncherService(python_executable="python3", preset_store_path=store_path)
            service.update_setting("spectate", "policy_a", "beam")
            service.update_setting("spectate", "beam_width_a", 16)
            service.save_preset("spectate")

            loaded = LauncherService(python_executable="python3", preset_store_path=store_path)
            self.assertEqual(loaded.load_next_preset("spectate"), "last-spectate")
            config = parse_realtime_config(loaded.command_for("spectate")[3:])

            self.assertEqual(config.policy_a, "beam")
            self.assertEqual(config.beam_width_a, 16)

    def test_job_start_stop_and_busy_message(self):
        processes = []

        def fake_popen(command, cwd=None):
            self.assertIn("-m", command)
            self.assertIsNotNone(cwd)
            process = FakeProcess()
            processes.append(process)
            return process

        service = self.make_service(popen_factory=fake_popen)

        self.assertTrue(service.start("arena"))
        self.assertFalse(service.start("models"))
        self.assertIn("評価 を停止", service.message)
        self.assertTrue(service.stop())
        self.assertTrue(processes[0].terminated)


@unittest.skipUnless(PYGAME_AVAILABLE, "pygame is not installed")
class TestLauncherController(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.preset_store_path = Path(self.temp_dir.name) / "presets.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_service(self):
        return LauncherService(python_executable="python3", preset_store_path=self.preset_store_path)

    def test_home_navigation_reaches_workflow_screen(self):
        service = self.make_service()
        controller = LauncherController(service)

        controller.handle_keydown(pygame.K_DOWN)
        controller.handle_keydown(pygame.K_RETURN)

        self.assertEqual(controller.screen, "spectate")
        controller.handle_keydown(pygame.K_ESCAPE)
        self.assertEqual(controller.screen, "home")

    def test_settings_screen_enters_edit_mode_before_changing_field(self):
        service = self.make_service()
        controller = LauncherController(service)
        controller.screen = "play"
        controller.selection = 0

        controller.handle_keydown(pygame.K_RETURN)
        self.assertTrue(controller.settings_mode)
        self.assertEqual(controller.current_options[0], "policy_a")

        controller.handle_keydown(pygame.K_RIGHT)
        self.assertEqual(service.settings.for_action("play").policy_a, "human")
        self.assertEqual(controller.current_options[controller.selection], "seed_b")
        controller.handle_keydown(pygame.K_LEFT)
        controller.handle_keydown(pygame.K_RETURN)
        self.assertEqual(controller.editing_field, "policy_a")
        controller.handle_keydown(pygame.K_RIGHT)
        self.assertNotEqual(service.settings.for_action("play").policy_a, "human")
        controller.handle_keydown(pygame.K_RETURN)
        controller.handle_keydown(pygame.K_ESCAPE)

        self.assertEqual(controller.screen, "play")
        self.assertFalse(controller.settings_mode)

    def test_settings_screen_pages_to_later_realtime_arguments(self):
        service = self.make_service()
        controller = LauncherController(service)
        controller.screen = "spectate"
        controller.settings_mode = True

        self.assertNotIn("inference_latency_ticks", controller.current_options)
        controller.selection = controller.current_options.index("next_page")
        controller.handle_keydown(pygame.K_RETURN)
        controller.selection = controller.current_options.index("next_page")
        controller.handle_keydown(pygame.K_RETURN)

        self.assertIn("inference_latency_ticks", controller.current_options)

    def test_mouse_hover_focuses_and_click_selects_setting(self):
        service = self.make_service()
        controller = LauncherController(service)

        controller.handle_mouse_down((70, 118), 1)
        self.assertEqual(controller.screen, "play")

        controller.handle_mouse_down((60, 506), 1)
        self.assertTrue(controller.settings_mode)

        before = service.settings.for_action("play").policy_a
        controller.handle_mouse_motion((80, 220))
        controller.handle_mouse_down((80, 220), 1)
        self.assertEqual(controller.editing_field, "policy_a")
        self.assertEqual(service.settings.for_action("play").policy_a, before)
        controller.handle_keydown(pygame.K_RIGHT)
        self.assertNotEqual(service.settings.for_action("play").policy_a, before)

    def test_string_editor_filters_candidates_and_numeric_editor_uses_repeatable_arrows(self):
        service = self.make_service()
        controller = LauncherController(service)
        controller.screen = "play"
        controller.settings_mode = True
        controller.selection = controller.current_options.index("checkpoint_a")
        controller.handle_keydown(pygame.K_RETURN)
        controller.handle_text_input("tmp")
        self.assertEqual(controller.search_query, "tmp")

        controller.handle_keydown(pygame.K_ESCAPE)
        controller.selection = controller.current_options.index("seed")
        controller.handle_keydown(pygame.K_RETURN)
        before = service.settings.for_action("play").seed
        controller.handle_keydown(pygame.K_RIGHT)
        self.assertEqual(service.settings.for_action("play").seed, before + 1)

    def test_choice_editor_accepts_text_and_lists_every_policy(self):
        service = self.make_service()
        controller = LauncherController(service)
        controller.screen = "play"
        controller.settings_mode = True
        controller.selection = controller.current_options.index("policy_a")
        controller.handle_keydown(pygame.K_RETURN)

        all_choices = controller.filtered_choices()
        self.assertEqual(len(controller._candidate_rects()), len(all_choices))
        self.assertIn("worker_survival", all_choices)

        controller.handle_text_input("manager")
        self.assertEqual(controller.search_query, "manager")
        self.assertEqual(controller.filtered_choices(), ("manager", "manager_rule"))
        self.assertEqual(service.settings.for_action("play").policy_a, "manager")

    def test_numeric_editor_buttons_are_clickable_and_do_not_hit_setting_rows(self):
        service = self.make_service()
        controller = LauncherController(service)
        controller.screen = "play"
        controller.settings_mode = True
        controller.selection = controller.current_options.index("speed")
        controller.handle_keydown(pygame.K_RETURN)
        before = service.settings.for_action("play").speed
        button = controller._numeric_button_rect(1)

        self.assertFalse(any(button.colliderect(controller._option_rect(i, len(controller.current_options))) for i in range(len(controller.visible_setting_fields()))))
        controller.handle_mouse_down(button.center, 1)
        self.assertGreater(service.settings.for_action("play").speed, before)

    def test_keyboard_navigation_reaches_all_settings_footer_actions(self):
        controller = LauncherController(self.make_service())
        controller.screen = "play"
        controller.settings_mode = True
        field_count = len(controller.visible_setting_fields())
        controller.selection = 5

        controller.handle_keydown(pygame.K_DOWN)
        self.assertEqual(controller.current_options[controller.selection], "prev_page")
        controller.handle_keydown(pygame.K_RIGHT)
        self.assertEqual(controller.current_options[controller.selection], "next_page")
        controller.handle_keydown(pygame.K_RIGHT)
        self.assertEqual(controller.current_options[controller.selection], "back")
        self.assertEqual(controller.selection, field_count + 2)

    def test_bundled_font_supports_japanese_glyphs(self):
        self.assertTrue(UI_ASSET_FONT.exists())
        font = _font(24)

        self.assertTrue(all(metric is not None for metric in font.metrics("ぷよ設定")))

    def test_dummy_video_driver_smoke_renders_home(self):
        result = run_launcher(service=self.make_service(), max_frames=1)

        self.assertEqual(result["screen"], "home")
        self.assertIn("job なし", result["job"])


if __name__ == "__main__":
    unittest.main()
