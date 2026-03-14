import unittest
from unittest.mock import patch

from src.core.constants import (
    Action,
    Direction,
    PuyoColor,
    LOCK_CONTACT_LIMIT,
    LOCK_FRAME_LIMIT,
    COUNTDOWN_SECONDS,
    VANISH_FLASH_SECONDS,
    CHAIN_DROP_TWEEN_SECONDS,
)
from src.core.game import GameState
from src.core.puyo import Puyo

try:
    import pygame
except ModuleNotFoundError:
    pygame = None

try:
    if pygame is not None:
        from src.input_handler import InputHandler
        INPUT_HANDLER_AVAILABLE = True
    else:
        InputHandler = None
        INPUT_HANDLER_AVAILABLE = False
except ModuleNotFoundError:
    InputHandler = None
    INPUT_HANDLER_AVAILABLE = False


class FakePressed:
    def __init__(self, active_keys=None):
        self.active_keys = set(active_keys or [])

    def __getitem__(self, key):
        return key in self.active_keys


class TestControlPriority(unittest.TestCase):
    def _create_control_game(self):
        game = GameState()
        game.spawn_puyo()
        return game

    def test_horizontal_has_priority_over_down_when_move_succeeds(self):
        game = self._create_control_game()
        start_x = game.puyo_x
        start_y = game.puyo_y

        game.update([Action.LEFT, Action.DOWN])

        self.assertEqual(game.puyo_x, start_x - 1)
        self.assertEqual(game.puyo_y, start_y)

    def test_down_executes_if_horizontal_is_blocked(self):
        game = self._create_control_game()
        game.puyo_x = 0
        start_y = game.puyo_y

        game.update([Action.LEFT, Action.DOWN])

        self.assertEqual(game.puyo_x, 0)
        self.assertEqual(game.puyo_y, start_y - 1)

    def test_left_and_right_cancel_each_other(self):
        game = self._create_control_game()
        start_x = game.puyo_x

        game.update([Action.LEFT, Action.RIGHT])

        self.assertEqual(game.puyo_x, start_x)

    def test_locks_after_32_ground_frames(self):
        game = self._create_control_game()
        game.puyo_x = 2
        game.puyo_y = 0
        game.puyo_rot = Direction.UP

        for _ in range(LOCK_FRAME_LIMIT - 1):
            game.update([])
            self.assertEqual(game.state, "control")

        game.update([])
        self.assertEqual(game.state, "animate")

    def test_locks_after_8_ground_contacts(self):
        game = self._create_control_game()
        game.puyo_x = 2
        game.puyo_rot = Direction.UP

        for _ in range(LOCK_CONTACT_LIMIT - 1):
            game.puyo_y = 0
            game.update([])  # grounded transition
            self.assertEqual(game.state, "control")

            game.puyo_y = 1
            game.update([])  # ungrounded frame
            self.assertEqual(game.state, "control")

        game.puyo_y = 0
        game.update([])
        self.assertEqual(game.state, "animate")

    def test_floor_kick_lifts_when_axis_below_is_blocked(self):
        game = self._create_control_game()
        game.puyo_x = 2
        game.puyo_y = 1
        game.puyo_rot = Direction.RIGHT
        game.field.place_puyo(2, 0, Puyo(PuyoColor.RED))

        game.rotate(True)

        self.assertEqual(game.puyo_rot, Direction.DOWN)
        self.assertEqual(game.puyo_y, 2)

    def test_floor_kick_does_not_lift_when_axis_below_is_free(self):
        game = self._create_control_game()
        game.puyo_x = 2
        game.puyo_y = 1
        game.puyo_rot = Direction.RIGHT

        game.rotate(True)

        self.assertEqual(game.puyo_rot, Direction.DOWN)
        self.assertEqual(game.puyo_y, 1)

    def test_horizontal_sweep_blocks_overstep_while_interpolating(self):
        game = self._create_control_game()
        game.puyo_x = 2
        game.puyo_y = 3
        game.puyo_rot = Direction.UP
        game.field.place_puyo(1, 2, Puyo(PuyoColor.BLUE))

        game.set_vertical_interpolation(0.6)
        game.update([Action.LEFT])

        self.assertEqual(game.puyo_x, 2)

    def test_double_rotate_becomes_180_when_vertical_and_sides_blocked(self):
        game = self._create_control_game()
        game.puyo_x = 2
        game.puyo_y = 5
        game.puyo_rot = Direction.UP
        game.field.place_puyo(1, 5, Puyo(PuyoColor.RED))
        game.field.place_puyo(1, 6, Puyo(PuyoColor.RED))
        game.field.place_puyo(3, 5, Puyo(PuyoColor.RED))
        game.field.place_puyo(3, 6, Puyo(PuyoColor.RED))

        game.update([Action.ROTATE_RIGHT])
        self.assertEqual(game.puyo_rot, Direction.UP)
        game.update([Action.ROTATE_LEFT])
        self.assertEqual(game.puyo_rot, Direction.DOWN)

    def test_double_rotate_does_not_force_180_when_sides_not_blocked(self):
        game = self._create_control_game()
        game.puyo_rot = Direction.UP

        game.update([Action.ROTATE_RIGHT])
        self.assertEqual(game.puyo_rot, Direction.RIGHT)
        game.update([Action.ROTATE_LEFT])
        self.assertEqual(game.puyo_rot, Direction.UP)

    def test_axis_cannot_enter_row14_while_child_can(self):
        game = self._create_control_game()
        self.assertTrue(game.can_place_pair(2, 12, Direction.UP))
        self.assertFalse(game.can_place_pair(2, 13, Direction.UP))

    def test_initial_state_is_ready_and_has_no_active_pair(self):
        game = GameState()
        self.assertEqual(game.state, "ready")
        self.assertIsNone(game.current_puyo_1)
        self.assertIsNone(game.current_puyo_2)
        self.assertGreaterEqual(len(game.next_puyo_queue), 2)

    def test_start_action_transitions_to_countdown(self):
        game = GameState()
        game.update([Action.START])

        self.assertEqual(game.state, "countdown")
        self.assertEqual(game.countdown_number, 3)

    def test_countdown_spawns_from_next_queue(self):
        game = GameState()
        first_pair = game.next_puyo_queue[0]
        second_pair = game.next_puyo_queue[1]

        game.update([Action.START])
        game.advance_countdown(COUNTDOWN_SECONDS)

        self.assertEqual(game.state, "control")
        self.assertIs(game.current_puyo_1, first_pair[0])
        self.assertIs(game.current_puyo_2, first_pair[1])
        self.assertIs(game.next_puyo_queue[0][0], second_pair[0])
        self.assertIs(game.next_puyo_queue[0][1], second_pair[1])
        self.assertGreaterEqual(len(game.next_puyo_queue), 2)

    def test_game_over_triggers_when_choke_point_is_filled(self):
        game = GameState()
        game.field.place_puyo(2, 11, Puyo(PuyoColor.RED))

        game.spawn_puyo()

        self.assertEqual(game.state, "gameover")
        self.assertTrue(game.game_over)

    def test_row_13_fill_alone_does_not_trigger_game_over(self):
        game = GameState()
        game.field.place_puyo(2, 12, Puyo(PuyoColor.BLUE))

        game.spawn_puyo()

        self.assertEqual(game.state, "control")
        self.assertFalse(game.game_over)

    def test_animate_vanish_flash_transitions_to_resolve(self):
        game = GameState()
        game.state = "animate"
        game.animation_state = "resolve"
        p = Puyo(PuyoColor.RED)
        game.field.place_puyo(0, 0, p)
        game.field.place_puyo(1, 0, p)
        game.field.place_puyo(0, 1, p)
        game.field.place_puyo(1, 1, p)

        game.advance_animation(0.0)
        self.assertEqual(game.animation_state, "vanish_flash")
        game.advance_animation(VANISH_FLASH_SECONDS)
        self.assertEqual(game.animation_state, "resolve")

    def test_animate_drop_tween_transitions_to_resolve(self):
        game = GameState()
        game.state = "animate"
        game.animation_state = "resolve"
        game.field.place_puyo(0, 3, Puyo(PuyoColor.BLUE))

        game.advance_animation(0.0)
        self.assertEqual(game.animation_state, "drop_tween")
        self.assertGreater(len(game.drop_tween_motions), 0)
        game.advance_animation(CHAIN_DROP_TWEEN_SECONDS)
        self.assertEqual(game.animation_state, "resolve")


