"""Ama-inspired beam search policy for chain construction."""

from __future__ import annotations

import copy
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from puyo_env.actions import action_to_placement, legal_action_indices
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, NORMAL_PUYO_COLORS, PuyoColor
from src.core.field import Field
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo
from src.core.tsumo import PuyoSequence


_SCENARIO_BAGS = (
    (0, 1, 2, 3),
    (0, 2, 1, 3),
    (0, 3, 1, 2),
    (1, 2, 0, 3),
    (1, 3, 0, 2),
    (2, 3, 0, 1),
)


@dataclass(frozen=True)
class BeamSearchConfig:
    depth: int = 10
    width: int = 48
    scenarios: int = 1
    chain_weight: float = 100_000.0
    score_weight: float = 1.0
    premature_chain_penalty: float = 350.0
    minimum_chain_count: int = 6
    scenario_seed: int | None = None

    def __post_init__(self) -> None:
        if self.depth < 1:
            raise ValueError("beam depth must be at least 1")
        if self.width < 1:
            raise ValueError("beam width must be at least 1")
        if not 1 <= self.scenarios <= len(_SCENARIO_BAGS):
            raise ValueError(f"beam scenarios must be in [1, {len(_SCENARIO_BAGS)}]")
        if self.minimum_chain_count < 1:
            raise ValueError("minimum chain count must be at least 1")


@dataclass(frozen=True)
class BeamSearchDiagnostics:
    elapsed_seconds: float
    expanded_nodes: int
    scenario_count: int
    candidate_values: tuple[tuple[int, float], ...]


@dataclass
class _Node:
    simulator: Any
    root_action: int
    evaluation: float
    best_chain_value: float
    premature_penalty: float


class _ScenarioSequence:
    """Repeat one of Ama's six representative unknown-pair patterns."""

    def __init__(self, scenario_id: int, colors=None):
        bag = _SCENARIO_BAGS[scenario_id]
        colors = tuple(colors or NORMAL_PUYO_COLORS)
        self.pairs = (
            (colors[bag[0]], colors[bag[1]]),
            (colors[bag[2]], colors[bag[3]]),
        )
        self.index = 0

    def next_pair(self):
        colors = self.pairs[self.index % len(self.pairs)]
        self.index += 1
        return Puyo(colors[0]), Puyo(colors[1])

    def clone(self) -> _ScenarioSequence:
        cloned = _ScenarioSequence.__new__(_ScenarioSequence)
        cloned.pairs = self.pairs
        cloned.index = self.index
        return cloned


