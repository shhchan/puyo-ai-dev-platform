"""Immutable pure-Python placement kernel for long-horizon search.

The compact representation uses one Python ``int`` bitboard per Puyo color.
It intentionally follows the repository's :class:`HeadlessPuyoSimulator`
semantics, including its two hidden rows and row-14 gravity behavior.  Ama
v2.0.1 inspired the representation boundary, but this implementation is
original and does not copy Ama source code.

Future tsumo queues are deliberately outside :class:`CompactSearchState`.
Callers must combine the state with :class:`CompactTranspositionKey`, whose
required scenario/cursor/depth fields prevent an incomplete transposition key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from puyo_env.actions import ACTION_TO_INDEX, PLACEMENT_ACTIONS, action_to_placement
from src.core.constants import (
    ALL_CLEAR_BONUS_SCORE,
    CHAIN_BONUS_TABLE,
    COLOR_BONUS_TABLE,
    GRID_HEIGHT,
    GRID_WIDTH,
    VISIBLE_HEIGHT,
    Direction,
    PuyoColor,
    get_connection_bonus,
)
from src.core.headless import HeadlessPuyoSimulator, PlacementAction


COMPACT_SEARCH_SCHEMA_VERSION = "puyo.compact_search_state.v1"
_BITS_PER_BOARD = GRID_WIDTH * GRID_HEIGHT
_BYTES_PER_PLANE = (_BITS_PER_BOARD + 7) // 8
_FULL_BOARD_MASK = (1 << _BITS_PER_BOARD) - 1
_VISIBLE_MASK = (1 << (GRID_WIDTH * VISIBLE_HEIGHT)) - 1
_GRAVITY_HEIGHT = GRID_HEIGHT - 1
_ROW_14_MASK = sum(1 << ((GRID_HEIGHT - 1) * GRID_WIDTH + x) for x in range(GRID_WIDTH))
_PLANE_COLORS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
    PuyoColor.PURPLE,
    PuyoColor.OJAMA,
)
_NORMAL_COLORS = _PLANE_COLORS[:-1]
_PLANE_INDEX = {color: index for index, color in enumerate(_PLANE_COLORS)}
_EMPTY_PLANES = (0,) * len(_PLANE_COLORS)
_DIRECTION_OFFSETS = {
    Direction.UP: (0, 1),
    Direction.RIGHT: (1, 0),
    Direction.DOWN: (0, -1),
    Direction.LEFT: (-1, 0),
}


def _cell_bit(x: int, y: int) -> int:
    return 1 << (y * GRID_WIDTH + x)


def _occupied_mask(planes: Sequence[int]) -> int:
    occupied = 0
    for plane in planes:
        occupied |= int(plane)
    return occupied


def _column_heights(occupied: int) -> tuple[int, ...]:
    heights = []
    for x in range(GRID_WIDTH):
        height = 0
        for y in range(GRID_HEIGHT - 1, -1, -1):
            if occupied & _cell_bit(x, y):
                height = y + 1
                break
        heights.append(height)
    return tuple(heights)


@dataclass(frozen=True, slots=True)
class CompactSearchState:
    """Immutable board and lifecycle state used by placement-level search.

    ``planes`` contains RED, BLUE, GREEN, YELLOW, PURPLE, and OJAMA masks in
    that order.  All 14 rows participate in equality, hashing, and canonical
    serialization.  The current/future pair is not part of this state.
    """

    planes: tuple[int, ...] = _EMPTY_PLANES
    all_clear_bonus_pending: bool = False
    game_over: bool = False
    score: int = 0
    last_chain_end_score: int = 0
    column_heights: tuple[int, ...] = field(init=False)
    _occupied: int = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        planes = tuple(int(value) for value in self.planes)
        if len(planes) != len(_PLANE_COLORS):
            raise ValueError(f"planes must contain {len(_PLANE_COLORS)} masks")
        if any(value < 0 or value & ~_FULL_BOARD_MASK for value in planes):
            raise ValueError("plane mask contains a cell outside the 6x14 board")
        occupied = 0
        for value in planes:
            if occupied & value:
                raise ValueError("compact color planes must not overlap")
            occupied |= value
        if int(self.score) < 0 or int(self.last_chain_end_score) < 0:
            raise ValueError("score lifecycle values must be non-negative")
        if int(self.last_chain_end_score) > int(self.score):
            raise ValueError("last_chain_end_score must not exceed score")
        object.__setattr__(self, "planes", planes)
        object.__setattr__(self, "score", int(self.score))
        object.__setattr__(
            self,
            "last_chain_end_score",
            int(self.last_chain_end_score),
        )
        object.__setattr__(self, "_occupied", occupied)
        object.__setattr__(self, "column_heights", _column_heights(occupied))

    @classmethod
    def empty(cls) -> "CompactSearchState":
        return cls()

    @classmethod
    def from_simulator(
        cls,
        simulator: HeadlessPuyoSimulator,
    ) -> "CompactSearchState":
        planes = [0] * len(_PLANE_COLORS)
        game = simulator.game
        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                color = game.field.grid[y][x].color
                index = _PLANE_INDEX.get(color)
                if index is not None:
                    planes[index] |= _cell_bit(x, y)
                elif color != PuyoColor.EMPTY:
                    raise ValueError(f"unsupported board color: {color!r}")
        return cls(
            planes=tuple(planes),
            all_clear_bonus_pending=bool(game.all_clear_bonus_pending),
            game_over=bool(game.game_over),
            score=int(game.score),
            last_chain_end_score=int(game.last_chain_end_score),
        )

    @property
    def occupied_mask(self) -> int:
        return self._occupied

    @property
    def cell_count(self) -> int:
        return self._occupied.bit_count()

    def color_at(self, x: int, y: int) -> PuyoColor:
        if not (0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT):
            return PuyoColor.WALL
        bit = _cell_bit(x, y)
        for index, plane in enumerate(self.planes):
            if plane & bit:
                return _PLANE_COLORS[index]
        return PuyoColor.EMPTY

    def to_color_grid(self) -> tuple[tuple[PuyoColor, ...], ...]:
        return _grid_from_planes(self.planes)

    def to_bytes(self) -> bytes:
        """Return a stable byte representation for digests and parity checks."""

        payload = bytearray(b"CSK1")
        for plane in self.planes:
            payload.extend(int(plane).to_bytes(_BYTES_PER_PLANE, "little"))
        flags = int(bool(self.all_clear_bonus_pending))
        flags |= int(bool(self.game_over)) << 1
        payload.append(flags)
        payload.extend(int(self.score).to_bytes(8, "little"))
        payload.extend(int(self.last_chain_end_score).to_bytes(8, "little"))
        return bytes(payload)


@dataclass(frozen=True, slots=True)
class CompactSearchSnapshot:
    """Adapter output that keeps the current pair outside the compact state."""

    state: CompactSearchState
    current_pair: tuple[PuyoColor, PuyoColor] | None

    @classmethod
    def from_simulator(
        cls,
        simulator: HeadlessPuyoSimulator,
    ) -> "CompactSearchSnapshot":
        game = simulator.game
        pair = None
        if game.current_puyo_1 is not None and game.current_puyo_2 is not None:
            pair = (game.current_puyo_1.color, game.current_puyo_2.color)
        return cls(CompactSearchState.from_simulator(simulator), pair)


@dataclass(frozen=True, slots=True)
class CompactTranspositionKey:
    """Complete TT key for externally-owned future-pair cursors.

    ``scenario_id``, ``pair_cursor``, and ``depth`` are required on purpose.
    A caller cannot construct a key from the board alone and accidentally
    merge nodes that consume different future tsumo pairs or search layers.
    """

    state: CompactSearchState
    scenario_id: int
    pair_cursor: int
    depth: int

    def __post_init__(self) -> None:
        if min(int(self.scenario_id), int(self.pair_cursor), int(self.depth)) < 0:
            raise ValueError("transposition coordinates must be non-negative")


@dataclass(frozen=True, slots=True)
class CompactChainStepResult:
    chain_index: int
    vanished_count: int
    garbage_cleared_count: int
    score: int
    base: int
    bonus: int
    groups: tuple[frozenset[tuple[int, int]], ...]
    vanished: frozenset[tuple[int, int]]
    garbage_cleared: frozenset[tuple[int, int]]
    board: tuple[tuple[PuyoColor, ...], ...] | tuple
    all_clear_bonus_score: int


@dataclass(frozen=True, slots=True)
class CompactTransitionResult:
    state: CompactSearchState
    action: PlacementAction
    action_id: int | None
    valid: bool
    axis_y: int | None
    score_delta: int
    attack_score_delta: int
    chain_count: int
    chains: tuple[CompactChainStepResult, ...]
    vanished_count: int
    garbage_cleared_count: int
    placement_board: tuple[tuple[PuyoColor, ...], ...] | tuple
    game_over: bool
    all_clear_achieved: bool
    all_clear_bonus_pending: bool
    all_clear_bonus_consumed: bool
    all_clear_bonus_score: int


def _coerce_pair(pair: Sequence[object]) -> tuple[PuyoColor, PuyoColor]:
    if len(pair) != 2:
        raise ValueError("pair must contain exactly two colors")
    colors = tuple(
        value if isinstance(value, PuyoColor) else getattr(value, "color", value)
        for value in pair
    )
    if any(color not in _NORMAL_COLORS for color in colors):
        raise ValueError("pair colors must be normal color puyos")
    return colors  # type: ignore[return-value]


def _coerce_action(
    action: int | PlacementAction | tuple[int, Direction],
) -> tuple[PlacementAction, int | None]:
    if isinstance(action, int):
        placement = action_to_placement(action)
        return placement, int(action)
    if isinstance(action, PlacementAction):
        return action, ACTION_TO_INDEX.get(action)
    axis_x, rotation = action
    placement = PlacementAction(int(axis_x), rotation)
    return placement, ACTION_TO_INDEX.get(placement)


def _can_place_pair(
    occupied: int,
    axis_x: int,
    axis_y: int,
    rotation: Direction,
) -> bool:
    if not (0 <= axis_x < GRID_WIDTH and 0 <= axis_y < GRID_HEIGHT):
        return False
    axis_bit = _cell_bit(axis_x, axis_y)
    if occupied & axis_bit:
        return False
    offset_x, offset_y = _DIRECTION_OFFSETS[rotation]
    child_x = axis_x + offset_x
    child_y = axis_y + offset_y
    if not (0 <= child_x < GRID_WIDTH and 0 <= child_y < GRID_HEIGHT):
        return False
    return not bool(occupied & _cell_bit(child_x, child_y))


def find_landing_y(
    state: CompactSearchState,
    placement: PlacementAction,
    *,
    start_y: int = 12,
) -> int | None:
    """Match ``GameState.find_landing_y`` for the repository's 14-row field."""

    if state.game_over:
        return None
    axis_x = placement.axis_x
    rotation = placement.rotation
    if not _can_place_pair(state.occupied_mask, axis_x, start_y, rotation):
        return None
    landing_y = start_y
    while _can_place_pair(
        state.occupied_mask,
        axis_x,
        landing_y - 1,
        rotation,
    ):
        landing_y -= 1
    return landing_y


