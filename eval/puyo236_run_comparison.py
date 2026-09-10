"""Sequential fresh-process supervisor with balanced order and strict resume."""

from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import time
from collections import Counter
from pathlib import Path

if __package__:
    from . import puyo236_four_pattern_trial as trial
else:
    import puyo236_four_pattern_trial as trial

from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation


def schedule(seeds=trial.SEEDS, repeats=(1, 2)):
    result = []
    for index, (seed, repeat) in enumerate((s, r) for s in seeds for r in repeats):
        order = trial.ARMS[index % 4:] + trial.ARMS[:index % 4]
        for position, arm in enumerate(order):
            result.append({"ordinal": len(result), "seed": seed, "repeat": repeat,
                           "arm": arm, "position": position})
    return result


def balanced_counts(entries):
    return {arm: dict(Counter(e["position"] for e in entries if e["arm"] == arm)) for arm in trial.ARMS}


def invoke(workspace, output, command, arm, *extra):
    checkout = workspace / arm
    env = {**os.environ, "PYTHONPATH": str(checkout), "PYTHONHASHSEED": "0",
           "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
    argv = [str(checkout / ".venv/bin/python"), str(Path(trial.__file__).resolve()),
            command, str(output), arm, *map(str, extra)]
    started = baseline.utc_timestamp()
    clock = time.perf_counter()
    process = subprocess.run(argv, cwd=checkout, env=env, text=True, capture_output=True, check=False)
    return {"command": argv, "started_at_utc": started, "finished_at_utc": baseline.utc_timestamp(),
            "fresh_process_seconds": time.perf_counter() - clock, "returncode": process.returncode,
            "stdout": process.stdout, "stderr": process.stderr}


def frozen_declaration(workspace, output):
    manifests = {arm: ablation.load_manifest(output / arm, contract=trial.CONTRACT) for arm in trial.ARMS}
    hosts = [m["build_provenance"]["host"] for m in manifests.values()]
    assert all(h == hosts[0] for h in hosts)
    dependencies = [[p for p in m["runtime"]["dependencies"] if not p.startswith("puyo-deep-chain-native")]
                    for m in manifests.values()]
    assert all(d == dependencies[0] for d in dependencies)
    assert all(m["common_configuration_sha256"] == trial.CONFIG_SHA for m in manifests.values())
    return {
        "ticket": "PUYO-236", "schema_version": "puyo.four_pattern_schedule.v1",
        "workspace": str(workspace), "output": str(output),
        "schedule": schedule(), "position_counts": balanced_counts(schedule()),
        "arm_manifest_sha256": {arm: m["manifest_sha256"] for arm, m in manifests.items()},
        "common_configuration_sha256": trial.CONFIG_SHA,
        "runner": manifests["none"]["runner"], "host": hosts[0],
        "single_local_heavy_process": True, "adoption": "pending_user_choice",
    }


def verify_saved(path, entry, manifest):
    run = baseline._read_json(path)
    identity = next(i for i in manifest["identities"] if (i["seed"], i["repeat"]) == (entry["seed"], entry["repeat"]))
    ablation.validate_run(run, identity, manifest, contract=trial.CONTRACT)
    if not run["fully_evaluated"]:
        raise ValueError(f"Incomplete attempt is immutable and cannot be reused: {path}")
    trial.validate_receipts(run, manifest)
    return run


def execute(workspace, output, mode):
    output.mkdir(parents=True, exist_ok=True)
    with (workspace / "measurement.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for arm in trial.ARMS:
            response = invoke(workspace, output, "init", arm)
            if response["returncode"]:
                raise RuntimeError(response)
        declaration = frozen_declaration(workspace, output)
        declared = output / "four-arm-manifest.json"
        if declared.exists():
            assert baseline._read_json(declared) == declaration
        else:
            baseline._write_json(declared, declaration)
        if mode == "init":
            print(declared)
            return
        if mode in ("smoke", "preflight"):
            for arm in trial.ARMS:
                receipt = invoke(workspace, output, mode, arm)
                baseline._write_json(output / f"{mode}-{arm}-process.json", receipt)
                print(receipt["stdout"], end="", flush=True)
                if receipt["returncode"]:
                    raise RuntimeError(receipt)
            return
        entries = schedule(trial.OLD_SEEDS, (1,)) if mode == "reproduce" else declaration["schedule"]
        if mode == "reproduce":
            entries = [e for e in entries if e["arm"] != "both"]
        for entry in entries:
            arm = entry["arm"]
            manifest = ablation.load_manifest(output / arm, contract=trial.CONTRACT)
            identity = next(i for i in manifest["identities"] if (i["seed"], i["repeat"]) == (entry["seed"], entry["repeat"]))
            path = ablation.run_path(output / arm, identity)
            receipt_path = output / "processes" / f"{entry['ordinal']:03d}-{arm}-seed-{entry['seed']}-repeat-{entry['repeat']}.json"
            if path.exists():
                verify_saved(path, entry, manifest)
                if not receipt_path.exists():
                    raise ValueError(f"Run exists without successful process receipt: {path}")
                old = baseline._read_json(receipt_path)
                assert old["returncode"] == 0 and old["raw_sha256"] == baseline.file_sha256(path)
                assert old["schedule_entry"] == entry
                continue
            receipt = invoke(workspace, output, "worker", arm, "--seed", entry["seed"], "--repeat", entry["repeat"])
            receipt["schedule_entry"] = entry
            receipt["raw_sha256"] = baseline.file_sha256(path) if path.exists() else None
            baseline._write_json(receipt_path, receipt)
            print(receipt["stdout"], end="", flush=True)
            if receipt["returncode"]:
                raise RuntimeError(f"Attempt retained at {receipt_path}: {receipt['stderr']}")
            verify_saved(path, entry, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("init", "smoke", "preflight", "reproduce", "measure"))
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    execute(args.workspace.resolve(), args.output.resolve(), args.mode)


if __name__ == "__main__":
    main()
