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


def _geometric_paths(source_game, actions, max_expanded_states, repair_budget=None, transition_cache=None):
    """One bounded BFS supplies candidate input paths, not execution guarantees."""
    targets = {
        action: (action.axis_x, source_game.find_landing_y(action.axis_x, action.rotation), action.rotation)
        for action in actions
    }
    remaining = {target for target in targets.values() if target[1] is not None}
    found = {}
    start = (source_game.puyo_x, source_game.puyo_y, source_game.puyo_rot,
             source_game.blocked_rotate_input_count)
    queue = deque([start])
    previous = {start: None}
    while queue and remaining:
        state = queue.popleft()
        target = state[:3]
        if target in remaining:
            found[target] = tuple(_reconstruct_actions(previous, state))
            remaining.remove(target)
        if len(previous) > max_expanded_states:
            break
        if repair_budget is not None:
            if repair_budget[0] <= 0:
                break
            repair_budget[0] -= 1
        for action in PLANNER_ACTIONS:
            next_state = _geometry_transition(source_game, state, action, transition_cache)
            if next_state is None or next_state in previous:
                continue
            previous[next_state] = (state, action)
            queue.append(next_state)
    return {action: (target, found.get(target)) for action, target in targets.items()}


def _geometry_transition(game, state, action, cache):
    if cache is None:
        return _transition_piece_state(game, state, action)
    key = (state, action)
    if key not in cache:
        cache[key] = _transition_piece_state(game, state, action)
    return cache[key]


class _ControlProbeGame(GameState):
    """Use authoritative control methods; copy the shared field only at lock."""

    def lock_puyo(self):
        # lock_puyo replaces grid cells; it never mutates existing Puyo values.
        # The probe stops at lock and never runs animation/chain resolution.
        self.field = copy.copy(self.field)
        self.field.grid = [row[:] for row in self.field.grid]
        locked = super().lock_puyo()
        if locked:
            self._planner_lock = (self.puyo_x, self.puyo_y, self.puyo_rot)
        return locked


def _control_probe(source, timing):
    if isinstance(source, RealtimeHeadlessSimulator):
        # The absolute gravity deadline and repeat state are part of the root.
        probe = copy.copy(source)
        probe.held_actions = set(source.held_actions)
        probe._next_repeat_tick = dict(source._next_repeat_tick)
    else:
        probe = RealtimeHeadlessSimulator(game_state=copy.deepcopy(_coerce_game(source)), timing=timing)
    game = object.__new__(_ControlProbeGame)
    game.__dict__ = probe.game.__dict__.copy()
    game._planner_lock = None
    probe.game = game
    return probe


def _step_control_probe(probe, tick_input):
    """Mirror the control branch of step without snapshots or chain animation."""
    fired = probe._collect_fired_actions(probe.tick, tick_input)
    probe.game.update(fired, held_actions={a: True for a in probe.held_actions})
    probe._apply_gravity_if_due(probe.tick)
    probe.tick += 1


def _verify_path(source, action, target, path, *, timing, max_expanded_states, repair_budget, repair_cache, all_actions, transition_cache):
    probe = _control_probe(source, timing)
    inputs, actions = [], []
    remaining = deque(path)
    repairs = 0
    # At most two geometry repairs; rejection is conservative, never an
    # unverified success. All actual motion uses the live clock and counters.
    while remaining and probe.game.state == "control":
        current = (probe.game.puyo_x, probe.game.puyo_y, probe.game.puyo_rot,
                   probe.game.blocked_rotate_input_count)
        step_action = remaining.popleft()
        predicted = _geometry_transition(probe.game, current, step_action, transition_cache)
        actions.append(step_action)
        for tick_input in inputs_from_action_pulses((step_action,)):
            if probe.game.state != "control":
                break
            inputs.append(tick_input)
            _step_control_probe(probe, tick_input)
        actual = (probe.game.puyo_x, probe.game.puyo_y, probe.game.puyo_rot,
                  probe.game.blocked_rotate_input_count)
        if probe.game.state == "control" and actual != predicted:
            if repairs >= 2:
                return None
            # A common prefix can cross the gravity boundary for many roots.
            # Share only its geometric search within this immutable-board call;
            # every timed witness is still checked against its own live clock.
            if actual not in repair_cache:
                repair_cache[actual] = _geometric_paths(
                    probe.game, all_actions, max_expanded_states, repair_budget, transition_cache
                )
            repaired_target, repaired = repair_cache[actual][action]
            if repaired is None or repaired_target != target:
                return None
            remaining = deque(repaired)
            repairs += 1
    for _ in range(probe.timing.lock_frame_limit + 2):
        if probe.game.state != "control":
            break
        tick_input = TickInput()
        inputs.append(tick_input)
        _step_control_probe(probe, tick_input)
    if probe.game._planner_lock != target:
        return None
    # Never leave an input pressed across the next pair, even if lock occurred
    # on a press tick before that pulse's usual release tick.
    if probe.held_actions:
        inputs.append(TickInput(release=tuple(sorted(probe.held_actions, key=lambda a: a.value))))
    return PlannedPlacement(action, True, tuple(inputs), tuple(actions), target[1])