def legal_action_indices(state: CompactSearchState) -> tuple[int, ...]:
    """Return authoritative stable placement IDs without symmetry reduction."""

    if state.game_over:
        return ()
    return tuple(
        action_id
        for action_id, placement in enumerate(PLACEMENT_ACTIONS)
        if find_landing_y(state, placement) is not None
    )


def legal_actions(state: CompactSearchState) -> tuple[PlacementAction, ...]:
    return tuple(PLACEMENT_ACTIONS[index] for index in legal_action_indices(state))


def symmetry_reduced_action_indices(
    state: CompactSearchState,
    pair: Sequence[object],
) -> tuple[int, ...]:
    """Deduplicate equal-pair outcomes while retaining stable root action IDs.

    Deduplication is outcome-based instead of assuming that UP/DOWN and
    LEFT/RIGHT are always interchangeable near hidden rows.  This preserves
    the exact result set even for row-13/14 edge cases.
    """

    colors = _coerce_pair(pair)
    actions = legal_action_indices(state)
    if colors[0] != colors[1]:
        return actions
    selected: list[int] = []
    seen: set[tuple[bytes, int, int, int, bool]] = set()
    for action in actions:
        result = transition(state, colors, action)
        signature = (
            result.state.to_bytes(),
            result.chain_count,
            result.score_delta,
            result.garbage_cleared_count,
            result.game_over,
        )
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(action)
    return tuple(selected)