class BeamSearchPolicy:
    """Search multiple placements ahead and select the best chain-building root move."""

    def __init__(self, config: BeamSearchConfig | None = None):
        self.config = config or BeamSearchConfig()
        self.last_diagnostics: BeamSearchDiagnostics | None = None

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        _ = observation
        simulator = info.get("simulator")
        if simulator is None:
            choices = _legal_indices_from_info(info)
            return choices[0] if choices else 0

        started = time.perf_counter()
        totals: dict[int, float] = {}
        expanded_nodes = 0
        scenario_ids = list(range(len(_SCENARIO_BAGS)))
        scenario_colors = [NORMAL_PUYO_COLORS] * self.config.scenarios
        if self.config.scenario_seed is not None:
            scenario_rng = random.Random(self.config.scenario_seed)
            scenario_rng.shuffle(scenario_ids)
            scenario_colors = []
            for _ in range(self.config.scenarios):
                colors = list(NORMAL_PUYO_COLORS)
                scenario_rng.shuffle(colors)
                scenario_colors.append(tuple(colors))

        for scenario_id, colors in zip(scenario_ids, scenario_colors):
            scenario_simulator = clone_simulator(simulator)
            # The current pair and two visible next pairs stay intact. Only hidden
            # future pairs are replaced by representative scenarios.
            scenario_simulator.game.puyo_sequence = _ScenarioSequence(scenario_id, colors)
            values, expanded = self._search_scenario(scenario_simulator)
            expanded_nodes += expanded
            for action, value in values.items():
                totals[action] = totals.get(action, 0.0) + value

        legal = legal_action_indices(simulator)
        if not legal:
            return 0
        best_action = max(legal, key=lambda action: (totals.get(action, float("-inf")), -action))
        self.last_diagnostics = BeamSearchDiagnostics(
            elapsed_seconds=time.perf_counter() - started,
            expanded_nodes=expanded_nodes,
            scenario_count=self.config.scenarios,
            candidate_values=tuple(sorted(totals.items())),
        )
        return best_action

    def _search_scenario(self, simulator) -> tuple[dict[int, float], int]:
        beam: list[_Node] = []
        best_by_action: dict[int, float] = {}
        expanded_nodes = 0

        for action in legal_action_indices(simulator):
            child = clone_simulator(simulator)
            result = child.step(action_to_placement(action))
            expanded_nodes += 1
            if not result.valid or result.game_over:
                continue
            chain_value, premature_penalty = self._chain_outcome(result.chain_count, result.score_delta)
            evaluation = evaluate_board(child.game)
            beam.append(_Node(child, action, evaluation, chain_value, premature_penalty))
            best_by_action[action] = chain_value - premature_penalty + evaluation

        beam = self._prune(beam)
        for _ in range(1, self.config.depth):
            children: list[_Node] = []
            seen: dict[tuple, _Node] = {}
            for node in beam:
                for action in legal_action_indices(node.simulator):
                    child = clone_simulator(node.simulator)
                    result = child.step(action_to_placement(action))
                    expanded_nodes += 1
                    if not result.valid or result.game_over:
                        continue

                    chain_value, premature_penalty = self._advance_chain_outcome(
                        node,
                        result.chain_count,
                        result.score_delta,
                    )
                    evaluation = evaluate_board(child.game)
                    candidate = _Node(child, node.root_action, evaluation, chain_value, premature_penalty)
                    fingerprint = _field_fingerprint(child.game)
                    previous = seen.get(fingerprint)
                    if previous is None or _node_value(candidate) > _node_value(previous):
                        seen[fingerprint] = candidate

                    value = chain_value - premature_penalty + evaluation
                    if value > best_by_action.get(node.root_action, float("-inf")):
                        best_by_action[node.root_action] = value

            if not seen:
                break
            children.extend(seen.values())
            beam = self._prune(children)

        return best_by_action, expanded_nodes

    def _prune(self, nodes: list[_Node]) -> list[_Node]:
        nodes.sort(key=lambda node: (_node_value(node), -node.root_action), reverse=True)
        return nodes[: self.config.width]

    def _chain_outcome(self, chain_count: int, score_delta: int) -> tuple[float, float]:
        if 0 < chain_count < self.config.minimum_chain_count:
            return 0.0, self.config.premature_chain_penalty * float(score_delta)
        if chain_count == 0:
            return 0.0, 0.0
        return (
            self.config.chain_weight * float(chain_count) + self.config.score_weight * float(score_delta),
            0.0,
        )

    def _advance_chain_outcome(
        self,
        node: _Node,
        chain_count: int,
        score_delta: int,
    ) -> tuple[float, float]:
        chain_value, penalty = self._chain_outcome(chain_count, score_delta)
        return max(node.best_chain_value, chain_value), node.premature_penalty + penalty


def evaluate_board(game) -> float:
    """Static board evaluation adapted from Ama's build-search features."""

    heights = _column_heights(game)
    average_height = sum(heights) / len(heights)
    ideal_offsets = (1, 1, 1, -1, -1, -1)
    shape_error = sum(abs(height - average_height - offset) for height, offset in zip(heights, ideal_offsets))

    well_depth = 0
    bump_height = 0
    for x, height in enumerate(heights):
        left = heights[x - 1] if x > 0 else heights[1]
        right = heights[x + 1] if x < GRID_WIDTH - 1 else heights[GRID_WIDTH - 2]
        if height < left and height < right:
            well_depth += min(left, right) - height
        if 0 < x < GRID_WIDTH - 1 and height > left and height > right:
            bump_height += height - max(left, right)

    groups = _color_groups(game)
    link_2 = sum(1 for group in groups if len(group) == 2)
    link_3 = sum(1 for group in groups if len(group) == 3)
    isolated = sum(1 for group in groups if len(group) == 1)
    reachable_ignitions = sum(_reachable_ignition_count(group, game, heights) for group in groups if len(group) == 3)
    grid = game.field.grid
    nuisance = sum(
        1
        for y in range(GRID_HEIGHT)
        for x in range(GRID_WIDTH)
        if grid[y][x].color == PuyoColor.OJAMA
    )

    danger = max(0, heights[2] - 8) * 1_200 + max(0, max(heights) - 10) * 900
    return (
        link_2 * 150.0
        + link_3 * 420.0
        + reachable_ignitions * 900.0
        - isolated * 18.0
        - shape_error * 45.0
        - well_depth * 70.0
        - bump_height * 90.0
        - nuisance * 250.0
        - danger
    )


