"""Persistent, reproducible opponent pools for training and evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .policies import Policy, make_policy
from .rating import EloConfig


OPPONENT_POOL_SCHEMA_VERSION = "puyo.opponent_pool.v2"
OPPONENT_SCHEDULE_SCHEMA_VERSION = "puyo.opponent_schedule.v1"
LEGACY_OPPONENT_POOL_SCHEMA_VERSION = "puyo.opponent_pool.legacy"
STRATIFIED_ELO_STRATEGY = "stratified_elo"
PAIRED_SIDES = ("player_0", "player_1")
_RESERVED_POLICY_KWARGS = {"seed", "checkpoint_path", "device", "deterministic"}


@dataclass
class OpponentSnapshot:
    name: str
    policy_type: str = "random"
    checkpoint_path: str | None = None
    rating: float = 1000.0
    games_played: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    stratum: str = "unstratified"
    role: str = ""
    policy_kwargs: dict[str, Any] = field(default_factory=dict)
    checkpoint_sha256: str | None = None
    checkpoint_schema: str | None = None
    enabled: bool = True
    fallback: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.role and isinstance(self.metadata, Mapping):
            self.role = str(self.metadata.get("role", ""))


@dataclass(frozen=True)
class OpponentAssignment:
    """One side of a paired match in a reproducible opponent schedule."""

    pair_index: int
    match_index: int
    game_seed: int
    learner_side: str
    stratum: str
    requested_opponent: str
    effective_opponent: str
    role: str
    policy_type: str
    policy_kwargs: dict[str, Any]
    checkpoint_path: str | None
    checkpoint_sha256: str | None
    checkpoint_schema: str | None
    rating: float
    elo_weight: float
    fallback: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OpponentPool:
    """Collection of opponents with optional stratified sampling contracts."""

    def __init__(
        self,
        snapshots: list[OpponentSnapshot] | None = None,
        elo_config: EloConfig | None = None,
        *,
        schema_version: str = LEGACY_OPPONENT_POOL_SCHEMA_VERSION,
        pool_id: str = "",
        sampling_strategy: str = "",
        quotas: Mapping[str, float] | None = None,
        elo_temperature: float = 200.0,
        fallback_by_stratum: Mapping[str, Mapping[str, Any]] | None = None,
        path_root: str = ".",
        source_path: str | Path | None = None,
    ):
        self.elo_config = elo_config or EloConfig()
        self.snapshots: list[OpponentSnapshot] = snapshots or []
        self.schema_version = schema_version
        self.pool_id = pool_id
        self.sampling_strategy = sampling_strategy
        self.quotas = {str(key): float(value) for key, value in (quotas or {}).items()}
        self.elo_temperature = float(elo_temperature)
        self.fallback_by_stratum = {
            str(key): dict(value) for key, value in (fallback_by_stratum or {}).items()
        }
        self.path_root = str(path_root or ".")
        self.source_path = Path(source_path).resolve() if source_path is not None else None
        base = self.source_path.parent if self.source_path is not None else Path.cwd()
        self.checkpoint_root = (base / self.path_root).resolve()
        self.manifest_sha256 = (
            _file_sha256(self.source_path)
            if self.source_path is not None and self.source_path.is_file()
            else None
        )

    def add(self, snapshot: OpponentSnapshot) -> None:
        if self.get(snapshot.name) is not None:
            raise ValueError(f"snapshot already exists: {snapshot.name}")
        self.snapshots.append(snapshot)

    def get(self, name: str) -> OpponentSnapshot | None:
        for snapshot in self.snapshots:
            if snapshot.name == name:
                return snapshot
        return None

    def validate(self) -> list[str]:
        errors: list[str] = []
        names: set[str] = set()
        for index, snapshot in enumerate(self.snapshots):
            prefix = f"snapshots[{index}]"
            if not snapshot.name:
                errors.append(f"{prefix}.name is required")
            elif snapshot.name in names:
                errors.append(f"duplicate snapshot name: {snapshot.name}")
            names.add(snapshot.name)
            if not snapshot.policy_type:
                errors.append(f"{prefix}.policy_type is required")
            if not snapshot.stratum:
                errors.append(f"{prefix}.stratum is required")
            if not isinstance(snapshot.metadata, dict):
                errors.append(f"{prefix}.metadata must be an object")
            else:
                try:
                    sampling_weight = float(
                        snapshot.metadata.get("sampling_weight", 1.0)
                    )
                except (TypeError, ValueError):
                    sampling_weight = 0.0
                if sampling_weight <= 0.0:
                    errors.append(f"{prefix}.metadata.sampling_weight must be positive")
            if not isinstance(snapshot.policy_kwargs, dict):
                errors.append(f"{prefix}.policy_kwargs must be an object")
            else:
                reserved = sorted(set(snapshot.policy_kwargs) & _RESERVED_POLICY_KWARGS)
                if reserved:
                    errors.append(
                        f"{prefix}.policy_kwargs contains reserved keys: {', '.join(reserved)}"
                    )
        if self.schema_version == OPPONENT_POOL_SCHEMA_VERSION:
            if not self.pool_id:
                errors.append("pool_id is required")
            if self.sampling_strategy != STRATIFIED_ELO_STRATEGY:
                errors.append(
                    f"sampling.strategy must be {STRATIFIED_ELO_STRATEGY!r}"
                )
            if not self.quotas:
                errors.append("sampling.quotas is required")
            elif not math.isclose(sum(self.quotas.values()), 1.0, abs_tol=1e-9):
                errors.append("sampling.quotas must sum to 1.0")
            if any(value <= 0.0 for value in self.quotas.values()):
                errors.append("sampling quotas must be positive")
            if self.elo_temperature <= 0.0:
                errors.append("sampling.elo_temperature must be positive")
            for index, snapshot in enumerate(self.snapshots):
                if not snapshot.checkpoint_path:
                    continue
                if not snapshot.checkpoint_sha256:
                    errors.append(f"snapshots[{index}].checkpoint_sha256 is required")
                if not snapshot.checkpoint_schema:
                    errors.append(f"snapshots[{index}].checkpoint_schema is required")
            enabled_strata = {
                snapshot.stratum for snapshot in self.snapshots if snapshot.enabled
            }
            for stratum in self.quotas:
                if stratum not in enabled_strata:
                    errors.append(f"sampling stratum has no enabled opponents: {stratum}")
                fallback = self.fallback_by_stratum.get(stratum)
                if not isinstance(fallback, Mapping):
                    errors.append(f"fallback_by_stratum.{stratum} is required")
                elif not fallback.get("name") or not fallback.get("policy_type"):
                    errors.append(
                        f"fallback_by_stratum.{stratum} requires name and policy_type"
                    )
                elif not isinstance(fallback.get("policy_kwargs", {}), Mapping):
                    errors.append(
                        f"fallback_by_stratum.{stratum}.policy_kwargs must be an object"
                    )
                else:
                    reserved = sorted(
                        set(fallback.get("policy_kwargs", {}))
                        & _RESERVED_POLICY_KWARGS
                    )
                    if reserved:
                        errors.append(
                            f"fallback_by_stratum.{stratum}.policy_kwargs contains "
                            f"reserved keys: {', '.join(reserved)}"
                        )
            unexpected = sorted(enabled_strata - set(self.quotas))
            if unexpected:
                errors.append(
                    "enabled snapshots use undeclared strata: " + ", ".join(unexpected)
                )
        return errors

    def sample(
        self,
        rng: random.Random | None = None,
        *,
        strategy: str = "uniform",
        target_rating: float | None = None,
    ) -> OpponentSnapshot:
        snapshots = [snapshot for snapshot in self.snapshots if snapshot.enabled]
        if not snapshots:
            raise ValueError("cannot sample from an empty opponent pool")
        chooser = rng or random
        if strategy == "uniform":
            return chooser.choice(snapshots)
        if strategy == "balanced":
            weights = [
                float(snapshot.metadata.get("sampling_weight", 1.0))
                / (1.0 + snapshot.games_played)
                for snapshot in snapshots
            ]
            return chooser.choices(snapshots, weights=weights, k=1)[0]
        if strategy == "elo":
            return self._sample_elo(snapshots, chooser, target_rating)
        if strategy == STRATIFIED_ELO_STRATEGY:
            if not self.quotas:
                raise ValueError("stratified_elo sampling requires quotas")
            strata = list(self.quotas)
            stratum = chooser.choices(
                strata,
                weights=[self.quotas[name] for name in strata],
                k=1,
            )[0]
            return self._sample_elo(
                [snapshot for snapshot in snapshots if snapshot.stratum == stratum],
                chooser,
                target_rating,
            )
        raise ValueError(f"unknown opponent sampling strategy: {strategy}")

    def build_schedule(
        self,
        *,
        pairs: int,
        seed: int,
        target_rating: float | None = None,
    ) -> tuple[OpponentAssignment, ...]:
        """Build exact outer quotas with Elo weighting only inside each stratum."""

        errors = self.validate()
        if errors:
            raise ValueError("invalid opponent pool: " + "; ".join(errors))
        if pairs <= 0:
            raise ValueError("pairs must be positive")
        chooser = random.Random(seed)
        selected_pairs: list[tuple[str, OpponentSnapshot]] = []
        for stratum, count in self._quota_counts(pairs).items():
            candidates = [
                snapshot
                for snapshot in self.snapshots
                if snapshot.enabled and snapshot.stratum == stratum
            ]
            selected_pairs.extend(
                (stratum, snapshot)
                for snapshot in self._sample_stratum_batch(
                    candidates,
                    count,
                    chooser,
                    target_rating,
                )
            )
        chooser.shuffle(selected_pairs)
        assignments: list[OpponentAssignment] = []
        for pair_index, (stratum, snapshot) in enumerate(selected_pairs):
            effective, fallback = self._resolve_snapshot(snapshot)
            elo_weight = self._elo_weight(snapshot, target_rating)
            for side_index, learner_side in enumerate(PAIRED_SIDES):
                assignments.append(
                    OpponentAssignment(
                        pair_index=pair_index,
                        match_index=pair_index * len(PAIRED_SIDES) + side_index,
                        game_seed=seed + pair_index,
                        learner_side=learner_side,
                        stratum=stratum,
                        requested_opponent=snapshot.name,
                        effective_opponent=str(effective["name"]),
                        role=snapshot.role,
                        policy_type=str(effective["policy_type"]),
                        policy_kwargs=dict(effective.get("policy_kwargs", {})),
                        checkpoint_path=effective.get("checkpoint_path"),
                        checkpoint_sha256=effective.get("checkpoint_sha256"),
                        checkpoint_schema=effective.get("checkpoint_schema"),
                        rating=float(snapshot.rating),
                        elo_weight=elo_weight,
                        fallback=fallback,
                    )
                )
        return tuple(assignments)

    def update_rating(self, name: str, rating: float) -> None:
        snapshot = self.get(name)
        if snapshot is None:
            raise KeyError(name)
        snapshot.rating = float(rating)
        snapshot.games_played += 1

    def make_policy(
        self,
        snapshot: OpponentSnapshot,
        *,
        seed: int | None = None,
        device: str = "cpu",
        deterministic: bool = True,
    ) -> Policy:
        effective, _ = self._resolve_snapshot(snapshot)
        kwargs = dict(effective.get("policy_kwargs", {}))
        checkpoint_path = effective.get("checkpoint_path")
        if checkpoint_path:
            checkpoint_path = str(self._resolve_checkpoint_path(str(checkpoint_path)))
        return make_policy(
            str(effective["policy_type"]),
            seed=seed,
            checkpoint_path=checkpoint_path,
            device=device,
            deterministic=deterministic,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "elo_config": asdict(self.elo_config),
            "snapshots": [asdict(snapshot) for snapshot in self.snapshots],
        }
        if self.schema_version == OPPONENT_POOL_SCHEMA_VERSION:
            result.update(
                {
                    "pool_id": self.pool_id,
                    "path_root": self.path_root,
                    "sampling": {
                        "strategy": self.sampling_strategy,
                        "quotas": dict(self.quotas),
                        "elo_temperature": self.elo_temperature,
                        "paired_sides": True,
                        "seed_schedule": "base_seed_plus_pair_index",
                    },
                    "fallback_by_stratum": self.fallback_by_stratum,
                }
            )
        return result

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        source_path: str | Path | None = None,
    ) -> "OpponentPool":
        elo_config = EloConfig(**dict(data.get("elo_config", {})))
        snapshots = [
            OpponentSnapshot(**dict(item)) for item in data.get("snapshots", [])
        ]
        sampling = data.get("sampling", {})
        if not isinstance(sampling, Mapping):
            sampling = {}
        return cls(
            snapshots=snapshots,
            elo_config=elo_config,
            schema_version=str(
                data.get("schema_version", LEGACY_OPPONENT_POOL_SCHEMA_VERSION)
            ),
            pool_id=str(data.get("pool_id", "")),
            sampling_strategy=str(sampling.get("strategy", "")),
            quotas=sampling.get("quotas", {}),
            elo_temperature=float(sampling.get("elo_temperature", 200.0)),
            fallback_by_stratum=data.get("fallback_by_stratum", {}),
            path_root=str(data.get("path_root", ".")),
            source_path=source_path,
        )

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        if self.schema_version == OPPONENT_POOL_SCHEMA_VERSION:
            data["path_root"] = os.path.relpath(
                self.checkpoint_root,
                target.parent.resolve(),
            )
        target.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "OpponentPool":
        source = Path(path)
        value = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError(f"{source} must contain a JSON object")
        pool = cls.from_dict(value, source_path=source)
        errors = pool.validate()
        if errors:
            raise ValueError(f"invalid opponent pool {source}: " + "; ".join(errors))
        return pool

    def _sample_elo(
        self,
        snapshots: Sequence[OpponentSnapshot],
        chooser: random.Random | Any,
        target_rating: float | None,
    ) -> OpponentSnapshot:
        if not snapshots:
            raise ValueError("cannot sample from an empty stratum")
        weights = [self._elo_weight(snapshot, target_rating) for snapshot in snapshots]
        return chooser.choices(list(snapshots), weights=weights, k=1)[0]

    def _elo_weight(
        self,
        snapshot: OpponentSnapshot,
        target_rating: float | None,
    ) -> float:
        center = (
            self.elo_config.default_rating
            if target_rating is None
            else float(target_rating)
        )
        sampling_weight = float(snapshot.metadata.get("sampling_weight", 1.0))
        return sampling_weight * math.exp(
            -abs(snapshot.rating - center) / self.elo_temperature
        )

    def _sample_stratum_batch(
        self,
        snapshots: Sequence[OpponentSnapshot],
        count: int,
        chooser: random.Random,
        target_rating: float | None,
    ) -> list[OpponentSnapshot]:
        """Keep coverage, then apportion remaining slots by in-stratum Elo."""

        if count <= 0:
            return []
        if not snapshots:
            raise ValueError("cannot sample from an empty stratum")
        weights = [self._elo_weight(snapshot, target_rating) for snapshot in snapshots]
        if sum(weights) <= 0.0:
            raise ValueError("opponent sampling weights must contain a positive value")
        if count < len(snapshots):
            candidates = list(snapshots)
            candidate_weights = list(weights)
            selected: list[OpponentSnapshot] = []
            for _ in range(count):
                snapshot = chooser.choices(candidates, weights=candidate_weights, k=1)[0]
                index = candidates.index(snapshot)
                selected.append(candidates.pop(index))
                candidate_weights.pop(index)
            return selected

        allocations = [1] * len(snapshots)
        remaining = count - len(snapshots)
        if remaining:
            weight_total = sum(weights)
            raw = [remaining * weight / weight_total for weight in weights]
            floors = [int(math.floor(value)) for value in raw]
            allocations = [base + extra for base, extra in zip(allocations, floors)]
            leftover = remaining - sum(floors)
            order = sorted(
                range(len(snapshots)),
                key=lambda index: (-(raw[index] - floors[index]), snapshots[index].name),
            )
            for index in order[:leftover]:
                allocations[index] += 1
        selected = [
            snapshot
            for snapshot, allocation in zip(snapshots, allocations)
            for _ in range(allocation)
        ]
        chooser.shuffle(selected)
        return selected

    def _quota_counts(self, pairs: int) -> dict[str, int]:
        counts = {
            stratum: int(math.floor(pairs * quota))
            for stratum, quota in self.quotas.items()
        }
        remaining = pairs - sum(counts.values())
        order = sorted(
            self.quotas,
            key=lambda stratum: (
                -(pairs * self.quotas[stratum] - counts[stratum]),
                list(self.quotas).index(stratum),
            ),
        )
        for stratum in order[:remaining]:
            counts[stratum] += 1
        return counts

    def _resolve_checkpoint_path(self, checkpoint_path: str) -> Path:
        target = Path(checkpoint_path)
        return target if target.is_absolute() else self.checkpoint_root / target

    def _resolve_snapshot(
        self,
        snapshot: OpponentSnapshot,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        requested = {
            "name": snapshot.name,
            "policy_type": snapshot.policy_type,
            "policy_kwargs": dict(snapshot.policy_kwargs),
            "checkpoint_path": snapshot.checkpoint_path,
            "checkpoint_sha256": snapshot.checkpoint_sha256,
            "checkpoint_schema": snapshot.checkpoint_schema,
        }
        if self.schema_version != OPPONENT_POOL_SCHEMA_VERSION:
            return requested, None
        if not snapshot.checkpoint_path:
            return requested, None
        path = self._resolve_checkpoint_path(snapshot.checkpoint_path)
        reason: str | None = None
        details: dict[str, Any] = {}
        if not path.is_file():
            reason = "missing_checkpoint"
        else:
            actual_sha256 = _file_sha256(path)
            details["actual_sha256"] = actual_sha256
            if actual_sha256 != snapshot.checkpoint_sha256:
                reason = "checkpoint_sha256_mismatch"
            else:
                try:
                    actual_schema = _checkpoint_schema(path)
                    details["actual_schema"] = actual_schema
                    if actual_schema != snapshot.checkpoint_schema:
                        reason = "checkpoint_schema_mismatch"
                except Exception as error:  # corrupt and unreadable checkpoints use fallback
                    reason = "checkpoint_unreadable"
                    details["error_type"] = type(error).__name__
        if reason is None:
            return requested, None
        fallback = dict(
            snapshot.fallback
            or self.fallback_by_stratum.get(snapshot.stratum, {})
        )
        if not fallback.get("name") or not fallback.get("policy_type"):
            raise ValueError(
                f"{snapshot.name} requires fallback after {reason}, but none is configured"
            )
        effective = {
            "name": str(fallback["name"]),
            "policy_type": str(fallback["policy_type"]),
            "policy_kwargs": dict(fallback.get("policy_kwargs", {})),
            "checkpoint_path": fallback.get("checkpoint_path"),
            "checkpoint_sha256": fallback.get("checkpoint_sha256"),
            "checkpoint_schema": fallback.get("checkpoint_schema"),
        }
        evidence = {
            "reason": reason,
            "requested": requested,
            "effective": effective,
            **details,
        }
        return effective, evidence


def build_schedule_artifact(
    pool: OpponentPool,
    assignments: Sequence[OpponentAssignment],
    *,
    pairs: int,
    seed: int,
) -> dict[str, Any]:
    records = [assignment.to_dict() for assignment in assignments]
    pair_records = [record for record in records if record["learner_side"] == "player_0"]
    strata: dict[str, int] = {}
    opponents: dict[str, int] = {}
    fallback_evidence: list[dict[str, Any]] = []
    for record in pair_records:
        strata[record["stratum"]] = strata.get(record["stratum"], 0) + 1
        name = record["effective_opponent"]
        opponents[name] = opponents.get(name, 0) + 1
        if record.get("fallback") is not None:
            fallback_evidence.append(
                {
                    "pair_index": record["pair_index"],
                    "stratum": record["stratum"],
                    **dict(record["fallback"]),
                }
            )
    source = {
        "path": _display_source_path(pool),
        "sha256": pool.manifest_sha256,
    }
    return {
        "schema_version": OPPONENT_SCHEDULE_SCHEMA_VERSION,
        "pool": {
            "pool_id": pool.pool_id,
            "schema_version": pool.schema_version,
            "source": source,
            "opponent_manifest": pool.to_dict(),
        },
        "schedule": {
            "strategy": pool.sampling_strategy,
            "seed": int(seed),
            "pairs": int(pairs),
            "matches": len(records),
            "paired_sides": list(PAIRED_SIDES),
            "seed_schedule": "base_seed_plus_pair_index",
        },
        "summary": {
            "pairs_by_stratum": strata,
            "pairs_by_effective_opponent": opponents,
            "fallback_pairs": len(fallback_evidence),
        },
        "fallback_evidence": fallback_evidence,
        "sampling_history": records,
    }


def write_schedule_artifact(
    path: str | Path,
    pool: OpponentPool,
    assignments: Sequence[OpponentAssignment],
    *,
    pairs: int,
    seed: int,
) -> dict[str, Any]:
    artifact = build_schedule_artifact(
        pool,
        assignments,
        pairs=pairs,
        seed=seed,
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_source_path(pool: OpponentPool) -> str | None:
    if pool.source_path is None:
        return None
    try:
        return str(pool.source_path.relative_to(pool.checkpoint_root))
    except ValueError:
        return str(pool.source_path)


def _checkpoint_schema(path: Path) -> str | None:
    try:
        import torch
    except ImportError as error:  # pragma: no cover - training dependency guard
        raise RuntimeError("checkpoint validation requires torch") from error
    payload = torch.load(path, map_location="cpu", weights_only=False)
    if not isinstance(payload, Mapping):
        raise ValueError("checkpoint payload must be a mapping")
    schema = payload.get("checkpoint_schema")
    if not isinstance(schema, Mapping):
        return None
    value = schema.get("schema_version")
    return None if value is None else str(value)


def default_opponent_pool() -> OpponentPool:
    return OpponentPool(
        snapshots=[
            OpponentSnapshot(name="random", policy_type="random"),
            OpponentSnapshot(name="greedy_score", policy_type="greedy"),
            OpponentSnapshot(name="worker_large", policy_type="worker_large"),
            OpponentSnapshot(name="worker_quick", policy_type="worker_quick"),
            OpponentSnapshot(name="worker_punish", policy_type="worker_punish"),
            OpponentSnapshot(name="worker_counter", policy_type="worker_counter"),
            OpponentSnapshot(name="worker_fire", policy_type="worker_fire"),
            OpponentSnapshot(name="worker_survival", policy_type="worker_survival"),
            OpponentSnapshot(name="puyo29_beam", policy_type="beam", rating=1150.0),
            OpponentSnapshot(name="manager_rule", policy_type="manager_rule"),
        ]
    )
