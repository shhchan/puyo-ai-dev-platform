"""Deterministic compact long-horizon expected-chain search.

This module owns the PUYO-174 search semantics.  It deliberately has no
dependency on the legacy simulator-backed beam implementation so the latter
can remain a compatibility path while quality profiles use the compact kernel.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agents.chain_structure import (
    ChainStructureAction,
    ChainStructureEvaluator,
    CompactNodeEvaluator,
)
from agents.compact_search import (
    CompactSearchState,
    CompactTranspositionKey,
    legal_action_indices,
    transition,
)
from src.core.constants import NORMAL_PUYO_COLORS, PuyoColor
from src.core.headless import HeadlessPuyoSimulator


LONG_HORIZON_PROFILE_SCHEMA_VERSION = "puyo.long_horizon_profile.v1"
EXPECTED_CHAIN_EVIDENCE_SCHEMA_VERSION = "puyo.expected_chain_evidence.v1"
EXPECTED_CHAIN_RANKING_RULE_VERSION = "puyo.expected_chain_ranking.v1"
SCENARIO_SEQUENCE_SCHEMA_VERSION = "puyo.long_horizon_scenario_sequence.v1"
LONG_HORIZON_PROPOSAL_DIGEST_VERSION = "puyo.long_horizon_proposal_digest.v1"

TERMINAL_FIRE_CONTINUE = "continue"
TERMINAL_FIRE_RECORD_AND_STOP = "record_and_stop"
TERMINAL_FIRE_RULES = {
    TERMINAL_FIRE_CONTINUE,
    TERMINAL_FIRE_RECORD_AND_STOP,
}

RUNTIME_PROFILE = "runtime"
QUALITY_D12_PROFILE = "quality-d12"
QUALITY_D16_PROFILE = "quality-d16"

# Ama-inspired representative color orderings.  Each ordering becomes two
# unknown pairs and repeats only after current + NEXT2 have been consumed.
REPRESENTATIVE_SCENARIO_BAGS = (
    (0, 1, 2, 3),
    (0, 2, 1, 3),
    (0, 3, 1, 2),
    (1, 2, 0, 3),
    (1, 3, 0, 2),
    (2, 3, 0, 1),
)


def _stable_digest(value: Any, *, prefix: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def compact_state_fingerprint(state: CompactSearchState) -> str:
    return f"compact-{hashlib.sha256(state.to_bytes()).hexdigest()[:24]}"


def long_horizon_proposal_digest(
    proposals: Sequence[Mapping[str, Any]],
) -> str:
    """Digest semantic K-best payloads without search-cost telemetry."""

    return _stable_digest(
        {
            "digest_version": LONG_HORIZON_PROPOSAL_DIGEST_VERSION,
            "proposals": [dict(proposal) for proposal in proposals],
        },
        prefix="long-horizon-proposal",
    )


@dataclass(frozen=True, slots=True)
class LongHorizonSearchProfile:
    """One versioned execution profile with an explicit budget authority."""

    name: str
    version: str
    depth: int
    width: int
    scenarios: int
    max_expanded_nodes: int
    candidate_limit: int = 8
    terminal_fire_rule: str = TERMINAL_FIRE_RECORD_AND_STOP
    terminal_fire_chain_count: int = 1
    use_transposition_table: bool = True
    budget_authority: str = "expanded_nodes"
    wall_clock_mode: str = "observational"
    schema_version: str = LONG_HORIZON_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LONG_HORIZON_PROFILE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported long-horizon profile schema: {self.schema_version}"
            )
        if not self.name or not self.version:
            raise ValueError("long-horizon profile name and version are required")
        if (
            min(
                self.depth,
                self.width,
                self.scenarios,
                self.max_expanded_nodes,
                self.candidate_limit,
                self.terminal_fire_chain_count,
            )
            <= 0
        ):
            raise ValueError("long-horizon profile budgets must be positive")
        if self.scenarios > len(REPRESENTATIVE_SCENARIO_BAGS):
            raise ValueError("long-horizon profile requests too many scenarios")
        if self.terminal_fire_rule not in TERMINAL_FIRE_RULES:
            raise ValueError(
                f"unsupported terminal-fire rule: {self.terminal_fire_rule}"
            )
        if self.budget_authority not in {
            "expanded_nodes",
            "external_runtime_deadline",
        }:
            raise ValueError("unsupported long-horizon budget authority")
        if self.wall_clock_mode not in {
            "observational",
            "external_deadline_contract",
        }:
            raise ValueError("unsupported long-horizon wall-clock mode")
        if self.name.startswith("quality-") and (
            self.budget_authority != "expanded_nodes"
            or self.wall_clock_mode != "observational"
        ):
            raise ValueError("quality profiles must use count-authoritative budgets")

    @property
    def profile_id(self) -> str:
        return f"{self.name}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "profile_id": self.profile_id,
            "depth": int(self.depth),
            "width": int(self.width),
            "scenarios": int(self.scenarios),
            "candidate_limit": int(self.candidate_limit),
            "terminal_fire": {
                "rule": self.terminal_fire_rule,
                "minimum_chain_count": int(self.terminal_fire_chain_count),
            },
            "transposition_table": bool(self.use_transposition_table),
            "budget": {
                "authority": self.budget_authority,
                "max_expanded_nodes": int(self.max_expanded_nodes),
                "wall_clock_mode": self.wall_clock_mode,
            },
        }


LONG_HORIZON_SEARCH_PROFILES: Mapping[str, LongHorizonSearchProfile] = {
    RUNTIME_PROFILE: LongHorizonSearchProfile(
        name=RUNTIME_PROFILE,
        version="1.0",
        depth=3,
        width=24,
        scenarios=1,
        max_expanded_nodes=2_048,
        budget_authority="external_runtime_deadline",
        wall_clock_mode="external_deadline_contract",
    ),
    QUALITY_D12_PROFILE: LongHorizonSearchProfile(
        name=QUALITY_D12_PROFILE,
        version="1.0",
        depth=12,
        width=128,
        scenarios=6,
        max_expanded_nodes=200_000,
    ),
    QUALITY_D16_PROFILE: LongHorizonSearchProfile(
        name=QUALITY_D16_PROFILE,
        version="1.0",
        depth=16,
        width=250,
        scenarios=6,
        max_expanded_nodes=600_000,
    ),
}


def long_horizon_profile(name: str) -> LongHorizonSearchProfile:
    try:
        return LONG_HORIZON_SEARCH_PROFILES[str(name)]
    except KeyError as exc:
        raise ValueError(f"unknown long-horizon profile: {name}") from exc


def _pair_colors(pair: Sequence[Any]) -> tuple[PuyoColor, PuyoColor]:
    if len(pair) != 2:
        raise ValueError("puyo pair must contain exactly two colors")
    colors = tuple(
        item if isinstance(item, PuyoColor) else getattr(item, "color", item)
        for item in pair
    )
    if any(color not in NORMAL_PUYO_COLORS for color in colors):
        raise ValueError("scenario pairs must contain normal puyo colors")
    return colors  # type: ignore[return-value]


def _pair_payload(pair: Sequence[PuyoColor]) -> list[str]:
    return [pair[0].name, pair[1].name]


@dataclass(frozen=True, slots=True)
class ScenarioPairSequence:
    """Known current/NEXT2 pairs followed by one representative future cycle."""

    scenario_id: int
    known_pairs: tuple[tuple[PuyoColor, PuyoColor], ...]
    hidden_cycle: tuple[tuple[PuyoColor, PuyoColor], ...]
    depth: int
    schema_version: str = SCENARIO_SEQUENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCENARIO_SEQUENCE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported scenario sequence schema: {self.schema_version}"
            )
        if not 0 <= self.scenario_id < len(REPRESENTATIVE_SCENARIO_BAGS):
            raise ValueError("scenario id is outside the representative set")
        if not self.known_pairs or not self.hidden_cycle or self.depth <= 0:
            raise ValueError("scenario sequence requires known and hidden pairs")

    @property
    def known_pair_count(self) -> int:
        return len(self.known_pairs)

    def pair_at(self, pair_cursor: int) -> tuple[PuyoColor, PuyoColor]:
        if pair_cursor < 0:
            raise ValueError("scenario pair cursor must be non-negative")
        if pair_cursor < self.known_pair_count:
            return self.known_pairs[pair_cursor]
        hidden_cursor = pair_cursor - self.known_pair_count
        return self.hidden_cycle[hidden_cursor % len(self.hidden_cycle)]

    @property
    def pairs(self) -> tuple[tuple[PuyoColor, PuyoColor], ...]:
        return tuple(self.pair_at(cursor) for cursor in range(self.depth))

    @property
    def sequence_digest(self) -> str:
        return _stable_digest(
            {
                "schema_version": self.schema_version,
                "scenario_id": self.scenario_id,
                "known_pair_count": self.known_pair_count,
                "pairs": [_pair_payload(pair) for pair in self.pairs],
            },
            prefix="scenario-sequence",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": int(self.scenario_id),
            "known_pair_count": int(self.known_pair_count),
            "unknown_boundary_cursor": int(self.known_pair_count),
            "hidden_cycle": [_pair_payload(pair) for pair in self.hidden_cycle],
            "pairs": [
                {
                    "cursor": cursor,
                    "source": (
                        "known" if cursor < self.known_pair_count else "unknown"
                    ),
                    "colors": _pair_payload(self.pair_at(cursor)),
                }
                for cursor in range(self.depth)
            ],
            "sequence_digest": self.sequence_digest,
        }


def build_scenario_sequences(
    simulator: HeadlessPuyoSimulator,
    *,
    scenarios: int,
    depth: int,
    scenario_seed: int | None = None,
) -> tuple[ScenarioPairSequence, ...]:
    if not 1 <= scenarios <= len(REPRESENTATIVE_SCENARIO_BAGS):
        raise ValueError("scenario count is outside the representative set")
    game = simulator.game
    current = (game.current_puyo_1, game.current_puyo_2)
    if any(item is None for item in current):
        raise ValueError("long-horizon search requires an active current pair")
    known_pairs = (_pair_colors(current),) + tuple(
        _pair_colors(pair) for pair in tuple(game.next_puyo_queue)[:2]
    )
    if len(known_pairs) < 3:
        raise ValueError("long-horizon search requires current + NEXT2")

    scenario_ids = list(range(len(REPRESENTATIVE_SCENARIO_BAGS)))
    color_orders = [tuple(NORMAL_PUYO_COLORS)] * scenarios
    if scenario_seed is not None:
        rng = random.Random(int(scenario_seed))
        rng.shuffle(scenario_ids)
        color_orders = []
        for _ in range(scenarios):
            colors = list(NORMAL_PUYO_COLORS)
            rng.shuffle(colors)
            color_orders.append(tuple(colors))

    result = []
    for scenario_id, colors in zip(scenario_ids[:scenarios], color_orders):
        bag = REPRESENTATIVE_SCENARIO_BAGS[scenario_id]
        hidden_cycle = (
            (colors[bag[0]], colors[bag[1]]),
            (colors[bag[2]], colors[bag[3]]),
        )
        result.append(
            ScenarioPairSequence(
                scenario_id=int(scenario_id),
                known_pairs=known_pairs,
                hidden_cycle=hidden_cycle,
                depth=int(depth),
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ChainFireEvidence:
    root_action: int
    scenario_id: int
    chain_count: int
    chain_score: int
    depth: int
    trigger_action: int
    state_fingerprint: str
    path: tuple[int, ...]
    terminal: bool
    terminal_reason: str | None

    @property
    def rank_key(self) -> tuple[Any, ...]:
        return (
            int(self.chain_score),
            int(self.chain_count),
            -int(self.depth),
            -int(self.trigger_action),
            self.state_fingerprint,
            tuple(-int(action) for action in self.path),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_action": int(self.root_action),
            "scenario_id": int(self.scenario_id),
            "chain_count": int(self.chain_count),
            "official_chain_score": int(self.chain_score),
            "depth": int(self.depth),
            "trigger_action": int(self.trigger_action),
            "state_fingerprint": self.state_fingerprint,
            "path": [int(action) for action in self.path],
            "terminal": bool(self.terminal),
            "terminal_reason": self.terminal_reason,
        }


@dataclass(frozen=True, slots=True)
class ScenarioRootEvidence:
    root_action: int
    scenario_id: int
    evaluated: bool
    search_complete: bool
    reached_depth: int
    max_chain_count: int
    max_chain_score: int
    best_fire: ChainFireEvidence | None
    fire_count: int
    terminal_fire_count: int
    survivor_evaluator_score: float | None
    expanded_nodes: int
    pruned_nodes: int
    transposition_hits: int
    truncation_reason: str | None
    terminal_fire_rule: str
    terminal_fire_chain_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_action": int(self.root_action),
            "scenario_id": int(self.scenario_id),
            "evaluated": bool(self.evaluated),
            "search_complete": bool(self.search_complete),
            "reached_depth": int(self.reached_depth),
            "max_chain_count": int(self.max_chain_count),
            "max_chain_score": int(self.max_chain_score),
            "best_fire": (None if self.best_fire is None else self.best_fire.to_dict()),
            "fire_count": int(self.fire_count),
            "terminal_fire_count": int(self.terminal_fire_count),
            "survivor_evaluator_score": self.survivor_evaluator_score,
            "expanded_nodes": int(self.expanded_nodes),
            "pruned_nodes": int(self.pruned_nodes),
            "transposition_hits": int(self.transposition_hits),
            "truncation_reason": self.truncation_reason,
            "terminal_fire": {
                "rule": self.terminal_fire_rule,
                "minimum_chain_count": int(self.terminal_fire_chain_count),
            },
        }


@dataclass(frozen=True, slots=True)
class ExpectedChainRootEvidence:
    root_action: int
    requested_scenarios: int
    scenario_values: tuple[ScenarioRootEvidence, ...]
    chain_score_sum: int
    chain_score_mean: float
    chain_count_sum: int
    chain_count_mean: float
    support: int
    worst_chain_score: int
    worst_chain_count: int
    chain_score_dispersion: float
    chain_count_dispersion: float
    continuation_score_mean: float | None
    max_chain_score: int
    max_chain_count: int
    best_fire: ChainFireEvidence | None
    ranking_rule_version: str = EXPECTED_CHAIN_RANKING_RULE_VERSION
    schema_version: str = EXPECTED_CHAIN_EVIDENCE_SCHEMA_VERSION

    @property
    def evaluated_scenarios(self) -> int:
        return sum(int(value.evaluated) for value in self.scenario_values)

    @property
    def coverage(self) -> float:
        if self.requested_scenarios <= 0:
            return 0.0
        return self.evaluated_scenarios / float(self.requested_scenarios)

    @property
    def candidate_value(self) -> float:
        # The public scalar remains the summed official score.  The versioned
        # tuple below supplies deterministic tie breaks without corrupting it.
        return float(self.chain_score_sum)

    @property
    def ranking_key(self) -> tuple[Any, ...]:
        return (
            float(self.coverage),
            int(self.chain_score_sum),
            int(self.chain_count_sum),
            int(self.support),
            int(self.worst_chain_score),
            int(self.worst_chain_count),
            -float(self.chain_score_dispersion),
            -float(self.chain_count_dispersion),
            (
                float("-inf")
                if self.continuation_score_mean is None
                else float(self.continuation_score_mean)
            ),
            -int(self.root_action),
        )

    def value_breakdown(self) -> dict[str, float]:
        return {
            "expected_chain_score_sum": float(self.chain_score_sum),
            "expected_chain_score_mean": float(self.chain_score_mean),
            "expected_chain_count_sum": float(self.chain_count_sum),
            "expected_chain_count_mean": float(self.chain_count_mean),
            "expected_chain_support": float(self.support),
            "expected_chain_worst_score": float(self.worst_chain_score),
            "expected_chain_score_dispersion": -float(self.chain_score_dispersion),
            "continuation_evaluator": float(
                0.0
                if self.continuation_score_mean is None
                else self.continuation_score_mean
            ),
            "total": self.candidate_value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ranking_rule_version": self.ranking_rule_version,
            "root_action": int(self.root_action),
            "requested_scenarios": int(self.requested_scenarios),
            "evaluated_scenarios": int(self.evaluated_scenarios),
            "coverage": float(self.coverage),
            "chain_score": {
                "sum": int(self.chain_score_sum),
                "mean": float(self.chain_score_mean),
                "worst": int(self.worst_chain_score),
                "dispersion": float(self.chain_score_dispersion),
                "maximum": int(self.max_chain_score),
            },
            "chain_count": {
                "sum": int(self.chain_count_sum),
                "mean": float(self.chain_count_mean),
                "worst": int(self.worst_chain_count),
                "dispersion": float(self.chain_count_dispersion),
                "maximum": int(self.max_chain_count),
            },
            "support": int(self.support),
            "continuation_score_mean": self.continuation_score_mean,
            "candidate_value": self.candidate_value,
            "best_fire": (None if self.best_fire is None else self.best_fire.to_dict()),
            "scenario_values": [value.to_dict() for value in self.scenario_values],
        }


@dataclass(frozen=True, slots=True)
class LongHorizonSearchConfig:
    depth: int
    width: int
    scenarios: int
    minimum_chain_count: int
    max_expanded_nodes: int
    scenario_seed: int | None = None
    terminal_fire_rule: str = TERMINAL_FIRE_RECORD_AND_STOP
    terminal_fire_chain_count: int = 1
    use_transposition_table: bool = True

    def __post_init__(self) -> None:
        if (
            min(
                self.depth,
                self.width,
                self.scenarios,
                self.minimum_chain_count,
                self.max_expanded_nodes,
                self.terminal_fire_chain_count,
            )
            <= 0
        ):
            raise ValueError("long-horizon search values must be positive")
        if self.scenarios > len(REPRESENTATIVE_SCENARIO_BAGS):
            raise ValueError("long-horizon search requests too many scenarios")
        if self.terminal_fire_rule not in TERMINAL_FIRE_RULES:
            raise ValueError(
                f"unsupported terminal-fire rule: {self.terminal_fire_rule}"
            )


@dataclass(frozen=True, slots=True)
class LongHorizonNode:
    state: CompactSearchState
    root_action: int
    scenario_id: int
    pair_cursor: int
    path: tuple[int, ...]
    evaluator_score: float
    evaluator_result: Any | None
    cumulative_action_score: int
    last_action: int

    @property
    def state_fingerprint(self) -> str:
        return compact_state_fingerprint(self.state)

    @property
    def danger(self) -> float:
        if self.evaluator_result is None:
            return 1.0 if self.state.game_over else 0.0
        return float(getattr(self.evaluator_result, "danger", 1.0))

    @property
    def continuation_flexibility(self) -> float:
        if self.evaluator_result is None:
            return 0.0
        return float(getattr(self.evaluator_result, "continuation_flexibility", 0.0))


@dataclass(slots=True)
class LongHorizonSearchCounters:
    expanded_nodes: int = 0
    generated_nodes: int = 0
    invalid_nodes: int = 0
    game_over_nodes: int = 0
    evaluated_nodes: int = 0
    pruned_nodes: int = 0
    transposition_hits: int = 0
    terminal_fire_nodes: int = 0
    reached_depth: int = 0
    budget_exhausted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "expanded_nodes": int(self.expanded_nodes),
            "generated_nodes": int(self.generated_nodes),
            "invalid_nodes": int(self.invalid_nodes),
            "game_over_nodes": int(self.game_over_nodes),
            "evaluated_nodes": int(self.evaluated_nodes),
            "pruned_nodes": int(self.pruned_nodes),
            "transposition_hits": int(self.transposition_hits),
            "terminal_fire_nodes": int(self.terminal_fire_nodes),
            "reached_depth": int(self.reached_depth),
            "budget_exhausted": bool(self.budget_exhausted),
        }


@dataclass(frozen=True, slots=True)
class LongHorizonSearchResult:
    root_evidence: tuple[ExpectedChainRootEvidence, ...]
    representatives: Mapping[int, LongHorizonNode]
    scenario_sequences: tuple[ScenarioPairSequence, ...]
    root_evaluation: Any
    counters: LongHorizonSearchCounters
    root_transposition_hits: Mapping[int, int]
    root_pruned_nodes: Mapping[int, int]
    root_reached_depth: Mapping[int, int]
    root_generated_scenarios: Mapping[int, tuple[int, ...]]

    @property
    def ranked_roots(self) -> tuple[ExpectedChainRootEvidence, ...]:
        return tuple(
            sorted(
                self.root_evidence,
                key=lambda value: value.ranking_key,
                reverse=True,
            )
        )

    @property
    def evidence_by_action(self) -> dict[int, ExpectedChainRootEvidence]:
        return {value.root_action: value for value in self.root_evidence}

    @property
    def deterministic_digest(self) -> str:
        return _stable_digest(
            {
                "root_evidence": [
                    evidence.to_dict() for evidence in self.root_evidence
                ],
                "scenario_sequences": [
                    sequence.to_dict() for sequence in self.scenario_sequences
                ],
            },
            prefix="long-horizon-search",
        )


@dataclass(slots=True)
class _ScenarioTracker:
    root_action: int
    scenario_id: int
    terminal_fire_rule: str
    terminal_fire_chain_count: int
    evaluated: bool = False
    search_complete: bool = True
    reached_depth: int = 0
    max_chain_count: int = 0
    max_chain_score: int = 0
    best_fire: ChainFireEvidence | None = None
    fire_count: int = 0
    terminal_fire_count: int = 0
    best_survivor: LongHorizonNode | None = None
    best_terminal: LongHorizonNode | None = None
    expanded_nodes: int = 0
    pruned_nodes: int = 0
    transposition_hits: int = 0
    truncation_reason: str | None = None

    def record_fire(
        self,
        *,
        result: Any,
        path: tuple[int, ...],
        terminal: bool,
        terminal_reason: str | None,
    ) -> None:
        evidence = ChainFireEvidence(
            root_action=int(self.root_action),
            scenario_id=int(self.scenario_id),
            chain_count=int(result.chain_count),
            chain_score=int(result.score_delta),
            depth=len(path),
            trigger_action=int(path[-1]),
            state_fingerprint=compact_state_fingerprint(result.state),
            path=path,
            terminal=bool(terminal),
            terminal_reason=terminal_reason,
        )
        self.fire_count += 1
        self.terminal_fire_count += int(terminal)
        self.max_chain_count = max(self.max_chain_count, evidence.chain_count)
        self.max_chain_score = max(self.max_chain_score, evidence.chain_score)
        if self.best_fire is None or evidence.rank_key > self.best_fire.rank_key:
            self.best_fire = evidence

    def record_survivor(self, node: LongHorizonNode) -> None:
        if self.best_survivor is None or _survivor_sort_key(node) < _survivor_sort_key(
            self.best_survivor
        ):
            self.best_survivor = node
        self.reached_depth = max(self.reached_depth, len(node.path))

    def record_terminal(self, node: LongHorizonNode) -> None:
        if self.best_terminal is None:
            self.best_terminal = node
            return
        left = self.best_fire
        if left is not None and left.path == node.path:
            self.best_terminal = node

    @property
    def representative(self) -> LongHorizonNode | None:
        if self.best_fire is not None and self.best_terminal is not None:
            if self.best_terminal.path == self.best_fire.path:
                return self.best_terminal
        return self.best_survivor or self.best_terminal

    def finish(self, *, budget_exhausted: bool, target_depth: int) -> None:
        if budget_exhausted and self.evaluated and self.reached_depth < target_depth:
            self.search_complete = False
            self.truncation_reason = "expanded_node_budget"
        elif not self.evaluated:
            self.search_complete = False
            self.truncation_reason = (
                "expanded_node_budget" if budget_exhausted else "not_evaluated"
            )

    def to_evidence(self) -> ScenarioRootEvidence:
        return ScenarioRootEvidence(
            root_action=int(self.root_action),
            scenario_id=int(self.scenario_id),
            evaluated=bool(self.evaluated),
            search_complete=bool(self.search_complete),
            reached_depth=int(self.reached_depth),
            max_chain_count=int(self.max_chain_count),
            max_chain_score=int(self.max_chain_score),
            best_fire=self.best_fire,
            fire_count=int(self.fire_count),
            terminal_fire_count=int(self.terminal_fire_count),
            survivor_evaluator_score=(
                None
                if self.best_survivor is None
                else float(self.best_survivor.evaluator_score)
            ),
            expanded_nodes=int(self.expanded_nodes),
            pruned_nodes=int(self.pruned_nodes),
            transposition_hits=int(self.transposition_hits),
            truncation_reason=self.truncation_reason,
            terminal_fire_rule=self.terminal_fire_rule,
            terminal_fire_chain_count=int(self.terminal_fire_chain_count),
        )


def _mean(values: Sequence[float]) -> float:
    return 0.0 if not values else sum(values) / float(len(values))


def _dispersion(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def aggregate_expected_chain_evidence(
    root_action: int,
    scenario_values: Sequence[ScenarioRootEvidence],
    *,
    requested_scenarios: int,
) -> ExpectedChainRootEvidence:
    raw = tuple(sorted(scenario_values, key=lambda value: value.scenario_id))
    evaluated = tuple(value for value in raw if value.evaluated)
    scores = [float(value.max_chain_score) for value in evaluated]
    counts = [float(value.max_chain_count) for value in evaluated]
    continuations = [
        float(value.survivor_evaluator_score)
        for value in evaluated
        if value.survivor_evaluator_score is not None
    ]
    fires = [value.best_fire for value in evaluated if value.best_fire is not None]
    return ExpectedChainRootEvidence(
        root_action=int(root_action),
        requested_scenarios=int(requested_scenarios),
        scenario_values=raw,
        chain_score_sum=int(sum(scores)),
        chain_score_mean=_mean(scores),
        chain_count_sum=int(sum(counts)),
        chain_count_mean=_mean(counts),
        support=sum(int(value.max_chain_count > 0) for value in evaluated),
        worst_chain_score=int(min(scores, default=0.0)),
        worst_chain_count=int(min(counts, default=0.0)),
        chain_score_dispersion=_dispersion(scores),
        chain_count_dispersion=_dispersion(counts),
        continuation_score_mean=(None if not continuations else _mean(continuations)),
        max_chain_score=int(max(scores, default=0.0)),
        max_chain_count=int(max(counts, default=0.0)),
        best_fire=max(fires, key=lambda value: value.rank_key, default=None),
    )


def _survivor_sort_key(node: LongHorizonNode) -> tuple[Any, ...]:
    return (
        -float(node.evaluator_score),
        int(node.root_action),
        node.state_fingerprint,
        int(node.last_action),
        tuple(int(action) for action in node.path),
    )


def _evaluation_score(result: Any) -> float:
    value = getattr(result, "score", None)
    return float("-inf") if value is None else float(value)


def _should_stop_fire(config: LongHorizonSearchConfig, chain_count: int) -> bool:
    return (
        config.terminal_fire_rule == TERMINAL_FIRE_RECORD_AND_STOP
        and int(chain_count) >= config.terminal_fire_chain_count
    )


def _terminal_reason(config: LongHorizonSearchConfig) -> str:
    return f"chain_count_gte_{int(config.terminal_fire_chain_count)}"


def _evaluate_node(
    evaluator: CompactNodeEvaluator,
    *,
    state: CompactSearchState,
    parent: Any | None,
    action: Any,
    config: LongHorizonSearchConfig,
) -> Any:
    return evaluator.evaluate(
        state,
        parent=parent,
        action=ChainStructureAction.from_result(action),
        target_chain_count=config.minimum_chain_count,
    )


def _new_node(
    *,
    result: Any,
    root_action: int,
    scenario_id: int,
    path: tuple[int, ...],
    evaluation: Any | None,
    cumulative_action_score: int,
) -> LongHorizonNode:
    return LongHorizonNode(
        state=result.state,
        root_action=int(root_action),
        scenario_id=int(scenario_id),
        pair_cursor=len(path),
        path=path,
        evaluator_score=(
            float("-inf") if evaluation is None else _evaluation_score(evaluation)
        ),
        evaluator_result=evaluation,
        cumulative_action_score=int(cumulative_action_score),
        last_action=int(path[-1]),
    )


def _prune_survivors(
    nodes: Sequence[LongHorizonNode],
    *,
    width: int,
    trackers: Mapping[int, _ScenarioTracker],
    counters: LongHorizonSearchCounters,
) -> list[LongHorizonNode]:
    ranked = sorted(nodes, key=_survivor_sort_key)
    retained = ranked[:width]
    for node in ranked[width:]:
        trackers[node.root_action].pruned_nodes += 1
        counters.pruned_nodes += 1
    for node in retained:
        trackers[node.root_action].record_survivor(node)
    return retained


def _consume_budget(
    config: LongHorizonSearchConfig,
    counters: LongHorizonSearchCounters,
) -> bool:
    if counters.expanded_nodes >= config.max_expanded_nodes:
        counters.budget_exhausted = True
        return False
    counters.expanded_nodes += 1
    return True


def run_long_horizon_search(
    simulator: HeadlessPuyoSimulator,
    config: LongHorizonSearchConfig,
    *,
    evaluator: CompactNodeEvaluator | None = None,
) -> LongHorizonSearchResult:
    """Run compact expected-chain search without simulator clones."""

    selected_evaluator = evaluator or ChainStructureEvaluator()
    root_state = CompactSearchState.from_simulator(simulator)
    root_evaluation = selected_evaluator.evaluate(
        root_state,
        target_chain_count=config.minimum_chain_count,
    )
    sequences = build_scenario_sequences(
        simulator,
        scenarios=config.scenarios,
        depth=config.depth,
        scenario_seed=config.scenario_seed,
    )
    roots = legal_action_indices(root_state)
    counters = LongHorizonSearchCounters()
    all_trackers: dict[tuple[int, int], _ScenarioTracker] = {}

    for sequence in sequences:
        trackers = {
            action: _ScenarioTracker(
                root_action=int(action),
                scenario_id=int(sequence.scenario_id),
                terminal_fire_rule=config.terminal_fire_rule,
                terminal_fire_chain_count=config.terminal_fire_chain_count,
            )
            for action in roots
        }
        all_trackers.update(
            {
                (action, sequence.scenario_id): tracker
                for action, tracker in trackers.items()
            }
        )
        if counters.budget_exhausted:
            continue

        root_candidates: list[LongHorizonNode] = []
        root_pair = sequence.pair_at(0)
        for action in roots:
            tracker = trackers[action]
            if not _consume_budget(config, counters):
                break
            tracker.evaluated = True
            tracker.expanded_nodes += 1
            tracker.reached_depth = 1
            result = transition(root_state, root_pair, action)
            if not result.valid:
                counters.invalid_nodes += 1
                continue
            counters.generated_nodes += 1
            counters.reached_depth = max(counters.reached_depth, 1)
            path = (int(action),)
            terminal = _should_stop_fire(config, result.chain_count)
            reason = _terminal_reason(config) if terminal else None
            if result.chain_count > 0:
                tracker.record_fire(
                    result=result,
                    path=path,
                    terminal=terminal,
                    terminal_reason=reason,
                )
            if terminal:
                counters.terminal_fire_nodes += 1
                terminal_node = _new_node(
                    result=result,
                    root_action=action,
                    scenario_id=sequence.scenario_id,
                    path=path,
                    evaluation=None,
                    cumulative_action_score=result.score_delta,
                )
                tracker.record_terminal(terminal_node)
                continue
            if result.game_over:
                counters.game_over_nodes += 1
                continue
            evaluation = _evaluate_node(
                selected_evaluator,
                state=result.state,
                parent=root_evaluation,
                action=result,
                config=config,
            )
            counters.evaluated_nodes += 1
            root_candidates.append(
                _new_node(
                    result=result,
                    root_action=action,
                    scenario_id=sequence.scenario_id,
                    path=path,
                    evaluation=evaluation,
                    cumulative_action_score=result.score_delta,
                )
            )

        beam = _prune_survivors(
            root_candidates,
            width=config.width,
            trackers=trackers,
            counters=counters,
        )
        for depth in range(2, config.depth + 1):
            if counters.budget_exhausted or not beam:
                break
            pair = sequence.pair_at(depth - 1)
            candidates: list[LongHorizonNode] = []
            transpositions: dict[
                tuple[int, CompactTranspositionKey], LongHorizonNode
            ] = {}
            for node in beam:
                for action in legal_action_indices(node.state):
                    if not _consume_budget(config, counters):
                        break
                    tracker = trackers[node.root_action]
                    tracker.expanded_nodes += 1
                    tracker.reached_depth = max(tracker.reached_depth, depth)
                    result = transition(node.state, pair, action)
                    if not result.valid:
                        counters.invalid_nodes += 1
                        continue
                    counters.generated_nodes += 1
                    counters.reached_depth = max(counters.reached_depth, depth)
                    path = node.path + (int(action),)
                    terminal = _should_stop_fire(config, result.chain_count)
                    reason = _terminal_reason(config) if terminal else None
                    if result.chain_count > 0:
                        tracker.record_fire(
                            result=result,
                            path=path,
                            terminal=terminal,
                            terminal_reason=reason,
                        )
                    if terminal:
                        counters.terminal_fire_nodes += 1
                        terminal_node = _new_node(
                            result=result,
                            root_action=node.root_action,
                            scenario_id=sequence.scenario_id,
                            path=path,
                            evaluation=None,
                            cumulative_action_score=(
                                node.cumulative_action_score + result.score_delta
                            ),
                        )
                        tracker.record_terminal(terminal_node)
                        continue
                    if result.game_over:
                        counters.game_over_nodes += 1
                        continue
                    evaluation = _evaluate_node(
                        selected_evaluator,
                        state=result.state,
                        parent=node.evaluator_result,
                        action=result,
                        config=config,
                    )
                    counters.evaluated_nodes += 1
                    candidate = _new_node(
                        result=result,
                        root_action=node.root_action,
                        scenario_id=sequence.scenario_id,
                        path=path,
                        evaluation=evaluation,
                        cumulative_action_score=(
                            node.cumulative_action_score + result.score_delta
                        ),
                    )
                    if not config.use_transposition_table:
                        candidates.append(candidate)
                        continue
                    key = (
                        int(node.root_action),
                        CompactTranspositionKey(
                            result.state,
                            scenario_id=sequence.scenario_id,
                            pair_cursor=depth,
                            depth=depth,
                        ),
                    )
                    previous = transpositions.get(key)
                    if previous is None:
                        transpositions[key] = candidate
                        continue
                    counters.transposition_hits += 1
                    tracker.transposition_hits += 1
                    if _survivor_sort_key(candidate) < _survivor_sort_key(previous):
                        transpositions[key] = candidate
                if counters.budget_exhausted:
                    break
            if config.use_transposition_table:
                candidates = list(transpositions.values())
            beam = _prune_survivors(
                candidates,
                width=config.width,
                trackers=trackers,
                counters=counters,
            )

    for tracker in all_trackers.values():
        tracker.finish(
            budget_exhausted=counters.budget_exhausted,
            target_depth=config.depth,
        )

    evidence = []
    representatives: dict[int, LongHorizonNode] = {}
    root_tt_hits: dict[int, int] = {}
    root_pruned: dict[int, int] = {}
    root_depth: dict[int, int] = {}
    root_scenarios: dict[int, tuple[int, ...]] = {}
    for action in roots:
        trackers = tuple(
            all_trackers[(action, sequence.scenario_id)] for sequence in sequences
        )
        scenario_values = tuple(tracker.to_evidence() for tracker in trackers)
        aggregate = aggregate_expected_chain_evidence(
            action,
            scenario_values,
            requested_scenarios=config.scenarios,
        )
        evidence.append(aggregate)
        candidates = [
            tracker.representative
            for tracker in trackers
            if tracker.representative is not None
        ]
        if aggregate.best_fire is not None:
            representative = next(
                (
                    node
                    for node in candidates
                    if node.path == aggregate.best_fire.path
                    and node.scenario_id == aggregate.best_fire.scenario_id
                ),
                None,
            )
        else:
            representative = None
        if representative is None and candidates:
            representative = min(candidates, key=_survivor_sort_key)
        if representative is not None:
            representatives[int(action)] = representative
        root_tt_hits[int(action)] = sum(
            tracker.transposition_hits for tracker in trackers
        )
        root_pruned[int(action)] = sum(tracker.pruned_nodes for tracker in trackers)
        root_depth[int(action)] = max(
            (tracker.reached_depth for tracker in trackers),
            default=0,
        )
        root_scenarios[int(action)] = tuple(
            tracker.scenario_id for tracker in trackers if tracker.evaluated
        )

    return LongHorizonSearchResult(
        root_evidence=tuple(sorted(evidence, key=lambda value: value.root_action)),
        representatives=representatives,
        scenario_sequences=sequences,
        root_evaluation=root_evaluation,
        counters=counters,
        root_transposition_hits=root_tt_hits,
        root_pruned_nodes=root_pruned,
        root_reached_depth=root_depth,
        root_generated_scenarios=root_scenarios,
    )


__all__ = [
    "EXPECTED_CHAIN_EVIDENCE_SCHEMA_VERSION",
    "EXPECTED_CHAIN_RANKING_RULE_VERSION",
    "LONG_HORIZON_PROPOSAL_DIGEST_VERSION",
    "LONG_HORIZON_PROFILE_SCHEMA_VERSION",
    "LONG_HORIZON_SEARCH_PROFILES",
    "QUALITY_D12_PROFILE",
    "QUALITY_D16_PROFILE",
    "REPRESENTATIVE_SCENARIO_BAGS",
    "RUNTIME_PROFILE",
    "SCENARIO_SEQUENCE_SCHEMA_VERSION",
    "TERMINAL_FIRE_CONTINUE",
    "TERMINAL_FIRE_RECORD_AND_STOP",
    "ChainFireEvidence",
    "ExpectedChainRootEvidence",
    "LongHorizonNode",
    "LongHorizonSearchConfig",
    "LongHorizonSearchProfile",
    "LongHorizonSearchResult",
    "ScenarioPairSequence",
    "ScenarioRootEvidence",
    "aggregate_expected_chain_evidence",
    "build_scenario_sequences",
    "compact_state_fingerprint",
    "long_horizon_proposal_digest",
    "long_horizon_profile",
    "run_long_horizon_search",
]
