"""Migrate a shape-compatible v1.7.2 checkpoint to BuildPotential v2."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import torch

from agents.v1_7_strategy_manager import (
    migrate_build_potential_v2_checkpoint_payload,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="pre-BuildPotential-v2 bootstrap checkpoint")
    parser.add_argument("output", help="new BuildPotential-v2-compatible checkpoint")
    parser.add_argument(
        "--force", action="store_true", help="replace an existing output"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    source = Path(args.source)
    output = Path(args.output)
    if not source.is_file():
        raise FileNotFoundError(f"checkpoint not found: {source}")
    if output.exists() and not args.force:
        raise FileExistsError(f"output already exists (use --force): {output}")
    payload = torch.load(source, map_location="cpu", weights_only=False)
    migrated = migrate_build_potential_v2_checkpoint_payload(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(migrated, output)
    print(f"migrated checkpoint: {output}")
    print(f"migration schema: {migrated['schema_migration']['schema_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
