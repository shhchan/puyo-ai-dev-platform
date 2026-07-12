import math
from collections import deque

from .constants import (
    GRID_WIDTH,
    GRID_HEIGHT,
    VISIBLE_HEIGHT,
    PuyoColor,
    Action,
    Direction,
    LOCK_CONTACT_LIMIT,
    LOCK_FRAME_LIMIT,
    COUNTDOWN_SECONDS,
    VANISH_FLASH_SECONDS,
    CHAIN_DROP_TWEEN_SECONDS,
    ALL_CLEAR_BONUS_SCORE,
    CHAIN_BONUS_TABLE,
    COLOR_BONUS_TABLE,
    get_connection_bonus,
)
from .puyo import Puyo
from .field import Field
from .tsumo import PuyoSequence


class GameState:
    def __init__(self, seed=None, puyo_sequence=None):
        self.field = Field()
        self.puyo_sequence = puyo_sequence or PuyoSequence(seed=seed)
        self.score = 0
        self.chain_count = 0
        self.game_over = False
        self.all_clear_achieved = False
        self.all_clear_bonus_pending = False
        self.all_clear_bonus_consumed = False
        self.all_clear_bonus_score = 0

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
        self.vanish_groups = []
        self.drop_tween_progress = 0.0
        self.drop_tween_grid_before = None
        self.drop_tween_static_cells = []
        self.drop_tween_motions = []
        self.chain_display_a = None
        self.chain_display_b = None
        self.chain_display_score = 0
        self.chain_display_all_clear_bonus = 0
        self.floor_kick_horizontal_grace = False
        self.soft_drop_cells_this_pair = 0
        self.soft_drop_used_this_pair = False

    def _fill_next_queue(self):
        while len(self.next_puyo_queue) < 2:
            self.next_puyo_queue.append(self.puyo_sequence.next_pair())

    def _reset_control_counters(self):
        self.ground_contact_count = 0
        self.ground_frame_count = 0
        self.was_grounded_prev_frame = False
        self.blocked_rotate_input_count = 0
        self.vertical_interpolation_progress = 0.0
        self.floor_kick_horizontal_grace = False

    def _reset_animation_data(self):
        self.animation_state = None
        self.animation_timer = 0.0
        self.vanish_coords = set()
        self.vanish_groups = []
        self.drop_tween_progress = 0.0
        self.drop_tween_grid_before = None
        self.drop_tween_static_cells = []
        self.drop_tween_motions = []
        self.chain_display_a = None
        self.chain_display_b = None
        self.chain_display_score = 0
        self.chain_display_all_clear_bonus = 0

    def _reset_drop_bonus_state(self):
        self.soft_drop_cells_this_pair = 0
        self.soft_drop_used_this_pair = False

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
        self._reset_drop_bonus_state()

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
        # Temporary investigation mode (PUYO-11):
        # Axis is also allowed on row 14 (index 13) while sub remains in-bounds.
        if not (0 <= axis_x < GRID_WIDTH and 0 <= axis_y < GRID_HEIGHT):
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

    def find_landing_y(self, axis_x, rot, start_y=12):
        if self.current_puyo_1 is None or self.current_puyo_2 is None:
            return None
        if not self.can_place_pair(axis_x, start_y, rot):
            return None

        landing_y = start_y
        while self.can_place_pair(axis_x, landing_y - 1, rot):
            landing_y -= 1
        return landing_y

    def _can_place_pair_with_vertical_sweep(self, axis_x, axis_y, rot):
        if not self.can_place_pair(axis_x, axis_y, rot):
            return False
        if self.vertical_interpolation_progress > 0.0:
            return self.can_place_pair(axis_x, axis_y - 1, rot)
        return True

    def _can_place_pair_for_rotation(self, axis_x, axis_y, rot, apply_interpolation_sweep=True):
        # Rotation uses pure board occupancy at the target orientation.
        # At half-cell interpolation, rotation should respect intermediate
        # occupancy so floor-kick resolves to +1.0 effective height.
        can_place = self.can_place_pair(axis_x, axis_y, rot)
        if can_place and apply_interpolation_sweep and self.vertical_interpolation_progress > 0.0:
            can_place = self.can_place_pair(axis_x, axis_y - 1, rot)
        if can_place:
            return True

        # Temporary top-row allowance only while interpolating between rows.
        # This enables floor-kick at 11.5 -> 12.5 cell heights.
        allow_top_row = self.vertical_interpolation_progress > 0.0 or self.floor_kick_horizontal_grace
        if (
            allow_top_row
            and axis_y == GRID_HEIGHT - 1
            and 0 <= axis_x < GRID_WIDTH
            and self.field.get_puyo(axis_x, axis_y).is_empty()
        ):
            ox, oy = self.get_sub_puyo_offset(rot)
            sub_x, sub_y = axis_x + ox, axis_y + oy
            sub_in_field = (
                0 <= sub_x < GRID_WIDTH
                and 0 <= sub_y < GRID_HEIGHT
                and self.field.get_puyo(sub_x, sub_y).is_empty()
            )
            allow_sub_overflow_for_interp = (
                self.vertical_interpolation_progress > 0.0
                and oy == 1
                and 0 <= sub_x < GRID_WIDTH
                and sub_y == GRID_HEIGHT
            )
            if sub_in_field or allow_sub_overflow_for_interp:
                if (
                    apply_interpolation_sweep
                    and axis_y - 1 >= 0
                    and not self.can_place_pair(axis_x, axis_y - 1, rot)
                ):
                    return False
                return True
        return False

    def can_move_horizontal(self, dx):
        target_x = self.puyo_x + dx
        if self.floor_kick_horizontal_grace:
            return self._can_place_pair_for_rotation(
                target_x,
                self.puyo_y,
                self.puyo_rot,
                apply_interpolation_sweep=False,
            )
        return self._can_place_pair_with_vertical_sweep(target_x, self.puyo_y, self.puyo_rot)

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
        check_rows = [self.puyo_y - 1]
        if self.vertical_interpolation_progress > 0.0:
            check_rows.append(self.puyo_y - 2)

        for row in check_rows:
            if row < 0:
                return True
            if not self.field.get_puyo(self.puyo_x, row).is_empty():
                return True
        return False

    def _is_vertical(self):
        return self.puyo_rot in (Direction.UP, Direction.DOWN)

    def _is_sides_blocked(self):
        # Use rotation placement semantics so interpolated top-row floor-kick
        # states are not treated as permanently side-blocked.
        return (not self._can_place_pair_for_rotation(self.puyo_x - 1, self.puyo_y, self.puyo_rot)) and (
            not self._can_place_pair_for_rotation(self.puyo_x + 1, self.puyo_y, self.puyo_rot)
        )

    def _register_interpolated_floor_kick_contact(self):
        # During half-cell falling interpolation, an up-kick should count as a
        # ground contact event for lock debugging/behavior visibility.
        if self.vertical_interpolation_progress <= 0.0:
            return
        self.ground_contact_count += 1
        self.floor_kick_horizontal_grace = True

    def _clear_floor_kick_horizontal_grace(self):
        self.floor_kick_horizontal_grace = False

    def _should_forbid_row14_floor_kick(self, target_axis_y, axis_below_blocked):
        # Temporary investigation mode (PUYO-11):
        # keep floor-kick path unconstrained by axis row-14 rule.
        _ = (target_axis_y, axis_below_blocked)
        return False

    def _rotate_90(self, clockwise):
        dirs = [Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT]
        idx = dirs.index(self.puyo_rot)
        new_idx = (idx + 1) % 4 if clockwise else (idx - 1) % 4
        new_rot = dirs[new_idx]

        if self._can_place_pair_for_rotation(self.puyo_x, self.puyo_y, new_rot):
            self.puyo_rot = new_rot
            return

        axis_below_blocked = self._is_axis_below_blocked()
        if axis_below_blocked:
            kick_target_y = self.puyo_y + 1
            forbid_row14_floor_kick = self._should_forbid_row14_floor_kick(kick_target_y, axis_below_blocked)
            if (not forbid_row14_floor_kick) and self._can_place_pair_for_rotation(
                self.puyo_x,
                kick_target_y,
                new_rot,
                apply_interpolation_sweep=False,
            ):
                self.puyo_y = kick_target_y
                self.puyo_rot = new_rot
                self._register_interpolated_floor_kick_contact()
                # When floor-kick context is active, never fallback to side-kick.
                return
            if not forbid_row14_floor_kick:
                # Keep existing no-side-kick behavior unless this specific
                # row14-axis rule is the reason kick is disallowed.
                return

        if self._can_place_pair_for_rotation(self.puyo_x + 1, self.puyo_y, new_rot):
            self.puyo_x += 1
            self.puyo_rot = new_rot
        elif self._can_place_pair_for_rotation(self.puyo_x - 1, self.puyo_y, new_rot):
            self.puyo_x -= 1
            self.puyo_rot = new_rot

    def _try_rotate_180(self):
        if self.puyo_rot == Direction.UP:
            target_rot = Direction.DOWN
        elif self.puyo_rot == Direction.DOWN:
            target_rot = Direction.UP
        else:
            return False

        if self.puyo_rot == Direction.UP:
            kick_target_y = self.puyo_y + 1
            if self._can_place_pair_for_rotation(
                self.puyo_x,
                kick_target_y,
                target_rot,
                apply_interpolation_sweep=False,
            ):
                self.puyo_y = kick_target_y
                self.puyo_rot = target_rot
                self._register_interpolated_floor_kick_contact()
                return True

        if self.puyo_rot == Direction.DOWN:
            lower_target_y = self.puyo_y - 1
            if self._can_place_pair_for_rotation(self.puyo_x, lower_target_y, target_rot):
                self.puyo_y = lower_target_y
                self.puyo_rot = target_rot
                return True

        if self._can_place_pair_for_rotation(self.puyo_x, self.puyo_y, target_rot):
            self.puyo_rot = target_rot
            return True

        axis_below_blocked = self._is_axis_below_blocked()
        if axis_below_blocked:
            kick_target_y = self.puyo_y + 1
            if self._should_forbid_row14_floor_kick(kick_target_y, axis_below_blocked):
                return False
            if self._can_place_pair_for_rotation(
                self.puyo_x,
                kick_target_y,
                target_rot,
                apply_interpolation_sweep=False,
            ):
                self.puyo_y = kick_target_y
                self.puyo_rot = target_rot
                self._register_interpolated_floor_kick_contact()
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
            return False

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

    def update(self, actions, held_actions=None):
        if self.state == "ready":
            if Action.START in actions:
                self.start_countdown()
            return

        if self.state in ("countdown", "gameover", "animate"):
            return

        left_pressed = Action.LEFT in actions
        right_pressed = Action.RIGHT in actions
        down_pressed = Action.DOWN in actions

        if left_pressed and not right_pressed:
            if self.can_move_horizontal(-1):
                self.puyo_x -= 1
        elif right_pressed and not left_pressed:
            if self.can_move_horizontal(1):
                self.puyo_x += 1

        if down_pressed:
            if self.can_move(0, -1, self.puyo_rot):
                self.puyo_y -= 1
                if self.puyo_y <= VISIBLE_HEIGHT:
                    self._clear_floor_kick_horizontal_grace()
                self.soft_drop_cells_this_pair += 1
                self.soft_drop_used_this_pair = True
                self.score += 1

        for action in actions:
            if action == Action.ROTATE_RIGHT:
                self.handle_rotate_input(True)
            elif action == Action.ROTATE_LEFT:
                self.handle_rotate_input(False)

        self._update_ground_lock()

    def step_gravity(self):
        if self.state == "control" and self.can_move(0, -1, self.puyo_rot):
            self.puyo_y -= 1
            if self.puyo_y <= VISIBLE_HEIGHT:
                self._clear_floor_kick_horizontal_grace()

    def lock_puyo(self):
        if self.soft_drop_used_this_pair:
            self.score += 1

        self.field.place_puyo(self.puyo_x, self.puyo_y, self.current_puyo_1)
        ox, oy = self.get_sub_puyo_offset(self.puyo_rot)
        self.field.place_puyo(self.puyo_x + ox, self.puyo_y + oy, self.current_puyo_2)

        self.current_puyo_1 = None
        self.current_puyo_2 = None
        self.state = "animate"
        self._clear_floor_kick_horizontal_grace()
        self._begin_chain_resolution()

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
        self.vanish_groups = []
        self.drop_tween_progress = 0.0
        self.drop_tween_grid_before = None
        self.drop_tween_static_cells = []
        self.drop_tween_motions = []
        self.chain_display_a = None
        self.chain_display_b = None
        self.chain_display_score = 0
        self.chain_display_all_clear_bonus = 0

    def _begin_chain_resolution(self):
        self.chain_count = 0
        self.all_clear_achieved = False
        self.all_clear_bonus_consumed = False
        self.all_clear_bonus_score = 0
        self._start_resolve_phase()

    def _consume_all_clear_bonus(self):
        if self.chain_count != 0 or not self.all_clear_bonus_pending:
            return 0
        self.all_clear_bonus_pending = False
        self.all_clear_bonus_consumed = True
        self.all_clear_bonus_score = ALL_CLEAR_BONUS_SCORE
        return ALL_CLEAR_BONUS_SCORE

    def _field_is_empty(self):
        return all(
            self.field.get_puyo(x, y).is_empty()
            for y in range(GRID_HEIGHT)
            for x in range(GRID_WIDTH)
        )

    def _finish_chain_resolution(self):
        self.all_clear_achieved = self.chain_count > 0 and self._field_is_empty()
        if self.all_clear_achieved:
            self.all_clear_bonus_pending = True

    def _calculate_chain_score_components(self):
        puyo_count = len(self.vanish_coords)
        if puyo_count == 0:
            return (0, 0, 0)

        chain_index = self.chain_count + 1
        chain_bonus_index = min(chain_index, len(CHAIN_BONUS_TABLE) - 1)
        chain_bonus = CHAIN_BONUS_TABLE[chain_bonus_index]

        connect_bonus = 0
        colors = set()
        for group in self.vanish_groups:
            connect_bonus += get_connection_bonus(len(group))
            if group:
                sample_x, sample_y = next(iter(group))
                colors.add(self.field.get_puyo(sample_x, sample_y).color)

        color_bonus = COLOR_BONUS_TABLE.get(len(colors), 0)
        raw_b = chain_bonus + connect_bonus + color_bonus
        b = max(1, raw_b)
        a = puyo_count * 10
        return (a, b, a * b)

    def _calculate_chain_score(self):
        _, _, score = self._calculate_chain_score_components()
        return score

    def resolve_chains_synchronously(self, spawn_next=False, capture_visuals=False):
        chain_results = []
        self.state = "animate"
        self._begin_chain_resolution()

        while True:
            self.field.drop_puyo()
            vanish_groups = self.field.get_vanish_groups()
            if not vanish_groups:
                break

            self.vanish_groups = vanish_groups
            self.vanish_coords = set()
            for group in vanish_groups:
                self.vanish_coords.update(group)

            a, b, chain_score = self._calculate_chain_score_components()
            all_clear_bonus_score = self._consume_all_clear_bonus()
            step_score = chain_score + all_clear_bonus_score
            chain_results.append(
                {
                    "chain_index": self.chain_count + 1,
                    "groups": [set(group) for group in vanish_groups],
                    "vanished": set(self.vanish_coords),
                    "vanished_count": len(self.vanish_coords),
                    "base": a,
                    "bonus": b,
                    "score": step_score,
                    "all_clear_bonus_score": all_clear_bonus_score,
                    "board": self._snapshot_field_colors() if capture_visuals else None,
                }
            )
            self.score += step_score
            self.field.remove_puyos(self.vanish_coords)
            self.chain_count += 1

        self._finish_chain_resolution()
        self._reset_animation_data()
        if spawn_next:
            self.spawn_puyo()
        else:
            self.state = "ready"
        return chain_results

    def place_current_pair_and_resolve(self, axis_x, rot, spawn_next=True, capture_visuals=False):
        if self.state == "ready":
            self.spawn_puyo()
        if self.state != "control" or self.current_puyo_1 is None or self.current_puyo_2 is None:
            return None

        landing_y = self.find_landing_y(axis_x, rot)
        if landing_y is None:
            return None

        score_before = self.score
        self.puyo_x = axis_x
        self.puyo_y = landing_y
        self.puyo_rot = rot
        self._reset_control_counters()
        self._reset_drop_bonus_state()
        self.lock_puyo()
        self.field.drop_puyo()
        placement_board = self._snapshot_field_colors() if capture_visuals else None
        chain_results = self.resolve_chains_synchronously(
            spawn_next=spawn_next,
            capture_visuals=capture_visuals,
        )

        return {
            "axis_x": axis_x,
            "axis_y": landing_y,
            "rotation": rot,
            "score_delta": self.score - score_before,
            "chain_count": len(chain_results),
            "chains": chain_results,
            "placement_board": placement_board,
            "game_over": self.game_over,
        }

    def get_score_display_text(self):
        if (
            self.state == "animate"
            and self.animation_state == "vanish_flash"
            and self.chain_display_a is not None
            and self.chain_display_b is not None
        ):
            return f"{self.chain_display_a:>3}x{self.chain_display_b:>3}".rjust(8)
        return f"{self.score:08d}"

    def get_ghost_cells(self):
        ghost_pos = self.get_ghost_axis_position()
        if ghost_pos is None:
            return []

        ghost_x, ghost_y = ghost_pos
        return self.get_landing_cells(
            ghost_x,
            self.puyo_rot,
            (self.current_puyo_1.color, self.current_puyo_2.color),
            axis_y=ghost_y,
        )

    def get_landing_cells(self, axis_x, rotation, pair_colors, axis_y=None):
        """Return the independently settled cells for a placement preview."""

        landing_y = self.find_landing_y(axis_x, rotation) if axis_y is None else axis_y
        if landing_y is None or pair_colors is None:
            return []

        ox, oy = self.get_sub_puyo_offset(rotation)
        cells = [
            (axis_x, landing_y, pair_colors[0]),
            (axis_x + ox, landing_y + oy, pair_colors[1]),
        ]
        in_bounds = [
            (x, y, color)
            for x, y, color in cells
            if 0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT
        ]
        return self._settle_ghost_cells(in_bounds)

    def _settle_ghost_cells(self, ghost_cells):
        settled = list(ghost_cells)
        while True:
            moved = False
            occupied = {(x, y) for x, y, _ in settled}
            next_cells = []
            for x, y, color in sorted(settled, key=lambda cell: cell[1]):
                occupied.remove((x, y))
                target_y = y - 1
                can_drop = (
                    target_y >= 0
                    and self.field.get_puyo(x, target_y).is_empty()
                    and (x, target_y) not in occupied
                )
                if can_drop:
                    y = target_y
                    moved = True
                occupied.add((x, y))
                next_cells.append((x, y, color))

            settled = next_cells
            if not moved:
                return settled

    def get_ghost_highlight_coords(self):
        if self.state != "control" or self.current_puyo_1 is None or self.current_puyo_2 is None:
            return set()

        ghost_cells = self.get_ghost_cells()
        if not ghost_cells:
            return set()

        ghost_color_cells = {}
        for x, y, color in ghost_cells:
            if not (0 <= y < VISIBLE_HEIGHT):
                continue
            if color not in (
                PuyoColor.RED,
                PuyoColor.BLUE,
                PuyoColor.GREEN,
                PuyoColor.YELLOW,
                PuyoColor.PURPLE,
            ):
                continue
            ghost_color_cells[(x, y)] = color

        if not ghost_color_cells:
            return set()

        visited = set()
        highlight = set()

        for (sx, sy), s_color in ghost_color_cells.items():
            if (sx, sy) in visited:
                continue

            stack = [(sx, sy)]
            group = set()
            has_ghost = False
            has_field = False

            while stack:
                x, y = stack.pop()
                if (x, y) in group:
                    continue
                if not (0 <= x < GRID_WIDTH and 0 <= y < VISIBLE_HEIGHT):
                    continue

                cell_color = ghost_color_cells.get((x, y))
                if cell_color is None:
                    field_puyo = self.field.get_puyo(x, y)
                    if field_puyo.is_empty() or field_puyo.color != s_color:
                        continue
                elif cell_color != s_color:
                    continue

                group.add((x, y))
                if (x, y) in ghost_color_cells:
                    has_ghost = True
                elif not self.field.get_puyo(x, y).is_empty():
                    has_field = True

                for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    stack.append((x + dx, y + dy))

            visited.update(group)
            if len(group) < 4 or (not has_ghost) or (not has_field):
                continue

            for x, y in group:
                if (x, y) not in ghost_color_cells and not self.field.get_puyo(x, y).is_empty():
                    highlight.add((x, y))

        return highlight

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

        vanish_groups = self.field.get_vanish_groups()
        if vanish_groups:
            self.vanish_groups = vanish_groups
            self.vanish_coords = set()
            for group in vanish_groups:
                self.vanish_coords.update(group)
            self.chain_display_a, self.chain_display_b, self.chain_display_score = self._calculate_chain_score_components()
            self.chain_display_all_clear_bonus = self._consume_all_clear_bonus()
            self.chain_display_score += self.chain_display_all_clear_bonus
            self.animation_state = "vanish_flash"
            self.animation_timer = 0.0
            return

        self._finish_chain_resolution()
        self.spawn_puyo()

    def advance_animation(self, delta_time):
        if self.state != "animate":
            return

        remaining_time = max(0.0, delta_time)

        while self.state == "animate":
            if self.animation_state == "resolve":
                self._resolve_animation_step()
                if self.animation_state == "resolve":
                    return
                continue

            if self.animation_state == "vanish_flash":
                elapsed = self.animation_timer + remaining_time
                if elapsed < VANISH_FLASH_SECONDS:
                    self.animation_timer = elapsed
                    return

                remaining_time = max(0.0, elapsed - VANISH_FLASH_SECONDS)
                self.animation_timer = VANISH_FLASH_SECONDS
                if self.chain_display_score <= 0:
                    self.chain_display_a, self.chain_display_b, self.chain_display_score = self._calculate_chain_score_components()
                self.score += self.chain_display_score
                self.field.remove_puyos(self.vanish_coords)
                self.chain_count += 1
                self.vanish_coords = set()
                self.vanish_groups = []
                self.chain_display_a = None
                self.chain_display_b = None
                self.chain_display_score = 0
                self.chain_display_all_clear_bonus = 0
                self.animation_state = "resolve"
                self.animation_timer = 0.0
                continue

            if self.animation_state == "drop_tween":
                elapsed = self.animation_timer + remaining_time
                if CHAIN_DROP_TWEEN_SECONDS > 0:
                    self.drop_tween_progress = min(1.0, elapsed / CHAIN_DROP_TWEEN_SECONDS)
                else:
                    self.drop_tween_progress = 1.0

                if elapsed < CHAIN_DROP_TWEEN_SECONDS:
                    self.animation_timer = elapsed
                    return

                remaining_time = max(0.0, elapsed - CHAIN_DROP_TWEEN_SECONDS)
                self.animation_timer = CHAIN_DROP_TWEEN_SECONDS
                self.drop_tween_progress = 1.0
                self.drop_tween_grid_before = None
                self.drop_tween_static_cells = []
                self.drop_tween_motions = []
                self.animation_state = "resolve"
                self.animation_timer = 0.0
                continue

            return

    def resolve_world(self):
        # Compatibility wrapper for older call sites.
        self.advance_animation(max(VANISH_FLASH_SECONDS, CHAIN_DROP_TWEEN_SECONDS))
