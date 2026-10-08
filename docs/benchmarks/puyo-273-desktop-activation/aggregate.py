"""Validate raw percentiles and retain every desktop activation A/B run."""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("desktop_aggregate", ROOT.parent / "puyo-269-desktop/aggregate.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


def main():
    summary = {}
    for path in sorted((ROOT / "raw").glob("*.json.gz")):
        raw = gzip.decompress(path.read_bytes())
        data = json.loads(raw)
        name = path.name.removesuffix(".json.gz")
        for key, values in data["raw_samples"].items():
            assert previous.stats(values) == data["samples"][key], (name, key)
        row = previous.previous.summarize(name, data, raw)
        row["activation_reference"] = data["activation_reference"]
        row["minimal"] = data["minimal"]
        row["cache_samples"] = data["cache_samples"]
        row["functions_ms"] = data["functions_ms"]
        row["source_files_sha256"] = data["source_files_sha256"]
        row["gate"] = {
            key: data["samples"][key]["p95"] <= 25 and data["samples"][key]["p99"] <= 50
            for key in ("frame_interval_ms", "input_schedule_ms")
        }
        row["over_50ms_frames"] = [
            {"frame": i, "frame_ms": value,
             "update_ms": data["raw_samples"]["update_ms"][i],
             "render_ms": data["raw_samples"]["render_ms"][i],
             "catchup": data["raw_samples"]["tick_catchup"][i],
             "spans": {key: round(sum(v["ms"] for v in values if v["frame"] == i), 3)
                       for key, values in data["functions_raw"].items()
                       if any(v["frame"] == i for v in values)}}
            for i, value in enumerate(data["raw_samples"]["frame_interval_ms"]) if value > 50
        ]
        row["input_tails"] = []
        for event in data["input_events"]:
            start, end = event["expected_ns"], event["handled_ns"]
            if (end - start) / 1e6 <= 50:
                continue
            overlaps = []
            for key in ("authoritative_mask", "authoritative_plan_proof", "scheduler_accept",
                        "scheduler_finish", "_activate_nextgen", "reader_decode", "gc_collect"):
                for span in data["functions_raw"].get(key, []):
                    first = span["started_ns"]
                    last = first + span["ms"] * 1e6
                    if last > start and first < end:
                        overlaps.append({"function": key, "thread": span["thread"],
                                         "frame": span["frame"], "ms": span["ms"],
                                         "relative_start_ms": (first - start) / 1e6,
                                         "overlap_ms": (min(last, end) - max(first, start)) / 1e6})
            row["input_tails"].append({"id": event["id"], "frame": event["frame"],
                                       "schedule_ms": (end - start) / 1e6,
                                       "post_delay_ms": (event["posted_ns"] - start) / 1e6,
                                       "queue_ms": (end - event["posted_ns"]) / 1e6,
                                       "overlapping_wall_spans_not_additive": overlaps})
        summary[name] = row
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({name: {"gate": row["gate"], "timing": {
        key: [round(row["timing_ms"][key][p], 2) for p in ("p95", "p99")]
        for key in ("frame_interval_ms", "input_schedule_ms", "input_to_render_ms")
    }} for name, row in summary.items()}, indent=2))
    paths = sorted(p for p in ROOT.rglob("*") if p.is_file() and p.name != "SHA256SUMS"
                   and "__pycache__" not in p.parts)
    (ROOT / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(ROOT)}\n" for path in paths
    ))


if __name__ == "__main__":
    main()
