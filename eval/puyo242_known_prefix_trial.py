"""PUYO-242 single known-prefix comparison with isolated source/release builds."""

from __future__ import annotations

import argparse
import json
import math
import resource
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from puyo242_request_migration import migrate_frozen_request

from agents.chain_structure import load_chain_structure_config
from agents.deep_chain_native import NativeDeepChainBackend, decode_request
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from agents.deep_chain_search_backend import semantic_sha256
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation

SEEDS = (123, 126, 130, 133, 135, 138, 147, 151)
CONDITIONS = ("baseline", "candidate")
SOURCE_COMMIT = "73ab4e8ce066555042f1a20e1b3b59be3a2a8968"
WHEEL_SHA256 = "6c5dde345ec65c6ce97f5b7a17a575a9060b4cfb260a9da383d72a6456a8fde6"
FIXED_BOARDS = ((123, 29), (126, 22), (130, 24), (135, 33), (138, 30), (147, 27), (151, 31))
ablation.SEEDS = SEEDS
CONTRACT = ablation.ExperimentContract(
    ticket="PUYO-242", schema="puyo.known_prefix_trial.v1", targets=(10,),
    canonical_safe_build=True,
)


def encode_ranking_key(values):
    """Represent ordered +/-Infinity sentinels in strict JSON, rejecting NaN."""
    result = []
    for value in values:
        if isinstance(value, float) and not math.isfinite(value):
            if math.isnan(value):
                raise ValueError("NaN is not a valid ranking sentinel")
            value = "-Infinity" if value < 0 else "Infinity"
        result.append(value)
    return result


def evaluator_config(condition):
    assert condition in CONDITIONS
    return load_chain_structure_config()


def policy_factory(condition, receipts):
    config = evaluator_config(condition)
    checksum = semantic_sha256(config.to_dict())
    file_checksum = baseline.file_sha256(Path("train/config/v1_7_chain_structure.yaml"))

    def factory(seed, profile):
        policy = baseline._policy_factory(seed, profile, "native", 10)
        backend = policy.search_backend
        original = backend.search

        def checked_search(request):
            assert request.evaluator_config == config
            assert request.evaluator_config_sha256 == file_checksum
            assert (request.search_config.depth, request.search_config.width,
                    request.search_config.scenarios, request.search_config.max_expanded_nodes,
                    request.search_config.minimum_chain_count) == (16, 250, 6, 600000, 10)
            execution = original(request)  # existing strict all-root materializer
            result = execution.result
            receipts.append({
                "request_digest": semantic_sha256({
                    "root": request.root_state.to_bytes().hex(),
                    "known_pairs": [[c.name for c in pair] for pair in request.known_pairs],
                    "search": asdict(request.search_config),
                    "evaluator": request.evaluator_config.to_dict(),
                    "request_id": request.request_id,
                }),
                "known_pair_count": len(request.known_pairs),
                "evaluator_config_sha256": checksum,
                "declared_evaluator_config_file_sha256": file_checksum,
                "strict_all_root_parity_passed": True,
                "ranked_root_actions": [r.root_action for r in result.ranked_roots],
                "root_count": len(result.root_evidence),
                "search_digest": result.deterministic_digest,
            })
            return execution

        backend.search = checked_search
        return policy
    return factory


def initialize(root, condition):
    path = root / condition / "experiment_manifest.json"
    provenance = baseline._native_build_provenance(strict=True)
    if condition == "baseline":
        assert provenance["evaluated_commit"] == SOURCE_COMMIT
        assert provenance["wheels"][0]["sha256"] == WHEEL_SHA256
    else:
        assert provenance["evaluated_commit"] == baseline.git_commit(Path.cwd())
    common = ablation.configuration(contract=CONTRACT)
    config = evaluator_config(condition).to_dict()
    runner_root = Path(__file__).resolve().parents[1]
    runner = {
        "file": "eval/puyo242_known_prefix_trial.py",
        "sha256": baseline.file_sha256(Path(__file__)),
        "git_commit": baseline.git_commit(runner_root),
    }
    # Reject uncommitted edits to the measured runner, while allowing later
    # independent evidence/report files to be authored during the measurements.
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", runner["file"]],
                   cwd=runner_root, check=True, capture_output=True)
    if path.exists():
        manifest = ablation.load_manifest(root / condition, contract=CONTRACT)
        for key in ("evaluated_commit", "host", "capabilities", "wheels", "configuration"):
            assert manifest["build_provenance"][key] == provenance[key], key
        assert manifest["common_configuration"] == common
        assert manifest["effective_evaluator_config"] == config
        assert manifest["runner"] == runner
        return manifest
    manifest = {
        "schema_version": CONTRACT.schema, "ticket": CONTRACT.ticket,
        "targets": [10], "identities": ablation.identities(contract=CONTRACT),
        "run_count": 16, "unique_seed_count": 8,
        "created_at_utc": baseline.utc_timestamp(),
        "build_provenance": provenance, "common_configuration": common,
        "common_configuration_sha256": ablation.digest(common),
        "condition_configuration_sha256": {
            "10": ablation.digest({"common": common, "target_chain_count": 10})
        },
        "condition": condition, "prefixed_conditions": list(CONDITIONS),
        "effective_evaluator_config": config,
        "effective_evaluator_config_sha256": semantic_sha256(config),
        "effective_runtime_sha256": semantic_sha256({"common": common, "evaluator": config}),
        "runner": runner, "fixed_boards_seed_zero_based_turn": FIXED_BOARDS,
        "single_change": "target depth <= public known pair count before terminal score; no search budget changes",
    }
    manifest["manifest_sha256"] = ablation.digest(manifest)
    baseline._write_json(path, manifest)
    return manifest


