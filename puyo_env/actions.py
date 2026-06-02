"""Placement-level discrete actions for the single-player RL environment."""

from __future__ import annotations

import random
from typing import Iterable, Sequence

from src.core.constants import Direction, GRID_WIDTH
from src.core.headless import HeadlessPuyoSimulator, PlacementAction


def _is_spawn_geometry_action(axis_x: int, rotation: Direction) -> bool:
    if not 0 <= axis_x < GRID_WIDTH:
        return False
    if rotation == Direction.RIGHT:
        return axis_x + 1 < GRID_WIDTH
    if rotation == Direction.LEFT:
        return axis_x - 1 >= 0
    return True


PLACEMENT_ACTIONS: tuple[PlacementAction, ...] = tuple(
    PlacementAction(axis_x, rotation)
    for axis_x in range(GRID_WIDTH)
    for rotation in Direction
    if _is_spawn_geometry_action(axis_x, rotation)
)

ACTION_TO_INDEX = {action: index for index, action in enumerate(PLACEMENT_ACTIONS)}
NUM_ACTIONS = len(PLACEMENT_ACTIONS)


def action_to_placement(action_index: int) -> PlacementAction:
    """Convert a discrete action index to a headless placement action."""

    try:
        return PLACEMENT_ACTIONS[int(action_index)]
    except (IndexError, ValueError) as exc:
        raise ValueError(f"action_index must be in [0, {NUM_ACTIONS})") from exc


def placement_to_action_index(action: PlacementAction) -> int:
    """Convert a placement action to its stable discrete action index."""

    return ACTION_TO_INDEX[action]


def legal_action_mask(simulator: HeadlessPuyoSimulator) -> list[bool]:
    """Return a dynamic action mask for the simulator's current state."""

    legal = set(simulator.legal_actions())
    return [action in legal for action in PLACEMENT_ACTIONS]


def legal_action_indices(simulator: HeadlessPuyoSimulator) -> list[int]:
    """Return legal discrete action indices for the simulator's current state."""

    return [index for index, allowed in enumerate(legal_action_mask(simulator)) if allowed]


def choose_random_legal_action(
    mask: Sequence[bool],
    rng: random.Random | None = None,
) -> int:
    """Sample an action index from a legal-action mask."""

    legal_indices = [index for index, allowed in enumerate(mask) if allowed]
    if not legal_indices:
        raise ValueError("cannot sample an action because the mask has no legal actions")
    chooser = rng or random
    return chooser.choice(legal_indices)


def placements_from_indices(indices: Iterable[int]) -> list[PlacementAction]:
    """Map multiple discrete action indices to placement actions."""

    return [action_to_placement(index) for index in indices]
