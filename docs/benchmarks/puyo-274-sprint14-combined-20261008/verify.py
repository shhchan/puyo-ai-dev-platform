"""Recompute the combined GUI gate and trace integrity from saved raw runs."""

import gzip
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
REPORT = json.loads((ROOT / "summary.json").read_text())


def percentile(values, quantile):
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (
        position - lower
    )


def verify():
    assert REPORT["schema"] == "puyo.sprint14.combined_gui_qa.v1"
    assert len(REPORT["rows"]) == 8
    sources = []
    for row in REPORT["rows"]:
        path = ROOT / "raw" / f"{row['condition']}.json.gz"
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["raw_sha256"]
        data = json.loads(gzip.decompress(raw))
        sources.append(tuple(json.dumps(data[key], sort_keys=True) for key in (
            "source_sha", "source_diff_sha256", "source_files_sha256", "native", "host",
            "resolution", "display",
        )))
        assert data["source_sha"] == REPORT["source_head"]
        assert data["source_diff_sha256"] == REPORT["source_diff_sha256"]
        assert data["native"]["sha256"] == REPORT["native_sha256"]
        assert data["frames"] == row["frames"]
        for source, prefix in (("frame_interval_ms", "frame"), ("input_schedule_ms", "input")):
            values = data["raw_samples"][source]
            for quantile, suffix in ((0.95, "p95_ms"), (0.99, "p99_ms")):
                measured = percentile(values, quantile)
                assert abs(measured - row[f"{prefix}_{suffix}"]) < 1e-8
                assert abs(measured - data["samples"][source][suffix[:-3]]) < 1e-8
        assert row["gate_pass"] == all(row[name] <= REPORT["gate"][f"{name}_max"] for name in (
            "frame_p95_ms", "frame_p99_ms", "input_p95_ms", "input_p99_ms",
        ))
        receipts = data["lock_receipts"]
        assert row["lock_receipts"] == len(receipts)
        assert row["lock_mismatches"] == sum(item["matches"] is False for item in receipts)
        assert row["scheduler_errors"] == sum(len(items) for items in data["scheduler_errors"].values())
        assert row["timeouts"] == sum(item["timeouts"] for item in data["diagnostics"].values())
        assert row["fallback_actions"] == sum(
            item["fallback_actions"] for item in data["diagnostics"].values()
        )
        assert row["worker_cleanup"] == all(data["worker_cleanup"].values())
    assert len(set(sources)) == 1
    assert REPORT["all_gate_pass"] == all(row["gate_pass"] for row in REPORT["rows"])
    assert REPORT["all_lock_and_worker_checks_pass"] == all(
        row["lock_mismatches"] == row["scheduler_errors"] == row["timeouts"]
        == row["fallback_actions"] == 0 and row["worker_cleanup"] for row in REPORT["rows"]
    )
    assert REPORT["all_gate_pass"] and REPORT["all_lock_and_worker_checks_pass"]
    print("8 combined GUI runs and all gates verified")


if __name__ == "__main__":
    verify()
