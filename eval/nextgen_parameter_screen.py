"""PUYO-280 development-only paired screen on tuning patterns.

The held-out formal cohort must never be used to choose these settings.
Results here are exploratory and cannot satisfy PUYO-266 by themselves.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from agents.chain_structure import load_chain_structure_config
from eval.nextgen_fire_goal_comparison import new_match
from eval.nextgen_single_quality_gate import PATTERN_IDS as TUNING_PATTERNS
from eval.nextgen_single_quality_runtime import make_policy, measure

PATTERNS = TUNING_PATTERNS
CANDIDATES = {
    "baseline": {},
    "score25": {"potential_chain_score": 0.25},
    "score25-safe": {"potential_chain_score": 0.25, "danger_ratio": -60000.0,
                     "premature_fire": -16000.0},
    "score50-safe": {"potential_chain_score": 0.5, "danger_ratio": -60000.0,
                     "premature_fire": -16000.0},
    "score100-safe": {"potential_chain_score": 1.0, "danger_ratio": -60000.0,
                      "premature_fire": -16000.0},
}


def policy_for(candidate, goal):
    score = 95000 if goal == "score95000" else None
    policy = make_policy("nextgen_tactic_manager", fire_goal_score=score,
                         evaluator_config=load_chain_structure_config())
    if candidate != "baseline":
        policy.evaluator_config = replace(
            policy.evaluator_config,
            weight_version="puyo-280-" + candidate,
            weights=replace(policy.evaluator_config.weights, **CANDIDATES[candidate]),
        )
    return policy


def run(candidate, goal, pattern, source_path):
    policy = policy_for(candidate, goal)
    config = {"environment_seed": 55, "provider": {"source_path": str(source_path)}}
    raw = measure(new_match(config, pattern), policy, "nextgen_tactic_manager",
                  placements=60, stop_first_chain=10)
    fires = [event for event in raw["events"]
             if event["type"] == "resolution_complete" and event["chain_count"] > 0]
    return {"candidate": candidate, "goal": goal, "pattern_id": pattern,
            "max_chain": raw["final"]["max_chain"],
            "max_score": max((event["score_delta"] for event in fires), default=0),
            "placements": len(raw["chains"]), "game_over": raw["game_over"],
            "termination": raw["termination"],
            "small_clears": sum(0 < count < 10 for count in raw["chains"]),
            "decision_seconds": sum(row["seconds"] for row in raw["rows"])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=CANDIDATES, required=True)
    parser.add_argument("--goal", choices=("chain10", "score95000"), required=True)
    parser.add_argument("--pattern", type=int, choices=PATTERNS, required=True)
    parser.add_argument("--tsumo-source", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.candidate, args.goal, args.pattern, args.tsumo_source)), flush=True)


if __name__ == "__main__":
    main()
