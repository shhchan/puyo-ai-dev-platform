"""Recompute desktop GUI evidence from compressed per-frame/event traces."""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("previous_aggregate", ROOT.parent / "puyo-269-followup/aggregate.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


def stats(values):
    values = sorted(values)
    def percentile(q):
        if not values:
            return None
        index = (len(values) - 1) * q
        lo = int(index)
        return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)
    return {"n": len(values), "p50": percentile(.5), "p95": percentile(.95),
            "p99": percentile(.99), "max": max(values) if values else None}


def aggregate():
    summary = {}
    for path in sorted((ROOT / "raw").glob("*.json.gz")):
        raw = gzip.decompress(path.read_bytes())
        data = json.loads(raw)
        name = path.name.removesuffix(".json.gz")
        for key, values in data["raw_samples"].items():
            assert stats(values) == data["samples"][key], (name, key)
        row = previous.summarize(name, data, raw)
        row["legacy_render"] = data.get("legacy_render", False)
        row["ui_decode"] = data.get("ui_decode", True)
        row["minimal"] = data["minimal"]
        row["functions_ms"] = {key: stats([v["ms"] for v in values])
                               for key, values in data["functions_raw"].items()}
        row["source_fingerprint"] = hashlib.sha256(
            json.dumps(data["source_files_sha256"], sort_keys=True).encode()
        ).hexdigest()
        row["cache_samples"] = data["cache_samples"]
        accepts = data["functions_raw"].get("scheduler_accept", [])
        first_accept = min((v["frame"] for v in accepts), default=None)
        row["first_accept_frame"] = first_accept
        row["cold_warm_frame_ms"] = {
            label: stats([value for frame, value in enumerate(data["raw_samples"]["frame_interval_ms"])
                          if (first_accept is None or frame < first_accept) == cold])
            for label, cold in (("before_first_accept", True), ("after_first_accept", False))
        }
        row["gate"] = {
            key: data["samples"][key]["p95"] <= 25 and data["samples"][key]["p99"] <= 50
            for key in ("frame_interval_ms", "input_schedule_ms")
        }
        row["gc_by_generation"] = {
            str(generation): stats([v["ms"] for v in data["functions_raw"].get("gc_collect", [])
                                   if v["generation"] == generation])
            for generation in range(3)
        }
        row["over_50ms_frames"] = [
            {"frame": frame, "frame_ms": value,
             "update_ms": data["raw_samples"]["update_ms"][frame],
             "render_ms": data["raw_samples"]["render_ms"][frame],
             "catchup": data["raw_samples"]["tick_catchup"][frame],
             "spans": {key: round(sum(v["ms"] for v in values if v["frame"] == frame), 3)
                       for key, values in data["functions_raw"].items()
                       if key in ("scheduler_accept", "scheduler_finish", "authoritative_mask",
                                  "render_receipt_summary", "ipc_deserialize", "gc_collect")}}
            for frame, value in enumerate(data["raw_samples"]["frame_interval_ms"]) if value > 50
        ]
        summary[name] = row
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Validated and summarized {len(summary)} traces")


if __name__ == "__main__":
    aggregate()
