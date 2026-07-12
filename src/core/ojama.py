"""Shared score conversion rules for versus ojama generation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OjamaScoreConversion:
    units: int
    carry: int


def convert_score_to_ojama(
    score_delta: int,
    score_carry: int = 0,
    target_score_per_ojama: int = 70,
) -> OjamaScoreConversion:
    """Convert newly earned chain-end score plus prior remainder to ojama."""

    target = int(target_score_per_ojama)
    if target <= 0:
        raise ValueError("target_score_per_ojama must be positive")

    total = max(0, int(score_carry)) + max(0, int(score_delta))
    return OjamaScoreConversion(units=total // target, carry=total % target)
