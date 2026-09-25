"""Strict public next-generation contracts; no search, policy, or checkpoint loading.

All values are frozen, nested inputs are copied, and JSON dispatch is versioned.
The public snapshot is a transport allowlist, not a proof of information visibility;
its producer must enforce the public-history boundary (PUYO-248).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import types
from functools import lru_cache
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from typing import ClassVar, Literal, Union, get_args, get_origin, get_type_hints

from puyo_env.actions import NUM_ACTIONS
from src.core.constants import NORMAL_PUYO_COLORS, PuyoColor

REQUEST_SCHEMA_VERSION = "puyo.nextgen.request.v1"
LEGACY_CANDIDATE_BATCH_SCHEMA_VERSION = "puyo.nextgen.candidate_batch.v1"
PRE_SURVIVAL_CANDIDATE_BATCH_SCHEMA_VERSION = "puyo.nextgen.candidate_batch.v2"
CANDIDATE_BATCH_SCHEMA_VERSION = "puyo.nextgen.candidate_batch.v3"
FEATURE_SCHEMA_VERSION = "puyo.nextgen.features.v1"
SELECTION_SCHEMA_VERSION = "puyo.nextgen.selection.v1"
DIAGNOSTICS_SCHEMA_VERSION = "puyo.nextgen.diagnostics.v1"
POLICY_ID = "nextgen_tactic_manager"
# Wire colors are independent of Enum.value: 0 empty, 1..N normal, N+1 garbage.
PUBLIC_COLOR_IDS = tuple(range(1, len(NORMAL_PUYO_COLORS) + 1))
PUBLIC_GARBAGE_ID = len(NORMAL_PUYO_COLORS) + 1
PUBLIC_CELL_TO_COLOR = (PuyoColor.EMPTY, *NORMAL_PUYO_COLORS, PuyoColor.OJAMA)
TACTIC_IDS = (
    "build_main",
    "build_template",
    "fire_main",
    "cancel",
    "counter",
    "decisive_short_attack",
)
TacticId = Literal[
    "build_main",
    "build_template",
    "fire_main",
    "cancel",
    "counter",
    "decisive_short_attack",
]
EvidenceStatus = Literal["evaluated", "partial", "not_evaluated", "unsupported"]
EvidenceSource = Literal["visible_exact", "public_estimate", "sampled_future"]
MaskReason = Literal[
    "available",
    "no_legal_root",
    "no_reachable_root",
    "inactive_phase",
    "no_public_threat",
    "deadline_unreachable",
    "trigger_blocked",
    "not_found_within_budget",
    "not_evaluated",
    "unsupported",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _json_value(value):
    if is_dataclass(value):
        return {f.name: _json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [_json_value(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _json_value(v) for k, v in value.items()}
    return value


def semantic_digest(value) -> str:
    """Canonical JSON SHA-256; non-finite values are never serializable."""
    return hashlib.sha256(
        json.dumps(
            _json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


TACTIC_REGISTRY_HASH = semantic_digest({"version": 1, "tactic_ids": TACTIC_IDS})


def _digest(value: str) -> None:
    _require(
        re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "expected lowercase SHA-256 digest",
    )


def _identifier(value: str) -> None:
    _require(bool(value.strip()) and len(value) <= 256, "invalid identifier")


def _action(value: int) -> None:
    _require(0 <= value < NUM_ACTIONS, "invalid root/plan action ID")


def _typed(value, annotation, path):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        _require(
            any(type(value) is type(a) and value == a for a in args),
            f"{path}: invalid ID/value {value!r}",
        )
    elif origin in (types.UnionType, Union):
        for item in args:
            try:
                return _typed(value, item, path)
            except ValueError:
                pass
        raise ValueError(f"{path}: invalid nullable/union value")
    elif origin is tuple:
        _require(type(value) in (tuple, list), f"{path}: expected array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_typed(v, args[0], path) for v in value)
        _require(len(value) == len(args), f"{path}: wrong array length")
        return tuple(_typed(v, a, path) for v, a in zip(value, args))
    elif isinstance(annotation, type) and issubclass(annotation, Contract):
        if isinstance(value, Mapping):
            return annotation.from_dict(value)
        _require(type(value) is annotation, f"{path}: expected {annotation.__name__}")
    elif annotation is float:
        _require(
            type(value) in (int, float) and math.isfinite(value),
            f"{path}: expected finite number",
        )
        return float(value)
    else:
        _require(type(value) is annotation, f"{path}: expected {annotation}")
    return value


@lru_cache(maxsize=64)
def _contract_hints(contract_type):
    # These module-owned frozen schema classes do not mutate annotations.
    # Cache only introspection; value/type/schema validation still runs below.
    return get_type_hints(contract_type)


class Contract:
    SCHEMA: ClassVar[str | None] = None
    READABLE_SCHEMAS: ClassVar[tuple[str, ...]] = ()

    def __post_init__(self):
        contract_type = type(self)
        hints = (_contract_hints(contract_type) if contract_type.__module__ == __name__
                 else get_type_hints(contract_type))
        for f in fields(self):
            object.__setattr__(
                self, f.name, _typed(getattr(self, f.name), hints[f.name], f.name)
            )
        if self.SCHEMA is not None:
            _require(self.schema_version in (self.SCHEMA, *self.READABLE_SCHEMAS), "unsupported schema version")
        self._validate()

    def _validate(self):
        pass

    def to_dict(self) -> dict:
        return _json_value(self)

    @classmethod
    def from_dict(cls, value: Mapping):
        _require(isinstance(value, Mapping), "expected object")
        names = {f.name for f in fields(cls)}
        _require(
            set(value) == names,
            f"{cls.__name__}: missing/unknown fields: {set(value) ^ names}",
        )
        return cls(**dict(value))

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )


@dataclass(frozen=True)
class NumericEvidence(Contract):
    value: float | None
    status: EvidenceStatus
    source: EvidenceSource

    def _validate(self):
        _require(
            (self.value is not None) == (self.status in ("evaluated", "partial")),
            "evidence status/value mismatch",
        )


@dataclass(frozen=True)
class DecisionIdentity(Contract):
    episode_id: str
    player_id: int
    decision_id: int
    request_id: str
    snapshot_digest: str

    def _validate(self):
        _identifier(self.episode_id)
        _identifier(self.request_id)
        _require(
            self.player_id in (0, 1) and self.decision_id >= 0,
            "invalid decision identity",
        )
        _digest(self.snapshot_digest)


@dataclass(frozen=True)
class PublicAttackPacket(Contract):
    packet_id: str
    amount: int
    arrival_tick: int | None
    landed_tick: int | None

    def _validate(self):
        _identifier(self.packet_id)
        _require(self.amount >= 0, "negative packet amount")
        for tick in (self.arrival_tick, self.landed_tick):
            _require(tick is None or tick >= 0, "negative packet tick")
        if self.arrival_tick is not None and self.landed_tick is not None:
            _require(self.landed_tick >= self.arrival_tick, "landing precedes arrival")


@dataclass(frozen=True)
class PublicPlayerState(Contract):
    # Rows are top to bottom. None is unobserved, 0 empty, 1..4 colors, 5 garbage.
    visible_board: tuple[tuple[int | None, ...], ...]
    known_pieces: tuple[tuple[int, int], ...]
    phase: str
    attack_packets: tuple[PublicAttackPacket, ...]
    score_carry: int
    all_clear_bonus_pending: bool
    all_clear_achieved: bool
    all_clear_bonus_consumed: bool

    def _validate(self):
        _require(
            len(self.visible_board) > 0
            and all(len(row) == 6 for row in self.visible_board),
            "board must contain six-column rows",
        )
        _require(
            all(
                v is None or 0 <= v <= PUBLIC_GARBAGE_ID
                for row in self.visible_board
                for v in row
            ),
            "invalid public cell",
        )
        _require(
            len(self.known_pieces) <= 3
            and all(v in PUBLIC_COLOR_IDS for pair in self.known_pieces for v in pair),
            "public current/NEXT/NEXT2 only",
        )
        _identifier(self.phase)
        _require(self.score_carry >= 0, "invalid score carry")
        _require(
            len({p.packet_id for p in self.attack_packets}) == len(self.attack_packets),
            "duplicate packet ID",
        )


@dataclass(frozen=True)
class PublicEvent(Contract):
    event_id: str
    player_id: int
    kind: Literal["placement", "clear", "attack", "arrival", "drop", "phase"]
    tick: int
    action: int | None
    cells: tuple[tuple[int, int, int], ...]

    def _validate(self):
        _identifier(self.event_id)
        _require(self.player_id in (0, 1) and self.tick >= 0, "invalid public event")
        if self.action is not None:
            _action(self.action)
        _require(
            all(
                r >= 0 and 0 <= c < 6 and 0 <= v <= PUBLIC_GARBAGE_ID
                for r, c, v in self.cells
            ),
            "invalid event cells",
        )


@dataclass(frozen=True)
class PublicSnapshot(Contract):
    own: PublicPlayerState
    opponent: PublicPlayerState
    events: tuple[PublicEvent, ...]
    information_mode: Literal["public_only"] = "public_only"

    @property
    def digest(self):
        return semantic_digest(self)


@dataclass(frozen=True)
class ExecutionContext(Contract):
    reachable_mask: tuple[bool, ...]
    request_tick: int
    timeout_tick: int
    timing_schema: str
    timing_digest: str
    latency_mode: Literal["configured", "measured"]

    def _validate(self):
        _require(len(self.reachable_mask) == NUM_ACTIONS, "wrong placement mask length")
        _require(
            0 <= self.request_tick <= self.timeout_tick, "invalid request/timeout ticks"
        )
        _identifier(self.timing_schema)
        _digest(self.timing_digest)


@dataclass(frozen=True)
class PhaseSnapshot(Contract):
    phase_id: str | None
    template_id: str | None
    active: bool
    remaining_decisions: int
    decision_limit: int
    progress: float
    fit_status: Literal["fit", "no_fit", "unknown"]
    previous_tactic: TacticId | None
    tactic_continuation: int

    def _validate(self):
        _require(
            self.decision_limit > 0
            and 0 <= self.remaining_decisions <= self.decision_limit,
            "invalid phase decision budget",
        )
        _require(
            0 <= self.progress <= 1 and self.tactic_continuation >= 0,
            "invalid phase summary",
        )
        for value in (self.phase_id, self.template_id):
            if value is not None:
                _identifier(value)
        _require(
            not self.active
            or (
                self.phase_id is not None
                and self.template_id is not None
                and self.remaining_decisions > 0
            ),
            "active phase lacks identity/budget",
        )


@dataclass(frozen=True)
class SearchProfile(Contract):
    profile_id: str
    shared_quota: int
    template_quota: int
    response_quota: int

    def _validate(self):
        _identifier(self.profile_id)
        _require(
            min(self.shared_quota, self.template_quota, self.response_quota) >= 0,
            "negative search quota",
        )

    @property
    def total_quota(self):
        return self.shared_quota + self.template_quota + self.response_quota


@dataclass(frozen=True)
class ScenarioProvenance(Contract):
    generator_version: str
    public_prefix_digest: str
    stream_id: str
    scenario_digest: str

    def _validate(self):
        _identifier(self.generator_version)
        _identifier(self.stream_id)
        _digest(self.public_prefix_digest)
        _digest(self.scenario_digest)


@dataclass(frozen=True)
class ControlContext(Contract):
    phase: PhaseSnapshot
    search_profile: SearchProfile
    template_config_hash: str
    scenario_provenance: ScenarioProvenance
    tactic_registry_hash: str = TACTIC_REGISTRY_HASH

    def _validate(self):
        _digest(self.template_config_hash)
        _require(
            self.tactic_registry_hash == TACTIC_REGISTRY_HASH,
            "tactic registry mismatch",
        )


@dataclass(frozen=True)
class NextgenRequest(Contract):
    SCHEMA: ClassVar[str] = REQUEST_SCHEMA_VERSION
    identity: DecisionIdentity
    public: PublicSnapshot
    execution: ExecutionContext
    control: ControlContext
    schema_version: str = REQUEST_SCHEMA_VERSION

    def _validate(self):
        _require(
            self.identity.snapshot_digest == self.public.digest,
            "snapshot digest mismatch",
        )


@dataclass(frozen=True)
class PlanStep(Contract):
    action: int
    piece: tuple[int, int]
    provenance: Literal["public_known", "sampled_future"]

    def _validate(self):
        _action(self.action)
        _require(all(v in PUBLIC_COLOR_IDS for v in self.piece), "invalid piece")


EVIDENCE_NAMES = (
    "chain_count",
    "score",
    "generated",
    "canceled",
    "outgoing",
    "fire_depth",
    "fire_start_lower",
    "fire_start_upper",
    "fire_end_lower",
    "fire_end_upper",
    "deadline_lower",
    "deadline_upper",
    "response_surplus",
    "trigger_survives",
    "counter_after_first_drop",
    "scenario_support",
    "scenario_coverage",
    "scenario_worst",
    "fatal_rate",
    "template_progress",
    "survival_safe",
    "survival_status",
    "survival_root_chain",
    "survival_depth",
)


@dataclass(frozen=True)
class NamedEvidence(Contract):
    name: str
    evidence: NumericEvidence

    def _validate(self):
        _require(self.name in EVIDENCE_NAMES, "unknown candidate evidence")


@dataclass(frozen=True)
class CandidateAssumptions(Contract):
    snapshot_digest: str
    scenario_digest: str
    timing_digest: str
    search_profile_digest: str
    template_config_hash: str

    def _validate(self):
        for f in fields(self):
            _digest(getattr(self, f.name))


def candidate_id(
    identity: DecisionIdentity,
    plan: tuple[PlanStep, ...],
    assumptions: CandidateAssumptions,
) -> str:
    """Identity excludes request retries, ranks, scores and wall-clock telemetry."""
    return "candidate:" + semantic_digest(
        {
            "episode_id": identity.episode_id,
            "player_id": identity.player_id,
            "decision_id": identity.decision_id,
            "plan": plan,
            "assumptions": assumptions,
        }
    )


@dataclass(frozen=True)
class Candidate(Contract):
    """Deduplicated plan; rank is stable batch enumeration, not tactic priority."""
    identity: DecisionIdentity
    candidate_id: str
    root_action: int
    plan: tuple[PlanStep, ...]
    tactics: tuple[TacticId, ...]
    rank: int
    root_legal: bool
    root_reachable: bool
    assumptions: CandidateAssumptions
    evidence: tuple[NamedEvidence, ...]
    fallback: bool = False

    def _validate(self):
        _action(self.root_action)
        _require(
            bool(self.plan) and self.plan[0].action == self.root_action,
            "root/plan mismatch",
        )
        _require(
            self.plan[0].provenance == "public_known",
            "root must use public current piece",
        )
        _require(
            self.rank >= 0
            and bool(self.tactics)
            and len(set(self.tactics)) == len(self.tactics),
            "invalid candidate rank/tactics",
        )
        _require(
            self.candidate_id
            == candidate_id(self.identity, self.plan, self.assumptions),
            "candidate ID semantic hash mismatch",
        )
        _require(
            self.identity.snapshot_digest == self.assumptions.snapshot_digest,
            "candidate snapshot mismatch",
        )
        _require(
            len({e.name for e in self.evidence}) == len(self.evidence),
            "duplicate evidence",
        )
        _require(
            not self.fallback or self.tactics == ("build_main",),
            "fallback must belong only to build_main",
        )


@dataclass(frozen=True)
class TacticSummary(Contract):
    """Candidate IDs in this tactic's immutable, search-time priority order."""
    tactic_id: TacticId
    candidate_ids: tuple[str, ...]
    best_id: str | None
    available: bool
    mask_reason: MaskReason
    evaluation_status: EvidenceStatus
    known_witness: bool
    evidence: tuple[NamedEvidence, ...]

    def _validate(self):
        _require(
            len(set(self.candidate_ids)) == len(self.candidate_ids),
            "duplicate tactic candidate ID",
        )
        _require(self.available == bool(self.candidate_ids), "mask/candidate mismatch")
        _require(
            (self.mask_reason == "available") == self.available, "mask reason mismatch"
        )
        _require(
            self.best_id == (self.candidate_ids[0] if self.candidate_ids else None),
            "best ID must be fixed rank first",
        )
        _require(
            not self.available or self.evaluation_status in ("evaluated", "partial"),
            "unevaluated tactic cannot be available",
        )
        _require(not self.known_witness or self.available, "witness without candidate")
        _require(
            len({e.name for e in self.evidence}) == len(self.evidence),
            "duplicate summary evidence",
        )


