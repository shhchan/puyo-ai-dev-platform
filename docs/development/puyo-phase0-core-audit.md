# Phase 0 core audit

PUYO-13 covers the first simulator split for reinforcement-learning use. The goal is to keep the existing human-play implementation intact while exposing a deterministic, headless stepping surface.

## Reused core logic

- `src/core/field.py`
  - Board storage, gravity, vanish-group detection, and adjacent ojama clearing.
  - `Field.to_color_grid()` was added for deterministic state comparison in tests.
- `src/core/game.py`
  - `GameState`, active pair placement, movement legality, lock, chain scoring, and game-over detection.
  - `resolve_chains_synchronously()` was added to run the same chain/scoring path without animation timers.
  - `place_current_pair_and_resolve()` was added as a placement-level one-hand API.
- `src/core/constants.py`
  - Board size, action/rotation enums, and Puyo Puyo Tsu score bonus tables remain the source of truth.
- `src/core/puyo.py`
  - Puyo color/type checks are reused unchanged.

## Headless API

- `src/core/tsumo.py`
  - `PuyoSequence(seed=...)` owns random pair generation, avoiding global `random.choice` state.
- `src/core/headless.py`
  - `HeadlessPuyoSimulator(seed=...)` starts directly in `control` state.
  - `legal_actions()` returns valid placement actions for the current board and active pair.
  - `step((axis_x, rotation))` hard-drops the active pair, locks it, resolves all chains synchronously, spawns the next pair, and returns score/chain/game-over data.

The API depends only on `src/core`; it does not import Gymnasium, PettingZoo, PyTorch, or other RL-layer packages.

## Golden tests added

`tests/test_headless_simulator.py` fixes the Phase 0 baseline:

- seed reproducibility for queue and simulation result
- legal placement count on an empty board
- one-chain score
- two-chain score
- simultaneous two-color clear score
- adjacent ojama clearing without ojama score
- invalid placement reporting
- choke-point game-over after a headless step

## Known gaps for later phases

- The placement API models final placement by hard drop from spawn. It does not search frame-level routes or mawashi paths.
- Ojama reservation, sousai, attack-point conversion, margin time, and same-tsumo versus supply are still unimplemented.
- Tsu opening color constraints are not modeled yet; `PuyoSequence` currently samples uniformly from the four normal colors.
- Local smoke benchmark on this workspace: 1,000 headless steps in 0.1176s, about 8,503 steps/s.
- Vectorized/batched stepping is not implemented.
