import pygame

from ..core.constants import GRID_WIDTH, GRID_HEIGHT, PUYO_SIZE, PuyoColor, VISIBLE_HEIGHT


class Renderer:
    def __init__(self, screen):
        self.screen = screen
        self.font = pygame.font.SysFont("Arial", 20)
        self.small_font = pygame.font.SysFont("Arial", 16)
        self.large_font = pygame.font.SysFont("Arial", 64, bold=True)
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
        self.hidden_rows = GRID_HEIGHT - VISIBLE_HEIGHT
        self.field_inner_left = 56
        self.field_inner_top = 64
        self.field_width = GRID_WIDTH * PUYO_SIZE
        self.field_height = GRID_HEIGHT * PUYO_SIZE
        self.visible_top = self.field_inner_top + self.hidden_rows * PUYO_SIZE
        self.visible_height = VISIBLE_HEIGHT * PUYO_SIZE

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
        sy = self.field_inner_top + (GRID_HEIGHT - 1 - y) * PUYO_SIZE
        return sx, sy

    def _draw_puyo_rect(self, x, y, color):
        ix, iy = int(round(x)), int(round(y))
        pygame.draw.rect(self.screen, color, (ix, iy, PUYO_SIZE, PUYO_SIZE))
        pygame.draw.rect(self.screen, (18, 18, 18), (ix, iy, PUYO_SIZE, PUYO_SIZE), 1)

    def _draw_ghost_rect(self, x, y, color):
        # Centered 75% ghost marker.
        ghost_size = int(PUYO_SIZE * 0.75)
        offset = (PUYO_SIZE - ghost_size) // 2
        gx = int(round(x + offset))
        gy = int(round(y + offset))

        ghost = pygame.Surface((ghost_size, ghost_size), pygame.SRCALPHA)
        ghost.fill((color[0], color[1], color[2], 85))
        self.screen.blit(ghost, (gx, gy))
        pygame.draw.rect(self.screen, (color[0], color[1], color[2]), (gx, gy, ghost_size, ghost_size), 1)

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

        top_color = self.colors.get(pair[1].color, (255, 255, 255))
        bottom_color = self.colors.get(pair[0].color, (255, 255, 255))

        pygame.draw.rect(self.screen, top_color, (preview_x, top_y, preview_size, preview_size))
        pygame.draw.rect(self.screen, (18, 18, 18), (preview_x, top_y, preview_size, preview_size), 1)
        pygame.draw.rect(self.screen, bottom_color, (preview_x, bottom_y, preview_size, preview_size))
        pygame.draw.rect(self.screen, (18, 18, 18), (preview_x, bottom_y, preview_size, preview_size), 1)

    def _draw_field_background(self):
        hidden_rect = pygame.Rect(
            self.field_inner_left,
            self.field_inner_top,
            self.field_width,
            self.hidden_rows * PUYO_SIZE,
        )
        visible_rect = pygame.Rect(
            self.field_inner_left,
            self.visible_top,
            self.field_width,
            self.visible_height,
        )
        pygame.draw.rect(self.screen, (44, 50, 63), hidden_rect)
        pygame.draw.rect(self.screen, (24, 28, 36), visible_rect)
        pygame.draw.line(
            self.screen,
            (218, 120, 120),
            (self.field_inner_left, self.visible_top),
            (self.field_inner_left + self.field_width, self.visible_top),
            2,
        )

        hidden_label = self.small_font.render("OFFSCREEN (13-14)", True, (205, 160, 160))
        self.screen.blit(hidden_label, (self.field_inner_left + 8, self.field_inner_top + 6))

    def _draw_field_grid_lines(self):
        for x in range(GRID_WIDTH + 1):
            line_x = self.field_inner_left + x * PUYO_SIZE
            pygame.draw.line(
                self.screen,
                (40, 45, 56),
                (line_x, self.field_inner_top),
                (line_x, self.field_inner_top + self.field_height),
                1,
            )

        for y in range(GRID_HEIGHT + 1):
            line_y = self.field_inner_top + y * PUYO_SIZE
            pygame.draw.line(
                self.screen,
                (40, 45, 56),
                (self.field_inner_left, line_y),
                (self.field_inner_left + self.field_width, line_y),
                1,
            )

    def _draw_field_cells(self, game_state, skip_coords=None):
        skip_coords = skip_coords or set()
        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                if (x, y) in skip_coords:
                    continue
                puyo = game_state.field.get_puyo(x, y)
                if puyo.is_empty():
                    continue
                sx, sy = self._grid_to_screen(x, y)
                color = self.colors.get(puyo.color, (255, 255, 255))
                self._draw_puyo_rect(sx, sy, color)

    def _draw_drop_tween(self, game_state):
        # Draw static cells from the snapshot before drop.
        for x, y, color in game_state.drop_tween_static_cells:
            sx, sy = self._grid_to_screen(x, y)
            draw_color = self.colors.get(color, (255, 255, 255))
            self._draw_puyo_rect(sx, sy, draw_color)

        # Draw moving cells at interpolated positions.
        p = game_state.drop_tween_progress
        for x, from_y, to_y, color in game_state.drop_tween_motions:
            interp_y = from_y + (to_y - from_y) * p
            sx, sy = self._grid_to_screen(x, interp_y)
            draw_color = self.colors.get(color, (255, 255, 255))
            self._draw_puyo_rect(sx, sy, draw_color)

    def _draw_active_pair(self, game_state, fall_offset_px):
        if not (game_state.current_puyo_1 and game_state.current_puyo_2):
            return

        ghost_pos = game_state.get_ghost_axis_position()
        if ghost_pos is not None:
            ghost_x, ghost_y = ghost_pos
            ghost_color_1 = self.colors.get(game_state.current_puyo_1.color, (255, 255, 255))
            ghost_sx, ghost_sy = self._grid_to_screen(ghost_x, ghost_y)
            self._draw_ghost_rect(ghost_sx, ghost_sy, ghost_color_1)

            ox, oy = game_state.get_sub_puyo_offset(game_state.puyo_rot)
            ghost_sub_x, ghost_sub_y = ghost_x + ox, ghost_y + oy
            if 0 <= ghost_sub_y < GRID_HEIGHT:
                ghost_color_2 = self.colors.get(game_state.current_puyo_2.color, (255, 255, 255))
                ghost_sx2, ghost_sy2 = self._grid_to_screen(ghost_sub_x, ghost_sub_y)
                self._draw_ghost_rect(ghost_sx2, ghost_sy2, ghost_color_2)

        axis_x, axis_y = game_state.puyo_x, game_state.puyo_y
        if 0 <= axis_y < GRID_HEIGHT:
            sx, sy = self._grid_to_screen(axis_x, axis_y)
            sy += fall_offset_px
            color = self.colors.get(game_state.current_puyo_1.color, (255, 255, 255))
            self._draw_puyo_rect(sx, sy, color)

        ox, oy = game_state.get_sub_puyo_offset(game_state.puyo_rot)
        sub_x, sub_y = axis_x + ox, axis_y + oy
        if 0 <= sub_y < GRID_HEIGHT:
            sx, sy = self._grid_to_screen(sub_x, sub_y)
            sy += fall_offset_px
            color = self.colors.get(game_state.current_puyo_2.color, (255, 255, 255))
            self._draw_puyo_rect(sx, sy, color)

    def draw(self, game_state, fall_offset_px=0.0):
        self.screen.fill((36, 39, 46))

        pygame.draw.rect(self.screen, (33, 47, 72), self.field_outer_rect)
        pygame.draw.rect(self.screen, (180, 188, 205), self.field_outer_rect, 3)

        self._draw_field_background()

        if game_state.state == "animate" and game_state.animation_state == "drop_tween":
            self._draw_drop_tween(game_state)
        elif game_state.state == "animate" and game_state.animation_state == "vanish_flash":
            blink_on = int(game_state.animation_timer / 0.04) % 2 == 0
            skip_coords = set()
            if not blink_on:
                skip_coords = set(game_state.vanish_coords)
            self._draw_field_cells(game_state, skip_coords=skip_coords)
        else:
            self._draw_field_cells(game_state)

        if game_state.state == "control":
            self._draw_active_pair(game_state, fall_offset_px)

        self._draw_field_grid_lines()

        score_surface = self.font.render(f"Score: {game_state.score}", True, (242, 242, 242))
        self.screen.blit(score_surface, (self.field_outer_rect.x, 22))

        next_pairs = list(game_state.next_puyo_queue)
        next_pair = next_pairs[0] if len(next_pairs) > 0 else None
        next2_pair = next_pairs[1] if len(next_pairs) > 1 else None
        self._draw_preview_pair(next_pair, self.next_rect, "NEXT")
        self._draw_preview_pair(next2_pair, self.next2_rect, "NEXT2")

        if game_state.state == "ready":
            start_surface = self.small_font.render("PRESS SPACE / ENTER TO START", True, (240, 240, 240))
            x = self.field_outer_rect.x + (self.field_outer_rect.width - start_surface.get_width()) // 2
            y = self.visible_top + self.visible_height // 2 - 12
            self.screen.blit(start_surface, (x, y))

        if game_state.state == "countdown" and game_state.countdown_number is not None:
            num_surface = self.large_font.render(str(game_state.countdown_number), True, (250, 230, 130))
            x = self.field_outer_rect.x + (self.field_outer_rect.width - num_surface.get_width()) // 2
            y = self.visible_top + self.visible_height // 2 - num_surface.get_height() // 2
            self.screen.blit(num_surface, (x, y))

        if game_state.state == "gameover":
            game_over_surface = self.font.render("GAME OVER", True, (245, 120, 120))
            x = self.field_outer_rect.x + (self.field_outer_rect.width - game_over_surface.get_width()) // 2
            y = self.field_outer_rect.y - 30
            self.screen.blit(game_over_surface, (x, y))

        pygame.display.flip()