@dataclass(frozen=True)
class SearchCounters(Contract):
    shared_nodes: int
    template_nodes: int
    response_nodes: int
    feature_evaluations: int
    elapsed_ms: float

    def _validate(self):
        _require(
            min(
                self.shared_nodes,
                self.template_nodes,
                self.response_nodes,
                self.feature_evaluations,
                self.elapsed_ms,
            )
            >= 0,
            "negative search counter",
        )


@dataclass(frozen=True)
class CandidateBatch(Contract):
    SCHEMA: ClassVar[str] = CANDIDATE_BATCH_SCHEMA_VERSION
    READABLE_SCHEMAS: ClassVar[tuple[str, ...]] = (LEGACY_CANDIDATE_BATCH_SCHEMA_VERSION, PRE_SURVIVAL_CANDIDATE_BATCH_SCHEMA_VERSION,)
    identity: DecisionIdentity
    status: Literal["complete", "partial", "unavailable"]
    cutoff_reason: str | None
    known_prefix_length: int
    candidates: tuple[Candidate, ...]
    tactics: tuple[TacticSummary, ...]
    counters: SearchCounters
    schema_version: str = CANDIDATE_BATCH_SCHEMA_VERSION

    def _validate(self):
        _require(0 <= self.known_prefix_length <= 3, "invalid known prefix")
        _require(
            (self.cutoff_reason is None) == (self.status == "complete"),
            "cutoff/status mismatch",
        )
        if self.cutoff_reason is not None:
            _identifier(self.cutoff_reason)
        _require(
            tuple(t.tactic_id for t in self.tactics) == TACTIC_IDS,
            "tactics must use fixed six-ID order",
        )
        ids = {c.candidate_id: c for c in self.candidates}
        _require(len(ids) == len(self.candidates), "duplicate candidate ID")
        _require(
            len({c.rank for c in self.candidates}) == len(self.candidates),
            "duplicate fixed rank",
        )
        for c in self.candidates:
            if self.schema_version != CANDIDATE_BATCH_SCHEMA_VERSION:
                _require(not any(e.name.startswith("survival_") for e in c.evidence),
                         "survival evidence requires candidate batch v3")
            _require(
                c.identity == self.identity, "candidate decision identity mismatch"
            )
            seen_sampled = False
            for i, step in enumerate(c.plan):
                seen_sampled |= step.provenance == "sampled_future"
                _require(
                    not (
                        step.provenance == "public_known"
                        and (seen_sampled or i >= self.known_prefix_length)
                    ),
                    "plan public prefix mismatch",
                )
        for t in self.tactics:
            expected = {
                c.candidate_id
                for c in self.candidates
                if t.tactic_id in c.tactics and c.root_legal and c.root_reachable
            }
            _require(
                set(t.candidate_ids) == expected, "tactic candidate reference mismatch"
            )
            if self.schema_version == LEGACY_CANDIDATE_BATCH_SCHEMA_VERSION:
                _require(
                    t.candidate_ids == tuple(c.candidate_id for c in sorted(self.candidates, key=lambda c: c.rank)
                                             if c.candidate_id in expected),
                    "legacy tactic candidate rank mismatch",
                )
        _require(
            self.status != "unavailable" or not self.candidates,
            "unavailable batch has candidates",
        )

    @property
    def action_mask(self) -> tuple[bool, ...]:
        return tuple(t.available for t in self.tactics)

    @property
    def digest(self) -> str:
        payload = self.to_dict()
        del payload["counters"]["elapsed_ms"]
        return semantic_digest(payload)


