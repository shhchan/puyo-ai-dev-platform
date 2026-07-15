"""Ama-inspired beam search policy for chain construction."""

from __future__ import annotations

import copy
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Sequence

from puyo_env.actions import action_to_placement, legal_action_indices
from src.core.constants import (
    GRID_HEIGHT,
    GRID_WIDTH,
    NORMAL_PUYO_COLORS,
    VISIBLE_HEIGHT,
    PuyoColor,
)
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
    trigger_preservation: str = "ignore"
    probe_width: int = 0
    trace_paths: bool = False

    def __post_init__(self) -> None:
        if self.depth < 1:
            raise ValueError("beam depth must be at least 1")
        if self.width < 1:
            raise ValueError("beam width must be at least 1")
        if not 1 <= self.scenarios <= len(_SCENARIO_BAGS):
            raise ValueError(f"beam scenarios must be in [1, {len(_SCENARIO_BAGS)}]")
        if self.minimum_chain_count < 1:
            raise ValueError("minimum chain count must be at least 1")
        if self.trigger_preservation not in {"required", "prefer", "ignore"}:
            raise ValueError(
                f"unsupported trigger preservation: {self.trigger_preservation}"
            )
        if self.probe_width < 0:
            raise ValueError("potential probe width must be non-negative")


@dataclass(frozen=True)
class BuildPotential:
    """Best bounded single-column ignition found for one quiet field."""

    chain_count: int = 0
    required_puyos: int = 0
    trigger_x: int | None = None
    trigger_y: int | None = None
    trigger_color: PuyoColor | None = None

    @property
    def exists(self) -> bool:
        return self.chain_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_count": int(self.chain_count),
            "required_puyos": int(self.required_puyos),
            "trigger": (
                None
                if self.trigger_x is None or self.trigger_y is None
                else {"x": int(self.trigger_x), "y": int(self.trigger_y)}
            ),
            "trigger_color": (
                None if self.trigger_color is None else self.trigger_color.name
            ),
        }


@dataclass(frozen=True)
class BeamSearchDiagnostics:
    elapsed_seconds: float
    expanded_nodes: int
    scenario_count: int
    candidate_values: tuple[tuple[int, float], ...]
    trigger_preservation: str
    probe_width: int
    root_potential: BuildPotential
    selected_potential: BuildPotential
    trigger_preserved: bool
    potential_probe_count: int
    potential_cache_hits: int
    candidates: tuple[BeamCandidateDiagnostics, ...]


@dataclass(frozen=True)
class BeamCandidateDiagnostics:
    """Offline-safe root-candidate survival details for one decision."""

    action: int
    root_generated: bool
    root_rejected: bool
    base_prune_depth: int
    potential_probe_depth: int
    final_prune_depth: int
    safety_suppressed_depth: int
    predicted_max_chain: int
    best_chain_depth: int
    best_path: tuple[int, ...]
    premature_fire_score: int
    premature_fire_penalty: float
    candidate_value: float | None
    potential: BuildPotential

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": int(self.action),
            "root_generated": bool(self.root_generated),
            "root_rejected": bool(self.root_rejected),
            "stages": {
                "base_prune_depth": int(self.base_prune_depth),
                "potential_probe_depth": int(self.potential_probe_depth),
                "final_prune_depth": int(self.final_prune_depth),
                "safety_suppressed_depth": int(self.safety_suppressed_depth),
            },
            "predicted_max_chain": int(self.predicted_max_chain),
            "best_chain_depth": int(self.best_chain_depth),
            "best_path": [int(action) for action in self.best_path],
            "fire_cost": {
                "score": int(self.premature_fire_score),
                "penalty": float(self.premature_fire_penalty),
            },
            "candidate_value": (
                None if self.candidate_value is None else float(self.candidate_value)
            ),
            "potential": self.potential.to_dict(),
        }


@dataclass
class _CandidateTraceState:
    root_generated: bool = False
    root_rejected: bool = False
    base_prune_depth: int = 0
    potential_probe_depth: int = 0
    final_prune_depth: int = 0
    safety_suppressed_depth: int = 0
    best_value: float = float("-inf")
    predicted_max_chain: int = 0
    best_chain_depth: int = 0
    best_path: tuple[int, ...] = ()
    premature_fire_score: int = 0
    premature_fire_penalty: float = 0.0
    potential: BuildPotential = BuildPotential()


@dataclass
class _PathTraceState:
    generated: bool = False
    base_prune: bool = False
    potential_probe: bool = False
    final_prune: bool = False
    safety_suppressed: bool = False


