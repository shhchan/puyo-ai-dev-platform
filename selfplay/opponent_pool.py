"""Persistent opponent pool for self-play snapshots."""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .policies import Policy, make_policy
from .rating import EloConfig


@dataclass
class OpponentSnapshot:
    name: str
    policy_type: str = "random"
    checkpoint_path: str | None = None
    rating: float = 1000.0
    games_played: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class OpponentPool:
    """Collection of historical opponents with ratings."""

    def __init__(self, snapshots: list[OpponentSnapshot] | None = None, elo_config: EloConfig | None = None):
        self.elo_config = elo_config or EloConfig()
        self.snapshots: list[OpponentSnapshot] = snapshots or []

    def add(self, snapshot: OpponentSnapshot) -> None:
        if self.get(snapshot.name) is not None:
            raise ValueError(f"snapshot already exists: {snapshot.name}")
        self.snapshots.append(snapshot)

    def get(self, name: str) -> OpponentSnapshot | None:
        for snapshot in self.snapshots:
            if snapshot.name == name:
                return snapshot
        return None

    def sample(
        self,
        rng: random.Random | None = None,
        *,
        strategy: str = "uniform",
        target_rating: float | None = None,
    ) -> OpponentSnapshot:
        if not self.snapshots:
            raise ValueError("cannot sample from an empty opponent pool")
        chooser = rng or random
        if strategy == "uniform":
            return chooser.choice(self.snapshots)
        if strategy == "balanced":
            weights = [1.0 / (1.0 + snapshot.games_played) for snapshot in self.snapshots]
            return chooser.choices(self.snapshots, weights=weights, k=1)[0]
        if strategy == "elo":
            center = self.elo_config.default_rating if target_rating is None else float(target_rating)
            weights = [math.exp(-abs(snapshot.rating - center) / 200.0) for snapshot in self.snapshots]
            return chooser.choices(self.snapshots, weights=weights, k=1)[0]
        raise ValueError(f"unknown opponent sampling strategy: {strategy}")

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
        return make_policy(
            snapshot.policy_type,
            seed=seed,
            checkpoint_path=snapshot.checkpoint_path,
            device=device,
            deterministic=deterministic,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "elo_config": asdict(self.elo_config),
            "snapshots": [asdict(snapshot) for snapshot in self.snapshots],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OpponentPool":
        elo_config = EloConfig(**data.get("elo_config", {}))
        snapshots = [OpponentSnapshot(**item) for item in data.get("snapshots", [])]
        return cls(snapshots=snapshots, elo_config=elo_config)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "OpponentPool":
        source = Path(path)
        return cls.from_dict(json.loads(source.read_text(encoding="utf-8")))


def default_opponent_pool() -> OpponentPool:
    return OpponentPool(
        snapshots=[
            OpponentSnapshot(name="random", policy_type="random"),
            OpponentSnapshot(name="greedy_score", policy_type="greedy"),
            OpponentSnapshot(name="worker_large", policy_type="worker_large"),
            OpponentSnapshot(name="worker_quick", policy_type="worker_quick"),
            OpponentSnapshot(name="worker_fire", policy_type="worker_fire"),
            OpponentSnapshot(name="puyo29_beam", policy_type="beam", rating=1150.0),
            OpponentSnapshot(name="manager_rule", policy_type="manager_rule"),
        ]
    )
