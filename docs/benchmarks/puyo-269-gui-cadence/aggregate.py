"""Verify the archived PUYO-269 profiler outputs and their compact summary.

This checks the profiler's recorded percentile values; the profiler did not
retain individual frame samples, so percentiles cannot be recomputed here.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CPU_TICKS_PER_SECOND = 100  # getconf CLK_TCK on the measurement host
COUNTERS = (
    "decision_requests", "decisions_activated", "stale_decisions", "timeouts",
    "deadline_misses", "fallback_actions", "placements_completed",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    index = int(position)
    return values[index] + (values[min(index + 1, len(values) - 1)] - values[index]) * (position - index)


def _stats(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": max(values) if values else None,
    }


def _processes(samples: list[dict]) -> list[dict]:
    by_pid: dict[int, list[dict]] = {}
    for sample in samples:
        for process in sample["processes"]:
            by_pid.setdefault(process["pid"], []).append(process)
    return [
        {
            "role": "gui" if index == 0 else f"worker_{index}",
            "pid": pid,
            "max_rss_mib": max(row["rss_kb"] for row in rows) / 1024,
            "max_threads": max(row["threads"] for row in rows),
            "sampled_cpu_seconds": (
                rows[-1]["cpu_ticks"] - rows[0]["cpu_ticks"]
            ) / CPU_TICKS_PER_SECOND,
            "sample_count": len(rows),
        }
        for index, (pid, rows) in enumerate(by_pid.items())
    ]


def _read_sources(manifest: dict) -> dict[str, dict]:
    sources = {}
    for label, metadata in manifest["source_files"].items():
        data = (ROOT / metadata["path"]).read_bytes()
        expected = metadata.get("compressed_sha256", metadata.get("sha256"))
        if _sha256(data) != expected or len(data) != metadata.get("compressed_bytes", metadata.get("bytes")):
            raise ValueError(f"archive checksum/size mismatch: {label}")
        if label.endswith(".json") or label.endswith(".txt"):
            continue
        original = gzip.decompress(data)
        if _sha256(original) != metadata["uncompressed_sha256"] or len(original) != metadata["uncompressed_bytes"]:
            raise ValueError(f"original checksum/size mismatch: {label}")
        sources[label] = json.loads(original)
    return sources


def _summary(sources: dict[str, dict], conditions: dict) -> dict:
    gates = conditions["gates_predeclared"]
    runs = {}
    for label, source in sources.items():
        samples = source["samples"]
        runs[label] = {
            "settings": source["settings"],
            "frames": source["frames"],
            "elapsed_seconds": source["elapsed_seconds"],
            "ticks": source["ticks"],
            "samples": samples,
            "tick_duration_ms": source["tick_duration_ms"],
            "tick_catchup": source["tick_catchup"],
            "functions_ms": source["functions_ms"],
            "functions_total_ms": source["functions_total_ms"],
            "decision_counters": {
                agent: {counter: diagnostics.get(counter, 0) for counter in COUNTERS}
                for agent, diagnostics in source["diagnostics"].items()
            },
            "processes": _processes(source["process_samples"]),
            "cadence_gate": {
                f"{metric}.{quantile}": samples[metric][quantile] <= threshold
                for metric, quantile, threshold in (
                    ("frame_interval_ms", "p95", gates["frame_interval_p95_ms"]),
                    ("frame_interval_ms", "p99", gates["frame_interval_p99_ms"]),
                    ("input_age_ms", "p95", gates["input_age_p95_ms"]),
                    ("input_age_ms", "p99", gates["input_age_p99_ms"]),
                )
            },
        }
        if source.get("cache_samples"):
            runs[label]["cache"] = {
                key: _stats([row["worker_seconds"] for row in source["cache_samples"] if row["hit"] is hit])
                for key, hit in (("hit_worker_seconds", True), ("miss_worker_seconds", False))
            }
    return {
        "schema_version": "puyo.gui_cadence_summary.v1",
        "note": "Percentiles and stage totals are the recorded profiler aggregates, not recomputed from per-frame observations.",
        "cpu_ticks_per_second": CPU_TICKS_PER_SECOND,
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Regenerate summary.json from the archived source outputs")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "manifest.json").read_text())
    conditions = json.loads((ROOT / "conditions.json").read_text())
    sources = _read_sources(manifest)
    result = _summary(sources, conditions)
    summary_path = ROOT / "summary.json"
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        summary_path.write_text(rendered)
    elif summary_path.read_text() != rendered:
        raise ValueError("summary.json differs from the archived profiler outputs")
    print(f"Verified {len(sources)} archived profiler outputs and summary.json")


if __name__ == "__main__":
    main()
