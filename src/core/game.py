import math
import random
from collections import deque

from .constants import (
    GRID_WIDTH,
    GRID_HEIGHT,
    PuyoColor,
    Action,
    Direction,
    LOCK_CONTACT_LIMIT,
    LOCK_FRAME_LIMIT,
    COUNTDOWN_SECONDS,
    VANISH_FLASH_SECONDS,
    CHAIN_DROP_TWEEN_SECONDS,
)
from .puyo import Puyo
from .field import Field


class GameState:
    def __init__(self):
        self.field = Field()
        self.score = 0
        self.chain_count = 0
        self.game_over = False

        self.current_puyo_1 = None
        self.current_puyo_2 = None
        self.puyo_x = 2
        self.puyo_y = 12  # Top visible spawn row (axis)
        self.puyo_rot = Direction.UP

        self.next_puyo_queue = deque()
        self._fill_next_queue()

        self.state = "ready"
        self.drop_timer = 0

        self.ground_contact_count = 0
        self.ground_frame_count = 0
        self.was_grounded_prev_frame = False

        self.countdown_time_left = 0.0
        self.countdown_number = None

        # Visual interpolation progress in cell units [0, 1].
        self.vertical_interpolation_progress = 0.0

        # Rotation combo counter for blocked vertical 180 turns.
        self.blocked_rotate_input_count = 0

        # Animation phase data.
        self.animation_state = None
        self.animation_timer = 0.0
        self.vanish_coords = set()
        self.drop_tween_progress = 0.0
        self.drop_tween_grid_before = None
        self.drop_tween_static_cells = []
        self.drop_tween_motions = []

    def _fill_next_queue(self):
        while len(self.next_puyo_queue) < 2:
            colors = [
                PuyoColor.RED,
                PuyoColor.BLUE,
                PuyoColor.GREEN,
                PuyoColor.YELLOW,
                PuyoColor.PURPLE,
            ]
            p1 = Puyo(random.choice(colors))
            p2 = Puyo(random.choice(colors))
            self.next_puyo_queue.append((p1, p2))

    def _reset_control_counters(self):
        self.ground_contact_count = 0
        self.ground_frame_count = 0
        self.was_grounded_prev_frame = False
        self.blocked_rotate_input_count = 0
        self.vertical_interpolation_progress = 0.0

    def _reset_animation_data(self):
        self.animation_state = None
        self.animation_timer = 0.0
        self.vanish_coords = set()
        self.drop_tween_progress = 0.0
        self.drop_tween_grid_before = None
        self.drop_tween_static_cells = []
        self.drop_tween_motions = []

    def spawn_puyo(self):
        spawn_y = 12
        # Choke point: 12th visible row, 3rd column from the left.
        if not self.field.get_puyo(2, 11).is_empty():
            self.game_over = True
            self.state = "gameover"
            return

        next_pair = self.next_puyo_queue.popleft()
        self._fill_next_queue()

        self.current_puyo_1 = next_pair[0]
        self.current_puyo_2 = next_pair[1]

        self.puyo_x = 2
        self.puyo_y = spawn_y
        self.puyo_rot = Direction.UP
        self.state = "control"
        self.countdown_time_left = 0.0
        self.countdown_number = None
        self._reset_control_counters()
        self._reset_animation_data()

    def get_sub_puyo_offset(self, rotation):
        if rotation == Direction.UP:
            return (0, 1)
        if rotation == Direction.RIGHT:
            return (1, 0)
        if rotation == Direction.DOWN:
            return (0, -1)
        if rotation == Direction.LEFT:
            return (-1, 0)
        return (0, 1)

    def set_vertical_interpolation(self, progress_cells):
        self.vertical_interpolation_progress = max(0.0, min(1.0, progress_cells))

    def can_place_pair(self, axis_x, axis_y, rot):
        # Axis cannot enter row 14 (index 13). Child can.
        if not (0 <= axis_x < GRID_WIDTH and 0 <= axis_y < GRID_HEIGHT - 1):
            return False
        if not self.field.get_puyo(axis_x, axis_y).is_empty():
            return False

        ox, oy = self.get_sub_puyo_offset(rot)
        sub_x, sub_y = axis_x + ox, axis_y + oy
        if not (0 <= sub_x < GRID_WIDTH and 0 <= sub_y < GRID_HEIGHT):
            return False
        if not self.field.get_puyo(sub_x, sub_y).is_empty():
            return False

        return True

    def can_move(self, dx, dy, rot):
        return self.can_place_pair(self.puyo_x + dx, self.puyo_y + dy, rot)

    def can_move_horizontal(self, dx):
        target_x = self.puyo_x + dx
        if not self.can_place_pair(target_x, self.puyo_y, self.puyo_rot):
            return False

        # If currently between rows visually, sweep-check one row below too.
        if self.vertical_interpolation_progress > 0.0:
            return self.can_place_pair(target_x, self.puyo_y - 1, self.puyo_rot)

        return True

    def get_ghost_axis_position(self):
        if self.current_puyo_1 is None or self.current_puyo_2 is None or self.state != "control":
            return None

        ghost_y = self.puyo_y
        while self.can_place_pair(self.puyo_x, ghost_y - 1, self.puyo_rot):
            ghost_y -= 1
        return (self.puyo_x, ghost_y)

    def start_countdown(self):
        if self.state != "ready":
            return

        self.state = "countdown"
        self.countdown_time_left = COUNTDOWN_SECONDS
        self.countdown_number = int(math.ceil(self.countdown_time_left))

    def advance_countdown(self, delta_time):
        if self.state != "countdown":
            return

        self.countdown_time_left = max(0.0, self.countdown_time_left - delta_time)
        if self.countdown_time_left > 0.0:
            self.countdown_number = int(math.ceil(self.countdown_time_left))
            return

        self.countdown_number = None
        self.spawn_puyo()

    def _is_axis_below_blocked(self):
        return self.puyo_y == 0 or (not self.field.get_puyo(self.puyo_x, self.puyo_y - 1).is_empty())

    def _is_vertical(self):
        return self.puyo_rot in (Direction.UP, Direction.DOWN)

    def _is_sides_blocked(self):
        return (not self.can_move(-1, 0, self.puyo_rot)) and (not self.can_move(1, 0, self.puyo_rot))

    def _rotate_90(self, clockwise):
        dirs = [Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT]
        idx = dirs.index(self.puyo_rot)
        new_idx = (idx + 1) % 4 if clockwise else (idx - 1) % 4
        new_rot = dirs[new_idx]

        if self.can_move(0, 0, new_rot):
            self.puyo_rot = new_rot
            return

        axis_below_blocked = self._is_axis_below_blocked()
        if axis_below_blocked and self.can_move(0, 1, new_rot):
            self.puyo_y += 1
            self.puyo_rot = new_rot
        elif self.can_move(1, 0, new_rot):
            self.puyo_x += 1
            self.puyo_rot = new_rot
        elif self.can_move(-1, 0, new_rot):
            self.puyo_x -= 1
            self.puyo_rot = new_rot

    def _try_rotate_180(self):
        if self.puyo_rot == Direction.UP:
            target_rot = Direction.DOWN
        elif self.puyo_rot == Direction.DOWN:
            target_rot = Direction.UP
        else:
            return False

        if self.can_move(0, 0, target_rot):
            self.puyo_rot = target_rot
            return True

        if self._is_axis_below_blocked() and self.can_move(0, 1, target_rot):
            self.puyo_y += 1
            self.puyo_rot = target_rot
            return True

        return False

    def handle_rotate_input(self, clockwise):
        if self._is_vertical() and self._is_sides_blocked():
            self.blocked_rotate_input_count += 1
            if self.blocked_rotate_input_count >= 2:
                self._try_rotate_180()
                self.blocked_rotate_input_count = 0
            return

        self.blocked_rotate_input_count = 0
        self._rotate_90(clockwise)

    def rotate(self, clockwise):
        self.handle_rotate_input(clockwise)

    def _update_ground_lock(self):
        if self.state != "control" or self.current_puyo_1 is None:
            return

        grounded = not self.can_move(0, -1, self.puyo_rot)
        if grounded:
            self.ground_frame_count += 1
            if not self.was_grounded_prev_frame:
                self.ground_contact_count += 1
            self.was_grounded_prev_frame = True

            if self.ground_contact_count >= LOCK_CONTACT_LIMIT or self.ground_frame_count >= LOCK_FRAME_LIMIT:
                self.lock_puyo()
        else:
            self.was_grounded_prev_frame = False

    def update(self, actions):
        if self.state == "ready":
            if Action.START in actions:
                self.start_countdown()
            return

        if self.state in ("countdown", "gameover", "animate"):
            return

        left_pressed = Action.LEFT in actions
        right_pressed = Action.RIGHT in actions
        down_pressed = Action.DOWN in actions

        horizontal_requested = False
        horizontal_moved = False

        if left_pressed and not right_pressed:
            horizontal_requested = True
            if self.can_move_horizontal(-1):
                self.puyo_x -= 1
                horizontal_moved = True
        elif right_pressed and not left_pressed:
            horizontal_requested = True
            if self.can_move_horizontal(1):
                self.puyo_x += 1
                horizontal_moved = True

        if down_pressed:
            # Horizontal input has priority only when horizontal movement succeeds.
            can_apply_down = (not horizontal_requested) or (not horizontal_moved)
            if can_apply_down and self.can_move(0, -1, self.puyo_rot):
                self.puyo_y -= 1

        for action in actions:
            if action == Action.ROTATE_RIGHT:
                self.handle_rotate_input(True)
            elif action == Action.ROTATE_LEFT:
                self.handle_rotate_input(False)

        self._update_ground_lock()

    def step_gravity(self):
        if self.state == "control" and self.can_move(0, -1, self.puyo_rot):
            self.puyo_y -= 1

    def lock_puyo(self):
        self.field.place_puyo(self.puyo_x, self.puyo_y, self.current_puyo_1)
        ox, oy = self.get_sub_puyo_offset(self.puyo_rot)
        self.field.place_puyo(self.puyo_x + ox, self.puyo_y + oy, self.current_puyo_2)

        self.current_puyo_1 = None
        self.current_puyo_2 = None
        self.state = "animate"
        self.chain_count = 0
        self._start_resolve_phase()

    def _snapshot_field_colors(self):
        snapshot = []
        for y in range(GRID_HEIGHT):
            row = []
            for x in range(GRID_WIDTH):
                row.append(self.field.get_puyo(x, y).color)
            snapshot.append(row)
        return snapshot

    def _start_resolve_phase(self):
        self.animation_state = "resolve"
        self.animation_timer = 0.0
        self.vanish_coords = set()
        self.drop_tween_progress = 0.0
        self.drop_tween_grid_before = None
        self.drop_tween_static_cells = []
        self.drop_tween_motions = []

    def _prepare_drop_tween(self, before_snapshot):
        motions = []
        moving_sources = set()

        for x in range(GRID_WIDTH):
            before_col = [
                (y, before_snapshot[y][x])
                for y in range(GRID_HEIGHT - 1)
                if before_snapshot[y][x] != PuyoColor.EMPTY
            ]
            after_col = [
                (y, self.field.get_puyo(x, y).color)
                for y in range(GRID_HEIGHT - 1)
                if not self.field.get_puyo(x, y).is_empty()
            ]

            for idx, (to_y, color) in enumerate(after_col):
                from_y, _ = before_col[idx]
                if from_y != to_y:
                    motions.append((x, from_y, to_y, color))
                    moving_sources.add((x, from_y))

        static_cells = []
        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                color = before_snapshot[y][x]
                if color == PuyoColor.EMPTY:
                    continue
                if (x, y) in moving_sources:
                    continue
                static_cells.append((x, y, color))

        self.drop_tween_grid_before = before_snapshot
        self.drop_tween_motions = motions
        self.drop_tween_static_cells = static_cells
        self.drop_tween_progress = 0.0
        self.animation_state = "drop_tween"
        self.animation_timer = 0.0

    def _resolve_animation_step(self):
        before_snapshot = self._snapshot_field_colors()
        dropped = self.field.drop_puyo()
        if dropped:
            self._prepare_drop_tween(before_snapshot)
            return

        vanish = self.field.check_vanish()
        if vanish:
            self.vanish_coords = vanish
            self.animation_state = "vanish_flash"
            self.animation_timer = 0.0
            return

        self.spawn_puyo()

    def advance_animation(self, delta_time):
        if self.state != "animate":
            return

        if self.animation_state == "resolve":
            self._resolve_animation_step()
            return

        if self.animation_state == "vanish_flash":
            self.animation_timer += delta_time
            if self.animation_timer < VANISH_FLASH_SECONDS:
                return

            self.field.remove_puyos(self.vanish_coords)
            self.score += len(self.vanish_coords) * 10 * (self.chain_count + 1)
            self.chain_count += 1
            self.vanish_coords = set()
            self.animation_state = "resolve"
            self.animation_timer = 0.0
            return

        if self.animation_state == "drop_tween":
            self.animation_timer += delta_time
            if CHAIN_DROP_TWEEN_SECONDS > 0:
                self.drop_tween_progress = min(1.0, self.animation_timer / CHAIN_DROP_TWEEN_SECONDS)
            else:
                self.drop_tween_progress = 1.0

            if self.animation_timer < CHAIN_DROP_TWEEN_SECONDS:
                return

            self.drop_tween_progress = 1.0
            self.drop_tween_grid_before = None
            self.drop_tween_static_cells = []
            self.drop_tween_motions = []
            self.animation_state = "resolve"
            self.animation_timer = 0.0

    def resolve_world(self):
        # Compatibility wrapper for older call sites.
        self.advance_animation(max(VANISH_FLASH_SECONDS, CHAIN_DROP_TWEEN_SECONDS))
