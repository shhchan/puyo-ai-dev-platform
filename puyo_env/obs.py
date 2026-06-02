"""Observation encoding for placement-level Puyo environments."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency guard
    np = None

from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor
from src.core.game import GameState
from src.core.headless import HeadlessPuyoSimulator


BOARD_ROWS = GRID_HEIGHT - 1
BOARD_COLOR_CHANNELS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
    PuyoColor.PURPLE,
    PuyoColor.OJAMA,
)
NORMAL_COLOR_CHANNELS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
    PuyoColor.PURPLE,
)
VISIBLE_PAIR_COUNT = 3
SCALAR_FEATURE_DIM = 4

_BOARD_COLOR_TO_INDEX = {color: index for index, color in enumerate(BOARD_COLOR_CHANNELS)}
_NORMAL_COLOR_TO_INDEX = {color: index for index, color in enumerate(NORMAL_COLOR_CHANNELS)}


def _require_numpy():
    if np is None:
        raise ImportError("puyo_env observation encoding requires numpy. Install requirements.txt.")
    return np


def encode_board(game: GameState, dtype: Any | None = None):
    """Encode rows y=0..12 as one-hot channels with row 0 at the top."""

    numpy = _require_numpy()
    output_dtype = dtype or numpy.float32
    board = numpy.zeros(
        (len(BOARD_COLOR_CHANNELS), BOARD_ROWS, GRID_WIDTH),
        dtype=output_dtype,
    )
    for y in range(BOARD_ROWS):
        encoded_row = BOARD_ROWS - 1 - y
        for x in range(GRID_WIDTH):
            color = game.field.get_puyo(x, y).color
            channel = _BOARD_COLOR_TO_INDEX.get(color)
            if channel is not None:
                board[channel, encoded_row, x] = 1.0
    return board


def visible_pairs(game: GameState, count: int = VISIBLE_PAIR_COUNT) -> list[tuple[Any, Any]]:
    """Return current pair followed by queued next pairs, padded by omission."""

    pairs = []
    if game.current_puyo_1 is not None and game.current_puyo_2 is not None:
        pairs.append((game.current_puyo_1, game.current_puyo_2))
    pairs.extend(list(game.next_puyo_queue))
    return pairs[:count]


def encode_next_pairs(game: GameState, count: int = VISIBLE_PAIR_COUNT, dtype: Any | None = None):
    """One-hot encode the visible current/next/next-next pair colors."""

    numpy = _require_numpy()
    output_dtype = dtype or numpy.float32
    encoded = numpy.zeros(
        (count, 2, len(NORMAL_COLOR_CHANNELS)),
        dtype=output_dtype,
    )
    for pair_index, pair in enumerate(visible_pairs(game, count=count)):
        for puyo_index, puyo in enumerate(pair[:2]):
            channel = _NORMAL_COLOR_TO_INDEX.get(puyo.color)
            if channel is not None:
                encoded[pair_index, puyo_index, channel] = 1.0
    return encoded


def encode_scalars(
    game: GameState,
    step_count: int,
    max_steps: int,
    pending_ojama: int = 0,
    sent_ojama: int = 0,
    dtype: Any | None = None,
):
    """Encode small non-spatial features with bounded magnitudes."""

    numpy = _require_numpy()
    output_dtype = dtype or numpy.float32
    safe_max_steps = max(1, max_steps)
    return numpy.asarray(
        [
            min(float(pending_ojama) / 30.0, 1.0),
            min(float(sent_ojama) / 30.0, 1.0),
            min(float(game.score) / 100000.0, 1.0),
            min(float(step_count) / float(safe_max_steps), 1.0),
        ],
        dtype=output_dtype,
    )


def flatten_vector_features(observation: dict[str, Any]):
    """Flatten non-spatial observation entries for MLP inputs."""

    numpy = _require_numpy()
    return numpy.concatenate(
        [
            observation["next_pairs"].reshape(-1),
            observation["scalars"].reshape(-1),
        ]
    ).astype(numpy.float32, copy=False)


def encode_observation(
    simulator: HeadlessPuyoSimulator,
    step_count: int,
    max_steps: int,
    action_mask: Sequence[bool] | None = None,
    include_action_mask: bool = False,
) -> dict[str, Any]:
    """Build the Gymnasium observation dictionary."""

    numpy = _require_numpy()
    game = simulator.game
    observation = {
        "board": encode_board(game),
        "next_pairs": encode_next_pairs(game),
        "scalars": encode_scalars(game, step_count=step_count, max_steps=max_steps),
    }
    if include_action_mask:
        if action_mask is None:
            raise ValueError("action_mask is required when include_action_mask=True")
        observation["action_mask"] = numpy.asarray(action_mask, dtype=numpy.int8)
    return observation


def make_observation_space(spaces: Any, include_action_mask: bool = False, action_count: int = 0):
    """Create the Gymnasium Dict observation space."""

    numpy = _require_numpy()
    entries = {
        "board": spaces.Box(
            low=0.0,
            high=1.0,
            shape=(len(BOARD_COLOR_CHANNELS), BOARD_ROWS, GRID_WIDTH),
            dtype=numpy.float32,
        ),
        "next_pairs": spaces.Box(
            low=0.0,
            high=1.0,
            shape=(VISIBLE_PAIR_COUNT, 2, len(NORMAL_COLOR_CHANNELS)),
            dtype=numpy.float32,
        ),
        "scalars": spaces.Box(
            low=0.0,
            high=1.0,
            shape=(SCALAR_FEATURE_DIM,),
            dtype=numpy.float32,
        ),
    }
    if include_action_mask:
        entries["action_mask"] = spaces.MultiBinary(action_count)
    return spaces.Dict(entries)