class _SearchTrace:
    """Collect candidate metadata without participating in search ranking."""

    def __init__(self, *, trace_paths: bool = False) -> None:
        self._states: dict[int, _CandidateTraceState] = {}
        self._trace_paths = bool(trace_paths)
        self._paths: dict[tuple[int, ...], _PathTraceState] = {}

    def _state(self, action: int) -> _CandidateTraceState:
        return self._states.setdefault(int(action), _CandidateTraceState())

    def mark_root_generated(self, action: int) -> None:
        self._state(action).root_generated = True

    def mark_root_rejected(self, action: int) -> None:
        self._state(action).root_rejected = True

    def mark_generated_path(self, path: tuple[int, ...]) -> None:
        if self._trace_paths:
            self._paths.setdefault(path, _PathTraceState()).generated = True

    def mark_stage(self, stage: str, nodes: list[_Node], depth: int) -> None:
        attribute = {
            "base": "base_prune_depth",
            "probe": "potential_probe_depth",
            "final": "final_prune_depth",
        }[stage]
        for node in nodes:
            state = self._state(node.root_action)
            setattr(state, attribute, max(int(getattr(state, attribute)), int(depth)))
            if self._trace_paths:
                path_state = self._paths.setdefault(node.path, _PathTraceState())
                setattr(
                    path_state,
                    {
                        "base": "base_prune",
                        "probe": "potential_probe",
                        "final": "final_prune",
                    }[stage],
                    True,
                )

    def mark_safety_suppressed(self, node: _Node, depth: int) -> None:
        state = self._state(node.root_action)
        state.safety_suppressed_depth = max(
            state.safety_suppressed_depth,
            int(depth),
        )
        if self._trace_paths:
            self._paths.setdefault(node.path, _PathTraceState()).safety_suppressed = True

    def record_best(self, node: _Node, value: float) -> None:
        state = self._state(node.root_action)
        if value <= state.best_value:
            return
        state.best_value = float(value)
        state.predicted_max_chain = int(node.best_chain_count)
        state.best_chain_depth = int(node.best_chain_depth)
        state.best_path = node.path
        state.premature_fire_score = int(node.premature_fire_score)
        state.premature_fire_penalty = float(node.premature_penalty)
        state.potential = node.potential

    def diagnostics(
        self,
        actions: list[int],
        values: dict[int, float],
    ) -> tuple[BeamCandidateDiagnostics, ...]:
        result = []
        for action in actions:
            state = self._state(action)
            result.append(
                BeamCandidateDiagnostics(
                    action=int(action),
                    root_generated=state.root_generated,
                    root_rejected=state.root_rejected,
                    base_prune_depth=state.base_prune_depth,
                    potential_probe_depth=state.potential_probe_depth,
                    final_prune_depth=state.final_prune_depth,
                    safety_suppressed_depth=state.safety_suppressed_depth,
                    predicted_max_chain=state.predicted_max_chain,
                    best_chain_depth=state.best_chain_depth,
                    best_path=state.best_path,
                    premature_fire_score=state.premature_fire_score,
                    premature_fire_penalty=state.premature_fire_penalty,
                    candidate_value=(
                        None if action not in values else float(values[action])
                    ),
                    potential=state.potential,
                )
            )
        return tuple(result)

    def path_diagnostics(self, path: Sequence[int]) -> tuple[dict[str, Any], ...]:
        actions = tuple(int(action) for action in path)
        result = []
        for depth in range(1, len(actions) + 1):
            prefix = actions[:depth]
            state = self._paths.get(prefix, _PathTraceState())
            result.append(
                {
                    "depth": depth,
                    "path": list(prefix),
                    "generated": bool(state.generated),
                    "base_prune": bool(state.base_prune),
                    "potential_probe": bool(state.potential_probe),
                    "final_prune": bool(state.final_prune),
                    "safety_suppressed": bool(state.safety_suppressed),
                }
            )
        return tuple(result)


