"""CLI entrypoint for realtime PPO smoke training."""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.realtime_ppo import RealtimePPOConfig, train_realtime_ppo


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Train a realtime fixed-tick PPO smoke policy.")
    parser.add_argument("--config", default="train/config/realtime_smoke.yaml")
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


def build_config(args) -> RealtimePPOConfig:
    path = Path(args.config)
    with path.open("r", encoding="utf-8") as handle:
        values = yaml.safe_load(handle) or {}
    if not isinstance(values, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    defaults = RealtimePPOConfig()
    valid = {field.name for field in fields(RealtimePPOConfig)}
    for override in args.set:
        if "=" not in override:
            raise ValueError(f"override must be KEY=VALUE: {override}")
        key, raw = override.split("=", 1)
        if key not in valid:
            raise ValueError(f"unknown config field: {key}")
        values[key] = _coerce(raw, getattr(defaults, key))
    return RealtimePPOConfig(**values)


def main(argv=None):
    result = train_realtime_ppo(build_config(parse_args(argv)))
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
    if result["mean_deadline_misses"] is not None:
        print(f"mean_deadline_misses_last10: {result['mean_deadline_misses']:.2f}")
    if result["evaluation"].get("enabled"):
        print(f"eval_win_rate: {result['evaluation']['win_rate_policy_a']:.3f}")


if __name__ == "__main__":
    main()
