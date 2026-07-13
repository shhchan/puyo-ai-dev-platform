"""Collect deterministic v1.7.0 teacher replays for PUYO-128 retraining rounds."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any, Mapping, Sequence

from eval.realtime_arena import replay_realtime_match, run_realtime_match, write_realtime_replay
from selfplay.policies import make_policy
from train.artifacts import describe_artifact, utc_timestamp


TEACHER_POLICY = "v1_7_analyzer_manager"
OPPONENTS = ("manager_rule", "beam", "v1_7_analyzer_manager")
COLLECTION_SCHEMA_VERSION = "puyo.v1_7_teacher_replay_collection.v1"


class _RealtimeTimingGuard:
    """Use a legal fallback while the opponent has no transient active pair."""

    def __init__(self, policy):
        self.policy = policy

    def select_action(self, observation, info):
        try:
            return self.policy.select_action(observation, info)
        except ValueError as exc:
            if "active current pair" not in str(exc):
                raise
            mask = info.get("action_mask", ())
            return next((index for index, legal in enumerate(mask) if bool(legal)), 0)

    def reset(self):
        reset = getattr(self.policy, "reset", None)
        if callable(reset):
            reset()

    def __getattr__(self, name):
        return getattr(self.policy, name)


def _policy(policy_type: str, *, seed: int):
    return _RealtimeTimingGuard(
        make_policy(
            policy_type,
            seed=seed,
            device="cpu",
            deterministic=True,
            beam_depth=10,
            beam_width=48,
            beam_scenarios=1,
            beam_minimum_chain=6,
        )
    )


def compact_teacher_replay(
    replay: Mapping[str, Any],
    *,
    teacher_agent: str,
) -> dict[str, Any]:
    """Keep every deterministic input/hash but only changed teacher diagnostics."""

    ticks = []
    last_plan_id: str | None = None
    for source in replay.get("ticks", ()):
        tick = {
            key: source[key]
            for key in (
                "tick",
                "inputs",
                "all_clear_diagnostics",
                "attack_diagnostics",
                "snapshot_hash",
            )
            if key in source
        }
        diagnostics = source.get("policy_diagnostics", {}).get(teacher_agent, {})
        plan_id = str(diagnostics.get("plan_id", "")) if isinstance(diagnostics, Mapping) else ""
        if plan_id and plan_id != last_plan_id:
            tick["policy_diagnostics"] = {teacher_agent: diagnostics}
            last_plan_id = plan_id
        else:
            tick["policy_diagnostics"] = {}
        ticks.append(tick)
    return {
        key: value
        for key, value in {
            "format": replay.get("format"),
            "seed": replay.get("seed"),
            "max_ticks": replay.get("max_ticks"),
            "initial_all_clear_diagnostics": replay.get("initial_all_clear_diagnostics"),
            "ticks": ticks,
            "expected_final_hash": replay.get("expected_final_hash"),
            "teacher_agent": teacher_agent,
        }.items()
        if value is not None
    }


def _collect_one(job: tuple[str, int, str, str, int]) -> dict[str, Any]:
    output_path, environment_seed, opponent, teacher_agent, max_ticks = job
    opponent_agent = "player_1" if teacher_agent == "player_0" else "player_0"
    policies = {
        teacher_agent: _policy(TEACHER_POLICY, seed=environment_seed + 20_000),
        opponent_agent: _policy(opponent, seed=environment_seed + 30_000),
    }
    match = run_realtime_match(
        policies["player_0"],
        policies["player_1"],
        seed=environment_seed,
        max_ticks=max_ticks,
        record_replay=True,
    )
    replay = compact_teacher_replay(match.replay or {}, teacher_agent=teacher_agent)
    final_hash = replay_realtime_match(replay)
    path = Path(output_path)
    write_realtime_replay(path, replay)
    return {
        "seed": environment_seed,
        "opponent": opponent,
        "teacher_agent": teacher_agent,
        "winner": match.winner,
        "ticks": match.ticks,
        "teacher_decisions": (
            match.decisions_player_0 if teacher_agent == "player_0" else match.decisions_player_1
        ),
        "final_hash": final_hash,
        "path": str(path),
    }


def collect_replays(
    *,
    output_dir: str | Path,
    seed: int,
    seeds: int,
    max_ticks: int,
    workers: int,
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    jobs = []
    for offset in range(seeds):
        environment_seed = seed + offset
        for opponent in OPPONENTS:
            for teacher_agent in ("player_0", "player_1"):
                replay_path = (
                    root
                    / opponent
                    / f"seed-{environment_seed}"
                    / f"teacher-{teacher_agent}"
                    / "replay.json"
                )
                jobs.append(
                    (str(replay_path), environment_seed, opponent, teacher_agent, max_ticks)
                )
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=workers,
        mp_context=mp.get_context("spawn"),
    ) as executor:
        records = []
        for index, record in enumerate(executor.map(_collect_one, jobs), start=1):
            records.append(record)
            print(
                f"[{index}/{len(jobs)}] {record['opponent']} seed={record['seed']} "
                f"teacher={record['teacher_agent']} decisions={record['teacher_decisions']}",
                flush=True,
            )
    replay_paths = [Path(record["path"]) for record in records]
    manifest = {
        "schema_version": COLLECTION_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "config": {
            "seed": seed,
            "seeds": seeds,
            "max_ticks": max_ticks,
            "workers": workers,
            "teacher_policy": TEACHER_POLICY,
            "opponents": list(OPPONENTS),
            "paired_sides": True,
        },
        "records": records,
        "artifacts": [
            describe_artifact(path, run_dir=root, role="teacher_replay")
            for path in replay_paths
        ],
    }
    (root / "collection_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect current-schema v1.7.0 teacher replays.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--max-ticks", type=int, default=600)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if min(args.seeds, args.max_ticks, args.workers) <= 0:
        parser.error("seeds, max-ticks, and workers must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    collect_replays(
        output_dir=args.output_dir,
        seed=args.seed,
        seeds=args.seeds,
        max_ticks=args.max_ticks,
        workers=args.workers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