@dataclass(frozen=True)
class FeatureSpec(Contract):
    name: str
    dtype: Literal["float32"]
    scale: float
    clip: tuple[float, float]
    transform: Literal["linear", "log1p"]
    missingness: Literal["zero_with_bit"]
    source: str
    version: int


def _feature(name, scale=1.0, transform="linear", source="public_summary"):
    return FeatureSpec(
        name, "float32", scale, (0.0, 1.0), transform, "zero_with_bit", source, 1
    )


FEATURE_REGISTRY = (
    *(_feature(f"threat.{x}") for x in ("none", "potential", "pressing", "immediate")),
    *(
        _feature(f"response.{x}")
        for x in ("possible", "marginal", "impossible", "unknown")
    ),
    *(_feature(f"first_fire_loss.{x}", 3) for x in ("lower", "upper")),
    *(
        _feature(f"{side}.{kind}.{name}", scale, transform, "search_evidence")
        for side in ("own", "opponent")
        for kind in ("main", "sub")
        for name, scale, transform in (
            ("chain_count", 19, "linear"),
            ("firepower", 360, "log1p"),
            ("danger", 1, "linear"),
            ("trigger_survives", 1, "linear"),
        )
    ),
    *(
        _feature(f"phase.{x}")
        for x in (
            "active",
            "remaining_ratio",
            "progress",
            "fit",
            "no_fit",
            "fit_unknown",
        )
    ),
    *(_feature(f"phase.previous.{x}") for x in TACTIC_IDS),
    _feature("phase.continuation", 14),
    *(
        _feature(f"tactic.{tactic}.{name}", scale, transform, "tactic_summary")
        for tactic in TACTIC_IDS
        for name, scale, transform in (
            ("available", 1, "linear"),
            ("known_witness", 1, "linear"),
            ("count", 8, "linear"),
            ("firepower", 360, "log1p"),
            ("deadline_negative", 1, "linear"),
            ("deadline_zero", 1, "linear"),
            ("deadline_positive", 1, "linear"),
            ("scenario_coverage", 1, "linear"),
            ("fatal_rate", 1, "linear"),
        )
    ),
)
FEATURE_NAMES = tuple(f.name for f in FEATURE_REGISTRY)
FEATURE_REGISTRY_HASH = semantic_digest(
    {"schema_version": FEATURE_SCHEMA_VERSION, "features": FEATURE_REGISTRY}
)


