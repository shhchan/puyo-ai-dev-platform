"""Pygame renderer for the placement-level versus UI."""

from __future__ import annotations

import math

import pygame

from puyo_env.actions import action_to_placement
from src.core.constants import GRID_WIDTH, PUYO_SIZE, VISIBLE_HEIGHT, PuyoColor
from src.ui.keybindings import ACTION_LABELS, ACTION_ORDER


SCREEN_WIDTH = 1120
SCREEN_HEIGHT = 780
PANEL_MARGIN = 24
PANEL_GAP = 20
PANEL_WIDTH = (SCREEN_WIDTH - PANEL_MARGIN * 2 - PANEL_GAP) // 2
FIELD_TOP = 186
OJAMA_DENOMINATIONS = (
    ("comet", 1440),
    ("crown", 720),
    ("moon", 360),
    ("star", 180),
    ("rock", 30),
    ("large", 6),
    ("small", 1),
)


def decompose_ojama(count: int) -> list[str]:
    remaining = max(0, int(count))
    icons = []
    for name, value in OJAMA_DENOMINATIONS:
        icon_count, remaining = divmod(remaining, value)
        icons.extend([name] * icon_count)
    return icons


def active_pair_cells(action: int) -> tuple[tuple[int, int], tuple[int, int]]:
    placement = action_to_placement(action)
    offsets = {
        "UP": (0, 1),
        "RIGHT": (1, 0),
        "DOWN": (0, -1),
        "LEFT": (-1, 0),
    }
    ox, oy = offsets[placement.rotation.name]
    axis_y = VISIBLE_HEIGHT + 1 - min(0, oy)
    return (
        (placement.axis_x, axis_y),
        (placement.axis_x + ox, axis_y + oy),
    )


def live_active_pair_cells(game) -> tuple[tuple[int, int, object], ...]:
    if game.current_puyo_1 is None or game.current_puyo_2 is None:
        return ()
    ox, oy = game.get_sub_puyo_offset(game.puyo_rot)
    return (
        (game.puyo_x, game.puyo_y, game.current_puyo_1.color),
        (game.puyo_x + ox, game.puyo_y + oy, game.current_puyo_2.color),
    )


def winner_banner_label(winner: str | None) -> str:
    if winner is None:
        return "DRAW"
    try:
        player_number = int(winner.rsplit("_", 1)[1]) + 1
    except (IndexError, ValueError):
        return f"{winner.replace('_', ' ').upper()} WINS"
    return f"PLAYER {player_number} WINS"


