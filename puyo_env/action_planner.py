"""Convert placement-level actions into deterministic low-level inputs."""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
from typing import Iterable

from src.core.constants import Action, Direction
from src.core.game import GameState
from src.core.headless import HeadlessPuyoSimulator, PlacementAction
from src.core.realtime import (
    DEFAULT_REALTIME_TIMING,
    RealtimeHeadlessSimulator,
    RealtimeTimingConfig,
    TickInput,
    inputs_from_action_pulses,
)


PLANNER_ACTIONS = (
    Action.LEFT,
    Action.RIGHT,
    Action.ROTATE_LEFT,
    Action.ROTATE_RIGHT,
    Action.DOWN,
)


@dataclass(frozen=True)
class PlannedPlacement:
    action: PlacementAction
    reachable: bool
    inputs: tuple[TickInput, ...]
    high_level_actions: tuple[Action, ...]
    expected_axis_y: int | None
    reason: str | None = None

    @property
    def tick_count(self) -> int:
        return len(self.inputs)


def _coerce_game(game_or_simulator: GameState | HeadlessPuyoSimulator | RealtimeHeadlessSimulator) -> GameState:
    if isinstance(game_or_simulator, GameState):
        return game_or_simulator
    return game_or_simulator.game


def plan_placement_action(
    game_or_simulator: GameState | HeadlessPuyoSimulator | RealtimeHeadlessSimulator,
    action: PlacementAction | tuple[int, Direction],
    *,
    timing: RealtimeTimingConfig | None = None,
    max_expanded_states: int = 2_000,
) -> PlannedPlacement:
    """Plan a press/release input sequence for a placement action.

    The planner searches over the same board geometry and rotation helpers used
    by ``GameState``. It reports unreachable targets instead of manufacturing a
    partial sequence.
    """

    timing = timing or DEFAULT_REALTIME_TIMING
    if not isinstance(action, PlacementAction):
        action = PlacementAction(action[0], action[1])

    source_game = copy.deepcopy(_coerce_game(game_or_simulator))
    if source_game.state == "ready":
        source_game.spawn_puyo()
    if source_game.state != "control":
        return PlannedPlacement(
            action=action,
            reachable=False,
            inputs=(),
            high_level_actions=(),
            expected_axis_y=None,
            reason=f"game state {source_game.state!r} cannot accept control input",
        )

    target_y = source_game.find_landing_y(action.axis_x, action.rotation)
    if target_y is None:
        return PlannedPlacement(
            action=action,
            reachable=False,
            inputs=(),
            high_level_actions=(),
            expected_axis_y=None,
            reason="target placement is not legal on the current field",
        )

    start = (
        source_game.puyo_x,
        source_game.puyo_y,
        source_game.puyo_rot,
        source_game.blocked_rotate_input_count,
    )
    target = (action.axis_x, target_y, action.rotation)
    queue: deque[tuple[int, int, Direction, int]] = deque([start])
    previous: dict[
        tuple[int, int, Direction, int],
        tuple[tuple[int, int, Direction, int], Action] | None,
    ] = {start: None}

    found_state = None
    while queue:
        state = queue.popleft()
        if state[:3] == target:
            found_state = state
            break
        if len(previous) > max_expanded_states:
            break

        for planner_action in PLANNER_ACTIONS:
            next_state = _transition_piece_state(source_game, state, planner_action)
            if next_state is None or next_state in previous:
                continue
            previous[next_state] = (state, planner_action)
            queue.append(next_state)

    if found_state is None:
        return PlannedPlacement(
            action=action,
            reachable=False,
            inputs=(),
            high_level_actions=(),
            expected_axis_y=target_y,
            reason="no low-level path reached the target placement",
        )

    high_level_actions = _reconstruct_actions(previous, found_state)
    inputs = list(inputs_from_action_pulses(high_level_actions))
    inputs.extend(TickInput() for _ in range(timing.lock_frame_limit + 2))
    return PlannedPlacement(
        action=action,
        reachable=True,
        inputs=tuple(inputs),
        high_level_actions=tuple(high_level_actions),
        expected_axis_y=target_y,
    )


def execute_planned_placement(
    game_or_simulator: GameState | HeadlessPuyoSimulator | RealtimeHeadlessSimulator,
    plan: PlannedPlacement,
    *,
    timing: RealtimeTimingConfig | None = None,
    max_resolution_ticks: int = 2_000,
) -> RealtimeHeadlessSimulator:
    """Run a planned input sequence on a copied realtime simulator."""

    timing = timing or DEFAULT_REALTIME_TIMING
    source_game = copy.deepcopy(_coerce_game(game_or_simulator))
    sim = RealtimeHeadlessSimulator(game_state=source_game, timing=timing)
    for tick_input in plan.inputs:
        sim.step(tick_input)
    sim.run_until_control_or_game_over(max_ticks=max_resolution_ticks)
    return sim


def plan_all_legal_actions(
    game_or_simulator: GameState | HeadlessPuyoSimulator | RealtimeHeadlessSimulator,
    actions: Iterable[PlacementAction],
    *,
    timing: RealtimeTimingConfig | None = None,
) -> dict[PlacementAction, PlannedPlacement]:
    return {
        action: plan_placement_action(game_or_simulator, action, timing=timing)
        for action in actions
    }


def _transition_piece_state(
    base_game: GameState,
    state: tuple[int, int, Direction, int],
    action: Action,
) -> tuple[int, int, Direction, int] | None:
    # Movement probes only mutate piece/control counters and read the board.
    # Sharing the field avoids thousands of deep board copies per BFS.
    probe = copy.copy(base_game)
    probe.puyo_x, probe.puyo_y, probe.puyo_rot, probe.blocked_rotate_input_count = state
    probe.vertical_interpolation_progress = 0.0
    probe.floor_kick_horizontal_grace = False

    if action == Action.LEFT:
        if not probe.can_move_horizontal(-1):
            return None
        probe.puyo_x -= 1
    elif action == Action.RIGHT:
        if not probe.can_move_horizontal(1):
            return None
        probe.puyo_x += 1
    elif action == Action.DOWN:
        if not probe.can_move(0, -1, probe.puyo_rot):
            return None
        probe.puyo_y -= 1
    elif action == Action.ROTATE_LEFT:
        before = (probe.puyo_x, probe.puyo_y, probe.puyo_rot, probe.blocked_rotate_input_count)
        probe.handle_rotate_input(False)
        after = (probe.puyo_x, probe.puyo_y, probe.puyo_rot, probe.blocked_rotate_input_count)
        if after == before:
            return None
    elif action == Action.ROTATE_RIGHT:
        before = (probe.puyo_x, probe.puyo_y, probe.puyo_rot, probe.blocked_rotate_input_count)
        probe.handle_rotate_input(True)
        after = (probe.puyo_x, probe.puyo_y, probe.puyo_rot, probe.blocked_rotate_input_count)
        if after == before:
            return None
    else:
        return None

    return (probe.puyo_x, probe.puyo_y, probe.puyo_rot, probe.blocked_rotate_input_count)


def _reconstruct_actions(
    previous: dict[
        tuple[int, int, Direction, int],
        tuple[tuple[int, int, Direction, int], Action] | None,
    ],
    found_state: tuple[int, int, Direction, int],
) -> list[Action]:
    actions: list[Action] = []
    cursor = found_state
    while previous[cursor] is not None:
        prior, action = previous[cursor]
        actions.append(action)
        cursor = prior
    actions.reverse()
    return actions
