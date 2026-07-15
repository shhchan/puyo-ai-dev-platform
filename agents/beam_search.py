"""Ama-inspired beam search policy for chain construction."""

from __future__ import annotations

import copy
import itertools
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

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

BUILD_POTENTIAL_V1_SCHEMA_VERSION = "puyo.build_potential.v1"
BUILD_POTENTIAL_SCHEMA_VERSION = "puyo.build_potential.v2"
BUILD_SCORING_V2 = "build_potential_v2"
LEGACY_BUILD_SCORING = "legacy"
LEGACY_CANDIDATE_MODE = "legacy"
DIVERSE_CANDIDATE_MODE = "diverse"
DIVERSE_CANDIDATE_SCHEMA_VERSION = "puyo.diverse_beam_candidate.v1"

_DIVERSITY_AXES = (
    "potential",
    "survival",
    "continuation",
    "actual_chain",
    "recoverability",
)


@dataclass(frozen=True)
class BuildPotentialBudget:
    """Count-bounded, deterministic budget for one board-only evaluation."""

    max_added_puyos: int = 4
    max_pattern_nodes: int = 1_024
    max_resolution_nodes: int = 48
    max_alternatives: int = 8
    max_continuation_actions: int = 8
    max_recovery_puyos: int = 2

    def __post_init__(self) -> None:
        if min(
            self.max_added_puyos,
            self.max_pattern_nodes,
            self.max_resolution_nodes,
            self.max_alternatives,
            self.max_continuation_actions,
        ) <= 0:
            raise ValueError("build-potential budgets must be positive")
        if self.max_added_puyos > 4:
            raise ValueError("build-potential ignition search supports at most 4 puyos")
        if self.max_recovery_puyos < 0:
            raise ValueError("build-potential recovery budget must be non-negative")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_added_puyos": int(self.max_added_puyos),
            "max_pattern_nodes": int(self.max_pattern_nodes),
            "max_resolution_nodes": int(self.max_resolution_nodes),
            "max_alternatives": int(self.max_alternatives),
            "max_continuation_actions": int(self.max_continuation_actions),
            "max_recovery_puyos": int(self.max_recovery_puyos),
        }

    @property
    def cache_key(self) -> tuple[int, ...]:
        return tuple(self.to_dict().values())


@dataclass(frozen=True)
class TriggerAlternative:
    """One gravity-valid virtual ignition, independent of named chain styles."""

    chain_count: int
    score: int
    added_puyos: int
    trigger_color: PuyoColor
    placements: tuple[tuple[int, int], ...]
    anchor_cells: tuple[tuple[int, int], ...]
    danger_margin: float

    @property
    def columns(self) -> tuple[int, ...]:
        return tuple(sorted({x for x, _ in self.placements}))

    @property
    def ignition_turns_lower_bound(self) -> int:
        return (self.added_puyos + 1) // 2

    @property
    def exact_signature(self) -> tuple[Any, ...]:
        return (
            self.trigger_color.value,
            self.anchor_cells,
            self.placements,
        )

    @property
    def equivalence_key(self) -> tuple[int, int]:
        return (int(self.chain_count), int(self.added_puyos))

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicted_chain_count": int(self.chain_count),
            "predicted_score": int(self.score),
            "ignition_cost": {
                "puyos": int(self.added_puyos),
                "turns_lower_bound": int(self.ignition_turns_lower_bound),
                "columns": len(self.columns),
            },
            "trigger_color": self.trigger_color.name,
            "placements": [
                {"x": int(x), "y": int(y)} for x, y in self.placements
            ],
            "anchor_cells": [
                {"x": int(x), "y": int(y)} for x, y in self.anchor_cells
            ],
            "danger_margin": float(self.danger_margin),
        }