class VersusRenderer:
    def __init__(self, screen):
        self.screen = screen
        self.font = pygame.font.SysFont("Arial", 20)
        self.small_font = pygame.font.SysFont("Arial", 16)
        self.tiny_font = pygame.font.SysFont("Arial", 14)
        self.score_font = pygame.font.SysFont("Consolas", 28, bold=True)
        self.title_font = pygame.font.SysFont("Arial", 28, bold=True)
        self.banner_font = pygame.font.SysFont("Arial", 40, bold=True)
        self.settings_font = pygame.font.SysFont("Consolas", 18)
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
        return pygame.Rect(x, 20, PANEL_WIDTH, 660)

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

    def _draw_board(self, field: pygame.Rect, event, board) -> None:
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
                color_name = board[y][x]
                if color_name == PuyoColor.EMPTY:
                    continue
                sx, sy = self._grid_position(field, x, y)
                color = self.colors.get(color_name, (255, 255, 255))
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
        cells = game.get_landing_cells(
            placement.axis_x,
            placement.rotation,
            pair_colors,
            axis_y=landing_y,
        )
        for x, y, color_name in cells:
            if 0 <= x < GRID_WIDTH and 0 <= y < VISIBLE_HEIGHT:
                sx, sy = self._grid_position(field, x, y)
                self._draw_puyo(sx, sy, self.colors[color_name], alpha=alpha, ring=(245, 245, 245, 210))

    def _draw_active_pair(self, field: pygame.Rect, pair, action: int) -> None:
        if pair is None:
            return
        colors = (pair[0].color, pair[1].color)
        for (x, y), color_name in zip(active_pair_cells(action), colors):
            sx, sy = self._grid_position(field, x, y)
            self._draw_puyo(sx, sy, self.colors[color_name])

    def _draw_live_active_pair(self, field: pygame.Rect, game) -> None:
        for x, y, color_name in live_active_pair_cells(game):
            if not 0 <= x < GRID_WIDTH or not -2 <= y < VISIBLE_HEIGHT + 2:
                continue
            sx, sy = self._grid_position(field, x, y)
            self._draw_puyo(sx, sy, self.colors[color_name], ring=(245, 245, 245, 210))

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

    def _draw_ojama_icon(self, name: str, center: tuple[int, int], size: int) -> None:
        cx, cy = center
        radius = max(4, size // 2 - 2)
        outline = (32, 35, 43)
        if name == "small":
            pygame.draw.circle(self.screen, (210, 210, 215), center, max(4, radius - 3))
            pygame.draw.circle(self.screen, (125, 130, 142), center, max(4, radius - 3), 1)
        elif name in ("large", "rock"):
            color = (205, 205, 210) if name == "large" else (220, 74, 66)
            pygame.draw.circle(self.screen, color, center, radius)
            pygame.draw.circle(self.screen, outline, center, radius, 1)
            eye_y = cy - max(1, size // 8)
            for eye_x in (cx - size // 5, cx + size // 5):
                pygame.draw.circle(self.screen, (245, 245, 245), (eye_x, eye_y), max(2, size // 7))
                pygame.draw.circle(self.screen, (55, 58, 68), (eye_x, eye_y), max(1, size // 14))
        elif name == "star":
            points = []
            for index in range(10):
                angle = -math.pi / 2 + index * math.pi / 5
                point_radius = radius if index % 2 == 0 else radius * 0.45
                points.append(
                    (
                        int(round(cx + math.cos(angle) * point_radius)),
                        int(round(cy + math.sin(angle) * point_radius)),
                    )
                )
            pygame.draw.polygon(self.screen, (250, 205, 55), points)
            pygame.draw.polygon(self.screen, (218, 120, 35), points, 1)
        elif name == "moon":
            surface = pygame.Surface((size, size), pygame.SRCALPHA)
            local_center = (size // 2, size // 2)
            pygame.draw.circle(surface, (238, 143, 34), local_center, radius)
            pygame.draw.circle(
                surface,
                (0, 0, 0, 0),
                (local_center[0] - size // 5, local_center[1] - size // 8),
                max(3, radius - 3),
            )
            self.screen.blit(surface, (cx - size // 2, cy - size // 2))
        elif name == "crown":
            left = cx - radius
            right = cx + radius
            top = cy - radius
            bottom = cy + radius
            points = [
                (left, bottom),
                (left, cy - 2),
                (left + size // 4, top + size // 4),
                (cx, cy - 1),
                (right - size // 4, top + size // 4),
                (right, cy - 2),
                (right, bottom),
            ]
            pygame.draw.polygon(self.screen, (246, 178, 48), points)
            pygame.draw.polygon(self.screen, (210, 95, 30), points, 1)
        elif name == "comet":
            head = (cx + size // 5, cy - size // 8)
            tail = [
                (cx - radius, cy + radius),
                (cx - size // 5, cy - size // 8),
                (cx + size // 4, cy + size // 5),
            ]
            pygame.draw.polygon(self.screen, (65, 160, 230), tail)
            pygame.draw.circle(self.screen, (72, 176, 238), head, max(5, radius - 2))
            pygame.draw.circle(self.screen, (35, 100, 175), head, max(5, radius - 2), 1)

    def _draw_ojama_forecast(self, field: pygame.Rect, pending: int) -> None:
        rect = pygame.Rect(field.x, field.y - 30, field.width, 25)
        pygame.draw.rect(self.screen, (30, 36, 48), rect, border_radius=4)
        pygame.draw.rect(self.screen, (112, 126, 154), rect, 1, border_radius=4)
        icons = decompose_ojama(pending)
        if not icons:
            return
        step = min(23.0, max(9.0, (rect.width - 10) / max(1, len(icons))))
        size = max(10, min(22, int(step)))
        total_width = step * (len(icons) - 1) + size
        start_x = rect.centerx - total_width / 2 + size / 2
        for index, name in enumerate(icons):
            center = (int(round(start_x + index * step)), rect.centery)
            self._draw_ojama_icon(name, center, size)

    def _draw_player(self, controller, agent: str, index: int) -> None:
        panel = self._panel_rect(index)
        field = self._field_rect(panel)
        player_state = controller.env.player_states[agent]
        game = player_state.simulator.game
        info = controller.infos[agent]
        policy_name = controller.policy_display_name(agent)
        event = (
            controller.current_event
            if controller.current_event and controller.current_event.agent == agent
            else None
        )

        pygame.draw.rect(self.screen, (28, 33, 43), panel, border_radius=8)
        pygame.draw.rect(self.screen, (92, 105, 132), panel, 2, border_radius=8)
        self._draw_text(f"PLAYER {index + 1}", self.title_font, (238, 240, 248), (panel.x + 22, 32))
        self._draw_text(policy_name.upper(), self.small_font, (148, 190, 255), (panel.x + 24, 70))
        active_pair = None
        if game.current_puyo_1 is not None and game.current_puyo_2 is not None:
            active_pair = (game.current_puyo_1, game.current_puyo_2)
        self._draw_ojama_forecast(field, info["pending_ojama"])
        self._draw_board(field, event, controller.display_boards[agent])
        self._draw_text(
            f"{info['score']:08d}",
            self.score_font,
            (250, 250, 250),
            (field.centerx, field.bottom + 25),
            center=True,
        )

        selector_action = None
        selector_colors = None
        target_action = None
        target_action_for = getattr(controller, "target_action", None)
        if callable(target_action_for):
            target_action = target_action_for(agent)
        if (
            event is None
            and controller.human is not None
            and controller.human.agent == agent
            and controller.env.agents
        ):
            selector_action = controller.human.action
            selector_colors = (game.current_puyo_1.color, game.current_puyo_2.color)
        if target_action is not None and active_pair is not None:
            self._draw_pair_at_action(
                field,
                game,
                target_action,
                (active_pair[0].color, active_pair[1].color),
                alpha=85,
            )
        if selector_action is not None:
            self._draw_pair_at_action(
                field,
                game,
                selector_action,
                selector_colors,
                axis_y=event.axis_y if event is not None else None,
                alpha=180 if event is not None else 125,
            )
        if callable(getattr(controller, "uses_live_active_pair", None)) and controller.uses_live_active_pair():
            self._draw_live_active_pair(field, game)
        else:
            self._draw_active_pair(field, active_pair, controller.active_action(agent))

        side_x = field.right + 18
        queue = list(game.next_puyo_queue)
        self._draw_next_pair(queue[0] if queue else None, pygame.Rect(side_x, FIELD_TOP, 112, 92), "NEXT")
        self._draw_next_pair(
            queue[1] if len(queue) > 1 else None,
            pygame.Rect(side_x, FIELD_TOP + 104, 112, 92),
            "NEXT2",
        )

        tactical = controller.tactical_diagnostics(agent)
        stats = (
            (
                f"pending {info['pending_ojama']} t-{info.get('incoming_turns', 0)}",
                (238, 238, 242),
            ),
            (f"carry {player_state.score_carry}/70", (190, 198, 215)),
            (f"sent {info['sent_ojama_total']}", (190, 198, 215)),
            (f"max chain {info['max_chain_count']}", (190, 198, 215)),
            (
                f"target {tactical.get('target_attack', 0)} in {tactical.get('deadline', 0)}",
                (160, 210, 255),
            ),
            (
                str(tactical.get("reason", ""))[:18],
                (180, 188, 205),
            ),
        )
        for offset, (text, color) in enumerate(stats):
            self._draw_text(text, self.tiny_font, color, (side_x, FIELD_TOP + 225 + offset * 20))

        realtime_diagnostics = getattr(controller, "realtime_diagnostics", None)
        if callable(realtime_diagnostics):
            diagnostics = realtime_diagnostics(agent)
            input_label = str(diagnostics.get("input", "idle"))[:18]
            plan_label = str(diagnostics.get("plan", "plan 0/0"))[:18]
            event_label = str(diagnostics.get("event", ""))[:18]
            deadline_label = str(diagnostics.get("deadline", "deadline -"))[:18]
            for offset, (text, color) in enumerate(
                (
                    (f"input {input_label}", (255, 220, 145)),
                    (plan_label, (180, 220, 255)),
                    (event_label, (190, 198, 215)),
                    (deadline_label, (190, 198, 215)),
                )
            ):
                self._draw_text(text, self.tiny_font, color, (side_x, FIELD_TOP + 350 + offset * 18))

        if event is not None:
            color = (255, 230, 120) if event.kind == "chain" else (255, 170, 120)
            self._draw_text(event.label, self.font, color, (field.centerx, field.y + 18), center=True)

        if game.game_over:
            shade = pygame.Surface(field.size, pygame.SRCALPHA)
            shade.fill((10, 10, 14, 145))
            self.screen.blit(shade, field.topleft)
            self._draw_text("GAME OVER", self.title_font, (255, 130, 130), field.center, center=True)

    def _draw_footer(self, controller) -> None:
        state = "PAUSED" if controller.paused else "PLAYING"
        progress_unit = getattr(controller, "progress_unit", "step")
        progress_value = getattr(controller, "progress_value", controller.env.step_count)
        progress_limit = getattr(controller.config, "max_ticks", None)
        if progress_limit is None:
            progress_limit = controller.config.max_steps
        status = (
            f"{state}   speed {controller.speed:g}x   seed {controller.config.seed}   "
            f"{progress_unit} {progress_value}/{progress_limit}"
        )
        self._draw_text(status, self.font, (230, 232, 240), (SCREEN_WIDTH // 2, 704), center=True)
        bindings = controller.keybindings
        controls = (
            f"{bindings.display_names('open_settings')} keys  "
            f"{bindings.display_names('pause')} pause  "
            f"{bindings.display_names('step')} step  "
            f"{bindings.display_names('reset')} reset"
        )
        if controller.human is not None:
            controls += (
                f"   Human: {bindings.display_names('human_left')}/"
                f"{bindings.display_names('human_right')} move  "
                f"{bindings.display_names('rotate_left')}/"
                f"{bindings.display_names('rotate_right')} rotate  "
                f"{bindings.display_names('drop')} drop"
            )
        self._draw_text(controls, self.small_font, (170, 178, 198), (SCREEN_WIDTH // 2, 744), center=True)

    def _draw_key_settings(self, controller) -> None:
        shade = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        shade.fill((5, 8, 14, 215))
        self.screen.blit(shade, (0, 0))
        panel = pygame.Rect(280, 48, 560, 620)
        pygame.draw.rect(self.screen, (27, 33, 45), panel, border_radius=10)
        pygame.draw.rect(self.screen, (135, 155, 195), panel, 2, border_radius=10)
        self._draw_text("KEY BINDINGS", self.title_font, (245, 246, 252), (panel.centerx, 78), center=True)
        self._draw_text(
            "Up/Down: select   Enter: change   Backspace: defaults   Esc: close",
            self.tiny_font,
            (175, 185, 208),
            (panel.centerx, 112),
            center=True,
        )
        row_y = 144
        for index, action in enumerate(ACTION_ORDER):
            row = pygame.Rect(panel.x + 28, row_y + index * 36, panel.width - 56, 30)
            selected = index == controller.settings_index
            if selected:
                pygame.draw.rect(self.screen, (62, 80, 116), row, border_radius=5)
            color = (255, 232, 145) if selected else (225, 229, 240)
            self._draw_text(ACTION_LABELS[action], self.small_font, color, (row.x + 10, row.y + 6))
            key_text = controller.keybindings.display_names(action)
            key_surface = self.settings_font.render(key_text, True, color)
            self.screen.blit(key_surface, (row.right - key_surface.get_width() - 10, row.y + 4))
        message_color = (255, 220, 120) if controller.settings_capture else (180, 195, 220)
        self._draw_text(
            controller.settings_message,
            self.small_font,
            message_color,
            (panel.centerx, panel.bottom - 34),
            center=True,
        )

    def draw(self, controller) -> None:
        self.screen.fill((17, 20, 27))
        self._draw_player(controller, "player_0", 0)
        self._draw_player(controller, "player_1", 1)
        self._draw_footer(controller)
        if not controller.env.agents:
            winner = controller.winner
            label = winner_banner_label(winner)
            banner = pygame.Surface((520, 82), pygame.SRCALPHA)
            banner.fill((12, 15, 22, 225))
            rect = banner.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2))
            self.screen.blit(banner, rect)
            self._draw_text(label, self.banner_font, (255, 235, 145), rect.center, center=True)
        if controller.settings_open:
            self._draw_key_settings(controller)
        pygame.display.flip()
