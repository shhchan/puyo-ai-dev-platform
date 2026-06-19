import os
import unittest


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401

    from eval.realtime_versus_ui import parse_config as parse_realtime_config
    from eval.versus_ui import parse_config as parse_versus_config
    from src.ui.launcher import LauncherController, LauncherService, run_launcher

    ENV_AVAILABLE = True
except (ImportError, OSError):
    ENV_AVAILABLE = False
    parse_realtime_config = None
    parse_versus_config = None
    LauncherController = None
    LauncherService = None
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
    def test_play_command_round_trips_through_existing_versus_parser(self):
        service = LauncherService(python_executable="python3")
        config = parse_versus_config(service.command_for("play")[3:])

        self.assertEqual(config.policy_a, "human")
        self.assertEqual(config.policy_b, "greedy")
        self.assertEqual(config.seed, 57)
        self.assertTrue(config.start_paused)

    def test_spectate_command_round_trips_through_existing_realtime_parser(self):
        service = LauncherService(python_executable="python3")
        config = parse_realtime_config(service.command_for("spectate")[3:])

        self.assertEqual(config.policy_a, "first")
        self.assertEqual(config.policy_b, "random")
        self.assertEqual(config.max_ticks, 600)
        self.assertTrue(config.start_paused)

    def test_job_start_stop_and_busy_message(self):
        processes = []

        def fake_popen(command, cwd=None):
            self.assertIn("-m", command)
            self.assertIsNotNone(cwd)
            process = FakeProcess()
            processes.append(process)
            return process

        service = LauncherService(python_executable="python3", popen_factory=fake_popen)

        self.assertTrue(service.start("arena"))
        self.assertFalse(service.start("models"))
        self.assertIn("Stop Arena", service.message)
        self.assertTrue(service.stop())
        self.assertTrue(processes[0].terminated)


@unittest.skipUnless(PYGAME_AVAILABLE, "pygame is not installed")
class TestLauncherController(unittest.TestCase):
    def test_home_navigation_reaches_workflow_screen(self):
        service = LauncherService(python_executable="python3")
        controller = LauncherController(service)

        controller.handle_keydown(pygame.K_DOWN)
        controller.handle_keydown(pygame.K_RETURN)

        self.assertEqual(controller.screen, "spectate")
        controller.handle_keydown(pygame.K_ESCAPE)
        self.assertEqual(controller.screen, "home")

    def test_dummy_video_driver_smoke_renders_home(self):
        result = run_launcher(service=LauncherService(python_executable="python3"), max_frames=1)

        self.assertEqual(result["screen"], "home")
        self.assertIn("No job", result["job"])


if __name__ == "__main__":
    unittest.main()
