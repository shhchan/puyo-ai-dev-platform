"""PUYO-236 immutable four-arm workers using the canonical safe-build runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

if __package__:
    from .puyo236_fixture_migration import migrate_frozen_request
else:
    from puyo236_fixture_migration import migrate_frozen_request

from agents.chain_structure import load_chain_structure_config
from agents.deep_chain_native import NativeDeepChainBackend, decode_request
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from agents.deep_chain_search_backend import semantic_sha256
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation

ARMS = ("none", "only240", "only242", "both")
SEEDS = tuple(range(123, 153))
OLD_SEEDS = (123, 126, 130, 133, 135, 138, 147, 151)
BASE_COMMIT = "73ab4e8ce066555042f1a20e1b3b59be3a2a8968"
CONFIG_SHA = "d347818c6bddb25dfa35af6bd5d4916179bd19a0f2cc7eeebfed08293be72b5f"
CORPUS_SHA = "4027c48d482b21547f6b1d0b554ad949e73439de4a4e09c71a503d2ca722bed4"
CONTRACT = ablation.ExperimentContract(
    ticket="PUYO-236", schema="puyo.four_pattern_comparison.v1", targets=(10,),
    canonical_safe_build=True,
)
RUNTIME_FILES = (
    "agents/long_horizon_search.py", "agents/deep_chain_native.py",
    "agents/deep_chain_native_search.py", "agents/deep_chain_builder.py",
    "agents/deep_chain_search_backend.py", "agents/chain_structure.py",
    "native/deep_chain_native/src/lib.rs", "native/deep_chain_native/src/long_horizon.rs",
    "native/deep_chain_native/src/chain_structure.rs", "native/deep_chain_native/Cargo.lock",
    "eval/deep_chain_builder_benchmark.py", "eval/deep_chain_target_ablation.py",
    "eval/simulator_parity.py", *ablation.CONFIG_PATHS,
)
RUNNER_FILES = ("eval/puyo236_four_pattern_trial.py", "eval/puyo236_fixture_migration.py",
                "eval/puyo236_run_comparison.py", "eval/puyo236_summarize.py",
                "eval/puyo236_prepare_inputs.py")


def strict_json_value(value):
    """Preserve legitimate ordered infinities; NaN always indicates a defect."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            raise ValueError("NaN is not a valid ranking sentinel")
        return "-Infinity" if value < 0 else "Infinity"
    if isinstance(value, dict):
        return {str(k): strict_json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [strict_json_value(v) for v in value]
    return value


def command(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def runtime_receipt():
    import _puyo_deep_chain_native as native
    return {
        "source_tree": command("git", "rev-parse", "HEAD^{tree}"),
        "runtime_file_sha256": {p: baseline.file_sha256(Path(p)) for p in RUNTIME_FILES},
        "native_extension_sha256": baseline.file_sha256(Path(native.__file__)),
        "dependencies": command(str(Path.cwd() / ".venv/bin/python"), "-m", "pip", "freeze").splitlines(),
        "thread_environment": {k: os.environ.get(k) for k in
                               ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "RAYON_NUM_THREADS")},
    }


def initialize(root, arm):
    assert arm in ARMS
    provenance = baseline._native_build_provenance(strict=True)
    if arm == "none":
        assert provenance["evaluated_commit"] == BASE_COMMIT
    common = ablation.configuration(contract=CONTRACT)
    assert ablation.digest(common) == CONFIG_SHA
    assert baseline.file_sha256(Path("eval/deep_chain_native_corpus.json")) == CORPUS_SHA
    runner_root = Path(__file__).resolve().parents[1]
    runner = {
        "commit": baseline.git_commit(runner_root),
        "files": {p: baseline.file_sha256(runner_root / p) for p in RUNNER_FILES},
    }
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *RUNNER_FILES],
                   cwd=runner_root, check=True, capture_output=True)
    current = {
        "arm": arm, "build_provenance": provenance, "common_configuration": common,
        "runner": runner, "runtime": runtime_receipt(),
    }
    path = root / arm / "experiment_manifest.json"
    if path.exists():
        manifest = ablation.load_manifest(root / arm, contract=CONTRACT)
        for key, value in current.items():
            if key == "build_provenance":
                for name in ("evaluated_commit", "host", "capabilities", "wheels", "configuration"):
                    assert manifest[key][name] == value[name], (key, name)
            else:
                assert manifest[key] == value, key
        return manifest
    manifest = {
        "schema_version": CONTRACT.schema, "ticket": CONTRACT.ticket,
        "targets": [10], "identities": ablation.identities(contract=CONTRACT),
        "run_count": 60, "unique_seed_count": 30, "repeats_are_independent_seeds": False,
        "created_at_utc": baseline.utc_timestamp(), **current,
        "common_configuration_sha256": CONFIG_SHA,
        "condition_configuration_sha256": {"10": ablation.digest({"common": common, "target_chain_count": 10})},
        "effective_evaluator_config": load_chain_structure_config().to_dict(),
        "adoption": "pending_user_choice", "new_human_gui_qa": "pending",
    }
    manifest["manifest_sha256"] = ablation.digest(manifest)
    baseline._write_json(path, manifest)
    return manifest


