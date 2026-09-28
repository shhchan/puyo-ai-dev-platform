"""Compare receipt rendering reads on an identical recorded diagnostic batch."""

import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from puyo_env.realtime_ai import RealtimeControllerDiagnostics, RealtimeDecisionRecord
from src.ui.nextgen_display import live_nextgen_receipt_summary, nextgen_receipt_summary
from aggregate import stats


ROOT = Path(__file__).parent


def main():
    results = {}
    for name in ("one", "two", "human"):
        raw = gzip.decompress((ROOT / "raw" / f"baseline-{name}.json.gz").read_bytes())
        data = json.loads(raw)
        values = data["diagnostics"]["player_0"]
        fields = RealtimeControllerDiagnostics.__dataclass_fields__
        controller = RealtimeControllerDiagnostics(**{
            key: value for key, value in values.items() if key in fields and key != "last_decision"
        })
        controller.last_decision = RealtimeDecisionRecord(**values["last_decision"])
        policy = {"nextgen": controller.last_decision.nextgen_diagnostics}
        times = {"legacy": [], "current": []}
        for _ in range(100):
            start = time.perf_counter_ns()
            legacy = nextgen_receipt_summary(policy, controller.to_dict())
            times["legacy"].append((time.perf_counter_ns() - start) / 1e6)
            start = time.perf_counter_ns()
            current = live_nextgen_receipt_summary(policy, controller.last_decision)
            times["current"].append((time.perf_counter_ns() - start) / 1e6)
            assert legacy == current
        results[name] = {"raw_sha256": hashlib.sha256(raw).hexdigest(),
                         "summary_identical": True, "samples_ms": times,
                         "stats_ms": {key: stats(value) for key, value in times.items()}}
    (ROOT / "receipt-microbenchmark.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
