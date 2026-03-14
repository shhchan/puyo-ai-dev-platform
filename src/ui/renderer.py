import pygame
from ..core.constants import GRID_WIDTH, PUYO_SIZE, PuyoColor, VISIBLE_HEIGHT


class Renderer:
    def __init__(self, screen):
        self.screen = screen
        self.font = pygame.font.SysFont("Arial", 20)
        self.small_font = pygame.font.SysFont("Arial", 16)
        self.colors = {
            PuyoColor.RED: (235, 74, 74),
            PuyoColor.BLUE: (65, 135, 245),
            PuyoColor.GREEN: (84, 224, 84),
            PuyoColor.YELLOW: (244, 220, 80),
            PuyoColor.PURPLE: (177, 96, 226),
            PuyoColor.OJAMA: (200, 200, 200),
            PuyoColor.WALL: (100, 100, 100),
            PuyoColor.EMPTY: (0, 0, 0),
        }

        self.field_border = 6
        self.field_inner_left = 56
        self.field_inner_top = 96
        self.field_width = GRID_WIDTH * PUYO_SIZE
        self.field_height = VISIBLE_HEIGHT * PUYO_SIZE
        self.field_outer_rect = pygame.Rect(
            self.field_inner_left - self.field_border,
            self.field_inner_top - self.field_border,
            self.field_width + self.field_border * 2,
            self.field_height + self.field_border * 2,
        )

        next_panel_left = self.field_outer_rect.right + 22
        self.next_rect = pygame.Rect(next_panel_left, 146, 104, 120)
        self.next2_rect = pygame.Rect(next_panel_left, 296, 104, 120)

    def _grid_to_screen(self, x, y):
        sx = self.field_inner_left + x * PUYO_SIZE
        sy = self.field_inner_top + (VISIBLE_HEIGHT - 1 - y) * PUYO_SIZE
        return sx, sy

    def _draw_puyo_rect(self, x, y, color):
        pygame.draw.rect(self.screen, color, (x, y, PUYO_SIZE, PUYO_SIZE))
        pygame.draw.rect(self.screen, (18, 18, 18), (x, y, PUYO_SIZE, PUYO_SIZE), 1)

    def _draw_preview_pair(self, pair, panel_rect, label):
        pygame.draw.rect(self.screen, (40, 40, 52), panel_rect)
        pygame.draw.rect(self.screen, (165, 170, 185), panel_rect, 2)

        label_surface = self.small_font.render(label, True, (235, 235, 235))
        label_x = panel_rect.x + (panel_rect.width - label_surface.get_width()) // 2
        self.screen.blit(label_surface, (label_x, panel_rect.y + 8))

        if pair is None:
            return

        preview_size = 22
        preview_x = panel_rect.x + (panel_rect.width - preview_size) // 2
        top_y = panel_rect.y + 42
        bottom_y = top_y + preview_size + 6

        top_color = self.colors.get(pair[0].color, (255, 255, 255))
        bottom_color = self.colors.get(pair[1].color, (255, 255, 255))

        pygame.draw.rect(self.screen, top_color, (preview_x, top_y, preview_size, preview_size))
        pygame.draw.rect(self.screen, (18, 18, 18), (preview_x, top_y, preview_size, preview_size), 1)
        pygame.draw.rect(self.screen, bottom_color, (preview_x, bottom_y, preview_size, preview_size))
        pygame.draw.rect(self.screen, (18, 18, 18), (preview_x, bottom_y, preview_size, preview_size), 1)

    def draw(self, game_state):
        self.screen.fill((36, 39, 46))

        pygame.draw.rect(self.screen, (33, 47, 72), self.field_outer_rect)
        pygame.draw.rect(self.screen, (180, 188, 205), self.field_outer_rect, 3)

        field_inner_rect = pygame.Rect(
            self.field_inner_left,
            self.field_inner_top,
            self.field_width,
            self.field_height,
        )
        pygame.draw.rect(self.screen, (24, 28, 36), field_inner_rect)

        for x in range(GRID_WIDTH + 1):
            line_x = self.field_inner_left + x * PUYO_SIZE
            pygame.draw.line(
                self.screen,
                (40, 45, 56),
                (line_x, self.field_inner_top),
                (line_x, self.field_inner_top + self.field_height),
                1,
            )

        for y in range(VISIBLE_HEIGHT + 1):
            line_y = self.field_inner_top + y * PUYO_SIZE
            pygame.draw.line(
                self.screen,
                (40, 45, 56),
                (self.field_inner_left, line_y),
                (self.field_inner_left + self.field_width, line_y),
                1,
            )

        for y in range(VISIBLE_HEIGHT):
            for x in range(GRID_WIDTH):
                puyo = game_state.field.get_puyo(x, y)
                if not puyo.is_empty():
                    sx, sy = self._grid_to_screen(x, y)
                    color = self.colors.get(puyo.color, (255, 255, 255))
                    self._draw_puyo_rect(sx, sy, color)

        if game_state.current_puyo_1 and game_state.current_puyo_2:
            axis_x, axis_y = game_state.puyo_x, game_state.puyo_y
            if 0 <= axis_y < VISIBLE_HEIGHT:
                sx, sy = self._grid_to_screen(axis_x, axis_y)
                color = self.colors.get(game_state.current_puyo_1.color, (255, 255, 255))
                self._draw_puyo_rect(sx, sy, color)

            ox, oy = game_state.get_sub_puyo_offset(game_state.puyo_rot)
            sub_x, sub_y = axis_x + ox, axis_y + oy
            if 0 <= sub_y < VISIBLE_HEIGHT:
                sx, sy = self._grid_to_screen(sub_x, sub_y)
                color = self.colors.get(game_state.current_puyo_2.color, (255, 255, 255))
                self._draw_puyo_rect(sx, sy, color)

        score_surface = self.font.render(f"Score: {game_state.score}", True, (242, 242, 242))
        self.screen.blit(score_surface, (self.field_outer_rect.x, 42))

        next_pairs = list(game_state.next_puyo_queue)
        next_pair = next_pairs[0] if len(next_pairs) > 0 else None
        next2_pair = next_pairs[1] if len(next_pairs) > 1 else None

        self._draw_preview_pair(next_pair, self.next_rect, "NEXT")
        self._draw_preview_pair(next2_pair, self.next2_rect, "NEXT2")

        if game_state.state == "gameover":
            game_over_surface = self.font.render("GAME OVER", True, (245, 120, 120))
            x = self.field_outer_rect.x + (self.field_outer_rect.width - game_over_surface.get_width()) // 2
            y = self.field_outer_rect.y - 30
            self.screen.blit(game_over_surface, (x, y))

        pygame.display.flip()
