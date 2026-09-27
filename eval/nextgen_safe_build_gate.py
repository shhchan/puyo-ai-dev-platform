"""Frozen native safe-build cohort for G2; other gate evidence stays unknown.

Run init, then run-all (one fresh process per identity), then finalize. This
does not replace the historical Python smoke benchmark or authorize training.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from agents.deep_chain_builder import load_deep_chain_builder_config
from agents.deep_chain_native import NATIVE_MODULE_NAME, NativeDeepChainBackend
from eval.nextgen_gates import REPEATS, SEEDS, THRESHOLDS, digest, evaluate
from eval.nextgen_realtime_diagnostic import source_identity
from eval.nextgen_safe_build_diagnostic import make_policy, measure

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "nextgen_safe_build"
SCHEMA = "puyo.nextgen.native_safe_build_gate.v1"


def write_new(path, value):
    content = json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode()
    if path.suffix == ".gz":
        content = gzip.compress(content, mtime=0)
    with path.open("xb") as stream:
        stream.write(content)


def read(path):
    content = path.read_bytes()
    return json.loads(gzip.decompress(content) if path.suffix == ".gz" else content)


def build_identity():
    # Strict ABI/build validation happens before any experiment is declared.
    NativeDeepChainBackend()
    module = importlib.import_module(NATIVE_MODULE_NAME)
    binary = Path(module.__file__).resolve()
    return {
        "host": platform.uname()._asdict(), "python": sys.version,
        "python_binary_sha256": hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),
        "native_binary": str(binary), "native_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "capabilities_sha256": hashlib.sha256(module.capabilities()).hexdigest(),
        "packages": {name: importlib.metadata.version(name) for name in ("numpy", "PyYAML", "pygame", "gymnasium")},
        "thread_environment": {name: os.environ.get(name) for name in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "RAYON_NUM_THREADS")},
    }


def initialize(output):
    changed = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "agents", "puyo_env", "src", "eval", "train", "tests"],
        cwd=ROOT, text=True,
    ).strip()
    if changed:
        raise ValueError("commit source and tests before freezing evaluation")
    policy = make_policy("nextgen", SEEDS[0], PROFILE)
    reference = load_deep_chain_builder_config().profile("reference")
    matched = all(getattr(policy.search_config, name) == getattr(reference, name)
                  for name in ("depth", "width", "scenarios", "max_expanded_nodes"))
    if not matched or policy.search_config.minimum_chain_count != 10:
        raise ValueError("safe-build budget no longer matches reference calibration")
    config = {
        "seeds": list(SEEDS), "repeats": list(REPEATS), "thresholds": THRESHOLDS,
        "runtime_information": "public_only", "reference_profile_calibrated": True,
        "calibration": "reference depth/width/scenarios/shared quota equality; PUYO-266 budget calibration; performance assessed separately",
        "profile": policy.profile.to_dict(), "search_at_first_seed": asdict(policy.search_config),
        "catalog_digest": policy.catalog.semantic_digest,
        "template_binding_budget": policy.template_binding_budget,
        "backend": "native", "latency_mode": "configured", "configured_latency_ticks": 0,
        "environment": "realtime solo; opponent frozen; outgoing attacks suppressed",
        "placements": 40, "max_ticks": 30000, "workers": 1,
        "process_isolation": "new process for every seed/repeat",
        "seed_streams": {"environment": "run_seed", "selector": "run_seed", "search": "run_seed XOR 0x4E4753"},
        "required_other_evidence": {"G0": None, "G1": None, "threat_fixtures": None},
    }
    manifest = {"schema": SCHEMA, "source": source_identity(), "build": build_identity(),
                "config": config, "config_sha256": digest(config),
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    manifest["sha256"] = digest(manifest)
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "manifest.json", manifest)
    return manifest


def load_manifest(output, *, execution=False):
    value = read(output / "manifest.json")
    if value["schema"] != SCHEMA or digest({k: v for k, v in value.items() if k != "sha256"}) != value["sha256"]:
        raise ValueError("manifest schema/checksum mismatch")
    if digest(value["config"]) != value["config_sha256"]:
        raise ValueError("configuration checksum mismatch")
    if execution and (source_identity() != value["source"] or build_identity() != value["build"]):
        raise ValueError("source/build/host changed after declaration")
    return value


def worker(output, seed, repeat):
    manifest = load_manifest(output, execution=True)
    if seed not in SEEDS or repeat not in REPEATS:
        raise ValueError("unexpected identity")
    path = output / f"seed-{seed}-repeat-{repeat}.json.gz"
    if path.exists():
        raise ValueError("run already exists; refusing overwrite")
    try:
        result = measure("nextgen", seed, PROFILE, placements=40)
        load_manifest(output, execution=True)
    except Exception as error:
        write_new(output / f"failure-{seed}-{repeat}.json", {
            "seed": seed, "repeat": repeat, "manifest_sha256": manifest["sha256"],
            "error_type": type(error).__name__, "error": str(error),
        })
        raise
    result.update(repeat=repeat, manifest_sha256=manifest["sha256"],
                  termination="game_over" if result["game_over"] else "placements" if result["placements"] == 40 else "tick_limit")
    write_new(path, result)
    print("COMPLETE", seed, repeat, result["max_chain"], result["premature"], result["game_over"], flush=True)


def cohort_row(value, manifest):
    if value["manifest_sha256"] != manifest["sha256"]:
        raise ValueError("run belongs to another declaration")
    if digest(value["semantic"]) != value["semantic_digest"]:
        raise ValueError("trajectory checksum mismatch")
    if (value["placements"] != len(value["chains"])
            or value["max_chain"] != max(value["chains"], default=0)
            or value["premature"] != sum(0 < n < 10 for n in value["chains"])):
        raise ValueError("summary differs from actual chains")
    result = {key: value[key] for key in ("seed", "repeat", "termination", "placements", "game_over", "max_chain", "premature")}
    # Include frozen batch choices and every adoption, not wall-clock telemetry.
    result["semantic_digest"] = digest({"trajectory": value["semantic"],
        "batches": [row["selection"]["batch_digest"] for row in value["ledger"]]})
    result["decision_seconds"] = [row["seconds"] for row in value["rows"]]
    return result


def finalize(output):
    manifest = load_manifest(output)
    rows, artifacts, errors = [], {}, []
    for path in sorted(output.glob("seed-*-repeat-*.json.gz")):
        value = read(path)
        if path.name != f"seed-{value['seed']}-repeat-{value['repeat']}.json.gz":
            raise ValueError("filename/identity mismatch")
        rows.append(cohort_row(value, manifest))
        artifacts[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        errors.extend(value["errors"])
    report = evaluate(rows=rows, contract=manifest["config"])
    failures = [read(path) for path in sorted(output.glob("failure-*.json"))]
    report.update(manifest_sha256=manifest["sha256"], artifacts=artifacts, scheduler_errors=errors,
                  failed_workers=failures,
                  source_changed_since_declaration=source_identity() != manifest["source"],
                  evidence_note="This cohort establishes only the safe-build measurements. G0/G1 and threat fixture evidence are not inferred.")
    write_new(output / "report.json", report)
    print(json.dumps({"safe_build": report["safe_build"], "G2": report["gates"]["G2"],
                      "observed_quality": report["observed_quality"]}, indent=2))
    return report


def run_all(output):
    load_manifest(output, execution=True)
    for seed in SEEDS:
        for repeat in REPEATS:
            process = subprocess.run([sys.executable, "-m", "eval.nextgen_safe_build_gate", "--output", str(output),
                                      "worker", "--seed", str(seed), "--repeat", str(repeat)], cwd=ROOT, check=False)
            if process.returncode:
                # Preserve the failed identity; still measure independent runs.
                print("FAILED", seed, repeat, process.returncode, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", choices=("init", "worker", "run-all", "finalize"))
    parser.add_argument("--seed", type=int, choices=SEEDS)
    parser.add_argument("--repeat", type=int, choices=REPEATS)
    args = parser.parse_args()
    if args.command == "worker" and (args.seed is None or args.repeat is None):
        parser.error("worker requires seed and repeat")
    {"init": lambda: initialize(args.output), "worker": lambda: worker(args.output, args.seed, args.repeat),
     "run-all": lambda: run_all(args.output), "finalize": lambda: finalize(args.output)}[args.command]()


if __name__ == "__main__":
    main()
