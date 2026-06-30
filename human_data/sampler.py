"""Reconstruct placement-policy samples from fixed-tick human trajectories."""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from puyo_env.actions import ACTION_TO_INDEX
from puyo_env.realtime_ai import build_realtime_info, build_realtime_observation
from puyo_env.realtime_versus import REALTIME_AGENTS, RealtimeVersusMatch
from src.core.constants import Direction
from src.core.headless import PlacementAction
from src.core.realtime import TickInput

from human_data.dataset import validate_session
from train.artifacts import file_sha256


SAMPLER_SCHEMA_VERSION = "puyo.human_placement_samples.v1"
TRAINING_METHODS = ("imitation", "advantage_weighted", "mixed_replay")


@dataclass(frozen=True)
class PlacementSample:
    session_id: str
    agent: str
    source: str
    tick: int
    observation: dict[str, Any]
    action_mask: Any
    action_index: int
    weight: float


@dataclass(frozen=True)
class SampledDataset:
    samples: tuple[PlacementSample, ...]
    selection: dict[str, Any]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def selected_session_dirs(dataset_root: str | Path, session_ids: Iterable[str] = ()) -> list[Path]:
    root = Path(dataset_root)
    requested = tuple(dict.fromkeys(str(value) for value in session_ids if value))
    if requested:
        result = [root / "sessions" / session_id for session_id in requested]
        missing = [path.name for path in result if not path.is_dir()]
        if missing:
            raise ValueError(f"selected sessions do not exist: {', '.join(missing)}")
        return result
    return sorted(path for path in (root / "sessions").glob("*") if path.is_dir())


def _outcome_advantage(outcome: Mapping[str, Any], agent: str) -> float:
    winner = outcome.get("winner")
    if winner is None:
        return 0.5
    return 1.0 if winner == agent else 0.0


def _reconstruct_session(session_dir: Path) -> tuple[list[PlacementSample], dict[str, Any]]:
    errors = validate_session(session_dir, verify_replay=True)
    if errors:
        raise ValueError(f"invalid session {session_dir.name}: {'; '.join(errors)}")
    manifest_path = session_dir / "human_session_manifest.json"
    trajectory_path = session_dir / "trajectory.json"
    manifest = _read_json(manifest_path)
    trajectory = _read_json(trajectory_path)
    match = RealtimeVersusMatch(seed=trajectory["seed"])
    pending_observations: dict[str, dict[str, Any] | None] = {agent: None for agent in REALTIME_AGENTS}
    pending_masks: dict[str, Any] = {agent: None for agent in REALTIME_AGENTS}
    samples: list[PlacementSample] = []

    for tick in trajectory["ticks"]:
        for agent in REALTIME_AGENTS:
            game = match.player_states[agent].simulator.game
            if pending_observations[agent] is None and game.state == "control" and not game.game_over:
                pending_observations[agent] = copy.deepcopy(build_realtime_observation(match, agent))
                pending_masks[agent] = copy.deepcopy(
                    build_realtime_info(match, agent, use_reachable_action_mask=False)["action_mask"]
                )
        inputs = {
            agent: TickInput.from_names(**tick["inputs"][agent])
            for agent in REALTIME_AGENTS
        }
        result = match.step(inputs)
        if result.snapshot_hash != tick["snapshot_hash"]:
            raise ValueError(f"session {session_dir.name} diverged at tick {tick['tick']}")
        for agent, player_result in result.player_results.items():
            for event in player_result.events:
                if event.type != "lock" or pending_observations[agent] is None:
                    continue
                placement = PlacementAction(
                    axis_x=int(event.data["axis_x"]),
                    rotation=Direction[str(event.data["rotation"])],
                )
                action_index = ACTION_TO_INDEX.get(placement)
                if action_index is not None:
                    policy = str(manifest["models"][agent].get("policy") or "human")
                    samples.append(
                        PlacementSample(
                            session_id=manifest["session_id"],
                            agent=agent,
                            source="human" if policy == "human" else "self_play",
                            tick=int(tick["tick"]),
                            observation=pending_observations[agent],
                            action_mask=pending_masks[agent],
                            action_index=action_index,
                            weight=_outcome_advantage(manifest.get("outcome", {}), agent),
                        )
                    )
                pending_observations[agent] = None
                pending_masks[agent] = None
    return samples, {
        "session_id": manifest["session_id"],
        "manifest_path": str(manifest_path),
        "manifest_sha256": file_sha256(manifest_path),
        "trajectory_sha256": file_sha256(trajectory_path),
    }


def sample_human_dataset(
    dataset_root: str | Path,
    *,
    session_ids: Iterable[str] = (),
    method: str = "imitation",
    self_play_ratio: float = 0.0,
    minimum_advantage: float = 0.0,
    seed: int = 1,
) -> SampledDataset:
    """Return deterministic placement samples and an immutable selection record."""
    if method not in TRAINING_METHODS:
        raise ValueError(f"method must be one of: {', '.join(TRAINING_METHODS)}")
    if not 0.0 <= self_play_ratio <= 1.0:
        raise ValueError("self_play_ratio must be between 0 and 1")
    records: list[dict[str, Any]] = []
    reconstructed: list[PlacementSample] = []
    for session_dir in selected_session_dirs(dataset_root, session_ids):
        session_samples, record = _reconstruct_session(session_dir)
        reconstructed.extend(session_samples)
        records.append(record)

    human = [sample for sample in reconstructed if sample.source == "human"]
    if method == "advantage_weighted":
        human = [sample for sample in human if sample.weight >= minimum_advantage]
    else:
        human = [PlacementSample(**{**sample.__dict__, "weight": 1.0}) for sample in human]
    selected = list(human)
    if method == "mixed_replay" and self_play_ratio > 0.0:
        replay = [sample for sample in reconstructed if sample.source == "self_play"]
        replay_count = min(len(replay), round(len(human) * self_play_ratio / max(1e-9, 1.0 - self_play_ratio)))
        random.Random(seed).shuffle(replay)
        selected.extend(PlacementSample(**{**sample.__dict__, "weight": 1.0}) for sample in replay[:replay_count])
    random.Random(seed).shuffle(selected)
    if not selected:
        raise ValueError("dataset selection produced no trainable human placement samples")
    selection = {
        "schema_version": SAMPLER_SCHEMA_VERSION,
        "dataset_root": str(Path(dataset_root)),
        "method": method,
        "seed": int(seed),
        "self_play_ratio": float(self_play_ratio),
        "minimum_advantage": float(minimum_advantage),
        "sessions": records,
        "sample_count": len(selected),
        "human_sample_count": sum(sample.source == "human" for sample in selected),
        "self_play_sample_count": sum(sample.source == "self_play" for sample in selected),
    }
    return SampledDataset(samples=tuple(selected), selection=selection)
