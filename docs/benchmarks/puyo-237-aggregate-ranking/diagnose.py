"""Capture full root evidence and real private-input counterfactuals.

Run after the 12-run measurements, from the evaluated checkout:
PYTHONPATH=. python <this-file> OUTPUT_DIRECTORY
Excluded from trajectory quality and performance estimates.
"""

import importlib
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

from agents.deep_chain_native import (
    NATIVE_MODULE_NAME,
    NativeDeepChainBackend,
    decode_request,
)
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation

fixture = json.loads(Path("tests/fixtures/aggregate_ranking_requests.json").read_text())
backend = NativeDeepChainBackend(importlib.import_module(NATIVE_MODULE_NAME))
results = []
for case in fixture["cases"]:
    request = decode_request(bytes.fromhex(case["request_hex"]))
    for target in (6, 8, 10, 12) if case["seed"] == 151 else (case["target"],):
        modes = []
        for mode in ("oracle-1", "scenario-6"):
            req = replace(
                request,
                search_config=replace(
                    request.search_config, minimum_chain_count=target
                ),
                execution_mode=mode,
            )
            native = backend.decide(req)
            python = materialize_native_long_horizon_result(native, req)
            modes.append(
                {
                    "mode": mode,
                    "native_ranking": list(native.ranked_root_actions),
                    "native_selected_action": native.selected_action,
                    "digest": native.deterministic_digest,
                    "counters": dict(native.counters),
                    "roots": [
                        {
                            "root_action": e.root_action,
                            "ranking_key": [
                                str(value)
                                if isinstance(value, float) and not math.isfinite(value)
                                else value
                                for value in e.ranking_key
                            ],
                            "candidate_value_hex": e.candidate_value.hex(),
                            "scenario_values": [v.to_dict() for v in e.scenario_values],
                        }
                        for e in python.ranked_roots
                    ],
                }
            )
        assert {k: v for k, v in modes[0].items() if k != "mode"} == {
            k: v for k, v in modes[1].items() if k != "mode"
        }
        results.append(
            {
                "seed": case["seed"],
                "target": target,
                "decision": case["decision"],
                "modes": modes,
            }
        )
future = []
for target in (6, 8, 10, 12):
    obs, info = baseline._initial_observation_and_info(151, max_steps=40)
    samples = []
    for marker in ("left", "right"):
        observation, details = dict(obs), dict(info)
        observation["private_future_queue"] = "private-" + marker
        details.update(
            simulator="private-simulator-" + marker, future_queue="private-" + marker
        )
        policy = baseline._policy_factory(151, "reference", "native", target)
        action = int(policy.select_action(observation, details))
        d = policy.tactical_diagnostics
        samples.append(
            {
                "action": action,
                "plan": baseline._plan_summary(d["plan"]),
                "search_digest": d["search"]["deterministic_digest"],
                "scenario_accounting": baseline._scenario_accounting(d),
                "fallback": d["fallback"],
            }
        )
    assert samples[0] == samples[1]
    future.append(
        {
            "seed": 151,
            "target": target,
            "private_counterfactual_matches": True,
            "samples": samples,
        }
    )
payload = {
    "ticket": "PUYO-237",
    "kind": "same_request_diagnostics_excluded_from_trajectory_quality_and_performance",
    "evaluated_commit": baseline.git_commit(baseline.REPO_ROOT),
    "fixture_source_commit": fixture["source_commit"],
    "cases": results,
    "future_isolation": future,
}
output = Path(sys.argv[1]) / "diagnostics.json.gz"
if output.exists():
    raise ValueError("refusing to overwrite existing diagnostic evidence")
ablation.write_run(output, payload)
print("all root evidence and private counterfactual match: 6 cases, 4 targets")