@dataclass
class _Node:
    simulator: Any
    root_action: int
    evaluation: float
    best_chain_value: float
    premature_penalty: float
    best_chain_count: int
    best_chain_depth: int
    premature_fire_score: int
    path: tuple[int, ...]
    potential: BuildPotential
    target_achieved: bool


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
        self._potential_cache: dict[tuple, BuildPotential] = {}
        self._potential_probe_count = 0
        self._potential_cache_hits = 0
        self._root_potential = BuildPotential()
        self._search_trace = _SearchTrace(trace_paths=self.config.trace_paths)

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        _ = observation
        simulator = info.get("simulator")
        if simulator is None:
            choices = _legal_indices_from_info(info)
            return choices[0] if choices else 0

        started = time.perf_counter()
        self._potential_cache = {}
        self._potential_probe_count = 0
        self._potential_cache_hits = 0
        self._search_trace = _SearchTrace(trace_paths=self.config.trace_paths)
        self._root_potential = (
            self._probe_potential(simulator)
            if self._preserves_trigger
            else BuildPotential()
        )
        totals: dict[int, float] = {}
        potentials: dict[int, list[BuildPotential]] = {}
        preserved: dict[int, list[bool]] = {}
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
            values, scenario_potentials, scenario_preserved, expanded = (
                self._search_scenario(scenario_simulator)
            )
            expanded_nodes += expanded
            for action, value in values.items():
                totals[action] = totals.get(action, 0.0) + value
                potentials.setdefault(action, []).append(scenario_potentials[action])
                preserved.setdefault(action, []).append(scenario_preserved[action])

        legal = legal_action_indices(simulator)
        if not legal:
            return 0
        best_action = max(legal, key=lambda action: (totals.get(action, float("-inf")), -action))
        selected_candidates = potentials.get(best_action, ())
        selected_potential = max(
            selected_candidates,
            key=_potential_rank_key,
            default=BuildPotential(),
        )
        self.last_diagnostics = BeamSearchDiagnostics(
            elapsed_seconds=time.perf_counter() - started,
            expanded_nodes=expanded_nodes,
            scenario_count=self.config.scenarios,
            candidate_values=tuple(sorted(totals.items())),
            trigger_preservation=self.config.trigger_preservation,
            probe_width=self.config.probe_width,
            root_potential=self._root_potential,
            selected_potential=selected_potential,
            trigger_preserved=(
                self._preserves_trigger
                and bool(preserved.get(best_action))
                and all(preserved[best_action])
            ),
            potential_probe_count=self._potential_probe_count,
            potential_cache_hits=self._potential_cache_hits,
            candidates=self._search_trace.diagnostics(legal, totals),
        )
        return best_action

    def candidate_path_diagnostics(
        self,
        path: Sequence[int],
    ) -> tuple[dict[str, Any], ...]:
        """Return offline trace stages for every prefix of one action path."""

        return self._search_trace.path_diagnostics(path)

    @property
    def _preserves_trigger(self) -> bool:
        return (
            self.config.trigger_preservation != "ignore"
            and self.config.probe_width > 0
        )

    def _search_scenario(
        self,
        simulator,
    ) -> tuple[dict[int, float], dict[int, BuildPotential], dict[int, bool], int]:
        beam: list[_Node] = []
        best_by_action: dict[int, float] = {}
        potential_by_action: dict[int, BuildPotential] = {}
        preserved_by_action: dict[int, bool] = {}
        expanded_nodes = 0

        root_candidates: list[tuple[_Node, int]] = []
        for action in legal_action_indices(simulator):
            child = clone_simulator(simulator)
            result = child.step(action_to_placement(action))
            expanded_nodes += 1
            if not result.valid or result.game_over:
                self._search_trace.mark_root_rejected(action)
                continue
            self._search_trace.mark_root_generated(action)
            chain_value, premature_penalty = self._chain_outcome(result.chain_count, result.score_delta)
            evaluation = evaluate_board(child.game)
            path = (int(action),) if self.config.trace_paths else ()
            best_chain_depth = 1 if result.chain_count > 0 else 0
            node = _Node(
                child,
                action,
                evaluation,
                chain_value,
                premature_penalty,
                int(result.chain_count),
                best_chain_depth,
                self._premature_fire_score(
                    result.chain_count,
                    result.score_delta,
                ),
                path,
                BuildPotential(),
                result.chain_count >= self.config.minimum_chain_count,
            )
            self._search_trace.mark_generated_path(path)
            root_candidates.append(
                (
                    node,
                    result.chain_count,
                )
            )

        beam = self._prune(
            self._suppress_premature(root_candidates, depth=1),
            depth=1,
        )
        self._record_best(
            beam,
            best_by_action,
            potential_by_action,
            preserved_by_action,
        )
        for depth in range(2, self.config.depth + 1):
            seen: dict[tuple, _Node] = {}
            for node in beam:
                node_candidates: list[tuple[_Node, int]] = []
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
                    best_chain_depth = (
                        depth
                        if int(result.chain_count) > node.best_chain_count
                        else node.best_chain_depth
                    )
                    path = (
                        node.path + (int(action),)
                        if self.config.trace_paths
                        else ()
                    )
                    candidate = _Node(
                        child,
                        node.root_action,
                        evaluation,
                        chain_value,
                        premature_penalty,
                        max(node.best_chain_count, int(result.chain_count)),
                        best_chain_depth,
                        node.premature_fire_score
                        + self._premature_fire_score(
                            result.chain_count,
                            result.score_delta,
                        ),
                        path,
                        BuildPotential(),
                        node.target_achieved
                        or result.chain_count >= self.config.minimum_chain_count,
                    )
                    self._search_trace.mark_generated_path(path)
                    node_candidates.append((candidate, result.chain_count))

                for candidate in self._suppress_premature(
                    node_candidates,
                    depth=depth,
                ):
                    fingerprint = _field_fingerprint(candidate.simulator.game)
                    previous = seen.get(fingerprint)
                    if previous is None or _base_node_value(candidate) > _base_node_value(previous):
                        seen[fingerprint] = candidate

            if not seen:
                break
            beam = self._prune(list(seen.values()), depth=depth)
            self._record_best(
                beam,
                best_by_action,
                potential_by_action,
                preserved_by_action,
            )

        return best_by_action, potential_by_action, preserved_by_action, expanded_nodes

    def _suppress_premature(
        self,
        candidates: list[tuple[_Node, int]],
        *,
        depth: int = 1,
    ) -> list[_Node]:
        if not self._preserves_trigger or not any(chain == 0 for _, chain in candidates):
            return [node for node, _ in candidates]
        retained = []
        for node, chain in candidates:
            if 0 < chain < self.config.minimum_chain_count:
                root_action = getattr(node, "root_action", None)
                if root_action is not None:
                    self._search_trace.mark_safety_suppressed(node, depth)
                continue
            retained.append(node)
        return retained

    def _record_best(
        self,
        nodes: list[_Node],
        values: dict[int, float],
        potentials: dict[int, BuildPotential],
        preserved: dict[int, bool],
    ) -> None:
        for node in nodes:
            value = _node_value(node)
            if value <= values.get(node.root_action, float("-inf")):
                continue
            values[node.root_action] = value
            potentials[node.root_action] = node.potential
            preserved[node.root_action] = node.target_achieved or _same_trigger(
                self._root_potential,
                node.potential,
            )
            self._search_trace.record_best(node, value)

    def _prune(self, nodes: list[_Node], *, depth: int = 1) -> list[_Node]:
        nodes.sort(
            key=lambda node: (_base_node_value(node), -node.root_action),
            reverse=True,
        )
        self._search_trace.mark_stage("base", nodes[: self.config.width], depth)
        if self._preserves_trigger:
            probed = nodes[: self.config.probe_width]
            self._search_trace.mark_stage("probe", probed, depth)
            for node in probed:
                node.potential = self._probe_potential(node.simulator)
                if not node.target_achieved:
                    potential_value = _potential_value(node.potential)
                    root_value = _potential_value(self._root_potential)
                    node.evaluation += self.config.chain_weight * potential_value
                    if potential_value < root_value:
                        preserve_scale = {
                            "prefer": 0.5,
                            "required": 1.0,
                            "ignore": 0.0,
                        }[self.config.trigger_preservation]
                        node.evaluation -= (
                            self.config.chain_weight
                            * (root_value - potential_value)
                            * preserve_scale
                        )
        nodes.sort(
            key=lambda node: (
                _node_value(node),
                _potential_rank_key(node.potential),
                -node.root_action,
            ),
            reverse=True,
        )
        retained = nodes[: self.config.width]
        self._search_trace.mark_stage("final", retained, depth)
        return retained

    def _probe_potential(self, simulator) -> BuildPotential:
        fingerprint = _field_fingerprint(simulator.game)
        cached = self._potential_cache.get(fingerprint)
        if cached is not None:
            self._potential_cache_hits += 1
            return cached
        potential = evaluate_build_potential(simulator)
        self._potential_cache[fingerprint] = potential
        self._potential_probe_count += 1
        return potential

    def _chain_outcome(self, chain_count: int, score_delta: int) -> tuple[float, float]:
        if 0 < chain_count < self.config.minimum_chain_count:
            return 0.0, self.config.premature_chain_penalty * float(score_delta)
        if chain_count == 0:
            return 0.0, 0.0
        return (
            self.config.chain_weight * float(chain_count) + self.config.score_weight * float(score_delta),
            0.0,
        )

    def _premature_fire_score(self, chain_count: int, score_delta: int) -> int:
        if 0 < chain_count < self.config.minimum_chain_count:
            return int(score_delta)
        return 0

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


