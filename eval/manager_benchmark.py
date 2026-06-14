"""Reproducible benchmark matrix for a trained strategy manager."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from eval.arena import (
    run_parallel_paired_series,
    summarize_result,
    write_markdown_report,
    write_matches_csv,
    write_summary_csv,
)


BASELINES = (
    "previous_manager",
    "manager_rule",
    "worker_large",
    "worker_quick",
    "worker_punish",
    "worker_counter",
    "worker_fire",
    "worker_survival",
    "greedy",
    "puyo29_beam",
)


def _policy_spec(
    policy_type: str,
    *,
    seed: int,
    checkpoint_path: str | None = None,
    beam_depth: int = 10,
    beam_width: int = 48,
) -> dict[str, Any]:
    return {
        "policy_type": policy_type,
        "seed": seed,
        "checkpoint_path": checkpoint_path,
        "device": "cpu",
        "deterministic": True,
        "beam_depth": beam_depth,
        "beam_width": beam_width,
        "beam_scenarios": 1,
        "beam_minimum_chain": 6,
    }


def _baseline_spec(name: str, args) -> dict[str, Any]:
    if name == "previous_manager":
        if not args.previous_checkpoint:
            raise ValueError("previous_manager requires --previous-checkpoint")
        return _policy_spec(
            "manager",
            seed=args.seed + 10_000,
            checkpoint_path=args.previous_checkpoint,
        )
    if name == "puyo29_beam":
        return _policy_spec(
            "beam",
            seed=args.seed + 10_000,
            beam_depth=args.beam_depth,
            beam_width=args.beam_width,
        )
    return _policy_spec(name, seed=args.seed + 10_000)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark a manager against the PUYO-51 matrix.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--previous-checkpoint", default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--games", type=int, default=50, help="Seeds; paired execution produces twice as many matches.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--beam-depth", type=int, default=10)
    parser.add_argument("--beam-width", type=int, default=48)
    parser.add_argument("--baselines", nargs="+", choices=BASELINES, default=list(BASELINES))
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manager_spec = _policy_spec("manager", seed=args.seed, checkpoint_path=args.checkpoint)
    summaries = []

    for baseline in args.baselines:
        result = run_parallel_paired_series(
            manager_spec,
            _baseline_spec(baseline, args),
            games=args.games,
            seed=args.seed,
            max_steps=args.max_steps,
            workers=args.workers,
        )
        summary = summarize_result(
            result,
            label=f"manager_vs_{baseline}",
            policy_a="manager",
            policy_b=baseline,
            checkpoint_a=args.checkpoint,
            checkpoint_b=args.previous_checkpoint if baseline == "previous_manager" else None,
            games=len(result.matches),
            seed=args.seed,
            max_steps=args.max_steps,
        )
        summaries.append(summary)
        write_matches_csv(output_dir / f"{baseline}_matches.csv", result.matches)
        write_summary_csv(output_dir / f"{baseline}_summary.csv", summary)
        write_markdown_report(output_dir / f"{baseline}.md", summary)
        print(
            f"{baseline}: score_rate={summary['score_rate_policy_a']:.3f} "
            f"wins={summary['wins_policy_a']} losses={summary['wins_policy_b']} "
            f"draws={summary['draws']}"
        )

    (output_dir / "summary.json").write_text(
        json.dumps(summaries, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if summaries:
        with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
            writer.writeheader()
            writer.writerows(summaries)


if __name__ == "__main__":
    main()
