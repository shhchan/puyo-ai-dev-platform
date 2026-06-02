"""Elo rating utilities for self-play evaluation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EloConfig:
    default_rating: float = 1000.0
    k_factor: float = 32.0


def expected_score(rating_a: float, rating_b: float) -> float:
    """Return player A's expected score against player B."""

    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def update_elo(
    rating_a: float,
    rating_b: float,
    score_a: float,
    k_factor: float = 32.0,
) -> tuple[float, float]:
    """Update a two-player Elo pair.

    ``score_a`` is 1.0 for A win, 0.5 for draw, and 0.0 for A loss.
    """

    if score_a < 0.0 or score_a > 1.0:
        raise ValueError("score_a must be in [0, 1]")
    expected_a = expected_score(rating_a, rating_b)
    delta_a = k_factor * (score_a - expected_a)
    return rating_a + delta_a, rating_b - delta_a


class EloTable:
    """Mutable rating table keyed by snapshot name."""

    def __init__(self, config: EloConfig | None = None):
        self.config = config or EloConfig()
        self.ratings: dict[str, float] = {}

    def get(self, name: str) -> float:
        return self.ratings.get(name, self.config.default_rating)

    def set(self, name: str, rating: float) -> None:
        self.ratings[name] = float(rating)

    def record_match(self, name_a: str, name_b: str, score_a: float) -> tuple[float, float]:
        new_a, new_b = update_elo(
            self.get(name_a),
            self.get(name_b),
            score_a,
            k_factor=self.config.k_factor,
        )
        self.set(name_a, new_a)
        self.set(name_b, new_b)
        return new_a, new_b
