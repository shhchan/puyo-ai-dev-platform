"""Versioned K-best worker proposals shared by runtime, training, and replay."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from agents.beam_search import DiverseBeamCandidate, clone_simulator
from agents.v1_7_planner import resolve_preview_attack
from puyo_env.actions import NUM_ACTIONS, action_to_placement


WORKER_PROPOSAL_SCHEMA_VERSION = "puyo.worker_proposal_batch.v1"
LEGACY_WORKER_PROPOSAL_SCHEMA_VERSION = "puyo.worker_proposal_batch.v0"
WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION = "puyo.worker_proposal_candidate.v1"
CANDIDATE_RANKER_INPUT_SCHEMA_VERSION = "puyo.worker_candidate_ranker_input.v1"
CANDIDATE_SELECTION_TELEMETRY_SCHEMA_VERSION = (
    "puyo.worker_candidate_selection_telemetry.v1"
)
CANDIDATE_DISTRIBUTION_SCHEMA_VERSION = "puyo.worker_candidate_distribution.v1"

CANDIDATE_RANKER_FEATURE_NAMES = (
    "candidate_value",
    "predicted_chain_count",
    "predicted_score",
    "predicted_attack.generated",
    "predicted_attack.outgoing",
    "build_potential.predicted_chain_potential",
    "ignition_cost.normalized",
    "trigger_recoverability.recoverable",
    "continuation_flexibility",
    "danger",
    "scenario_uncertainty.coverage",
    "search_latency_ms",
    "expanded_nodes",
)

_PREVIEW_STATUSES = {
    "complete",
    "partial",
    "unavailable",
    "deterministic_fallback",
}
_SELECTION_MODES = {
    "compatibility_rank_0",
    "deterministic_fallback",
    "learned_ranker",
    "empty",
}


def _canonical_digest(value: Any, *, prefix: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _mapping_copy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return copy.deepcopy(dict(value or {}))


def _float_mapping(value: Mapping[str, Any] | None) -> dict[str, float]:
    return {str(key): float(item) for key, item in (value or {}).items()}


def _normalize_legal_mask(mask: Sequence[bool] | None) -> tuple[bool, ...]:
    values = tuple(bool(value) for value in (mask or ()))
    if len(values) != NUM_ACTIONS:
        raise ValueError(
            f"legal action mask requires {NUM_ACTIONS} entries, got {len(values)}"
        )
    return values


def _bounded(value: Any, maximum: float, *, signed: bool = False) -> float:
    number = float(value)
    if signed:
        return max(-1.0, min(1.0, number / maximum))
    return max(0.0, min(1.0, number / maximum))


def _nested(value: Mapping[str, Any], path: str, default: Any = 0.0) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


@dataclass(frozen=True)
class WorkerProposalCandidate:
    """One legal candidate slot with a replay-complete structured preview."""

    candidate_id: str
    rank: int
    source_rank: int
    root_action: int
    action_sequence: tuple[int, ...]
    candidate_value: float
    predicted_chain_count: int
    predicted_score: int
    attack_preview: Mapping[str, int]
    build_potential: Mapping[str, Any]
    trigger_recoverability: Mapping[str, Any]
    continuation_flexibility: float
    danger: float
    scenario_uncertainty: Mapping[str, Any]
    search_latency_ms: float
    expanded_nodes: int
    value_breakdown: Mapping[str, float]
    generation_reasons: tuple[str, ...] = ()
    retention_reasons: tuple[str, ...] = ()
    pruning_reasons: tuple[str, ...] = ()
    chain_style_metadata: Mapping[str, Any] | None = None
    preview_status: str = "complete"
    source_schema_version: str = ""
    fallback: bool = False
    schema_version: str = WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported worker proposal candidate schema: {self.schema_version}"
            )
        if not self.candidate_id:
            raise ValueError("worker proposal candidate id is required")
        if self.rank < 0 or self.source_rank < 0:
            raise ValueError("worker proposal ranks must be non-negative")
        if not 0 <= self.root_action < NUM_ACTIONS:
            raise ValueError(f"worker proposal root action is invalid: {self.root_action}")
        if not self.action_sequence or self.action_sequence[0] != self.root_action:
            raise ValueError("worker proposal sequence must start with its root action")
        if any(not 0 <= action < NUM_ACTIONS for action in self.action_sequence):
            raise ValueError("worker proposal sequence contains an invalid action")
        if min(self.predicted_chain_count, self.predicted_score, self.expanded_nodes) < 0:
            raise ValueError("worker proposal preview counts must be non-negative")
        if self.search_latency_ms < 0.0:
            raise ValueError("worker proposal latency must be non-negative")
        if not 0.0 <= self.continuation_flexibility <= 1.0:
            raise ValueError("worker proposal continuation flexibility must be in [0, 1]")
        if not 0.0 <= self.danger <= 1.0:
            raise ValueError("worker proposal danger must be in [0, 1]")
        if self.preview_status not in _PREVIEW_STATUSES:
            raise ValueError(f"unsupported worker proposal preview status: {self.preview_status}")

    @property
    def predicted_attack(self) -> int:
        return max(0, int(self.attack_preview.get("generated", 0)))

    @property
    def ignition_cost(self) -> Mapping[str, Any] | None:
        value = self.build_potential.get("ignition_cost")
        return value if isinstance(value, Mapping) else None

    @property
    def ranker_features(self) -> tuple[float, ...]:
        potential = self.build_potential.get("predicted_chain_potential")
        recoverable = self.trigger_recoverability.get("recoverable")
        return (
            _bounded(self.candidate_value, 1_000_000.0, signed=True),
            _bounded(self.predicted_chain_count, 19.0),
            _bounded(self.predicted_score, 100_000.0),
            _bounded(self.attack_preview.get("generated", 0), 180.0),
            _bounded(self.attack_preview.get("outgoing", 0), 180.0),
            0.0 if potential is None else _bounded(potential, 1.0),
            _bounded(_nested(self.build_potential, "ignition_cost.normalized", 0.0), 1.0),
            1.0 if recoverable is True else 0.0,
            _bounded(self.continuation_flexibility, 1.0),
            _bounded(self.danger, 1.0),
            _bounded(self.scenario_uncertainty.get("coverage", 0.0), 1.0),
            _bounded(self.search_latency_ms, 2_000.0),
            _bounded(self.expanded_nodes, 8_192.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "rank": int(self.rank),
            "source_rank": int(self.source_rank),
            "root_action": int(self.root_action),
            "action_sequence": [int(action) for action in self.action_sequence],
            "candidate_value": float(self.candidate_value),
            "preview": {
                "status": self.preview_status,
                "predicted_chain_count": int(self.predicted_chain_count),
                "predicted_score": int(self.predicted_score),
                "predicted_attack": {
                    key: int(value) for key, value in self.attack_preview.items()
                },
                "build_potential": _mapping_copy(self.build_potential),
                "ignition_cost": (
                    None if self.ignition_cost is None else _mapping_copy(self.ignition_cost)
                ),
                "trigger_recoverability": _mapping_copy(
                    self.trigger_recoverability
                ),
                "continuation_flexibility": float(
                    self.continuation_flexibility
                ),
                "danger": float(self.danger),
                "scenario_uncertainty": _mapping_copy(
                    self.scenario_uncertainty
                ),
                "search_cost": {
                    "latency_ms": float(self.search_latency_ms),
                    "expanded_nodes": int(self.expanded_nodes),
                    "attribution": "decision_search_total",
                },
            },
            "value_breakdown": _float_mapping(self.value_breakdown),
            "chain_style": (
                None
                if self.chain_style_metadata is None
                else _mapping_copy(self.chain_style_metadata)
            ),
            "reasons": {
                "generated": list(self.generation_reasons),
                "retained": list(self.retention_reasons),
                "pruned": list(self.pruning_reasons),
            },
            "source": {
                "schema_version": self.source_schema_version,
                "fallback": bool(self.fallback),
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkerProposalCandidate":
        if value.get("schema_version") != WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION:
            raise ValueError("unsupported serialized worker proposal candidate")
        preview = value.get("preview")
        reasons = value.get("reasons", {})
        source = value.get("source", {})
        if not isinstance(preview, Mapping):
            raise ValueError("worker proposal candidate preview must be a mapping")
        if not isinstance(reasons, Mapping) or not isinstance(source, Mapping):
            raise ValueError("worker proposal candidate metadata must be mappings")
        attack = preview.get("predicted_attack", {})
        search_cost = preview.get("search_cost", {})
        if not isinstance(attack, Mapping) or not isinstance(search_cost, Mapping):
            raise ValueError("worker proposal attack/search preview must be mappings")
        style = value.get("chain_style")
        return cls(
            candidate_id=str(value["candidate_id"]),
            rank=int(value["rank"]),
            source_rank=int(value.get("source_rank", value["rank"])),
            root_action=int(value["root_action"]),
            action_sequence=tuple(int(action) for action in value["action_sequence"]),
            candidate_value=float(value.get("candidate_value", 0.0)),
            predicted_chain_count=int(preview.get("predicted_chain_count", 0)),
            predicted_score=int(preview.get("predicted_score", 0)),
            attack_preview={str(key): int(item) for key, item in attack.items()},
            build_potential=_mapping_copy(
                preview.get("build_potential")
                if isinstance(preview.get("build_potential"), Mapping)
                else None
            ),
            trigger_recoverability=_mapping_copy(
                preview.get("trigger_recoverability")
                if isinstance(preview.get("trigger_recoverability"), Mapping)
                else None
            ),
            continuation_flexibility=float(
                preview.get("continuation_flexibility", 0.0)
            ),
            danger=float(preview.get("danger", 1.0)),
            scenario_uncertainty=_mapping_copy(
                preview.get("scenario_uncertainty")
                if isinstance(preview.get("scenario_uncertainty"), Mapping)
                else None
            ),
            search_latency_ms=float(search_cost.get("latency_ms", 0.0)),
            expanded_nodes=int(search_cost.get("expanded_nodes", 0)),
            value_breakdown=_float_mapping(
                value.get("value_breakdown")
                if isinstance(value.get("value_breakdown"), Mapping)
                else None
            ),
            generation_reasons=tuple(str(item) for item in reasons.get("generated", ())),
            retention_reasons=tuple(str(item) for item in reasons.get("retained", ())),
            pruning_reasons=tuple(str(item) for item in reasons.get("pruned", ())),
            chain_style_metadata=(
                _mapping_copy(style) if isinstance(style, Mapping) else None
            ),
            preview_status=str(preview.get("status", "unavailable")),
            source_schema_version=str(source.get("schema_version", "")),
            fallback=bool(source.get("fallback", False)),
        )


@dataclass(frozen=True)
class CandidateRankerInput:
    """Fixed-shape policy/critic input that requires no environment re-search."""

    proposal_id: str
    candidate_ids: tuple[str | None, ...]
    features: tuple[tuple[float, ...], ...]
    candidate_mask: tuple[bool, ...]
    legal_action_mask: tuple[bool, ...]
    selected_index: int | None
    schema_version: str = CANDIDATE_RANKER_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        size = len(self.candidate_mask)
        if len(self.candidate_ids) != size or len(self.features) != size:
            raise ValueError("candidate ranker slots and mask must have equal lengths")
        if any(len(row) != len(CANDIDATE_RANKER_FEATURE_NAMES) for row in self.features):
            raise ValueError("candidate ranker feature width does not match its contract")
        if self.selected_index is not None:
            if not 0 <= self.selected_index < size:
                raise ValueError("candidate ranker selected index is out of range")
            if not self.candidate_mask[self.selected_index]:
                raise ValueError("candidate ranker selected index must be unmasked")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "dtype": "float32",
            "feature_names": list(CANDIDATE_RANKER_FEATURE_NAMES),
            "candidate_ids": list(self.candidate_ids),
            "features": [list(row) for row in self.features],
            "candidate_mask": list(self.candidate_mask),
            "legal_action_mask": list(self.legal_action_mask),
            "selected_index": self.selected_index,
        }


@dataclass(frozen=True)
class CandidateSelectionTelemetry:
    candidate_count: int
    candidate_limit: int
    legal_action_count: int
    unique_root_actions: int
    unique_action_sequences: int
    candidate_coverage: float
    candidate_collapse_ratio: float
    selected_index: int | None
    selected_candidate_id: str | None
    selected_value: float | None
    best_value: float | None
    selection_regret: float | None
    search_latency_ms: float
    expanded_nodes: int
    fallback_used: bool
    schema_version: str = CANDIDATE_SELECTION_TELEMETRY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_count": int(self.candidate_count),
            "candidate_limit": int(self.candidate_limit),
            "legal_action_count": int(self.legal_action_count),
            "unique_root_actions": int(self.unique_root_actions),
            "unique_action_sequences": int(self.unique_action_sequences),
            "candidate_coverage": float(self.candidate_coverage),
            "candidate_collapse_ratio": float(self.candidate_collapse_ratio),
            "selected_index": self.selected_index,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_value": self.selected_value,
            "best_value": self.best_value,
            "selection_regret": self.selection_regret,
            "search_latency_ms": float(self.search_latency_ms),
            "expanded_nodes": int(self.expanded_nodes),
            "fallback_used": bool(self.fallback_used),
        }


@dataclass(frozen=True)
class CandidateDistribution:
    probabilities: tuple[float, ...]
    selected_index: int | None
    selected_log_probability: float | None
    entropy: float
    schema_version: str = CANDIDATE_DISTRIBUTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "probabilities": list(self.probabilities),
            "selected_index": self.selected_index,
            "selected_log_probability": self.selected_log_probability,
            "entropy": float(self.entropy),
        }


@dataclass(frozen=True)
class RankedCandidateSelection:
    index: int
    candidate_id: str
    action: int
    distribution: CandidateDistribution
    telemetry: CandidateSelectionTelemetry


@dataclass(frozen=True)
class WorkerProposalBatch:
    """Fixed-length K-best proposal artifact with explicit padding semantics."""

    proposal_id: str
    decision_id: str
    profile_id: int
    profile_name: str
    strategy: str
    candidate_limit: int
    candidates: tuple[WorkerProposalCandidate | None, ...]
    candidate_mask: tuple[bool, ...]
    legal_action_mask: tuple[bool, ...]
    selected_index: int | None
    selection_mode: str
    fallback_reason: str | None
    search_latency_ms: float
    expanded_nodes: int
    schema_version: str = WORKER_PROPOSAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != WORKER_PROPOSAL_SCHEMA_VERSION:
            raise ValueError(f"unsupported worker proposal schema: {self.schema_version}")
        if self.candidate_limit <= 0:
            raise ValueError("worker proposal candidate limit must be positive")
        if len(self.candidates) != self.candidate_limit:
            raise ValueError("worker proposal slots must equal candidate limit")
        if len(self.candidate_mask) != self.candidate_limit:
            raise ValueError("worker proposal candidate mask has the wrong length")
        if tuple(candidate is not None for candidate in self.candidates) != self.candidate_mask:
            raise ValueError("worker proposal padding and candidate mask disagree")
        seen_padding = False
        for candidate in self.candidates:
            if candidate is None:
                seen_padding = True
            elif seen_padding:
                raise ValueError("worker proposal padding must follow all candidates")
        if len(self.legal_action_mask) != NUM_ACTIONS:
            raise ValueError("worker proposal legal action mask has the wrong length")
        if self.selection_mode not in _SELECTION_MODES:
            raise ValueError(f"unsupported worker proposal selection mode: {self.selection_mode}")
        if self.selected_index is not None:
            if not 0 <= self.selected_index < self.candidate_limit:
                raise ValueError("worker proposal selected index is out of range")
            if not self.candidate_mask[self.selected_index]:
                raise ValueError("worker proposal selected index points to padding")
        elif any(self.candidate_mask):
            raise ValueError("non-empty worker proposal requires a selected candidate")
        actual = tuple(candidate for candidate in self.candidates if candidate is not None)
        if len({candidate.candidate_id for candidate in actual}) != len(actual):
            raise ValueError("worker proposal candidate ids must be unique")
        if len({candidate.root_action for candidate in actual}) != len(actual):
            raise ValueError("worker proposal root actions must be unique")
        if len({candidate.action_sequence for candidate in actual}) != len(actual):
            raise ValueError("worker proposal action sequences must be unique")
        for index, candidate in enumerate(actual):
            if candidate.rank != index:
                raise ValueError("worker proposal ranks must be contiguous")
            if not self.legal_action_mask[candidate.root_action]:
                raise ValueError("worker proposal contains an illegal root action")
        if self.search_latency_ms < 0.0 or self.expanded_nodes < 0:
            raise ValueError("worker proposal search costs must be non-negative")
        expected_id = _batch_id(
            self.decision_id,
            self.profile_id,
            self.candidate_limit,
            actual,
        )
        if self.proposal_id != expected_id:
            raise ValueError(
                f"worker proposal id mismatch: expected {expected_id}, got {self.proposal_id}"
            )

    @property
    def candidate_count(self) -> int:
        return sum(self.candidate_mask)

    @property
    def selected_candidate(self) -> WorkerProposalCandidate | None:
        return None if self.selected_index is None else self.candidates[self.selected_index]

    @property
    def selected_action(self) -> int | None:
        candidate = self.selected_candidate
        return None if candidate is None else candidate.root_action

    @property
    def ranker_input(self) -> CandidateRankerInput:
        zero_row = (0.0,) * len(CANDIDATE_RANKER_FEATURE_NAMES)
        return CandidateRankerInput(
            proposal_id=self.proposal_id,
            candidate_ids=tuple(
                None if candidate is None else candidate.candidate_id
                for candidate in self.candidates
            ),
            features=tuple(
                zero_row if candidate is None else candidate.ranker_features
                for candidate in self.candidates
            ),
            candidate_mask=self.candidate_mask,
            legal_action_mask=self.legal_action_mask,
            selected_index=self.selected_index,
        )

    def telemetry(self, selected_index: int | None = None) -> CandidateSelectionTelemetry:
        index = self.selected_index if selected_index is None else int(selected_index)
        if index is not None and (
            not 0 <= index < self.candidate_limit or not self.candidate_mask[index]
        ):
            raise ValueError("candidate telemetry selected index is masked")
        actual = tuple(candidate for candidate in self.candidates if candidate is not None)
        selected = None if index is None else self.candidates[index]
        best_value = max((candidate.candidate_value for candidate in actual), default=None)
        selected_value = None if selected is None else selected.candidate_value
        legal_count = sum(self.legal_action_mask)
        unique_roots = len({candidate.root_action for candidate in actual})
        unique_sequences = len({candidate.action_sequence for candidate in actual})
        possible = min(self.candidate_limit, legal_count)
        collapse = 0.0 if possible <= 0 else 1.0 - len(actual) / float(possible)
        return CandidateSelectionTelemetry(
            candidate_count=len(actual),
            candidate_limit=self.candidate_limit,
            legal_action_count=legal_count,
            unique_root_actions=unique_roots,
            unique_action_sequences=unique_sequences,
            candidate_coverage=(0.0 if legal_count <= 0 else unique_roots / float(legal_count)),
            candidate_collapse_ratio=max(0.0, min(1.0, collapse)),
            selected_index=index,
            selected_candidate_id=None if selected is None else selected.candidate_id,
            selected_value=selected_value,
            best_value=best_value,
            selection_regret=(
                None
                if best_value is None or selected_value is None
                else max(0.0, float(best_value) - float(selected_value))
            ),
            search_latency_ms=self.search_latency_ms,
            expanded_nodes=self.expanded_nodes,
            fallback_used=bool(selected is not None and selected.fallback),
        )

    def with_selected_index(
        self,
        selected_index: int,
        *,
        selection_mode: str = "learned_ranker",
    ) -> "WorkerProposalBatch":
        if not 0 <= selected_index < self.candidate_limit or not self.candidate_mask[selected_index]:
            raise ValueError("worker proposal selected index is masked")
        return replace(
            self,
            selected_index=int(selected_index),
            selection_mode=selection_mode,
        )

    def to_dict(self) -> dict[str, Any]:
        selected = self.selected_candidate
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "decision_id": self.decision_id,
            "profile": {
                "id": int(self.profile_id),
                "name": self.profile_name,
                "strategy": self.strategy,
            },
            "candidate_limit": int(self.candidate_limit),
            "candidate_count": int(self.candidate_count),
            "selection": {
                "mode": self.selection_mode,
                "selected_index": self.selected_index,
                "selected_candidate_id": (
                    None if selected is None else selected.candidate_id
                ),
                "selected_action": (
                    None if selected is None else int(selected.root_action)
                ),
                "fallback_reason": self.fallback_reason,
            },
            "masks": {
                "candidate": list(self.candidate_mask),
                "legal_action": list(self.legal_action_mask),
            },
            "padding": {
                "value": None,
                "semantics": "masked slots are null and must not enter policy or critic reductions",
            },
            "empty_semantics": "all candidate slots are masked and selected_index is null",
            "candidates": [
                None if candidate is None else candidate.to_dict()
                for candidate in self.candidates
            ],
            "ranker_input": self.ranker_input.to_dict(),
            "telemetry": self.telemetry().to_dict(),
            "search_cost": {
                "latency_ms": float(self.search_latency_ms),
                "expanded_nodes": int(self.expanded_nodes),
                "latency_kind": "observational_wall_clock",
                "budget_kind": "deterministic_expanded_nodes",
            },
        }

    def deterministic_dict(self) -> dict[str, Any]:
        """Return the contract payload with wall-clock observations neutralized."""

        payload = self.to_dict()
        payload["search_cost"]["latency_ms"] = 0.0
        payload["telemetry"]["search_latency_ms"] = 0.0
        latency_index = CANDIDATE_RANKER_FEATURE_NAMES.index("search_latency_ms")
        for row in payload["ranker_input"]["features"]:
            row[latency_index] = 0.0
        for candidate in payload["candidates"]:
            if candidate is not None:
                candidate["preview"]["search_cost"]["latency_ms"] = 0.0
        return payload

    @property
    def deterministic_digest(self) -> str:
        return _canonical_digest(
            self.deterministic_dict(),
            prefix="proposal-contract",
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkerProposalBatch":
        payload = migrate_worker_proposal_payload(value)
        profile = payload.get("profile")
        selection = payload.get("selection")
        masks = payload.get("masks")
        search = payload.get("search_cost", {})
        if not all(isinstance(item, Mapping) for item in (profile, selection, masks, search)):
            raise ValueError("worker proposal profile/selection/masks/search must be mappings")
        candidates = tuple(
            None
            if candidate is None
            else WorkerProposalCandidate.from_dict(candidate)
            for candidate in payload.get("candidates", ())
        )
        batch = cls(
            proposal_id=str(payload["proposal_id"]),
            decision_id=str(payload["decision_id"]),
            profile_id=int(profile["id"]),
            profile_name=str(profile.get("name", "")),
            strategy=str(profile.get("strategy", "")),
            candidate_limit=int(payload["candidate_limit"]),
            candidates=candidates,
            candidate_mask=tuple(bool(item) for item in masks.get("candidate", ())),
            legal_action_mask=_normalize_legal_mask(masks.get("legal_action")),
            selected_index=(
                None
                if selection.get("selected_index") is None
                else int(selection["selected_index"])
            ),
            selection_mode=str(selection.get("mode", "empty")),
            fallback_reason=(
                None
                if selection.get("fallback_reason") is None
                else str(selection["fallback_reason"])
            ),
            search_latency_ms=float(search.get("latency_ms", 0.0)),
            expanded_nodes=int(search.get("expanded_nodes", 0)),
        )
        if int(payload.get("candidate_count", batch.candidate_count)) != batch.candidate_count:
            raise ValueError("serialized worker proposal candidate count is inconsistent")
        return batch


def _batch_id(
    decision_id: str,
    profile_id: int,
    candidate_limit: int,
    candidates: Sequence[WorkerProposalCandidate],
) -> str:
    return _canonical_digest(
        {
            "schema_version": WORKER_PROPOSAL_SCHEMA_VERSION,
            "decision_id": decision_id,
            "profile_id": int(profile_id),
            "candidate_limit": int(candidate_limit),
            "candidate_ids": [candidate.candidate_id for candidate in candidates],
        },
        prefix="proposal",
    )


def _decision_id(simulator: Any, fallback_payload: Mapping[str, Any]) -> str:
    if simulator is None:
        return _canonical_digest(fallback_payload, prefix="decision")
    game = simulator.game
    grid = [
        [cell.color.name for cell in row]
        for row in game.field.grid
    ]

    def pair_payload(pair: Any) -> list[str | None]:
        return [
            getattr(getattr(item, "color", None), "name", None)
            for item in pair
        ]

    current = pair_payload((game.current_puyo_1, game.current_puyo_2))
    queued = [pair_payload(pair) for pair in game.next_puyo_queue]
    return _canonical_digest(
        {
            "field": grid,
            "current": current,
            "next": queued,
            "all_clear_bonus_pending": bool(game.all_clear_bonus_pending),
            "decision_context": dict(fallback_payload),
        },
        prefix="decision",
    )


def _candidate_id(decision_id: str, actions: Sequence[int]) -> str:
    return _canonical_digest(
        {
            "schema_version": WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION,
            "decision_id": decision_id,
            "action_sequence": [int(action) for action in actions],
        },
        prefix="candidate",
    )


def _simulate_action_sequence(
    simulator: Any,
    actions: Sequence[int],
    *,
    score_carry: int,
    incoming_attack: int,
) -> dict[str, Any]:
    attack = {
        "attack_score_delta": 0,
        "score_carry_before": max(0, int(score_carry)),
        "score_carry_after": max(0, int(score_carry)),
        "generated": 0,
        "canceled": 0,
        "outgoing": 0,
        "incoming_before": max(0, int(incoming_attack)),
        "incoming_after": max(0, int(incoming_attack)),
    }
    if simulator is None:
        return {
            "status": "unavailable",
            "predicted_chain_count": 0,
            "predicted_score": 0,
            "attack": attack,
        }
    cursor = clone_simulator(simulator)
    carry = max(0, int(score_carry))
    incoming = max(0, int(incoming_attack))
    max_chain = 0
    total_score = 0
    complete = True
    for action in actions:
        result = cursor.step(action_to_placement(int(action)))
        if not result.valid:
            complete = False
            break
        max_chain = max(max_chain, int(result.chain_count))
        total_score += max(0, int(result.score_delta))
        step = resolve_preview_attack(result.attack_score_delta, carry, incoming)
        carry = step.score_carry_after
        incoming = step.incoming_after
        attack["attack_score_delta"] += int(step.attack_score_delta)
        attack["score_carry_after"] = int(carry)
        attack["generated"] += int(step.generated)
        attack["canceled"] += int(step.canceled)
        attack["outgoing"] += int(step.outgoing)
        attack["incoming_after"] = int(incoming)
        if result.game_over:
            complete = False
            break
    return {
        "status": "complete" if complete else "partial",
        "predicted_chain_count": max_chain,
        "predicted_score": total_score,
        "attack": attack,
    }


def _scenario_uncertainty(
    candidate: DiverseBeamCandidate,
    scenario_budget: Mapping[str, Any] | None,
) -> dict[str, Any]:
    budget = scenario_budget or {}
    evaluated = max(
        int(budget.get("hidden_future_evaluated", 0)),
        int(candidate.scenario_support),
    )
    support = max(0, int(candidate.scenario_support))
    return {
        "status": str(budget.get("uncertainty", "unknown")),
        "support": support,
        "evaluated_scenarios": evaluated,
        "coverage": (0.0 if evaluated <= 0 else min(1.0, support / float(evaluated))),
        "scenario_ids": [int(item) for item in candidate.scenario_ids],
    }


def _candidate_from_beam(
    raw: DiverseBeamCandidate,
    *,
    rank: int,
    decision_id: str,
    simulator: Any,
    score_carry: int,
    incoming_attack: int,
    search_latency_ms: float,
    expanded_nodes: int,
    scenario_budget: Mapping[str, Any] | None,
) -> WorkerProposalCandidate:
    sequence = tuple(int(action) for action in raw.plan) or (int(raw.action),)
    runtime = _simulate_action_sequence(
        simulator,
        sequence,
        score_carry=score_carry,
        incoming_attack=incoming_attack,
    )
    style = getattr(raw, "chain_style_evaluation", None)
    return WorkerProposalCandidate(
        candidate_id=_candidate_id(decision_id, sequence),
        rank=int(rank),
        source_rank=max(0, int(raw.rank)),
        root_action=int(raw.action),
        action_sequence=sequence,
        candidate_value=float(raw.candidate_value),
        predicted_chain_count=max(
            int(raw.predicted_max_chain),
            int(runtime["predicted_chain_count"]),
        ),
        predicted_score=int(runtime["predicted_score"]),
        attack_preview=dict(runtime["attack"]),
        build_potential=raw.build_potential.to_dict(),
        trigger_recoverability=raw.trigger_recoverability.to_dict(),
        continuation_flexibility=float(raw.continuation_flexibility),
        danger=float(raw.danger),
        scenario_uncertainty=_scenario_uncertainty(raw, scenario_budget),
        search_latency_ms=max(0.0, float(search_latency_ms)),
        expanded_nodes=max(0, int(expanded_nodes)),
        value_breakdown=_float_mapping(raw.value_breakdown),
        generation_reasons=tuple(str(item) for item in raw.generation_reasons),
        retention_reasons=tuple(str(item) for item in raw.retention_reasons),
        pruning_reasons=tuple(str(item) for item in raw.pruning_reasons),
        chain_style_metadata=(
            _mapping_copy(style) if isinstance(style, Mapping) and style else None
        ),
        preview_status=str(runtime["status"]),
        source_schema_version=str(raw.schema_version),
    )


def _fallback_candidate(
    *,
    decision_id: str,
    action: int,
    simulator: Any,
    score_carry: int,
    incoming_attack: int,
    search_latency_ms: float,
    expanded_nodes: int,
    fallback_preview: Mapping[str, Any] | None,
) -> WorkerProposalCandidate:
    preview = fallback_preview or {}
    runtime = _simulate_action_sequence(
        simulator,
        (action,),
        score_carry=score_carry,
        incoming_attack=incoming_attack,
    )
    build_potential = preview.get("build_potential", {})
    recoverability = preview.get("trigger_recoverability", {})
    style = preview.get("chain_style")
    return WorkerProposalCandidate(
        candidate_id=_candidate_id(decision_id, (action,)),
        rank=0,
        source_rank=0,
        root_action=action,
        action_sequence=(action,),
        candidate_value=float(preview.get("candidate_value", 0.0)),
        predicted_chain_count=max(
            int(preview.get("predicted_chain_count", 0)),
            int(runtime["predicted_chain_count"]),
        ),
        predicted_score=max(
            int(preview.get("predicted_score", 0)),
            int(runtime["predicted_score"]),
        ),
        attack_preview=dict(runtime["attack"]),
        build_potential=(
            _mapping_copy(build_potential)
            if isinstance(build_potential, Mapping)
            else {}
        ),
        trigger_recoverability=(
            _mapping_copy(recoverability)
            if isinstance(recoverability, Mapping)
            else {}
        ),
        continuation_flexibility=float(
            preview.get("continuation_flexibility", 0.0)
        ),
        danger=float(preview.get("danger", 1.0)),
        scenario_uncertainty={
            "status": "fallback",
            "support": 0,
            "evaluated_scenarios": 0,
            "coverage": 0.0,
            "scenario_ids": [],
        },
        search_latency_ms=max(0.0, float(search_latency_ms)),
        expanded_nodes=max(0, int(expanded_nodes)),
        value_breakdown=_float_mapping(
            preview.get("value_breakdown")
            if isinstance(preview.get("value_breakdown"), Mapping)
            else None
        ),
        generation_reasons=("compatibility_selection",),
        retention_reasons=("deterministic_fallback",),
        chain_style_metadata=(
            _mapping_copy(style) if isinstance(style, Mapping) and style else None
        ),
        preview_status=(
            "deterministic_fallback"
            if runtime["status"] != "unavailable"
            else "unavailable"
        ),
        source_schema_version="compatibility_selection",
        fallback=True,
    )


def build_worker_proposal_batch(
    candidates: Sequence[DiverseBeamCandidate],
    *,
    selected_action: int,
    candidate_limit: int,
    legal_action_mask: Sequence[bool],
    profile_id: int,
    profile_name: str,
    strategy: str,
    simulator: Any = None,
    score_carry: int = 0,
    incoming_attack: int = 0,
    search_latency_ms: float = 0.0,
    expanded_nodes: int = 0,
    scenario_budget: Mapping[str, Any] | None = None,
    fallback_preview: Mapping[str, Any] | None = None,
) -> WorkerProposalBatch:
    """Normalize raw beam candidates into a fixed K, ranker-ready artifact."""

    limit = int(candidate_limit)
    if limit <= 0:
        raise ValueError("worker proposal candidate limit must be positive")
    legal = _normalize_legal_mask(legal_action_mask)
    raw_candidates = sorted(
        candidates,
        key=lambda item: (
            0 if int(item.action) == int(selected_action) else 1,
            int(item.rank),
            -float(item.candidate_value),
            int(item.action),
            tuple(int(action) for action in item.plan),
        ),
    )
    decision_id = _decision_id(
        simulator,
        {
            "profile_id": int(profile_id),
            "profile_name": str(profile_name),
            "strategy": str(strategy),
            "score_carry": max(0, int(score_carry)),
            "incoming_attack": max(0, int(incoming_attack)),
        },
    )
    retained: list[DiverseBeamCandidate] = []
    roots: set[int] = set()
    plans: set[tuple[int, ...]] = set()
    for raw in raw_candidates:
        action = int(raw.action)
        sequence = tuple(int(item) for item in raw.plan) or (action,)
        if not 0 <= action < NUM_ACTIONS or not legal[action]:
            continue
        if action in roots or sequence in plans:
            continue
        retained.append(raw)
        roots.add(action)
        plans.add(sequence)
        if len(retained) >= limit:
            break

    normalized = [
        _candidate_from_beam(
            raw,
            rank=rank,
            decision_id=decision_id,
            simulator=simulator,
            score_carry=score_carry,
            incoming_attack=incoming_attack,
            search_latency_ms=search_latency_ms,
            expanded_nodes=expanded_nodes,
            scenario_budget=scenario_budget,
        )
        for rank, raw in enumerate(retained)
    ]
    fallback_reason = None
    selection_mode = "compatibility_rank_0"
    if not normalized or normalized[0].root_action != int(selected_action):
        fallback_action = (
            int(selected_action)
            if 0 <= int(selected_action) < NUM_ACTIONS and legal[int(selected_action)]
            else next((index for index, allowed in enumerate(legal) if allowed), None)
        )
        if fallback_action is not None:
            fallback = _fallback_candidate(
                decision_id=decision_id,
                action=fallback_action,
                simulator=simulator,
                score_carry=score_carry,
                incoming_attack=incoming_attack,
                search_latency_ms=search_latency_ms,
                expanded_nodes=expanded_nodes,
                fallback_preview=fallback_preview,
            )
            normalized = [
                fallback,
                *(
                    candidate
                    for candidate in normalized
                    if candidate.root_action != fallback_action
                    and candidate.action_sequence != fallback.action_sequence
                ),
            ][:limit]
            normalized = [replace(candidate, rank=index) for index, candidate in enumerate(normalized)]
            fallback_reason = (
                "raw_candidate_missing_selected_action"
                if retained
                else "raw_candidate_set_empty"
            )
            selection_mode = "deterministic_fallback"
        else:
            normalized = []
            fallback_reason = "no_legal_actions"
            selection_mode = "empty"

    slots: tuple[WorkerProposalCandidate | None, ...] = tuple(
        [*normalized, *([None] * (limit - len(normalized)))]
    )
    mask = tuple(candidate is not None for candidate in slots)
    actual = tuple(candidate for candidate in slots if candidate is not None)
    proposal_id = _batch_id(decision_id, profile_id, limit, actual)
    return WorkerProposalBatch(
        proposal_id=proposal_id,
        decision_id=decision_id,
        profile_id=int(profile_id),
        profile_name=str(profile_name),
        strategy=str(strategy),
        candidate_limit=limit,
        candidates=slots,
        candidate_mask=mask,
        legal_action_mask=legal,
        selected_index=0 if normalized else None,
        selection_mode=selection_mode,
        fallback_reason=fallback_reason,
        search_latency_ms=max(0.0, float(search_latency_ms)),
        expanded_nodes=max(0, int(expanded_nodes)),
    )


def compatibility_action(batch: WorkerProposalBatch, *, empty_action: int = 0) -> int:
    """Return rank 0 exactly, preserving the pre-ranker single-action contract."""

    selected = batch.selected_candidate
    return int(empty_action) if selected is None else int(selected.root_action)


def masked_candidate_distribution(
    logits: Sequence[float],
    ranker_input: CandidateRankerInput,
    *,
    selected_index: int | None = None,
) -> CandidateDistribution:
    """Apply a stable masked softmax and expose log-probability/entropy targets."""

    if len(logits) != len(ranker_input.candidate_mask):
        raise ValueError("candidate logits and candidate mask must have equal lengths")
    valid = [index for index, allowed in enumerate(ranker_input.candidate_mask) if allowed]
    if not valid:
        return CandidateDistribution(
            probabilities=tuple(0.0 for _ in logits),
            selected_index=None,
            selected_log_probability=None,
            entropy=0.0,
        )
    values = [float(value) for value in logits]
    if any(not math.isfinite(values[index]) for index in valid):
        raise ValueError("unmasked candidate logits must be finite")
    maximum = max(values[index] for index in valid)
    exponentials = {index: math.exp(values[index] - maximum) for index in valid}
    total = sum(exponentials.values())
    probabilities = tuple(
        exponentials.get(index, 0.0) / total for index in range(len(values))
    )
    index = ranker_input.selected_index if selected_index is None else int(selected_index)
    if index is not None and index not in valid:
        raise ValueError("selected candidate index is masked")
    log_probability = (
        None
        if index is None
        else values[index] - maximum - math.log(total)
    )
    entropy = -sum(
        probability * math.log(probability)
        for probability in probabilities
        if probability > 0.0
    )
    return CandidateDistribution(
        probabilities=probabilities,
        selected_index=index,
        selected_log_probability=log_probability,
        entropy=entropy,
    )


def select_ranked_candidate(
    batch: WorkerProposalBatch,
    logits: Sequence[float],
) -> RankedCandidateSelection:
    """Deterministically adapt ranker logits back to one legal root action."""

    ranker_input = batch.ranker_input
    valid = [index for index, allowed in enumerate(ranker_input.candidate_mask) if allowed]
    if not valid:
        raise ValueError("cannot rank an empty worker proposal")
    if len(logits) != batch.candidate_limit:
        raise ValueError("candidate logits and proposal slots must have equal lengths")
    index = max(valid, key=lambda item: (float(logits[item]), -item))
    distribution = masked_candidate_distribution(
        logits,
        ranker_input,
        selected_index=index,
    )
    candidate = batch.candidates[index]
    if candidate is None:  # pragma: no cover - guarded by candidate_mask
        raise RuntimeError("ranker selected a padded candidate")
    return RankedCandidateSelection(
        index=index,
        candidate_id=candidate.candidate_id,
        action=candidate.root_action,
        distribution=distribution,
        telemetry=batch.telemetry(index),
    )


def migrate_worker_proposal_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate the pre-contract raw diverse-candidate envelope to batch v1."""

    if not isinstance(value, Mapping):
        raise ValueError("worker proposal payload must be a mapping")
    schema = value.get("schema_version")
    if schema == WORKER_PROPOSAL_SCHEMA_VERSION:
        return copy.deepcopy(dict(value))
    if schema != LEGACY_WORKER_PROPOSAL_SCHEMA_VERSION:
        raise ValueError(f"unsupported worker proposal schema: {schema}")

    raw_candidates = value.get("candidates", ())
    if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, (str, bytes)):
        raise ValueError("legacy worker proposal candidates must be a sequence")
    legal = _normalize_legal_mask(value.get("legal_action_mask"))
    profile = value.get("profile", {})
    if not isinstance(profile, Mapping):
        raise ValueError("legacy worker proposal profile must be a mapping")
    limit = max(1, int(value.get("candidate_limit", len(raw_candidates) or 1)))
    selected_action = int(value.get("selected_action", 0))
    decision_id = str(
        value.get("decision_id")
        or _canonical_digest(
            {
                "profile": dict(profile),
                "selected_action": selected_action,
                "candidates": raw_candidates,
            },
            prefix="decision",
        )
    )
    normalized: list[WorkerProposalCandidate] = []
    roots: set[int] = set()
    plans: set[tuple[int, ...]] = set()
    ordered = sorted(
        (item for item in raw_candidates if isinstance(item, Mapping)),
        key=lambda item: (
            0 if int(item.get("root_action", -1)) == selected_action else 1,
            int(item.get("rank", 0)),
            int(item.get("root_action", 0)),
        ),
    )
    for raw in ordered:
        action = int(raw.get("root_action", -1))
        sequence = tuple(int(item) for item in raw.get("plan", (action,)))
        if not 0 <= action < NUM_ACTIONS or not legal[action] or not sequence:
            continue
        if action in roots or sequence in plans:
            continue
        potential = raw.get("build_potential", {})
        recoverability = raw.get("trigger_recoverability", {})
        hidden = raw.get("hidden_future_scenarios", {})
        reasons = raw.get("reasons", {})
        style = raw.get("chain_style")
        candidate = WorkerProposalCandidate(
            candidate_id=_candidate_id(decision_id, sequence),
            rank=len(normalized),
            source_rank=max(0, int(raw.get("rank", len(normalized)))),
            root_action=action,
            action_sequence=sequence,
            candidate_value=float(raw.get("candidate_value", 0.0)),
            predicted_chain_count=max(0, int(raw.get("predicted_max_chain", 0))),
            predicted_score=0,
            attack_preview={
                "attack_score_delta": 0,
                "score_carry_before": 0,
                "score_carry_after": 0,
                "generated": 0,
                "canceled": 0,
                "outgoing": 0,
                "incoming_before": 0,
                "incoming_after": 0,
            },
            build_potential=(
                _mapping_copy(potential) if isinstance(potential, Mapping) else {}
            ),
            trigger_recoverability=(
                _mapping_copy(recoverability)
                if isinstance(recoverability, Mapping)
                else {}
            ),
            continuation_flexibility=float(raw.get("continuation_flexibility", 0.0)),
            danger=float(raw.get("danger", 1.0)),
            scenario_uncertainty={
                "status": "legacy_unknown",
                "support": int(hidden.get("support", 0)) if isinstance(hidden, Mapping) else 0,
                "evaluated_scenarios": 0,
                "coverage": 0.0,
                "scenario_ids": (
                    list(hidden.get("scenario_ids", ()))
                    if isinstance(hidden, Mapping)
                    else []
                ),
            },
            search_latency_ms=0.0,
            expanded_nodes=0,
            value_breakdown=_float_mapping(
                raw.get("value_breakdown")
                if isinstance(raw.get("value_breakdown"), Mapping)
                else None
            ),
            generation_reasons=tuple(
                str(item)
                for item in (reasons.get("generated", ()) if isinstance(reasons, Mapping) else ())
            ),
            retention_reasons=tuple(
                str(item)
                for item in (reasons.get("retained", ()) if isinstance(reasons, Mapping) else ())
            ),
            pruning_reasons=tuple(
                str(item)
                for item in (reasons.get("pruned", ()) if isinstance(reasons, Mapping) else ())
            ),
            chain_style_metadata=(
                _mapping_copy(style) if isinstance(style, Mapping) and style else None
            ),
            preview_status="unavailable",
            source_schema_version=str(raw.get("schema_version", "")),
        )
        normalized.append(candidate)
        roots.add(action)
        plans.add(sequence)
        if len(normalized) >= limit:
            break
    if normalized and normalized[0].root_action != selected_action:
        raise ValueError("legacy worker proposal selected action is absent from candidates")
    slots: tuple[WorkerProposalCandidate | None, ...] = tuple(
        [*normalized, *([None] * (limit - len(normalized)))]
    )
    actual = tuple(candidate for candidate in slots if candidate is not None)
    profile_id = int(profile.get("id", 0))
    batch = WorkerProposalBatch(
        proposal_id=_batch_id(decision_id, profile_id, limit, actual),
        decision_id=decision_id,
        profile_id=profile_id,
        profile_name=str(profile.get("name", "")),
        strategy=str(profile.get("strategy", "")),
        candidate_limit=limit,
        candidates=slots,
        candidate_mask=tuple(candidate is not None for candidate in slots),
        legal_action_mask=legal,
        selected_index=0 if normalized else None,
        selection_mode="compatibility_rank_0" if normalized else "empty",
        fallback_reason=None if normalized else "legacy_candidate_set_empty",
        search_latency_ms=0.0,
        expanded_nodes=0,
    )
    return batch.to_dict()
