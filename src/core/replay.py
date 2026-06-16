"""Replay fixtures for deterministic realtime headless simulations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .realtime import RealtimeHeadlessSimulator, TickInput


@dataclass(frozen=True)
class RealtimeReplayResult:
    seed: int | None
    ticks: int
    hashes: tuple[str, ...]
    final_hash: str


def load_replay_fixture(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def inputs_from_fixture(fixture: Mapping[str, Any]) -> dict[int, TickInput]:
    inputs = {}
    for entry in fixture.get("inputs", []):
        tick = int(entry["tick"])
        inputs[tick] = TickInput.from_names(
            press=entry.get("press", ()),
            release=entry.get("release", ()),
        )
    return inputs


def run_realtime_replay(fixture: Mapping[str, Any]) -> RealtimeReplayResult:
    seed = fixture.get("seed")
    ticks = int(fixture["ticks"])
    capture_every = int(fixture.get("capture_every", 1))
    simulator = RealtimeHeadlessSimulator(seed=seed)
    inputs = inputs_from_fixture(fixture)
    hashes = []
    for result in simulator.advance_ticks(ticks, inputs_by_tick=inputs):
        if result.tick % capture_every == 0:
            hashes.append(result.snapshot_hash)
    return RealtimeReplayResult(
        seed=seed,
        ticks=ticks,
        hashes=tuple(hashes),
        final_hash=simulator.state_hash(),
    )


def assert_replay_matches_fixture(fixture: Mapping[str, Any]) -> RealtimeReplayResult:
    result = run_realtime_replay(fixture)
    expected_final_hash = fixture.get("expected_final_hash")
    if expected_final_hash is not None and result.final_hash != expected_final_hash:
        raise AssertionError(
            f"final hash mismatch: expected {expected_final_hash}, got {result.final_hash}"
        )
    expected_hashes = fixture.get("expected_hashes")
    if expected_hashes is not None and tuple(expected_hashes) != result.hashes:
        raise AssertionError("captured replay hashes differ from fixture")
    return result