def evaluate_build_potential(
    simulator: HeadlessPuyoSimulator,
    *,
    max_required_puyos: int = 3,
) -> BuildPotential:
    """Evaluate Ama's bounded quiet ignition search without mutating ``simulator``."""

    if max_required_puyos < 1:
        raise ValueError("max required puyos must be at least 1")
    heights = _column_heights(simulator.game)
    x_min, x_max = _quiet_search_bounds(heights)
    best = BuildPotential()
    for x in range(x_min, x_max + 1):
        drop_max = min(max_required_puyos, VISIBLE_HEIGHT - heights[x])
        if drop_max <= 0:
            continue
        for color in NORMAL_PUYO_COLORS:
            trigger_y = heights[x]
            for required in range(1, drop_max + 1):
                if not _virtual_drop_triggers(
                    simulator.game,
                    x=x,
                    y=trigger_y,
                    color=color,
                    count=required,
                ):
                    continue
                candidate = clone_simulator(simulator)
                for offset in range(required):
                    candidate.game.field.place_puyo(
                        x,
                        trigger_y + offset,
                        Puyo(color),
                    )
                chains = candidate.game.resolve_chains_synchronously(
                    spawn_next=False,
                    capture_visuals=False,
                )
                potential = BuildPotential(
                    chain_count=len(chains),
                    required_puyos=required,
                    trigger_x=x,
                    trigger_y=trigger_y,
                    trigger_color=color,
                )
                if _potential_rank_key(potential) > _potential_rank_key(best):
                    best = potential
                break
    return best