@unittest.skipUnless(INPUT_HANDLER_AVAILABLE, "pygame is not installed")
class TestInputHandler(unittest.TestCase):
    def _run_frame(self, handler, now, active_keys, events):
        with patch("src.input_handler.time.time", return_value=now), patch(
            "src.input_handler.pygame.event.get", return_value=events
        ), patch(
            "src.input_handler.pygame.key.get_pressed",
            return_value=FakePressed(active_keys),
        ):
            return handler.process_input()

    def test_das_repeats_after_initial_delay(self):
        handler = InputHandler()
        base_time = 100.0

        actions = self._run_frame(
            handler,
            base_time,
            [pygame.K_a],
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a)],
        )
        self.assertIn(Action.LEFT, actions)

        actions = self._run_frame(handler, base_time + 0.10, [pygame.K_a], [])
        self.assertNotIn(Action.LEFT, actions)

        actions = self._run_frame(handler, base_time + 0.16, [pygame.K_a], [])
        self.assertIn(Action.LEFT, actions)

        actions = self._run_frame(handler, base_time + 0.19, [pygame.K_a], [])
        self.assertNotIn(Action.LEFT, actions)

        actions = self._run_frame(handler, base_time + 0.20, [pygame.K_a], [])
        self.assertIn(Action.LEFT, actions)

    def test_soft_drop_repeats_while_holding(self):
        handler = InputHandler()
        base_time = 200.0

        actions = self._run_frame(
            handler,
            base_time,
            [pygame.K_s],
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s)],
        )
        self.assertIn(Action.DOWN, actions)

        actions = self._run_frame(handler, base_time + 0.05, [pygame.K_s], [])
        self.assertNotIn(Action.DOWN, actions)

        actions = self._run_frame(handler, base_time + 0.081, [pygame.K_s], [])
        self.assertIn(Action.DOWN, actions)

    def test_left_right_cancel_but_down_still_works(self):
        handler = InputHandler()
        actions = self._run_frame(
            handler,
            300.0,
            [pygame.K_a, pygame.K_d, pygame.K_s],
            [
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a),
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d),
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s),
            ],
        )

        self.assertNotIn(Action.LEFT, actions)
        self.assertNotIn(Action.RIGHT, actions)
        self.assertIn(Action.DOWN, actions)

    def test_start_key_emits_start_action(self):
        handler = InputHandler()
        actions = self._run_frame(
            handler,
            301.0,
            [pygame.K_SPACE],
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)],
        )
        self.assertIn(Action.START, actions)


if __name__ == "__main__":
    unittest.main()