@dataclass(frozen=True)
class PolicyFeatures(Contract):
    SCHEMA: ClassVar[str] = FEATURE_SCHEMA_VERSION
    values: tuple[float, ...]
    missing: tuple[bool, ...]
    action_mask: tuple[bool, ...]
    feature_registry_hash: str = FEATURE_REGISTRY_HASH
    tactic_registry_hash: str = TACTIC_REGISTRY_HASH
    schema_version: str = FEATURE_SCHEMA_VERSION

    def _validate(self):
        _require(
            self.feature_registry_hash == FEATURE_REGISTRY_HASH
            and self.tactic_registry_hash == TACTIC_REGISTRY_HASH,
            "feature/tactic registry mismatch",
        )
        _require(
            len(self.values) == len(self.missing) == len(FEATURE_REGISTRY),
            "feature dimension mismatch",
        )
        _require(len(self.action_mask) == len(TACTIC_IDS), "wrong tactic mask length")
        for value, missing, spec in zip(self.values, self.missing, FEATURE_REGISTRY):
            _require(
                spec.clip[0] <= value <= spec.clip[1],
                "feature outside normalized range",
            )
            _require(not missing or value == 0, "missing feature must be zero filled")

    @property
    def actor_vector(self) -> tuple[float, ...]:
        """Only normalized values and their missing bits; mask is separate."""
        return self.values + tuple(float(x) for x in self.missing)