def validate_evaluator(run, manifest):
    checksum = manifest["effective_evaluator_config_sha256"]
    assert checksum == semantic_sha256(manifest["effective_evaluator_config"])
    records = [r for r in run["records"] if "action" in r]
    assert len(run["request_receipts"]) == len(records)
    for record, receipt in zip(records, run["request_receipts"], strict=True):
        config = record["search"]["backend"]["configuration"]
        source_checksum = manifest["common_configuration"]["configuration_sha256"]["train/config/v1_7_chain_structure.yaml"]
        assert config["evaluator_config_sha256"] == source_checksum == receipt["declared_evaluator_config_file_sha256"]
        assert receipt["evaluator_config_sha256"] == checksum
        assert config["evaluator_config_version"] == manifest["effective_evaluator_config"]["weight_version"]
        assert receipt["strict_all_root_parity_passed"]
        assert receipt["root_count"] == record["selection"]["candidate_count"]
        assert receipt["ranked_root_actions"][0] == record["action"]
        assert receipt["search_digest"] == record["search"]["deterministic_digest"]
        assert record["scenario_accounting"]["failure_count"] == 0


def worker(root, condition, seed, repeat):
    manifest = initialize(root, condition)
    identity = next(i for i in manifest["identities"] if i["seed"] == seed and i["repeat"] == repeat)
    path = ablation.run_path(root / condition, identity)
    if path.exists():
        raise ValueError("refusing to overwrite a measured run")
    receipts = []
    run = baseline.run_benchmark_run(
        seed=seed, repeat=repeat, profile="reference", max_steps=40,
        backend="native", target_chain_count=10,
        policy_factory=policy_factory(condition, receipts),
    )
    run.update(schema_version=CONTRACT.schema, ticket=CONTRACT.ticket,
               run_id=identity["run_id"], quality_floor=10,
               manifest_sha256=manifest["manifest_sha256"], request_receipts=receipts)
    ablation.validate_run(run, identity, manifest, contract=CONTRACT)
    validate_evaluator(run, manifest)
    ablation.write_run(path, run)
    if not run["fully_evaluated"]:
        raise RuntimeError(f"Incomplete identity saved at {path}: {run['termination_reason']}")
    print(json.dumps({"condition": condition, "seed": seed, "repeat": repeat,
                      "maximum_chain": run["maximum_actual_fire_chain_count"],
                      "premature": run["premature_fire_count"], "game_over": run["game_over"],
                      "termination": run["termination_reason"], "seconds": run["elapsed_seconds"]}), flush=True)


def fixed(root, condition, repeat):
    manifest = initialize(root, condition)
    path = root / condition / f"fixed-{repeat:02d}.json"
    if path.exists():
        raise ValueError("refusing to overwrite fixed-board measurements")
    config = evaluator_config(condition)
    backend = NativeDeepChainBackend(canonical=True)
    requests = baseline._read_json(root / "fixed-inputs.json")
    samples = []
    for case in requests["cases"]:
        migrated, migration = migrate_frozen_request(bytes.fromhex(case["request_hex"]))
        request = decode_request(migrated)
        assert request.evaluator_config == config
        for label in ("first", "repeat"):
            start = time.perf_counter()
            native = backend.decide(request)
            middle = time.perf_counter()
            result = materialize_native_long_horizon_result(native, request)
            end = time.perf_counter()
            samples.append({
                "seed": case["seed"], "turn": case["turn"], "label": label,
                "request_migration": migration,
                "elapsed_seconds": end - start, "materialization_seconds": end - middle,
                "native_telemetry": dict(native.telemetry), "counters": result.counters.to_dict(),
                "native_ranking": list(native.ranked_root_actions),
                "strict_all_root_parity_passed": True,
                "native_digest": native.deterministic_digest,
                "python_digest": result.deterministic_digest,
                "evaluator_config_sha256": semantic_sha256(request.evaluator_config.to_dict()),
                "roots": [dict(r.to_dict(), ranking_key=encode_ranking_key(r.ranking_key)) for r in result.ranked_roots],
                "representatives": {str(a): {"path": list(n.path), "state": n.state.to_bytes().hex()} for a, n in result.representatives.items()},
            })
    private = []
    for seed in (123, 135):
        obs, info = baseline._initial_observation_and_info(seed, max_steps=40)
        policy = policy_factory(condition, [])(seed, "reference")
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
            private.append({"seed": seed, "label": label, "elapsed_seconds": elapsed,
                            "semantic": {"action": action, "plan": baseline._plan_summary(d["plan"]),
                                         "search_digest": d["search"]["deterministic_digest"]},
                            "backend": baseline._json_ready(d["backend"]),
                            "scenario_accounting": baseline._scenario_accounting(d),
                            "fallback": baseline._json_ready(d["fallback"])})
        assert private[-1]["semantic"] == private[-2]["semantic"] == private[-3]["semantic"]
    baseline._write_json(path, {
        "manifest_sha256": manifest["manifest_sha256"], "samples": samples,
        "private_samples": private, "future_boundary": baseline.audit_future_isolation(SEEDS, max_steps=40),
        "inputs_sha256": baseline.file_sha256(root / "fixed-inputs.json"),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    })
    print(json.dumps({"condition": condition, "fixed_repeat": repeat, "private_matches": True}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "worker", "fixed"))
    parser.add_argument("root", type=Path)
    parser.add_argument("condition", choices=CONDITIONS)
    parser.add_argument("--seed", type=int, choices=SEEDS)
    parser.add_argument("--repeat", type=int)
    args = parser.parse_args()
    if args.command == "init":
        print(initialize(args.root, args.condition)["manifest_sha256"])
    elif args.command == "worker":
        worker(args.root, args.condition, args.seed, args.repeat)
    else:
        fixed(args.root, args.condition, args.repeat)


if __name__ == "__main__":
    main()
