import os
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401

    from eval.versus_ui import (
        VersusMatchController,
        VersusUiConfig,
        build_visual_events,
        parse_config,
        run_ui,
    )
    from puyo_env.versus_env import AGENTS, VersusPuyoEnv
    from selfplay.policies import legal_indices
    from src.ui.keybindings import KeyBindings
    from src.ui.versus_renderer import decompose_ojama

    ENV_AVAILABLE = True
except (ImportError, OSError):
    ENV_AVAILABLE = False
    VersusMatchController = None
    VersusUiConfig = None
    build_visual_events = None
    parse_config = None
    run_ui = None
    AGENTS = ()
    VersusPuyoEnv = None
    legal_indices = None
    KeyBindings = None
    decompose_ojama = None

try:
    import pygame  # noqa: F401

    PYGAME_AVAILABLE = ENV_AVAILABLE
except (ImportError, OSError):
    PYGAME_AVAILABLE = False


@unittest.skipUnless(ENV_AVAILABLE, "gymnasium/numpy are not installed")
class TestVersusUiConfig(unittest.TestCase):
    def test_checkpoint_path_is_required(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_config(["--policy-a", "checkpoint"])

    def test_human_vs_checkpoint_config(self):
        config = parse_config(
            [
                "--policy-a",
                "human",
                "--policy-b",
                "checkpoint",
                "--checkpoint-b",
                "model.pt",
                "--seed",
                "42",
            ]
        )

        self.assertEqual(config.policy_a, "human")
        self.assertEqual(config.policy_b, "checkpoint")
        self.assertEqual(config.seed, 42)

    def test_keybindings_path_can_be_overridden(self):
        config = parse_config(["--keybindings", "/tmp/puyo-keys.json"])

        self.assertEqual(config.keybindings_path, "/tmp/puyo-keys.json")

    def test_two_human_players_are_rejected(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_config(["--policy-a", "human", "--policy-b", "human"])


@unittest.skipUnless(ENV_AVAILABLE, "gymnasium/numpy are not installed")
class TestVersusMatchController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_keybinding_changes_are_saved_and_reloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "keys.json")
            bindings = KeyBindings(path)

            bindings.rebind("pause", pygame.K_z)
            reloaded = KeyBindings(path)

            self.assertTrue(reloaded.matches("pause", pygame.K_z))
            self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8"))["pause"], ["z"])
            reloaded.reset_defaults()
            self.assertTrue(KeyBindings(path).matches("pause", pygame.K_p))

    def test_key_settings_overlay_rebinds_controller_action(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "keys.json")
            controller = VersusMatchController(
                VersusUiConfig(
                    policy_a="greedy",
                    policy_b="random",
                    start_paused=False,
                    keybindings_path=path,
                )
            )

            controller.handle_keydown(pygame.K_F1)
            controller.settings_index = 1
            controller.handle_keydown(pygame.K_RETURN)
            controller.handle_keydown(pygame.K_z)
            controller.handle_keydown(pygame.K_ESCAPE)
            controller.handle_keydown(pygame.K_z)

            self.assertFalse(controller.settings_open)
            self.assertTrue(controller.paused)

    def test_ojama_2000_uses_standard_forecast_order(self):
        self.assertEqual(
            decompose_ojama(2000),
            ["comet", "moon", "star", "large", "large", "large", "small", "small"],
        )

    def test_each_ojama_forecast_symbol_has_its_denominator(self):
        self.assertEqual(
            decompose_ojama(2737),
            ["comet", "crown", "moon", "star", "rock", "large", "small"],
        )

    def test_same_seed_and_actions_match_headless_environment(self):
        config = VersusUiConfig(policy_a="random", policy_b="random", seed=17, max_steps=20)
        controller = VersusMatchController(config)
        reference = VersusPuyoEnv(seed=17, max_steps=20)
        _, reference_infos = reference.reset(seed=17)

        for _ in range(4):
            actions = {
                agent: legal_indices(reference_infos[agent])[0]
                for agent in AGENTS
            }
            controller.step_with_actions(actions)
            _, _, _, _, reference_infos = reference.step(actions)

            for agent in AGENTS:
                actual_state = controller.env.player_states[agent]
                expected_state = reference.player_states[agent]
                self.assertEqual(
                    actual_state.simulator.game.field.to_color_grid(),
                    expected_state.simulator.game.field.to_color_grid(),
                )
                self.assertEqual(actual_state.simulator.game.score, expected_state.simulator.game.score)
                self.assertEqual(actual_state.pending_ojama, expected_state.pending_ojama)
                self.assertEqual(
                    controller.infos[agent]["step_result"].chain_count,
                    reference_infos[agent]["step_result"].chain_count,
                )
            self.assertEqual(controller.winner, reference_infos["player_0"].get("winner"))

    def test_step_result_generates_placement_events(self):
        controller = VersusMatchController(
            VersusUiConfig(policy_a="greedy", policy_b="random", seed=2, max_steps=2)
        )
        actions = {
            agent: legal_indices(controller.infos[agent])[0]
            for agent in AGENTS
        }

        controller.step_with_actions(actions)

        events = [controller.current_event, *controller.event_queue]
        self.assertEqual(sum(event.kind == "placement" for event in events if event), 2)
        self.assertIsNotNone(controller.infos["player_0"]["step_result"])

    def test_paused_single_step_advances_one_joint_action(self):
        controller = VersusMatchController(
            VersusUiConfig(
                policy_a="greedy",
                policy_b="random",
                seed=8,
                max_steps=4,
                start_paused=True,
            )
        )

        self.assertTrue(controller.advance_one())
        self.assertEqual(controller.env.step_count, 1)
        self.assertTrue(controller.advance_one())
        self.assertEqual(controller.env.step_count, 2)

    def test_event_builder_uses_chain_and_garbage_results(self):
        chain = SimpleNamespace(chain_index=2, score=360, vanished=frozenset({(1, 2), (1, 3)}))
        result = SimpleNamespace(valid=True, axis_y=4, chains=(chain,))
        infos = {
            "player_0": {
                "reward_components": {"garbage_received": 6},
                "step_result": result,
            },
            "player_1": {
                "reward_components": {"garbage_received": 0},
                "step_result": result,
            },
        }

        events = build_visual_events(
            {"player_0": 0, "player_1": 0},
            infos,
            {"player_0": (None, None), "player_1": (None, None)},
        )

        self.assertEqual(events[0].kind, "garbage")
        self.assertEqual(sum(event.kind == "chain" for event in events), 2)
        self.assertEqual(next(event for event in events if event.kind == "chain").coords, chain.vanished)


@unittest.skipUnless(PYGAME_AVAILABLE, "pygame is not installed")
class TestVersusUiSmoke(unittest.TestCase):
    def test_dummy_video_driver_smoke(self):
        result = run_ui(
            VersusUiConfig(
                policy_a="greedy",
                policy_b="random",
                seed=3,
                max_steps=2,
                start_paused=True,
            ),
            max_frames=2,
        )

        self.assertEqual(result["steps"], 0)


if __name__ == "__main__":
    unittest.main()
