import unittest

from src.core.constants import VANISH_FLASH_SECONDS, PuyoColor
from src.core.game import GameState
from src.core.puyo import Puyo


class TestScoring(unittest.TestCase):
    def _setup_vanish_flash(self, game, groups, chain_count=0):
        game.state = "animate"
        game.animation_state = "vanish_flash"
        game.animation_timer = 0.0
        game.chain_count = chain_count
        game.vanish_groups = [set(group) for group in groups]
        game.vanish_coords = set()
        for group in groups:
            for coord in group:
                game.vanish_coords.add(coord)

    def _place_group(self, game, coords, color):
        for x, y in coords:
            game.field.place_puyo(x, y, Puyo(color))

    def test_score_4_single_color_single_chain_is_40(self):
        game = GameState()
        group = {(0, 0), (1, 0), (0, 1), (1, 1)}
        self._place_group(game, group, PuyoColor.RED)
        self._setup_vanish_flash(game, [group], chain_count=0)

        game.advance_animation(VANISH_FLASH_SECONDS)

        self.assertEqual(game.score, 40)

    def test_score_5_single_color_single_chain_is_100(self):
        game = GameState()
        group = {(0, 0), (1, 0), (2, 0), (1, 1), (1, 2)}
        self._place_group(game, group, PuyoColor.RED)
        self._setup_vanish_flash(game, [group], chain_count=0)

        game.advance_animation(VANISH_FLASH_SECONDS)

        self.assertEqual(game.score, 100)

    def test_score_two_colors_same_chain_is_240(self):
        game = GameState()
        group_red = {(0, 0), (1, 0), (0, 1), (1, 1)}
        group_blue = {(4, 0), (5, 0), (4, 1), (5, 1)}
        self._place_group(game, group_red, PuyoColor.RED)
        self._place_group(game, group_blue, PuyoColor.BLUE)
        self._setup_vanish_flash(game, [group_red, group_blue], chain_count=0)

        game.advance_animation(VANISH_FLASH_SECONDS)

        self.assertEqual(game.score, 240)

    def test_second_chain_applies_chain_bonus_8(self):
        game = GameState()
        group = {(0, 0), (1, 0), (0, 1), (1, 1)}
        self._place_group(game, group, PuyoColor.RED)
        self._setup_vanish_flash(game, [group], chain_count=1)

        game.advance_animation(VANISH_FLASH_SECONDS)

        self.assertEqual(game.score, 320)

    def test_chain_bonus_caps_at_19_chain(self):
        game = GameState()
        group = {(0, 0), (1, 0), (0, 1), (1, 1)}
        self._place_group(game, group, PuyoColor.RED)
        self._setup_vanish_flash(game, [group], chain_count=30)

        game.advance_animation(VANISH_FLASH_SECONDS)

        self.assertEqual(game.score, 20480)


if __name__ == "__main__":
    unittest.main()