def _set_plane_cell(
    planes: list[int],
    occupied: int,
    x: int,
    y: int,
    color: PuyoColor,
) -> int:
    bit = _cell_bit(x, y)
    if occupied & bit:
        raise ValueError("cannot place a compact puyo into an occupied cell")
    planes[_PLANE_INDEX[color]] |= bit
    return occupied | bit


def _apply_gravity(planes: Sequence[int]) -> tuple[int, ...]:
    """Compact rows 1-13 while preserving the simulator's static row 14."""

    result = [int(plane) & _ROW_14_MASK for plane in planes]
    for x in range(GRID_WIDTH):
        target_y = 0
        for source_y in range(_GRAVITY_HEIGHT):
            bit = _cell_bit(x, source_y)
            for index, plane in enumerate(planes):
                if int(plane) & bit:
                    result[index] |= _cell_bit(x, target_y)
                    target_y += 1
                    break
    return tuple(result)


def _grid_from_planes(
    planes: Sequence[int],
) -> tuple[tuple[PuyoColor, ...], ...]:
    rows = []
    for y in range(GRID_HEIGHT):
        row = []
        for x in range(GRID_WIDTH):
            bit = _cell_bit(x, y)
            color = PuyoColor.EMPTY
            for index, plane in enumerate(planes):
                if int(plane) & bit:
                    color = _PLANE_COLORS[index]
                    break
            row.append(color)
        rows.append(tuple(row))
    return tuple(rows)


