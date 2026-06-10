"""Benchmark chain-building policies on deterministic single-player games."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from puyo_env.actions import action_to_placement, legal_action_mask
from selfplay.policies import make_policy
from src.core.headless import HeadlessPuyoSimulator


@dataclass(frozen=True)
class ChainSearchResult:
    policy: str
    seed: int
    steps: int
    score: int
    max_chain: int
    chains_fired: int
    mean_fired_chain: float
    elapsed_seconds: float
    mean_decision_ms: float


def run_game(policy, *, policy_name: str, seed: int, max_steps: int) -> ChainSearchResult:
    simulator = HeadlessPuyoSimulator(seed=seed)
    chain_counts = []
    decision_seconds = []

    for step in range(max_steps):
        if simulator.game.game_over:
            break
        info = {"action_mask": legal_action_mask(simulator), "simulator": simulator}
        started = time.perf_counter()
        action = policy.select_action({}, info)
        decision_seconds.append(time.perf_counter() - started)
        result = simulator.step(action_to_placement(action))
        if not result.valid:
            break
        if result.chain_count > 0:
            chain_counts.append(result.chain_count)
    else:
        step = max_steps - 1

    steps = step + 1 if max_steps > 0 else 0
    return ChainSearchResult(
        policy=policy_name,
        seed=seed,
        steps=steps,
        score=simulator.game.score,
        max_chain=max(chain_counts, default=0),
        chains_fired=len(chain_counts),
        mean_fired_chain=mean(chain_counts) if chain_counts else 0.0,
        elapsed_seconds=sum(decision_seconds),
        mean_decision_ms=mean(decision_seconds) * 1000.0 if decision_seconds else 0.0,
    )


def run_benchmark(
    policy_names: list[str],
    *,
    games: int,
    seed: int,
    max_steps: int,
    beam_depth: int,
    beam_width: int,
    beam_scenarios: int,
) -> tuple[ChainSearchResult, ...]:
    results = []
    for policy_name in policy_names:
        for game_index in range(games):
            game_seed = seed + game_index
            policy = make_policy(
                policy_name,
                seed=game_seed + 100_000,
                beam_depth=beam_depth,
                beam_width=beam_width,
                beam_scenarios=beam_scenarios,
            )
            results.append(run_game(policy, policy_name=policy_name, seed=game_seed, max_steps=max_steps))
    return tuple(results)


def summarize(results: tuple[ChainSearchResult, ...]) -> dict[str, dict[str, float]]:
    summaries = {}
    for policy_name in sorted({result.policy for result in results}):
        selected = [result for result in results if result.policy == policy_name]
        summaries[policy_name] = {
            "games": len(selected),
            "mean_score": mean(result.score for result in selected),
            "mean_max_chain": mean(result.max_chain for result in selected),
            "best_chain": max(result.max_chain for result in selected),
            "mean_steps": mean(result.steps for result in selected),
            "mean_decision_ms": mean(result.mean_decision_ms for result in selected),
        }
    return summaries


def write_csv(path: str | Path, results: tuple[ChainSearchResult, ...]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(result) for result in results)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark deterministic chain construction policies.")
    parser.add_argument("--policies", nargs="+", choices=["first", "random", "greedy", "beam"], default=["random", "greedy", "beam"])
    parser.add_argument("--games", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=64)
    parser.add_argument("--beam-depth", type=int, default=5)
    parser.add_argument("--beam-width", type=int, default=32)
    parser.add_argument("--beam-scenarios", type=int, default=1)
    parser.add_argument("--csv", default=None)
    parser.add_argument("--summary-json", default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    results = run_benchmark(
        args.policies,
        games=args.games,
        seed=args.seed,
        max_steps=args.max_steps,
        beam_depth=args.beam_depth,
        beam_width=args.beam_width,
        beam_scenarios=args.beam_scenarios,
    )
    summary = summarize(results)
    if args.csv:
        write_csv(args.csv, results)
    if args.summary_json:
        target = Path(args.summary_json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for policy_name, metrics in summary.items():
        print(
            f"{policy_name}: mean_score={metrics['mean_score']:.1f} "
            f"mean_max_chain={metrics['mean_max_chain']:.2f} best_chain={metrics['best_chain']:.0f} "
            f"mean_steps={metrics['mean_steps']:.1f} mean_decision_ms={metrics['mean_decision_ms']:.1f}"
        )


if __name__ == "__main__":
    main()
