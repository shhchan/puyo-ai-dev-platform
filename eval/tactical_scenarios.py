"""Evaluate manager routing on the deterministic PUYO-45 scenario suite."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from agents.tactical_scenarios import apply_tactical_scenario, default_tactical_scenarios
from puyo_env.versus_env import VersusPuyoEnv
from selfplay.policies import make_policy


def evaluate_scenarios(policy) -> list[dict]:
    rows = []
    for scenario in default_tactical_scenarios():
        env = VersusPuyoEnv(seed=scenario.seed, max_steps=2)
        env.reset(seed=scenario.seed)
        observations, info = apply_tactical_scenario(env, scenario)
        policy.select_action(observations["player_0"], info)
        selected = getattr(policy, "current_profile_name", None) or ""
        diagnostics = getattr(policy, "tactical_diagnostics", {}) or {}
        rows.append(
            {
                "scenario": scenario.name,
                "category": scenario.category,
                "seed": scenario.seed,
                "expected_strategy": scenario.expected_strategy,
                "selected_strategy": selected,
                "correct": int(selected == scenario.expected_strategy),
                "incoming_attack": diagnostics.get("incoming_attack", 0),
                "target_attack": diagnostics.get("target_attack", 0),
                "deadline": diagnostics.get("deadline", 0),
                "reason": diagnostics.get("reason", ""),
            }
        )
        env.close()
    return rows


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate tactical manager routing.")
    parser.add_argument("--policy", choices=("manager", "manager_rule"), default="manager_rule")
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--csv", default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    policy = make_policy(
        args.policy,
        checkpoint_path=args.checkpoint,
        device=args.device,
        deterministic=True,
    )
    rows = evaluate_scenarios(policy)
    if args.csv:
        target = Path(args.csv)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    accuracy = sum(row["correct"] for row in rows) / max(1, len(rows))
    print(f"scenarios: {len(rows)}")
    print(f"accuracy: {accuracy:.3f}")
    for row in rows:
        print(
            f"{row['category']}: expected={row['expected_strategy']} "
            f"selected={row['selected_strategy']} correct={row['correct']}"
        )


if __name__ == "__main__":
    main()