def build_features(
    summaries: Mapping[str, float | NumericEvidence | None],
    action_mask: tuple[bool, ...],
) -> PolicyFeatures:
    """Allowlist raw summaries, normalize once; partial evidence is not missing."""
    _require(
        isinstance(summaries, Mapping) and not (set(summaries) - set(FEATURE_NAMES)),
        "unknown/non-summary RL feature",
    )
    values, missing = [], []
    for spec in FEATURE_REGISTRY:
        item = summaries.get(spec.name)
        value = item.value if isinstance(item, NumericEvidence) else item
        if value is None:
            values.append(0.0)
            missing.append(True)
        else:
            value = _typed(value, float, spec.name)
            value = max(0.0, value)
            value = (
                math.log1p(value) / math.log1p(spec.scale)
                if spec.transform == "log1p"
                else value / spec.scale
            )
            values.append(min(spec.clip[1], max(spec.clip[0], value)))
            missing.append(False)
    return PolicyFeatures(tuple(values), tuple(missing), action_mask)


@dataclass(frozen=True)
class Selection(Contract):
    SCHEMA: ClassVar[str] = SELECTION_SCHEMA_VERSION
    selected_tactic_id: TacticId
    candidate_id: str
    batch_digest: str
    selector_kind: Literal["rule", "rl"]
    selector_checkpoint: str | None
    behavior_log_prob: float | None
    value: float | None
    reason: str
    schema_version: str = SELECTION_SCHEMA_VERSION

    def _validate(self):
        _require(
            re.fullmatch(r"candidate:[0-9a-f]{64}", self.candidate_id) is not None,
            "invalid candidate ID",
        )
        _digest(self.batch_digest)
        _identifier(self.reason)
        if self.selector_kind == "rule":
            _require(
                self.selector_checkpoint is None
                and self.behavior_log_prob is None
                and self.value is None,
                "rule selection must not contain learned outputs",
            )
        else:
            _require(
                self.selector_checkpoint is not None
                and self.behavior_log_prob is not None
                and self.value is not None,
                "RL selection requires checkpoint/log_prob/value",
            )
            _digest(self.selector_checkpoint)
            _require(self.behavior_log_prob <= 0, "invalid log probability")

    def validate_batch(self, batch: CandidateBatch) -> Candidate:
        _require(self.batch_digest == batch.digest, "selection batch digest mismatch")
        tactic = batch.tactics[TACTIC_IDS.index(self.selected_tactic_id)]
        _require(
            tactic.available and self.candidate_id == tactic.best_id,
            "selection outside mask/fixed candidate rank",
        )
        return next(c for c in batch.candidates if c.candidate_id == self.candidate_id)