def result_receipt(request, result):
    representatives = {}
    for root in result.ranked_roots:
        node = result.representatives.get(root.root_action)
        fire = root.best_fire
        if fire is not None:
            assert node is not None
            assert tuple(node.path) == tuple(fire.path)
            assert node.scenario_id == fire.scenario_id
        representatives[str(root.root_action)] = None if node is None else {
            "path": list(node.path), "scenario_id": node.scenario_id,
            "state_sha256": hashlib.sha256(node.state.to_bytes()).hexdigest(),
        }
    return {
        "request_digest": semantic_sha256({
            "root": request.root_state.to_bytes().hex(),
            "known_pairs": [[c.name for c in pair] for pair in request.known_pairs],
            "search": asdict(request.search_config), "evaluator": request.evaluator_config.to_dict(),
            "request_id": request.request_id,
        }),
        "known_pair_count": len(request.known_pairs),
        "evaluator_semantic_sha256": semantic_sha256(request.evaluator_config.to_dict()),
        "evaluator_yaml_sha256": request.evaluator_config_sha256,
        "strict_all_root_parity_passed": True,
        "candidate_representatives_match": True,
        "ranked_root_actions": [r.root_action for r in result.ranked_roots],
        "root_ranking_keys": [strict_json_value(r.ranking_key) for r in result.ranked_roots],
        "root_evidence_sha256": semantic_sha256(strict_json_value([r.to_dict() for r in result.ranked_roots])),
        "representatives": representatives,
        "search_digest": result.deterministic_digest,
    }


def policy_factory(receipts, audit_seconds=None):
    evaluator = load_chain_structure_config()
    yaml_sha = baseline.file_sha256(Path("train/config/v1_7_chain_structure.yaml"))

    def factory(seed, profile):
        policy = baseline._policy_factory(seed, profile, "native", 10)
        backend = policy.search_backend
        original = backend.search

        def checked(request):
            config = request.search_config
            assert (config.depth, config.width, config.scenarios, config.max_expanded_nodes,
                    config.minimum_chain_count, config.fire_context, config.terminal_fire_rule) == (
                        16, 250, 6, 600000, 10, "safe_build", "record_and_stop")
            assert request.evaluator_config == evaluator
            assert request.evaluator_config_sha256 == yaml_sha
            execution = original(request)
            audit_started = time.perf_counter()
            receipts.append(result_receipt(request, execution.result))
            if audit_seconds is not None:
                audit_seconds.append(time.perf_counter() - audit_started)
            return execution

        backend.search = checked
        return policy
    return factory


def validate_receipts(run, manifest):
    records = [r for r in run["records"] if "action" in r]
    assert len(records) == len(run["request_receipts"])
    if "request_audit_seconds" in run:
        assert len(records) == len(run["request_audit_seconds"])
        assert all(0 <= elapsed <= record["elapsed_seconds"] for elapsed, record in
                   zip(run["request_audit_seconds"], records, strict=True))
    for record, receipt in zip(records, run["request_receipts"], strict=True):
        config = record["search"]["backend"]["configuration"]
        assert config["evaluator_config_sha256"] == receipt["evaluator_yaml_sha256"] == manifest[
            "common_configuration"]["configuration_sha256"]["train/config/v1_7_chain_structure.yaml"]
        assert receipt["evaluator_semantic_sha256"] == semantic_sha256(manifest["effective_evaluator_config"])
        assert receipt["strict_all_root_parity_passed"] and receipt["candidate_representatives_match"]
        assert len(receipt["ranked_root_actions"]) == record["selection"]["candidate_count"]
        assert receipt["ranked_root_actions"][0] == record["action"]
        assert receipt["search_digest"] == record["search"]["deterministic_digest"]
        representative = receipt["representatives"][str(record["action"])]
        assert representative is not None
        assert representative["path"] == [s["action"] for s in record["plan"]["steps"]]
        assert record["scenario_accounting"]["failure_count"] == 0
        assert record["parity"]["passed"] and record["actual_result"]["valid"]
        assert not record["fallback"]["used"]
    assert run["simulator_parity_mismatch_count"] == run["fallback_count"] == 0
    if run["termination_reason"] == "game_over":
        assert run["game_over"] and records and records[-1]["actual_result"]["game_over"]


