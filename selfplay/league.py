"""Self-play league orchestration with Elo updates."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from eval.arena import ArenaResult, run_series

from .opponent_pool import OpponentPool, OpponentSnapshot
from .policies import Policy, make_policy
from .rating import update_elo


@dataclass(frozen=True)
class LeagueConfig:
    games_per_pair: int = 10
    max_steps: int = 500
    seed: int = 1
    k_factor: float = 32.0
    device: str = "cpu"


@dataclass(frozen=True)
class LeagueEvaluation:
    latest_name: str
    opponent_name: str
    latest_rating: float
    opponent_rating: float
    arena_result: ArenaResult


class SelfPlayLeague:
    """Evaluate latest snapshots against an opponent pool."""

    def __init__(self, opponent_pool: OpponentPool, config: LeagueConfig | None = None):
        self.opponent_pool = opponent_pool
        self.config = config or LeagueConfig()
        self.rng = random.Random(self.config.seed)

    def add_latest_checkpoint(
        self,
        name: str,
        checkpoint_path: str,
        metadata: dict[str, Any] | None = None,
    ) -> OpponentSnapshot:
        snapshot = OpponentSnapshot(
            name=name,
            policy_type="checkpoint",
            checkpoint_path=checkpoint_path,
            rating=self.opponent_pool.elo_config.default_rating,
            metadata=metadata or {},
        )
        self.opponent_pool.add(snapshot)
        return snapshot

    def evaluate_pair(
        self,
        latest: OpponentSnapshot,
        opponent: OpponentSnapshot,
        latest_policy: Policy | None = None,
    ) -> LeagueEvaluation:
        policy_a = latest_policy or self.opponent_pool.make_policy(
            latest,
            seed=self.config.seed,
            device=self.config.device,
        )
        policy_b = self.opponent_pool.make_policy(
            opponent,
            seed=self.config.seed + 10_000,
            device=self.config.device,
        )
        arena_result = run_series(
            policy_a,
            policy_b,
            games=self.config.games_per_pair,
            seed=self.config.seed,
            max_steps=self.config.max_steps,
        )

        rating_a = latest.rating
        rating_b = opponent.rating
        for match in arena_result.matches:
            rating_a, rating_b = update_elo(
                rating_a,
                rating_b,
                match.score_for_player_0,
                k_factor=self.config.k_factor,
            )
        latest.rating = rating_a
        latest.games_played += len(arena_result.matches)
        opponent.rating = rating_b
        opponent.games_played += len(arena_result.matches)

        return LeagueEvaluation(
            latest_name=latest.name,
            opponent_name=opponent.name,
            latest_rating=rating_a,
            opponent_rating=rating_b,
            arena_result=arena_result,
        )

    def evaluate_latest(
        self,
        latest: OpponentSnapshot,
        *,
        opponent_count: int | None = None,
        latest_policy: Policy | None = None,
    ) -> list[LeagueEvaluation]:
        candidates = [snapshot for snapshot in self.opponent_pool.snapshots if snapshot.name != latest.name]
        if opponent_count is not None:
            self.rng.shuffle(candidates)
            candidates = candidates[:opponent_count]
        return [
            self.evaluate_pair(latest, opponent, latest_policy=latest_policy)
            for opponent in candidates
        ]


def fixed_policy_snapshot(name: str, policy_type: str) -> OpponentSnapshot:
    make_policy(policy_type)
    return OpponentSnapshot(name=name, policy_type=policy_type)
