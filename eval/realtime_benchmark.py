"""Local realtime-core benchmark runner.

This script is intentionally outside the unittest suite. It records coarse
throughput and planner latency baselines for development comparisons.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from puyo_env.action_planner import plan_placement_action
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.headless import HeadlessPuyoSimulator
from src.core.realtime import RealtimeHeadlessSimulator


def run_benchmark(*, ticks: int, planner_repeats: int) -> dict[str, object]:
    sim = RealtimeHeadlessSimulator(seed=123)
    start = time.perf_counter()
    sim.advance_ticks(ticks)
    elapsed = time.perf_counter() - start
    tick_throughput = ticks / elapsed if elapsed > 0 else float("inf")

    planner_latencies = []
    base = HeadlessPuyoSimulator(seed=123)
    for _ in range(planner_repeats):
        for action in PLACEMENT_ACTIONS:
            plan_start = time.perf_counter()
            plan_placement_action(base, action)
            planner_latencies.append(time.perf_counter() - plan_start)

    return {
        "ticks": ticks,
        "tick_throughput_per_second": tick_throughput,
        "planner_samples": len(planner_latencies),
        "planner_latency_ms_mean": statistics.mean(planner_latencies) * 1000.0,
        "planner_latency_ms_p95": _percentile(planner_latencies, 0.95) * 1000.0,
        "final_hash": sim.state_hash(),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=20_000)
    parser.add_argument("--planner-repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = run_benchmark(ticks=args.ticks, planner_repeats=args.planner_repeats)
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
