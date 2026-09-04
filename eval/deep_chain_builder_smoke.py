"""Reproducible PUYO-187 headless smoke for the deep-chain builder policy."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agents.deep_chain_builder import (
    DEEP_CHAIN_TARGET_CHAIN_CHOICES,
    DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
)
from agents.deep_chain_search_backend import LONG_HORIZON_BACKEND_CHOICES
from puyo_env.actions import action_to_placement, legal_action_mask
from puyo_env.obs import encode_observation
from selfplay.policies import make_policy
from src.core.constants import GRID_HEIGHT, GRID_WIDTH
from src.core.headless import HeadlessPuyoSimulator

SMOKE_SCHEMA_VERSION = "puyo.deep_chain_builder.smoke.v1"
DEFAULT_OUTPUT_PATH = Path("docs/benchmarks/puyo-187-deep-chain-builder-smoke.json")


def _stable_digest(value: Any, *, prefix: str) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(prefix.encode("utf-8") + b":" + payload).hexdigest()


def _board_snapshot(simulator: HeadlessPuyoSimulator) -> list[list[str]]:
    return [
        [simulator.game.field.grid[y][x].color.name for x in range(GRID_WIDTH)]
        for y in range(GRID_HEIGHT)
    ]


def _visible_pairs(simulator: HeadlessPuyoSimulator) -> list[list[str]]:
    game = simulator.game
    pairs = []
    if game.current_puyo_1 is not None and game.current_puyo_2 is not None:
        pairs.append([game.current_puyo_1.color.name, game.current_puyo_2.color.name])
    pairs.extend(
        [[pair[0].color.name, pair[1].color.name] for pair in game.next_puyo_queue]
    )
    return pairs[:3]


def _scenario_summary(
    values: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "root_action": int(value["root_action"]),
            "ranking_key": value.get("ranking_key", []),
            "score_breakdown": value.get("score_breakdown", {}),
            "fire_class": value.get("evidence", {}).get("fire_class"),
            "coverage": value.get("evidence", {}).get("coverage"),
        }
        for value in values
    ]


def run_headless_smoke(
    *,
    seed: int,
    turns: int,
    profile: str = "smoke",
    backend: str = "python",
    target_chain_count: int = DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
) -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=int(seed))
    policy = make_policy(
        "deep_chain_builder",
        seed=int(seed),
        deep_chain_profile=profile,
        deep_chain_backend=backend,
        deep_chain_target_chain=target_chain_count,
    )
    records = []
    for step_count in range(int(turns)):
        mask = legal_action_mask(simulator)
        if not any(mask):
            break
        observation = encode_observation(
            simulator,
            step_count=step_count,
            max_steps=turns,
        )
        info = {
            "action_mask": mask,
            "action_mask_source": "headless_simulator",
            "score": int(simulator.game.score),
            "step_count": int(step_count),
            "max_steps": int(turns),
            "last_chain_end_score": int(simulator.game.last_chain_end_score),
        }
        before_board = _board_snapshot(simulator)
        visible_pairs = _visible_pairs(simulator)
        action = int(policy.select_action(observation, info))
        diagnostics = policy.tactical_diagnostics
        plan = diagnostics.get("plan", {})
        steps = plan.get("steps", [])
        if not steps or int(steps[0]["action"]) != action:
            raise AssertionError("policy action and plan step 1 disagree")
        if not 0 <= action < len(mask) or not mask[action]:
            raise AssertionError("deep-chain smoke selected an illegal action")
        result = simulator.step(action_to_placement(action), capture_visuals=True)
        if not result.valid:
            raise AssertionError("deep-chain smoke action was rejected by headless")
        selection = diagnostics.get("selection_evidence", {})
        records.append(
            {
                "turn": int(step_count),
                "visible_pairs": visible_pairs,
                "board_before": before_board,
                "action": action,
                "legal_action_count": int(sum(mask)),
                "actual_result": {
                    "valid": bool(result.valid),
                    "chain_count": int(result.chain_count),
                    "score_delta": int(result.score_delta),
                    "game_over": bool(result.game_over),
                },
                "board_after": _board_snapshot(simulator),
                "plan_id": str(plan.get("plan_id", "")),
                "replan_reason": str(plan.get("replan_reason", "")),
                "plan": plan,
                "selection": {
                    "candidate_count": selection.get("candidate_count"),
                    "selection_reason": selection.get("selection_reason"),
                    "selected_root_action": selection.get("selected_root_action"),
                    "selected_ranking_key": selection.get("selected_ranking_key", []),
                    "selected_score_breakdown": selection.get(
                        "selected_score_breakdown", {}
                    ),
                    "scenario_aggregation": _scenario_summary(
                        selection.get("scenario_aggregation", [])
                    ),
                },
                "search": diagnostics.get("search", {}),
                "backend": diagnostics.get("backend", {}),
                "fallback": diagnostics.get("fallback", {}),
                "decision_trace": diagnostics.get("decision_trace", {}),
            }
        )
        if result.game_over:
            break

    deterministic_records = [
        {
            "turn": record["turn"],
            "action": record["action"],
            "plan_id": record["plan_id"],
            "plan_actions": [
                int(step["action"]) for step in record["plan"].get("steps", [])
            ],
            "state_fingerprints": [
                str(step.get("state_fingerprint", ""))
                for step in record["plan"].get("steps", [])
            ],
        }
        for record in records
    ]
    return {
        "seed": int(seed),
        "profile": profile,
        "backend": backend,
        "target_chain_count": int(target_chain_count),
        "requested_turns": int(turns),
        "completed_turns": len(records),
        "actions": [record["action"] for record in records],
        "plan_ids": [record["plan_id"] for record in records],
        "action_digest": _stable_digest(
            [record["action"] for record in records],
            prefix="deep-chain-smoke-actions",
        ),
        "plan_digest": _stable_digest(
            deterministic_records,
            prefix="deep-chain-smoke-plans",
        ),
        "final_board_digest": _stable_digest(
            _board_snapshot(simulator),
            prefix="deep-chain-smoke-final-board",
        ),
        "records": records,
    }


def build_smoke_artifact(
    *,
    seed: int = 187,
    turns: int = 3,
    repeats: int = 2,
    profile: str = "smoke",
    backend: str = "python",
    target_chain_count: int = DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
    ticket: str = "PUYO-187",
) -> dict[str, Any]:
    if min(int(turns), int(repeats)) <= 0:
        raise ValueError("smoke turns and repeats must be positive")
    runs = [
        run_headless_smoke(
            seed=seed,
            turns=turns,
            profile=profile,
            backend=backend,
            target_chain_count=target_chain_count,
        )
        for _ in range(repeats)
    ]
    action_digests = [run["action_digest"] for run in runs]
    plan_digests = [run["plan_digest"] for run in runs]
    board_digests = [run["final_board_digest"] for run in runs]
    records = [record for run in runs for record in run["records"]]
    checks = {
        "multiple_turns_completed": all(run["completed_turns"] >= 2 for run in runs),
        "policy_action_matches_plan_step_1": all(
            record["plan"]["steps"][0]["action"] == record["action"]
            for record in records
        ),
        "known_steps_present": any(
            step["known_tsumo"]
            for record in records
            for step in record["plan"]["steps"]
        ),
        "unknown_steps_present": any(
            not step["known_tsumo"]
            for record in records
            for step in record["plan"]["steps"]
        ),
        "scenario_ids_present": all(
            "scenario_id" in step
            for record in records
            for step in record["plan"]["steps"]
        ),
        "predicted_boards_present": all(
            bool(step["predicted_board"])
            for record in records
            for step in record["plan"]["steps"]
        ),
        "fallback_unused": all(
            not bool(record["fallback"].get("used")) for record in records
        ),
        "requested_backend_recorded": all(
            record.get("backend", {}).get("requested_backend") == backend
            for record in records
        ),
        "target_chain_recorded": all(
            record.get("plan", {}).get("objective", {}).get(
                "minimum_chain_count"
            )
            == target_chain_count
            and record.get("search", {}).get("target_chain_count")
            == target_chain_count
            for record in records
        ),
        "action_digest_match": len(set(action_digests)) == 1,
        "plan_digest_match": len(set(plan_digests)) == 1,
        "final_board_digest_match": len(set(board_digests)) == 1,
    }
    artifact = {
        "schema_version": SMOKE_SCHEMA_VERSION,
        "ticket": str(ticket),
        "environment": "safe_no_threat",
        "configuration": {
            "seed": int(seed),
            "turns": int(turns),
            "repeats": int(repeats),
            "profile": profile,
            "backend": backend,
            "target_chain_count": int(target_chain_count),
        },
        "checks": checks,
        "determinism": {
            "action_digests": action_digests,
            "plan_digests": plan_digests,
            "final_board_digests": board_digests,
        },
        "runs": runs,
    }
    artifact["artifact_digest"] = _stable_digest(
        {
            "configuration": artifact["configuration"],
            "checks": checks,
            "determinism": artifact["determinism"],
        },
        prefix="puyo-187-smoke-artifact",
    )
    return artifact


def verify_smoke_artifact(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SMOKE_SCHEMA_VERSION:
        raise AssertionError("unsupported deep-chain smoke schema")
    checks = payload.get("checks")
    if not isinstance(checks, Mapping) or not checks:
        raise AssertionError("deep-chain smoke checks are missing")
    failures = [str(name) for name, passed in checks.items() if not bool(passed)]
    if failures:
        raise AssertionError(f"deep-chain smoke checks failed: {failures}")
    expected_digest = _stable_digest(
        {
            "configuration": payload.get("configuration"),
            "checks": checks,
            "determinism": payload.get("determinism"),
        },
        prefix="puyo-187-smoke-artifact",
    )
    if payload.get("artifact_digest") != expected_digest:
        raise AssertionError("deep-chain smoke artifact digest mismatch")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=187)
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--profile", default="smoke")
    parser.add_argument(
        "--backend",
        choices=LONG_HORIZON_BACKEND_CHOICES,
        default="python",
    )
    parser.add_argument(
        "--target-chain",
        type=int,
        choices=DEEP_CHAIN_TARGET_CHAIN_CHOICES,
        default=DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
    )
    parser.add_argument("--ticket", default="PUYO-187")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify the existing output instead of regenerating it",
    )
    args = parser.parse_args(argv)
    if args.verify:
        artifact = json.loads(args.output.read_text(encoding="utf-8"))
    else:
        artifact = build_smoke_artifact(
            seed=args.seed,
            turns=args.turns,
            repeats=args.repeats,
            profile=args.profile,
            backend=args.backend,
            target_chain_count=args.target_chain,
            ticket=args.ticket,
        )
        _write_json(args.output, artifact)
    verify_smoke_artifact(artifact)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "artifact_digest": artifact["artifact_digest"],
                "checks": artifact["checks"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
