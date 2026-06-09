"""Ama-inspired beam search policy for chain construction."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

from puyo_env.actions import action_to_placement, legal_action_indices
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, NORMAL_PUYO_COLORS, PuyoColor
from src.core.puyo import Puyo


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
    depth: int = 5
    width: int = 32
    scenarios: int = 1
    chain_weight: float = 100_000.0
    score_weight: float = 1.0
    premature_chain_penalty: float = 350.0
    minimum_chain_count: int = 3

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


class _ScenarioSequence:
    """Repeat one of Ama's six representative unknown-pair patterns."""

    def __init__(self, scenario_id: int):
        bag = _SCENARIO_BAGS[scenario_id]
        colors = NORMAL_PUYO_COLORS
        self.pairs = (
            (colors[bag[0]], colors[bag[1]]),
            (colors[bag[2]], colors[bag[3]]),
        )
        self.index = 0

    def next_pair(self):
        colors = self.pairs[self.index % len(self.pairs)]
        self.index += 1
        return Puyo(colors[0]), Puyo(colors[1])


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
        for scenario_id in range(self.config.scenarios):
            scenario_simulator = copy.deepcopy(simulator)
            # The current pair and two visible next pairs stay intact. Only hidden
            # future pairs are replaced by representative scenarios.
            scenario_simulator.game.puyo_sequence = _ScenarioSequence(scenario_id)
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
            child = copy.deepcopy(simulator)
            result = child.step(action_to_placement(action))
            expanded_nodes += 1
            if not result.valid or result.game_over:
                continue
            chain_value = self._chain_value(result.chain_count, result.score_delta)
            evaluation = evaluate_board(child.game)
            beam.append(_Node(child, action, evaluation, chain_value))
            best_by_action[action] = chain_value + evaluation

        beam = self._prune(beam)
        for _ in range(1, self.config.depth):
            children: list[_Node] = []
            seen: dict[tuple, _Node] = {}
            for node in beam:
                for action in legal_action_indices(node.simulator):
                    child = copy.deepcopy(node.simulator)
                    result = child.step(action_to_placement(action))
                    expanded_nodes += 1
                    if not result.valid or result.game_over:
                        continue

                    chain_value = max(
                        node.best_chain_value,
                        self._chain_value(result.chain_count, result.score_delta),
                    )
                    evaluation = evaluate_board(child.game)
                    candidate = _Node(child, node.root_action, evaluation, chain_value)
                    fingerprint = _field_fingerprint(child.game)
                    previous = seen.get(fingerprint)
                    if previous is None or _node_value(candidate) > _node_value(previous):
                        seen[fingerprint] = candidate

                    value = chain_value + evaluation
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

    def _chain_value(self, chain_count: int, score_delta: int) -> float:
        if 0 < chain_count < self.config.minimum_chain_count:
            return -self.config.premature_chain_penalty * float(score_delta)
        return self.config.chain_weight * float(chain_count) + self.config.score_weight * float(score_delta)


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
    nuisance = sum(
        1
        for y in range(GRID_HEIGHT)
        for x in range(GRID_WIDTH)
        if game.field.get_puyo(x, y).color == PuyoColor.OJAMA
    )

    danger = max(0, heights[2] - 8) * 1_200 + max(0, max(heights) - 10) * 900
    return (
        link_2 * 150.0
        + link_3 * 420.0
        - isolated * 18.0
        - shape_error * 45.0
        - well_depth * 70.0
        - bump_height * 90.0
        - nuisance * 250.0
        - danger
    )


def _column_heights(game) -> tuple[int, ...]:
    heights = []
    for x in range(GRID_WIDTH):
        occupied = [y for y in range(GRID_HEIGHT) if not game.field.get_puyo(x, y).is_empty()]
        heights.append(max(occupied) + 1 if occupied else 0)
    return tuple(heights)


def _color_groups(game) -> tuple[frozenset[tuple[int, int]], ...]:
    visited: set[tuple[int, int]] = set()
    groups = []
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            if (x, y) in visited:
                continue
            color = game.field.get_puyo(x, y).color
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
                    if game.field.get_puyo(nx, ny).color == color and (nx, ny) not in group:
                        stack.append((nx, ny))
            visited.update(group)
            groups.append(frozenset(group))
    return tuple(groups)


def _field_fingerprint(game) -> tuple:
    return tuple(
        game.field.get_puyo(x, y).color.value
        for y in range(GRID_HEIGHT)
        for x in range(GRID_WIDTH)
    )


def _node_value(node: _Node) -> float:
    return node.best_chain_value + node.evaluation


def _legal_indices_from_info(info: dict[str, Any]) -> list[int]:
    mask = info.get("action_mask")
    if mask is None:
        return []
    return [index for index, allowed in enumerate(mask) if bool(allowed)]
