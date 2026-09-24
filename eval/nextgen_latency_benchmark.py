"""Fixed public-input latency comparison; no replay or quality promotion.

Run each backend in a fresh process on the same host, e.g.:
python -m eval.nextgen_latency_benchmark --backend python --output /tmp/python.json
python -m eval.nextgen_latency_benchmark --backend native --output /tmp/native.json
"""
from __future__ import annotations

import argparse
import copy
import json
import pickle
import platform
import resource
import sys
import time
from dataclasses import asdict
from pathlib import Path

from agents import nextgen_contracts as c
from agents.deep_chain_search_backend import NativeLongHorizonSearchBackend, PythonLongHorizonSearchBackend
from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy
from agents.long_horizon_search import LongHorizonSearchConfig
from eval.nextgen_realtime_diagnostic import source_identity
from puyo_env.realtime_ai import PolicyProcessExecutor, RealtimeDecisionConfig, RealtimePolicyController, _policy_diagnostics_snapshot
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.puyo import Puyo
from src.ui.launcher_settings import resolve_nextgen_catalog

# Bottom-up wire colors, fixed before either backend is run. Both public hidden
# rows remain unknown, and all request envelopes come from the real scheduler.
CASES = (
    ("empty", 55, ()),
    ("empty_other_stream", 56, ()),
    ("gtr_partial", 55, ("221000", "112000", "020000")),
    ("gtr_complete", 55, ("221000", "112000", "122000")),
    ("tower", 55, ("200000", "300000", "400000", "200000", "100000", "100000", "100000")),
    ("uneven", 57, ("123123", "231231", "312300", "120000")),
)


def distribution(values):
    values = sorted(values)
    def percentile(fraction):
        position = (len(values) - 1) * fraction
        low = int(position)
        return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)
    return {"n": len(values), "p50": percentile(.5), "p95": percentile(.95), "max": values[-1]}


class MeasuredPolicy(NextgenTacticManagerPolicy):
    """Benchmark-only telemetry, including CPU/RSS inside a spawned worker."""
    def select_action(self, observation, info):
        # Every row measures a fresh search, never a repeated-input cache hit.
        self.reset()
        cpu = time.process_time()
        action = super().select_action(observation, info)
        self.measurement = {"cpu_seconds": time.process_time() - cpu, "rss_high_water_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
        return action

    @property
    def tactical_diagnostics(self):
        started = time.perf_counter()
        payload = super().tactical_diagnostics
        payload["measurement"] = {**self.measurement, "diagnostics_materialization_seconds": time.perf_counter() - started}
        return payload


def make_case(name, seed, rows, backend):
    catalog, _ = resolve_nextgen_catalog(catalog_path="train/config/nextgen_templates.yaml", templates="gtr", mode="argmax", temperature=.1, commit_turns=14, repo_root=Path(__file__).resolve().parents[1])
    # Keep PUYO-265's fixed workload independent of later runtime defaults.
    policy = MeasuredPolicy(catalog=catalog, seed=seed, backend=NativeLongHorizonSearchBackend() if backend == "native" else PythonLongHorizonSearchBackend(),
                            profile=c.SearchProfile("nextgen_smoke", 256, 128, 256),
                            search_config=LongHorizonSearchConfig(depth=4, width=4, scenarios=1, minimum_chain_count=2, max_expanded_nodes=256, decision_seed=seed ^ 0x4E4753))
    match = RealtimeVersusMatch(seed=seed)
    game = match.player_states["player_0"].simulator.game
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if int(cell):
                game.field.grid[y][x] = Puyo(c.PUBLIC_CELL_TO_COLOR[int(cell)])
    controller = RealtimePolicyController(policy, config=RealtimeDecisionConfig(latency_mode="measured"))
    observation, info = controller.nextgen_scheduler.prepare(match, "player_0", controller.config)
    return policy, observation, info


def run(backend, repeats, worker, output):
    source = source_identity()
    rows, starts = [], []
    for case, seed, board in CASES:
        policy, observation, info = make_case(case, seed, board, backend)
        executor = None
        if worker:
            started = time.perf_counter()
            executor = PolicyProcessExecutor(policy, name="nextgen-latency")
            starts.append(time.perf_counter() - started)
        try:
            for repeat in range(repeats):
                started = time.perf_counter()
                encoded = pickle.dumps((observation, info))
                encode_seconds = time.perf_counter() - started
                started = time.perf_counter()
                if executor:
                    action, policy_seconds, payload = executor.submit_policy(observation, copy.deepcopy(info)).result(timeout=120)
                else:
                    action = policy.select_action(observation, copy.deepcopy(info))
                    policy_seconds = time.perf_counter() - started
                    payload = _policy_diagnostics_snapshot(policy)
                wall = time.perf_counter() - started
                started = time.perf_counter()
                encoded_result = json.dumps(payload)
                result_encode_seconds = time.perf_counter() - started
                diagnostics = c.Diagnostics.from_dict(payload["nextgen"])
                rows.append({
                    "case": case, "repeat": repeat, "input_digest": c.semantic_digest({"public": info["nextgen"]["public"], "execution": info["nextgen"]["execution"], "search": asdict(policy.search_config)}),
                    "policy_seconds": policy_seconds, "roundtrip_seconds": wall, "outside_policy_seconds": wall - policy_seconds,
                    "input_pickle_seconds": encode_seconds, "input_pickle_bytes": len(encoded),
                    "output_json_seconds": result_encode_seconds, "output_json_bytes": len(encoded_result),
                    **payload["measurement"], "stage_elapsed_ms": payload["search"]["stage_elapsed_ms"],
                    "backend": payload["search"]["backend"], "quotas": policy.profile.to_dict(),
                    "counters": diagnostics.batch.counters.to_dict(),
                    "action": action, "tactic": diagnostics.selection.selected_tactic_id,
                    "batch_digest": diagnostics.batch.digest, "selection": diagnostics.selection.to_dict(),
                })
        finally:
            if executor:
                executor.shutdown(wait=True)
    report = {"schema": "puyo.nextgen.latency_benchmark.v1", "source": source, "source_changed_during_run": source != source_identity(), "platform": platform.platform(), "python": sys.version,
              "backend": backend, "worker": worker, "repeats": repeats, "search": asdict(policy.search_config),
              "corpus_digest": c.semantic_digest(CASES), "rows": rows,
              "summary": {key: distribution([r[key] for r in rows]) for key in ("policy_seconds", "roundtrip_seconds", "outside_policy_seconds", "cpu_seconds", "rss_high_water_kib", "diagnostics_materialization_seconds", "input_pickle_seconds", "output_json_seconds")},
              "stage_summary_ms": {key: distribution([r["stage_elapsed_ms"][key] for r in rows]) for key in ("shared", "template", "response")},
              "worker_startup_seconds": distribution(starts) if starts else None,
              "quality_status": "fixed-input latency only; not GUI QA or chain-quality evidence"}
    Path(output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"backend": backend, "worker": worker, "summary": report["summary"], "stage": report["stage_summary_ms"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("python", "native"), required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    run(**vars(args))


if __name__ == "__main__":
    main()
