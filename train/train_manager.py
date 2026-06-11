"""CLI entrypoint for strategy-manager PPO training."""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.manager_ppo import ManagerPPOConfig, train_manager_ppo


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Train the strategy-profile PPO manager.")
    parser.add_argument("--config", default="train/config/manager.yaml")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    return parser.parse_args(argv)


def _coerce(raw: str, current):
    if isinstance(current, bool):
        return raw.lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def build_config(args) -> ManagerPPOConfig:
    path = Path(args.config)
    with path.open("r", encoding="utf-8") as handle:
        values = yaml.safe_load(handle) or {}
    defaults = ManagerPPOConfig()
    valid = {field.name for field in fields(ManagerPPOConfig)}
    for override in args.set:
        if "=" not in override:
            raise ValueError(f"override must be KEY=VALUE: {override}")
        key, raw = override.split("=", 1)
        if key not in valid:
            raise ValueError(f"unknown config field: {key}")
        values[key] = _coerce(raw, getattr(defaults, key))
    return ManagerPPOConfig(**values)


def main(argv=None):
    result = train_manager_ppo(build_config(parse_args(argv)))
    print(f"run_id: {result['run_id']}")
    print(f"run_dir: {result['run_dir']}")
    print(f"checkpoint: {result['checkpoint_path']}")
    print(f"metrics: {result['metrics_path']}")
    print(f"summary: {result['summary_path']}")


if __name__ == "__main__":
    main()
