import pygame

from ..core.constants import (
    GRID_WIDTH,
    GRID_HEIGHT,
    PUYO_SIZE,
    PuyoColor,
    VISIBLE_HEIGHT,
    VANISH_BLINK_INTERVAL_SECONDS,
)


class Renderer:
    def __init__(self, screen, debug_mode=False):
        self.screen = screen
        self.debug_mode = debug_mode
        self.font = pygame.font.SysFont("Arial", 20)
        self.small_font = pygame.font.SysFont("Arial", 16)
        self.score_font = pygame.font.SysFont("Consolas", 24, bold=True)
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
        self.draw_start_y = 0
        self.draw_row_count = GRID_HEIGHT if self.debug_mode else VISIBLE_HEIGHT
        self.draw_max_y = self.draw_start_y + self.draw_row_count - 1
        self.field_inner_left = 56
        self.field_inner_top = 64
        self.field_width = GRID_WIDTH * PUYO_SIZE
        self.field_height = self.draw_row_count * PUYO_SIZE
        self.visible_top = self.field_inner_top + (self.hidden_rows * PUYO_SIZE if self.debug_mode else 0)
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
        self.score_rect = pygame.Rect(
            self.field_outer_rect.x,
            self.field_outer_rect.bottom + 12,
            self.field_outer_rect.width,
            34,
        )

    def _grid_to_screen(self, x, y):
        sx = self.field_inner_left + x * PUYO_SIZE
        sy = self.field_inner_top + (self.draw_max_y - y) * PUYO_SIZE
        return sx, sy

    def _is_y_drawable(self, y):
        return self.draw_start_y <= y <= self.draw_max_y

    def _is_active_cell_visible(self, y, fall_offset_px):
        sx, sy = self._grid_to_screen(0, y)
        draw_top = sy + fall_offset_px
        draw_bottom = draw_top + PUYO_SIZE
        field_top = self.field_inner_top
        field_bottom = self.field_inner_top + self.field_height
        return draw_bottom > field_top and draw_top < field_bottom

    def _is_active_sub_drawable(self, y, fall_offset_px):
        if 0 <= y < GRID_HEIGHT and self._is_active_cell_visible(y, fall_offset_px):
            return True
        return self.debug_mode and y == GRID_HEIGHT

    def _draw_puyo_rect(self, x, y, color):
        ix, iy = int(round(x)), int(round(y))
        pygame.draw.rect(self.screen, color, (ix, iy, PUYO_SIZE, PUYO_SIZE))
        pygame.draw.rect(self.screen, (18, 18, 18), (ix, iy, PUYO_SIZE, PUYO_SIZE), 1)

    def _draw_puyo_blob(self, x, y, color, highlight_factor=1.0):
        ix, iy = int(round(x)), int(round(y))
        cx, cy = ix + PUYO_SIZE // 2, iy + PUYO_SIZE // 2
        radius = max(9, int(round(PUYO_SIZE * 0.34)))

        draw_color = self._scaled_color(color, highlight_factor)
        shadow_color = self._scaled_color(draw_color, 0.7)
        shine_color = self._scaled_color(draw_color, 1.25)

        pygame.draw.circle(self.screen, draw_color, (cx, cy), radius)
        pygame.draw.circle(self.screen, (20, 20, 24), (cx, cy), radius, 1)
        shine_radius = max(3, radius // 3)
        pygame.draw.circle(self.screen, shine_color, (cx - shine_radius, cy - shine_radius), shine_radius)
        pygame.draw.circle(self.screen, shadow_color, (cx + radius // 3, cy + radius // 3), max(2, radius // 4))

    def _draw_bridge_between(self, cx1, cy1, cx2, cy2, color, highlight_factor=1.0):
        bridge_color = self._scaled_color(color, highlight_factor)
        bridge_width = max(10, int(round(PUYO_SIZE * 0.34)))
        pygame.draw.line(self.screen, bridge_color, (cx1, cy1), (cx2, cy2), bridge_width)

    def _scaled_color(self, color, factor):
        return (
            max(0, min(255, int(round(color[0] * factor)))),
            max(0, min(255, int(round(color[1] * factor)))),
            max(0, min(255, int(round(color[2] * factor)))),
        )

    def _can_connect_puyo(self, game_state, x, y, dx, dy, color):
        nx, ny = x + dx, y + dy
        if not (0 <= x < GRID_WIDTH and 0 <= y < VISIBLE_HEIGHT):
            return False
        if not (0 <= nx < GRID_WIDTH and 0 <= ny < VISIBLE_HEIGHT):
            return False
        target = game_state.field.get_puyo(nx, ny)
        return target.is_color_puyo() and target.color == color

    def _draw_connected_field_puyo(self, game_state, x, y, color, highlight_factor=1.0):
        sx, sy = self._grid_to_screen(x, y)
        self._draw_puyo_blob(sx, sy, color, highlight_factor=highlight_factor)

    def _draw_ghost_rect(self, x, y, color):
        ghost_size = int(PUYO_SIZE * 0.68)
        offset = (PUYO_SIZE - ghost_size) // 2
        gx = int(round(x + offset))
        gy = int(round(y + offset))

        surface = pygame.Surface((ghost_size, ghost_size), pygame.SRCALPHA)
        center = ghost_size // 2
        radius = max(6, ghost_size // 2 - 1)
        pygame.draw.circle(surface, (color[0], color[1], color[2], 95), (center, center), radius)
        pygame.draw.circle(surface, (color[0], color[1], color[2], 170), (center, center), radius, 1)
        self.screen.blit(surface, (gx, gy))

    def _draw_preview_blob(self, x, y, size, color):
        cx = int(round(x + size / 2))
        cy = int(round(y + size / 2))
        radius = max(6, size // 2 - 1)
        pygame.draw.circle(self.screen, color, (cx, cy), radius)
        pygame.draw.circle(self.screen, (18, 18, 18), (cx, cy), radius, 1)
        shine_radius = max(2, radius // 3)
        shine = self._scaled_color(color, 1.2)
        pygame.draw.circle(self.screen, shine, (cx - shine_radius, cy - shine_radius), shine_radius)

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

        self._draw_preview_blob(preview_x, top_y, preview_size, top_color)
        self._draw_preview_blob(preview_x, bottom_y, preview_size, bottom_color)

    def _draw_field_background(self):
        if self.debug_mode:
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
            return

        visible_rect = pygame.Rect(
            self.field_inner_left,
            self.field_inner_top,
            self.field_width,
            self.field_height,
        )
        pygame.draw.rect(self.screen, (24, 28, 36), visible_rect)

    def _draw_field_grid_lines(self):
        if not self.debug_mode:
            return
        for x in range(GRID_WIDTH + 1):
            line_x = self.field_inner_left + x * PUYO_SIZE
            pygame.draw.line(
                self.screen,
                (40, 45, 56),
                (line_x, self.field_inner_top),
                (line_x, self.field_inner_top + self.field_height),
                1,
            )

        for y in range(self.draw_row_count + 1):
            line_y = self.field_inner_top + y * PUYO_SIZE
            pygame.draw.line(
                self.screen,
                (40, 45, 56),
                (self.field_inner_left, line_y),
                (self.field_inner_left + self.field_width, line_y),
                1,
            )

    def _draw_field_cells(self, game_state, skip_coords=None, highlight_coords=None, highlight_factor=1.0):
        skip_coords = skip_coords or set()
        highlight_coords = highlight_coords or set()
        color_cells = []
        color_factor_map = {}

        for y in range(self.draw_start_y, self.draw_start_y + self.draw_row_count):
            for x in range(GRID_WIDTH):
                if (x, y) in skip_coords:
                    continue
                puyo = game_state.field.get_puyo(x, y)
                if puyo.is_empty():
                    continue
                sx, sy = self._grid_to_screen(x, y)
                color = self.colors.get(puyo.color, (255, 255, 255))
                cell_factor = highlight_factor if (x, y) in highlight_coords else 1.0
                if puyo.is_color_puyo():
                    color_cells.append((x, y, puyo.color))
                    color_factor_map[(x, y)] = cell_factor
                else:
                    self._draw_puyo_rect(sx, sy, self._scaled_color(color, cell_factor))

        # Draw bridge only once per pair (right/up) for contiguous look.
        for x, y, puyo_color in color_cells:
            sx, sy = self._grid_to_screen(x, y)
            cx = int(round(sx + PUYO_SIZE / 2))
            cy = int(round(sy + PUYO_SIZE / 2))
            draw_color = self.colors.get(puyo_color, (255, 255, 255))

            if self._can_connect_puyo(game_state, x, y, 1, 0, puyo_color):
                nsx, nsy = self._grid_to_screen(x + 1, y)
                ncx = int(round(nsx + PUYO_SIZE / 2))
                ncy = int(round(nsy + PUYO_SIZE / 2))
                pair_factor = max(color_factor_map.get((x, y), 1.0), color_factor_map.get((x + 1, y), 1.0))
                self._draw_bridge_between(cx, cy, ncx, ncy, draw_color, highlight_factor=pair_factor)

            if self._can_connect_puyo(game_state, x, y, 0, 1, puyo_color):
                nsx, nsy = self._grid_to_screen(x, y + 1)
                ncx = int(round(nsx + PUYO_SIZE / 2))
                ncy = int(round(nsy + PUYO_SIZE / 2))
                pair_factor = max(color_factor_map.get((x, y), 1.0), color_factor_map.get((x, y + 1), 1.0))
                self._draw_bridge_between(cx, cy, ncx, ncy, draw_color, highlight_factor=pair_factor)

        for x, y, puyo_color in color_cells:
            cell_factor = color_factor_map.get((x, y), 1.0)
            draw_color = self.colors.get(puyo_color, (255, 255, 255))
            self._draw_connected_field_puyo(game_state, x, y, draw_color, highlight_factor=cell_factor)

    def _draw_drop_tween(self, game_state):
        # Draw static cells from the snapshot before drop.
        for x, y, color in game_state.drop_tween_static_cells:
            if not self._is_y_drawable(y):
                continue
            sx, sy = self._grid_to_screen(x, y)
            draw_color = self.colors.get(color, (255, 255, 255))
            self._draw_puyo_blob(sx, sy, draw_color)

        # Draw moving cells at interpolated positions.
        p = game_state.drop_tween_progress
        for x, from_y, to_y, color in game_state.drop_tween_motions:
            interp_y = from_y + (to_y - from_y) * p
            if not self._is_y_drawable(interp_y):
                continue
            sx, sy = self._grid_to_screen(x, interp_y)
            draw_color = self.colors.get(color, (255, 255, 255))
            self._draw_puyo_blob(sx, sy, draw_color)

    def _draw_active_pair(self, game_state, fall_offset_px):
        if not (game_state.current_puyo_1 and game_state.current_puyo_2):
            return

        for ghost_x, ghost_y, ghost_color in game_state.get_ghost_cells():
            if self._is_y_drawable(ghost_y):
                ghost_sx, ghost_sy = self._grid_to_screen(ghost_x, ghost_y)
                self._draw_ghost_rect(ghost_sx, ghost_sy, self.colors.get(ghost_color, (255, 255, 255)))

        axis_x, axis_y = game_state.puyo_x, game_state.puyo_y
        if 0 <= axis_y < GRID_HEIGHT and self._is_active_cell_visible(axis_y, fall_offset_px):
            sx, sy = self._grid_to_screen(axis_x, axis_y)
            sy += fall_offset_px
            color = self.colors.get(game_state.current_puyo_1.color, (255, 255, 255))
            self._draw_puyo_blob(sx, sy, color)

        ox, oy = game_state.get_sub_puyo_offset(game_state.puyo_rot)
        sub_x, sub_y = axis_x + ox, axis_y + oy
        if self._is_active_sub_drawable(sub_y, fall_offset_px):
            sx, sy = self._grid_to_screen(sub_x, sub_y)
            sy += fall_offset_px
            color = self.colors.get(game_state.current_puyo_2.color, (255, 255, 255))
            self._draw_puyo_blob(sx, sy, color)

    def _draw_debug_hud(self, game_state):
        if not self.debug_mode:
            return

        ground_f = self.small_font.render(
            f"Ground F: {game_state.ground_frame_count}", True, (210, 230, 255)
        )
        ground_c = self.small_font.render(
            f"Ground C: {game_state.ground_contact_count}", True, (210, 230, 255)
        )
        hud_x = self.field_outer_rect.x
        hud_y = self.score_rect.bottom + 8
        self.screen.blit(ground_f, (hud_x, hud_y))
        self.screen.blit(ground_c, (hud_x, hud_y + 18))

    def _draw_score_panel(self, game_state):
        pygame.draw.rect(self.screen, (22, 25, 32), self.score_rect)
        pygame.draw.rect(self.screen, (150, 158, 178), self.score_rect, 2)

        label = self.small_font.render("SCORE", True, (216, 220, 232))
        self.screen.blit(label, (self.score_rect.x + 8, self.score_rect.y + 8))

        score_text = game_state.get_score_display_text()
        score_surface = self.score_font.render(score_text, True, (244, 244, 244))
        score_x = self.score_rect.right - score_surface.get_width() - 8
        score_y = self.score_rect.y + (self.score_rect.height - score_surface.get_height()) // 2
        self.screen.blit(score_surface, (score_x, score_y))

    def draw(self, game_state, fall_offset_px=0.0):
        self.screen.fill((36, 39, 46))

        pygame.draw.rect(self.screen, (33, 47, 72), self.field_outer_rect)
        pygame.draw.rect(self.screen, (180, 188, 205), self.field_outer_rect, 3)

        self._draw_field_background()
        ghost_highlight_coords = set()
        ghost_highlight_factor = 1.0
        if game_state.state == "control":
            ghost_highlight_coords = game_state.get_ghost_highlight_coords()
            ghost_highlight_factor = 1.16 if (pygame.time.get_ticks() // 180) % 2 == 0 else 0.86

        if game_state.state == "animate" and game_state.animation_state == "drop_tween":
            self._draw_drop_tween(game_state)
        elif game_state.state == "animate" and game_state.animation_state == "vanish_flash":
            blink_on = True
            if VANISH_BLINK_INTERVAL_SECONDS > 0:
                blink_on = int(game_state.animation_timer / VANISH_BLINK_INTERVAL_SECONDS) % 2 == 0
            skip_coords = set()
            if not blink_on:
                skip_coords = set(game_state.vanish_coords)
            self._draw_field_cells(game_state, skip_coords=skip_coords)
        else:
            self._draw_field_cells(
                game_state,
                highlight_coords=ghost_highlight_coords,
                highlight_factor=ghost_highlight_factor,
            )

        if game_state.state == "control":
            self._draw_active_pair(game_state, fall_offset_px)

        self._draw_field_grid_lines()
        self._draw_score_panel(game_state)
        self._draw_debug_hud(game_state)

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