@dataclass(frozen=True)
class BuildPotential:
    """Versioned latent chain potential for a quiet field.

    The first five fields retain the v1 constructor ABI.  The v2 status makes an
    evaluated zero distinguishable from a state that was never probed.
    """

    chain_count: int = 0
    required_puyos: int = 0
    trigger_x: int | None = None
    trigger_y: int | None = None
    trigger_color: PuyoColor | None = None
    alternatives: tuple[TriggerAlternative, ...] = ()
    predicted_chain_potential: float | None = None
    continuation_flexibility: float | None = None
    danger_margin: float | None = None
    evaluation_status: str = "not_evaluated"
    search_complete: bool = False
    pattern_nodes: int = 0
    resolution_nodes: int = 0
    truncation_reason: str | None = None
    budget: BuildPotentialBudget | None = None
    schema_version: str = BUILD_POTENTIAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in {
            BUILD_POTENTIAL_V1_SCHEMA_VERSION,
            BUILD_POTENTIAL_SCHEMA_VERSION,
        }:
            raise ValueError(f"unsupported build-potential schema: {self.schema_version}")
        if self.evaluation_status not in {
            "available",
            "not_found",
            "not_evaluated",
            "budget_exhausted",
            "legacy_partial",
            "unknown",
        }:
            raise ValueError(f"unsupported build-potential status: {self.evaluation_status}")
        if min(self.chain_count, self.required_puyos, self.pattern_nodes, self.resolution_nodes) < 0:
            raise ValueError("build-potential counts must be non-negative")
        for value, name in (
            (self.predicted_chain_potential, "predicted_chain_potential"),
            (self.continuation_flexibility, "continuation_flexibility"),
            (self.danger_margin, "danger_margin"),
        ):
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1] when available")

    @property
    def exists(self) -> bool:
        return self.chain_count > 0

    @property
    def evaluated(self) -> bool:
        return self.evaluation_status in {
            "available",
            "not_found",
            "budget_exhausted",
        }

    @property
    def equivalence_class_count(self) -> int:
        return len({alternative.equivalence_key for alternative in self.alternatives})

    def to_dict(self) -> dict[str, Any]:
        legacy = {
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
        if self.schema_version == BUILD_POTENTIAL_V1_SCHEMA_VERSION:
            return legacy
        ignition_cost = None
        if self.exists and self.budget is not None:
            primary_columns = (
                len(self.alternatives[0].columns) if self.alternatives else 1
            )
            ignition_cost = {
                "puyos": int(self.required_puyos),
                "turns_lower_bound": int((self.required_puyos + 1) // 2),
                "columns": int(primary_columns),
                "normalized": min(
                    1.0,
                    self.required_puyos / float(self.budget.max_added_puyos),
                ),
            }
        recoverability_status = (
            "available"
            if self.exists
            else "not_applicable"
            if self.evaluation_status == "not_found"
            else "unknown"
        )
        return {
            "schema_version": self.schema_version,
            "evaluation_status": self.evaluation_status,
            "exists": bool(self.exists),
            **legacy,
            "predicted_chain_count": (
                int(self.chain_count)
                if self.exists
                or self.evaluation_status in {"not_found", "legacy_partial"}
                else None
            ),
            "predicted_chain_potential": (
                None
                if self.predicted_chain_potential is None
                else float(self.predicted_chain_potential)
            ),
            "ignition_cost": ignition_cost,
            "trigger_alternatives": [
                alternative.to_dict() for alternative in self.alternatives
            ],
            "trigger_equivalence": {
                "class_count": int(self.equivalence_class_count),
                "alternative_count": len(self.alternatives),
            },
            "trigger_recoverability": {
                "status": recoverability_status,
                "equivalent_alternatives": max(
                    0,
                    len(self.alternatives) - 1,
                ),
            },
            "continuation_flexibility": (
                None
                if self.continuation_flexibility is None
                else float(self.continuation_flexibility)
            ),
            "danger_margin": (
                None if self.danger_margin is None else float(self.danger_margin)
            ),
            "search": {
                "budget": None if self.budget is None else self.budget.to_dict(),
                "pattern_nodes": int(self.pattern_nodes),
                "resolution_nodes": int(self.resolution_nodes),
                "complete": bool(self.search_complete),
                "truncation_reason": self.truncation_reason,
            },
        }


@dataclass(frozen=True)
class TriggerRecoverability:
    status: str = "unknown"
    exact: bool = False
    equivalent: bool = False
    recoverable: bool | None = None
    recovery_cost_puyos: int | None = None
    root_chain_count: int | None = None
    selected_chain_count: int | None = None

    @property
    def policy_preserved(self) -> bool:
        return self.status == "not_applicable" or bool(self.recoverable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exact": bool(self.exact),
            "equivalent": bool(self.equivalent),
            "recoverable": self.recoverable,
            "recovery_cost_puyos": self.recovery_cost_puyos,
            "root_chain_count": self.root_chain_count,
            "selected_chain_count": self.selected_chain_count,
            "policy_preserved": bool(self.policy_preserved),
        }


def compare_build_potential_triggers(
    root: BuildPotential,
    selected: BuildPotential,
    *,
    max_recovery_puyos: int | None = None,
) -> TriggerRecoverability:
    """Compare ignition capability, allowing equivalent and bounded recovery."""

    if not root.exists and root.evaluation_status == "not_found":
        return TriggerRecoverability(
            status="not_applicable",
            recoverable=True,
            recovery_cost_puyos=0,
            root_chain_count=0,
            selected_chain_count=selected.chain_count,
        )
    if not root.evaluated or not selected.evaluated:
        return TriggerRecoverability(
            status="unknown",
            root_chain_count=(root.chain_count if root.evaluated else None),
            selected_chain_count=(selected.chain_count if selected.evaluated else None),
        )
    if not root.exists:
        return TriggerRecoverability(
            status="unknown",
            root_chain_count=0,
            selected_chain_count=selected.chain_count,
        )
    recovery_budget = (
        max_recovery_puyos
        if max_recovery_puyos is not None
        else (
            selected.budget.max_recovery_puyos
            if selected.budget is not None
            else 0
        )
    )
    root_alternatives = root.alternatives
    selected_alternatives = selected.alternatives
    exact = any(
        candidate.chain_count >= baseline.chain_count
        and candidate.exact_signature == baseline.exact_signature
        for baseline in root_alternatives
        for candidate in selected_alternatives
    )
    equivalent_candidates = [
        candidate
        for candidate in selected_alternatives
        if candidate.chain_count >= root.chain_count
        and candidate.added_puyos <= root.required_puyos
    ]
    equivalent = bool(equivalent_candidates)
    recoverable_candidates = [
        candidate
        for candidate in selected_alternatives
        if candidate.chain_count >= root.chain_count
        and candidate.added_puyos <= root.required_puyos + recovery_budget
    ]
    recoverable = bool(recoverable_candidates)
    recovery_cost = (
        min(
            max(0, candidate.added_puyos - root.required_puyos)
            for candidate in recoverable_candidates
        )
        if recoverable_candidates
        else None
    )
    if exact:
        status = "exact"
    elif equivalent:
        status = "equivalent"
    elif recoverable:
        status = "recoverable"
    elif selected.search_complete:
        status = "lost"
    else:
        status = "unknown"
        recoverable = None
    return TriggerRecoverability(
        status=status,
        exact=exact,
        equivalent=equivalent,
        recoverable=recoverable,
        recovery_cost_puyos=recovery_cost,
        root_chain_count=root.chain_count,
        selected_chain_count=selected.chain_count,
    )


def migrate_build_potential_v1(
    value: Mapping[str, Any],
    *,
    simulator: HeadlessPuyoSimulator | None = None,
    budget: BuildPotentialBudget | None = None,
) -> BuildPotential:
    """Project legacy diagnostics, or recompute v2 when the board is available."""

    if simulator is not None:
        return evaluate_build_potential(simulator, budget=budget)
    chain_count = max(0, int(value.get("chain_count", 0)))
    required = max(0, int(value.get("required_puyos", 0)))
    trigger = value.get("trigger")
    trigger_x = trigger.get("x") if isinstance(trigger, Mapping) else None
    trigger_y = trigger.get("y") if isinstance(trigger, Mapping) else None
    raw_color = value.get("trigger_color")
    trigger_color = (
        PuyoColor[str(raw_color)]
        if isinstance(raw_color, str) and raw_color in PuyoColor.__members__
        else None
    )
    return BuildPotential(
        chain_count=chain_count,
        required_puyos=required,
        trigger_x=None if trigger_x is None else int(trigger_x),
        trigger_y=None if trigger_y is None else int(trigger_y),
        trigger_color=trigger_color,
        evaluation_status="legacy_partial" if chain_count > 0 else "unknown",
        schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
    )


class BuildPotentialSession:
    """Decision-scoped cache whose count budget is independent of cache mode."""

    def __init__(
        self,
        *,
        schema_version: str = BUILD_POTENTIAL_SCHEMA_VERSION,
        budget: BuildPotentialBudget | None = None,
        max_evaluations: int = 64,
        use_cache: bool = True,
    ) -> None:
        if schema_version not in {
            BUILD_POTENTIAL_V1_SCHEMA_VERSION,
            BUILD_POTENTIAL_SCHEMA_VERSION,
        }:
            raise ValueError(f"unsupported build-potential schema: {schema_version}")
        if max_evaluations <= 0:
            raise ValueError("build-potential decision budget must be positive")
        self.schema_version = schema_version
        self.budget = budget or BuildPotentialBudget()
        self.max_evaluations = int(max_evaluations)
        self.use_cache = bool(use_cache)
        self._cache: dict[tuple[Any, ...], BuildPotential] = {}
        self._seen: set[tuple[Any, ...]] = set()
        self.cache_hits = 0
        self.budget_exhaustions = 0

    @property
    def evaluation_count(self) -> int:
        return len(self._seen)

    def evaluate(self, simulator: HeadlessPuyoSimulator) -> BuildPotential:
        key = (
            self.schema_version,
            self.budget.cache_key,
            _build_potential_fingerprint(simulator.game),
        )
        if self.use_cache and key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        is_new = key not in self._seen
        if is_new and len(self._seen) >= self.max_evaluations:
            self.budget_exhaustions += 1
            return BuildPotential(
                evaluation_status="not_evaluated",
                truncation_reason="decision_probe_budget",
                budget=self.budget,
                schema_version=self.schema_version,
            )
        self._seen.add(key)
        if self.schema_version == BUILD_POTENTIAL_V1_SCHEMA_VERSION:
            result = evaluate_build_potential_v1(simulator)
        else:
            result = evaluate_build_potential(simulator, budget=self.budget)
        if self.use_cache:
            self._cache[key] = result
        return result


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
    scoring_mode: str = LEGACY_BUILD_SCORING
    future_potential_weight: float = 1.0
    chain_shape_weight: float = 1.0
    danger_weight: float = 1.0
    danger_tolerance: float = 1.0
    build_potential_schema_version: str = BUILD_POTENTIAL_V1_SCHEMA_VERSION
    potential_probe_budget: int = 64
    build_potential_budget: BuildPotentialBudget = BuildPotentialBudget()
    chain_style_evaluator: Any = None
    candidate_mode: str = LEGACY_CANDIDATE_MODE
    candidate_limit: int = 1
    diversity_slots_per_axis: int = 1
    max_expanded_nodes: int | None = None
    use_potential_cache: bool = True

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
        if self.scoring_mode not in {LEGACY_BUILD_SCORING, BUILD_SCORING_V2}:
            raise ValueError(f"unsupported beam scoring mode: {self.scoring_mode}")
        if min(
            self.future_potential_weight,
            self.chain_shape_weight,
            self.danger_weight,
        ) < 0.0:
            raise ValueError("beam scoring weights must be non-negative")
        if not 0.0 <= self.danger_tolerance <= 1.0:
            raise ValueError("danger tolerance must be in [0, 1]")
        if self.build_potential_schema_version not in {
            BUILD_POTENTIAL_V1_SCHEMA_VERSION,
            BUILD_POTENTIAL_SCHEMA_VERSION,
        }:
            raise ValueError(
                "unsupported build-potential schema: "
                f"{self.build_potential_schema_version}"
            )
        if self.potential_probe_budget <= 0:
            raise ValueError("potential probe budget must be positive")
        if self.candidate_mode not in {
            LEGACY_CANDIDATE_MODE,
            DIVERSE_CANDIDATE_MODE,
        }:
            raise ValueError(
                f"unsupported beam candidate mode: {self.candidate_mode}"
            )
        if self.candidate_limit <= 0:
            raise ValueError("beam candidate limit must be positive")
        if self.diversity_slots_per_axis <= 0:
            raise ValueError("beam diversity slots must be positive")
        if self.max_expanded_nodes is not None and self.max_expanded_nodes <= 0:
            raise ValueError("beam expanded-node budget must be positive")
        if (
            self.scoring_mode == BUILD_SCORING_V2
            and self.build_potential_schema_version != BUILD_POTENTIAL_SCHEMA_VERSION
        ):
            raise ValueError("v2 scoring requires the BuildPotential v2 schema")


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
    trigger_recoverability: TriggerRecoverability
    potential_probe_count: int
    potential_cache_hits: int
    candidates: tuple[BeamCandidateDiagnostics, ...]
    scoring_mode: str = LEGACY_BUILD_SCORING
    build_potential_schema_version: str = BUILD_POTENTIAL_V1_SCHEMA_VERSION
    proposals: tuple[DiverseBeamCandidate, ...] = ()
    candidate_mode: str = LEGACY_CANDIDATE_MODE
    candidate_limit: int = 1
    generated_nodes: int = 0
    invalid_nodes: int = 0
    game_over_nodes: int = 0
    transposition_hits: int = 0
    potential_budget_exhaustions: int = 0
    budget_exhausted: bool = False
    fallback_reason: str | None = None
    reached_depth: int = 0
    scenario_budget: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiverseBeamCandidate:
    """One deterministic root proposal retained for a downstream ranker."""

    rank: int
    action: int
    plan: tuple[int, ...]
    candidate_value: float
    predicted_max_chain: int
    best_chain_depth: int
    build_potential: BuildPotential
    danger: float
    continuation_flexibility: float
    trigger_recoverability: TriggerRecoverability
    value_breakdown: Mapping[str, float] = field(default_factory=dict)
    generation_reasons: tuple[str, ...] = ()
    retention_reasons: tuple[str, ...] = ()
    pruning_reasons: tuple[str, ...] = ()
    scenario_support: int = 0
    scenario_ids: tuple[int, ...] = ()
    schema_version: str = DIVERSE_CANDIDATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rank": int(self.rank),
            "root_action": int(self.action),
            "plan": [int(action) for action in self.plan],
            "candidate_value": float(self.candidate_value),
            "predicted_max_chain": int(self.predicted_max_chain),
            "best_chain_depth": int(self.best_chain_depth),
            "build_potential": self.build_potential.to_dict(),
            "danger": float(self.danger),
            "continuation_flexibility": float(
                self.continuation_flexibility
            ),
            "trigger_recoverability": self.trigger_recoverability.to_dict(),
            "value_breakdown": {
                key: float(value) for key, value in self.value_breakdown.items()
            },
            "reasons": {
                "generated": list(self.generation_reasons),
                "retained": list(self.retention_reasons),
                "pruned": list(self.pruning_reasons),
            },
            "hidden_future_scenarios": {
                "support": int(self.scenario_support),
                "scenario_ids": [int(value) for value in self.scenario_ids],
            },
        }


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
    value_breakdown: Mapping[str, float] = field(default_factory=dict)
    chain_style_evaluation: Mapping[str, Any] = field(default_factory=dict)
    danger: float = 1.0
    continuation_flexibility: float = 0.0
    trigger_recoverability: TriggerRecoverability = TriggerRecoverability()
    generation_reasons: tuple[str, ...] = ()
    retention_reasons: tuple[str, ...] = ()
    pruning_reasons: tuple[str, ...] = ()
    duplicate_count: int = 0
    scenario_ids: tuple[int, ...] = ()

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
            "value_breakdown": {
                key: float(value) for key, value in self.value_breakdown.items()
            },
            "potential": self.potential.to_dict(),
            "chain_style_evaluation": dict(self.chain_style_evaluation),
            "danger": float(self.danger),
            "continuation_flexibility": float(
                self.continuation_flexibility
            ),
            "trigger_recoverability": self.trigger_recoverability.to_dict(),
            "reasons": {
                "generated": list(self.generation_reasons),
                "retained": list(self.retention_reasons),
                "pruned": list(self.pruning_reasons),
            },
            "duplicate_count": int(self.duplicate_count),
            "scenario_ids": [int(value) for value in self.scenario_ids],
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
    chain_style_evaluation: Mapping[str, Any] = field(default_factory=dict)
    danger: float = 1.0
    continuation_flexibility: float = 0.0
    trigger_recoverability: TriggerRecoverability = TriggerRecoverability()
    generation_reasons: set[str] = field(default_factory=set)
    retention_reasons: set[str] = field(default_factory=set)
    pruning_reasons: set[str] = field(default_factory=set)
    duplicate_count: int = 0
    scenario_ids: set[int] = field(default_factory=set)


@dataclass
class _PathTraceState:
    generated: bool = False
    base_prune: bool = False
    potential_probe: bool = False
    final_prune: bool = False
    safety_suppressed: bool = False


class _SearchTrace:
    """Collect candidate metadata without participating in search ranking."""

    def __init__(
        self,
        *,
        trace_paths: bool = False,
        potential_schema_version: str = BUILD_POTENTIAL_V1_SCHEMA_VERSION,
    ) -> None:
        self._states: dict[int, _CandidateTraceState] = {}
        self._trace_paths = bool(trace_paths)
        self._paths: dict[tuple[int, ...], _PathTraceState] = {}
        self._potential_schema_version = potential_schema_version

    def _state(self, action: int) -> _CandidateTraceState:
        return self._states.setdefault(
            int(action),
            _CandidateTraceState(
                potential=BuildPotential(
                    schema_version=self._potential_schema_version,
                )
            ),
        )

    def mark_root_generated(self, action: int, *, scenario_id: int) -> None:
        state = self._state(action)
        state.root_generated = True
        state.generation_reasons.add("legal_root")
        state.scenario_ids.add(int(scenario_id))

    def mark_root_rejected(self, action: int, reason: str) -> None:
        state = self._state(action)
        state.root_rejected = True
        state.pruning_reasons.add(str(reason))

    def mark_root_not_evaluated(self, action: int, reason: str) -> None:
        self._state(action).pruning_reasons.add(str(reason))

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
            state.retention_reasons.update(node.retention_reasons)
            state.scenario_ids.add(int(node.scenario_id))
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

    def mark_pruned(
        self,
        nodes: Sequence[_Node],
        *,
        reason: str,
        depth: int,
    ) -> None:
        for node in nodes:
            state = self._state(node.root_action)
            state.pruning_reasons.add(f"depth_{int(depth)}:{reason}")
            state.scenario_ids.add(int(node.scenario_id))

    def mark_duplicate(self, node: _Node, *, depth: int) -> None:
        state = self._state(node.root_action)
        state.duplicate_count += 1
        state.pruning_reasons.add(f"depth_{int(depth)}:transposition_duplicate")
        state.scenario_ids.add(int(node.scenario_id))

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
        state.chain_style_evaluation = dict(node.chain_style_evaluation)
        state.danger = float(node.danger)
        state.continuation_flexibility = float(node.continuation_flexibility)
        state.trigger_recoverability = node.trigger_recoverability
        state.generation_reasons.update(node.generation_reasons)
        state.retention_reasons.update(node.retention_reasons)
        state.pruning_reasons.update(node.pruning_reasons)
        state.scenario_ids.add(int(node.scenario_id))

    def diagnostics(
        self,
        actions: list[int],
        values: dict[int, float],
        breakdowns: Mapping[int, Mapping[str, float]],
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
                    value_breakdown=dict(breakdowns.get(action, {})),
                    chain_style_evaluation=dict(state.chain_style_evaluation),
                    danger=state.danger,
                    continuation_flexibility=state.continuation_flexibility,
                    trigger_recoverability=state.trigger_recoverability,
                    generation_reasons=tuple(sorted(state.generation_reasons)),
                    retention_reasons=tuple(sorted(state.retention_reasons)),
                    pruning_reasons=tuple(sorted(state.pruning_reasons)),
                    duplicate_count=state.duplicate_count,
                    scenario_ids=tuple(sorted(state.scenario_ids)),
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
    actual_chain_contribution: float = 0.0
    actual_score_contribution: float = 0.0
    chain_shape_contribution: float = 0.0
    danger_contribution: float = 0.0
    future_potential_contribution: float = 0.0
    trigger_preservation_contribution: float = 0.0
    chain_style_contribution: float = 0.0
    chain_style_applicable: bool = False
    chain_style_constraint_satisfied: bool = True
    chain_style_evaluation: Mapping[str, Any] = field(default_factory=dict)
    danger: float = 1.0
    continuation_flexibility: float = 0.0
    trigger_recoverability: TriggerRecoverability = TriggerRecoverability()
    scenario_id: int = 0
    generation_reasons: set[str] = field(default_factory=set)
    retention_reasons: set[str] = field(default_factory=set)
    pruning_reasons: set[str] = field(default_factory=set)


@dataclass
class _SearchCounters:
    generated_nodes: int = 0
    invalid_nodes: int = 0
    game_over_nodes: int = 0
    transposition_hits: int = 0
    reached_depth: int = 0


@dataclass
class _ExpansionBudget:
    limit: int | None
    consumed: int = 0
    exhausted: bool = False

    def consume(self) -> bool:
        if self.limit is not None and self.consumed >= self.limit:
            self.exhausted = True
            return False
        self.consumed += 1
        return True


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
        self.last_candidates: tuple[DiverseBeamCandidate, ...] = ()
        self._potential_session = self._new_potential_session()
        self._root_potential = self._not_probed_potential()
        self._search_trace = _SearchTrace(
            trace_paths=self.config.trace_paths,
            potential_schema_version=self.config.build_potential_schema_version,
        )

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        candidates = self.generate_candidates(observation, info)
        if candidates:
            return int(candidates[0].action)
        choices = _legal_indices_from_info(info)
        return choices[0] if choices else 0

    def generate_candidates(
        self,
        observation: dict[str, Any],
        info: dict[str, Any],
    ) -> tuple[DiverseBeamCandidate, ...]:
        """Generate deterministic, ranker-ready root candidates.

        ``select_action`` remains the single-best compatibility adapter and
        returns the first item from this set.
        """

        _ = observation
        simulator = info.get("simulator")
        if simulator is None:
            choices = _legal_indices_from_info(info)
            self.last_diagnostics = None
            self.last_candidates = (
                ()
                if not choices
                else (
                    DiverseBeamCandidate(
                        rank=0,
                        action=int(choices[0]),
                        plan=(int(choices[0]),),
                        candidate_value=0.0,
                        predicted_max_chain=0,
                        best_chain_depth=0,
                        build_potential=self._not_probed_potential(),
                        danger=1.0,
                        continuation_flexibility=0.0,
                        trigger_recoverability=TriggerRecoverability(),
                        generation_reasons=("action_mask_fallback",),
                        retention_reasons=("deterministic_fallback",),
                    ),
                )
            )
            return self.last_candidates

        started = time.perf_counter()
        counters = _SearchCounters()
        expansion_budget = _ExpansionBudget(self.config.max_expanded_nodes)
        self._potential_session = self._new_potential_session()
        self._search_trace = _SearchTrace(
            trace_paths=self.config.trace_paths,
            potential_schema_version=self.config.build_potential_schema_version,
        )
        self._root_potential = (
            self._probe_potential(simulator)
            if self._preserves_trigger
            else self._not_probed_potential()
        )
        totals: dict[int, float] = {}
        breakdown_totals: dict[int, dict[str, float]] = {}
        potentials: dict[int, list[BuildPotential]] = {}
        preserved: dict[int, list[bool]] = {}
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

        evaluated_scenario_ids: list[int] = []
        for scenario_id, colors in zip(scenario_ids, scenario_colors):
            if expansion_budget.exhausted:
                break
            scenario_simulator = clone_simulator(simulator)
            # The current pair and two visible next pairs stay intact. Only hidden
            # future pairs are replaced by representative scenarios.
            scenario_simulator.game.puyo_sequence = _ScenarioSequence(scenario_id, colors)
            (
                values,
                scenario_potentials,
                scenario_preserved,
                scenario_breakdowns,
            ) = self._search_scenario(
                scenario_simulator,
                scenario_id=int(scenario_id),
                expansion_budget=expansion_budget,
                counters=counters,
            )
            evaluated_scenario_ids.append(int(scenario_id))
            for action, value in values.items():
                totals[action] = totals.get(action, 0.0) + value
                potentials.setdefault(action, []).append(scenario_potentials[action])
                preserved.setdefault(action, []).append(scenario_preserved[action])
                _merge_value_breakdown(
                    breakdown_totals.setdefault(action, {}),
                    scenario_breakdowns[action],
                )

        legal = legal_action_indices(simulator)
        if not legal:
            self.last_candidates = ()
            self.last_diagnostics = None
            return ()
        ranked_actions = sorted(
            (action for action in legal if action in totals),
            key=lambda action: (-totals[action], action),
        )
        best_action = (
            ranked_actions[0]
            if ranked_actions
            else self._safe_fallback_action(simulator, legal)
        )
        selected_candidates = potentials.get(best_action, ())
        selected_potential = max(
            selected_candidates,
            key=_potential_rank_key,
            default=self._not_probed_potential(),
        )
        selected_recoverability = compare_build_potential_triggers(
            self._root_potential,
            selected_potential,
            max_recovery_puyos=self.config.build_potential_budget.max_recovery_puyos,
        )
        candidate_diagnostics = self._search_trace.diagnostics(
            legal,
            totals,
            breakdown_totals,
        )
        proposals = self._build_proposals(
            candidate_diagnostics,
            best_action=best_action,
        )
        if not proposals:
            proposals = (
                DiverseBeamCandidate(
                    rank=0,
                    action=int(best_action),
                    plan=(int(best_action),),
                    candidate_value=float(totals.get(best_action, 0.0)),
                    predicted_max_chain=0,
                    best_chain_depth=0,
                    build_potential=selected_potential,
                    danger=1.0,
                    continuation_flexibility=0.0,
                    trigger_recoverability=selected_recoverability,
                    generation_reasons=("safe_legal_fallback",),
                    retention_reasons=("deterministic_fallback",),
                    scenario_support=len(evaluated_scenario_ids),
                    scenario_ids=tuple(evaluated_scenario_ids),
                ),
            )
        self.last_candidates = proposals
        self.last_diagnostics = BeamSearchDiagnostics(
            elapsed_seconds=time.perf_counter() - started,
            expanded_nodes=expansion_budget.consumed,
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
            trigger_recoverability=selected_recoverability,
            potential_probe_count=self._potential_session.evaluation_count,
            potential_cache_hits=self._potential_session.cache_hits,
            candidates=candidate_diagnostics,
            scoring_mode=self.config.scoring_mode,
            build_potential_schema_version=(
                self.config.build_potential_schema_version
            ),
            proposals=proposals,
            candidate_mode=self.config.candidate_mode,
            candidate_limit=self.config.candidate_limit,
            generated_nodes=counters.generated_nodes,
            invalid_nodes=counters.invalid_nodes,
            game_over_nodes=counters.game_over_nodes,
            transposition_hits=counters.transposition_hits,
            potential_budget_exhaustions=(
                self._potential_session.budget_exhaustions
            ),
            budget_exhausted=expansion_budget.exhausted,
            fallback_reason=(
                "expanded_node_budget" if expansion_budget.exhausted else None
            ),
            reached_depth=counters.reached_depth,
            scenario_budget={
                "known_pair_count": int(1 + len(simulator.game.next_puyo_queue)),
                "hidden_future_requested": int(self.config.scenarios),
                "hidden_future_evaluated": len(evaluated_scenario_ids),
                "scenario_ids": evaluated_scenario_ids,
                "scenario_seed": self.config.scenario_seed,
                "uncertainty": (
                    "bounded_scenario_set"
                    if self.config.scenarios > 1
                    else "single_hidden_scenario"
                ),
                "max_expanded_nodes": self.config.max_expanded_nodes,
            },
        )
        return proposals

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

    @property
    def _probes_potential(self) -> bool:
        return self.config.probe_width > 0 and (
            (
                self.config.scoring_mode == BUILD_SCORING_V2
                and self.config.future_potential_weight > 0.0
            )
            or self.config.trigger_preservation != "ignore"
        )

    def _search_scenario(
        self,
        simulator,
        *,
        scenario_id: int,
        expansion_budget: _ExpansionBudget,
        counters: _SearchCounters,
    ) -> tuple[
        dict[int, float],
        dict[int, BuildPotential],
        dict[int, bool],
        dict[int, dict[str, float]],
    ]:
        beam: list[_Node] = []
        best_by_action: dict[int, float] = {}
        potential_by_action: dict[int, BuildPotential] = {}
        preserved_by_action: dict[int, bool] = {}
        breakdown_by_action: dict[int, dict[str, float]] = {}
        root_candidates: list[tuple[_Node, int]] = []
        root_actions = legal_action_indices(simulator)
        for index, action in enumerate(root_actions):
            if not expansion_budget.consume():
                for skipped in root_actions[index:]:
                    self._search_trace.mark_root_not_evaluated(
                        skipped,
                        "expanded_node_budget",
                    )
                break
            child = clone_simulator(simulator)
            result = child.step(action_to_placement(action))
            if not result.valid:
                counters.invalid_nodes += 1
                self._search_trace.mark_root_rejected(action, "invalid_action")
                continue
            if result.game_over:
                counters.game_over_nodes += 1
                self._search_trace.mark_root_rejected(action, "game_over")
                continue
            counters.generated_nodes += 1
            counters.reached_depth = max(counters.reached_depth, 1)
            self._search_trace.mark_root_generated(
                action,
                scenario_id=scenario_id,
            )
            (
                actual_chain,
                actual_score,
                premature_penalty,
            ) = self._chain_outcome_components(
                result.chain_count,
                result.score_delta,
            )
            chain_shape, danger = self._board_contributions(child.game)
            style = self._chain_style_evaluation(child)
            chain_value = actual_chain + actual_score
            evaluation = chain_shape + danger + style[0]
            path = (int(action),)
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
                self._not_probed_potential(),
                result.chain_count >= self.config.minimum_chain_count,
                actual_chain,
                actual_score,
                chain_shape,
                danger,
                chain_style_contribution=style[0],
                chain_style_applicable=style[1],
                chain_style_constraint_satisfied=style[2],
                chain_style_evaluation=style[3],
                danger=_board_danger_ratio(child.game),
                continuation_flexibility=_continuation_flexibility(
                    child,
                    self.config.build_potential_budget,
                ),
                scenario_id=int(scenario_id),
                generation_reasons={
                    "legal_transition",
                    f"hidden_scenario:{int(scenario_id)}",
                },
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
            breakdown_by_action,
        )
        for depth in range(2, self.config.depth + 1):
            if expansion_budget.exhausted:
                break
            seen: dict[tuple, _Node] = {}
            for node in beam:
                node_candidates: list[tuple[_Node, int]] = []
                for action in legal_action_indices(node.simulator):
                    if not expansion_budget.consume():
                        break
                    child = clone_simulator(node.simulator)
                    result = child.step(action_to_placement(action))
                    if not result.valid:
                        counters.invalid_nodes += 1
                        continue
                    if result.game_over:
                        counters.game_over_nodes += 1
                        continue
                    counters.generated_nodes += 1
                    counters.reached_depth = max(counters.reached_depth, depth)

                    (
                        chain_value,
                        premature_penalty,
                        actual_chain,
                        actual_score,
                    ) = self._advance_chain_outcome_components(
                        node,
                        result.chain_count,
                        result.score_delta,
                    )
                    chain_shape, danger = self._board_contributions(child.game)
                    style = self._chain_style_evaluation(child)
                    evaluation = chain_shape + danger + style[0]
                    best_chain_depth = (
                        depth
                        if int(result.chain_count) > node.best_chain_count
                        else node.best_chain_depth
                    )
                    path = node.path + (int(action),)
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
                        self._not_probed_potential(),
                        node.target_achieved
                        or result.chain_count >= self.config.minimum_chain_count,
                        actual_chain,
                        actual_score,
                        chain_shape,
                        danger,
                        chain_style_contribution=style[0],
                        chain_style_applicable=style[1],
                        chain_style_constraint_satisfied=style[2],
                        chain_style_evaluation=style[3],
                        danger=_board_danger_ratio(child.game),
                        continuation_flexibility=_continuation_flexibility(
                            child,
                            self.config.build_potential_budget,
                        ),
                        scenario_id=int(scenario_id),
                        generation_reasons=set(node.generation_reasons)
                        | {"legal_transition"},
                    )
                    self._search_trace.mark_generated_path(path)
                    node_candidates.append((candidate, result.chain_count))

                for candidate in self._suppress_premature(
                    node_candidates,
                    depth=depth,
                ):
                    fingerprint = _field_fingerprint(candidate.simulator.game)
                    previous = seen.get(fingerprint)
                    if previous is None:
                        seen[fingerprint] = candidate
                        continue
                    counters.transposition_hits += 1
                    candidate_is_better = _base_node_value(
                        candidate
                    ) > _base_node_value(previous)
                    if (
                        self.config.candidate_mode == DIVERSE_CANDIDATE_MODE
                        and _base_node_value(candidate)
                        == _base_node_value(previous)
                    ):
                        candidate_is_better = _node_rank_key(
                            candidate,
                            base=True,
                        ) > _node_rank_key(previous, base=True)
                    if candidate_is_better:
                        self._search_trace.mark_duplicate(previous, depth=depth)
                        seen[fingerprint] = candidate
                    else:
                        self._search_trace.mark_duplicate(candidate, depth=depth)

                if expansion_budget.exhausted:
                    break

            if not seen:
                break
            beam = self._prune(list(seen.values()), depth=depth)
            self._record_best(
                beam,
                best_by_action,
                potential_by_action,
                preserved_by_action,
                breakdown_by_action,
            )

        return (
            best_by_action,
            potential_by_action,
            preserved_by_action,
            breakdown_by_action,
        )

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
                    node.pruning_reasons.add("premature_fire_suppressed")
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
        breakdowns: dict[int, dict[str, float]],
    ) -> None:
        for node in nodes:
            value = _node_value(node)
            if value <= values.get(node.root_action, float("-inf")):
                continue
            values[node.root_action] = value
            potentials[node.root_action] = node.potential
            preserved[node.root_action] = (
                self._preserves_trigger
                and (
                    node.target_achieved
                    or self._trigger_preserved(node.potential)
                )
            )
            breakdowns[node.root_action] = _node_value_breakdown(node)
            self._search_trace.record_best(node, value)

    def _prune(self, nodes: list[_Node], *, depth: int = 1) -> list[_Node]:
        style_rejected = [
            node
            for node in nodes
            if node.chain_style_applicable
            and not node.chain_style_constraint_satisfied
        ]
        for node in style_rejected:
            node.pruning_reasons.add("chain_style_constraint")
        self._search_trace.mark_pruned(
            style_rejected,
            reason="chain_style_constraint",
            depth=depth,
        )
        style_rejected_ids = {id(node) for node in style_rejected}
        nodes = [node for node in nodes if id(node) not in style_rejected_ids]
        nodes.sort(
            key=lambda node: (_base_node_value(node), -node.root_action),
            reverse=True,
        )
        if self.config.candidate_mode == DIVERSE_CANDIDATE_MODE:
            base_retained = self._select_diverse_nodes(
                nodes,
                limit=self.config.width,
                phase="base",
            )
        else:
            base_retained = nodes[: self.config.width]
            for node in base_retained:
                node.retention_reasons.add("base:scalar_rank")
        self._search_trace.mark_stage("base", base_retained, depth)
        if self._probes_potential:
            if self.config.candidate_mode == DIVERSE_CANDIDATE_MODE:
                probed = self._select_diverse_nodes(
                    nodes,
                    limit=self.config.probe_width,
                    phase="probe",
                )
            else:
                probed = nodes[: self.config.probe_width]
                for node in probed:
                    node.retention_reasons.add("probe:scalar_rank")
            self._search_trace.mark_stage("probe", probed, depth)
            probed_ids = {id(node) for node in probed}
            probe_pruned = [node for node in nodes if id(node) not in probed_ids]
            self._search_trace.mark_pruned(
                probe_pruned,
                reason="probe_budget",
                depth=depth,
            )
            for node in probed:
                self._apply_potential_probe(node)
        for node in nodes:
            node.trigger_recoverability = compare_build_potential_triggers(
                self._root_potential,
                node.potential,
                max_recovery_puyos=(
                    self.config.build_potential_budget.max_recovery_puyos
                ),
            )
        nodes.sort(
            key=lambda node: (
                _node_value(node),
                (
                    _potential_rank_key(node.potential)
                    if self.config.scoring_mode == LEGACY_BUILD_SCORING
                    else (0, 0, 0, 0, 0)
                ),
                -node.root_action,
            ),
            reverse=True,
        )
        if self.config.candidate_mode == DIVERSE_CANDIDATE_MODE:
            retained = self._select_diverse_nodes(
                nodes,
                limit=self.config.width,
                phase="final",
            )
        else:
            retained = nodes[: self.config.width]
            for node in retained:
                node.retention_reasons.add("final:scalar_rank")
        retained_ids = {id(node) for node in retained}
        final_pruned = [node for node in nodes if id(node) not in retained_ids]
        for node in final_pruned:
            node.pruning_reasons.add("beam_width")
        self._search_trace.mark_pruned(
            final_pruned,
            reason="beam_width",
            depth=depth,
        )
        self._search_trace.mark_stage("final", retained, depth)
        return retained

    def _apply_potential_probe(self, node: _Node) -> None:
        node.potential = self._probe_potential(node.simulator)
        if node.potential.continuation_flexibility is not None:
            node.continuation_flexibility = float(
                node.potential.continuation_flexibility
            )
        if node.target_achieved:
            return
        potential_value = _potential_value(node.potential)
        future_weight = (
            self.config.future_potential_weight
            if self.config.scoring_mode == BUILD_SCORING_V2
            else 1.0
        )
        if potential_value is not None:
            node.future_potential_contribution = (
                self.config.chain_weight * future_weight * potential_value
            )
        root_value = (
            _potential_value(self._root_potential)
            if self._preserves_trigger
            else None
        )
        if (
            potential_value is not None
            and root_value is not None
            and potential_value < root_value
        ):
            preserve_scale = {
                "prefer": 0.5,
                "required": 1.0,
                "ignore": 0.0,
            }[self.config.trigger_preservation]
            should_penalize = (
                not self._trigger_preserved(node.potential)
                if self.config.scoring_mode == BUILD_SCORING_V2
                else True
            )
            node.trigger_preservation_contribution = (
                -self.config.chain_weight
                * (root_value - potential_value)
                * preserve_scale
                if should_penalize
                else 0.0
            )
        node.evaluation = (
            node.chain_shape_contribution
            + node.danger_contribution
            + node.future_potential_contribution
            + node.trigger_preservation_contribution
            + node.chain_style_contribution
        )

    def _select_diverse_nodes(
        self,
        nodes: Sequence[_Node],
        *,
        limit: int,
        phase: str,
    ) -> list[_Node]:
        """Retain scalar elites plus deterministic axis and root slots."""

        ranked = sorted(nodes, key=_node_rank_key, reverse=True)
        limit = min(max(0, int(limit)), len(ranked))
        if limit == 0:
            return []
        if len(ranked) <= limit:
            for node in ranked:
                node.retention_reasons.add(f"{phase}:within_budget")
            return ranked

        axis_capacity = min(
            max(0, limit - 1),
            len(_DIVERSITY_AXES) * self.config.diversity_slots_per_axis,
        )
        root_capacity = min(
            len({node.root_action for node in ranked}),
            max(0, (limit - axis_capacity) // 4),
        )
        elite_capacity = max(1, limit - axis_capacity - root_capacity)
        selected: list[_Node] = []
        selected_ids: set[int] = set()

        def add(node: _Node, reason: str) -> bool:
            if len(selected) >= limit or id(node) in selected_ids:
                return False
            selected.append(node)
            selected_ids.add(id(node))
            node.retention_reasons.add(f"{phase}:{reason}")
            return True

        for node in ranked[:elite_capacity]:
            add(node, "scalar_elite")

        for axis in _DIVERSITY_AXES:
            axis_ranked = sorted(
                ranked,
                key=lambda node, name=axis: (
                    _node_diversity_axis(node, name),
                    _node_rank_key(node),
                ),
                reverse=True,
            )
            added = 0
            for node in axis_ranked:
                if add(node, f"axis:{axis}"):
                    added += 1
                    if added >= self.config.diversity_slots_per_axis:
                        break

        root_best: dict[int, _Node] = {}
        for node in ranked:
            root_best.setdefault(node.root_action, node)
        added_roots = 0
        for node in sorted(root_best.values(), key=_node_rank_key, reverse=True):
            if add(node, "root_coverage"):
                added_roots += 1
                if added_roots >= root_capacity:
                    break

        for node in ranked:
            add(node, "scalar_fill")
            if len(selected) >= limit:
                break
        return sorted(selected, key=_node_rank_key, reverse=True)

    def _build_proposals(
        self,
        diagnostics: Sequence[BeamCandidateDiagnostics],
        *,
        best_action: int,
    ) -> tuple[DiverseBeamCandidate, ...]:
        eligible = [
            candidate
            for candidate in diagnostics
            if candidate.root_generated and candidate.candidate_value is not None
        ]
        ranked = sorted(
            eligible,
            key=lambda candidate: (
                float(candidate.candidate_value),
                -int(candidate.action),
            ),
            reverse=True,
        )
        if self.config.candidate_mode == DIVERSE_CANDIDATE_MODE:
            selected, selection_reasons = self._select_diverse_diagnostics(
                ranked,
                limit=self.config.candidate_limit,
            )
        else:
            selected = ranked[: self.config.candidate_limit]
            selection_reasons = {
                candidate.action: {"candidate_set:scalar_rank"}
                for candidate in selected
            }
        best = next(
            (candidate for candidate in ranked if candidate.action == best_action),
            None,
        )
        if best is not None and all(item.action != best_action for item in selected):
            selected = [best, *selected[: max(0, self.config.candidate_limit - 1)]]
            selection_reasons.setdefault(best_action, set()).add(
                "candidate_set:compatibility_best"
            )
        selected = sorted(
            selected,
            key=lambda candidate: (
                float(candidate.candidate_value),
                -int(candidate.action),
            ),
            reverse=True,
        )
        proposals = []
        for rank, candidate in enumerate(selected):
            recoverability = compare_build_potential_triggers(
                self._root_potential,
                candidate.potential,
                max_recovery_puyos=(
                    self.config.build_potential_budget.max_recovery_puyos
                ),
            )
            proposals.append(
                DiverseBeamCandidate(
                    rank=rank,
                    action=int(candidate.action),
                    plan=(
                        candidate.best_path
                        if candidate.best_path
                        else (int(candidate.action),)
                    ),
                    candidate_value=float(candidate.candidate_value),
                    predicted_max_chain=int(candidate.predicted_max_chain),
                    best_chain_depth=int(candidate.best_chain_depth),
                    build_potential=candidate.potential,
                    danger=float(candidate.danger),
                    continuation_flexibility=float(
                        candidate.continuation_flexibility
                    ),
                    trigger_recoverability=recoverability,
                    value_breakdown=dict(candidate.value_breakdown),
                    generation_reasons=candidate.generation_reasons,
                    retention_reasons=tuple(
                        sorted(
                            set(candidate.retention_reasons)
                            | selection_reasons.get(candidate.action, set())
                        )
                    ),
                    pruning_reasons=candidate.pruning_reasons,
                    scenario_support=len(candidate.scenario_ids),
                    scenario_ids=candidate.scenario_ids,
                )
            )
        return tuple(proposals)

    def _select_diverse_diagnostics(
        self,
        ranked: Sequence[BeamCandidateDiagnostics],
        *,
        limit: int,
    ) -> tuple[list[BeamCandidateDiagnostics], dict[int, set[str]]]:
        limit = min(max(0, int(limit)), len(ranked))
        if limit == 0:
            return [], {}
        selected: list[BeamCandidateDiagnostics] = []
        selected_actions: set[int] = set()
        reasons: dict[int, set[str]] = {}

        def add(candidate: BeamCandidateDiagnostics, reason: str) -> bool:
            if len(selected) >= limit or candidate.action in selected_actions:
                return False
            selected.append(candidate)
            selected_actions.add(candidate.action)
            reasons.setdefault(candidate.action, set()).add(
                f"candidate_set:{reason}"
            )
            return True

        axis_capacity = min(
            max(0, limit - 1),
            len(_DIVERSITY_AXES) * self.config.diversity_slots_per_axis,
        )
        elite_capacity = max(1, limit - axis_capacity)
        for candidate in ranked[:elite_capacity]:
            add(candidate, "scalar_elite")
        for axis in _DIVERSITY_AXES:
            axis_ranked = sorted(
                ranked,
                key=lambda candidate, name=axis: (
                    _diagnostic_diversity_axis(candidate, name),
                    float(candidate.candidate_value),
                    -int(candidate.action),
                ),
                reverse=True,
            )
            added = 0
            for candidate in axis_ranked:
                if add(candidate, f"axis:{axis}"):
                    added += 1
                    if added >= self.config.diversity_slots_per_axis:
                        break
        for candidate in ranked:
            add(candidate, "scalar_fill")
            if len(selected) >= limit:
                break
        return selected, reasons

    @staticmethod
    def _safe_fallback_action(simulator: Any, legal: Sequence[int]) -> int:
        for action in legal:
            child = clone_simulator(simulator)
            result = child.step(action_to_placement(action))
            if result.valid and not result.game_over:
                return int(action)
        return int(legal[0])

    def _chain_style_evaluation(
        self,
        simulator: Any,
    ) -> tuple[float, bool, bool, Mapping[str, Any]]:
        evaluator = self.config.chain_style_evaluator
        if evaluator is None:
            return (0.0, False, True, {})
        evaluation = evaluator.evaluate(simulator)
        return (
            float(evaluation.score_contribution),
            bool(evaluation.applicable),
            bool(evaluation.hard_constraint_satisfied),
            evaluation.to_dict(),
        )

    def _probe_potential(self, simulator) -> BuildPotential:
        return self._potential_session.evaluate(simulator)

    def _new_potential_session(self) -> BuildPotentialSession:
        return BuildPotentialSession(
            schema_version=self.config.build_potential_schema_version,
            budget=self.config.build_potential_budget,
            max_evaluations=self.config.potential_probe_budget,
            use_cache=self.config.use_potential_cache,
        )

    def _not_probed_potential(self) -> BuildPotential:
        return BuildPotential(
            evaluation_status="not_evaluated",
            budget=(
                self.config.build_potential_budget
                if self.config.build_potential_schema_version
                == BUILD_POTENTIAL_SCHEMA_VERSION
                else None
            ),
            schema_version=self.config.build_potential_schema_version,
        )

    def _trigger_preserved(self, selected: BuildPotential) -> bool:
        if self.config.scoring_mode != BUILD_SCORING_V2:
            return _same_trigger(self._root_potential, selected)
        return compare_build_potential_triggers(
            self._root_potential,
            selected,
            max_recovery_puyos=(
                self.config.build_potential_budget.max_recovery_puyos
            ),
        ).policy_preserved

    def _board_contributions(self, game) -> tuple[float, float]:
        if self.config.scoring_mode != BUILD_SCORING_V2:
            return evaluate_board(game), 0.0
        shape = (
            self.config.chain_shape_weight
            * evaluate_chain_shape_v2(game)
        )
        danger_risk = _board_danger_ratio(game)
        tolerance_scale = 1.0 + (1.0 - self.config.danger_tolerance)
        danger = (
            -10_000.0
            * self.config.danger_weight
            * danger_risk
            * tolerance_scale
        )
        return shape, danger

    def _chain_outcome_components(
        self,
        chain_count: int,
        score_delta: int,
    ) -> tuple[float, float, float]:
        if 0 < chain_count < self.config.minimum_chain_count:
            return (
                0.0,
                0.0,
                self.config.premature_chain_penalty * float(score_delta),
            )
        if chain_count == 0:
            return 0.0, 0.0, 0.0
        return (
            self.config.chain_weight * float(chain_count),
            self.config.score_weight * float(score_delta),
            0.0,
        )

    def _chain_outcome(self, chain_count: int, score_delta: int) -> tuple[float, float]:
        chain, score, penalty = self._chain_outcome_components(
            chain_count,
            score_delta,
        )
        return chain + score, penalty

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

    def _advance_chain_outcome_components(
        self,
        node: _Node,
        chain_count: int,
        score_delta: int,
    ) -> tuple[float, float, float, float]:
        chain, score, penalty = self._chain_outcome_components(
            chain_count,
            score_delta,
        )
        candidate_value = chain + score
        if candidate_value > node.best_chain_value:
            return (
                candidate_value,
                node.premature_penalty + penalty,
                chain,
                score,
            )
        return (
            node.best_chain_value,
            node.premature_penalty + penalty,
            node.actual_chain_contribution,
            node.actual_score_contribution,
        )


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


def evaluate_chain_shape_v2(game) -> float:
    """Symmetric, named-style-free chain-shape contribution for v2 scoring."""

    heights = _column_heights(game)
    groups = _color_groups(game)
    link_2 = sum(1 for group in groups if len(group) == 2)
    link_3 = sum(1 for group in groups if len(group) == 3)
    isolated = sum(1 for group in groups if len(group) == 1)
    reachable_ignitions = sum(
        _reachable_ignition_count(group, game, heights)
        for group in groups
        if len(group) == 3
    )
    adjacent_roughness = sum(
        abs(left - right) for left, right in zip(heights, heights[1:])
    )
    height_spread = max(heights) - min(heights)
    return (
        link_2 * 150.0
        + link_3 * 420.0
        + reachable_ignitions * 900.0
        - isolated * 18.0
        - adjacent_roughness * 45.0
        - height_spread * 20.0
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


def _build_potential_fingerprint(game) -> tuple:
    """Fingerprint only cells; lifecycle and tsumo state are out of contract."""

    grid = game.field.grid
    return tuple(
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


def evaluate_build_potential_v1(
    simulator: HeadlessPuyoSimulator,
    *,
    max_required_puyos: int = 3,
) -> BuildPotential:
    """Evaluate the legacy single-column ignition without mutating ``simulator``."""

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
                    evaluation_status="available",
                    search_complete=True,
                    schema_version=BUILD_POTENTIAL_V1_SCHEMA_VERSION,
                )
                if _potential_rank_key(potential) > _potential_rank_key(best):
                    best = potential
                break
    if best.exists:
        return best
    return BuildPotential(
        evaluation_status="not_found",
        search_complete=True,
        schema_version=BUILD_POTENTIAL_V1_SCHEMA_VERSION,
    )


def evaluate_build_potential(
    simulator: HeadlessPuyoSimulator,
    *,
    budget: BuildPotentialBudget | None = None,
    max_required_puyos: int | None = None,
) -> BuildPotential:
    """Evaluate deterministic multi-column BuildPotential v2.

    ``max_required_puyos`` remains as a compatibility spelling for callers that
    previously controlled the v1 ignition depth.  It now widens or narrows the
    explicit v2 added-puyo budget without restoring single-column semantics.
    """

    selected_budget = budget or BuildPotentialBudget()
    if max_required_puyos is not None:
        if max_required_puyos < 1:
            raise ValueError("max required puyos must be at least 1")
        selected_budget = BuildPotentialBudget(
            max_added_puyos=int(max_required_puyos),
            max_pattern_nodes=selected_budget.max_pattern_nodes,
            max_resolution_nodes=selected_budget.max_resolution_nodes,
            max_alternatives=selected_budget.max_alternatives,
            max_continuation_actions=selected_budget.max_continuation_actions,
            max_recovery_puyos=selected_budget.max_recovery_puyos,
        )
    heights = _column_heights(simulator.game)
    alternatives: list[TriggerAlternative] = []
    seen_alternatives: set[tuple[Any, ...]] = set()
    pattern_nodes = 0
    resolution_nodes = 0
    truncation_reason: str | None = None

    for added_puyos in range(1, selected_budget.max_added_puyos + 1):
        for column_orbit in _column_pattern_orbits(added_puyos):
            gravity_valid: list[tuple[tuple[int, int], ...]] = []
            for columns in column_orbit:
                counts = tuple(columns.count(x) for x in range(GRID_WIDTH))
                if any(
                    heights[x] + count > VISIBLE_HEIGHT
                    for x, count in enumerate(counts)
                ):
                    continue
                gravity_valid.append(
                    tuple(
                        (x, heights[x] + offset)
                        for x, count in enumerate(counts)
                        for offset in range(count)
                    )
                )
            if not gravity_valid:
                continue
            for color in NORMAL_PUYO_COLORS:
                # A mirror orbit is admitted atomically.  Reserving the whole
                # orbit prevents a count cutoff from preferring the left or
                # right orientation while keeping actual node counts bounded.
                if (
                    pattern_nodes + len(gravity_valid)
                    > selected_budget.max_pattern_nodes
                ):
                    truncation_reason = "pattern_nodes"
                    break
                pattern_nodes += len(gravity_valid)
                pending: list[
                    tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]
                ] = []
                for placements in gravity_valid:
                    anchors = _virtual_pattern_trigger_anchors(
                        simulator.game,
                        placements=placements,
                        color=color,
                    )
                    if anchors is None:
                        continue
                    if any(
                        _virtual_pattern_trigger_anchors(
                            simulator.game,
                            placements=reduced,
                            color=color,
                        )
                        is not None
                        for reduced in _one_puyo_smaller_gravity_patterns(
                            placements,
                            heights=heights,
                        )
                    ):
                        # Supersets inflate alternatives and ignition cost
                        # without representing a new minimal first ignition.
                        continue
                    pending.append((placements, anchors))
                if (
                    resolution_nodes + len(pending)
                    > selected_budget.max_resolution_nodes
                ):
                    truncation_reason = "resolution_nodes"
                    break
                for placements, anchors in pending:
                    candidate = clone_simulator(simulator)
                    # Lifecycle entitlement is deliberately excluded: this is
                    # a board-only latent-chain probe, not an attack preview.
                    candidate.game.all_clear_bonus_pending = False
                    score_before = int(candidate.game.score)
                    for x, y in placements:
                        candidate.game.field.place_puyo(x, y, Puyo(color))
                    chains = candidate.game.resolve_chains_synchronously(
                        spawn_next=False,
                        capture_visuals=False,
                    )
                    resolution_nodes += 1
                    if not chains:
                        continue
                    alternative = TriggerAlternative(
                        chain_count=len(chains),
                        score=max(
                            0,
                            int(candidate.game.score) - score_before,
                        ),
                        added_puyos=added_puyos,
                        trigger_color=color,
                        placements=placements,
                        anchor_cells=anchors,
                        danger_margin=_board_danger_margin(candidate.game),
                    )
                    signature = (
                        alternative.chain_count,
                        alternative.added_puyos,
                        alternative.trigger_color.value,
                        alternative.placements,
                        alternative.anchor_cells,
                    )
                    if signature not in seen_alternatives:
                        seen_alternatives.add(signature)
                        alternatives.append(alternative)
            if truncation_reason is not None:
                break
        if truncation_reason is not None:
            break

    alternatives.sort(key=_alternative_rank_key, reverse=True)
    retained = tuple(alternatives[: selected_budget.max_alternatives])
    complete = truncation_reason is None
    continuation = _continuation_flexibility(simulator, selected_budget)
    danger_margin = _board_danger_margin(simulator.game)
    if retained:
        primary = retained[0]
        potential_value = _predicted_chain_potential(
            primary,
            alternative_count=len(retained),
            budget=selected_budget,
        )
        return BuildPotential(
            chain_count=primary.chain_count,
            required_puyos=primary.added_puyos,
            trigger_x=primary.placements[0][0],
            trigger_y=primary.placements[0][1],
            trigger_color=primary.trigger_color,
            alternatives=retained,
            predicted_chain_potential=potential_value,
            continuation_flexibility=continuation,
            danger_margin=danger_margin,
            evaluation_status=("available" if complete else "budget_exhausted"),
            search_complete=complete,
            pattern_nodes=pattern_nodes,
            resolution_nodes=resolution_nodes,
            truncation_reason=truncation_reason,
            budget=selected_budget,
            schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
        )
    return BuildPotential(
        predicted_chain_potential=(0.0 if complete else None),
        continuation_flexibility=continuation,
        danger_margin=danger_margin,
        evaluation_status=("not_found" if complete else "budget_exhausted"),
        search_complete=complete,
        pattern_nodes=pattern_nodes,
        resolution_nodes=resolution_nodes,
        truncation_reason=truncation_reason,
        budget=selected_budget,
        schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
    )


def _column_pattern_orbits(
    added_puyos: int,
) -> tuple[tuple[tuple[int, ...], ...], ...]:
    """Return deterministic column multisets grouped with their mirror."""

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
            (columns,)
            if columns == mirrored
            else tuple(sorted((columns, mirrored)))
        )
    return tuple(orbits)


