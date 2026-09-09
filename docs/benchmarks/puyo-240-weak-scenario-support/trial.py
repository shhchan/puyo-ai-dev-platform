"""PUYO-240 単独試行。既存の全状態/全root検証を保持する。"""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

from agents.deep_chain_native import NativeDeepChainBackend, decode_request
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation

SEEDS = (123, 126, 130, 133, 135, 138, 147, 151)
ablation.SEEDS = SEEDS
CONTRACT = ablation.ExperimentContract(
    ticket="PUYO-240", schema="puyo.small_quality_trial.v1", targets=(10,),
    canonical_safe_build=True,
)


def initialize(root: Path) -> dict:
    path = root / "experiment_manifest.json"
    provenance = baseline._native_build_provenance(strict=True)
    configuration = ablation.configuration(contract=CONTRACT)
    if path.exists():
        manifest = baseline._read_json(path)
        assert manifest["common_configuration"] == configuration
        for key in ("evaluated_commit", "host", "capabilities", "wheels", "configuration"):
            assert manifest["build_provenance"][key] == provenance[key], key
        return manifest
    manifest = {
        "schema_version": CONTRACT.schema, "ticket": CONTRACT.ticket,
        "targets": [10], "identities": ablation.identities(contract=CONTRACT),
        "run_count": 16, "unique_seed_count": 8,
        "created_at_utc": baseline.utc_timestamp(),
        "build_provenance": provenance, "common_configuration": configuration,
        "common_configuration_sha256": ablation.digest(configuration),
        "condition_configuration_sha256": {
            "10": ablation.digest({"common": configuration, "target_chain_count": 10})
        },
        "runner_sha256": baseline.file_sha256(Path(__file__)),
    }
    manifest["manifest_sha256"] = ablation.digest(manifest)
    baseline._write_json(path, manifest)
    return manifest


def worker(root: Path, seed: int, repeat: int) -> None:
    manifest = initialize(root)
    identity = next(i for i in manifest["identities"] if i["seed"] == seed and i["repeat"] == repeat)
    path = ablation.run_path(root, identity)
    if path.exists():
        raise ValueError("既存runを上書きしません")
    run = baseline.run_benchmark_run(seed=seed, repeat=repeat, profile="reference",
                                     max_steps=40, backend="native", target_chain_count=10)
    run.update(schema_version=CONTRACT.schema, ticket=CONTRACT.ticket,
               run_id=identity["run_id"], quality_floor=10,
               manifest_sha256=manifest["manifest_sha256"])
    ablation.validate_run(run, identity, manifest, contract=CONTRACT)
    ablation.write_run(path, run)
    print(json.dumps({"condition": root.name, "seed": seed, "repeat": repeat,
                      "maximum_chain": run["maximum_actual_fire_chain_count"],
                      "premature": run["premature_fire_count"], "game_over": run["game_over"],
                      "termination": run["termination_reason"], "seconds": run["elapsed_seconds"]}), flush=True)


def fixed(root: Path, repeat: int) -> None:
    manifest = initialize(root)
    path = root / f"fixed-{repeat:02d}.json"
    if path.exists():
        raise ValueError("既存fixed診断を上書きしません")
    samples = []
    for seed in (123, 135):
        obs, info = baseline._initial_observation_and_info(seed, max_steps=40)
        policy = baseline._policy_factory(seed, "reference", "native", 10)
        for label in ("cold", "warm", "private_counterfactual"):
            policy.reset()
            observation, details = dict(obs), dict(info)
            if label == "private_counterfactual":
                observation["private_future_queue"] = "private-sentinel"
                details.update(simulator="private-simulator", future_queue="private-queue")
            start = time.perf_counter()
            action = int(policy.select_action(observation, details))
            elapsed = time.perf_counter() - start
            d = policy.tactical_diagnostics
            semantic = {"action": action, "plan": baseline._plan_summary(d["plan"]),
                        "search_digest": d["search"]["deterministic_digest"]}
            samples.append({"seed": seed, "label": label, "elapsed_seconds": elapsed,
                            "semantic": semantic, "backend": baseline._json_ready(d["backend"]),
                            "search": baseline._json_ready(d["search"]),
                            "scenario_accounting": baseline._scenario_accounting(d),
                            "fallback": baseline._json_ready(d["fallback"])})
        assert samples[-1]["semantic"] == samples[-2]["semantic"] == samples[-3]["semantic"]
    payload = {"manifest_sha256": manifest["manifest_sha256"], "samples": samples,
               "future_boundary": baseline.audit_future_isolation(SEEDS, max_steps=40),
               "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    source = baseline._read_json(Path(__file__).parent / "seed-135-all-roots.json.gz")
    backend = NativeDeepChainBackend(canonical=True)
    request_samples = []
    for turn in (10, 11):
        request = decode_request(bytes.fromhex(source["cases"][turn]["request_hex"]))
        for label in ("first", "repeat"):
            start = time.perf_counter()
            native = backend.decide(request)
            materialization_start = time.perf_counter()
            result = materialize_native_long_horizon_result(native, request)
            end = time.perf_counter()
            request_samples.append({
                "seed": 135, "turn": turn, "label": label,
                "elapsed_seconds": end - start,
                "materialization_seconds": end - materialization_start,
                "native_telemetry": dict(native.telemetry),
                "counters": result.counters.to_dict(),
                "native_ranking": list(native.ranked_root_actions),
                "strict_all_root_parity_passed": True,
                "guard_applied": any(root.ranking_rule_version.endswith(".v3")
                                     for root in result.root_evidence),
            })
    payload["saved_request_samples"] = request_samples
    payload["saved_request_source_sha256"] = baseline.file_sha256(
        Path(__file__).parent / "seed-135-all-roots.json.gz"
    )
    baseline._write_json(path, payload)
    print(json.dumps({"condition": root.name, "fixed_repeat": repeat, "private_matches": True}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "worker", "fixed"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--seed", type=int, choices=SEEDS)
    parser.add_argument("--repeat", type=int)
    args = parser.parse_args()
    if args.command == "init":
        print(initialize(args.root)["manifest_sha256"])
    elif args.command == "worker":
        worker(args.root, args.seed, args.repeat)
    else:
        fixed(args.root, args.repeat)
