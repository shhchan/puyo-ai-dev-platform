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
from selfplay.rating import update_elo
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
    received_ojama_player_0: int
    received_ojama_player_1: int
    max_chain_player_0: int
    max_chain_player_1: int

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

    @property
    def mean_steps(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.steps for match in self.matches) / len(self.matches)

    @property
    def mean_max_chain_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.max_chain_player_0 for match in self.matches) / len(self.matches)

    @property
    def mean_max_chain_player_1(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.max_chain_player_1 for match in self.matches) / len(self.matches)

    @property
    def mean_sent_ojama_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.sent_ojama_player_0 for match in self.matches) / len(self.matches)

    @property
    def mean_sent_ojama_player_1(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.sent_ojama_player_1 for match in self.matches) / len(self.matches)

    @property
    def mean_received_ojama_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.received_ojama_player_0 for match in self.matches) / len(self.matches)

    @property
    def mean_received_ojama_player_1(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.received_ojama_player_1 for match in self.matches) / len(self.matches)


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
        received_ojama_player_0=int(info_0["received_ojama_total"]),
        received_ojama_player_1=int(info_1["received_ojama_total"]),
        max_chain_player_0=int(info_0["max_chain_count"]),
        max_chain_player_1=int(info_1["max_chain_count"]),
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
                "received_ojama_player_0",
                "received_ojama_player_1",
                "max_chain_player_0",
                "max_chain_player_1",
            ],
        )
        writer.writeheader()
        for match in matches:
            writer.writerow(match.__dict__)


def elo_after_matches(
    matches: tuple[MatchResult, ...],
    *,
    rating_player_0: float = 1000.0,
    rating_player_1: float = 1000.0,
    k_factor: float = 32.0,
) -> tuple[float, float]:
    rating_0 = rating_player_0
    rating_1 = rating_player_1
    for match in matches:
        rating_0, rating_1 = update_elo(
            rating_0,
            rating_1,
            match.score_for_player_0,
            k_factor=k_factor,
        )
    return rating_0, rating_1


def summarize_result(
    result: ArenaResult,
    *,
    label: str,
    policy_a: str,
    policy_b: str,
    checkpoint_a: str | None,
    checkpoint_b: str | None,
    games: int,
    seed: int,
    max_steps: int,
    rating_a: float = 1000.0,
    rating_b: float = 1000.0,
    k_factor: float = 32.0,
) -> dict[str, Any]:
    final_rating_a, final_rating_b = elo_after_matches(
        result.matches,
        rating_player_0=rating_a,
        rating_player_1=rating_b,
        k_factor=k_factor,
    )
    return {
        "label": label,
        "policy_a": policy_a,
        "policy_b": policy_b,
        "checkpoint_a": checkpoint_a or "",
        "checkpoint_b": checkpoint_b or "",
        "games": games,
        "seed": seed,
        "max_steps": max_steps,
        "wins_player_0": result.wins_player_0,
        "wins_player_1": result.wins_player_1,
        "draws": result.draws,
        "win_rate_player_0": result.win_rate_player_0,
        "mean_steps": result.mean_steps,
        "mean_score_player_0": result.mean_score_player_0,
        "mean_score_player_1": result.mean_score_player_1,
        "mean_max_chain_player_0": result.mean_max_chain_player_0,
        "mean_max_chain_player_1": result.mean_max_chain_player_1,
        "mean_sent_ojama_player_0": result.mean_sent_ojama_player_0,
        "mean_sent_ojama_player_1": result.mean_sent_ojama_player_1,
        "mean_received_ojama_player_0": result.mean_received_ojama_player_0,
        "mean_received_ojama_player_1": result.mean_received_ojama_player_1,
        "initial_rating_player_0": rating_a,
        "initial_rating_player_1": rating_b,
        "final_rating_player_0": final_rating_a,
        "final_rating_player_1": final_rating_b,
        "elo_delta_player_0": final_rating_a - rating_a,
        "k_factor": k_factor,
    }