@dataclass(frozen=True)
class ExecutionReceipt(Contract):
    requested_candidate_id: str
    requested_action: int
    executed_action: int | None
    outcome: Literal["activated", "stale", "timeout", "fallback"]
    request_tick: int
    completion_tick: int
    activation_tick: int | None
    timeout_tick: int
    pre_execution_snapshot_digest: str
    reason: str

    def _validate(self):
        _require(
            re.fullmatch(r"candidate:[0-9a-f]{64}", self.requested_candidate_id)
            is not None,
            "invalid requested candidate ID",
        )
        _action(self.requested_action)
        if self.executed_action is not None:
            _action(self.executed_action)
        _require(
            0 <= self.request_tick <= self.completion_tick
            and self.timeout_tick >= self.request_tick,
            "invalid receipt ticks",
        )
        _require(
            self.activation_tick is None
            or self.activation_tick >= self.completion_tick,
            "activation precedes completion",
        )
        _digest(self.pre_execution_snapshot_digest)
        _identifier(self.reason)
        if self.outcome == "activated":
            _require(
                self.executed_action == self.requested_action
                and self.activation_tick is not None
                and self.activation_tick <= self.timeout_tick,
                "invalid activation receipt",
            )

    @property
    def actor_trainable(self) -> bool:
        return self.outcome == "activated"


