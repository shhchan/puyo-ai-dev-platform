"""Compact, deterministic structural-chain evaluation for beam ordering.

The evaluator consumes :class:`CompactSearchState` only.  It deliberately does
not know about mutable simulators, future tsumo queues, or named chain styles.
Feature values are raw counts or documented ratios; score scaling lives in the
versioned YAML configuration so search ordering can be ablated independently.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import yaml

from agents.compact_search import CompactSearchState
from src.core.constants import (
    CHAIN_BONUS_TABLE,
    COLOR_BONUS_TABLE,
    GRID_HEIGHT,
    GRID_WIDTH,
    VISIBLE_HEIGHT,
    PuyoColor,
    get_connection_bonus,
)


CHAIN_STRUCTURE_FEATURE_VERSION = "puyo.chain_structure_features.v1"
CHAIN_STRUCTURE_RESULT_SCHEMA_VERSION = "puyo.chain_structure_evaluation.v1"
CHAIN_STRUCTURE_WEIGHT_SCHEMA_VERSION = "puyo.chain_structure_weights.v1"
DEFAULT_CHAIN_STRUCTURE_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "train"
    / "config"
    / "v1_7_chain_structure.yaml"
)

_STRUCTURE_COLORS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
    PuyoColor.PURPLE,
)
_NORMAL_COLOR_COUNT = len(_STRUCTURE_COLORS)
_OJAMA_PLANE_INDEX = _NORMAL_COLOR_COUNT
_BITS_PER_BOARD = GRID_WIDTH * GRID_HEIGHT
_FULL_BOARD_MASK = (1 << _BITS_PER_BOARD) - 1
_VISIBLE_MASK = (1 << (GRID_WIDTH * VISIBLE_HEIGHT)) - 1
_ROW_14_MASK = sum(1 << ((GRID_HEIGHT - 1) * GRID_WIDTH + x) for x in range(GRID_WIDTH))
_GRAVITY_HEIGHT = GRID_HEIGHT - 1
_NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _cell_bit(x: int, y: int) -> int:
    return 1 << (y * GRID_WIDTH + x)


def _canonical_cells(
    cells: Sequence[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    ordered = tuple(sorted((int(x), int(y)) for x, y in cells))
    mirrored = tuple(sorted((GRID_WIDTH - 1 - x, y) for x, y in ordered))
    return min(ordered, mirrored)


def _stable_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mirror_mask(mask: int) -> int:
    result = 0
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            if int(mask) & _cell_bit(x, y):
                result |= _cell_bit(GRID_WIDTH - 1 - x, y)
    return result


def mirror_state(state: CompactSearchState) -> CompactSearchState:
    """Return the horizontal reflection used by symmetry regression tests."""

    return CompactSearchState(
        planes=tuple(_mirror_mask(plane) for plane in state.planes),
        all_clear_bonus_pending=state.all_clear_bonus_pending,
        game_over=state.game_over,
        score=state.score,
        last_chain_end_score=state.last_chain_end_score,
    )


@dataclass(frozen=True, slots=True)
class ChainStructureBudget:
    max_added_puyos: int = 3
    max_pattern_nodes: int = 512
    max_resolution_nodes: int = 96
    max_candidates: int = 12

    def __post_init__(self) -> None:
        if (
            min(
                self.max_added_puyos,
                self.max_pattern_nodes,
                self.max_resolution_nodes,
                self.max_candidates,
            )
            <= 0
        ):
            raise ValueError("chain-structure budgets must be positive")
        if self.max_added_puyos > 3:
            raise ValueError("chain-structure quiescence supports at most 3 puyos")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChainStructureBudget":
        return cls(
            max_added_puyos=int(value.get("max_added_puyos", 3)),
            max_pattern_nodes=int(value.get("max_pattern_nodes", 512)),
            max_resolution_nodes=int(value.get("max_resolution_nodes", 96)),
            max_candidates=int(value.get("max_candidates", 12)),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_added_puyos": int(self.max_added_puyos),
            "max_pattern_nodes": int(self.max_pattern_nodes),
            "max_resolution_nodes": int(self.max_resolution_nodes),
            "max_candidates": int(self.max_candidates),
        }


@dataclass(frozen=True, slots=True)
class ChainStructureWeights:
    potential_chain_count: float
    potential_chain_score: float
    required_key_count: float
    trigger_height: float
    trigger_protection: float
    remaining_link_2: float
    remaining_link_3: float
    connectivity_edge: float
    connection_candidate: float
    reachable_ignition: float
    growth_site: float
    foundation_cell: float
    fold_space: float
    adjacent_roughness: float
    height_spread: float
    well_depth: float
    bump_height: float
    danger_ratio: float
    nuisance_puyo: float
    hidden_row_puyo: float
    tear: float
    waste: float
    trigger_damage: float
    premature_fire: float

    def __post_init__(self) -> None:
        values = {item.name: float(getattr(self, item.name)) for item in fields(self)}
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("chain-structure weights must be finite")
        rewards = {
            "potential_chain_count",
            "potential_chain_score",
            "trigger_protection",
            "remaining_link_2",
            "remaining_link_3",
            "connectivity_edge",
            "connection_candidate",
            "reachable_ignition",
            "growth_site",
            "foundation_cell",
            "fold_space",
        }
        costs = set(values) - rewards
        if any(values[name] < 0.0 for name in rewards) or any(
            values[name] > 0.0 for name in costs
        ):
            raise ValueError("chain-structure reward/cost weight sign is invalid")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChainStructureWeights":
        expected = {item.name for item in fields(cls)}
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        if missing or unknown:
            raise ValueError(
                f"invalid chain-structure weights: missing={missing}, unknown={unknown}"
            )
        return cls(**{name: float(value[name]) for name in expected})

    def to_dict(self) -> dict[str, float]:
        return {item.name: float(getattr(self, item.name)) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class ChainStructureConfig:
    weight_version: str
    budget: ChainStructureBudget
    weights: ChainStructureWeights
    fatal_score: float
    feature_version: str = CHAIN_STRUCTURE_FEATURE_VERSION
    schema_version: str = CHAIN_STRUCTURE_WEIGHT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CHAIN_STRUCTURE_WEIGHT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported chain-structure weight schema: {self.schema_version}"
            )
        if self.feature_version != CHAIN_STRUCTURE_FEATURE_VERSION:
            raise ValueError(
                f"unsupported chain-structure feature version: {self.feature_version}"
            )
        if not self.weight_version:
            raise ValueError("chain-structure weight version is required")
        if not math.isfinite(self.fatal_score) or self.fatal_score >= 0.0:
            raise ValueError("chain-structure fatal score must be negative")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChainStructureConfig":
        quiescence = value.get("quiescence")
        weights = value.get("weights")
        if not isinstance(quiescence, Mapping) or not isinstance(weights, Mapping):
            raise ValueError("chain-structure config requires quiescence and weights")
        return cls(
            schema_version=str(value.get("schema_version", "")),
            feature_version=str(value.get("feature_version", "")),
            weight_version=str(value.get("weight_version", "")),
            budget=ChainStructureBudget.from_dict(quiescence),
            weights=ChainStructureWeights.from_dict(weights),
            fatal_score=float(value.get("fatal_score", 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "feature_version": self.feature_version,
            "weight_version": self.weight_version,
            "quiescence": self.budget.to_dict(),
            "weights": self.weights.to_dict(),
            "fatal_score": float(self.fatal_score),
        }


def load_chain_structure_config(
    path: str | Path = DEFAULT_CHAIN_STRUCTURE_CONFIG_PATH,
) -> ChainStructureConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise ValueError("chain-structure config must be a mapping")
    return ChainStructureConfig.from_dict(payload)


@dataclass(frozen=True, slots=True)
class ChainComponent:
    component_id: str
    color: PuyoColor
    cells: tuple[tuple[int, int], ...]
    reachable_extensions: tuple[tuple[int, int], ...]
    support_edges: int
    connection_edges: int

    @property
    def size(self) -> int:
        return len(self.cells)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "color": self.color.name,
            "size": self.size,
            "cells": [list(cell) for cell in self.cells],
            "reachable_extensions": [list(cell) for cell in self.reachable_extensions],
            "support_edges": int(self.support_edges),
            "connection_edges": int(self.connection_edges),
        }


@dataclass(frozen=True, slots=True)
class ComponentConnection:
    color: PuyoColor
    source_component_ids: tuple[str, ...]
    bridge_cell: tuple[int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "color": self.color.name,
            "source_component_ids": list(self.source_component_ids),
            "bridge_cell": list(self.bridge_cell),
            "canonical_bridge_cell": list(_canonical_cells((self.bridge_cell,))[0]),
        }


@dataclass(frozen=True, slots=True)
class IgnitionRelation:
    chain_index: int
    color: PuyoColor
    vanished_cells: tuple[tuple[int, int], ...]
    source_component_ids: tuple[str, ...]
    caused_by_chain_index: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_index": int(self.chain_index),
            "color": self.color.name,
            "vanished_cells": [list(cell) for cell in self.vanished_cells],
            "source_component_ids": list(self.source_component_ids),
            "caused_by_chain_index": self.caused_by_chain_index,
        }


@dataclass(frozen=True, slots=True)
class QuiescenceCandidate:
    chain_count: int
    chain_score: int
    required_key_count: int
    trigger_color: PuyoColor
    placements: tuple[tuple[int, int], ...]
    anchor_cells: tuple[tuple[int, int], ...]
    trigger_column: int
    trigger_height: int
    trigger_protection: float
    remaining_link_2: int
    remaining_link_3: int
    remaining_connection_edges: int
    extension_space: int
    relations: tuple[IgnitionRelation, ...]

    @property
    def canonical_signature(self) -> tuple[Any, ...]:
        return (
            self.trigger_color.name,
            _canonical_cells(self.placements),
            _canonical_cells(self.anchor_cells),
            int(self.chain_count),
            int(self.chain_score),
            int(self.required_key_count),
            int(self.trigger_height),
            int(self.remaining_link_2),
            int(self.remaining_link_3),
            int(self.remaining_connection_edges),
            int(self.extension_space),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_count": int(self.chain_count),
            "chain_score": int(self.chain_score),
            "required_key_count": int(self.required_key_count),
            "trigger_color": self.trigger_color.name,
            "placements": [list(cell) for cell in self.placements],
            "anchor_cells": [list(cell) for cell in self.anchor_cells],
            "trigger": {
                "column": int(self.trigger_column),
                "height": int(self.trigger_height),
                "protection": float(self.trigger_protection),
            },
            "remaining_links": {
                "link_2": int(self.remaining_link_2),
                "link_3": int(self.remaining_link_3),
                "connection_edges": int(self.remaining_connection_edges),
            },
            "extension_space": int(self.extension_space),
            "relations": [relation.to_dict() for relation in self.relations],
        }


@dataclass(frozen=True, slots=True)
class QuiescenceSummary:
    best: QuiescenceCandidate | None
    candidates: tuple[QuiescenceCandidate, ...]
    pattern_nodes: int
    resolution_nodes: int
    search_complete: bool
    truncation_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "best": None if self.best is None else self.best.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "search": {
                "pattern_nodes": int(self.pattern_nodes),
                "resolution_nodes": int(self.resolution_nodes),
                "complete": bool(self.search_complete),
                "truncation_reason": self.truncation_reason,
            },
        }


@dataclass(frozen=True, slots=True)
class ChainStructureAction:
    chain_count: int = 0
    score_delta: int = 0
    vanished_count: int = 0
    garbage_cleared_count: int = 0
    game_over: bool = False
    all_clear_achieved: bool = False

    @classmethod
    def from_result(cls, result: Any) -> "ChainStructureAction":
        vanished = getattr(result, "vanished_count", None)
        if vanished is None:
            vanished = sum(
                int(getattr(step, "vanished_count", 0))
                for step in getattr(result, "chains", ())
            )
        garbage = getattr(result, "garbage_cleared_count", None)
        if garbage is None:
            garbage = sum(
                int(getattr(step, "garbage_cleared_count", 0))
                for step in getattr(result, "chains", ())
            )
        return cls(
            chain_count=int(getattr(result, "chain_count", 0)),
            score_delta=int(getattr(result, "score_delta", 0)),
            vanished_count=int(vanished),
            garbage_cleared_count=int(garbage),
            game_over=bool(getattr(result, "game_over", False)),
            all_clear_achieved=bool(getattr(result, "all_clear_achieved", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_count": int(self.chain_count),
            "score_delta": int(self.score_delta),
            "vanished_count": int(self.vanished_count),
            "garbage_cleared_count": int(self.garbage_cleared_count),
            "game_over": bool(self.game_over),
            "all_clear_achieved": bool(self.all_clear_achieved),
        }


@dataclass(frozen=True, slots=True)
class ActionStructureFeatures:
    evaluated: bool = False
    tear_count: int = 0
    waste_count: int = 0
    trigger_damage: int = 0
    premature_fire: bool = False
    danger_delta: float = 0.0
    death: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated": bool(self.evaluated),
            "tear_count": int(self.tear_count),
            "waste_count": int(self.waste_count),
            "trigger_damage": int(self.trigger_damage),
            "premature_fire": bool(self.premature_fire),
            "danger_delta": float(self.danger_delta),
            "death": bool(self.death),
        }


@dataclass(frozen=True, slots=True)
class ChainStructureFeatures:
    canonical_column_heights: tuple[int, ...]
    normal_puyo_count: int
    component_count: int
    isolated_count: int
    link_2: int
    link_3: int
    connectivity_edges: int
    connection_candidate_count: int
    reachable_ignition_count: int
    growth_site_count: int
    foundation_cell_count: int
    fold_space: int
    adjacent_roughness: int
    height_spread: int
    well_depth: int
    bump_height: int
    danger_ratio: float
    nuisance_count: int
    hidden_row_count: int
    trigger_reachable: bool
    trigger_protection: float
    potential_chain_count: int
    potential_chain_score: int
    required_key_count: int | None
    trigger_column: int | None
    trigger_height: int | None
    remaining_link_2: int
    remaining_link_3: int
    remaining_connection_edges: int
    death: bool
    unreachable_trigger: bool
    structural_dead_end: bool

    @property
    def continuation_ratio(self) -> float:
        return min(1.0, self.growth_site_count / float(GRID_WIDTH * 2))

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_heights": list(self.canonical_column_heights),
            "normal_puyo_count": int(self.normal_puyo_count),
            "components": {
                "count": int(self.component_count),
                "isolated": int(self.isolated_count),
                "link_2": int(self.link_2),
                "link_3": int(self.link_3),
                "connectivity_edges": int(self.connectivity_edges),
                "connection_candidates": int(self.connection_candidate_count),
                "reachable_ignitions": int(self.reachable_ignition_count),
            },
            "shape": {
                "growth_sites": int(self.growth_site_count),
                "foundation_cells": int(self.foundation_cell_count),
                "fold_space": int(self.fold_space),
                "adjacent_roughness": int(self.adjacent_roughness),
                "height_spread": int(self.height_spread),
                "well_depth": int(self.well_depth),
                "bump_height": int(self.bump_height),
            },
            "danger": {
                "ratio": float(self.danger_ratio),
                "nuisance_count": int(self.nuisance_count),
                "hidden_row_count": int(self.hidden_row_count),
                "death": bool(self.death),
            },
            "trigger": {
                "reachable": bool(self.trigger_reachable),
                "protection": float(self.trigger_protection),
                "potential_chain_count": int(self.potential_chain_count),
                "potential_chain_score": int(self.potential_chain_score),
                "required_key_count": self.required_key_count,
                "column": self.trigger_column,
                "height": self.trigger_height,
                "remaining_link_2": int(self.remaining_link_2),
                "remaining_link_3": int(self.remaining_link_3),
                "remaining_connection_edges": int(self.remaining_connection_edges),
                "unreachable": bool(self.unreachable_trigger),
            },
            "structural_dead_end": bool(self.structural_dead_end),
        }


@dataclass(frozen=True, slots=True)
class ChainStructureScoreBreakdown:
    quiescence_chain: float = 0.0
    key_cost: float = 0.0
    trigger_position: float = 0.0
    remaining_links: float = 0.0
    component_connectivity: float = 0.0
    connection_potential: float = 0.0
    shape: float = 0.0
    danger: float = 0.0
    nuisance: float = 0.0
    tear: float = 0.0
    waste: float = 0.0
    trigger_damage: float = 0.0
    premature_fire: float = 0.0
    fatal: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {item.name: float(getattr(self, item.name)) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class ChainStructureResult:
    evaluation_status: str
    evaluated: bool
    score: float | None
    features: ChainStructureFeatures | None
    components: tuple[ChainComponent, ...]
    connection_candidates: tuple[ComponentConnection, ...]
    quiescence: QuiescenceSummary | None
    action_features: ActionStructureFeatures
    score_breakdown: ChainStructureScoreBreakdown
    tie_break_digest: str
    weight_version: str
    truncation_reason: str | None = None
    feature_version: str = CHAIN_STRUCTURE_FEATURE_VERSION
    schema_version: str = CHAIN_STRUCTURE_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CHAIN_STRUCTURE_RESULT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported chain-structure result schema: {self.schema_version}"
            )
        if self.feature_version != CHAIN_STRUCTURE_FEATURE_VERSION:
            raise ValueError(
                f"unsupported chain-structure feature version: {self.feature_version}"
            )
        if self.evaluation_status not in {
            "available",
            "not_found",
            "budget_exhausted",
            "not_evaluated",
        }:
            raise ValueError(
                f"unsupported chain-structure status: {self.evaluation_status}"
            )
        if self.evaluated != (self.evaluation_status != "not_evaluated"):
            raise ValueError("chain-structure evaluated flag disagrees with status")
        if self.evaluated and (self.score is None or self.features is None):
            raise ValueError("evaluated chain-structure result requires score/features")
        if not self.evaluated and (self.score is not None or self.features is not None):
            raise ValueError(
                "unevaluated chain-structure result cannot contain a score"
            )

    @classmethod
    def not_evaluated(
        cls,
        *,
        weight_version: str,
        reason: str,
    ) -> "ChainStructureResult":
        digest = _stable_digest(
            {
                "feature_version": CHAIN_STRUCTURE_FEATURE_VERSION,
                "weight_version": weight_version,
                "status": "not_evaluated",
                "reason": reason,
            }
        )
        return cls(
            evaluation_status="not_evaluated",
            evaluated=False,
            score=None,
            features=None,
            components=(),
            connection_candidates=(),
            quiescence=None,
            action_features=ActionStructureFeatures(),
            score_breakdown=ChainStructureScoreBreakdown(),
            tie_break_digest=digest,
            weight_version=weight_version,
            truncation_reason=reason,
        )

    @property
    def danger(self) -> float:
        return 1.0 if self.features is None else float(self.features.danger_ratio)

    @property
    def continuation_flexibility(self) -> float:
        return 0.0 if self.features is None else float(self.features.continuation_ratio)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "feature_version": self.feature_version,
            "weight_version": self.weight_version,
            "metric_namespace": "generic_chain_structure",
            "evaluation_status": self.evaluation_status,
            "evaluated": bool(self.evaluated),
            "truncation_reason": self.truncation_reason,
            "score": None if self.score is None else float(self.score),
            "tie_break_digest": self.tie_break_digest,
            "features": None if self.features is None else self.features.to_dict(),
            "components": [component.to_dict() for component in self.components],
            "connection_candidates": [
                candidate.to_dict() for candidate in self.connection_candidates
            ],
            "quiescence": (
                None if self.quiescence is None else self.quiescence.to_dict()
            ),
            "action": self.action_features.to_dict(),
            "score_breakdown": self.score_breakdown.to_dict(),
        }


class CompactNodeEvaluator(Protocol):
    def evaluate(
        self,
        state: CompactSearchState,
        *,
        parent: ChainStructureResult | None = None,
        action: ChainStructureAction | None = None,
        target_chain_count: int = 6,
    ) -> ChainStructureResult: ...


class ChainStructureEvaluator:
    """Evaluate generic chain capacity with a count-bounded compact probe."""

    def __init__(self, config: ChainStructureConfig | None = None) -> None:
        self.config = config or load_chain_structure_config()

    def evaluate(
        self,
        state: CompactSearchState,
        *,
        parent: ChainStructureResult | None = None,
        action: ChainStructureAction | None = None,
        target_chain_count: int = 6,
    ) -> ChainStructureResult:
        if target_chain_count < 1:
            raise ValueError("target chain count must be positive")
        components = extract_components(state)
        connections = connection_candidates(state, components)
        quiescence = bounded_quiescence(
            state,
            components=components,
            budget=self.config.budget,
        )
        features = _build_features(
            state,
            components=components,
            connections=connections,
            quiescence=quiescence,
        )
        action_features = _action_features(
            features,
            parent=parent,
            action=action,
            target_chain_count=target_chain_count,
        )
        breakdown = _score(
            features,
            action_features=action_features,
            weights=self.config.weights,
            fatal_score=self.config.fatal_score,
        )
        status = (
            "budget_exhausted"
            if not quiescence.search_complete
            else "available"
            if quiescence.best is not None
            else "not_found"
        )
        digest = _evaluation_digest(
            state,
            features=features,
            quiescence=quiescence,
            action_features=action_features,
            weight_version=self.config.weight_version,
        )
        return ChainStructureResult(
            evaluation_status=status,
            evaluated=True,
            score=breakdown.total,
            features=features,
            components=components,
            connection_candidates=connections,
            quiescence=quiescence,
            action_features=action_features,
            score_breakdown=breakdown,
            tie_break_digest=digest,
            weight_version=self.config.weight_version,
            truncation_reason=quiescence.truncation_reason,
        )


def extract_components(
    state: CompactSearchState,
) -> tuple[ChainComponent, ...]:
    """Extract color components with mirror-stable identifiers."""

    occupied = state.occupied_mask
    heights = state.column_heights
    reachable_columns = _reachable_columns(heights)
    components: list[ChainComponent] = []
    component_ordinals: dict[tuple[str, tuple[tuple[int, int], ...]], int] = {}
    for plane_index, color in enumerate(_STRUCTURE_COLORS):
        plane = int(state.planes[plane_index])
        visited = 0
        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                bit = _cell_bit(x, y)
                if not plane & bit or visited & bit:
                    continue
                stack = [(x, y)]
                cells: set[tuple[int, int]] = set()
                while stack:
                    cell_x, cell_y = stack.pop()
                    if not (0 <= cell_x < GRID_WIDTH and 0 <= cell_y < GRID_HEIGHT):
                        continue
                    cell_bit = _cell_bit(cell_x, cell_y)
                    if not plane & cell_bit or (cell_x, cell_y) in cells:
                        continue
                    cells.add((cell_x, cell_y))
                    stack.extend((cell_x + dx, cell_y + dy) for dx, dy in _NEIGHBORS)
                for cell_x, cell_y in cells:
                    visited |= _cell_bit(cell_x, cell_y)
                ordered = tuple(sorted(cells))
                canonical = _canonical_cells(ordered)
                identity_key = (color.name, canonical)
                ordinal = component_ordinals.get(identity_key, 0)
                component_ordinals[identity_key] = ordinal + 1
                identifier = (
                    _stable_digest(
                        {
                            "color": color.name,
                            "cells": canonical,
                        }
                    )[:16]
                    + f":{ordinal}"
                )
                extensions = _component_extensions(
                    cells,
                    occupied=occupied,
                    heights=heights,
                    reachable_columns=reachable_columns,
                )
                connection_edges = sum(
                    int((cell_x + 1, cell_y) in cells)
                    + int((cell_x, cell_y + 1) in cells)
                    for cell_x, cell_y in cells
                )
                support_edges = sum(
                    cell_y == 0 or bool(occupied & _cell_bit(cell_x, cell_y - 1))
                    for cell_x, cell_y in cells
                )
                components.append(
                    ChainComponent(
                        component_id=identifier,
                        color=color,
                        cells=ordered,
                        reachable_extensions=extensions,
                        support_edges=int(support_edges),
                        connection_edges=int(connection_edges),
                    )
                )
    return tuple(
        sorted(
            components,
            key=lambda component: (
                component.color.value,
                component.component_id,
            ),
        )
    )


def connection_candidates(
    state: CompactSearchState,
    components: Sequence[ChainComponent] | None = None,
) -> tuple[ComponentConnection, ...]:
    """Find one-key gravity-reachable bridges between same-color components."""

    selected = tuple(components or extract_components(state))
    by_cell = {cell: component for component in selected for cell in component.cells}
    heights = state.column_heights
    reachable = _reachable_columns(heights)
    result: list[ComponentConnection] = []
    for x in reachable:
        y = heights[x]
        if y >= VISIBLE_HEIGHT or state.occupied_mask & _cell_bit(x, y):
            continue
        adjacent: dict[PuyoColor, set[str]] = {}
        for dx, dy in _NEIGHBORS:
            component = by_cell.get((x + dx, y + dy))
            if component is not None:
                adjacent.setdefault(component.color, set()).add(component.component_id)
        for color, identifiers in adjacent.items():
            if len(identifiers) < 2:
                continue
            result.append(
                ComponentConnection(
                    color=color,
                    source_component_ids=tuple(sorted(identifiers)),
                    bridge_cell=(x, y),
                )
            )
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.color.value,
                item.source_component_ids,
                _canonical_cells((item.bridge_cell,)),
            ),
        )
    )


def bounded_quiescence(
    state: CompactSearchState,
    *,
    components: Sequence[ChainComponent] | None = None,
    budget: ChainStructureBudget | None = None,
) -> QuiescenceSummary:
    """Search minimal 1-3 puyo virtual ignitions without simulator clones."""

    selected_budget = budget or ChainStructureBudget()
    selected_components = tuple(components or extract_components(state))
    component_by_cell = {
        cell: component for component in selected_components for cell in component.cells
    }
    heights = state.column_heights
    reachable = frozenset(_reachable_columns(heights))
    candidates: list[QuiescenceCandidate] = []
    seen: set[tuple[Any, ...]] = set()
    pattern_nodes = 0
    resolution_nodes = 0
    truncation_reason: str | None = None

    for added_puyos in range(1, selected_budget.max_added_puyos + 1):
        for orbit in _column_pattern_orbits(added_puyos):
            valid_patterns = []
            for columns in orbit:
                if any(column not in reachable for column in columns):
                    continue
                counts = tuple(columns.count(x) for x in range(GRID_WIDTH))
                if any(
                    heights[x] + count > VISIBLE_HEIGHT
                    for x, count in enumerate(counts)
                ):
                    continue
                valid_patterns.append(
                    tuple(
                        (x, heights[x] + offset)
                        for x, count in enumerate(counts)
                        for offset in range(count)
                    )
                )
            if not valid_patterns:
                continue
            for plane_index, color in enumerate(_STRUCTURE_COLORS):
                if (
                    pattern_nodes + len(valid_patterns)
                    > selected_budget.max_pattern_nodes
                ):
                    truncation_reason = "pattern_nodes"
                    break
                pattern_nodes += len(valid_patterns)
                pending = []
                for placements in valid_patterns:
                    anchors = _virtual_trigger_anchors(
                        state.planes[plane_index],
                        placements,
                    )
                    if anchors is None:
                        continue
                    if any(
                        _virtual_trigger_anchors(
                            state.planes[plane_index],
                            smaller,
                        )
                        is not None
                        for smaller in _smaller_gravity_patterns(
                            placements,
                            heights=heights,
                        )
                    ):
                        continue
                    pending.append((placements, anchors))
                if (
                    resolution_nodes + len(pending)
                    > selected_budget.max_resolution_nodes
                ):
                    truncation_reason = "resolution_nodes"
                    break
                for placements, anchors in pending:
                    planes = list(state.planes)
                    for x, y in placements:
                        planes[plane_index] |= _cell_bit(x, y)
                    resolved = _resolve_virtual(
                        tuple(planes),
                        component_by_cell=component_by_cell,
                    )
                    resolution_nodes += 1
                    if resolved.chain_count == 0:
                        continue
                    remaining = _components_from_planes(resolved.planes)
                    link_2 = sum(len(cells) == 2 for _, cells in remaining)
                    link_3 = sum(len(cells) == 3 for _, cells in remaining)
                    connection_edges = sum(
                        _connection_edge_count(cells) for _, cells in remaining
                    )
                    extension_space = _extension_space(
                        resolved.planes,
                        remaining,
                    )
                    protection = _trigger_protection(
                        state,
                        anchors=anchors,
                        placements=placements,
                    )
                    candidate = QuiescenceCandidate(
                        chain_count=resolved.chain_count,
                        chain_score=resolved.score,
                        required_key_count=added_puyos,
                        trigger_color=color,
                        placements=placements,
                        anchor_cells=anchors,
                        trigger_column=min(x for x, _ in placements),
                        trigger_height=min(y for _, y in placements),
                        trigger_protection=protection,
                        remaining_link_2=int(link_2),
                        remaining_link_3=int(link_3),
                        remaining_connection_edges=int(connection_edges),
                        extension_space=int(extension_space),
                        relations=resolved.relations,
                    )
                    signature = candidate.canonical_signature
                    if signature in seen:
                        continue
                    seen.add(signature)
                    candidates.append(candidate)
            if truncation_reason is not None:
                break
        if truncation_reason is not None:
            break

    ranked = tuple(sorted(candidates, key=_candidate_rank_key, reverse=True))
    retained = ranked[: selected_budget.max_candidates]
    return QuiescenceSummary(
        best=(retained[0] if retained else None),
        candidates=retained,
        pattern_nodes=pattern_nodes,
        resolution_nodes=resolution_nodes,
        search_complete=truncation_reason is None,
        truncation_reason=truncation_reason,
    )


@dataclass(frozen=True, slots=True)
class _ResolvedVirtual:
    planes: tuple[int, ...]
    chain_count: int
    score: int
    relations: tuple[IgnitionRelation, ...]


def _resolve_virtual(
    planes: tuple[int, ...],
    *,
    component_by_cell: Mapping[tuple[int, int], ChainComponent],
) -> _ResolvedVirtual:
    current = tuple(int(plane) for plane in planes)
    provenance = {
        cell: component.component_id for cell, component in component_by_cell.items()
    }
    score = 0
    relations: list[IgnitionRelation] = []
    chain_index = 0
    while True:
        groups = _vanishing_groups(current)
        if not groups:
            break
        chain_index += 1
        vanished = frozenset(cell for _, group in groups for cell in group)
        garbage = _adjacent_ojama(current[_OJAMA_PLANE_INDEX], vanished)
        colors = {plane_index for plane_index, _ in groups}
        chain_bonus = CHAIN_BONUS_TABLE[min(chain_index, len(CHAIN_BONUS_TABLE) - 1)]
        connection_bonus = sum(get_connection_bonus(len(group)) for _, group in groups)
        color_bonus = COLOR_BONUS_TABLE.get(len(colors), 0)
        bonus = max(1, chain_bonus + connection_bonus + color_bonus)
        score += len(vanished) * 10 * bonus
        for plane_index, group in groups:
            source_ids = tuple(
                sorted({provenance[cell] for cell in group if cell in provenance})
            )
            relations.append(
                IgnitionRelation(
                    chain_index=chain_index,
                    color=_STRUCTURE_COLORS[plane_index],
                    vanished_cells=tuple(sorted(group)),
                    source_component_ids=source_ids,
                    caused_by_chain_index=(
                        None if chain_index == 1 else chain_index - 1
                    ),
                )
            )
        cleared = _clear_cells(current, vanished, garbage)
        provenance = _apply_provenance_gravity(provenance, cleared)
        current = _apply_gravity(cleared)
    return _ResolvedVirtual(
        planes=current,
        chain_count=chain_index,
        score=score,
        relations=tuple(relations),
    )


def _vanishing_groups(
    planes: Sequence[int],
) -> tuple[tuple[int, frozenset[tuple[int, int]]], ...]:
    result = []
    for plane_index in range(_NORMAL_COLOR_COUNT):
        plane = int(planes[plane_index]) & _VISIBLE_MASK
        visited = 0
        for y in range(VISIBLE_HEIGHT):
            for x in range(GRID_WIDTH):
                bit = _cell_bit(x, y)
                if not plane & bit or visited & bit:
                    continue
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
                    stack.extend((cell_x + dx, cell_y + dy) for dx, dy in _NEIGHBORS)
                for cell_x, cell_y in group:
                    visited |= _cell_bit(cell_x, cell_y)
                if len(group) >= 4:
                    result.append((plane_index, frozenset(group)))
    return tuple(result)


def _adjacent_ojama(
    ojama_plane: int,
    vanished: Sequence[tuple[int, int]],
) -> frozenset[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for x, y in vanished:
        for dx, dy in _NEIGHBORS:
            target_x, target_y = x + dx, y + dy
            if not (0 <= target_x < GRID_WIDTH and 0 <= target_y < VISIBLE_HEIGHT):
                continue
            if int(ojama_plane) & _cell_bit(target_x, target_y):
                result.add((target_x, target_y))
    return frozenset(result)


def _clear_cells(
    planes: Sequence[int],
    vanished: Sequence[tuple[int, int]],
    garbage: Sequence[tuple[int, int]],
) -> tuple[int, ...]:
    vanished_mask = sum(_cell_bit(x, y) for x, y in vanished)
    garbage_mask = sum(_cell_bit(x, y) for x, y in garbage)
    result = [int(plane) & ~vanished_mask for plane in planes]
    result[_OJAMA_PLANE_INDEX] &= ~garbage_mask
    return tuple(result)


def _apply_gravity(planes: Sequence[int]) -> tuple[int, ...]:
    result = [int(plane) & _ROW_14_MASK for plane in planes]
    for x in range(GRID_WIDTH):
        target_y = 0
        for source_y in range(_GRAVITY_HEIGHT):
            bit = _cell_bit(x, source_y)
            for plane_index, plane in enumerate(planes):
                if int(plane) & bit:
                    result[plane_index] |= _cell_bit(x, target_y)
                    target_y += 1
                    break
    return tuple(result)


def _apply_provenance_gravity(
    provenance: Mapping[tuple[int, int], str],
    planes: Sequence[int],
) -> dict[tuple[int, int], str]:
    occupied = _occupied_mask(planes)
    result = {}
    for x in range(GRID_WIDTH):
        top = (x, GRID_HEIGHT - 1)
        if occupied & _cell_bit(*top) and top in provenance:
            result[top] = provenance[top]
        target_y = 0
        for source_y in range(_GRAVITY_HEIGHT):
            if not occupied & _cell_bit(x, source_y):
                continue
            source = (x, source_y)
            if source in provenance:
                result[(x, target_y)] = provenance[source]
            target_y += 1
    return result


def _virtual_trigger_anchors(
    plane: int,
    placements: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...] | None:
    virtual = frozenset(placements)
    combined = int(plane)
    for x, y in placements:
        combined |= _cell_bit(x, y)
    visited: set[tuple[int, int]] = set()
    for origin in placements:
        if origin in visited:
            continue
        stack = [origin]
        component: set[tuple[int, int]] = set()
        while stack:
            x, y = stack.pop()
            if not (0 <= x < GRID_WIDTH and 0 <= y < VISIBLE_HEIGHT):
                continue
            bit = _cell_bit(x, y)
            if not combined & bit or (x, y) in component:
                continue
            component.add((x, y))
            stack.extend((x + dx, y + dy) for dx, dy in _NEIGHBORS)
        visited.update(component)
        anchors = tuple(sorted(cell for cell in component if cell not in virtual))
        if len(component) >= 4 and anchors:
            return anchors
    return None


@lru_cache(maxsize=3)
def _column_pattern_orbits(
    added_puyos: int,
) -> tuple[tuple[tuple[int, ...], ...], ...]:
    orbits = []
    for columns in itertools.combinations_with_replacement(
        range(GRID_WIDTH),
        added_puyos,
    ):
        mirrored = tuple(sorted(GRID_WIDTH - 1 - x for x in columns))
        canonical = min(columns, mirrored)
        if columns != canonical:
            continue
        orbits.append(
            (columns,) if columns == mirrored else tuple(sorted((columns, mirrored)))
        )
    return tuple(orbits)


def _smaller_gravity_patterns(
    placements: tuple[tuple[int, int], ...],
    *,
    heights: tuple[int, ...],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    if len(placements) <= 1:
        return ()
    counts = tuple(
        sum(x == column for x, _ in placements) for column in range(GRID_WIDTH)
    )
    result = []
    for removed_column, count in enumerate(counts):
        if count == 0:
            continue
        reduced = list(counts)
        reduced[removed_column] -= 1
        result.append(
            tuple(
                (column, heights[column] + offset)
                for column, reduced_count in enumerate(reduced)
                for offset in range(reduced_count)
            )
        )
    return tuple(result)


def _components_from_planes(
    planes: Sequence[int],
) -> tuple[tuple[int, frozenset[tuple[int, int]]], ...]:
    result = []
    for plane_index in range(_NORMAL_COLOR_COUNT):
        plane = int(planes[plane_index])
        visited = 0
        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                bit = _cell_bit(x, y)
                if not plane & bit or visited & bit:
                    continue
                stack = [(x, y)]
                component: set[tuple[int, int]] = set()
                while stack:
                    cell_x, cell_y = stack.pop()
                    if not (0 <= cell_x < GRID_WIDTH and 0 <= cell_y < GRID_HEIGHT):
                        continue
                    cell_bit = _cell_bit(cell_x, cell_y)
                    if not plane & cell_bit or (cell_x, cell_y) in component:
                        continue
                    component.add((cell_x, cell_y))
                    stack.extend((cell_x + dx, cell_y + dy) for dx, dy in _NEIGHBORS)
                for cell_x, cell_y in component:
                    visited |= _cell_bit(cell_x, cell_y)
                result.append((plane_index, frozenset(component)))
    return tuple(result)


def _connection_edge_count(cells: Sequence[tuple[int, int]]) -> int:
    selected = frozenset(cells)
    return sum(
        int((x + 1, y) in selected) + int((x, y + 1) in selected) for x, y in selected
    )


def _occupied_mask(planes: Sequence[int]) -> int:
    occupied = 0
    for plane in planes:
        occupied |= int(plane)
    return occupied & _FULL_BOARD_MASK


def _heights_from_planes(planes: Sequence[int]) -> tuple[int, ...]:
    occupied = _occupied_mask(planes)
    heights = []
    for x in range(GRID_WIDTH):
        height = 0
        for y in range(GRID_HEIGHT - 1, -1, -1):
            if occupied & _cell_bit(x, y):
                height = y + 1
                break
        heights.append(height)
    return tuple(heights)


def _extension_space(
    planes: Sequence[int],
    components: Sequence[tuple[int, frozenset[tuple[int, int]]]],
) -> int:
    occupied = _occupied_mask(planes)
    heights = _heights_from_planes(planes)
    reachable = frozenset(_reachable_columns(heights))
    extensions = set()
    for _, cells in components:
        for x, y in cells:
            for dx, dy in _NEIGHBORS:
                target_x, target_y = x + dx, y + dy
                if target_x not in reachable or not (0 <= target_y < VISIBLE_HEIGHT):
                    continue
                if target_y != heights[target_x]:
                    continue
                if not occupied & _cell_bit(target_x, target_y):
                    extensions.add((target_x, target_y))
    return len(extensions)


def _component_extensions(
    cells: set[tuple[int, int]],
    *,
    occupied: int,
    heights: tuple[int, ...],
    reachable_columns: Sequence[int],
) -> tuple[tuple[int, int], ...]:
    reachable = frozenset(reachable_columns)
    result = set()
    for x, y in cells:
        for dx, dy in _NEIGHBORS:
            target_x, target_y = x + dx, y + dy
            if target_x not in reachable or not (0 <= target_y < VISIBLE_HEIGHT):
                continue
            if target_y != heights[target_x]:
                continue
            if not occupied & _cell_bit(target_x, target_y):
                result.add((target_x, target_y))
    return tuple(sorted(result))


def _reachable_columns(heights: Sequence[int]) -> tuple[int, ...]:
    open_columns = {
        x for x, height in enumerate(heights) if int(height) < VISIBLE_HEIGHT
    }
    frontier = [x for x in (2, 3) if x in open_columns]
    reachable = set(frontier)
    while frontier:
        x = frontier.pop()
        for neighbor in (x - 1, x + 1):
            if neighbor in open_columns and neighbor not in reachable:
                reachable.add(neighbor)
                frontier.append(neighbor)
    return tuple(sorted(reachable))


def _trigger_protection(
    state: CompactSearchState,
    *,
    anchors: Sequence[tuple[int, int]],
    placements: Sequence[tuple[int, int]],
) -> float:
    if not anchors:
        return 0.0
    occupied = state.occupied_mask
    virtual = frozenset(placements)
    protected_sides = 0
    possible_sides = 0
    for x, y in anchors:
        for dx, dy in _NEIGHBORS:
            target_x, target_y = x + dx, y + dy
            if not (0 <= target_x < GRID_WIDTH and 0 <= target_y < VISIBLE_HEIGHT):
                continue
            possible_sides += 1
            bit = _cell_bit(target_x, target_y)
            if occupied & bit and (target_x, target_y) not in virtual:
                protected_sides += 1
    return 0.0 if possible_sides == 0 else protected_sides / float(possible_sides)


def _candidate_rank_key(candidate: QuiescenceCandidate) -> tuple[Any, ...]:
    return (
        int(candidate.chain_count),
        -int(candidate.required_key_count),
        int(candidate.chain_score),
        int(candidate.remaining_link_3),
        int(candidate.remaining_link_2),
        int(candidate.remaining_connection_edges),
        int(candidate.extension_space),
        float(candidate.trigger_protection),
        -int(candidate.trigger_height),
        _stable_digest(candidate.canonical_signature),
    )


def _build_features(
    state: CompactSearchState,
    *,
    components: Sequence[ChainComponent],
    connections: Sequence[ComponentConnection],
    quiescence: QuiescenceSummary,
) -> ChainStructureFeatures:
    heights = tuple(int(value) for value in state.column_heights)
    canonical_heights = min(heights, tuple(reversed(heights)))
    occupied = state.occupied_mask
    normal_count = sum(
        int(state.planes[index]).bit_count() for index in range(_NORMAL_COLOR_COUNT)
    )
    nuisance_count = int(state.planes[_OJAMA_PLANE_INDEX]).bit_count()
    hidden_row_count = sum(
        bool(occupied & _cell_bit(x, y))
        for y in range(VISIBLE_HEIGHT, GRID_HEIGHT)
        for x in range(GRID_WIDTH)
    )
    isolated = sum(component.size == 1 for component in components)
    link_2 = sum(component.size == 2 for component in components)
    link_3 = sum(component.size == 3 for component in components)
    connectivity_edges = sum(component.connection_edges for component in components)
    reachable_ignitions = sum(
        component.size == 3 and bool(component.reachable_extensions)
        for component in components
    )
    growth_sites = len(
        {cell for component in components for cell in component.reachable_extensions}
    )
    foundation = sum(
        y <= 2 and (y == 0 or bool(occupied & _cell_bit(x, y - 1)))
        for component in components
        for x, y in component.cells
    )
    adjacent_roughness = sum(
        abs(left - right) for left, right in zip(heights, heights[1:])
    )
    height_spread = max(heights, default=0) - min(heights, default=0)
    well_depth = 0
    bump_height = 0
    for x, height in enumerate(heights):
        left = heights[x - 1] if x > 0 else heights[1]
        right = heights[x + 1] if x < GRID_WIDTH - 1 else heights[-2]
        if height < left and height < right:
            well_depth += min(left, right) - height
        if 0 < x < GRID_WIDTH - 1 and height > left and height > right:
            bump_height += height - max(left, right)
    headroom = tuple(max(0, VISIBLE_HEIGHT - height) for height in heights)
    fold_space = (
        sum(min(left, right) for left, right in zip(headroom, headroom[1:]))
        if normal_count
        else 0
    )
    peak = max(heights, default=0) / float(VISIBLE_HEIGHT)
    center = max(heights[2], heights[3]) / float(VISIBLE_HEIGHT)
    nuisance_ratio = min(1.0, nuisance_count / 30.0)
    danger_ratio = min(1.0, center * 0.55 + peak * 0.35 + nuisance_ratio * 0.10)
    death = bool(state.game_over or max(heights[2], heights[3]) >= VISIBLE_HEIGHT)
    best = quiescence.best
    unreachable = bool(normal_count > 0 and quiescence.search_complete and best is None)
    dead_end = bool(unreachable and not connections and growth_sites == 0)
    return ChainStructureFeatures(
        canonical_column_heights=canonical_heights,
        normal_puyo_count=normal_count,
        component_count=len(components),
        isolated_count=int(isolated),
        link_2=int(link_2),
        link_3=int(link_3),
        connectivity_edges=int(connectivity_edges),
        connection_candidate_count=len(connections),
        reachable_ignition_count=int(reachable_ignitions),
        growth_site_count=int(growth_sites),
        foundation_cell_count=int(foundation),
        fold_space=int(fold_space),
        adjacent_roughness=int(adjacent_roughness),
        height_spread=int(height_spread),
        well_depth=int(well_depth),
        bump_height=int(bump_height),
        danger_ratio=float(danger_ratio),
        nuisance_count=nuisance_count,
        hidden_row_count=int(hidden_row_count),
        trigger_reachable=best is not None,
        trigger_protection=(0.0 if best is None else best.trigger_protection),
        potential_chain_count=(0 if best is None else best.chain_count),
        potential_chain_score=(0 if best is None else best.chain_score),
        required_key_count=(None if best is None else best.required_key_count),
        trigger_column=(
            None
            if best is None
            else min(x for x, _ in _canonical_cells(best.placements))
        ),
        trigger_height=(None if best is None else best.trigger_height),
        remaining_link_2=(0 if best is None else best.remaining_link_2),
        remaining_link_3=(0 if best is None else best.remaining_link_3),
        remaining_connection_edges=(
            0 if best is None else best.remaining_connection_edges
        ),
        death=death,
        unreachable_trigger=unreachable,
        structural_dead_end=dead_end,
    )


def _action_features(
    features: ChainStructureFeatures,
    *,
    parent: ChainStructureResult | None,
    action: ChainStructureAction | None,
    target_chain_count: int,
) -> ActionStructureFeatures:
    if parent is None or parent.features is None or action is None:
        return ActionStructureFeatures(death=features.death)
    before = parent.features
    premature = 0 < action.chain_count < target_chain_count
    target_achieved = action.chain_count >= target_chain_count
    link_loss = max(
        0,
        before.connectivity_edges - features.connectivity_edges,
    )
    bridge_loss = max(
        0,
        before.connection_candidate_count - features.connection_candidate_count,
    )
    tear = 0 if target_achieved else link_loss + bridge_loss
    expected_normal = before.normal_puyo_count + 2
    resource_loss = max(0, expected_normal - features.normal_puyo_count)
    hidden_growth = max(0, features.hidden_row_count - before.hidden_row_count)
    waste = hidden_growth + (
        max(resource_loss, action.vanished_count) if premature else 0
    )
    trigger_damage = 0
    if before.trigger_reachable and not target_achieved:
        if features.unreachable_trigger:
            trigger_damage = max(1, before.potential_chain_count)
        elif features.trigger_reachable:
            trigger_damage += max(
                0,
                before.potential_chain_count - features.potential_chain_count,
            )
            if (
                before.required_key_count is not None
                and features.required_key_count is not None
            ):
                trigger_damage += max(
                    0,
                    features.required_key_count - before.required_key_count,
                )
    return ActionStructureFeatures(
        evaluated=True,
        tear_count=int(tear),
        waste_count=int(waste),
        trigger_damage=int(trigger_damage),
        premature_fire=premature,
        danger_delta=float(features.danger_ratio - before.danger_ratio),
        death=bool(features.death or action.game_over),
    )


def _score(
    features: ChainStructureFeatures,
    *,
    action_features: ActionStructureFeatures,
    weights: ChainStructureWeights,
    fatal_score: float,
) -> ChainStructureScoreBreakdown:
    key_count = (
        0 if features.required_key_count is None else features.required_key_count
    )
    trigger_height = 0 if features.trigger_height is None else features.trigger_height
    quiescence_chain = (
        features.potential_chain_count * weights.potential_chain_count
        + features.potential_chain_score * weights.potential_chain_score
    )
    key_cost = key_count * weights.required_key_count
    trigger_position = (
        trigger_height * weights.trigger_height
        + features.trigger_protection * weights.trigger_protection
    )
    remaining_links = (
        features.remaining_link_2 * weights.remaining_link_2
        + features.remaining_link_3 * weights.remaining_link_3
        + features.remaining_connection_edges * weights.connectivity_edge
    )
    component_connectivity = features.connectivity_edges * weights.connectivity_edge
    connection_potential = (
        features.connection_candidate_count * weights.connection_candidate
        + features.reachable_ignition_count * weights.reachable_ignition
    )
    shape = (
        features.growth_site_count * weights.growth_site
        + features.foundation_cell_count * weights.foundation_cell
        + features.fold_space * weights.fold_space
        + features.adjacent_roughness * weights.adjacent_roughness
        + features.height_spread * weights.height_spread
        + features.well_depth * weights.well_depth
        + features.bump_height * weights.bump_height
    )
    danger = features.danger_ratio * weights.danger_ratio
    nuisance = (
        features.nuisance_count * weights.nuisance_puyo
        + features.hidden_row_count * weights.hidden_row_puyo
    )
    tear = action_features.tear_count * weights.tear
    waste = action_features.waste_count * weights.waste
    trigger_damage = action_features.trigger_damage * weights.trigger_damage
    premature_fire = weights.premature_fire if action_features.premature_fire else 0.0
    regular_total = sum(
        (
            quiescence_chain,
            key_cost,
            trigger_position,
            remaining_links,
            component_connectivity,
            connection_potential,
            shape,
            danger,
            nuisance,
            tear,
            waste,
            trigger_damage,
            premature_fire,
        )
    )
    fatal = bool(
        features.death
        or features.unreachable_trigger
        or features.structural_dead_end
        or action_features.death
    )
    return ChainStructureScoreBreakdown(
        quiescence_chain=float(quiescence_chain),
        key_cost=float(key_cost),
        trigger_position=float(trigger_position),
        remaining_links=float(remaining_links),
        component_connectivity=float(component_connectivity),
        connection_potential=float(connection_potential),
        shape=float(shape),
        danger=float(danger),
        nuisance=float(nuisance),
        tear=float(tear),
        waste=float(waste),
        trigger_damage=float(trigger_damage),
        premature_fire=float(premature_fire),
        fatal=(float(fatal_score) if fatal else 0.0),
        total=(float(fatal_score) if fatal else float(regular_total)),
    )


def _evaluation_digest(
    state: CompactSearchState,
    *,
    features: ChainStructureFeatures,
    quiescence: QuiescenceSummary,
    action_features: ActionStructureFeatures,
    weight_version: str,
) -> str:
    mirrored = tuple(_mirror_mask(plane) for plane in state.planes)
    canonical_planes = min(tuple(state.planes), mirrored)
    return _stable_digest(
        {
            "feature_version": CHAIN_STRUCTURE_FEATURE_VERSION,
            "weight_version": weight_version,
            "planes": canonical_planes,
            "lifecycle": {
                "game_over": state.game_over,
                "all_clear_bonus_pending": state.all_clear_bonus_pending,
            },
            "features": features.to_dict(),
            "best_quiescence": (
                None if quiescence.best is None else quiescence.best.canonical_signature
            ),
            "search": {
                "pattern_nodes": quiescence.pattern_nodes,
                "resolution_nodes": quiescence.resolution_nodes,
                "complete": quiescence.search_complete,
                "truncation_reason": quiescence.truncation_reason,
            },
            "action": action_features.to_dict(),
        }
    )


__all__ = [
    "CHAIN_STRUCTURE_FEATURE_VERSION",
    "CHAIN_STRUCTURE_RESULT_SCHEMA_VERSION",
    "CHAIN_STRUCTURE_WEIGHT_SCHEMA_VERSION",
    "ActionStructureFeatures",
    "ChainComponent",
    "ChainStructureAction",
    "ChainStructureBudget",
    "ChainStructureConfig",
    "ChainStructureEvaluator",
    "ChainStructureFeatures",
    "ChainStructureResult",
    "ChainStructureScoreBreakdown",
    "ChainStructureWeights",
    "CompactNodeEvaluator",
    "ComponentConnection",
    "IgnitionRelation",
    "QuiescenceCandidate",
    "QuiescenceSummary",
    "bounded_quiescence",
    "connection_candidates",
    "extract_components",
    "load_chain_structure_config",
    "mirror_state",
]
