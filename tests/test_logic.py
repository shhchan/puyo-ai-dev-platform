import unittest
from src.core.field import Field
from src.core.puyo import Puyo
from src.core.constants import PuyoColor, GRID_HEIGHT

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

    def test_hidden_row_vanish(self):
        f = Field()
        # Place 4 Reds including High Y (Index 12)
        # (0, 12), (0, 11), (0, 10), (0, 9)
        p = Puyo(PuyoColor.RED)
        f.place_puyo(0, 12, p)
        f.place_puyo(0, 11, p)
        f.place_puyo(0, 10, p)
        f.place_puyo(0, 9, p)
        
        vanish = f.check_vanish()
        self.assertEqual(len(vanish), 4)
        self.assertIn((0, 12), vanish)

if __name__ == '__main__':
    unittest.main()