def _plans_for_actions(source, actions, *, timing=None, max_expanded_states=2000):
    timing = source.timing if isinstance(source, RealtimeHeadlessSimulator) else (timing or DEFAULT_REALTIME_TIMING)
    probe = _control_probe(source, timing)
    if probe.game.state != "control" or probe.game.game_over:
        return {}
    actions = tuple(actions)
    transition_cache = {}
    paths = _geometric_paths(probe.game, actions, max_expanded_states, transition_cache=transition_cache)
    # One additional expansion budget for the entire batch, in action order.
    # Exhaustion may omit a valid root but can never admit an unverified root.
    repair_budget = [max(0, max_expanded_states)]
    repair_cache = {}
    return {
        action: _verify_path(probe, action, target, path, timing=timing,
                             max_expanded_states=max_expanded_states, repair_budget=repair_budget,
                             repair_cache=repair_cache, all_actions=actions, transition_cache=transition_cache)
        for action, (target, path) in paths.items() if path is not None
    }


def plan_placement_action(
    game_or_simulator: GameState | HeadlessPuyoSimulator | RealtimeHeadlessSimulator,
    action: PlacementAction | tuple[int, Direction],
    *,
    timing: RealtimeTimingConfig | None = None,
    max_expanded_states: int = 2_000,
) -> PlannedPlacement:
    """Return a bounded input witness whose first actual lock matches the root.

    Realtime sources retain their clock, gravity deadline, held/repeat state
    and lock counters. GameState/headless sources start a new realtime clock.
    A geometrically reachable target without a verified timed path is rejected.
    """
    if not isinstance(action, PlacementAction):
        action = PlacementAction(action[0], action[1])
    result = _plans_for_actions(game_or_simulator, (action,), timing=timing,
                                max_expanded_states=max_expanded_states).get(action)
    if result is not None:
        return result
    game = _coerce_game(game_or_simulator)
    reason = ("target placement is not legal on the current field"
              if game.find_landing_y(action.axis_x, action.rotation) is None
              else "no verified timed input path reached the target lock")
    return PlannedPlacement(action, False, (), (), None, reason)


def reachable_placement_actions(
    game_or_simulator: GameState | HeadlessPuyoSimulator | RealtimeHeadlessSimulator,
    actions: Iterable[PlacementAction],
    *,
    timing: RealtimeTimingConfig | None = None,
    max_expanded_states: int = 2_000,
    plan_results: dict[PlacementAction, PlannedPlacement] | None = None,
) -> tuple[bool, ...]:
    """Return verified roots, optionally retaining this call's timed witnesses.

    The output mapping is cleared on every call. Witnesses belong to this exact
    live state; callers must not carry them across simulation ticks or mutations.
    """
    actions = tuple(actions)
    plans = _plans_for_actions(game_or_simulator, actions, timing=timing,
                               max_expanded_states=max_expanded_states)
    if plan_results is not None:
        plan_results.clear()
        plan_results.update((action, plan) for action, plan in plans.items() if plan is not None)
    return tuple(plans.get(action) is not None for action in actions)


def planned_inputs_reach_target(simulator, plan, *, start_index=0):
    """Check the queued suffix, including its releases, against the live clock."""
    if not plan.reachable or not 0 <= start_index <= len(plan.inputs):
        return False
    probe = _control_probe(simulator, simulator.timing)
    target = (plan.action.axis_x, plan.expected_axis_y, plan.action.rotation)
    for tick_input in plan.inputs[start_index:]:
        if probe.game.state != "control":
            break
        _step_control_probe(probe, tick_input)
    return probe.game._planner_lock == target


def execute_planned_placement(
    game_or_simulator: GameState | HeadlessPuyoSimulator | RealtimeHeadlessSimulator,
    plan: PlannedPlacement,
    *,
    timing: RealtimeTimingConfig | None = None,
    max_resolution_ticks: int = 2_000,
) -> RealtimeHeadlessSimulator:
    """Run a planned input sequence on a copied realtime simulator."""

    if isinstance(game_or_simulator, RealtimeHeadlessSimulator):
        sim = game_or_simulator.clone()
    else:
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
    probe = object.__new__(type(base_game))
    probe.__dict__ = base_game.__dict__.copy()
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
