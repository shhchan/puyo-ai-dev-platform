"""Headless arena for evaluating two Puyo policies."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from puyo_env.versus_env import VersusPuyoEnv
from selfplay.policies import Policy, make_policy


@dataclass(frozen=True)
class MatchResult:
    seed: int
    winner: str | None
    steps: int
    score_player_0: int
    score_player_1: int
    sent_ojama_player_0: int
    sent_ojama_player_1: int

    @property
    def score_for_player_0(self) -> float:
        if self.winner == "player_0":
            return 1.0
        if self.winner == "player_1":
            return 0.0
        return 0.5


@dataclass(frozen=True)
class ArenaResult:
    matches: tuple[MatchResult, ...]

    @property
    def wins_player_0(self) -> int:
        return sum(1 for match in self.matches if match.winner == "player_0")

    @property
    def wins_player_1(self) -> int:
        return sum(1 for match in self.matches if match.winner == "player_1")

    @property
    def draws(self) -> int:
        return sum(1 for match in self.matches if match.winner is None)

    @property
    def win_rate_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return self.wins_player_0 / len(self.matches)

    @property
    def mean_score_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.score_player_0 for match in self.matches) / len(self.matches)

    @property
    def mean_score_player_1(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.score_player_1 for match in self.matches) / len(self.matches)


def run_match(
    policy_player_0: Policy,
    policy_player_1: Policy,
    *,
    seed: int,
    max_steps: int = 500,
) -> MatchResult:
    env = VersusPuyoEnv(seed=seed, max_steps=max_steps)
    observations, infos = env.reset(seed=seed)

    while env.agents:
        actions = {
            agent: (
                policy_player_0.select_action(observations[agent], infos[agent])
                if agent == "player_0"
                else policy_player_1.select_action(observations[agent], infos[agent])
            )
            for agent in env.agents
        }
        observations, _, _, _, infos = env.step(actions)

    info_0 = infos["player_0"]
    info_1 = infos["player_1"]
    return MatchResult(
        seed=seed,
        winner=info_0.get("winner"),
        steps=int(info_0.get("step_count", max_steps)),
        score_player_0=int(info_0["score"]),
        score_player_1=int(info_1["score"]),
        sent_ojama_player_0=int(info_0["sent_ojama_total"]),
        sent_ojama_player_1=int(info_1["sent_ojama_total"]),
    )


def run_series(
    policy_player_0: Policy,
    policy_player_1: Policy,
    *,
    games: int = 20,
    seed: int = 1,
    max_steps: int = 500,
) -> ArenaResult:
    matches = tuple(
        run_match(
            policy_player_0,
            policy_player_1,
            seed=seed + game_index,
            max_steps=max_steps,
        )
        for game_index in range(games)
    )
    return ArenaResult(matches=matches)


def write_matches_csv(path: str | Path, matches: tuple[MatchResult, ...]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "seed",
                "winner",
                "steps",
                "score_player_0",
                "score_player_1",
                "sent_ojama_player_0",
                "sent_ojama_player_1",
            ],
        )
        writer.writeheader()
        for match in matches:
            writer.writerow(match.__dict__)


def _policy_from_args(args, side: str) -> Policy:
    return make_policy(
        getattr(args, f"policy_{side}"),
        seed=args.seed + (0 if side == "a" else 10_000),
        checkpoint_path=getattr(args, f"checkpoint_{side}"),
        device=args.device,
        deterministic=args.deterministic,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate two Puyo policies headlessly.")
    parser.add_argument("--policy-a", choices=["first", "random", "greedy", "checkpoint"], default="greedy")
    parser.add_argument("--policy-b", choices=["first", "random", "greedy", "checkpoint"], default="random")
    parser.add_argument("--checkpoint-a", default=None)
    parser.add_argument("--checkpoint-b", default=None)
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--csv", default=None, help="Optional path to write per-match results.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = run_series(
        _policy_from_args(args, "a"),
        _policy_from_args(args, "b"),
        games=args.games,
        seed=args.seed,
        max_steps=args.max_steps,
    )
    if args.csv:
        write_matches_csv(args.csv, result.matches)

    total = len(result.matches)
    print(f"games: {total}")
    print(f"player_0_wins: {result.wins_player_0}")
    print(f"player_1_wins: {result.wins_player_1}")
    print(f"draws: {result.draws}")
    print(f"player_0_win_rate: {result.win_rate_player_0:.3f}")
    print(f"mean_score_player_0: {result.mean_score_player_0:.2f}")
    print(f"mean_score_player_1: {result.mean_score_player_1:.2f}")


if __name__ == "__main__":
    main()
