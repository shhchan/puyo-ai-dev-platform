"""Deterministic fixed-tick headless Puyo simulation."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence

from .constants import (
    Action,
    LOCK_FRAME_LIMIT,
    REALTIME_ATTACK_DELAY_TICKS,
    REALTIME_CHAIN_DROP_TWEEN_TICKS,
    REALTIME_DAS_INITIAL_DELAY_TICKS,
    REALTIME_DAS_REPEAT_INTERVAL_TICKS,
    REALTIME_GRAVITY_INTERVAL_TICKS,
    REALTIME_SOFT_DROP_REPEAT_INTERVAL_TICKS,
    REALTIME_TICK_RATE,
    REALTIME_VANISH_FLASH_TICKS,
)
from .game import GameState


HOLD_ACTIONS = (Action.LEFT, Action.RIGHT, Action.DOWN)
ROTATION_ACTIONS = (Action.ROTATE_LEFT, Action.ROTATE_RIGHT)


@dataclass(frozen=True)
class RealtimeTimingConfig:
    """All fixed-step timing values used by realtime headless code."""

    tick_rate: int = REALTIME_TICK_RATE
    gravity_interval_ticks: int = REALTIME_GRAVITY_INTERVAL_TICKS
    das_initial_delay_ticks: int = REALTIME_DAS_INITIAL_DELAY_TICKS
    das_repeat_interval_ticks: int = REALTIME_DAS_REPEAT_INTERVAL_TICKS
    soft_drop_repeat_interval_ticks: int = REALTIME_SOFT_DROP_REPEAT_INTERVAL_TICKS
    vanish_flash_ticks: int = REALTIME_VANISH_FLASH_TICKS
    chain_drop_tween_ticks: int = REALTIME_CHAIN_DROP_TWEEN_TICKS
    lock_frame_limit: int = LOCK_FRAME_LIMIT
    attack_delay_ticks: int = REALTIME_ATTACK_DELAY_TICKS

    @property
    def tick_seconds(self) -> float:
        return 1.0 / float(self.tick_rate)

    def repeat_delay(self, action: Action) -> int:
        if action == Action.DOWN:
            return self.soft_drop_repeat_interval_ticks
        return self.das_initial_delay_ticks

    def repeat_interval(self, action: Action) -> int:
        if action == Action.DOWN:
            return self.soft_drop_repeat_interval_ticks
        return self.das_repeat_interval_ticks


DEFAULT_REALTIME_TIMING = RealtimeTimingConfig()


@dataclass(frozen=True)
class TickInput:
    """Low-level input edges applied at one deterministic tick."""

    press: tuple[Action, ...] = ()
    release: tuple[Action, ...] = ()

    @classmethod
    def from_names(
        cls,
        *,
        press: Sequence[str] = (),
        release: Sequence[str] = (),
    ) -> "TickInput":
        return cls(
            press=tuple(Action[name] for name in press),
            release=tuple(Action[name] for name in release),
        )

    def to_json(self) -> dict[str, list[str]]:
        return {
            "press": [action.name for action in self.press],
            "release": [action.name for action in self.release],
        }


@dataclass(frozen=True)
class RealtimeEvent:
    """A deterministic event emitted while advancing a tick."""

    type: str
    tick: int
    data: Mapping[str, object]


@dataclass(frozen=True)
class RealtimeSnapshot:
    tick: int
    state: str
    score: int
    chain_count: int
    game_over: bool
    active_pair: tuple[str | None, str | None]
    active_position: tuple[int, int, str] | None
    held_actions: tuple[str, ...]
    next_queue: tuple[tuple[str, str], ...]
    board: tuple[tuple[str, ...], ...]

    def stable_dict(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "state": self.state,
            "score": self.score,
            "chain_count": self.chain_count,
            "game_over": self.game_over,
            "active_pair": self.active_pair,
            "active_position": self.active_position,
            "held_actions": self.held_actions,
            "next_queue": self.next_queue,
            "board": self.board,
        }

    def hash(self) -> str:
        payload = json.dumps(self.stable_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RealtimeStepResult:
    tick: int
    tick_input: TickInput
    fired_actions: tuple[Action, ...]
    state_before: str
    state_after: str
    score_delta: int
    events: tuple[RealtimeEvent, ...]
    snapshot_hash: str


class RealtimeHeadlessSimulator:
    """Run ``GameState`` from press/release input on a fixed tick clock."""

    def __init__(
        self,
        seed: int | None = None,
        game_state: GameState | None = None,
        timing: RealtimeTimingConfig | None = None,
        *,
        auto_spawn: bool = True,
    ):
        self.game = game_state or GameState(seed=seed)
        self.timing = timing or DEFAULT_REALTIME_TIMING
        self.tick = 0
        self.held_actions: set[Action] = set()
        self._next_repeat_tick: dict[Action, int | None] = {action: None for action in HOLD_ACTIONS}
        self._next_gravity_tick = self.timing.gravity_interval_ticks
        self._resolution_score_start: int | None = self.game.score if self.game.state == "animate" else None
        self.last_resolution_score_delta = 0
        self.last_resolution_chain_count = 0

        if auto_spawn and self.game.state == "ready":
            self.game.spawn_puyo()

    def clone(self) -> "RealtimeHeadlessSimulator":
        return copy.deepcopy(self)

    def snapshot(self) -> RealtimeSnapshot:
        game = self.game
        active_pair = (
            game.current_puyo_1.color.name if game.current_puyo_1 is not None else None,
            game.current_puyo_2.color.name if game.current_puyo_2 is not None else None,
        )
        active_position = None
        if game.current_puyo_1 is not None and game.current_puyo_2 is not None:
            active_position = (game.puyo_x, game.puyo_y, game.puyo_rot.name)
        board = tuple(tuple(cell.name for cell in row) for row in game.field.to_color_grid())
        next_queue = tuple(
            (first.color.name, second.color.name)
            for first, second in game.next_puyo_queue
        )
        return RealtimeSnapshot(
            tick=self.tick,
            state=game.state,
            score=game.score,
            chain_count=game.chain_count,
            game_over=game.game_over,
            active_pair=active_pair,
            active_position=active_position,
            held_actions=tuple(sorted(action.name for action in self.held_actions)),
            next_queue=next_queue,
            board=board,
        )

    def state_hash(self) -> str:
        return self.snapshot().hash()

    def step(self, tick_input: TickInput | None = None) -> RealtimeStepResult:
        tick_input = tick_input or TickInput()
        current_tick = self.tick
        state_before = self.game.state
        score_before = self.game.score
        events: list[RealtimeEvent] = []

        fired_actions = self._collect_fired_actions(current_tick, tick_input)

        if self.game.state == "animate":
            self.game.advance_animation(self.timing.tick_seconds)
        elif self.game.state == "countdown":
            self.game.advance_countdown(self.timing.tick_seconds)
        else:
            self.game.update(fired_actions, held_actions={action: True for action in self.held_actions})
            self._apply_gravity_if_due(current_tick)

        if state_before == "control" and self.game.state == "animate":
            self._resolution_score_start = score_before
            events.append(
                RealtimeEvent(
                    type="lock",
                    tick=current_tick,
                    data={
                        "axis_x": self.game.puyo_x,
                        "axis_y": self.game.puyo_y,
                        "rotation": self.game.puyo_rot.name,
                    },
                )
            )

        if state_before == "animate" and self.game.state != "animate":
            score_start = self._resolution_score_start
            score_delta = self.game.score - (score_start if score_start is not None else score_before)
            self.last_resolution_score_delta = score_delta
            self.last_resolution_chain_count = self.game.chain_count
            events.append(
                RealtimeEvent(
                    type="resolution_complete",
                    tick=current_tick,
                    data={
                        "score_delta": score_delta,
                        "chain_count": self.game.chain_count,
                        "game_over": self.game.game_over,
                    },
                )
            )
            self._resolution_score_start = None

        self.tick += 1
        snapshot_hash = self.state_hash()
        return RealtimeStepResult(
            tick=current_tick,
            tick_input=tick_input,
            fired_actions=tuple(fired_actions),
            state_before=state_before,
            state_after=self.game.state,
            score_delta=self.game.score - score_before,
            events=tuple(events),
            snapshot_hash=snapshot_hash,
        )

    def advance_ticks(
        self,
        count: int,
        inputs_by_tick: Mapping[int, TickInput] | None = None,
    ) -> list[RealtimeStepResult]:
        inputs_by_tick = inputs_by_tick or {}
        results = []
        for _ in range(int(count)):
            results.append(self.step(inputs_by_tick.get(self.tick)))
        return results

    def run_until_control_or_game_over(self, max_ticks: int = 10_000) -> list[RealtimeStepResult]:
        results = []
        for _ in range(max_ticks):
            if self.game.state == "control" or self.game.game_over:
                break
            results.append(self.step())
        return results

    def _collect_fired_actions(self, current_tick: int, tick_input: TickInput) -> list[Action]:
        for action in tick_input.release:
            if action in HOLD_ACTIONS:
                self.held_actions.discard(action)
                self._next_repeat_tick[action] = None

        fired: list[Action] = []
        just_pressed_hold: set[Action] = set()
        one_shot_presses: list[Action] = []
        for action in tick_input.press:
            if action in HOLD_ACTIONS:
                if action not in self.held_actions:
                    self.held_actions.add(action)
                    self._next_repeat_tick[action] = current_tick + self.timing.repeat_delay(action)
                    just_pressed_hold.add(action)
            else:
                one_shot_presses.append(action)

        for action in HOLD_ACTIONS:
            should_fire = action in just_pressed_hold
            next_tick = self._next_repeat_tick[action]
            if action in self.held_actions and next_tick is not None and current_tick >= next_tick:
                while self._next_repeat_tick[action] is not None and self._next_repeat_tick[action] <= current_tick:
                    self._next_repeat_tick[action] += self.timing.repeat_interval(action)
                should_fire = True
            if should_fire:
                fired.append(action)

        if Action.LEFT in self.held_actions and Action.RIGHT in self.held_actions:
            fired = [action for action in fired if action not in (Action.LEFT, Action.RIGHT)]

        ordered = []
        if Action.START in one_shot_presses:
            ordered.append(Action.START)
        for action in (Action.LEFT, Action.RIGHT, Action.DOWN):
            if action in fired:
                ordered.append(action)
        for action in one_shot_presses:
            if action in ROTATION_ACTIONS or action == Action.QUIT:
                ordered.append(action)
        return ordered

    def _apply_gravity_if_due(self, current_tick: int) -> None:
        if self.game.state != "control":
            return
        if current_tick < self._next_gravity_tick:
            return

        while current_tick >= self._next_gravity_tick:
            self._next_gravity_tick += self.timing.gravity_interval_ticks

        if self.game.can_move(0, -1, self.game.puyo_rot):
            self.game.step_gravity()
            self.game._update_ground_lock()


def pulse_input(action: Action) -> tuple[TickInput, TickInput]:
    """Return press/release ticks for one unbuffered input pulse."""

    return (TickInput(press=(action,)), TickInput(release=(action,)))


def inputs_from_action_pulses(actions: Sequence[Action]) -> tuple[TickInput, ...]:
    inputs: list[TickInput] = []
    for action in actions:
        inputs.extend(pulse_input(action))
    return tuple(inputs)
