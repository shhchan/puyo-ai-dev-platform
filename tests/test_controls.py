import unittest
from unittest.mock import patch

from src.core.constants import Action
from src.core.game import GameState

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
    def test_horizontal_has_priority_over_down_when_move_succeeds(self):
        game = GameState()
        start_x = game.puyo_x
        start_y = game.puyo_y

        game.update([Action.LEFT, Action.DOWN])

        self.assertEqual(game.puyo_x, start_x - 1)
        self.assertEqual(game.puyo_y, start_y)

    def test_down_executes_if_horizontal_is_blocked(self):
        game = GameState()
        game.puyo_x = 0
        start_y = game.puyo_y

        game.update([Action.LEFT, Action.DOWN])

        self.assertEqual(game.puyo_x, 0)
        self.assertEqual(game.puyo_y, start_y - 1)

    def test_left_and_right_cancel_each_other(self):
        game = GameState()
        start_x = game.puyo_x

        game.update([Action.LEFT, Action.RIGHT])

        self.assertEqual(game.puyo_x, start_x)


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


if __name__ == "__main__":
    unittest.main()
