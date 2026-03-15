import unittest

from src.core.constants import PUYO_SIZE

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


if __name__ == "__main__":
    unittest.main()
