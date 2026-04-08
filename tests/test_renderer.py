import unittest

from src.core.constants import PUYO_SIZE, PuyoColor, Direction, GRID_HEIGHT
from src.core.game import GameState
from src.core.puyo import Puyo

try:
    import pygame
    from src.ui.renderer import Renderer

    RENDERER_AVAILABLE = True
except ModuleNotFoundError:
    pygame = None
    Renderer = None
    RENDERER_AVAILABLE = False


@unittest.skipUnless(RENDERER_AVAILABLE, "pygame is not installed")
class TestRendererVisibility(unittest.TestCase):
    def setUp(self):
        pygame.init()
        self.screen = pygame.Surface((400, 600))

    def tearDown(self):
        pygame.quit()

    def test_non_debug_top_hidden_row_becomes_visible_at_half_cell(self):
        renderer = Renderer(self.screen, debug_mode=False)
        self.assertFalse(renderer._is_active_cell_visible(12, 0))
        self.assertTrue(renderer._is_active_cell_visible(12, PUYO_SIZE * 0.5))

    def test_connection_does_not_span_visible_and_hidden_rows(self):
        renderer = Renderer(self.screen, debug_mode=True)
        game = GameState()
        game.field.place_puyo(1, 11, Puyo(PuyoColor.RED))
        game.field.place_puyo(1, 12, Puyo(PuyoColor.RED))

        self.assertFalse(renderer._can_connect_puyo(game, 1, 11, 0, 1, PuyoColor.RED))

    def test_adjacent_same_color_cells_draw_bridge_between_centers(self):
        renderer = Renderer(self.screen, debug_mode=False)
        game = GameState()
        game.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
        game.field.place_puyo(1, 0, Puyo(PuyoColor.RED))

        renderer._draw_field_background()
        renderer._draw_field_cells(game)

        sx, sy = renderer._grid_to_screen(0, 0)
        bridge_x = int(round(sx + PUYO_SIZE))
        bridge_y = int(round(sy + PUYO_SIZE / 2))
        px = self.screen.get_at((bridge_x, bridge_y))[:3]

        self.assertNotEqual(px, (24, 28, 36))

    def test_next_preview_uses_round_puyo_not_square_corner_fill(self):
        renderer = Renderer(self.screen, debug_mode=False)
        pair = (Puyo(PuyoColor.RED), Puyo(PuyoColor.BLUE))
        renderer._draw_preview_pair(pair, renderer.next_rect, "NEXT")

        preview_size = 22
        preview_x = renderer.next_rect.x + (renderer.next_rect.width - preview_size) // 2
        top_y = renderer.next_rect.y + 42
        center_x = preview_x + preview_size // 2
        center_y = top_y + preview_size // 2

        corner_color = self.screen.get_at((preview_x, top_y))[:3]
        center_color = self.screen.get_at((center_x, center_y))[:3]

        self.assertEqual(corner_color, (40, 40, 52))
        self.assertNotEqual(center_color, (40, 40, 52))

    def test_active_sub_overflow_drawability_is_debug_only(self):
        debug_renderer = Renderer(self.screen, debug_mode=True)
        normal_renderer = Renderer(self.screen, debug_mode=False)

        self.assertTrue(debug_renderer._is_active_sub_drawable(GRID_HEIGHT, 0))
        self.assertFalse(normal_renderer._is_active_sub_drawable(GRID_HEIGHT, 0))

    def test_debug_mode_draws_active_sub_overflow_above_row14(self):
        renderer = Renderer(self.screen, debug_mode=True)
        game = GameState()
        game.spawn_puyo()
        game.state = "control"
        game.current_puyo_1 = Puyo(PuyoColor.RED)
        game.current_puyo_2 = Puyo(PuyoColor.BLUE)
        game.puyo_x = 2
        game.puyo_y = 13
        game.puyo_rot = Direction.UP

        self.screen.fill((1, 2, 3))
        renderer._draw_active_pair(game, fall_offset_px=0)

        sx, sy = renderer._grid_to_screen(2, GRID_HEIGHT)
        cx = int(round(sx + PUYO_SIZE / 2))
        cy = int(round(sy + PUYO_SIZE / 2))
        self.assertNotEqual(self.screen.get_at((cx, cy))[:3], (1, 2, 3))


if __name__ == "__main__":
    unittest.main()
