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
        HumanPlacement,
        VersusMatchController,
        VersusUiConfig,
        build_visual_events,
        parse_config,
        run_ui,
    )
    from puyo_env.actions import ACTION_TO_INDEX, NUM_ACTIONS, action_to_placement
    from puyo_env.versus_env import AGENTS, VersusPuyoEnv
    from selfplay.policies import legal_indices
    from src.core.constants import Direction, PuyoColor
    from src.core.headless import PlacementAction
    from src.ui.keybindings import KeyBindings
    from src.ui.versus_renderer import active_pair_cells, decompose_ojama, winner_banner_label

    ENV_AVAILABLE = True
except (ImportError, OSError):
    ENV_AVAILABLE = False
    VersusMatchController = None
    VersusUiConfig = None
    HumanPlacement = None
    build_visual_events = None
    parse_config = None
    run_ui = None
    AGENTS = ()
    VersusPuyoEnv = None
    legal_indices = None
    KeyBindings = None
    decompose_ojama = None
    active_pair_cells = None
    winner_banner_label = None

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

    def test_manager_checkpoint_can_be_selected_on_either_side(self):
        config = parse_config(
            [
                "--policy-a",
                "manager",
                "--checkpoint-a",
                "manager-a.pt",
                "--policy-b",
                "manager",
                "--checkpoint-b",
                "manager-b.pt",
            ]
        )

        self.assertEqual(config.policy_a, "manager")
        self.assertEqual(config.checkpoint_b, "manager-b.pt")

    def test_keybindings_path_can_be_overridden(self):
        config = parse_config(["--keybindings", "/tmp/puyo-keys.json"])

        self.assertEqual(config.keybindings_path, "/tmp/puyo-keys.json")

    def test_policy_specific_search_options_are_parsed(self):
        config = parse_config(
            [
                "--policy-a",
                "beam",
                "--policy-b",
                "beam",
                "--seed-a",
                "11",
                "--seed-b",
                "22",
                "--beam-depth-a",
                "4",
                "--beam-depth-b",
                "7",
                "--beam-width-a",
                "16",
                "--beam-width-b",
                "32",
                "--stochastic-b",
            ]
        )

        self.assertEqual((config.seed_a, config.seed_b), (11, 22))
        self.assertEqual((config.beam_depth_a, config.beam_depth_b), (4, 7))
        self.assertEqual((config.beam_width_a, config.beam_width_b), (16, 32))
        self.assertIsNone(config.deterministic_a)
        self.assertFalse(config.deterministic_b)

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

    def test_human_starts_in_third_column_facing_up(self):
        human = HumanPlacement("player_0")
        human.reset({"action_mask": [True] * NUM_ACTIONS})

        placement = action_to_placement(human.action)
        self.assertEqual((placement.axis_x, placement.rotation), (2, Direction.UP))

    def test_human_edge_rotation_uses_one_quarter_turn_with_wall_kick(self):
        info = {"action_mask": [True] * NUM_ACTIONS}
        human = HumanPlacement("player_0")

        human.action = ACTION_TO_INDEX[PlacementAction(0, Direction.UP)]
        human.rotate(-1, info)
        self.assertEqual(action_to_placement(human.action), PlacementAction(1, Direction.LEFT))

        human.action = ACTION_TO_INDEX[PlacementAction(5, Direction.UP)]
        human.rotate(1, info)
        self.assertEqual(action_to_placement(human.action), PlacementAction(4, Direction.RIGHT))

    def test_policy_factory_receives_side_specific_parameters(self):
        calls = []

        class StubPolicy:
            def select_action(self, observation, info):
                return legal_indices(info)[0]

        def factory(policy_type, **kwargs):
            calls.append((policy_type, kwargs))
            return StubPolicy()

        VersusMatchController(
            VersusUiConfig(
                policy_a="beam",
                policy_b="beam",
                seed=5,
                seed_a=101,
                seed_b=202,
                beam_depth_a=3,
                beam_depth_b=6,
                beam_width_a=12,
                beam_width_b=24,
            ),
            policy_factory=factory,
        )

        self.assertEqual([kwargs["seed"] for _, kwargs in calls], [101, 202])
        self.assertEqual([kwargs["beam_depth"] for _, kwargs in calls], [3, 6])
        self.assertEqual([kwargs["beam_width"] for _, kwargs in calls], [12, 24])

    def test_policy_display_name_includes_current_manager_profile(self):
        class StubPolicy:
            current_profile_name = "survival"

            def select_action(self, observation, info):
                return legal_indices(info)[0]

        controller = VersusMatchController(
            VersusUiConfig(policy_a="manager_rule", policy_b="random", seed=2),
            policy_factory=lambda policy_type, **kwargs: StubPolicy(),
        )

        self.assertEqual(controller.policy_display_name("player_0"), "manager_rule: survival")

    def test_controller_exposes_manager_tactical_diagnostics(self):
        class StubPolicy:
            current_profile_name = "counter"
            tactical_diagnostics = {
                "incoming_attack": 8,
                "target_attack": 10,
                "deadline": 2,
                "reason": "counter before arrival",
            }

            def select_action(self, observation, info):
                return legal_indices(info)[0]

        controller = VersusMatchController(
            VersusUiConfig(policy_a="manager_rule", policy_b="random", seed=2),
            policy_factory=lambda policy_type, **kwargs: StubPolicy(),
        )

        self.assertEqual(controller.tactical_diagnostics("player_0")["target_attack"], 10)

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
        self.assertTrue(all(event.board for event in events if event and event.kind == "placement"))
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
        placement_board = ((PuyoColor.RED,),)
        chain_board = ((PuyoColor.BLUE,),)
        chain = SimpleNamespace(
            chain_index=2,
            score=360,
            vanished=frozenset({(1, 2), (1, 3)}),
            board=chain_board,
        )
        result = SimpleNamespace(
            valid=True,
            axis_y=4,
            chains=(chain,),
            placement_board=placement_board,
        )
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

        self.assertEqual(events[-1].kind, "garbage")
        self.assertEqual(sum(event.kind == "chain" for event in events), 2)
        self.assertEqual(next(event for event in events if event.kind == "chain").coords, chain.vanished)
        self.assertEqual(next(event for event in events if event.kind == "placement").board, placement_board)
        self.assertEqual(next(event for event in events if event.kind == "chain").board, chain_board)

    def test_active_pair_uses_field_columns_above_ojama_forecast(self):
        up = ACTION_TO_INDEX[PlacementAction(2, Direction.UP)]
        down = ACTION_TO_INDEX[PlacementAction(2, Direction.DOWN)]

        self.assertEqual(active_pair_cells(up), ((2, 13), (2, 14)))
        self.assertEqual(active_pair_cells(down), ((2, 14), (2, 13)))

    def test_winner_banner_uses_one_based_player_number(self):
        self.assertEqual(winner_banner_label("player_0"), "PLAYER 1 WINS")
        self.assertEqual(winner_banner_label("player_1"), "PLAYER 2 WINS")
        self.assertEqual(winner_banner_label(None), "DRAW")


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