def _normal_plane_index_at(planes: Sequence[int], bit: int) -> int | None:
    for index in range(len(_NORMAL_COLORS)):
        if int(planes[index]) & bit:
            return index
    return None


def _vanish_groups(
    planes: Sequence[int],
) -> tuple[frozenset[tuple[int, int]], ...]:
    visited = 0
    groups: list[frozenset[tuple[int, int]]] = []
    for y in range(VISIBLE_HEIGHT):
        for x in range(GRID_WIDTH):
            bit = _cell_bit(x, y)
            if visited & bit:
                continue
            plane_index = _normal_plane_index_at(planes, bit)
            if plane_index is None:
                continue
            plane = int(planes[plane_index]) & _VISIBLE_MASK
            stack = [(x, y)]
            group: set[tuple[int, int]] = set()
            while stack:
                cell_x, cell_y = stack.pop()
                if not (0 <= cell_x < GRID_WIDTH and 0 <= cell_y < VISIBLE_HEIGHT):
                    continue
                cell_bit = _cell_bit(cell_x, cell_y)
                if not plane & cell_bit or (cell_x, cell_y) in group:
                    continue
                group.add((cell_x, cell_y))
                stack.extend(
                    (
                        (cell_x + 1, cell_y),
                        (cell_x - 1, cell_y),
                        (cell_x, cell_y + 1),
                        (cell_x, cell_y - 1),
                    )
                )
            for cell_x, cell_y in group:
                visited |= _cell_bit(cell_x, cell_y)
            if len(group) >= 4:
                groups.append(frozenset(group))
    return tuple(groups)


def _group_color(
    planes: Sequence[int],
    group: frozenset[tuple[int, int]],
) -> PuyoColor:
    x, y = next(iter(group))
    bit = _cell_bit(x, y)
    index = _normal_plane_index_at(planes, bit)
    if index is None:
        raise AssertionError("vanish group does not contain a normal color")
    return _PLANE_COLORS[index]


