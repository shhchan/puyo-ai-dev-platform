"""CLI entrypoint for Phase 1 flat PPO training."""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.flat_ppo import FlatPPOConfig, train_flat_ppo


def _load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _coerce_value(value: str, current):
    if isinstance(current, bool):
        return value.lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Train a flat PPO Puyo agent.")
    parser.add_argument("--config", default="train/config/flat.yaml", help="YAML config path.")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override a config value. Example: --set total_timesteps=20000",
    )
    return parser.parse_args(argv)


def build_config(args) -> FlatPPOConfig:
    values = _load_config(Path(args.config))
    defaults = FlatPPOConfig()
    valid_fields = {field.name for field in fields(FlatPPOConfig)}
    for override in args.set:
        if "=" not in override:
            raise ValueError(f"override must be KEY=VALUE: {override}")
        key, raw_value = override.split("=", 1)
        if key not in valid_fields:
            raise ValueError(f"unknown config field: {key}")
        values[key] = _coerce_value(raw_value, getattr(defaults, key))
    return FlatPPOConfig(**values)


def main(argv=None):
    args = parse_args(argv)
    config = build_config(args)
    result = train_flat_ppo(config)
    print(f"checkpoint: {result['checkpoint_path']}")
    print(f"metrics: {result['metrics_path']}")
    print(f"manifest: {result['manifest_path']}")
    if result["mean_episode_score"] is not None:
        print(f"mean_episode_score_last10: {result['mean_episode_score']:.2f}")


if __name__ == "__main__":
    main()