def _one_puyo_smaller_gravity_patterns(
    placements: tuple[tuple[int, int], ...],
    *,
    heights: tuple[int, ...],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Remove one virtual puyo and compact its column under gravity."""

    counts = tuple(
        sum(x == column for x, _ in placements)
        for column in range(GRID_WIDTH)
    )
    patterns = []
    for removed_column, count in enumerate(counts):
        if count == 0:
            continue
        reduced_counts = list(counts)
        reduced_counts[removed_column] -= 1
        patterns.append(
            tuple(
                (column, heights[column] + offset)
                for column, reduced_count in enumerate(reduced_counts)
                for offset in range(reduced_count)
            )
        )
    return tuple(patterns)


def _alternative_rank_key(
    alternative: TriggerAlternative,
) -> tuple[Any, ...]:
    color_rank = NORMAL_PUYO_COLORS.index(alternative.trigger_color)
    return (
        int(alternative.chain_count),
        -int(alternative.added_puyos),
        int(alternative.score),
        float(alternative.danger_margin),
        tuple((-x, -y) for x, y in alternative.placements),
        -color_rank,
    )


def _predicted_chain_potential(
    primary: TriggerAlternative,
    *,
    alternative_count: int,
    budget: BuildPotentialBudget,
) -> float:
    chain_progress = min(1.0, primary.chain_count / 19.0)
    if budget.max_added_puyos <= 1:
        ignition_efficiency = 1.0
    else:
        ignition_efficiency = 1.0 - (
            (primary.added_puyos - 1) / float(budget.max_added_puyos - 1)
        )
    alternative_strength = min(1.0, alternative_count / 4.0)
    return max(
        0.0,
        min(
            1.0,
            chain_progress * 0.65
            + ignition_efficiency * 0.25
            + alternative_strength * 0.10,
        ),
    )


def _virtual_pattern_trigger_anchors(
    game,
    *,
    placements: tuple[tuple[int, int], ...],
    color: PuyoColor,
) -> tuple[tuple[int, int], ...] | None:
    virtual = frozenset(placements)
    if not virtual:
        return None
    visited: set[tuple[int, int]] = set()
    for origin in placements:
        if origin in visited:
            continue
        stack = [origin]
        component: set[tuple[int, int]] = set()
        anchors: set[tuple[int, int]] = set()
        while stack:
            cell = stack.pop()
            if cell in component:
                continue
            x, y = cell
            if not (0 <= x < GRID_WIDTH and 0 <= y < VISIBLE_HEIGHT):
                continue
            if cell in virtual:
                pass
            elif game.field.grid[y][x].color == color:
                anchors.add(cell)
            else:
                continue
            component.add(cell)
            visited.add(cell)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                stack.append((x + dx, y + dy))
        if len(component) >= 4 and component.intersection(virtual):
            return tuple(sorted(anchors))
    return None


def _board_danger_ratio(game) -> float:
    heights = _column_heights(game)
    ojama = sum(
        game.field.grid[y][x].color == PuyoColor.OJAMA
        for y in range(GRID_HEIGHT)
        for x in range(GRID_WIDTH)
    )
    center = heights[2] / float(VISIBLE_HEIGHT)
    peak = max(heights) / float(VISIBLE_HEIGHT)
    nuisance = min(1.0, ojama / 30.0)
    return min(1.0, center * 0.55 + peak * 0.35 + nuisance * 0.10)


def _board_danger_margin(game) -> float:
    return max(0.0, min(1.0, 1.0 - _board_danger_ratio(game)))


def _continuation_flexibility(
    simulator: HeadlessPuyoSimulator,
    budget: BuildPotentialBudget,
) -> float:
    # Treat one bounded continuation action as one left/right-equivalent
    # column orbit.  Cutting the raw column sequence would inspect the left
    # edge first and make otherwise mirrored boards score differently.
    column_orbits = tuple(
        (left, GRID_WIDTH - 1 - left)
        for left in reversed(range(GRID_WIDTH // 2))
    )
    selected_orbits = column_orbits[: budget.max_continuation_actions]
    columns = tuple(x for orbit in selected_orbits for x in orbit)
    if not columns:
        return 0.0
    heights = _column_heights(simulator.game)
    # Two cells are one future pair's maximum vertical footprint.  Averaging
    # bounded per-column headroom is board-only, color/style neutral, and stable
    # across cache modes and hidden-tsumo scenarios.
    return sum(
        min(2, max(0, VISIBLE_HEIGHT - heights[x])) / 2.0
        for x in columns
    ) / float(len(columns))


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


def _node_rank_key(node: _Node, *, base: bool = False) -> tuple[Any, ...]:
    value = _base_node_value(node) if base else _node_value(node)
    return (
        float(value),
        (
            _potential_rank_key(node.potential)
            if not base and node.potential.schema_version
            == BUILD_POTENTIAL_V1_SCHEMA_VERSION
            else (0, 0, 0, 0, 0)
        ),
        -int(node.root_action),
        tuple(-int(action) for action in node.path),
    )


def _recoverability_rank(value: TriggerRecoverability) -> int:
    return {
        "exact": 5,
        "equivalent": 4,
        "recoverable": 3,
        "not_applicable": 2,
        "unknown": 1,
        "lost": 0,
    }.get(value.status, 0)


def _node_diversity_axis(node: _Node, axis: str) -> tuple[float, ...]:
    if axis == "potential":
        potential = _potential_value(node.potential)
        return (float(-1.0 if potential is None else potential),)
    if axis == "survival":
        return (float(1.0 - node.danger),)
    if axis == "continuation":
        return (float(node.continuation_flexibility),)
    if axis == "actual_chain":
        return (float(node.best_chain_count), float(node.best_chain_value))
    if axis == "recoverability":
        return (float(_recoverability_rank(node.trigger_recoverability)),)
    raise ValueError(f"unsupported diversity axis: {axis}")


def _diagnostic_diversity_axis(
    candidate: BeamCandidateDiagnostics,
    axis: str,
) -> tuple[float, ...]:
    if axis == "potential":
        potential = _potential_value(candidate.potential)
        return (float(-1.0 if potential is None else potential),)
    if axis == "survival":
        return (float(1.0 - candidate.danger),)
    if axis == "continuation":
        return (float(candidate.continuation_flexibility),)
    if axis == "actual_chain":
        return (
            float(candidate.predicted_max_chain),
            (
                float("-inf")
                if candidate.candidate_value is None
                else float(candidate.candidate_value)
            ),
        )
    if axis == "recoverability":
        return (float(_recoverability_rank(candidate.trigger_recoverability)),)
    raise ValueError(f"unsupported diversity axis: {axis}")


def _node_value_breakdown(node: _Node) -> dict[str, float]:
    result = {
        "actual_chain": float(node.actual_chain_contribution),
        "actual_score": float(node.actual_score_contribution),
        "chain_shape": float(node.chain_shape_contribution),
        "future_potential": float(node.future_potential_contribution),
        "danger": float(node.danger_contribution),
        "trigger_preservation": float(node.trigger_preservation_contribution),
        "style_adherence": float(node.chain_style_contribution),
        "premature_fire": -float(node.premature_penalty),
    }
    result["total"] = sum(result.values())
    return result


def _merge_value_breakdown(
    target: dict[str, float],
    source: Mapping[str, float],
) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0.0) + float(value)


def _potential_value(potential: BuildPotential) -> float | None:
    if potential.schema_version == BUILD_POTENTIAL_SCHEMA_VERSION:
        if not potential.evaluated or potential.predicted_chain_potential is None:
            return None
        return float(potential.predicted_chain_potential)
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