def validate_request_batch(request: NextgenRequest, batch: CandidateBatch) -> None:
    _require(request.identity == batch.identity, "request/batch identity mismatch")
    _require(
        batch.known_prefix_length == len(request.public.own.known_pieces),
        "request/batch known prefix mismatch",
    )
    profile, counters = request.control.search_profile, batch.counters
    _require(
        counters.shared_nodes <= profile.shared_quota
        and counters.template_nodes <= profile.template_quota
        and counters.response_nodes <= profile.response_quota,
        "search quota exceeded",
    )
    expected = CandidateAssumptions(
        request.public.digest,
        request.control.scenario_provenance.scenario_digest,
        request.execution.timing_digest,
        semantic_digest(profile),
        request.control.template_config_hash,
    )
    for c in batch.candidates:
        _require(c.assumptions == expected, "candidate/request assumptions mismatch")
        _require(
            c.root_reachable == request.execution.reachable_mask[c.root_action],
            "candidate reachability mismatch",
        )
        for index, step in enumerate(c.plan):
            if step.provenance == "public_known":
                _require(
                    step.piece == request.public.own.known_pieces[index],
                    "candidate public piece mismatch",
                )
    if any(request.execution.reachable_mask):
        _require(
            batch.action_mask[0], "reachable decision requires build_main/fallback"
        )


