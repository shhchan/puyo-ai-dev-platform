from dataclasses import dataclass
from typing import Optional

from .constants import GRID_WIDTH, Direction
from .game import GameState


@dataclass(frozen=True)
class PlacementAction:
    axis_x: int
    rotation: Direction


@dataclass(frozen=True)
class ChainStepResult:
    chain_index: int
    vanished_count: int
    score: int
    base: int
    bonus: int
    groups: tuple
    vanished: frozenset


@dataclass(frozen=True)
class HeadlessStepResult:
    action: PlacementAction
    valid: bool
    axis_y: Optional[int]
    score_delta: int
    chain_count: int
    chains: tuple
    game_over: bool


class HeadlessPuyoSimulator:
    def __init__(self, seed=None, game_state=None):
        self.game = game_state or GameState(seed=seed)
        if self.game.state == "ready":
            self.game.spawn_puyo()

    def legal_actions(self):
        actions = []
        for axis_x in range(GRID_WIDTH):
            for rotation in Direction:
                if self.game.find_landing_y(axis_x, rotation) is not None:
                    actions.append(PlacementAction(axis_x, rotation))
        return actions

    def step(self, action):
        if isinstance(action, PlacementAction):
            axis_x = action.axis_x
            rotation = action.rotation
        else:
            axis_x, rotation = action
            action = PlacementAction(axis_x, rotation)

        raw_result = self.game.place_current_pair_and_resolve(axis_x, rotation, spawn_next=True)
        if raw_result is None:
            return HeadlessStepResult(
                action=action,
                valid=False,
                axis_y=None,
                score_delta=0,
                chain_count=0,
                chains=(),
                game_over=self.game.game_over,
            )

        chains = tuple(
            ChainStepResult(
                chain_index=chain["chain_index"],
                vanished_count=chain["vanished_count"],
                score=chain["score"],
                base=chain["base"],
                bonus=chain["bonus"],
                groups=tuple(frozenset(group) for group in chain["groups"]),
                vanished=frozenset(chain["vanished"]),
            )
            for chain in raw_result["chains"]
        )

        return HeadlessStepResult(
            action=action,
            valid=True,
            axis_y=raw_result["axis_y"],
            score_delta=raw_result["score_delta"],
            chain_count=raw_result["chain_count"],
            chains=chains,
            game_over=raw_result["game_over"],
        )
