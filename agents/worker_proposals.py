"""Versioned K-best worker proposals shared by runtime, training, and replay."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from agents.beam_search import DiverseBeamCandidate, clone_simulator
from agents.v1_7_planner import resolve_preview_attack
from puyo_env.actions import NUM_ACTIONS, action_to_placement


WORKER_PROPOSAL_V1_SCHEMA_VERSION = "puyo.worker_proposal_batch.v1"
WORKER_PROPOSAL_SCHEMA_VERSION = "puyo.worker_proposal_batch.v2"
LEGACY_WORKER_PROPOSAL_SCHEMA_VERSION = "puyo.worker_proposal_batch.v0"
WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION = (
    "puyo.worker_proposal_candidate.v1"
)
WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION = "puyo.worker_proposal_candidate.v2"
CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION = (
    "puyo.worker_candidate_ranker_input.v1"
)
CANDIDATE_RANKER_INPUT_SCHEMA_VERSION = "puyo.worker_candidate_ranker_input.v2"
CANDIDATE_EVIDENCE_SCHEMA_VERSION = "puyo.worker_candidate_evidence.v2"
WORKER_PROPOSAL_SHARED_CONTEXT_SCHEMA_VERSION = (
    "puyo.worker_proposal_shared_context.v2"
)
CANDIDATE_RANKER_COMPAT_PROJECTION_SCHEMA_VERSION = (
    "puyo.worker_candidate_ranker_projection.v2-to-v1"
)
CANDIDATE_SELECTION_TELEMETRY_SCHEMA_VERSION = (
    "puyo.worker_candidate_selection_telemetry.v1"
)
CANDIDATE_DISTRIBUTION_SCHEMA_VERSION = "puyo.worker_candidate_distribution.v1"
WORKER_PROPOSAL_SCENARIO_LIMIT = 6

CANDIDATE_RANKER_V1_FEATURE_NAMES = (
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

CANDIDATE_RANKER_V1_FEATURE_SPECS = (
    ("candidate_value", 1_000_000.0, True),
    ("predicted_chain_count", 19.0, False),
    ("predicted_score", 100_000.0, False),
    ("predicted_attack.generated", 180.0, False),
    ("predicted_attack.outgoing", 180.0, False),
    ("build_potential.predicted_chain_potential", 1.0, False),
    ("ignition_cost.normalized", 1.0, False),
    ("trigger_recoverability.recoverable", 1.0, False),
    ("continuation_flexibility", 1.0, False),
    ("danger", 1.0, False),
    ("scenario_uncertainty.coverage", 1.0, False),
    ("search_latency_ms", 2_000.0, False),
    ("expanded_nodes", 8_192.0, False),
)

CANDIDATE_RANKER_V2_FEATURE_SPECS = (
    ("root_action", float(NUM_ACTIONS - 1), False),
    ("candidate_value", 1_000_000.0, True),
    ("predicted_chain_count", 19.0, False),
    ("predicted_score", 100_000.0, False),
    ("predicted_attack.generated", 180.0, False),
    ("predicted_attack.outgoing", 180.0, False),
    ("expected_chain.score.sum", 600_000.0, False),
    ("expected_chain.score.mean", 100_000.0, False),
    ("expected_chain.score.worst", 100_000.0, False),
    ("expected_chain.score.dispersion", 100_000.0, False),
    ("expected_chain.score.maximum", 100_000.0, False),
    ("expected_chain.count.sum", 114.0, False),
    ("expected_chain.count.mean", 19.0, False),
    ("expected_chain.count.worst", 19.0, False),
    ("expected_chain.count.dispersion", 19.0, False),
    ("expected_chain.count.maximum", 19.0, False),
    ("expected_chain.support", 6.0, False),
    ("expected_chain.coverage", 1.0, False),
    ("trajectory.max_chain_depth", 32.0, False),
    ("trajectory.terminal_fire", 1.0, False),
    ("trajectory.premature_fire", 1.0, False),
    ("trajectory.target_fire", 1.0, False),
    ("structural.score", 1_000_000.0, True),
    ("structural.potential_chain_count", 19.0, False),
    ("structural.required_key_count", 8.0, False),
    ("structural.trigger_height", 14.0, False),
    ("structural.connectivity_edges", 128.0, False),
    ("structural.connection_candidates", 64.0, False),
    ("structural.growth_sites", 64.0, False),
    ("structural.remaining_connection_edges", 128.0, False),
    ("structural.danger", 1.0, False),
    ("structural.tear", 64.0, False),
    ("structural.waste", 64.0, False),
    ("structural.trigger_damage", 64.0, False),
    ("build_potential.predicted_chain_count", 19.0, False),
    ("build_potential.predicted_chain_potential", 1.0, False),
    ("build_potential.required_puyos", 4.0, False),
    ("build_potential.evaluated", 1.0, False),
    ("build_potential.budget_exhausted", 1.0, False),
    ("trigger_recoverability.recoverable", 1.0, False),
    ("continuation_flexibility", 1.0, False),
    ("danger", 1.0, False),
    ("scenario_uncertainty.coverage", 1.0, False),
)

CANDIDATE_RANKER_FEATURE_NAMES = tuple(
    name for name, _, _ in CANDIDATE_RANKER_V2_FEATURE_SPECS
)
CANDIDATE_RANKER_SCENARIO_FEATURE_SPECS = (
    ("max_chain_count", 19.0, False),
    ("max_chain_score", 100_000.0, False),
    ("reached_depth", 32.0, False),
    ("evaluated", 1.0, False),
    ("search_complete", 1.0, False),
    ("terminal_fire_count", 64.0, False),
    ("expanded_nodes", 600_000.0, False),
    ("pruned_nodes", 600_000.0, False),
    ("transposition_hits", 600_000.0, False),
)
CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES = tuple(
    name for name, _, _ in CANDIDATE_RANKER_SCENARIO_FEATURE_SPECS
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


def _nested_value(value: Mapping[str, Any], path: str) -> tuple[Any, bool]:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None, False
        current = current[part]
    return current, current is not None


def _validate_json_finite(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_json_finite(item, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _validate_json_finite(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"{path} contains a non-JSON value: {type(value).__name__}")


def _normalization_metadata(
    specs: Sequence[tuple[str, float, bool]],
) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "scale": float(scale),
            "signed": bool(signed),
            "clamp": [-1.0, 1.0] if signed else [0.0, 1.0],
        }
        for name, scale, signed in specs
    ]


def candidate_ranker_schema_metadata(schema_version: str) -> dict[str, Any]:
    if schema_version == CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION:
        payload = {
            "schema_version": schema_version,
            "dtype": "float32",
            "feature_names": list(CANDIDATE_RANKER_V1_FEATURE_NAMES),
            "normalization": _normalization_metadata(
                CANDIDATE_RANKER_V1_FEATURE_SPECS
            ),
            "feature_mask": False,
            "scenario_limit": 0,
            "scenario_feature_names": [],
            "shared_context": "candidate_rows_include_decision_search_cost",
        }
    elif schema_version == CANDIDATE_RANKER_INPUT_SCHEMA_VERSION:
        payload = {
            "schema_version": schema_version,
            "dtype": "float32",
            "feature_names": list(CANDIDATE_RANKER_FEATURE_NAMES),
            "normalization": _normalization_metadata(
                CANDIDATE_RANKER_V2_FEATURE_SPECS
            ),
            "feature_mask": True,
            "scenario_limit": WORKER_PROPOSAL_SCENARIO_LIMIT,
            "scenario_feature_names": list(
                CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES
            ),
            "scenario_normalization": _normalization_metadata(
                CANDIDATE_RANKER_SCENARIO_FEATURE_SPECS
            ),
            "shared_context": "separate_object",
        }
    else:
        raise ValueError(f"unsupported candidate ranker schema: {schema_version}")
    payload["schema_hash"] = _canonical_digest(
        payload,
        prefix="candidate-ranker-schema",
    )
    return payload


CANDIDATE_RANKER_V1_SCHEMA_HASH = candidate_ranker_schema_metadata(
    CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION
)["schema_hash"]
CANDIDATE_RANKER_SCHEMA_HASH = candidate_ranker_schema_metadata(
    CANDIDATE_RANKER_INPUT_SCHEMA_VERSION
)["schema_hash"]


class EvidenceStatus(str, Enum):
    EVALUATED = "evaluated"
    NOT_EVALUATED = "not_evaluated"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LEGACY_MISSING = "legacy_missing"


@dataclass(frozen=True)
class MaskedNumeric:
    """One finite numeric value with explicit presence/evaluation semantics."""

    value: float | None
    is_present: bool
    evaluated: bool
    status: EvidenceStatus

    def __post_init__(self) -> None:
        if self.is_present != (self.value is not None):
            raise ValueError("masked numeric presence flag disagrees with its value")
        if self.value is not None and not math.isfinite(float(self.value)):
            raise ValueError("masked numeric values must be finite")
        if self.status == EvidenceStatus.LEGACY_MISSING and self.is_present:
            raise ValueError("legacy-missing numeric evidence cannot contain a value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": None if self.value is None else float(self.value),
            "is_present": bool(self.is_present),
            "evaluated": bool(self.evaluated),
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MaskedNumeric":
        raw = value.get("value")
        return cls(
            value=None if raw is None else float(raw),
            is_present=bool(value.get("is_present", raw is not None)),
            evaluated=bool(value.get("evaluated", False)),
            status=EvidenceStatus(str(value.get("status", "not_evaluated"))),
        )


def _scenario_identity_digest(
    scenario_ids: Sequence[int | None],
    scenario_digests: Sequence[str | None],
    scenario_mask: Sequence[bool],
) -> str:
    return _canonical_digest(
        [
            {
                "scenario_id": scenario_id,
                "sequence_digest": sequence_digest,
                "mask": bool(mask),
            }
            for scenario_id, sequence_digest, mask in zip(
                scenario_ids,
                scenario_digests,
                scenario_mask,
            )
        ],
        prefix="proposal-scenarios",
    )


@dataclass(frozen=True)
class WorkerProposalSharedContext:
    """Decision-level search context kept outside candidate-local ranker rows."""

    profile: Mapping[str, Any]
    known_queue_length: int
    scenario_ids: tuple[int | None, ...]
    scenario_digests: tuple[str | None, ...]
    scenario_mask: tuple[bool, ...]
    scenario_sequences: tuple[Mapping[str, Any], ...]
    scenario_digest: str
    search_config: Mapping[str, Any]
    search_totals: Mapping[str, Any]
    latency: Mapping[str, Any]
    worker_deadline: Mapping[str, Any]
    schema_version: str = WORKER_PROPOSAL_SHARED_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != WORKER_PROPOSAL_SHARED_CONTEXT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported worker proposal shared context: {self.schema_version}"
            )
        if self.known_queue_length < 0:
            raise ValueError("known queue length must be non-negative")
        expected = WORKER_PROPOSAL_SCENARIO_LIMIT
        if not (
            len(self.scenario_ids)
            == len(self.scenario_digests)
            == len(self.scenario_mask)
            == expected
        ):
            raise ValueError("worker proposal scenario slots have the wrong length")
        seen_padding = False
        actual_ids: list[int] = []
        for scenario_id, digest, mask in zip(
            self.scenario_ids,
            self.scenario_digests,
            self.scenario_mask,
        ):
            if not mask:
                seen_padding = True
                if scenario_id is not None or digest is not None:
                    raise ValueError("padded scenario identity must be null")
                continue
            if seen_padding:
                raise ValueError("scenario padding must follow all real scenarios")
            if scenario_id is None:
                raise ValueError("real scenario slot requires an id")
            actual_ids.append(int(scenario_id))
        if actual_ids != sorted(actual_ids) or len(set(actual_ids)) != len(actual_ids):
            raise ValueError("scenario ids must be unique and canonical")
        expected_digest = _scenario_identity_digest(
            self.scenario_ids,
            self.scenario_digests,
            self.scenario_mask,
        )
        if self.scenario_digest != expected_digest:
            raise ValueError("worker proposal scenario digest mismatch")
        _validate_json_finite(self.to_dict(), path="shared_context")

    @property
    def scenario_count(self) -> int:
        return sum(self.scenario_mask)

    @property
    def elapsed_ms(self) -> float:
        return max(0.0, float(self.latency.get("elapsed_ms", 0.0)))

    @property
    def expanded_nodes(self) -> int:
        return max(0, int(self.search_totals.get("expanded_nodes", 0)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile": _mapping_copy(self.profile),
            "known_queue_length": int(self.known_queue_length),
            "scenarios": {
                "count": int(self.scenario_count),
                "limit": WORKER_PROPOSAL_SCENARIO_LIMIT,
                "ids": list(self.scenario_ids),
                "sequence_digests": list(self.scenario_digests),
                "mask": list(self.scenario_mask),
                "digest": self.scenario_digest,
                "sequences": [
                    _mapping_copy(sequence) for sequence in self.scenario_sequences
                ],
            },
            "search_config": _mapping_copy(self.search_config),
            "search_totals": _mapping_copy(self.search_totals),
            "latency": _mapping_copy(self.latency),
            "worker_deadline": _mapping_copy(self.worker_deadline),
        }

    def deterministic_dict(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload["latency"].update(
            {
                "elapsed_ms": 0.0,
                "p50_ms": 0.0,
                "p95_ms": 0.0,
            }
        )
        payload["worker_deadline"].update(
            {
                "status": "observational_neutralized",
                "overrun": False,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkerProposalSharedContext":
        if value.get("schema_version") != WORKER_PROPOSAL_SHARED_CONTEXT_SCHEMA_VERSION:
            raise ValueError("unsupported serialized worker proposal shared context")
        scenarios = value.get("scenarios")
        if not isinstance(scenarios, Mapping):
            raise ValueError("worker proposal shared scenarios must be a mapping")
        sequences = scenarios.get("sequences", ())
        if not isinstance(sequences, Sequence) or isinstance(sequences, (str, bytes)):
            raise ValueError("worker proposal scenario sequences must be a sequence")
        return cls(
            profile=_mapping_copy(
                value.get("profile") if isinstance(value.get("profile"), Mapping) else None
            ),
            known_queue_length=int(value.get("known_queue_length", 0)),
            scenario_ids=tuple(
                None if item is None else int(item)
                for item in scenarios.get("ids", ())
            ),
            scenario_digests=tuple(
                None if item is None else str(item)
                for item in scenarios.get("sequence_digests", ())
            ),
            scenario_mask=tuple(bool(item) for item in scenarios.get("mask", ())),
            scenario_sequences=tuple(
                _mapping_copy(item)
                for item in sequences
                if isinstance(item, Mapping)
            ),
            scenario_digest=str(scenarios.get("digest", "")),
            search_config=_mapping_copy(
                value.get("search_config")
                if isinstance(value.get("search_config"), Mapping)
                else None
            ),
            search_totals=_mapping_copy(
                value.get("search_totals")
                if isinstance(value.get("search_totals"), Mapping)
                else None
            ),
            latency=_mapping_copy(
                value.get("latency")
                if isinstance(value.get("latency"), Mapping)
                else None
            ),
            worker_deadline=_mapping_copy(
                value.get("worker_deadline")
                if isinstance(value.get("worker_deadline"), Mapping)
                else None
            ),
        )


@dataclass(frozen=True)
class CandidateEvidence:
    """Lossless v2 candidate evidence plus explicit feature missingness."""

    status: EvidenceStatus
    expected_chain: Mapping[str, Any] | None
    structural_chain: Mapping[str, Any] | None
    trajectory: Mapping[str, Any]
    build_potential_status: Mapping[str, Any]
    numeric_fields: Mapping[str, MaskedNumeric]
    scenario_values: tuple[Mapping[str, Any] | None, ...]
    scenario_mask: tuple[bool, ...]
    scenario_digest: str
    source_schema_version: str
    schema_version: str = CANDIDATE_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CANDIDATE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError(f"unsupported candidate evidence: {self.schema_version}")
        if len(self.scenario_values) != WORKER_PROPOSAL_SCENARIO_LIMIT:
            raise ValueError("candidate evidence scenario vector has the wrong length")
        if len(self.scenario_mask) != WORKER_PROPOSAL_SCENARIO_LIMIT:
            raise ValueError("candidate evidence scenario mask has the wrong length")
        if tuple(value is not None for value in self.scenario_values) != self.scenario_mask:
            raise ValueError("candidate evidence scenario values and mask disagree")
        if set(self.numeric_fields) - set(CANDIDATE_RANKER_FEATURE_NAMES):
            raise ValueError("candidate evidence contains an unknown ranker field")
        _validate_json_finite(self.to_dict(), path="candidate_evidence")

    @property
    def ranker_eligible(self) -> bool:
        return self.status in {
            EvidenceStatus.EVALUATED,
            EvidenceStatus.BUDGET_EXHAUSTED,
        }

    def numeric(self, name: str) -> MaskedNumeric:
        return self.numeric_fields.get(
            name,
            MaskedNumeric(
                value=None,
                is_present=False,
                evaluated=False,
                status=self.status,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "source_schema_version": self.source_schema_version,
            "expected_chain": (
                None
                if self.expected_chain is None
                else _mapping_copy(self.expected_chain)
            ),
            "structural_chain": (
                None
                if self.structural_chain is None
                else _mapping_copy(self.structural_chain)
            ),
            "trajectory": _mapping_copy(self.trajectory),
            "build_potential_status": _mapping_copy(
                self.build_potential_status
            ),
            "numeric_fields": {
                name: value.to_dict()
                for name, value in sorted(self.numeric_fields.items())
            },
            "scenario_vector": {
                "digest": self.scenario_digest,
                "mask": list(self.scenario_mask),
                "values": [
                    None if value is None else _mapping_copy(value)
                    for value in self.scenario_values
                ],
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateEvidence":
        if value.get("schema_version") != CANDIDATE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported serialized candidate evidence")
        vector = value.get("scenario_vector")
        fields = value.get("numeric_fields")
        if not isinstance(vector, Mapping) or not isinstance(fields, Mapping):
            raise ValueError("candidate evidence vector/fields must be mappings")
        scenario_values = vector.get("values", ())
        if not isinstance(scenario_values, Sequence) or isinstance(
            scenario_values, (str, bytes)
        ):
            raise ValueError("candidate evidence scenario values must be a sequence")
        expected = value.get("expected_chain")
        structural = value.get("structural_chain")
        return cls(
            status=EvidenceStatus(str(value.get("status", "not_evaluated"))),
            expected_chain=(
                _mapping_copy(expected) if isinstance(expected, Mapping) else None
            ),
            structural_chain=(
                _mapping_copy(structural)
                if isinstance(structural, Mapping)
                else None
            ),
            trajectory=_mapping_copy(
                value.get("trajectory")
                if isinstance(value.get("trajectory"), Mapping)
                else None
            ),
            build_potential_status=_mapping_copy(
                value.get("build_potential_status")
                if isinstance(value.get("build_potential_status"), Mapping)
                else None
            ),
            numeric_fields={
                str(name): MaskedNumeric.from_dict(item)
                for name, item in fields.items()
                if isinstance(item, Mapping)
            },
            scenario_values=tuple(
                _mapping_copy(item) if isinstance(item, Mapping) else None
                for item in scenario_values
            ),
            scenario_mask=tuple(bool(item) for item in vector.get("mask", ())),
            scenario_digest=str(vector.get("digest", "")),
            source_schema_version=str(value.get("source_schema_version", "")),
        )


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
    evidence: CandidateEvidence | None = None
    schema_version: str = WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in {
            WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION,
            WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION,
        }:
            raise ValueError(
                f"unsupported worker proposal candidate schema: {self.schema_version}"
            )
        if self.schema_version == WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION:
            if self.evidence is None:
                raise ValueError("worker proposal candidate v2 requires evidence")
        elif self.evidence is not None:
            raise ValueError("worker proposal candidate v1 cannot contain v2 evidence")
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
        _validate_json_finite(self.to_dict(), path="worker_proposal_candidate")

    @property
    def predicted_attack(self) -> int:
        return max(0, int(self.attack_preview.get("generated", 0)))

    @property
    def ignition_cost(self) -> Mapping[str, Any] | None:
        value = self.build_potential.get("ignition_cost")
        return value if isinstance(value, Mapping) else None

    @property
    def ranker_v1_features(self) -> tuple[float, ...]:
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

    @property
    def ranker_v1_feature_mask(self) -> tuple[bool, ...]:
        potential, potential_present = _nested_value(
            self.build_potential,
            "predicted_chain_potential",
        )
        ignition, ignition_present = _nested_value(
            self.build_potential,
            "ignition_cost.normalized",
        )
        recoverable = self.trigger_recoverability.get("recoverable")
        coverage = self.scenario_uncertainty.get("coverage")
        if self.evidence is not None:
            potential_present = self.evidence.numeric(
                "build_potential.predicted_chain_potential"
            ).is_present
            recoverable_present = self.evidence.numeric(
                "trigger_recoverability.recoverable"
            ).is_present
            coverage_present = self.evidence.numeric(
                "expected_chain.coverage"
            ).is_present
        else:
            recoverable_present = isinstance(recoverable, bool)
            coverage_present = coverage is not None
        return (
            True,
            True,
            True,
            True,
            True,
            bool(potential_present and potential is not None),
            bool(ignition_present and ignition is not None),
            recoverable_present,
            True,
            True,
            coverage_present,
            True,
            True,
        )

    @property
    def ranker_features(self) -> tuple[float, ...]:
        values, _ = self.ranker_v2_row
        return values

    @property
    def ranker_feature_mask(self) -> tuple[bool, ...]:
        _, mask = self.ranker_v2_row
        return mask

    @property
    def ranker_eligible(self) -> bool:
        return self.evidence is not None and self.evidence.ranker_eligible

    @property
    def ranker_v2_row(self) -> tuple[tuple[float, ...], tuple[bool, ...]]:
        base: dict[str, tuple[Any, bool]] = {
            "root_action": (self.root_action, True),
            "candidate_value": (self.candidate_value, True),
            "predicted_chain_count": (self.predicted_chain_count, True),
            "predicted_score": (self.predicted_score, True),
            "predicted_attack.generated": (
                self.attack_preview.get("generated", 0),
                "generated" in self.attack_preview,
            ),
            "predicted_attack.outgoing": (
                self.attack_preview.get("outgoing", 0),
                "outgoing" in self.attack_preview,
            ),
            "continuation_flexibility": (self.continuation_flexibility, True),
            "danger": (self.danger, True),
            "scenario_uncertainty.coverage": (
                self.scenario_uncertainty.get("coverage"),
                self.scenario_uncertainty.get("coverage") is not None,
            ),
        }
        values: list[float] = []
        mask: list[bool] = []
        for name, scale, signed in CANDIDATE_RANKER_V2_FEATURE_SPECS:
            if name in base:
                raw, present = base[name]
            elif self.evidence is None:
                raw, present = None, False
            else:
                numeric = self.evidence.numeric(name)
                raw, present = numeric.value, numeric.is_present
            values.append(
                0.0
                if not present or raw is None
                else _bounded(raw, scale, signed=signed)
            )
            mask.append(bool(present and raw is not None))
        return tuple(values), tuple(mask)

    @property
    def scenario_ranker_rows(
        self,
    ) -> tuple[
        tuple[tuple[float, ...], ...],
        tuple[tuple[bool, ...], ...],
        tuple[bool, ...],
    ]:
        zero = (0.0,) * len(CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES)
        false = (False,) * len(CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES)
        if self.evidence is None:
            return (
                tuple(zero for _ in range(WORKER_PROPOSAL_SCENARIO_LIMIT)),
                tuple(false for _ in range(WORKER_PROPOSAL_SCENARIO_LIMIT)),
                (False,) * WORKER_PROPOSAL_SCENARIO_LIMIT,
            )
        rows: list[tuple[float, ...]] = []
        masks: list[tuple[bool, ...]] = []
        for value in self.evidence.scenario_values:
            if value is None:
                rows.append(zero)
                masks.append(false)
                continue
            source = value.get("source")
            if not isinstance(source, Mapping):
                rows.append(zero)
                masks.append(false)
                continue
            row: list[float] = []
            row_mask: list[bool] = []
            for name, scale, signed in CANDIDATE_RANKER_SCENARIO_FEATURE_SPECS:
                raw, present = _nested_value(source, name)
                if name in {"evaluated", "search_complete"} and name in source:
                    raw, present = int(bool(source[name])), True
                row.append(
                    0.0
                    if not present or raw is None
                    else _bounded(raw, scale, signed=signed)
                )
                row_mask.append(bool(present and raw is not None))
            rows.append(tuple(row))
            masks.append(tuple(row_mask))
        return tuple(rows), tuple(masks), self.evidence.scenario_mask

    def to_dict(self) -> dict[str, Any]:
        preview = {
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
        }
        if self.schema_version == WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION:
            preview["search_cost"] = {
                "latency_ms": float(self.search_latency_ms),
                "expanded_nodes": int(self.expanded_nodes),
                "attribution": "decision_search_total",
            }
        payload = {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "rank": int(self.rank),
            "source_rank": int(self.source_rank),
            "root_action": int(self.root_action),
            "action_sequence": [int(action) for action in self.action_sequence],
            "candidate_value": float(self.candidate_value),
            "preview": preview,
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
        if self.schema_version == WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION:
            payload["evidence"] = self.evidence.to_dict()  # type: ignore[union-attr]
        return payload

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        shared_context: WorkerProposalSharedContext | None = None,
    ) -> "WorkerProposalCandidate":
        schema = value.get("schema_version")
        if schema not in {
            WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION,
            WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION,
        }:
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
        evidence = value.get("evidence")
        if schema == WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION:
            if not isinstance(evidence, Mapping):
                raise ValueError("worker proposal candidate v2 evidence is required")
            parsed_evidence = CandidateEvidence.from_dict(evidence)
        else:
            parsed_evidence = None
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
            search_latency_ms=float(
                search_cost.get(
                    "latency_ms",
                    0.0 if shared_context is None else shared_context.elapsed_ms,
                )
            ),
            expanded_nodes=int(
                search_cost.get(
                    "expanded_nodes",
                    0 if shared_context is None else shared_context.expanded_nodes,
                )
            ),
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
            evidence=parsed_evidence,
            schema_version=str(schema),
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
    feature_mask: tuple[tuple[bool, ...], ...] = ()
    scenario_ids: tuple[int | None, ...] = ()
    scenario_digests: tuple[str | None, ...] = ()
    scenario_mask: tuple[bool, ...] = ()
    scenario_features: tuple[tuple[tuple[float, ...], ...], ...] = ()
    scenario_feature_mask: tuple[tuple[tuple[bool, ...], ...], ...] = ()
    candidate_scenario_mask: tuple[tuple[bool, ...], ...] = ()
    shared_context: WorkerProposalSharedContext | None = None
    schema_hash: str = ""
    schema_version: str = CANDIDATE_RANKER_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        size = len(self.candidate_mask)
        if len(self.candidate_ids) != size or len(self.features) != size:
            raise ValueError("candidate ranker slots and mask must have equal lengths")
        if self.schema_version == CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION:
            expected_width = len(CANDIDATE_RANKER_V1_FEATURE_NAMES)
            expected_hash = CANDIDATE_RANKER_V1_SCHEMA_HASH
            if any(
                (
                    self.feature_mask,
                    self.scenario_ids,
                    self.scenario_digests,
                    self.scenario_mask,
                    self.scenario_features,
                    self.scenario_feature_mask,
                    self.candidate_scenario_mask,
                )
            ) or self.shared_context is not None:
                raise ValueError("candidate ranker v1 cannot contain v2 masks/context")
        elif self.schema_version == CANDIDATE_RANKER_INPUT_SCHEMA_VERSION:
            expected_width = len(CANDIDATE_RANKER_FEATURE_NAMES)
            expected_hash = CANDIDATE_RANKER_SCHEMA_HASH
            if len(self.feature_mask) != size or any(
                len(row) != expected_width for row in self.feature_mask
            ):
                raise ValueError("candidate ranker feature mask has the wrong shape")
            scenario_limit = WORKER_PROPOSAL_SCENARIO_LIMIT
            if not (
                len(self.scenario_ids)
                == len(self.scenario_digests)
                == len(self.scenario_mask)
                == scenario_limit
            ):
                raise ValueError("candidate ranker scenario identity has the wrong shape")
            if not (
                len(self.scenario_features)
                == len(self.scenario_feature_mask)
                == len(self.candidate_scenario_mask)
                == size
            ):
                raise ValueError("candidate ranker scenario rows have the wrong shape")
            scenario_width = len(CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES)
            for rows, masks, row_mask in zip(
                self.scenario_features,
                self.scenario_feature_mask,
                self.candidate_scenario_mask,
            ):
                if not (
                    len(rows) == len(masks) == len(row_mask) == scenario_limit
                ):
                    raise ValueError("candidate ranker scenario vector has the wrong shape")
                if any(len(row) != scenario_width for row in rows) or any(
                    len(row) != scenario_width for row in masks
                ):
                    raise ValueError("candidate ranker scenario feature width mismatch")
            if self.shared_context is None:
                raise ValueError("candidate ranker v2 requires shared context")
            if (
                self.scenario_ids != self.shared_context.scenario_ids
                or self.scenario_digests != self.shared_context.scenario_digests
                or self.scenario_mask != self.shared_context.scenario_mask
            ):
                raise ValueError("candidate ranker/shared scenario identity mismatch")
        else:
            raise ValueError(
                f"unsupported candidate ranker input schema: {self.schema_version}"
            )
        if any(len(row) != expected_width for row in self.features):
            raise ValueError("candidate ranker feature width does not match its contract")
        if self.schema_hash and self.schema_hash != expected_hash:
            raise ValueError(
                f"candidate ranker schema hash mismatch: expected {expected_hash}, "
                f"got {self.schema_hash}"
            )
        if self.selected_index is not None:
            if not 0 <= self.selected_index < size:
                raise ValueError("candidate ranker selected index is out of range")
            if not self.candidate_mask[self.selected_index]:
                raise ValueError("candidate ranker selected index must be unmasked")
        _validate_json_finite(self.to_dict(), path="candidate_ranker_input")

    def to_dict(self) -> dict[str, Any]:
        if self.schema_version == CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION:
            return {
                "schema_version": self.schema_version,
                "proposal_id": self.proposal_id,
                "dtype": "float32",
                "feature_names": list(CANDIDATE_RANKER_V1_FEATURE_NAMES),
                "candidate_ids": list(self.candidate_ids),
                "features": [list(row) for row in self.features],
                "candidate_mask": list(self.candidate_mask),
                "legal_action_mask": list(self.legal_action_mask),
                "selected_index": self.selected_index,
            }
        return {
            "schema_version": self.schema_version,
            "schema_hash": CANDIDATE_RANKER_SCHEMA_HASH,
            "proposal_id": self.proposal_id,
            "dtype": "float32",
            "feature_names": list(CANDIDATE_RANKER_FEATURE_NAMES),
            "normalization": _normalization_metadata(
                CANDIDATE_RANKER_V2_FEATURE_SPECS
            ),
            "candidate_ids": list(self.candidate_ids),
            "features": [list(row) for row in self.features],
            "feature_mask": [list(row) for row in self.feature_mask],
            "candidate_mask": list(self.candidate_mask),
            "legal_action_mask": list(self.legal_action_mask),
            "selected_index": self.selected_index,
            "scenarios": {
                "limit": WORKER_PROPOSAL_SCENARIO_LIMIT,
                "ids": list(self.scenario_ids),
                "sequence_digests": list(self.scenario_digests),
                "mask": list(self.scenario_mask),
                "feature_names": list(
                    CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES
                ),
                "normalization": _normalization_metadata(
                    CANDIDATE_RANKER_SCENARIO_FEATURE_SPECS
                ),
                "features": [
                    [list(row) for row in candidate]
                    for candidate in self.scenario_features
                ],
                "feature_mask": [
                    [list(row) for row in candidate]
                    for candidate in self.scenario_feature_mask
                ],
                "candidate_mask": [
                    list(row) for row in self.candidate_scenario_mask
                ],
            },
            "shared_context": self.shared_context.to_dict(),  # type: ignore[union-attr]
        }

    @property
    def deterministic_digest(self) -> str:
        payload = self.to_dict()
        if self.schema_version == CANDIDATE_RANKER_INPUT_SCHEMA_VERSION:
            payload["shared_context"] = self.shared_context.deterministic_dict()  # type: ignore[union-attr]
        return _canonical_digest(payload, prefix="ranker-input")


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
    shared_context: WorkerProposalSharedContext | None = None
    schema_version: str = WORKER_PROPOSAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in {
            WORKER_PROPOSAL_V1_SCHEMA_VERSION,
            WORKER_PROPOSAL_SCHEMA_VERSION,
        }:
            raise ValueError(f"unsupported worker proposal schema: {self.schema_version}")
        expected_candidate_schema = (
            WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION
            if self.schema_version == WORKER_PROPOSAL_SCHEMA_VERSION
            else WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION
        )
        if self.schema_version == WORKER_PROPOSAL_SCHEMA_VERSION:
            if self.shared_context is None:
                raise ValueError("worker proposal v2 requires shared context")
            if not math.isclose(
                self.search_latency_ms,
                self.shared_context.elapsed_ms,
                rel_tol=0.0,
                abs_tol=1.0e-9,
            ) or self.expanded_nodes != self.shared_context.expanded_nodes:
                raise ValueError("worker proposal costs disagree with shared context")
        elif self.shared_context is not None:
            raise ValueError("worker proposal v1 cannot contain v2 shared context")
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
            if candidate.schema_version != expected_candidate_schema:
                raise ValueError("worker proposal candidate schema does not match batch")
            if candidate.rank != index:
                raise ValueError("worker proposal ranks must be contiguous")
            if not self.legal_action_mask[candidate.root_action]:
                raise ValueError("worker proposal contains an illegal root action")
            if (
                self.shared_context is not None
                and candidate.evidence is not None
                and candidate.evidence.scenario_digest
                != self.shared_context.scenario_digest
            ):
                raise ValueError("candidate/shared scenario digest mismatch")
        if self.search_latency_ms < 0.0 or self.expanded_nodes < 0:
            raise ValueError("worker proposal search costs must be non-negative")
        expected_id = _batch_id(
            self.decision_id,
            self.profile_id,
            self.candidate_limit,
            actual,
            schema_version=self.schema_version,
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
        if self.schema_version == WORKER_PROPOSAL_V1_SCHEMA_VERSION:
            zero_row = (0.0,) * len(CANDIDATE_RANKER_V1_FEATURE_NAMES)
            return CandidateRankerInput(
                proposal_id=self.proposal_id,
                candidate_ids=tuple(
                    None if candidate is None else candidate.candidate_id
                    for candidate in self.candidates
                ),
                features=tuple(
                    zero_row if candidate is None else candidate.ranker_v1_features
                    for candidate in self.candidates
                ),
                candidate_mask=self.candidate_mask,
                legal_action_mask=self.legal_action_mask,
                selected_index=self.selected_index,
                schema_hash=CANDIDATE_RANKER_V1_SCHEMA_HASH,
                schema_version=CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION,
            )
        zero_row = (0.0,) * len(CANDIDATE_RANKER_FEATURE_NAMES)
        false_row = (False,) * len(CANDIDATE_RANKER_FEATURE_NAMES)
        scenario_zero = tuple(
            (0.0,) * len(CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES)
            for _ in range(WORKER_PROPOSAL_SCENARIO_LIMIT)
        )
        scenario_false = tuple(
            (False,) * len(CANDIDATE_RANKER_SCENARIO_FEATURE_NAMES)
            for _ in range(WORKER_PROPOSAL_SCENARIO_LIMIT)
        )
        candidate_scenario_false = (False,) * WORKER_PROPOSAL_SCENARIO_LIMIT
        rows = []
        feature_masks = []
        scenario_rows = []
        scenario_feature_masks = []
        candidate_scenario_masks = []
        ranker_mask = []
        for candidate in self.candidates:
            if candidate is None:
                rows.append(zero_row)
                feature_masks.append(false_row)
                scenario_rows.append(scenario_zero)
                scenario_feature_masks.append(scenario_false)
                candidate_scenario_masks.append(candidate_scenario_false)
                ranker_mask.append(False)
                continue
            row, row_mask = candidate.ranker_v2_row
            candidate_rows, candidate_masks, candidate_scenarios = (
                candidate.scenario_ranker_rows
            )
            rows.append(row)
            feature_masks.append(row_mask)
            scenario_rows.append(candidate_rows)
            scenario_feature_masks.append(candidate_masks)
            candidate_scenario_masks.append(candidate_scenarios)
            ranker_mask.append(
                bool(
                    candidate.ranker_eligible
                    and self.legal_action_mask[candidate.root_action]
                )
            )
        ranker_mask_tuple = tuple(ranker_mask)
        ranker_selected = (
            self.selected_index
            if self.selected_index is not None
            and ranker_mask_tuple[self.selected_index]
            else None
        )
        context = self.shared_context
        if context is None:  # pragma: no cover - guarded by __post_init__
            raise RuntimeError("worker proposal v2 shared context is missing")
        return CandidateRankerInput(
            proposal_id=self.proposal_id,
            candidate_ids=tuple(
                None if candidate is None else candidate.candidate_id
                for candidate in self.candidates
            ),
            features=tuple(rows),
            feature_mask=tuple(feature_masks),
            candidate_mask=ranker_mask_tuple,
            legal_action_mask=self.legal_action_mask,
            selected_index=ranker_selected,
            scenario_ids=context.scenario_ids,
            scenario_digests=context.scenario_digests,
            scenario_mask=context.scenario_mask,
            scenario_features=tuple(scenario_rows),
            scenario_feature_mask=tuple(scenario_feature_masks),
            candidate_scenario_mask=tuple(candidate_scenario_masks),
            shared_context=context,
            schema_hash=CANDIDATE_RANKER_SCHEMA_HASH,
            schema_version=CANDIDATE_RANKER_INPUT_SCHEMA_VERSION,
        )

    @property
    def compatibility_ranker_input(self) -> CandidateRankerInput:
        """Project v2 explicitly to the stable v1 feature order."""

        if self.schema_version == WORKER_PROPOSAL_V1_SCHEMA_VERSION:
            return self.ranker_input
        zero_row = (0.0,) * len(CANDIDATE_RANKER_V1_FEATURE_NAMES)
        eligible_mask = self.ranker_input.candidate_mask
        selected_index = (
            self.selected_index
            if self.selected_index is not None
            and eligible_mask[self.selected_index]
            else None
        )
        return CandidateRankerInput(
            proposal_id=self.proposal_id,
            candidate_ids=tuple(
                None if candidate is None else candidate.candidate_id
                for candidate in self.candidates
            ),
            features=tuple(
                zero_row if candidate is None else candidate.ranker_v1_features
                for candidate in self.candidates
            ),
            candidate_mask=eligible_mask,
            legal_action_mask=self.legal_action_mask,
            selected_index=selected_index,
            schema_hash=CANDIDATE_RANKER_V1_SCHEMA_HASH,
            schema_version=CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION,
        )

    @property
    def compatibility_projection(self) -> dict[str, Any]:
        projected = self.compatibility_ranker_input
        false_row = (False,) * len(CANDIDATE_RANKER_V1_FEATURE_NAMES)
        feature_masks = [
            list(false_row if candidate is None else candidate.ranker_v1_feature_mask)
            for candidate in self.candidates
        ]
        payload = {
            "schema_version": CANDIDATE_RANKER_COMPAT_PROJECTION_SCHEMA_VERSION,
            "source_schema_version": CANDIDATE_RANKER_INPUT_SCHEMA_VERSION,
            "source_schema_hash": CANDIDATE_RANKER_SCHEMA_HASH,
            "target_schema_version": CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION,
            "target_schema_hash": CANDIDATE_RANKER_V1_SCHEMA_HASH,
            "lossless": False,
            "feature_names": list(CANDIDATE_RANKER_V1_FEATURE_NAMES),
            "normalization": _normalization_metadata(
                CANDIDATE_RANKER_V1_FEATURE_SPECS
            ),
            "feature_mask": feature_masks,
            "missing_feature_mask": [
                [not present for present in row] for row in feature_masks
            ],
            "dropped_namespaces": [
                "expected_chain",
                "structural_chain",
                "trajectory_status",
                "scenario_vector",
                "shared_context",
            ],
            "input": projected.to_dict(),
        }
        payload["deterministic_digest"] = _canonical_digest(
            payload,
            prefix="ranker-compat-projection",
        )
        return payload

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
        allowed_mask = (
            self.ranker_input.candidate_mask
            if selection_mode == "learned_ranker"
            else self.candidate_mask
        )
        if not 0 <= selected_index < self.candidate_limit or not allowed_mask[selected_index]:
            raise ValueError("worker proposal selected index is masked")
        return replace(
            self,
            selected_index=int(selected_index),
            selection_mode=selection_mode,
        )

    def to_dict(self) -> dict[str, Any]:
        selected = self.selected_candidate
        payload = {
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
        }
        if self.schema_version == WORKER_PROPOSAL_V1_SCHEMA_VERSION:
            payload["search_cost"] = {
                "latency_ms": float(self.search_latency_ms),
                "expanded_nodes": int(self.expanded_nodes),
                "latency_kind": "observational_wall_clock",
                "budget_kind": "deterministic_expanded_nodes",
            }
        else:
            payload["shared_context"] = self.shared_context.to_dict()  # type: ignore[union-attr]
            payload["compatibility_projection"] = self.compatibility_projection
        return payload

    def deterministic_dict(self) -> dict[str, Any]:
        """Return the contract payload with wall-clock observations neutralized."""

        payload = self.to_dict()
        payload["telemetry"]["search_latency_ms"] = 0.0
        if self.schema_version == WORKER_PROPOSAL_V1_SCHEMA_VERSION:
            payload["search_cost"]["latency_ms"] = 0.0
            latency_index = CANDIDATE_RANKER_V1_FEATURE_NAMES.index(
                "search_latency_ms"
            )
            for row in payload["ranker_input"]["features"]:
                row[latency_index] = 0.0
            for candidate in payload["candidates"]:
                if candidate is not None:
                    candidate["preview"]["search_cost"]["latency_ms"] = 0.0
            return payload
        deterministic_context = self.shared_context.deterministic_dict()  # type: ignore[union-attr]
        payload["shared_context"] = copy.deepcopy(deterministic_context)
        payload["ranker_input"]["shared_context"] = copy.deepcopy(
            deterministic_context
        )
        projection = payload["compatibility_projection"]
        latency_index = CANDIDATE_RANKER_V1_FEATURE_NAMES.index(
            "search_latency_ms"
        )
        for row in projection["input"]["features"]:
            row[latency_index] = 0.0
        projection_without_digest = dict(projection)
        projection_without_digest.pop("deterministic_digest", None)
        projection["deterministic_digest"] = _canonical_digest(
            projection_without_digest,
            prefix="ranker-compat-projection",
        )
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
        schema_version = str(payload.get("schema_version", ""))
        profile = payload.get("profile")
        selection = payload.get("selection")
        masks = payload.get("masks")
        if not all(isinstance(item, Mapping) for item in (profile, selection, masks)):
            raise ValueError("worker proposal profile/selection/masks must be mappings")
        if schema_version == WORKER_PROPOSAL_SCHEMA_VERSION:
            shared = payload.get("shared_context")
            if not isinstance(shared, Mapping):
                raise ValueError("worker proposal v2 shared context must be a mapping")
            shared_context = WorkerProposalSharedContext.from_dict(shared)
            search_latency_ms = shared_context.elapsed_ms
            expanded_nodes = shared_context.expanded_nodes
        elif schema_version == WORKER_PROPOSAL_V1_SCHEMA_VERSION:
            search = payload.get("search_cost", {})
            if not isinstance(search, Mapping):
                raise ValueError("worker proposal v1 search cost must be a mapping")
            shared_context = None
            search_latency_ms = float(search.get("latency_ms", 0.0))
            expanded_nodes = int(search.get("expanded_nodes", 0))
        else:
            raise ValueError(f"unsupported worker proposal schema: {schema_version}")
        candidates = tuple(
            None
            if candidate is None
            else WorkerProposalCandidate.from_dict(
                candidate,
                shared_context=shared_context,
            )
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
            search_latency_ms=search_latency_ms,
            expanded_nodes=expanded_nodes,
            shared_context=shared_context,
            schema_version=schema_version,
        )
        if int(payload.get("candidate_count", batch.candidate_count)) != batch.candidate_count:
            raise ValueError("serialized worker proposal candidate count is inconsistent")
        serialized_ranker = payload.get("ranker_input")
        if isinstance(serialized_ranker, Mapping) and dict(serialized_ranker) != batch.ranker_input.to_dict():
            raise ValueError("serialized worker proposal ranker input is inconsistent")
        serialized_projection = payload.get("compatibility_projection")
        if (
            schema_version == WORKER_PROPOSAL_SCHEMA_VERSION
            and isinstance(serialized_projection, Mapping)
            and dict(serialized_projection) != batch.compatibility_projection
        ):
            raise ValueError("serialized compatibility projection is inconsistent")
        return batch


def _batch_id(
    decision_id: str,
    profile_id: int,
    candidate_limit: int,
    candidates: Sequence[WorkerProposalCandidate],
    *,
    schema_version: str,
) -> str:
    return _canonical_digest(
        {
            "schema_version": schema_version,
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
            # Candidate identity is deliberately pinned to the v1 identity
            # contract so v1 -> v2 migration cannot change stable IDs.
            "schema_version": WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION,
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


def _canonical_scenario_context(
    scenario_budget: Mapping[str, Any] | None,
) -> tuple[
    tuple[int | None, ...],
    tuple[str | None, ...],
    tuple[bool, ...],
    tuple[Mapping[str, Any], ...],
    str,
]:
    budget = scenario_budget or {}
    raw_sequences = budget.get("scenario_sequences", ())
    sequences: list[dict[str, Any]] = []
    if isinstance(raw_sequences, Sequence) and not isinstance(
        raw_sequences, (str, bytes)
    ):
        sequences = [
            _mapping_copy(item)
            for item in raw_sequences
            if isinstance(item, Mapping)
        ]
    descriptors: list[tuple[int, str | None, dict[str, Any]]] = []
    if sequences:
        for sequence in sequences:
            if "scenario_id" not in sequence:
                raise ValueError("scenario sequence requires a scenario id")
            descriptors.append(
                (
                    int(sequence["scenario_id"]),
                    (
                        None
                        if sequence.get("sequence_digest") is None
                        else str(sequence["sequence_digest"])
                    ),
                    sequence,
                )
            )
    else:
        raw_ids = budget.get("scenario_ids", ())
        if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, (str, bytes)):
            for scenario_id in raw_ids:
                identifier = int(scenario_id)
                descriptors.append(
                    (
                        identifier,
                        None,
                        {"scenario_id": identifier, "sequence_digest": None},
                    )
                )
    descriptors.sort(key=lambda item: item[0])
    ids = [item[0] for item in descriptors]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario context contains duplicate ids")
    if len(descriptors) > WORKER_PROPOSAL_SCENARIO_LIMIT:
        raise ValueError("scenario context exceeds the fixed scenario limit")
    actual_ids: list[int | None] = [item[0] for item in descriptors]
    actual_digests: list[str | None] = [item[1] for item in descriptors]
    padding = WORKER_PROPOSAL_SCENARIO_LIMIT - len(descriptors)
    scenario_ids = tuple([*actual_ids, *([None] * padding)])
    scenario_digests = tuple([*actual_digests, *([None] * padding)])
    scenario_mask = tuple([*(True for _ in descriptors), *(False for _ in range(padding))])
    digest = _scenario_identity_digest(
        scenario_ids,
        scenario_digests,
        scenario_mask,
    )
    return (
        scenario_ids,
        scenario_digests,
        scenario_mask,
        tuple(item[2] for item in descriptors),
        digest,
    )


def _build_shared_context(
    *,
    profile_id: int,
    profile_name: str,
    strategy: str,
    candidate_limit: int,
    search_latency_ms: float,
    expanded_nodes: int,
    scenario_budget: Mapping[str, Any] | None,
    worker_deadline_status: Mapping[str, Any] | None,
) -> WorkerProposalSharedContext:
    budget = _mapping_copy(scenario_budget)
    (
        scenario_ids,
        scenario_digests,
        scenario_mask,
        scenario_sequences,
        scenario_digest,
    ) = _canonical_scenario_context(budget)
    search_profile = budget.get("profile")
    search_profile_payload = (
        _mapping_copy(search_profile)
        if isinstance(search_profile, Mapping)
        else {"name": str(budget.get("search_profile", "custom")), "version": None}
    )
    profile = {
        "id": int(profile_id),
        "name": str(profile_name),
        "strategy": str(strategy),
        "search": search_profile_payload,
        "backend": str(budget.get("search_backend", "simulator")),
    }
    search_config = {
        "depth": budget.get("depth"),
        "width": budget.get("width"),
        "scenario_count": int(
            budget.get("hidden_future_requested", sum(scenario_mask))
        ),
        "candidate_limit": int(candidate_limit),
        "max_expanded_nodes": budget.get("max_expanded_nodes"),
        "potential_probe_budget": budget.get("potential_probe_budget"),
        "build_potential_budget": _mapping_copy(
            budget.get("build_potential_budget")
            if isinstance(budget.get("build_potential_budget"), Mapping)
            else None
        ),
        "budget_authority": budget.get("budget_authority"),
        "wall_clock_mode": budget.get("wall_clock_mode"),
        "minimum_chain_count": budget.get("minimum_chain_count"),
        "terminal_fire": _mapping_copy(
            budget.get("terminal_fire")
            if isinstance(budget.get("terminal_fire"), Mapping)
            else None
        ),
        "transposition_table": _mapping_copy(
            budget.get("transposition_table")
            if isinstance(budget.get("transposition_table"), Mapping)
            else None
        ),
    }
    search_totals = {
        "expanded_nodes": max(0, int(expanded_nodes)),
        "generated_nodes": max(0, int(budget.get("generated_nodes", 0))),
        "pruned_nodes": max(0, int(budget.get("pruned_nodes", 0))),
        "transposition_hits": max(
            0,
            int(
                budget.get(
                    "transposition_hits",
                    _nested(budget, "transposition_table.hits", 0),
                )
            ),
        ),
        "potential_probe_count": max(
            0,
            int(budget.get("potential_probe_count", 0)),
        ),
        "potential_cache_hits": max(
            0,
            int(budget.get("potential_cache_hits", 0)),
        ),
        "budget_exhausted": bool(budget.get("budget_exhausted", False)),
        "truncation_reason": budget.get("truncation_reason"),
        "reached_depth": max(0, int(budget.get("reached_depth", 0))),
    }
    elapsed = max(0.0, float(search_latency_ms))
    latency = {
        "measurement_interval": "decision_search_and_projection",
        "sample_count": 1,
        "elapsed_ms": elapsed,
        "p50_ms": elapsed,
        "p95_ms": elapsed,
        "kind": "observational_wall_clock",
    }
    deadline = _mapping_copy(worker_deadline_status)
    if not deadline:
        deadline = {
            "status": "not_configured",
            "budget_ms": None,
            "overrun": False,
        }
    return WorkerProposalSharedContext(
        profile=profile,
        known_queue_length=max(0, int(budget.get("known_pair_count", 0))),
        scenario_ids=scenario_ids,
        scenario_digests=scenario_digests,
        scenario_mask=scenario_mask,
        scenario_sequences=scenario_sequences,
        scenario_digest=scenario_digest,
        search_config=search_config,
        search_totals=search_totals,
        latency=latency,
        worker_deadline=deadline,
    )


def _evidence_status(
    expected_chain: Mapping[str, Any] | None,
    *,
    legacy: bool = False,
) -> EvidenceStatus:
    if expected_chain is None:
        return (
            EvidenceStatus.LEGACY_MISSING
            if legacy
            else EvidenceStatus.NOT_EVALUATED
        )
    coverage = float(expected_chain.get("coverage", 0.0))
    scenario_values = expected_chain.get("scenario_values", ())
    truncated = False
    if isinstance(scenario_values, Sequence) and not isinstance(
        scenario_values, (str, bytes)
    ):
        truncated = any(
            isinstance(item, Mapping)
            and (
                item.get("truncation_reason") is not None
                or not bool(item.get("search_complete", False))
            )
            for item in scenario_values
        )
    if coverage < 1.0 or truncated:
        return EvidenceStatus.BUDGET_EXHAUSTED
    return EvidenceStatus.EVALUATED


def _masked_numeric(
    value: Any,
    present: bool,
    *,
    status: EvidenceStatus,
    evaluated: bool,
) -> MaskedNumeric:
    if status == EvidenceStatus.LEGACY_MISSING:
        value = None
        present = False
        evaluated = False
    return MaskedNumeric(
        value=None if not present or value is None else float(value),
        is_present=bool(present and value is not None),
        evaluated=bool(evaluated),
        status=status,
    )


def _status_for_source(value: Mapping[str, Any] | None) -> EvidenceStatus:
    if value is None:
        return EvidenceStatus.NOT_EVALUATED
    source_status = str(value.get("evaluation_status", "not_evaluated"))
    if source_status == "budget_exhausted":
        return EvidenceStatus.BUDGET_EXHAUSTED
    if source_status in {"available", "not_found", "legacy_partial"}:
        return EvidenceStatus.EVALUATED
    return EvidenceStatus.NOT_EVALUATED


def _build_candidate_evidence(
    raw: DiverseBeamCandidate | None,
    *,
    build_potential: Mapping[str, Any],
    shared_context: WorkerProposalSharedContext,
    best_chain_depth: int,
    source_schema_version: str,
    legacy: bool = False,
) -> CandidateEvidence:
    expected_raw = None if raw is None else getattr(raw, "expected_chain_evidence", None)
    structural_raw = (
        None if raw is None else getattr(raw, "chain_structure_evaluation", None)
    )
    expected = (
        _mapping_copy(expected_raw)
        if isinstance(expected_raw, Mapping) and expected_raw
        else None
    )
    structural = (
        _mapping_copy(structural_raw)
        if isinstance(structural_raw, Mapping) and structural_raw
        else None
    )
    status = _evidence_status(expected, legacy=legacy)
    expected_evaluated = status in {
        EvidenceStatus.EVALUATED,
        EvidenceStatus.BUDGET_EXHAUSTED,
    }
    best_fire = expected.get("best_fire") if expected is not None else None
    best_fire_payload = best_fire if isinstance(best_fire, Mapping) else {}
    best_chain_count = int(best_fire_payload.get("chain_count", 0))
    target_chain = int(
        shared_context.search_config.get("minimum_chain_count") or 0
    )
    terminal_fire = bool(best_fire_payload.get("terminal", False))
    trajectory = {
        "max_chain_depth": max(0, int(best_chain_depth)),
        "terminal_fire": terminal_fire,
        "terminal_fire_reason": best_fire_payload.get("terminal_reason"),
        "premature_fire": bool(
            best_chain_count > 0
            and target_chain > 0
            and best_chain_count < target_chain
        ),
        "target_fire": bool(target_chain > 0 and best_chain_count >= target_chain),
        "best_fire_chain_count": best_chain_count,
    }
    build_status = _status_for_source(build_potential)
    build_evaluated = build_status in {
        EvidenceStatus.EVALUATED,
        EvidenceStatus.BUDGET_EXHAUSTED,
    }
    build_search = build_potential.get("search")
    build_search_payload = build_search if isinstance(build_search, Mapping) else {}
    build_potential_status = {
        "status": build_status.value,
        "source_status": build_potential.get("evaluation_status"),
        "evaluated": build_evaluated,
        "budget_exhausted": build_status == EvidenceStatus.BUDGET_EXHAUSTED,
        "truncation_reason": build_search_payload.get("truncation_reason"),
        "search_complete": bool(build_search_payload.get("complete", False)),
    }
    numeric_fields: dict[str, MaskedNumeric] = {}

    def add(
        name: str,
        container: Mapping[str, Any] | None,
        path: str,
        *,
        field_status: EvidenceStatus,
        evaluated: bool,
    ) -> None:
        value, present = (
            (None, False)
            if container is None
            else _nested_value(container, path)
        )
        numeric_fields[name] = _masked_numeric(
            value,
            present,
            status=field_status,
            evaluated=evaluated,
        )

    expected_paths = {
        "expected_chain.score.sum": "chain_score.sum",
        "expected_chain.score.mean": "chain_score.mean",
        "expected_chain.score.worst": "chain_score.worst",
        "expected_chain.score.dispersion": "chain_score.dispersion",
        "expected_chain.score.maximum": "chain_score.maximum",
        "expected_chain.count.sum": "chain_count.sum",
        "expected_chain.count.mean": "chain_count.mean",
        "expected_chain.count.worst": "chain_count.worst",
        "expected_chain.count.dispersion": "chain_count.dispersion",
        "expected_chain.count.maximum": "chain_count.maximum",
        "expected_chain.support": "support",
        "expected_chain.coverage": "coverage",
    }
    for name, path in expected_paths.items():
        add(
            name,
            expected,
            path,
            field_status=status,
            evaluated=expected_evaluated,
        )
    trajectory_fields = {
        "trajectory.max_chain_depth": "max_chain_depth",
        "trajectory.terminal_fire": "terminal_fire",
        "trajectory.premature_fire": "premature_fire",
        "trajectory.target_fire": "target_fire",
    }
    for name, path in trajectory_fields.items():
        add(
            name,
            trajectory,
            path,
            field_status=status,
            evaluated=expected_evaluated,
        )
    structural_status = _status_for_source(structural)
    structural_evaluated = structural_status in {
        EvidenceStatus.EVALUATED,
        EvidenceStatus.BUDGET_EXHAUSTED,
    }
    structural_paths = {
        "structural.score": "score",
        "structural.potential_chain_count": "features.trigger.potential_chain_count",
        "structural.required_key_count": "features.trigger.required_key_count",
        "structural.trigger_height": "features.trigger.height",
        "structural.connectivity_edges": "features.components.connectivity_edges",
        "structural.connection_candidates": "features.components.connection_candidates",
        "structural.growth_sites": "features.shape.growth_sites",
        "structural.remaining_connection_edges": "features.trigger.remaining_connection_edges",
        "structural.danger": "features.danger.ratio",
        "structural.tear": "action.tear_count",
        "structural.waste": "action.waste_count",
        "structural.trigger_damage": "action.trigger_damage",
    }
    for name, path in structural_paths.items():
        add(
            name,
            structural,
            path,
            field_status=structural_status,
            evaluated=structural_evaluated,
        )
    build_paths = {
        "build_potential.predicted_chain_count": "predicted_chain_count",
        "build_potential.predicted_chain_potential": "predicted_chain_potential",
        "build_potential.required_puyos": "required_puyos",
    }
    for name, path in build_paths.items():
        add(
            name,
            build_potential,
            path,
            field_status=build_status,
            evaluated=build_evaluated,
        )
    numeric_fields["build_potential.evaluated"] = _masked_numeric(
        int(build_evaluated),
        not legacy,
        status=(EvidenceStatus.LEGACY_MISSING if legacy else build_status),
        evaluated=build_evaluated,
    )
    numeric_fields["build_potential.budget_exhausted"] = _masked_numeric(
        int(build_status == EvidenceStatus.BUDGET_EXHAUSTED),
        not legacy,
        status=(EvidenceStatus.LEGACY_MISSING if legacy else build_status),
        evaluated=build_evaluated,
    )
    recoverability = None if raw is None else raw.trigger_recoverability.to_dict()
    add(
        "trigger_recoverability.recoverable",
        recoverability,
        "recoverable",
        field_status=status,
        evaluated=expected_evaluated,
    )

    expected_scenarios = expected.get("scenario_values", ()) if expected else ()
    by_id = {
        int(item["scenario_id"]): item
        for item in expected_scenarios
        if isinstance(item, Mapping) and "scenario_id" in item
    }
    scenario_values: list[Mapping[str, Any] | None] = []
    for scenario_id, sequence_digest, real in zip(
        shared_context.scenario_ids,
        shared_context.scenario_digests,
        shared_context.scenario_mask,
    ):
        source = None if scenario_id is None else by_id.get(int(scenario_id))
        if not real or source is None:
            scenario_values.append(None)
            continue
        scenario_values.append(
            {
                "scenario_id": int(scenario_id),
                "sequence_digest": sequence_digest,
                "source": _mapping_copy(source),
            }
        )
    if legacy:
        numeric_fields = {
            name: MaskedNumeric(
                value=None,
                is_present=False,
                evaluated=False,
                status=EvidenceStatus.LEGACY_MISSING,
            )
            for name in numeric_fields
        }
        scenario_values = [None] * WORKER_PROPOSAL_SCENARIO_LIMIT
        build_potential_status = {
            "status": EvidenceStatus.LEGACY_MISSING.value,
            "source_status": None,
            "evaluated": False,
            "budget_exhausted": False,
            "truncation_reason": None,
            "search_complete": False,
        }
    return CandidateEvidence(
        status=status,
        expected_chain=expected,
        structural_chain=structural,
        trajectory=trajectory,
        build_potential_status=build_potential_status,
        numeric_fields=numeric_fields,
        scenario_values=tuple(scenario_values),
        scenario_mask=tuple(value is not None for value in scenario_values),
        scenario_digest=shared_context.scenario_digest,
        source_schema_version=source_schema_version,
    )


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
    shared_context: WorkerProposalSharedContext | None,
    schema_version: str,
) -> WorkerProposalCandidate:
    sequence = tuple(int(action) for action in raw.plan) or (int(raw.action),)
    runtime = _simulate_action_sequence(
        simulator,
        sequence,
        score_carry=score_carry,
        incoming_attack=incoming_attack,
    )
    style = getattr(raw, "chain_style_evaluation", None)
    build_potential = raw.build_potential.to_dict()
    candidate_schema = (
        WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION
        if schema_version == WORKER_PROPOSAL_SCHEMA_VERSION
        else WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION
    )
    evidence = (
        None
        if shared_context is None
        else _build_candidate_evidence(
            raw,
            build_potential=build_potential,
            shared_context=shared_context,
            best_chain_depth=int(raw.best_chain_depth),
            source_schema_version=str(raw.schema_version),
        )
    )
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
        build_potential=build_potential,
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
        evidence=evidence,
        schema_version=candidate_schema,
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
    shared_context: WorkerProposalSharedContext | None,
    schema_version: str,
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
    build_potential_payload = (
        _mapping_copy(build_potential)
        if isinstance(build_potential, Mapping)
        else {}
    )
    candidate_schema = (
        WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION
        if schema_version == WORKER_PROPOSAL_SCHEMA_VERSION
        else WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION
    )
    evidence = (
        None
        if shared_context is None
        else _build_candidate_evidence(
            None,
            build_potential=build_potential_payload,
            shared_context=shared_context,
            best_chain_depth=1,
            source_schema_version="compatibility_selection",
        )
    )
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
        build_potential=build_potential_payload,
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
        evidence=evidence,
        schema_version=candidate_schema,
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
    worker_deadline_status: Mapping[str, Any] | None = None,
    schema_version: str = WORKER_PROPOSAL_SCHEMA_VERSION,
) -> WorkerProposalBatch:
    """Normalize raw beam candidates into a fixed K, ranker-ready artifact."""

    limit = int(candidate_limit)
    if limit <= 0:
        raise ValueError("worker proposal candidate limit must be positive")
    if schema_version not in {
        WORKER_PROPOSAL_V1_SCHEMA_VERSION,
        WORKER_PROPOSAL_SCHEMA_VERSION,
    }:
        raise ValueError(f"unsupported worker proposal schema: {schema_version}")
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
    shared_context = (
        _build_shared_context(
            profile_id=profile_id,
            profile_name=profile_name,
            strategy=strategy,
            candidate_limit=limit,
            search_latency_ms=search_latency_ms,
            expanded_nodes=expanded_nodes,
            scenario_budget=scenario_budget,
            worker_deadline_status=worker_deadline_status,
        )
        if schema_version == WORKER_PROPOSAL_SCHEMA_VERSION
        else None
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
            shared_context=shared_context,
            schema_version=schema_version,
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
                shared_context=shared_context,
                schema_version=schema_version,
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
    proposal_id = _batch_id(
        decision_id,
        profile_id,
        limit,
        actual,
        schema_version=schema_version,
    )
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
        shared_context=shared_context,
        schema_version=schema_version,
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


def _migrate_v0_to_v1(value: Mapping[str, Any]) -> dict[str, Any]:
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
            schema_version=WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION,
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
        proposal_id=_batch_id(
            decision_id,
            profile_id,
            limit,
            actual,
            schema_version=WORKER_PROPOSAL_V1_SCHEMA_VERSION,
        ),
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
        schema_version=WORKER_PROPOSAL_V1_SCHEMA_VERSION,
    )
    return batch.to_dict()


def _candidate_as_v1(candidate: WorkerProposalCandidate) -> WorkerProposalCandidate:
    return replace(
        candidate,
        evidence=None,
        schema_version=WORKER_PROPOSAL_CANDIDATE_V1_SCHEMA_VERSION,
    )


def _candidate_as_legacy_missing_v2(
    candidate: WorkerProposalCandidate,
    shared_context: WorkerProposalSharedContext,
) -> WorkerProposalCandidate:
    evidence = _build_candidate_evidence(
        None,
        build_potential=candidate.build_potential,
        shared_context=shared_context,
        best_chain_depth=0,
        source_schema_version=candidate.schema_version,
        legacy=True,
    )
    return replace(
        candidate,
        evidence=evidence,
        schema_version=WORKER_PROPOSAL_CANDIDATE_SCHEMA_VERSION,
    )


def upgrade_worker_proposal_v1(
    batch: WorkerProposalBatch,
) -> WorkerProposalBatch:
    """Upgrade v1 without inventing evidence absent from that artifact."""

    if batch.schema_version != WORKER_PROPOSAL_V1_SCHEMA_VERSION:
        raise ValueError("worker proposal upgrade requires a v1 batch")
    scenario_ids = sorted(
        {
            int(scenario_id)
            for candidate in batch.candidates
            if candidate is not None
            for scenario_id in candidate.scenario_uncertainty.get(
                "scenario_ids",
                (),
            )
        }
    )
    shared_context = _build_shared_context(
        profile_id=batch.profile_id,
        profile_name=batch.profile_name,
        strategy=batch.strategy,
        candidate_limit=batch.candidate_limit,
        search_latency_ms=batch.search_latency_ms,
        expanded_nodes=batch.expanded_nodes,
        scenario_budget={
            "scenario_ids": scenario_ids,
            "hidden_future_requested": len(scenario_ids),
            "hidden_future_evaluated": 0,
            "uncertainty": "legacy_unknown",
            "search_backend": "legacy_v1_artifact",
            "truncation_reason": "legacy_missing",
        },
        worker_deadline_status={
            "status": "legacy_missing",
            "budget_ms": None,
            "overrun": False,
        },
    )
    candidates = tuple(
        None
        if candidate is None
        else _candidate_as_legacy_missing_v2(candidate, shared_context)
        for candidate in batch.candidates
    )
    actual = tuple(candidate for candidate in candidates if candidate is not None)
    return WorkerProposalBatch(
        proposal_id=_batch_id(
            batch.decision_id,
            batch.profile_id,
            batch.candidate_limit,
            actual,
            schema_version=WORKER_PROPOSAL_SCHEMA_VERSION,
        ),
        decision_id=batch.decision_id,
        profile_id=batch.profile_id,
        profile_name=batch.profile_name,
        strategy=batch.strategy,
        candidate_limit=batch.candidate_limit,
        candidates=candidates,
        candidate_mask=batch.candidate_mask,
        legal_action_mask=batch.legal_action_mask,
        selected_index=batch.selected_index,
        selection_mode=batch.selection_mode,
        fallback_reason=batch.fallback_reason,
        search_latency_ms=batch.search_latency_ms,
        expanded_nodes=batch.expanded_nodes,
        shared_context=shared_context,
        schema_version=WORKER_PROPOSAL_SCHEMA_VERSION,
    )


def project_worker_proposal_v1(
    batch: WorkerProposalBatch,
) -> WorkerProposalBatch:
    """Create the explicit, lossy v2 -> v1 compatibility projection."""

    if batch.schema_version == WORKER_PROPOSAL_V1_SCHEMA_VERSION:
        return batch
    if batch.schema_version != WORKER_PROPOSAL_SCHEMA_VERSION:
        raise ValueError("unsupported worker proposal projection source")
    candidates = tuple(
        None if candidate is None else _candidate_as_v1(candidate)
        for candidate in batch.candidates
    )
    actual = tuple(candidate for candidate in candidates if candidate is not None)
    return WorkerProposalBatch(
        proposal_id=_batch_id(
            batch.decision_id,
            batch.profile_id,
            batch.candidate_limit,
            actual,
            schema_version=WORKER_PROPOSAL_V1_SCHEMA_VERSION,
        ),
        decision_id=batch.decision_id,
        profile_id=batch.profile_id,
        profile_name=batch.profile_name,
        strategy=batch.strategy,
        candidate_limit=batch.candidate_limit,
        candidates=candidates,
        candidate_mask=batch.candidate_mask,
        legal_action_mask=batch.legal_action_mask,
        selected_index=batch.selected_index,
        selection_mode=batch.selection_mode,
        fallback_reason=batch.fallback_reason,
        search_latency_ms=batch.search_latency_ms,
        expanded_nodes=batch.expanded_nodes,
        schema_version=WORKER_PROPOSAL_V1_SCHEMA_VERSION,
    )


def migrate_worker_proposal_payload(
    value: Mapping[str, Any],
    *,
    target_schema_version: str | None = None,
    allow_lossy_projection: bool = False,
) -> dict[str, Any]:
    """Dispatch v0/v1/v2 and require explicit loss when downgrading v2."""

    if not isinstance(value, Mapping):
        raise ValueError("worker proposal payload must be a mapping")
    schema = value.get("schema_version")
    if schema == LEGACY_WORKER_PROPOSAL_SCHEMA_VERSION:
        payload = _migrate_v0_to_v1(value)
        schema = WORKER_PROPOSAL_V1_SCHEMA_VERSION
    elif schema in {
        WORKER_PROPOSAL_V1_SCHEMA_VERSION,
        WORKER_PROPOSAL_SCHEMA_VERSION,
    }:
        payload = copy.deepcopy(dict(value))
    else:
        raise ValueError(f"unsupported worker proposal schema: {schema}")
    if target_schema_version is None or target_schema_version == schema:
        return payload
    if target_schema_version == WORKER_PROPOSAL_SCHEMA_VERSION:
        if schema != WORKER_PROPOSAL_V1_SCHEMA_VERSION:
            raise ValueError("worker proposal v2 upgrade requires v1 input")
        return upgrade_worker_proposal_v1(
            WorkerProposalBatch.from_dict(payload)
        ).to_dict()
    if target_schema_version == WORKER_PROPOSAL_V1_SCHEMA_VERSION:
        if schema != WORKER_PROPOSAL_SCHEMA_VERSION:
            raise ValueError("worker proposal v1 projection requires v2 input")
        if not allow_lossy_projection:
            raise ValueError(
                "worker proposal v2 -> v1 is lossy; use explicit compatibility projection"
            )
        return project_worker_proposal_v1(
            WorkerProposalBatch.from_dict(payload)
        ).to_dict()
    raise ValueError(
        f"unsupported worker proposal migration target: {target_schema_version}"
    )


def ranker_input_for_model(
    batch: WorkerProposalBatch,
    model_contract: Mapping[str, Any],
    *,
    allow_compatibility_projection: bool = False,
) -> CandidateRankerInput:
    """Validate model schema/hash before exposing candidate tensors."""

    schema_version = str(model_contract.get("schema_version", ""))
    schema_hash = str(model_contract.get("schema_hash", ""))
    direct = batch.ranker_input
    direct_hash = candidate_ranker_schema_metadata(direct.schema_version)[
        "schema_hash"
    ]
    if schema_version == direct.schema_version and schema_hash == direct_hash:
        return direct
    if (
        allow_compatibility_projection
        and batch.schema_version == WORKER_PROPOSAL_SCHEMA_VERSION
        and schema_version == CANDIDATE_RANKER_INPUT_V1_SCHEMA_VERSION
        and schema_hash == CANDIDATE_RANKER_V1_SCHEMA_HASH
    ):
        return batch.compatibility_ranker_input
    raise ValueError(
        "candidate ranker schema mismatch: "
        f"model={schema_version}/{schema_hash}, "
        f"input={direct.schema_version}/{direct_hash}"
    )
