"""Build a reproducible paired-side schedule from the v1.7.2 opponent pool."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from selfplay.opponent_pool import OpponentPool, write_schedule_artifact


DEFAULT_CONFIG = "train/config/v1_7_2_opponent_pool.json"
DEFAULT_OUTPUT = "runs/v1_7_opponent_pool/opponent_schedule.json"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve the v1.7.2 pool and emit a reproducible match schedule."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--pairs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=172129)
    parser.add_argument("--target-rating", type=float, default=1000.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    pool = OpponentPool.load(args.config)
    assignments = pool.build_schedule(
        pairs=args.pairs,
        seed=args.seed,
        target_rating=args.target_rating,
    )
    artifact = write_schedule_artifact(
        Path(args.output),
        pool,
        assignments,
        pairs=args.pairs,
        seed=args.seed,
    )
    summary = artifact["summary"]
    print(f"pool_id: {pool.pool_id}")
    print(f"pairs: {args.pairs}")
    print(f"matches: {len(assignments)}")
    print(f"pairs_by_stratum: {summary['pairs_by_stratum']}")
    print(f"fallback_pairs: {summary['fallback_pairs']}")
    print(f"artifact: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
