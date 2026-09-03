"""Deterministic compact long-horizon expected-chain search.

This module owns the PUYO-174 search semantics.  It deliberately has no
dependency on the legacy simulator-backed beam implementation so the latter
can remain a compatibility path while quality profiles use the compact kernel.
PUYO-179 adds versioned, seeded hidden-future queues generated through the
production ``PuyoSequence`` distribution; fixed six pairings are legacy-only.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

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
from src.core.tsumo import PuyoSequence

LONG_HORIZON_PROFILE_SCHEMA_VERSION = "puyo.long_horizon_profile.v3"
EXPECTED_CHAIN_EVIDENCE_SCHEMA_VERSION = "puyo.expected_chain_evidence.v2"
EXPECTED_CHAIN_RANKING_RULE_VERSION = "puyo.expected_chain_ranking.v2"
SCENARIO_SEQUENCE_SCHEMA_VERSION = "puyo.long_horizon_scenario_sequence.v2"
FUTURE_SAMPLING_SCHEMA_VERSION = "puyo.future_tsumo_sampling.v1"
FUTURE_SAMPLING_SEEDED_AUTHORITATIVE = "seeded-authoritative"
FUTURE_SAMPLING_LEGACY_FIXED_SIX = "legacy-fixed-six"
FUTURE_SAMPLING_MODES = {
    FUTURE_SAMPLING_SEEDED_AUTHORITATIVE,
    FUTURE_SAMPLING_LEGACY_FIXED_SIX,
}
FUTURE_ROLLOUT_SEED_DERIVATION = "sha256-decision-seed-sample-index-v1"
FUTURE_QUEUE_GENERATOR = "src.core.tsumo.PuyoSequence"
LONG_HORIZON_PROPOSAL_DIGEST_VERSION = "puyo.long_horizon_proposal_digest.v1"
LONG_HORIZON_SURVIVOR_TIE_BREAK_VERSION = "puyo.long_horizon_survivor_tie_break.v2"
TERMINAL_FIRE_SCORE_VERSION = "puyo.build_main_terminal_score.v1"
ROOT_SURVIVOR_COVERAGE_SCHEMA_VERSION = "puyo.root_survivor_coverage.v1"
ROOT_BUILD_DIAGNOSTICS_SCHEMA_VERSION = "puyo.build_main_root_diagnostics.v1"

TERMINAL_FIRE_CONTINUE = "continue"
TERMINAL_FIRE_RECORD_AND_STOP = "record_and_stop"
TERMINAL_FIRE_RULES = {
    TERMINAL_FIRE_CONTINUE,
    TERMINAL_FIRE_RECORD_AND_STOP,
}

FIRE_CONTEXT_SAFE_BUILD = "safe_build"
FIRE_CONTEXT_FORCED_SAFETY = "forced_safety"
FIRE_CONTEXTS = {
    FIRE_CONTEXT_SAFE_BUILD,
    FIRE_CONTEXT_FORCED_SAFETY,
}

FIRE_CLASS_UNAVAILABLE = "unavailable"
FIRE_CLASS_PREMATURE = "premature_fire"
FIRE_CLASS_QUIET = "quiet_continuation"
FIRE_CLASS_FORCED_SAFETY = "forced_safety_fire"
FIRE_CLASS_TARGET = "target_fire"
FIRE_CLASS_WINNING = "winning_fire"
FIRE_CLASSES = {
    FIRE_CLASS_UNAVAILABLE,
    FIRE_CLASS_PREMATURE,
    FIRE_CLASS_QUIET,
    FIRE_CLASS_FORCED_SAFETY,
    FIRE_CLASS_TARGET,
    FIRE_CLASS_WINNING,
}
ALLOWED_FIRE_CLASSES = {
    FIRE_CLASS_FORCED_SAFETY,
    FIRE_CLASS_TARGET,
    FIRE_CLASS_WINNING,
}
FIRE_CLASS_PRIORITY = {
    FIRE_CLASS_UNAVAILABLE: 0,
    FIRE_CLASS_PREMATURE: 1,
    FIRE_CLASS_QUIET: 2,
    FIRE_CLASS_FORCED_SAFETY: 3,
    FIRE_CLASS_TARGET: 4,
    FIRE_CLASS_WINNING: 5,
}

RUNTIME_PROFILE = "runtime"
SMOKE_PROFILE = "smoke"
QUALITY_D12_PROFILE = "quality-d12"
QUALITY_D16_PROFILE = "quality-d16"
LEGACY_FIXED_SIX_PROFILE = FUTURE_SAMPLING_LEGACY_FIXED_SIX

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


def _stable_seed(value: Any) -> int:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")


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
    future_sampling_mode: str = FUTURE_SAMPLING_SEEDED_AUTHORITATIVE
    terminal_fire_rule: str = TERMINAL_FIRE_RECORD_AND_STOP
    terminal_fire_chain_count: int = 1
    root_survivor_quota: int = 1
    fire_context: str = FIRE_CONTEXT_SAFE_BUILD
    premature_target_gap_penalty: float = 1_000.0
    winning_score_threshold: int | None = None
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
                self.root_survivor_quota,
            )
            <= 0
        ):
            raise ValueError("long-horizon profile budgets must be positive")
        if self.scenarios > len(REPRESENTATIVE_SCENARIO_BAGS):
            raise ValueError("long-horizon profile requests too many future samples")
        if self.future_sampling_mode not in FUTURE_SAMPLING_MODES:
            raise ValueError(
                f"unsupported future sampling mode: {self.future_sampling_mode}"
            )
        if self.terminal_fire_rule not in TERMINAL_FIRE_RULES:
            raise ValueError(
                f"unsupported terminal-fire rule: {self.terminal_fire_rule}"
            )
        if self.fire_context not in FIRE_CONTEXTS:
            raise ValueError(f"unsupported fire context: {self.fire_context}")
        if (
            not math.isfinite(self.premature_target_gap_penalty)
            or self.premature_target_gap_penalty < 0.0
        ):
            raise ValueError(
                "premature target-gap penalty must be finite and non-negative"
            )
        if (
            self.winning_score_threshold is not None
            and self.winning_score_threshold <= 0
        ):
            raise ValueError("winning score threshold must be positive when configured")
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
            "future_sampling": {
                "schema_version": FUTURE_SAMPLING_SCHEMA_VERSION,
                "mode": self.future_sampling_mode,
                "sample_count": int(self.scenarios),
                "known_queue": "current_plus_next2",
                "rollout_seed_derivation": (
                    FUTURE_ROLLOUT_SEED_DERIVATION
                    if self.future_sampling_mode == FUTURE_SAMPLING_SEEDED_AUTHORITATIVE
                    else None
                ),
                "generator": (
                    FUTURE_QUEUE_GENERATOR
                    if self.future_sampling_mode == FUTURE_SAMPLING_SEEDED_AUTHORITATIVE
                    else "ama-representative-two-pair-cycle"
                ),
                "unknown_pairs_per_sample": max(0, int(self.depth) - 3),
            },
            "terminal_fire": {
                "rule": self.terminal_fire_rule,
                "minimum_chain_count": int(self.terminal_fire_chain_count),
            },
            "fire_semantics": {
                "context": self.fire_context,
                "target_chain_source": "objective.target_chain",
                "winning_score_threshold": self.winning_score_threshold,
                "terminal_score_version": TERMINAL_FIRE_SCORE_VERSION,
                "premature_target_gap_penalty": float(
                    self.premature_target_gap_penalty
                ),
            },
            "root_survivor_quota": int(self.root_survivor_quota),
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
        version="3.0",
        depth=4,
        width=24,
        scenarios=2,
        max_expanded_nodes=4_096,
        budget_authority="external_runtime_deadline",
        wall_clock_mode="external_deadline_contract",
    ),
    SMOKE_PROFILE: LongHorizonSearchProfile(
        name=SMOKE_PROFILE,
        version="3.0",
        depth=6,
        width=48,
        scenarios=3,
        max_expanded_nodes=30_000,
    ),
    QUALITY_D12_PROFILE: LongHorizonSearchProfile(
        name=QUALITY_D12_PROFILE,
        version="3.0",
        depth=12,
        width=128,
        scenarios=6,
        max_expanded_nodes=200_000,
    ),
    QUALITY_D16_PROFILE: LongHorizonSearchProfile(
        name=QUALITY_D16_PROFILE,
        version="3.0",
        depth=16,
        width=250,
        scenarios=6,
        max_expanded_nodes=600_000,
    ),
    LEGACY_FIXED_SIX_PROFILE: LongHorizonSearchProfile(
        name=LEGACY_FIXED_SIX_PROFILE,
        version="1.0",
        depth=16,
        width=250,
        scenarios=6,
        max_expanded_nodes=600_000,
        future_sampling_mode=FUTURE_SAMPLING_LEGACY_FIXED_SIX,
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
    """Known current/NEXT2 followed by one versioned hidden-future sample."""

    scenario_id: int
    known_pairs: tuple[tuple[PuyoColor, PuyoColor], ...]
    hidden_pairs: tuple[tuple[PuyoColor, PuyoColor], ...]
    depth: int
    sampling_mode: str
    sample_index: int
    sample_id: str
    decision_seed: int
    decision_seed_source: str
    rollout_seed: int | None
    repeats_hidden_pairs: bool = False
    schema_version: str = SCENARIO_SEQUENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCENARIO_SEQUENCE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported scenario sequence schema: {self.schema_version}"
            )
        if not 0 <= self.scenario_id < len(REPRESENTATIVE_SCENARIO_BAGS):
            raise ValueError("scenario id is outside the representative set")
        if self.sample_index < 0 or self.depth <= 0:
            raise ValueError("scenario sample index and depth must be non-negative")
        if not self.known_pairs:
            raise ValueError("scenario sequence requires the known current/NEXT queue")
        if self.sampling_mode not in FUTURE_SAMPLING_MODES:
            raise ValueError(f"unsupported future sampling mode: {self.sampling_mode}")
        if not self.sample_id or not self.decision_seed_source:
            raise ValueError("scenario sequence requires sample and seed provenance")
        if self.sampling_mode == FUTURE_SAMPLING_SEEDED_AUTHORITATIVE:
            if self.rollout_seed is None:
                raise ValueError("seeded future samples require a rollout seed")
            if self.repeats_hidden_pairs:
                raise ValueError("seeded future samples must not repeat a hidden cycle")
            if self.depth > self.known_pair_count + len(self.hidden_pairs):
                raise ValueError("seeded future sample is shorter than search depth")
        elif not self.repeats_hidden_pairs or not self.hidden_pairs:
            raise ValueError(
                "legacy fixed-six samples require a repeating hidden cycle"
            )
        for pair in (*self.known_pairs, *self.hidden_pairs):
            _pair_colors(pair)

    @property
    def known_pair_count(self) -> int:
        return len(self.known_pairs)

    def pair_at(self, pair_cursor: int) -> tuple[PuyoColor, PuyoColor]:
        if pair_cursor < 0:
            raise ValueError("scenario pair cursor must be non-negative")
        if pair_cursor < self.known_pair_count:
            return self.known_pairs[pair_cursor]
        hidden_cursor = pair_cursor - self.known_pair_count
        if hidden_cursor < len(self.hidden_pairs):
            return self.hidden_pairs[hidden_cursor]
        if self.repeats_hidden_pairs:
            return self.hidden_pairs[hidden_cursor % len(self.hidden_pairs)]
        raise IndexError("scenario pair cursor exceeds the sampled future queue")

    @property
    def pairs(self) -> tuple[tuple[PuyoColor, PuyoColor], ...]:
        return tuple(self.pair_at(cursor) for cursor in range(self.depth))

    @property
    def queue_digest(self) -> str:
        return _stable_digest(
            {
                "schema_version": FUTURE_SAMPLING_SCHEMA_VERSION,
                "known_pair_count": self.known_pair_count,
                "pairs": [_pair_payload(pair) for pair in self.pairs],
            },
            prefix="future-queue",
        )

    @property
    def sequence_digest(self) -> str:
        return _stable_digest(
            {
                "schema_version": self.schema_version,
                "scenario_id": self.scenario_id,
                "sampling_mode": self.sampling_mode,
                "sample_index": self.sample_index,
                "sample_id": self.sample_id,
                "decision_seed": self.decision_seed,
                "rollout_seed": self.rollout_seed,
                "known_pair_count": self.known_pair_count,
                "queue_digest": self.queue_digest,
            },
            prefix="scenario-sequence",
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "scenario_id": int(self.scenario_id),
            "sample_id": self.sample_id,
            "sample_index": int(self.sample_index),
            "sampling": {
                "schema_version": FUTURE_SAMPLING_SCHEMA_VERSION,
                "mode": self.sampling_mode,
                "generator": (
                    FUTURE_QUEUE_GENERATOR
                    if self.sampling_mode == FUTURE_SAMPLING_SEEDED_AUTHORITATIVE
                    else "ama-representative-two-pair-cycle"
                ),
                "distribution": (
                    "independent_uniform_normal_colors"
                    if self.sampling_mode == FUTURE_SAMPLING_SEEDED_AUTHORITATIVE
                    else "fixed_representative_pairing"
                ),
                "decision_seed": int(self.decision_seed),
                "decision_seed_source": self.decision_seed_source,
                "rollout_seed": self.rollout_seed,
                "rollout_seed_derivation": (
                    FUTURE_ROLLOUT_SEED_DERIVATION
                    if self.rollout_seed is not None
                    else None
                ),
                "repeat_policy": (
                    "two_pair_cycle" if self.repeats_hidden_pairs else "none"
                ),
            },
            "known_pair_count": int(self.known_pair_count),
            "unknown_boundary_cursor": int(self.known_pair_count),
            "hidden_pair_count": len(self.hidden_pairs),
            "hidden_pairs": [_pair_payload(pair) for pair in self.hidden_pairs],
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
            "queue_digest": self.queue_digest,
            "sequence_digest": self.sequence_digest,
        }
        if self.repeats_hidden_pairs:
            payload["hidden_cycle"] = [
                _pair_payload(pair) for pair in self.hidden_pairs
            ]
        return payload


def _known_scenario_pairs(
    simulator: HeadlessPuyoSimulator,
) -> tuple[tuple[PuyoColor, PuyoColor], ...]:
    game = simulator.game
    current = (game.current_puyo_1, game.current_puyo_2)
    if any(item is None for item in current):
        raise ValueError("long-horizon search requires an active current pair")
    known_pairs = (_pair_colors(current),) + tuple(
        _pair_colors(pair) for pair in tuple(game.next_puyo_queue)[:2]
    )
    if len(known_pairs) < 3:
        raise ValueError("long-horizon search requires current + NEXT2")
    return known_pairs


def _resolve_decision_seed(
    simulator: HeadlessPuyoSimulator,
    *,
    decision_seed: int | None,
    known_pairs: Sequence[Sequence[PuyoColor]],
) -> tuple[int, str]:
    if decision_seed is not None:
        return int(decision_seed), "explicit"
    sequence_seed = getattr(simulator.game.puyo_sequence, "seed", None)
    if not isinstance(sequence_seed, (str, int, float, bool, type(None))):
        sequence_seed = repr(sequence_seed)
    return (
        _stable_seed(
            {
                "schema_version": FUTURE_SAMPLING_SCHEMA_VERSION,
                "source": "simulator_decision_state",
                "simulator_sequence_seed": sequence_seed,
                "state_fingerprint": compact_state_fingerprint(
                    CompactSearchState.from_simulator(simulator)
                ),
                "known_pairs": [_pair_payload(pair) for pair in known_pairs],
            }
        ),
        "derived_from_simulator_decision_state",
    )


def _rollout_seed(decision_seed: int, sample_index: int) -> int:
    return _stable_seed(
        {
            "schema_version": FUTURE_SAMPLING_SCHEMA_VERSION,
            "derivation": FUTURE_ROLLOUT_SEED_DERIVATION,
            "decision_seed": int(decision_seed),
            "sample_index": int(sample_index),
        }
    )


def _authoritative_hidden_pairs(
    *,
    rollout_seed: int,
    count: int,
) -> tuple[tuple[PuyoColor, PuyoColor], ...]:
    sequence = PuyoSequence(seed=int(rollout_seed))
    return tuple(_pair_colors(pair) for pair in sequence.next_pairs(int(count)))


def build_scenario_sequences(
    simulator: HeadlessPuyoSimulator,
    *,
    scenarios: int,
    depth: int,
    scenario_seed: int | None = None,
    decision_seed: int | None = None,
    sampling_mode: str = FUTURE_SAMPLING_SEEDED_AUTHORITATIVE,
) -> tuple[ScenarioPairSequence, ...]:
    if not 1 <= scenarios <= len(REPRESENTATIVE_SCENARIO_BAGS):
        raise ValueError("future sample count is outside the supported set")
    if depth <= 0:
        raise ValueError("future sample depth must be positive")
    if sampling_mode not in FUTURE_SAMPLING_MODES:
        raise ValueError(f"unsupported future sampling mode: {sampling_mode}")
    if (
        scenario_seed is not None
        and decision_seed is not None
        and int(scenario_seed) != int(decision_seed)
    ):
        raise ValueError("scenario_seed and decision_seed must agree when both are set")
    known_pairs = _known_scenario_pairs(simulator)
    configured_seed = decision_seed if decision_seed is not None else scenario_seed
    resolved_seed, seed_source = _resolve_decision_seed(
        simulator,
        decision_seed=configured_seed,
        known_pairs=known_pairs,
    )
    return build_scenario_sequences_from_known_pairs(
        known_pairs,
        scenarios=scenarios,
        depth=depth,
        decision_seed=resolved_seed,
        sampling_mode=sampling_mode,
        decision_seed_source=seed_source,
    )


def build_scenario_sequences_from_known_pairs(
    known_pairs: Sequence[Sequence[Any]],
    *,
    scenarios: int,
    depth: int,
    scenario_seed: int | None = None,
    decision_seed: int | None = None,
    sampling_mode: str = FUTURE_SAMPLING_SEEDED_AUTHORITATIVE,
    decision_seed_source: str | None = None,
) -> tuple[ScenarioPairSequence, ...]:
    """Complete a visible queue without requiring a simulator or private queue."""

    if not 1 <= scenarios <= len(REPRESENTATIVE_SCENARIO_BAGS):
        raise ValueError("future sample count is outside the supported set")
    if depth <= 0:
        raise ValueError("future sample depth must be positive")
    if sampling_mode not in FUTURE_SAMPLING_MODES:
        raise ValueError(f"unsupported future sampling mode: {sampling_mode}")
    if (
        scenario_seed is not None
        and decision_seed is not None
        and int(scenario_seed) != int(decision_seed)
    ):
        raise ValueError("scenario_seed and decision_seed must agree when both are set")
    known = tuple(_pair_colors(pair) for pair in known_pairs)
    if not known:
        raise ValueError("known queue must contain at least one pair")
    configured_seed = decision_seed if decision_seed is not None else scenario_seed
    resolved_seed = 0 if configured_seed is None else int(configured_seed)
    seed_source = decision_seed_source or (
        "explicit" if configured_seed is not None else "visible_observation_default"
    )
    hidden_pair_count = max(0, int(depth) - len(known))

    if sampling_mode == FUTURE_SAMPLING_SEEDED_AUTHORITATIVE:
        result = []
        for sample_index in range(scenarios):
            rollout_seed = _rollout_seed(resolved_seed, sample_index)
            hidden_pairs = _authoritative_hidden_pairs(
                rollout_seed=rollout_seed, count=hidden_pair_count
            )
            sample_id = _stable_digest(
                {
                    "schema_version": FUTURE_SAMPLING_SCHEMA_VERSION,
                    "decision_seed": resolved_seed,
                    "rollout_seed": rollout_seed,
                    "sample_index": sample_index,
                },
                prefix="future-sample",
            )
            result.append(
                ScenarioPairSequence(
                    scenario_id=sample_index,
                    known_pairs=known,
                    hidden_pairs=hidden_pairs,
                    depth=depth,
                    sampling_mode=sampling_mode,
                    sample_index=sample_index,
                    sample_id=sample_id,
                    decision_seed=resolved_seed,
                    decision_seed_source=seed_source,
                    rollout_seed=rollout_seed,
                )
            )
        return tuple(result)

    scenario_ids = list(range(len(REPRESENTATIVE_SCENARIO_BAGS)))
    color_orders = [tuple(NORMAL_PUYO_COLORS)] * scenarios
    if configured_seed is not None:
        rng = random.Random(int(configured_seed))
        rng.shuffle(scenario_ids)
        color_orders = []
        for _ in range(scenarios):
            colors = list(NORMAL_PUYO_COLORS)
            rng.shuffle(colors)
            color_orders.append(tuple(colors))
    result = []
    for sample_index, (scenario_id, colors) in enumerate(
        zip(scenario_ids[:scenarios], color_orders)
    ):
        bag = REPRESENTATIVE_SCENARIO_BAGS[scenario_id]
        hidden_pairs = (
            (colors[bag[0]], colors[bag[1]]),
            (colors[bag[2]], colors[bag[3]]),
        )
        result.append(
            ScenarioPairSequence(
                scenario_id=scenario_id,
                known_pairs=known,
                hidden_pairs=hidden_pairs,
                depth=depth,
                sampling_mode=sampling_mode,
                sample_index=sample_index,
                sample_id=f"legacy-fixed-six-{scenario_id}",
                decision_seed=resolved_seed,
                decision_seed_source=seed_source,
                rollout_seed=None,
                repeats_hidden_pairs=True,
            )
        )
    return tuple(result)


def classify_build_main_fire(
    *,
    chain_count: int,
    chain_score: int,
    target_chain_count: int,
    fire_context: str = FIRE_CONTEXT_SAFE_BUILD,
    winning_score_threshold: int | None = None,
) -> str:
    """Classify a fire without letting sub-target official score imply success."""

    if fire_context not in FIRE_CONTEXTS:
        raise ValueError(f"unsupported fire context: {fire_context}")
    if chain_count <= 0:
        return FIRE_CLASS_QUIET
    if winning_score_threshold is not None and int(chain_score) >= int(
        winning_score_threshold
    ):
        return FIRE_CLASS_WINNING
    if int(chain_count) >= int(target_chain_count):
        return FIRE_CLASS_TARGET
    if fire_context == FIRE_CONTEXT_FORCED_SAFETY:
        return FIRE_CLASS_FORCED_SAFETY
    return FIRE_CLASS_PREMATURE


def _mapping_from(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        return dict(payload) if isinstance(payload, Mapping) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def _terminal_fire_score(
    *,
    result: Any,
    evaluation: Any,
    fire_class: str,
    config: LongHorizonSearchConfig,
) -> tuple[float, dict[str, float]]:
    structural_score = _evaluation_score(evaluation)
    if not math.isfinite(structural_score):
        structural_score = -1_000_000_000_000.0
    target_gap = max(0, int(config.minimum_chain_count) - int(result.chain_count))
    target_gap_penalty = (
        -float(config.premature_target_gap_penalty) * float(target_gap)
        if fire_class == FIRE_CLASS_PREMATURE
        else 0.0
    )
    official_score = (
        float(result.score_delta) if fire_class in ALLOWED_FIRE_CLASSES else 0.0
    )
    total = structural_score + target_gap_penalty + official_score
    return total, {
        "structural_score": float(structural_score),
        "official_score": float(official_score),
        "target_chain_gap": float(target_gap),
        "target_gap_penalty": float(target_gap_penalty),
        "total": float(total),
    }


def _evaluation_fire_details(evaluation: Any) -> dict[str, Any]:
    action = getattr(evaluation, "action_features", None)
    features = getattr(evaluation, "features", None)
    score_breakdown = _mapping_from(getattr(evaluation, "score_breakdown", None))
    trigger_damage = max(0, int(getattr(action, "trigger_damage", 0)))
    return {
        "evaluation_status": str(getattr(evaluation, "evaluation_status", "available")),
        "danger": float(getattr(evaluation, "danger", 1.0)),
        "trigger_damage": trigger_damage,
        "trigger_preserved": bool(trigger_damage == 0),
        "structural_potential": {
            "chain_count": (
                None
                if features is None
                else int(getattr(features, "potential_chain_count", 0))
            ),
            "chain_score": (
                None
                if features is None
                else int(getattr(features, "potential_chain_score", 0))
            ),
            "required_key_count": (
                None
                if features is None
                else getattr(features, "required_key_count", None)
            ),
        },
        "score_breakdown": {
            str(key): float(value)
            for key, value in score_breakdown.items()
            if isinstance(value, (int, float)) and math.isfinite(float(value))
        },
    }


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
    fire_class: str = FIRE_CLASS_PREMATURE
    target_chain_count: int = 1
    target_chain_gap: int = 0
    allowed: bool = False
    terminal_score: float | None = None
    terminal_score_breakdown: Mapping[str, float] = field(default_factory=dict)
    terminal_evaluation: Mapping[str, Any] = field(default_factory=dict)

    @property
    def rank_key(self) -> tuple[Any, ...]:
        return (
            int(FIRE_CLASS_PRIORITY.get(self.fire_class, -1)),
            (
                float("-inf")
                if self.terminal_score is None
                else float(self.terminal_score)
            ),
            int(self.chain_score),
            int(self.chain_count),
            -int(self.depth),
            -int(self.trigger_action),
            self.state_fingerprint,
            tuple(-int(action) for action in self.path),
        )

    @property
    def official_rank_key(self) -> tuple[Any, ...]:
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
            "fire_class": self.fire_class,
            "allowed": bool(self.allowed),
            "target_chain_count": int(self.target_chain_count),
            "target_chain_gap": int(self.target_chain_gap),
            "terminal_score": {
                "schema_version": TERMINAL_FIRE_SCORE_VERSION,
                "value": self.terminal_score,
                "breakdown": {
                    str(key): float(value)
                    for key, value in self.terminal_score_breakdown.items()
                },
                "evaluation": dict(self.terminal_evaluation),
            },
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
    selected_fire_class: str = FIRE_CLASS_UNAVAILABLE
    selected_fire: ChainFireEvidence | None = None
    observed_fire_classes: tuple[str, ...] = ()
    quiet_survivor: bool = False
    survivor_quota: int = 1
    survivor_candidate_counts: tuple[tuple[int, int], ...] = ()
    survivor_counts: tuple[tuple[int, int], ...] = ()
    survivor_shortfalls: tuple[tuple[int, str], ...] = ()
    invalid_nodes: int = 0
    game_over_nodes: int = 0

    def to_dict(self) -> dict[str, Any]:
        candidate_counts = {
            str(depth): int(count) for depth, count in self.survivor_candidate_counts
        }
        retained_counts = {
            str(depth): int(count) for depth, count in self.survivor_counts
        }
        shortfalls = {str(depth): reason for depth, reason in self.survivor_shortfalls}
        attempted_depths = sorted(
            {
                *candidate_counts,
                *retained_counts,
                *shortfalls,
            },
            key=int,
        )
        return {
            "root_action": int(self.root_action),
            "scenario_id": int(self.scenario_id),
            "evaluated": bool(self.evaluated),
            "search_complete": bool(self.search_complete),
            "reached_depth": int(self.reached_depth),
            "max_chain_count": int(self.max_chain_count),
            "max_chain_score": int(self.max_chain_score),
            "best_fire": (None if self.best_fire is None else self.best_fire.to_dict()),
            "selected_fire_class": self.selected_fire_class,
            "selected_fire": (
                None if self.selected_fire is None else self.selected_fire.to_dict()
            ),
            "observed_fire_classes": list(self.observed_fire_classes),
            "fire_count": int(self.fire_count),
            "terminal_fire_count": int(self.terminal_fire_count),
            "survivor_evaluator_score": self.survivor_evaluator_score,
            "quiet_survivor": bool(self.quiet_survivor),
            "survivor_coverage": {
                "schema_version": ROOT_SURVIVOR_COVERAGE_SCHEMA_VERSION,
                "quota": int(self.survivor_quota),
                "attempted_depths": [int(depth) for depth in attempted_depths],
                "candidate_counts": candidate_counts,
                "retained_counts": retained_counts,
                "shortfalls": shortfalls,
                "quota_satisfied": bool(attempted_depths and not shortfalls),
            },
            "expanded_nodes": int(self.expanded_nodes),
            "pruned_nodes": int(self.pruned_nodes),
            "transposition_hits": int(self.transposition_hits),
            "invalid_nodes": int(self.invalid_nodes),
            "game_over_nodes": int(self.game_over_nodes),
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
    fire_class: str = FIRE_CLASS_UNAVAILABLE
    fire_class_support: Mapping[str, int] = field(default_factory=dict)
    terminal_score_sum: float = 0.0
    terminal_score_mean: float | None = None
    terminal_score_worst: float | None = None
    terminal_score_dispersion: float = 0.0
    quiet_support: int = 0
    target_not_reached_fire_count: int = 0
    root_survivor_quota: int = 1
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
        if self.fire_class == FIRE_CLASS_QUIET:
            return float(
                -1_000_000_000_000.0
                if self.continuation_score_mean is None
                else self.continuation_score_mean
            )
        if self.fire_class in FIRE_CLASSES - {
            FIRE_CLASS_UNAVAILABLE,
            FIRE_CLASS_QUIET,
        }:
            return float(
                -1_000_000_000_000.0
                if self.terminal_score_mean is None
                else self.terminal_score_sum
            )
        return -1_000_000_000_000.0

    @property
    def ranking_key(self) -> tuple[Any, ...]:
        class_support = int(self.fire_class_support.get(self.fire_class, 0))
        return (
            int(FIRE_CLASS_PRIORITY.get(self.fire_class, -1)),
            float(self.coverage),
            class_support,
            float(self.candidate_value),
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
            int(self.quiet_support),
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
            "fire_class_priority": float(FIRE_CLASS_PRIORITY.get(self.fire_class, -1)),
            "fire_class_support": float(
                self.fire_class_support.get(self.fire_class, 0)
            ),
            "terminal_score_sum": float(self.terminal_score_sum),
            "terminal_score_mean": float(
                0.0 if self.terminal_score_mean is None else self.terminal_score_mean
            ),
            "quiet_candidate_coverage": (
                0.0
                if self.requested_scenarios <= 0
                else self.quiet_support / float(self.requested_scenarios)
            ),
            "target_not_reached_fire_count": float(self.target_not_reached_fire_count),
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
            "fire_class": self.fire_class,
            "fire_class_priority": int(FIRE_CLASS_PRIORITY.get(self.fire_class, -1)),
            "fire_class_support": {
                fire_class: int(self.fire_class_support.get(fire_class, 0))
                for fire_class in sorted(FIRE_CLASSES)
            },
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
            "quiet_candidate_coverage": (
                0.0
                if self.requested_scenarios <= 0
                else self.quiet_support / float(self.requested_scenarios)
            ),
            "quiet_support": int(self.quiet_support),
            "target_not_reached_fire_count": int(self.target_not_reached_fire_count),
            "terminal_score": {
                "schema_version": TERMINAL_FIRE_SCORE_VERSION,
                "sum": float(self.terminal_score_sum),
                "mean": self.terminal_score_mean,
                "worst": self.terminal_score_worst,
                "dispersion": float(self.terminal_score_dispersion),
            },
            "root_survivor_quota": int(self.root_survivor_quota),
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
    decision_seed: int | None = None
    future_sampling_mode: str = FUTURE_SAMPLING_SEEDED_AUTHORITATIVE
    terminal_fire_rule: str = TERMINAL_FIRE_RECORD_AND_STOP
    terminal_fire_chain_count: int = 1
    root_survivor_quota: int = 1
    fire_context: str = FIRE_CONTEXT_SAFE_BUILD
    premature_target_gap_penalty: float = 1_000.0
    winning_score_threshold: int | None = None
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
                self.root_survivor_quota,
            )
            <= 0
        ):
            raise ValueError("long-horizon search values must be positive")
        if self.scenarios > len(REPRESENTATIVE_SCENARIO_BAGS):
            raise ValueError("long-horizon search requests too many future samples")
        if self.future_sampling_mode not in FUTURE_SAMPLING_MODES:
            raise ValueError(
                f"unsupported future sampling mode: {self.future_sampling_mode}"
            )
        if (
            self.scenario_seed is not None
            and self.decision_seed is not None
            and int(self.scenario_seed) != int(self.decision_seed)
        ):
            raise ValueError(
                "scenario_seed and decision_seed must agree when both are set"
            )
        if self.terminal_fire_rule not in TERMINAL_FIRE_RULES:
            raise ValueError(
                f"unsupported terminal-fire rule: {self.terminal_fire_rule}"
            )
        if self.fire_context not in FIRE_CONTEXTS:
            raise ValueError(f"unsupported fire context: {self.fire_context}")
        if (
            not math.isfinite(self.premature_target_gap_penalty)
            or self.premature_target_gap_penalty < 0.0
        ):
            raise ValueError(
                "premature target-gap penalty must be finite and non-negative"
            )
        if (
            self.winning_score_threshold is not None
            and self.winning_score_threshold <= 0
        ):
            raise ValueError("winning score threshold must be positive when configured")

    @property
    def resolved_decision_seed(self) -> int | None:
        return (
            self.decision_seed if self.decision_seed is not None else self.scenario_seed
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
    root_diagnostics: Mapping[int, Mapping[str, Any]]

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
                "root_diagnostics": {
                    str(action): dict(payload)
                    for action, payload in sorted(self.root_diagnostics.items())
                },
            },
            prefix="long-horizon-search",
        )


@dataclass(slots=True)
class _ScenarioTracker:
    root_action: int
    scenario_id: int
    terminal_fire_rule: str
    terminal_fire_chain_count: int
    root_survivor_quota: int
    evaluated: bool = False
    search_complete: bool = True
    reached_depth: int = 0
    max_chain_count: int = 0
    max_chain_score: int = 0
    best_fire: ChainFireEvidence | None = None
    fires_by_class: dict[str, ChainFireEvidence] = field(default_factory=dict)
    terminals_by_class: dict[str, LongHorizonNode] = field(default_factory=dict)
    fire_count: int = 0
    terminal_fire_count: int = 0
    best_survivor: LongHorizonNode | None = None
    expanded_nodes: int = 0
    pruned_nodes: int = 0
    transposition_hits: int = 0
    invalid_nodes: int = 0
    game_over_nodes: int = 0
    survivor_candidate_counts: dict[int, int] = field(default_factory=dict)
    survivor_counts: dict[int, int] = field(default_factory=dict)
    survivor_shortfalls: dict[int, str] = field(default_factory=dict)
    truncation_reason: str | None = None

    def record_fire(
        self,
        *,
        result: Any,
        evaluation: Any,
        fire_class: str,
        terminal_score: float,
        terminal_score_breakdown: Mapping[str, float],
        target_chain_count: int,
        path: tuple[int, ...],
        terminal: bool,
        terminal_reason: str | None,
    ) -> ChainFireEvidence:
        details = _evaluation_fire_details(evaluation)
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
            fire_class=fire_class,
            target_chain_count=int(target_chain_count),
            target_chain_gap=max(
                0,
                int(target_chain_count) - int(result.chain_count),
            ),
            allowed=fire_class in ALLOWED_FIRE_CLASSES,
            terminal_score=float(terminal_score),
            terminal_score_breakdown=dict(terminal_score_breakdown),
            terminal_evaluation=details,
        )
        self.fire_count += 1
        self.terminal_fire_count += int(terminal)
        self.max_chain_count = max(self.max_chain_count, evidence.chain_count)
        self.max_chain_score = max(self.max_chain_score, evidence.chain_score)
        if (
            self.best_fire is None
            or evidence.official_rank_key > self.best_fire.official_rank_key
        ):
            self.best_fire = evidence
        best_class_fire = self.fires_by_class.get(fire_class)
        if best_class_fire is None or evidence.rank_key > best_class_fire.rank_key:
            self.fires_by_class[fire_class] = evidence
        return evidence

    def record_survivor(self, node: LongHorizonNode) -> None:
        if self.best_survivor is None or _survivor_sort_key(node) < _survivor_sort_key(
            self.best_survivor
        ):
            self.best_survivor = node
        self.reached_depth = max(self.reached_depth, len(node.path))

    def record_terminal(self, node: LongHorizonNode, fire: ChainFireEvidence) -> None:
        best = self.fires_by_class.get(fire.fire_class)
        if best is not None and best.path == node.path:
            self.terminals_by_class[fire.fire_class] = node

    def record_survivor_coverage(
        self,
        *,
        depth: int,
        candidate_count: int,
        retained_count: int,
        quota: int,
        budget_exhausted: bool,
    ) -> None:
        self.survivor_candidate_counts[int(depth)] = int(candidate_count)
        self.survivor_counts[int(depth)] = int(retained_count)
        if retained_count >= quota:
            self.survivor_shortfalls.pop(int(depth), None)
            return
        if budget_exhausted:
            reason = "expanded_node_budget"
        elif candidate_count >= quota:
            reason = "beam_width_below_root_quota"
        elif self.terminal_fire_count > 0:
            reason = "terminal_fire_without_quiet_survivor"
        elif self.game_over_nodes > 0:
            reason = "game_over_without_survivor"
        elif self.invalid_nodes > 0:
            reason = "invalid_transition_without_survivor"
        else:
            reason = "no_non_terminal_survivor"
        self.survivor_shortfalls[int(depth)] = reason

    @property
    def selected_fire_class(self) -> str:
        available = set(self.fires_by_class)
        if FIRE_CLASS_WINNING in available:
            return FIRE_CLASS_WINNING
        if FIRE_CLASS_TARGET in available:
            return FIRE_CLASS_TARGET
        if FIRE_CLASS_FORCED_SAFETY in available:
            return FIRE_CLASS_FORCED_SAFETY
        if self.best_survivor is not None:
            return FIRE_CLASS_QUIET
        if FIRE_CLASS_PREMATURE in available:
            return FIRE_CLASS_PREMATURE
        return FIRE_CLASS_UNAVAILABLE

    @property
    def selected_fire(self) -> ChainFireEvidence | None:
        return self.fires_by_class.get(self.selected_fire_class)

    @property
    def representative(self) -> LongHorizonNode | None:
        if self.selected_fire_class == FIRE_CLASS_QUIET:
            return self.best_survivor
        return (
            self.terminals_by_class.get(self.selected_fire_class) or self.best_survivor
        )

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
            selected_fire_class=self.selected_fire_class,
            selected_fire=self.selected_fire,
            observed_fire_classes=tuple(sorted(self.fires_by_class)),
            quiet_survivor=self.best_survivor is not None,
            survivor_quota=int(self.root_survivor_quota),
            survivor_candidate_counts=tuple(
                sorted(self.survivor_candidate_counts.items())
            ),
            survivor_counts=tuple(sorted(self.survivor_counts.items())),
            survivor_shortfalls=tuple(sorted(self.survivor_shortfalls.items())),
            invalid_nodes=int(self.invalid_nodes),
            game_over_nodes=int(self.game_over_nodes),
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

    def selected_class(value: ScenarioRootEvidence) -> str:
        if value.selected_fire_class != FIRE_CLASS_UNAVAILABLE:
            return value.selected_fire_class
        if value.best_fire is not None:
            return value.best_fire.fire_class
        if value.survivor_evaluator_score is not None:
            return FIRE_CLASS_QUIET
        return FIRE_CLASS_UNAVAILABLE

    def selected_fire(value: ScenarioRootEvidence) -> ChainFireEvidence | None:
        if value.selected_fire is not None:
            return value.selected_fire
        if selected_class(value) != FIRE_CLASS_QUIET:
            return value.best_fire
        return None

    class_counts = Counter(selected_class(value) for value in evaluated)
    fire_class = max(
        class_counts,
        key=lambda value: (
            FIRE_CLASS_PRIORITY.get(value, -1),
            class_counts[value],
            value,
        ),
        default=FIRE_CLASS_UNAVAILABLE,
    )
    selected_scenarios = tuple(
        value for value in evaluated if selected_class(value) == fire_class
    )
    selected_fires = tuple(
        selected_fire(value)
        for value in selected_scenarios
        if selected_fire(value) is not None
    )
    scores = [
        float(fire.chain_score)
        if selected_class(value) == fire_class
        and (fire := selected_fire(value)) is not None
        else 0.0
        for value in evaluated
    ]
    counts = [
        float(fire.chain_count)
        if selected_class(value) == fire_class
        and (fire := selected_fire(value)) is not None
        else 0.0
        for value in evaluated
    ]
    continuations = [
        float(value.survivor_evaluator_score)
        for value in evaluated
        if selected_class(value) == FIRE_CLASS_QUIET
        and value.survivor_evaluator_score is not None
    ]
    terminal_scores = [
        float(value.terminal_score)
        for value in selected_fires
        if value.terminal_score is not None
    ]
    quiet_support = int(class_counts.get(FIRE_CLASS_QUIET, 0))
    target_not_reached_fire_count = sum(
        int(
            any(
                fire_class_name == FIRE_CLASS_PREMATURE
                for fire_class_name in value.observed_fire_classes
            )
        )
        for value in evaluated
    )
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
        max_chain_score=max(
            (value.max_chain_score for value in evaluated),
            default=0,
        ),
        max_chain_count=max(
            (value.max_chain_count for value in evaluated),
            default=0,
        ),
        best_fire=max(
            selected_fires,
            key=lambda value: value.rank_key,
            default=None,
        ),
        fire_class=fire_class,
        fire_class_support={
            candidate_class: int(class_counts.get(candidate_class, 0))
            for candidate_class in FIRE_CLASSES
        },
        terminal_score_sum=float(sum(terminal_scores)),
        terminal_score_mean=(None if not terminal_scores else _mean(terminal_scores)),
        terminal_score_worst=(None if not terminal_scores else min(terminal_scores)),
        terminal_score_dispersion=_dispersion(terminal_scores),
        quiet_support=quiet_support,
        target_not_reached_fire_count=int(target_not_reached_fire_count),
        root_survivor_quota=max(
            (value.survivor_quota for value in evaluated),
            default=1,
        ),
    )


def _survivor_sort_key(node: LongHorizonNode) -> tuple[Any, ...]:
    return (
        -float(node.evaluator_score),
        int(node.root_action),
        node.state.to_bytes(),
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
    depth: int,
    width: int,
    root_survivor_quota: int,
    trackers: Mapping[int, _ScenarioTracker],
    counters: LongHorizonSearchCounters,
) -> list[LongHorizonNode]:
    ranked = sorted(nodes, key=_survivor_sort_key)
    by_root: dict[int, list[LongHorizonNode]] = {
        int(root_action): [] for root_action in trackers
    }
    for node in ranked:
        by_root[int(node.root_action)].append(node)

    # Reserve one or more deterministic round-robin slots for every legal root
    # before allowing the remaining candidates to compete in the shared beam.
    retained: list[LongHorizonNode] = []
    retained_ids: set[int] = set()
    for quota_index in range(root_survivor_quota):
        for root_action in sorted(trackers):
            candidates = by_root[root_action]
            if quota_index >= len(candidates) or len(retained) >= width:
                continue
            node = candidates[quota_index]
            retained.append(node)
            retained_ids.add(id(node))
    for node in ranked:
        if len(retained) >= width:
            break
        if id(node) in retained_ids:
            continue
        retained.append(node)
        retained_ids.add(id(node))

    retained_counts = Counter(int(node.root_action) for node in retained)
    for root_action, tracker in trackers.items():
        tracker.record_survivor_coverage(
            depth=depth,
            candidate_count=len(by_root[int(root_action)]),
            retained_count=int(retained_counts.get(int(root_action), 0)),
            quota=root_survivor_quota,
            budget_exhausted=counters.budget_exhausted,
        )
    for node in ranked:
        if id(node) in retained_ids:
            continue
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


def _root_build_diagnostics(
    *,
    root_evaluation: Any,
    representative: LongHorizonNode | None,
    evidence: ExpectedChainRootEvidence,
) -> dict[str, Any]:
    before = getattr(root_evaluation, "features", None)
    after_evaluation = (
        None if representative is None else representative.evaluator_result
    )
    after = getattr(after_evaluation, "features", None)
    action = getattr(after_evaluation, "action_features", None)

    def delta(name: str) -> int | float | None:
        if before is None or after is None:
            return None
        left = getattr(before, name, None)
        right = getattr(after, name, None)
        if not isinstance(left, (int, float)) or not isinstance(
            right,
            (int, float),
        ):
            return None
        return right - left

    scenario_coverage = [
        value.to_dict()["survivor_coverage"] for value in evidence.scenario_values
    ]
    trigger_damage = (
        None if action is None else max(0, int(getattr(action, "trigger_damage", 0)))
    )
    return {
        "schema_version": ROOT_BUILD_DIAGNOSTICS_SCHEMA_VERSION,
        "root_action": int(evidence.root_action),
        "fire_class": evidence.fire_class,
        "quiet_candidate": evidence.fire_class == FIRE_CLASS_QUIET,
        "quiet_candidate_coverage": (
            0.0
            if evidence.requested_scenarios <= 0
            else evidence.quiet_support / float(evidence.requested_scenarios)
        ),
        "target_not_reached_fire_count": int(evidence.target_not_reached_fire_count),
        "survivor_coverage": {
            "quota": int(evidence.root_survivor_quota),
            "scenarios": scenario_coverage,
        },
        "trigger": {
            "damage": trigger_damage,
            "preserved": None if trigger_damage is None else trigger_damage == 0,
        },
        "danger": {
            "value": (
                None
                if after_evaluation is None
                else float(getattr(after_evaluation, "danger", 1.0))
            ),
            "delta": delta("danger_ratio"),
        },
        "structural_potential_delta": {
            "potential_chain_count": delta("potential_chain_count"),
            "potential_chain_score": delta("potential_chain_score"),
            "reachable_ignitions": delta("reachable_ignition_count"),
            "connection_candidates": delta("connection_candidate_count"),
            "growth_sites": delta("growth_site_count"),
        },
    }


def run_long_horizon_search(
    simulator: HeadlessPuyoSimulator,
    config: LongHorizonSearchConfig,
    *,
    evaluator: CompactNodeEvaluator | None = None,
) -> LongHorizonSearchResult:
    """Run compact expected-chain search without simulator clones."""

    return _run_long_horizon_search(
        CompactSearchState.from_simulator(simulator),
        _known_scenario_pairs(simulator),
        config,
        evaluator=evaluator,
    )


def run_compact_long_horizon_search(
    root_state: CompactSearchState,
    known_pairs: Sequence[Sequence[Any]],
    config: LongHorizonSearchConfig,
    *,
    evaluator: CompactNodeEvaluator | None = None,
) -> LongHorizonSearchResult:
    """Run the search from an allowlisted observation snapshot."""

    return _run_long_horizon_search(
        root_state,
        known_pairs,
        config,
        evaluator=evaluator,
    )


def _run_long_horizon_search(
    root_state: CompactSearchState,
    known_pairs: Sequence[Sequence[Any]],
    config: LongHorizonSearchConfig,
    *,
    evaluator: CompactNodeEvaluator | None = None,
) -> LongHorizonSearchResult:
    """Shared search implementation for simulator and visible-input callers."""

    selected_evaluator = evaluator or ChainStructureEvaluator()
    root_evaluation = selected_evaluator.evaluate(
        root_state,
        target_chain_count=config.minimum_chain_count,
    )
    sequences = build_scenario_sequences_from_known_pairs(
        known_pairs,
        scenarios=config.scenarios,
        depth=config.depth,
        decision_seed=config.resolved_decision_seed,
        sampling_mode=config.future_sampling_mode,
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
                root_survivor_quota=config.root_survivor_quota,
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
                tracker.invalid_nodes += 1
                continue
            counters.generated_nodes += 1
            counters.reached_depth = max(counters.reached_depth, 1)
            path = (int(action),)
            terminal = _should_stop_fire(config, result.chain_count)
            reason = _terminal_reason(config) if terminal else None
            evaluation = None
            fire = None
            if result.chain_count > 0:
                evaluation = _evaluate_node(
                    selected_evaluator,
                    state=result.state,
                    parent=root_evaluation,
                    action=result,
                    config=config,
                )
                counters.evaluated_nodes += 1
                fire_class = classify_build_main_fire(
                    chain_count=result.chain_count,
                    chain_score=result.score_delta,
                    target_chain_count=config.minimum_chain_count,
                    fire_context=config.fire_context,
                    winning_score_threshold=config.winning_score_threshold,
                )
                terminal_score, terminal_breakdown = _terminal_fire_score(
                    result=result,
                    evaluation=evaluation,
                    fire_class=fire_class,
                    config=config,
                )
                fire = tracker.record_fire(
                    result=result,
                    evaluation=evaluation,
                    fire_class=fire_class,
                    terminal_score=terminal_score,
                    terminal_score_breakdown=terminal_breakdown,
                    target_chain_count=config.minimum_chain_count,
                    path=path,
                    terminal=terminal,
                    terminal_reason=reason,
                )
            if terminal:
                if fire is None:
                    raise RuntimeError("terminal fire is missing classified evidence")
                counters.terminal_fire_nodes += 1
                terminal_node = _new_node(
                    result=result,
                    root_action=action,
                    scenario_id=sequence.scenario_id,
                    path=path,
                    evaluation=evaluation,
                    cumulative_action_score=result.score_delta,
                )
                tracker.record_terminal(terminal_node, fire)
                continue
            if result.game_over:
                counters.game_over_nodes += 1
                tracker.game_over_nodes += 1
                continue
            if evaluation is None:
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
            depth=1,
            width=config.width,
            root_survivor_quota=config.root_survivor_quota,
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
                        tracker.invalid_nodes += 1
                        continue
                    counters.generated_nodes += 1
                    counters.reached_depth = max(counters.reached_depth, depth)
                    path = node.path + (int(action),)
                    terminal = _should_stop_fire(config, result.chain_count)
                    reason = _terminal_reason(config) if terminal else None
                    evaluation = None
                    fire = None
                    if result.chain_count > 0:
                        evaluation = _evaluate_node(
                            selected_evaluator,
                            state=result.state,
                            parent=node.evaluator_result,
                            action=result,
                            config=config,
                        )
                        counters.evaluated_nodes += 1
                        fire_class = classify_build_main_fire(
                            chain_count=result.chain_count,
                            chain_score=result.score_delta,
                            target_chain_count=config.minimum_chain_count,
                            fire_context=config.fire_context,
                            winning_score_threshold=config.winning_score_threshold,
                        )
                        terminal_score, terminal_breakdown = _terminal_fire_score(
                            result=result,
                            evaluation=evaluation,
                            fire_class=fire_class,
                            config=config,
                        )
                        fire = tracker.record_fire(
                            result=result,
                            evaluation=evaluation,
                            fire_class=fire_class,
                            terminal_score=terminal_score,
                            terminal_score_breakdown=terminal_breakdown,
                            target_chain_count=config.minimum_chain_count,
                            path=path,
                            terminal=terminal,
                            terminal_reason=reason,
                        )
                    if terminal:
                        if fire is None:
                            raise RuntimeError(
                                "terminal fire is missing classified evidence"
                            )
                        counters.terminal_fire_nodes += 1
                        terminal_node = _new_node(
                            result=result,
                            root_action=node.root_action,
                            scenario_id=sequence.scenario_id,
                            path=path,
                            evaluation=evaluation,
                            cumulative_action_score=(
                                node.cumulative_action_score + result.score_delta
                            ),
                        )
                        tracker.record_terminal(terminal_node, fire)
                        continue
                    if result.game_over:
                        counters.game_over_nodes += 1
                        tracker.game_over_nodes += 1
                        continue
                    if evaluation is None:
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
                depth=depth,
                width=config.width,
                root_survivor_quota=config.root_survivor_quota,
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
    root_diagnostics: dict[int, Mapping[str, Any]] = {}
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
            and tracker.selected_fire_class == aggregate.fire_class
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
        root_diagnostics[int(action)] = _root_build_diagnostics(
            root_evaluation=root_evaluation,
            representative=representative,
            evidence=aggregate,
        )
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
        root_diagnostics=root_diagnostics,
    )


__all__ = [
    "ALLOWED_FIRE_CLASSES",
    "EXPECTED_CHAIN_EVIDENCE_SCHEMA_VERSION",
    "EXPECTED_CHAIN_RANKING_RULE_VERSION",
    "FIRE_CLASSES",
    "FIRE_CLASS_FORCED_SAFETY",
    "FIRE_CLASS_PREMATURE",
    "FIRE_CLASS_PRIORITY",
    "FIRE_CLASS_QUIET",
    "FIRE_CLASS_TARGET",
    "FIRE_CLASS_UNAVAILABLE",
    "FIRE_CLASS_WINNING",
    "FIRE_CONTEXTS",
    "FIRE_CONTEXT_FORCED_SAFETY",
    "FIRE_CONTEXT_SAFE_BUILD",
    "FUTURE_QUEUE_GENERATOR",
    "FUTURE_ROLLOUT_SEED_DERIVATION",
    "FUTURE_SAMPLING_LEGACY_FIXED_SIX",
    "FUTURE_SAMPLING_MODES",
    "FUTURE_SAMPLING_SCHEMA_VERSION",
    "FUTURE_SAMPLING_SEEDED_AUTHORITATIVE",
    "LEGACY_FIXED_SIX_PROFILE",
    "LONG_HORIZON_PROFILE_SCHEMA_VERSION",
    "LONG_HORIZON_PROPOSAL_DIGEST_VERSION",
    "LONG_HORIZON_SEARCH_PROFILES",
    "LONG_HORIZON_SURVIVOR_TIE_BREAK_VERSION",
    "QUALITY_D12_PROFILE",
    "QUALITY_D16_PROFILE",
    "REPRESENTATIVE_SCENARIO_BAGS",
    "ROOT_BUILD_DIAGNOSTICS_SCHEMA_VERSION",
    "ROOT_SURVIVOR_COVERAGE_SCHEMA_VERSION",
    "RUNTIME_PROFILE",
    "SCENARIO_SEQUENCE_SCHEMA_VERSION",
    "SMOKE_PROFILE",
    "TERMINAL_FIRE_CONTINUE",
    "TERMINAL_FIRE_RECORD_AND_STOP",
    "TERMINAL_FIRE_SCORE_VERSION",
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
    "build_scenario_sequences_from_known_pairs",
    "classify_build_main_fire",
    "compact_state_fingerprint",
    "long_horizon_profile",
    "long_horizon_proposal_digest",
    "run_compact_long_horizon_search",
    "run_long_horizon_search",
]
