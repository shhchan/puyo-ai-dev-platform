"""Generate PUYO-45 tactical teacher examples."""

from __future__ import annotations

import argparse

from agents.tactical_scenarios import generate_teacher_examples, write_teacher_dataset
from agents.strategy_workers import default_worker_profiles, smoke_worker_profiles


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Generate counterfactual tactical teacher data.")
    parser.add_argument("--output", default="runs/manager_teacher/tactical_teacher.json")
    parser.add_argument("--smoke-profiles", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    profiles = smoke_worker_profiles() if args.smoke_profiles else default_worker_profiles()
    examples = generate_teacher_examples(profiles=profiles)
    write_teacher_dataset(args.output, examples)
    print(f"examples: {len(examples)}")
    print(f"output: {args.output}")


if __name__ == "__main__":
    main()
