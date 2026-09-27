"""Recompute the PUYO-269 GUI follow-up summary from saved raw traces."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parent
PERCENTILES = ("p50", "p95", "p99", "max")


def summarize(name: str, data: dict, raw: bytes) -> dict:
    samples = data["samples"]
    human_ticks = data["human_ticks"]
    processed = [row["id"] for row in data["input_events"]]
    posted = [row["id"] for row in data["posted_events"]]
    processes: dict[int, list[dict]] = {}
    for sample in data["process_samples"]:
        for process in sample["processes"]:
            processes.setdefault(process["pid"], []).append(process)
    cpu = [
        {
            "pid": pid,
            "sampled_cpu_cores": round(
                (rows[-1]["cpu_ticks"] - rows[0]["cpu_ticks"])
                / data["clock_ticks"] / data["elapsed_seconds"], 3
            ),
            "max_rss_mib": round(max(row["rss_kb"] for row in rows) / 1024, 1),
            "max_threads": max(row["threads"] for row in rows),
        }
        for pid, rows in processes.items()
    ]
    human = None
    if human_ticks:
        holds = {"LEFT", "RIGHT", "DOWN"}
        human = {
            "held_mismatch_ticks": sum(
                set(row["held"]) & holds != set(row["ui_held"]) & holds
                for row in human_ticks
            ),
            "excess_horizontal_fired": {
                action: sum(
                    action in row["fired"]
                    and action not in row["ui_held"]
                    and action not in row["press"]
                    for row in human_ticks
                )
                for action in ("LEFT", "RIGHT")
            },
            "rotation_emitted_fired": {
                action: [
                    sum(action in row["press"] for row in human_ticks),
                    sum(action in row["fired"] for row in human_ticks),
                ]
                for action in ("ROTATE_LEFT", "ROTATE_RIGHT")
            },
        }
    return {
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "source_sha": data["source_sha"],
        "source_diff_sha256": data["source_diff_sha256"],
        "reference": data["puyo_269_reference"],
        "settings": data["settings"],
        "host": data["host"],
        "native_sha256": data["native"]["sha256"],
        "frames": data["frames"],
        "elapsed_seconds": data["elapsed_seconds"],
        "active_frames": sum(data["active_frames"]),
        "timing_ms": {key: {p: samples[key][p] for p in PERCENTILES}
                      for key in ("frame_interval_ms", "input_schedule_ms",
                                  "input_queue_ms", "input_to_state_ms",
                                  "input_to_render_ms", "event_ms", "update_ms",
                                  "render_ms", "tick_ms", "tick_catchup")},
        "scheduler_ms": {
            key: data["functions_ms"].get(key)
            for key in ("scheduler_accept", "scheduler_finish", "simulation",
                        "authoritative_mask", "_complete_decision", "_activate_nextgen")
        },
        "diagnostics": {
            agent: {
                key: values.get(key)
                for key in ("decisions_started", "decisions_activated", "timeouts",
                            "stale_decisions", "deadline_misses", "fallback_actions",
                            "masked_actions", "unreachable_plans")
            }
            for agent, values in data["diagnostics"].items()
        },
        "scheduler_errors": data["scheduler_errors"],
        "worker_cleanup": data["worker_cleanup"],
        "processes": cpu,
        "input_events": {
            "posted": len(posted), "processed": len(processed),
            "duplicate_processed_ids": len(processed) - len(set(processed)),
            "unprocessed_ids": sorted(set(posted) - set(processed)),
        },
        "human": human,
        "locks": {
            "total": len(data.get("lock_receipts", [])),
            "compared": sum(row["matches"] is not None for row in data.get("lock_receipts", [])),
            "mismatch": sum(row["matches"] is False for row in data.get("lock_receipts", [])),
            "by_agent": {
                agent: {
                    "total": sum(row["agent"] == agent for row in data.get("lock_receipts", [])),
                    "compared": sum(row["agent"] == agent and row["matches"] is not None
                                    for row in data.get("lock_receipts", [])),
                    "mismatch": sum(row["agent"] == agent and row["matches"] is False
                                    for row in data.get("lock_receipts", [])),
                }
                for agent in sorted({row["agent"] for row in data.get("lock_receipts", [])})
            },
        },
    }


def aggregate(directory: Path) -> None:
    result = {}
    for path in sorted((directory / "raw").glob("*.json.gz")):
        raw = gzip.decompress(path.read_bytes())
        name = path.name.removesuffix(".json.gz")
        result[name] = summarize(name, json.loads(raw), raw)
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Recomputed {len(result)} raw traces in {directory}")


def main() -> None:
    aggregate(ROOT)
    aggregate(ROOT / "integrated")


if __name__ == "__main__":
    main()