def _adjacent_ojama(
    ojama_plane: int,
    vanished: Iterable[tuple[int, int]],
) -> frozenset[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for x, y in vanished:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            target_x, target_y = x + dx, y + dy
            if not (0 <= target_x < GRID_WIDTH and 0 <= target_y < VISIBLE_HEIGHT):
                continue
            if ojama_plane & _cell_bit(target_x, target_y):
                result.add((target_x, target_y))
    return frozenset(result)


def _clear_cells(
    planes: Sequence[int],
    vanished: frozenset[tuple[int, int]],
    garbage: frozenset[tuple[int, int]],
) -> tuple[int, ...]:
    vanished_mask = 0
    for x, y in vanished:
        vanished_mask |= _cell_bit(x, y)
    garbage_mask = 0
    for x, y in garbage:
        garbage_mask |= _cell_bit(x, y)
    result = [int(plane) & ~vanished_mask for plane in planes]
    result[_PLANE_INDEX[PuyoColor.OJAMA]] &= ~garbage_mask
    return tuple(result)


def _invalid_result(
    state: CompactSearchState,
    action: PlacementAction,
    action_id: int | None,
) -> CompactTransitionResult:
    return CompactTransitionResult(
        state=state,
        action=action,
        action_id=action_id,
        valid=False,
        axis_y=None,
        score_delta=0,
        attack_score_delta=0,
        chain_count=0,
        chains=(),
        vanished_count=0,
        garbage_cleared_count=0,
        placement_board=(),
        game_over=state.game_over,
        all_clear_achieved=False,
        all_clear_bonus_pending=state.all_clear_bonus_pending,
        all_clear_bonus_consumed=False,
        all_clear_bonus_score=0,
    )


def transition(
    state: CompactSearchState,
    pair: Sequence[object],
    action: int | PlacementAction | tuple[int, Direction],
    *,
    capture_visuals: bool = False,
) -> CompactTransitionResult:
    """Apply one pair placement and resolve every chain without mutation."""

    colors = _coerce_pair(pair)
    placement, action_id = _coerce_action(action)
    landing_y = find_landing_y(state, placement)
    if landing_y is None:
        return _invalid_result(state, placement, action_id)

    planes = list(state.planes)
    occupied = state.occupied_mask
    occupied = _set_plane_cell(
        planes,
        occupied,
        placement.axis_x,
        landing_y,
        colors[0],
    )
    offset_x, offset_y = _DIRECTION_OFFSETS[placement.rotation]
    _set_plane_cell(
        planes,
        occupied,
        placement.axis_x + offset_x,
        landing_y + offset_y,
        colors[1],
    )
    current_planes = _apply_gravity(planes)
    placement_board = _grid_from_planes(current_planes) if capture_visuals else ()

    pending = bool(state.all_clear_bonus_pending)
    all_clear_consumed = False
    all_clear_bonus_score = 0
    score = int(state.score)
    chain_steps: list[CompactChainStepResult] = []

    while True:
        groups = _vanish_groups(current_planes)
        if not groups:
            break
        vanished = frozenset(cell for group in groups for cell in group)
        garbage = _adjacent_ojama(
            current_planes[_PLANE_INDEX[PuyoColor.OJAMA]],
            vanished,
        )
        chain_index = len(chain_steps) + 1
        chain_bonus = CHAIN_BONUS_TABLE[min(chain_index, len(CHAIN_BONUS_TABLE) - 1)]
        connection_bonus = sum(get_connection_bonus(len(group)) for group in groups)
        colors_cleared = {_group_color(current_planes, group) for group in groups}
        color_bonus = COLOR_BONUS_TABLE.get(len(colors_cleared), 0)
        bonus = max(1, chain_bonus + connection_bonus + color_bonus)
        base = len(vanished) * 10
        step_all_clear_bonus = 0
        if chain_index == 1 and pending:
            pending = False
            all_clear_consumed = True
            step_all_clear_bonus = ALL_CLEAR_BONUS_SCORE
            all_clear_bonus_score = ALL_CLEAR_BONUS_SCORE
        step_score = base * bonus + step_all_clear_bonus
        chain_steps.append(
            CompactChainStepResult(
                chain_index=chain_index,
                vanished_count=len(vanished),
                garbage_cleared_count=len(garbage),
                score=step_score,
                base=base,
                bonus=bonus,
                groups=groups,
                vanished=vanished,
                garbage_cleared=garbage,
                board=(_grid_from_planes(current_planes) if capture_visuals else ()),
                all_clear_bonus_score=step_all_clear_bonus,
            )
        )
        score += step_score
        current_planes = _apply_gravity(_clear_cells(current_planes, vanished, garbage))

    chain_count = len(chain_steps)
    all_clear_achieved = chain_count > 0 and not _occupied_mask(current_planes)
    if all_clear_achieved:
        pending = True
    last_chain_end_score = int(state.last_chain_end_score)
    attack_score_delta = 0
    if chain_count > 0:
        attack_score_delta = max(0, score - last_chain_end_score)
        last_chain_end_score = score

    game_over = bool(_occupied_mask(current_planes) & _cell_bit(2, VISIBLE_HEIGHT - 1))
    next_state = CompactSearchState(
        planes=tuple(current_planes),
        all_clear_bonus_pending=pending,
        game_over=game_over,
        score=score,
        last_chain_end_score=last_chain_end_score,
    )
    return CompactTransitionResult(
        state=next_state,
        action=placement,
        action_id=action_id,
        valid=True,
        axis_y=landing_y,
        score_delta=score - int(state.score),
        attack_score_delta=attack_score_delta,
        chain_count=chain_count,
        chains=tuple(chain_steps),
        vanished_count=sum(step.vanished_count for step in chain_steps),
        garbage_cleared_count=sum(step.garbage_cleared_count for step in chain_steps),
        placement_board=placement_board,
        game_over=game_over,
        all_clear_achieved=all_clear_achieved,
        all_clear_bonus_pending=pending,
        all_clear_bonus_consumed=all_clear_consumed,
        all_clear_bonus_score=all_clear_bonus_score,
    )


__all__ = [
    "COMPACT_SEARCH_SCHEMA_VERSION",
    "CompactChainStepResult",
    "CompactSearchSnapshot",
    "CompactSearchState",
    "CompactTransitionResult",
    "CompactTranspositionKey",
    "find_landing_y",
    "legal_action_indices",
    "legal_actions",
    "symmetry_reduced_action_indices",
    "transition",
]
