import unittest
from src.core.field import Field
from src.core.game import GameState
from src.core.puyo import Puyo
from src.core.constants import PuyoColor, Direction

class TestPuyoLogic(unittest.TestCase):
    def test_gravity(self):
        f = Field()
        # Place Puyo at Y=2 (Middleish)
        p1 = Puyo(PuyoColor.RED)
        f.place_puyo(0, 2, p1)
        
        # Verify it's there
        self.assertEqual(f.get_puyo(0, 2).color, PuyoColor.RED)
        self.assertTrue(f.get_puyo(0, 0).is_empty())
        
        # Drop
        f.drop_puyo()
        
        # Should fall to Y=0 (Bottom)
        self.assertEqual(f.get_puyo(0, 0).color, PuyoColor.RED)
        self.assertTrue(f.get_puyo(0, 2).is_empty())

    def test_stacking(self):
        f = Field()
        p1 = Puyo(PuyoColor.RED)
        p2 = Puyo(PuyoColor.BLUE)
        f.place_puyo(0, 0, p1) # Already at bottom
        f.place_puyo(0, 2, p2) # Above
        
        f.drop_puyo()
        
        # p1 should stay at 0
        self.assertEqual(f.get_puyo(0, 0).color, PuyoColor.RED)
        # p2 should fall to 1
        self.assertEqual(f.get_puyo(0, 1).color, PuyoColor.BLUE)

    def test_vanish(self):
        f = Field()
        # Create 4 Reds connected
        # (0,0), (1,0), (0,1), (0,2) - L shape
        p = Puyo(PuyoColor.RED)
        f.place_puyo(0, 0, p)
        f.place_puyo(1, 0, p)
        f.place_puyo(0, 1, p)
        f.place_puyo(0, 2, p)
        
        vanish = f.check_vanish()
        self.assertEqual(len(vanish), 4)
        
        f.remove_puyos(vanish)
        self.assertTrue(f.get_puyo(0, 0).is_empty())

    def test_hidden_row_is_not_vanish_target(self):
        f = Field()
        # Visible rows are 0..11. Row 12 (13th row) must not be vanish target.
        # (0, 12), (0, 11), (0, 10), (0, 9) would be 4-connect only if row 12 is counted.
        p = Puyo(PuyoColor.RED)
        f.place_puyo(0, 12, p)
        f.place_puyo(0, 11, p)
        f.place_puyo(0, 10, p)
        f.place_puyo(0, 9, p)
        
        vanish = f.check_vanish()
        self.assertEqual(len(vanish), 0)

    def test_row14_puyo_does_not_fall(self):
        f = Field()
        top = Puyo(PuyoColor.RED)
        floating = Puyo(PuyoColor.BLUE)
        f.place_puyo(0, 13, top)
        f.place_puyo(0, 3, floating)

        f.drop_puyo()

        self.assertEqual(f.get_puyo(0, 13).color, PuyoColor.RED)
        self.assertEqual(f.get_puyo(0, 0).color, PuyoColor.BLUE)


class TestGhostHighlight(unittest.TestCase):
    def _create_control_game(self):
        game = GameState()
        game.spawn_puyo()
        return game

    def test_ghost_highlight_marks_existing_puyos_when_next_drop_will_clear(self):
        game = self._create_control_game()
        game.current_puyo_1 = Puyo(PuyoColor.RED)
        game.current_puyo_2 = Puyo(PuyoColor.RED)
        game.puyo_x = 1
        game.puyo_y = 5
        game.puyo_rot = Direction.UP

        game.field.place_puyo(1, 0, Puyo(PuyoColor.RED))
        game.field.place_puyo(2, 0, Puyo(PuyoColor.RED))

        highlight = game.get_ghost_highlight_coords()

        self.assertEqual(highlight, {(1, 0), (2, 0)})

    def test_ghost_highlight_ignores_groups_that_only_form_in_hidden_rows(self):
        game = self._create_control_game()
        game.current_puyo_1 = Puyo(PuyoColor.RED)
        game.current_puyo_2 = Puyo(PuyoColor.RED)
        game.puyo_x = 1
        game.puyo_y = 13
        game.puyo_rot = Direction.UP

        game.field.place_puyo(1, 9, Puyo(PuyoColor.RED))
        game.field.place_puyo(1, 10, Puyo(PuyoColor.RED))
        game.field.place_puyo(1, 11, Puyo(PuyoColor.RED))

        highlight = game.get_ghost_highlight_coords()

        self.assertEqual(highlight, set())

    def test_ghost_cells_settle_after_pair_splits_on_uneven_stack(self):
        game = self._create_control_game()
        game.current_puyo_1 = Puyo(PuyoColor.RED)
        game.current_puyo_2 = Puyo(PuyoColor.BLUE)
        game.puyo_x = 1
        game.puyo_y = 8
        game.puyo_rot = Direction.RIGHT

        for y in range(3):
            game.field.place_puyo(1, y, Puyo(PuyoColor.YELLOW))

        ghost_cells = {(x, y, color) for x, y, color in game.get_ghost_cells()}

        self.assertEqual(
            ghost_cells,
            {
                (1, 3, PuyoColor.RED),
                (2, 0, PuyoColor.BLUE),
            },
        )

    def test_placement_preview_cells_use_the_same_split_landing(self):
        game = self._create_control_game()
        for y in range(3):
            game.field.place_puyo(1, y, Puyo(PuyoColor.YELLOW))

        cells = set(
            game.get_landing_cells(
                1,
                Direction.RIGHT,
                (PuyoColor.RED, PuyoColor.BLUE),
            )
        )

        self.assertEqual(
            cells,
            {
                (1, 3, PuyoColor.RED),
                (2, 0, PuyoColor.BLUE),
            },
        )


if __name__ == '__main__':
    unittest.main()