def _virtual_drop_triggers(
    game,
    *,
    x: int,
    y: int,
    color: PuyoColor,
    count: int,
) -> bool:
    """Check the dropped color group locally before cloning and resolving."""

    virtual = {(x, y + offset) for offset in range(count)}
    stack = [(x, y)]
    visited: set[tuple[int, int]] = set()
    while stack:
        cell = stack.pop()
        if cell in visited:
            continue
        cx, cy = cell
        if not (0 <= cx < GRID_WIDTH and 0 <= cy < VISIBLE_HEIGHT):
            continue
        if cell not in virtual and game.field.grid[cy][cx].color != color:
            continue
        visited.add(cell)
        if len(visited) >= 4:
            return True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            stack.append((cx + dx, cy + dy))
    return False


def _quiet_search_bounds(heights: tuple[int, ...]) -> tuple[int, int]:
    """Match Ama's center-reachable column bound for quiet drops."""

    x_min = 2
    x_max = 2
    for x in range(3, GRID_WIDTH):
        if heights[x] > VISIBLE_HEIGHT - 1:
            break
        x_max += 1
    for x in range(1, -1, -1):
        if heights[x] > VISIBLE_HEIGHT - 1:
            break
        x_min -= 1
    return x_min, x_max


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


def _base_node_value(node: _Node) -> float:
    return node.best_chain_value - node.premature_penalty + node.evaluation


def _potential_value(potential: BuildPotential) -> float:
    if not potential.exists:
        return 0.0
    return float(potential.chain_count) - 0.25 * float(potential.required_puyos - 1)


def _potential_rank_key(potential: BuildPotential) -> tuple[int, int, int, int, int]:
    color_rank = (
        len(NORMAL_PUYO_COLORS)
        if potential.trigger_color is None
        else NORMAL_PUYO_COLORS.index(potential.trigger_color)
    )
    return (
        int(potential.chain_count),
        -int(potential.required_puyos),
        -int(potential.trigger_x if potential.trigger_x is not None else GRID_WIDTH),
        -int(potential.trigger_y if potential.trigger_y is not None else GRID_HEIGHT),
        -color_rank,
    )


def _same_trigger(root: BuildPotential, selected: BuildPotential) -> bool:
    if not root.exists:
        return not selected.exists
    return (
        selected.exists
        and selected.chain_count >= root.chain_count
        and selected.trigger_x == root.trigger_x
        and selected.trigger_y == root.trigger_y
        and selected.trigger_color == root.trigger_color
    )


def _legal_indices_from_info(info: dict[str, Any]) -> list[int]:
    mask = info.get("action_mask")
    if mask is None:
        return []
    return [index for index, allowed in enumerate(mask) if bool(allowed)]
