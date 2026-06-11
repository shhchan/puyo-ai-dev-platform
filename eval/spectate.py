"""Text-mode spectator for one headless versus match."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from puyo_env.actions import action_to_placement
from puyo_env.versus_env import VersusPuyoEnv
from selfplay.policies import Policy, make_policy
from src.core.constants import GRID_WIDTH, PuyoColor, VISIBLE_HEIGHT
from src.core.game import GameState


COLOR_SYMBOLS = {
    PuyoColor.EMPTY: ".",
    PuyoColor.RED: "R",
    PuyoColor.BLUE: "B",
    PuyoColor.GREEN: "G",
    PuyoColor.YELLOW: "Y",
    PuyoColor.PURPLE: "P",
    PuyoColor.OJAMA: "O",
    PuyoColor.WALL: "#",
}


def render_board(game: GameState) -> list[str]:
    """Render visible rows from top to bottom."""

    rows = []
    for y in reversed(range(VISIBLE_HEIGHT)):
        rows.append("".join(COLOR_SYMBOLS.get(game.field.get_puyo(x, y).color, "?") for x in range(GRID_WIDTH)))
    return rows


def _format_action(action_index: int) -> str:
    placement = action_to_placement(action_index)
    return f"x={placement.axis_x}, rot={placement.rotation.name.lower()}"


def render_side_by_side(env: VersusPuyoEnv, infos: dict, actions: dict[str, int] | None = None) -> str:
    """Return a compact two-board text snapshot."""

    state_0 = env.player_states["player_0"]
    state_1 = env.player_states["player_1"]
    board_0 = render_board(state_0.simulator.game)
    board_1 = render_board(state_1.simulator.game)
    action_0 = "-" if actions is None else _format_action(actions["player_0"])
    action_1 = "-" if actions is None else _format_action(actions["player_1"])
    lines = [
        f"step={env.step_count}  winner={infos['player_0'].get('winner')}",
        (
            "player_0 "
            f"score={infos['player_0']['score']} pending={infos['player_0']['pending_ojama']} "
            f"sent={infos['player_0']['sent_ojama_total']} action={action_0}"
        ),
        (
            "player_1 "
            f"score={infos['player_1']['score']} pending={infos['player_1']['pending_ojama']} "
            f"sent={infos['player_1']['sent_ojama_total']} action={action_1}"
        ),
        "  P0      P1",
    ]
    lines.extend(f"{left}  {right}" for left, right in zip(board_0, board_1))
    return "\n".join(lines)


def run_spectated_match(
    policy_player_0: Policy,
    policy_player_1: Policy,
    *,
    seed: int = 1,
    max_steps: int = 100,
    delay: float = 0.0,
    output: TextIO = sys.stdout,
) -> dict:
    """Run one match and print every board state."""

    env = VersusPuyoEnv(seed=seed, max_steps=max_steps)
    observations, infos = env.reset(seed=seed)
    print(render_side_by_side(env, infos), file=output)

    while env.agents:
        actions = {
            "player_0": policy_player_0.select_action(observations["player_0"], infos["player_0"]),
            "player_1": policy_player_1.select_action(observations["player_1"], infos["player_1"]),
        }
        observations, rewards, _, _, infos = env.step(actions)
        print("", file=output)
        print(render_side_by_side(env, infos, actions=actions), file=output)
        print(f"rewards: player_0={rewards['player_0']:.3f} player_1={rewards['player_1']:.3f}", file=output)
        if delay > 0:
            time.sleep(delay)

    return {
        "winner": infos["player_0"].get("winner"),
        "score_player_0": infos["player_0"]["score"],
        "score_player_1": infos["player_1"]["score"],
        "steps": infos["player_0"].get("step_count", max_steps),
    }


def _policy_from_args(args, side: str) -> Policy:
    return make_policy(
        getattr(args, f"policy_{side}"),
        seed=args.seed + (0 if side == "a" else 10_000),
        checkpoint_path=getattr(args, f"checkpoint_{side}"),
        device=args.device,
        deterministic=args.deterministic,
        beam_depth=args.beam_depth,
        beam_width=args.beam_width,
        beam_scenarios=args.beam_scenarios,
        beam_minimum_chain=args.beam_minimum_chain,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Watch one headless Puyo versus match in text mode.")
    parser.add_argument("--policy-a", choices=["first", "random", "greedy", "beam", "checkpoint"], default="checkpoint")
    parser.add_argument("--policy-b", choices=["first", "random", "greedy", "beam", "checkpoint"], default="random")
    parser.add_argument("--checkpoint-a", default=None)
    parser.add_argument("--checkpoint-b", default=None)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--beam-depth", type=int, default=10)
    parser.add_argument("--beam-width", type=int, default=48)
    parser.add_argument("--beam-scenarios", type=int, default=1)
    parser.add_argument("--beam-minimum-chain", type=int, default=6)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = run_spectated_match(
        _policy_from_args(args, "a"),
        _policy_from_args(args, "b"),
        seed=args.seed,
        max_steps=args.max_steps,
        delay=args.delay,
    )
    print(
        f"\nresult: winner={result['winner']} "
        f"score_player_0={result['score_player_0']} score_player_1={result['score_player_1']} "
        f"steps={result['steps']}"
    )


if __name__ == "__main__":
    main()