def _column_heights(game) -> tuple[int, ...]:
    grid = game.field.grid
    heights = []
    for x in range(GRID_WIDTH):
        height = 0
        for y in range(GRID_HEIGHT - 1, -1, -1):
            if not grid[y][x].is_empty():
                height = y + 1
                break
        heights.append(height)
    return tuple(heights)


def _color_groups(game) -> tuple[frozenset[tuple[int, int]], ...]:
    grid = game.field.grid
    visited: set[tuple[int, int]] = set()
    groups = []
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            if (x, y) in visited:
                continue
            color = grid[y][x].color
            if color not in NORMAL_PUYO_COLORS:
                continue
            stack = [(x, y)]
            group: set[tuple[int, int]] = set()
            while stack:
                cell = stack.pop()
                if cell in group:
                    continue
                group.add(cell)
                cx, cy = cell
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if not (0 <= nx < GRID_WIDTH and 0 <= ny < GRID_HEIGHT):
                        continue
                    if grid[ny][nx].color == color and (nx, ny) not in group:
                        stack.append((nx, ny))
            visited.update(group)
            groups.append(frozenset(group))
    return tuple(groups)


def _field_fingerprint(game) -> tuple:
    grid = game.field.grid
    return (game.all_clear_bonus_pending,) + tuple(
        grid[y][x].color.value
        for y in range(GRID_HEIGHT)
        for x in range(GRID_WIDTH)
    )


def _reachable_ignition_count(group, game, heights: tuple[int, ...]) -> int:
    grid = game.field.grid
    candidates = set()
    for x, y in group:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < GRID_WIDTH and 0 <= ny < GRID_HEIGHT):
                continue
            if ny != heights[nx] or not grid[ny][nx].is_empty():
                continue
            candidates.add((nx, ny))
    return len(candidates)


def clone_simulator(simulator: HeadlessPuyoSimulator) -> HeadlessPuyoSimulator:
    """Clone the mutable headless search state without copying UI-only data."""

    cloned = HeadlessPuyoSimulator.__new__(HeadlessPuyoSimulator)
    cloned.game = copy.copy(simulator.game)

    field = Field.__new__(Field)
    field.width = simulator.game.field.width
    field.height = simulator.game.field.height
    field.grid = [row.copy() for row in simulator.game.field.grid]
    cloned.game.field = field

    cloned.game.next_puyo_queue = deque(simulator.game.next_puyo_queue)
    cloned.game.puyo_sequence = _clone_sequence(simulator.game.puyo_sequence)

    # These containers are reset during synchronous resolution, but must not be
    # shared if a caller clones a state between animation phases.
    cloned.game.vanish_coords = set(simulator.game.vanish_coords)
    cloned.game.vanish_groups = [set(group) for group in simulator.game.vanish_groups]
    cloned.game.drop_tween_static_cells = list(simulator.game.drop_tween_static_cells)
    cloned.game.drop_tween_motions = list(simulator.game.drop_tween_motions)
    if simulator.game.drop_tween_grid_before is None:
        cloned.game.drop_tween_grid_before = None
    else:
        cloned.game.drop_tween_grid_before = [row.copy() for row in simulator.game.drop_tween_grid_before]
    return cloned


def _clone_sequence(sequence):
    if isinstance(sequence, _ScenarioSequence):
        return sequence.clone()
    if isinstance(sequence, PuyoSequence):
        cloned = PuyoSequence.__new__(PuyoSequence)
        cloned.seed = sequence.seed
        cloned.colors = sequence.colors
        cloned._rng = random.Random()
        cloned._rng.setstate(sequence._rng.getstate())
        return cloned
    return copy.deepcopy(sequence)


def _node_value(node: _Node) -> float:
    return node.best_chain_value - node.premature_penalty + node.evaluation


def _legal_indices_from_info(info: dict[str, Any]) -> list[int]:
    mask = info.get("action_mask")
    if mask is None:
        return []
    return [index for index, allowed in enumerate(mask) if bool(allowed)]