@dataclass(frozen=True)
class Diagnostics(Contract):
    SCHEMA: ClassVar[str] = DIAGNOSTICS_SCHEMA_VERSION
    request: NextgenRequest
    batch: CandidateBatch
    features: PolicyFeatures
    selection: Selection | None
    receipt: ExecutionReceipt | None
    schema_version: str = DIAGNOSTICS_SCHEMA_VERSION

    def _validate(self):
        validate_request_batch(self.request, self.batch)
        _require(
            self.features.action_mask == self.batch.action_mask,
            "features/batch mask mismatch",
        )
        _require(
            (self.selection is not None) == any(self.batch.action_mask),
            "selection/mask mismatch",
        )
        if self.selection is not None:
            candidate = self.selection.validate_batch(self.batch)
            if self.receipt is not None:
                r = self.receipt
                _require(
                    r.requested_candidate_id == candidate.candidate_id
                    and r.requested_action == candidate.root_action,
                    "receipt/selection reference mismatch",
                )
                _require(
                    r.request_tick == self.request.execution.request_tick
                    and r.timeout_tick == self.request.execution.timeout_tick,
                    "receipt/request timing mismatch",
                )
                if r.outcome == "activated":
                    _require(
                        r.pre_execution_snapshot_digest == self.request.public.digest,
                        "activated stale snapshot",
                    )
        else:
            _require(self.receipt is None, "receipt without selection")


def checkpoint_contract() -> dict:
    """Required metadata checked BEFORE a future trainer loads weights."""
    return {
        "policy_id": POLICY_ID,
        "feature_schema": FEATURE_SCHEMA_VERSION,
        "feature_registry_hash": FEATURE_REGISTRY_HASH,
        "tactic_registry_hash": TACTIC_REGISTRY_HASH,
        "tactic_ids": list(TACTIC_IDS),
        "actor_input_size": len(FEATURE_REGISTRY) * 2,
        "actor_output_size": 6,
        "action_kind": "tactic_only",
    }


def validate_checkpoint_contract(metadata: Mapping) -> None:
    _require(
        isinstance(metadata, Mapping) and dict(metadata) == checkpoint_contract(),
        "nextgen checkpoint contract mismatch; legacy weights require explicit migration",
    )


_SCHEMA_TYPES = {
    c.SCHEMA: c
    for c in (NextgenRequest, CandidateBatch, PolicyFeatures, Selection, Diagnostics)
}
_SCHEMA_TYPES[LEGACY_CANDIDATE_BATCH_SCHEMA_VERSION] = CandidateBatch
_SCHEMA_TYPES[PRE_SURVIVAL_CANDIDATE_BATCH_SCHEMA_VERSION] = CandidateBatch


def from_dict(payload: Mapping) -> Contract:
    _require(isinstance(payload, Mapping), "expected versioned object")
    _require(type(payload.get("schema_version")) is str, "unsupported schema version")
    cls = _SCHEMA_TYPES.get(payload.get("schema_version"))
    _require(
        cls is not None, "unsupported schema version; no implicit legacy conversion"
    )
    return cls.from_dict(payload)


def from_json(payload: str) -> Contract:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    return from_dict(
        json.loads(
            payload,
            object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    )
