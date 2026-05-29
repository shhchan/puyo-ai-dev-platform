import unittest

from src.core.constants import VANISH_FLASH_SECONDS, PuyoColor, Action
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
        game.chain_display_a, game.chain_display_b, game.chain_display_score = game._calculate_chain_score_components()

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

    def test_score_display_is_zero_padded_when_not_chaining(self):
        game = GameState()
        game.score = 123
        self.assertEqual(game.get_score_display_text(), "00000123")

    def test_score_display_shows_axb_during_vanish_flash(self):
        game = GameState()
        group = {(0, 0), (1, 0), (0, 1), (1, 1)}
        self._place_group(game, group, PuyoColor.RED)
        self._setup_vanish_flash(game, [group], chain_count=0)

        self.assertEqual(game.get_score_display_text(), "  40x  1")

    def test_soft_drop_bonus_not_added_without_soft_drop(self):
        game = GameState()
        game.spawn_puyo()

        game.lock_puyo()

        self.assertEqual(game.score, 0)

    def test_soft_drop_bonus_adds_cells_plus_landing_point(self):
        game = GameState()
        game.spawn_puyo()
        start_y = game.puyo_y

        game.update([Action.DOWN])
        self.assertEqual(game.score, 1)
        game.update([Action.DOWN])
        self.assertEqual(game.score, 2)
        self.assertEqual(game.puyo_y, start_y - 2)

        game.lock_puyo()

        self.assertEqual(game.score, 3)


if __name__ == "__main__":
    unittest.main()
