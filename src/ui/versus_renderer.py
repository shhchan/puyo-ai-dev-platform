"""Pygame renderer for the placement-level versus UI."""

from __future__ import annotations

import math

import pygame

from puyo_env.actions import action_to_placement
from src.core.constants import GRID_WIDTH, PUYO_SIZE, VISIBLE_HEIGHT, PuyoColor


SCREEN_WIDTH = 1120
SCREEN_HEIGHT = 720
PANEL_MARGIN = 24
PANEL_GAP = 20
PANEL_WIDTH = (SCREEN_WIDTH - PANEL_MARGIN * 2 - PANEL_GAP) // 2
FIELD_TOP = 146


class VersusRenderer:
    def __init__(self, screen):
        self.screen = screen
        self.font = pygame.font.SysFont("Arial", 20)
        self.small_font = pygame.font.SysFont("Arial", 16)
        self.tiny_font = pygame.font.SysFont("Arial", 14)
        self.score_font = pygame.font.SysFont("Consolas", 28, bold=True)
        self.title_font = pygame.font.SysFont("Arial", 28, bold=True)
        self.banner_font = pygame.font.SysFont("Arial", 40, bold=True)
        self.colors = {
            PuyoColor.RED: (235, 74, 74),
            PuyoColor.BLUE: (65, 135, 245),
            PuyoColor.GREEN: (84, 224, 84),
            PuyoColor.YELLOW: (244, 220, 80),
            PuyoColor.PURPLE: (177, 96, 226),
            PuyoColor.OJAMA: (205, 205, 210),
            PuyoColor.WALL: (100, 100, 100),
            PuyoColor.EMPTY: (0, 0, 0),
        }

    def _panel_rect(self, index: int) -> pygame.Rect:
        x = PANEL_MARGIN + index * (PANEL_WIDTH + PANEL_GAP)
        return pygame.Rect(x, 20, PANEL_WIDTH, 610)

    def _field_rect(self, panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.x + 32, FIELD_TOP, GRID_WIDTH * PUYO_SIZE, VISIBLE_HEIGHT * PUYO_SIZE)

    def _grid_position(self, field: pygame.Rect, x: int, y: float) -> tuple[float, float]:
        return field.x + x * PUYO_SIZE, field.bottom - (y + 1) * PUYO_SIZE

    def _draw_text(self, text: str, font, color, position, *, center: bool = False) -> pygame.Rect:
        surface = font.render(text, True, color)
        rect = surface.get_rect()
        if center:
            rect.center = position
        else:
            rect.topleft = position
        self.screen.blit(surface, rect)
        return rect

    def _scaled_color(self, color, factor: float):
        return tuple(max(0, min(255, int(channel * factor))) for channel in color)

    def _draw_puyo(self, x: float, y: float, color, *, alpha: int = 255, ring=None) -> None:
        size = PUYO_SIZE
        surface = pygame.Surface((size, size), pygame.SRCALPHA)
        center = size // 2
        radius = int(size * 0.38)
        draw_color = (*color, alpha)
        pygame.draw.circle(surface, draw_color, (center, center), radius)
        pygame.draw.circle(surface, (15, 18, 24, alpha), (center, center), radius, 1)
        shine = (*self._scaled_color(color, 1.22), alpha)
        pygame.draw.circle(surface, shine, (center - 6, center - 6), 4)
        if ring is not None:
            pygame.draw.circle(surface, ring, (center, center), radius + 2, 3)
        self.screen.blit(surface, (int(round(x)), int(round(y))))

    def _draw_board(self, field: pygame.Rect, game, event) -> None:
        pygame.draw.rect(self.screen, (18, 23, 32), field)
        for row in range(VISIBLE_HEIGHT + 1):
            y = field.y + row * PUYO_SIZE
            pygame.draw.line(self.screen, (38, 44, 56), (field.x, y), (field.right, y), 1)
        for column in range(GRID_WIDTH + 1):
            x = field.x + column * PUYO_SIZE
            pygame.draw.line(self.screen, (38, 44, 56), (x, field.y), (x, field.bottom), 1)

        highlighted = event.coords if event is not None and event.kind == "chain" else frozenset()
        pulse = 0.75 + 0.25 * math.sin(pygame.time.get_ticks() / 80.0)
        for y in range(VISIBLE_HEIGHT):
            for x in range(GRID_WIDTH):
                puyo = game.field.get_puyo(x, y)
                if puyo.is_empty():
                    continue
                sx, sy = self._grid_position(field, x, y)
                color = self.colors.get(puyo.color, (255, 255, 255))
                ring = (255, 245, 170, 255) if (x, y) in highlighted else None
                self._draw_puyo(sx, sy, self._scaled_color(color, pulse if ring else 1.0), ring=ring)

        if event is not None and event.kind == "chain":
            for x, y in event.coords:
                if not 0 <= y < VISIBLE_HEIGHT:
                    continue
                sx, sy = self._grid_position(field, x, y)
                self._draw_puyo(sx, sy, (255, 245, 170), alpha=95, ring=(255, 245, 170, 220))

        pygame.draw.rect(self.screen, (188, 195, 212), field, 4)

    def _draw_pair_at_action(self, field: pygame.Rect, game, action: int, pair_colors, axis_y=None, alpha=150):
        placement = action_to_placement(action)
        landing_y = game.find_landing_y(placement.axis_x, placement.rotation) if axis_y is None else axis_y
        if landing_y is None or pair_colors is None:
            return
        ox, oy = game.get_sub_puyo_offset(placement.rotation)
        cells = (
            (placement.axis_x, landing_y, pair_colors[0]),
            (placement.axis_x + ox, landing_y + oy, pair_colors[1]),
        )
        for x, y, color_name in cells:
            if 0 <= x < GRID_WIDTH and 0 <= y < VISIBLE_HEIGHT:
                sx, sy = self._grid_position(field, x, y)
                self._draw_puyo(sx, sy, self.colors[color_name], alpha=alpha, ring=(245, 245, 245, 210))

    def _draw_next_pair(self, pair, rect: pygame.Rect, label: str) -> None:
        pygame.draw.rect(self.screen, (30, 36, 48), rect)
        pygame.draw.rect(self.screen, (125, 136, 160), rect, 2)
        self._draw_text(label, self.tiny_font, (210, 215, 230), (rect.centerx, rect.y + 15), center=True)
        if pair is None:
            return
        size = 24
        x = rect.centerx - PUYO_SIZE // 2
        self._draw_puyo(x, rect.y + 27, self.colors[pair[1].color])
        self._draw_puyo(x, rect.y + 27 + size, self.colors[pair[0].color])

    def _draw_ojama_meter(self, rect: pygame.Rect, pending: int) -> None:
        pygame.draw.rect(self.screen, (28, 32, 42), rect)
        pygame.draw.rect(self.screen, (120, 130, 150), rect, 1)
        visible = min(30, max(0, pending))
        for index in range(visible):
            col = index % 6
            row = index // 6
            cx = rect.x + 10 + col * 17
            cy = rect.y + 14 + row * 14
            pygame.draw.circle(self.screen, (205, 205, 210), (cx, cy), 5)

    def _draw_player(self, controller, agent: str, index: int) -> None:
        panel = self._panel_rect(index)
        field = self._field_rect(panel)
        player_state = controller.env.player_states[agent]
        game = player_state.simulator.game
        info = controller.infos[agent]
        policy_name = controller.policy_names[agent]
        event = (
            controller.current_event
            if controller.current_event and controller.current_event.agent == agent
            else None
        )

        pygame.draw.rect(self.screen, (28, 33, 43), panel, border_radius=8)
        pygame.draw.rect(self.screen, (92, 105, 132), panel, 2, border_radius=8)
        self._draw_text(f"PLAYER {index + 1}", self.title_font, (238, 240, 248), (panel.x + 22, 32))
        self._draw_text(policy_name.upper(), self.small_font, (148, 190, 255), (panel.x + 24, 70))
        self._draw_text(f"SCORE {info['score']:08d}", self.score_font, (250, 250, 250), (panel.x + 22, 94))
        active_pair = None
        if game.current_puyo_1 is not None and game.current_puyo_2 is not None:
            active_pair = (game.current_puyo_1, game.current_puyo_2)
        self._draw_next_pair(
            active_pair,
            pygame.Rect(panel.right - 132, 38, 112, 88),
            "ACTIVE",
        )

        self._draw_board(field, game, event)

        selector_action = None
        selector_colors = None
        if controller.human is not None and controller.human.agent == agent and controller.env.agents:
            selector_action = controller.human.action
            selector_colors = (game.current_puyo_1.color, game.current_puyo_2.color)
        elif event is not None and event.kind == "placement":
            selector_action = event.action
            selector_colors = event.pair_colors
        if selector_action is not None:
            self._draw_pair_at_action(
                field,
                game,
                selector_action,
                selector_colors,
                axis_y=event.axis_y if event is not None else None,
                alpha=180 if event is not None else 125,
            )

        side_x = field.right + 18
        queue = list(game.next_puyo_queue)
        self._draw_next_pair(queue[0] if queue else None, pygame.Rect(side_x, FIELD_TOP, 112, 92), "NEXT")
        self._draw_next_pair(
            queue[1] if len(queue) > 1 else None,
            pygame.Rect(side_x, FIELD_TOP + 104, 112, 92),
            "NEXT2",
        )

        self._draw_text("OJAMA", self.tiny_font, (210, 215, 230), (side_x, FIELD_TOP + 218))
        meter = pygame.Rect(side_x, FIELD_TOP + 239, 112, 82)
        self._draw_ojama_meter(meter, info["pending_ojama"])
        stats = (
            (f"pending {info['pending_ojama']}", (238, 238, 242)),
            (f"carry {player_state.score_carry}/70", (190, 198, 215)),
            (f"sent {info['sent_ojama_total']}", (190, 198, 215)),
            (f"max chain {info['max_chain_count']}", (190, 198, 215)),
        )
        for offset, (text, color) in enumerate(stats):
            self._draw_text(text, self.tiny_font, color, (side_x, meter.bottom + 5 + offset * 18))

        if event is not None:
            color = (255, 230, 120) if event.kind == "chain" else (255, 170, 120)
            self._draw_text(event.label, self.font, color, (field.centerx, field.y - 18), center=True)

        if game.game_over:
            shade = pygame.Surface(field.size, pygame.SRCALPHA)
            shade.fill((10, 10, 14, 145))
            self.screen.blit(shade, field.topleft)
            self._draw_text("GAME OVER", self.title_font, (255, 130, 130), field.center, center=True)

    def _draw_footer(self, controller) -> None:
        state = "PAUSED" if controller.paused else "PLAYING"
        status = (
            f"{state}   speed {controller.speed:g}x   seed {controller.config.seed}   "
            f"step {controller.env.step_count}/{controller.config.max_steps}"
        )
        self._draw_text(status, self.font, (230, 232, 240), (SCREEN_WIDTH // 2, 654), center=True)
        controls = "P pause  N step  [/] speed  R reset  Esc quit"
        if controller.human is not None:
            controls += "   Human: A/D move  Q/E rotate  S/Enter drop"
        self._draw_text(controls, self.small_font, (170, 178, 198), (SCREEN_WIDTH // 2, 686), center=True)

    def draw(self, controller) -> None:
        self.screen.fill((17, 20, 27))
        self._draw_player(controller, "player_0", 0)
        self._draw_player(controller, "player_1", 1)
        self._draw_footer(controller)
        if not controller.env.agents:
            winner = controller.winner
            label = "DRAW" if winner is None else f"{winner.replace('_', ' ').upper()} WINS"
            banner = pygame.Surface((520, 82), pygame.SRCALPHA)
            banner.fill((12, 15, 22, 225))
            rect = banner.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2))
            self.screen.blit(banner, rect)
            self._draw_text(label, self.banner_font, (255, 235, 145), rect.center, center=True)
        pygame.display.flip()
