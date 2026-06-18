"""CLI entrypoint for Phase 2 versus PPO training."""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.versus_ppo import VersusPPOConfig, train_versus_ppo


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
    parser = argparse.ArgumentParser(description="Train a flat PPO Puyo agent in versus self-play.")
    parser.add_argument("--config", default="train/config/versus.yaml", help="YAML config path.")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override a config value. Example: --set total_timesteps=20000",
    )
    return parser.parse_args(argv)


def build_config(args) -> VersusPPOConfig:
    values = _load_config(Path(args.config))
    defaults = VersusPPOConfig()
    valid_fields = {field.name for field in fields(VersusPPOConfig)}
    for override in args.set:
        if "=" not in override:
            raise ValueError(f"override must be KEY=VALUE: {override}")
        key, raw_value = override.split("=", 1)
        if key not in valid_fields:
            raise ValueError(f"unknown config field: {key}")
        values[key] = _coerce_value(raw_value, getattr(defaults, key))
    return VersusPPOConfig(**values)


def main(argv=None):
    args = parse_args(argv)
    config = build_config(args)
    result = train_versus_ppo(config)
    print(f"run_id: {result['run_id']}")
    print(f"run_dir: {result['run_dir']}")
    print(f"checkpoint: {result['checkpoint_path']}")
    if result["best_checkpoint_path"] is not None:
        print(f"best_checkpoint: {result['best_checkpoint_path']}")
    print(f"metrics: {result['metrics_path']}")
    print(f"config_dump: {result['config_path']}")
    print(f"summary: {result['summary_path']}")
    print(f"manifest: {result['manifest_path']}")
    if result["mean_episode_score"] is not None:
        print(f"mean_episode_score_last10: {result['mean_episode_score']:.2f}")
    if result["mean_win_rate"] is not None:
        print(f"mean_win_rate_last10: {result['mean_win_rate']:.3f}")
    if result["mean_max_chain"] is not None:
        print(f"mean_max_chain_last10: {result['mean_max_chain']:.2f}")


if __name__ == "__main__":
    main()