def write_summary_csv(path: str | Path, summary: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary.keys()))
        writer.writeheader()
        writer.writerow(summary)


def write_markdown_report(path: str | Path, summary: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("games", summary["games"]),
        ("wins_player_0", summary["wins_player_0"]),
        ("wins_player_1", summary["wins_player_1"]),
        ("draws", summary["draws"]),
        ("win_rate_player_0", f"{summary['win_rate_player_0']:.3f}"),
        ("mean_score_player_0", f"{summary['mean_score_player_0']:.2f}"),
        ("mean_score_player_1", f"{summary['mean_score_player_1']:.2f}"),
        ("mean_max_chain_player_0", f"{summary['mean_max_chain_player_0']:.2f}"),
        ("mean_max_chain_player_1", f"{summary['mean_max_chain_player_1']:.2f}"),
        ("elo_delta_player_0", f"{summary['elo_delta_player_0']:.2f}"),
    ]
    lines = [
        f"# Arena Report: {summary['label']}",
        "",
        f"- policy_a: `{summary['policy_a']}`",
        f"- policy_b: `{summary['policy_b']}`",
        f"- checkpoint_a: `{summary['checkpoint_a'] or '-'}`",
        f"- checkpoint_b: `{summary['checkpoint_b'] or '-'}`",
        f"- seed: `{summary['seed']}`",
        f"- max_steps: `{summary['max_steps']}`",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in rows)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate two Puyo policies headlessly.")
    parser.add_argument("--policy-a", choices=["first", "random", "greedy", "beam", "checkpoint"], default="greedy")
    parser.add_argument("--policy-b", choices=["first", "random", "greedy", "beam", "checkpoint"], default="random")
    parser.add_argument("--checkpoint-a", default=None)
    parser.add_argument("--checkpoint-b", default=None)
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--beam-depth", type=int, default=5)
    parser.add_argument("--beam-width", type=int, default=32)
    parser.add_argument("--beam-scenarios", type=int, default=1)
    parser.add_argument("--csv", default=None, help="Optional path to write per-match results.")
    parser.add_argument("--summary-csv", default=None, help="Optional path to write one-row aggregate metrics.")
    parser.add_argument("--markdown", default=None, help="Optional path to write a Markdown arena report.")
    parser.add_argument("--label", default=None, help="Label stored in aggregate reports.")
    parser.add_argument("--rating-a", type=float, default=1000.0)
    parser.add_argument("--rating-b", type=float, default=1000.0)
    parser.add_argument("--k-factor", type=float, default=32.0)
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
    label = args.label or f"{args.policy_a}_vs_{args.policy_b}"
    summary = summarize_result(
        result,
        label=label,
        policy_a=args.policy_a,
        policy_b=args.policy_b,
        checkpoint_a=args.checkpoint_a,
        checkpoint_b=args.checkpoint_b,
        games=args.games,
        seed=args.seed,
        max_steps=args.max_steps,
        rating_a=args.rating_a,
        rating_b=args.rating_b,
        k_factor=args.k_factor,
    )
    if args.summary_csv:
        write_summary_csv(args.summary_csv, summary)
    if args.markdown:
        write_markdown_report(args.markdown, summary)

    total = len(result.matches)
    print(f"games: {total}")
    print(f"player_0_wins: {result.wins_player_0}")
    print(f"player_1_wins: {result.wins_player_1}")
    print(f"draws: {result.draws}")
    print(f"player_0_win_rate: {result.win_rate_player_0:.3f}")
    print(f"mean_score_player_0: {result.mean_score_player_0:.2f}")
    print(f"mean_score_player_1: {result.mean_score_player_1:.2f}")
    print(f"mean_max_chain_player_0: {result.mean_max_chain_player_0:.2f}")
    print(f"mean_max_chain_player_1: {result.mean_max_chain_player_1:.2f}")
    print(f"final_rating_player_0: {summary['final_rating_player_0']:.2f}")
    print(f"elo_delta_player_0: {summary['elo_delta_player_0']:.2f}")


if __name__ == "__main__":
    main()
