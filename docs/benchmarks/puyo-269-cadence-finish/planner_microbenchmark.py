"""Compare exact timed witnesses on fixed states independently of GUI progress."""

import json
from pathlib import Path
import pickle
from statistics import median
from time import perf_counter_ns

import hashlib
import subprocess
import sys
import types
from puyo_env.action_planner import _plans_for_actions
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.realtime import RealtimeHeadlessSimulator
from tests.test_timed_placement_planner import CASES, first_lock, simulator


BASELINE = "0a364aa2a6d8b5a5ca0313c5aa6ca3369a21f222"
def reference_planner():
    source = subprocess.check_output(["git", "show", f"{BASELINE}:puyo_env/action_planner.py"], text=True)
    module = types.ModuleType("puyo269_reference_planner")
    sys.modules[module.__name__] = module
    exec(compile(source, "puyo269_reference_planner", "exec"), vars(module))
    return module, hashlib.sha256(source.encode()).hexdigest()

def main():
    reference, source_hash = reference_planner()
    rows = []
    for name, source in [("empty", RealtimeHeadlessSimulator(seed=55))] + [
        (f"seed-{case['seed']}", simulator(case)) for case in CASES
    ]:
        before = pickle.dumps(source)
        expected = reference._plans_for_actions(source, PLACEMENT_ACTIONS)
        actual = _plans_for_actions(source, PLACEMENT_ACTIONS)
        # Reference is loaded under another module, so compare public values.
        for action in PLACEMENT_ACTIONS:
            old, new = expected.get(action), actual.get(action)
            assert (vars(old) if old else None) == (vars(new) if new else None)
            if new:
                assert first_lock(source.clone(), new.inputs) == (
                    action.axis_x, new.expected_axis_y, action.rotation.name
                )
        assert pickle.dumps(source) == before
        times = {"before": [], "after": []}
        for index in range(30):
            order = (("before", reference._plans_for_actions), ("after", _plans_for_actions))
            for label, function in order if index % 2 else reversed(order):
                started = perf_counter_ns()
                function(source, PLACEMENT_ACTIONS)
                times[label].append((perf_counter_ns() - started) / 1e6)
        rows.append({"case": name, "plans_equal": True, "source_unchanged": True,
                     "allowed_roots": sum(p is not None for p in actual.values()),
                     "median_ms": {key: median(v) for key, v in times.items()},
                     "raw_ms": times})
    result = {"baseline": BASELINE, "baseline_planner_sha256": source_hash, "cases": rows}
    Path(__file__).with_name("planner_microbenchmark.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps([{k: v for k, v in row.items() if k != "raw_ms"} for row in rows], indent=2))


if __name__ == "__main__":
    main()