def worker(root, arm, seed, repeat):
    manifest = initialize(root, arm)
    identity = next(i for i in manifest["identities"] if (i["seed"], i["repeat"]) == (seed, repeat))
    path = ablation.run_path(root / arm, identity)
    if path.exists():
        raise ValueError(f"Refusing to overwrite {path}")
    receipts = []
    audit_seconds = []
    started = baseline.utc_timestamp()
    run = baseline.run_benchmark_run(
        seed=seed, repeat=repeat, profile="reference", max_steps=40, backend="native",
        target_chain_count=10, policy_factory=policy_factory(receipts, audit_seconds),
    )
    run.update(schema_version=CONTRACT.schema, ticket=CONTRACT.ticket, arm=arm,
               run_id=identity["run_id"], quality_floor=10, manifest_sha256=manifest["manifest_sha256"],
               request_receipts=receipts, request_audit_seconds=audit_seconds, worker_started_at_utc=started,
               worker_finished_at_utc=baseline.utc_timestamp(), worker_pid=os.getpid())
    # Retain the attempted run before validation, including incomplete policy errors.
    ablation.write_run(path, strict_json_value(run))
    ablation.validate_run(run, identity, manifest, contract=CONTRACT)
    if not run["fully_evaluated"]:
        raise RuntimeError(f"Incomplete identity saved: {path}: {run['termination_reason']}")
    validate_receipts(run, manifest)
    print(json.dumps({"arm": arm, "seed": seed, "repeat": repeat,
                      "maximum_chain": run["maximum_actual_fire_chain_count"],
                      "premature": run["premature_fire_count"], "game_over": run["game_over"],
                      "seconds": run["elapsed_seconds"], "raw_sha256": baseline.file_sha256(path)}), flush=True)


def preflight(root, arm):
    manifest = initialize(root, arm)
    path = root / arm / "preflight.json.gz"
    assert not path.exists()
    backend = NativeDeepChainBackend(canonical=True)
    inputs = baseline._read_json(root / "fixed-inputs.json")
    samples = []
    for case in inputs["cases"]:
        encoded, migration = migrate_frozen_request(bytes.fromhex(case["request_hex"]))
        request = decode_request(encoded)
        native = backend.decide(request)
        result = materialize_native_long_horizon_result(native, request)
        samples.append({"seed": case["seed"], "turn": case["turn"], "migration": migration,
                        "receipt": result_receipt(request, result), "counters": result.counters.to_dict(),
                        "roots": strict_json_value([r.to_dict() for r in result.ranked_roots])})
    private = []
    for seed in SEEDS:
        obs, info = baseline._initial_observation_and_info(seed, max_steps=40)
        policy = policy_factory([])(seed, "reference")
        semantics = []
        for counterfactual in (False, True):
            policy.reset()
            observation, details = dict(obs), dict(info)
            if counterfactual:
                observation["private_future_queue"] = "private-sentinel"
                details.update(simulator="private-simulator", future_queue="private-queue")
            action = int(policy.select_action(observation, details))
            diagnostics = policy.tactical_diagnostics
            semantics.append({"action": action, "plan": baseline._plan_summary(diagnostics["plan"]),
                              "search_digest": diagnostics["search"]["deterministic_digest"]})
            assert not diagnostics["fallback"]["used"]
            assert baseline._scenario_accounting(diagnostics)["failure_count"] == 0
        assert semantics[0] == semantics[1]
        private.append({"seed": seed, "counterfactual_matches": True, "semantic": semantics[0]})
    audit = baseline.audit_future_isolation(SEEDS, max_steps=40)
    assert audit["passed"]
    ablation.write_run(path, {"manifest_sha256": manifest["manifest_sha256"],
                              "inputs_sha256": baseline.file_sha256(root / "fixed-inputs.json"),
                              "fixed_samples": samples, "private_counterfactuals": private,
                              "future_isolation": audit,
                              "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
    print(json.dumps({"arm": arm, "preflight": "passed", "fixed": len(samples), "private_seeds": len(private)}), flush=True)


def smoke(root, arm):
    manifest = initialize(root, arm)
    receipts = []
    run = baseline.run_benchmark_run(seed=123, repeat=1, profile="reference", max_steps=1,
                                      backend="native", target_chain_count=10,
                                      policy_factory=policy_factory(receipts))
    run["request_receipts"] = receipts
    path = root / arm / "smoke.json.gz"
    assert not path.exists()
    ablation.write_run(path, strict_json_value(run))
    assert run["fully_evaluated"] and run["completed_turns"] == 1
    validate_receipts(run, manifest)
    print(json.dumps({"arm": arm, "smoke": "passed"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "smoke", "worker", "preflight"))
    parser.add_argument("root", type=Path)
    parser.add_argument("arm", choices=ARMS)
    parser.add_argument("--seed", type=int, choices=SEEDS)
    parser.add_argument("--repeat", type=int, choices=(1, 2))
    args = parser.parse_args()
    if args.command == "worker":
        worker(args.root, args.arm, args.seed, args.repeat)
    elif args.command == "init":
        print(initialize(args.root, args.arm)["manifest_sha256"])
    elif args.command == "smoke":
        smoke(args.root, args.arm)
    else:
        preflight(args.root, args.arm)


if __name__ == "__main__":
    main()
