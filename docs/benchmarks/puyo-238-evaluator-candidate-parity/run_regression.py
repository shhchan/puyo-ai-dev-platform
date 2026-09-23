"""PUYO-238's 2 fixed identities; reuse PUYO-231 metrics without changing its raw.

Run with PYTHONPATH=. python <this-file> run|finalize|verify OUTPUT_DIRECTORY.
Every measured identity is a sequential, fresh process. The output directory must
be new; resume checks commit, release wheel, host, and all configuration hashes.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation
from train.artifacts import file_sha256

SOURCE = Path("docs/benchmarks/puyo-231-target-ablation/experiment_manifest.json")
IDENTITIES = [
    {
        "target": target,
        "seed": seed,
        "repeat": repeat,
        "run_id": f"target-{target:02d}/seed-{seed:03d}-repeat-{repeat:02d}",
    }
    for seed, target in ((146, 12),)
    for repeat in (1, 2)
]


def initialize(root):
    provenance = baseline._native_build_provenance(strict=True)
    common = ablation.configuration()
    source = baseline._read_json(SOURCE)
    if common != source["common_configuration"]:
        raise ValueError("PUYO-231 configuration changed")
    path = root / "experiment_manifest.json"
    if path.exists():
        manifest = load_manifest(root)
        for key in (
            "evaluated_commit",
            "host",
            "capabilities",
            "wheels",
            "configuration",
        ):
            if manifest["build_provenance"][key] != provenance[key]:
                raise ValueError(f"resume provenance mismatch: {key}")
        if manifest["common_configuration"] != common:
            raise ValueError("resume configuration mismatch")
        return manifest
    if root.exists() and any(root.iterdir()):
        raise ValueError("new regression requires an empty output directory")
    manifest = {
        "schema_version": "puyo.evaluator_candidate_regression.v1",
        "ticket": "PUYO-238",
        "identities": IDENTITIES,
        "source_manifest": str(SOURCE),
        "source_manifest_sha256": file_sha256(SOURCE),
        "source_evaluated_commit": source["build_provenance"]["evaluated_commit"],
        "build_provenance": provenance,
        "common_configuration": common,
        "common_configuration_sha256": ablation.digest(common),
        "scope": "2 failure regressions; excludes full baseline adoption",
    }
    manifest["manifest_sha256"] = ablation.digest(manifest)
    baseline._write_json(path, manifest)
    return manifest


def load_manifest(root):
    manifest = baseline._read_json(root / "experiment_manifest.json")
    if manifest["identities"] != IDENTITIES or manifest["ticket"] != "PUYO-238":
        raise ValueError("regression identities changed")
    if manifest["manifest_sha256"] != ablation.digest(
        {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    ):
        raise ValueError("manifest checksum mismatch")
    return manifest


def validate(run, identity, manifest):
    # Raw uses the existing ablation schema, plus a separate regression ticket.
    # Reuse its placement, target, quality-floor, budget, parity and lineage checks.
    ablation.validate_run(run, identity, manifest)
    if run.get("regression_ticket") != "PUYO-238":
        raise ValueError("wrong regression ticket")
    for record in run["records"]:
        if "action" not in record:
            continue
        provenance = record["search"]["backend"]["provenance"]
        if (
            provenance["source_revision"] != run["evaluated_commit"]
            or provenance["build_profile"] != "release"
            or provenance["thread_mode"] != "scenario-6"
            or provenance["thread_count"] != 6
        ):
            raise ValueError("decision native provenance mismatch")


def worker(root, index):
    manifest = initialize(root)
    identity = IDENTITIES[index]
    path = ablation.run_path(root, identity)
    if path.exists():
        raise ValueError("refusing to overwrite existing raw")
    run = baseline.run_benchmark_run(
        seed=identity["seed"],
        repeat=identity["repeat"],
        profile="reference",
        max_steps=40,
        backend="native",
        target_chain_count=identity["target"],
    )
    run.update(
        schema_version=ablation.SCHEMA,
        ticket=ablation.TICKET,
        regression_ticket="PUYO-238",
        run_id=identity["run_id"],
        quality_floor=10,
        manifest_sha256=manifest["manifest_sha256"],
    )
    validate(run, identity, manifest)
    ablation.write_run(path, run)
    print(
        identity["run_id"],
        run["termination_reason"],
        run["completed_turns"],
        flush=True,
    )


def summarize(root):
    manifest = load_manifest(root)
    if file_sha256(SOURCE) != manifest["source_manifest_sha256"]:
        raise ValueError("historical manifest changed")
    runs = []
    checksums = {}
    for identity in IDENTITIES:
        path = ablation.run_path(root, identity)
        run = baseline._read_json(path)
        validate(run, identity, manifest)
        runs.append(run)
        checksums[str(path.relative_to(root))] = file_sha256(path)
    targets = {}
    for target in (12,):
        selected = [r for r in runs if r["target_chain_count"] == target]
        records = [
            record for r in selected for record in r["records"] if "action" in record
        ]
        determinism = baseline._determinism_summary(
            selected, seeds=sorted({r["seed"] for r in selected})
        )
        complete = all(r["fully_evaluated"] for r in selected)
        latency = baseline._aggregate_latency(selected)
        metrics = [ablation.run_metrics(r) for r in selected]
        targets[str(target)] = {
            "run_count": len(selected),
            "completed_runs": sum(r["fully_evaluated"] for r in selected),
            "decisions": len(records),
            "errors": [e for r in selected for e in ablation.decision_errors(r)],
            "parity_mismatches": sum(
                r["simulator_parity_mismatch_count"] for r in selected
            ),
            "fallback_count": sum(r["fallback_count"] for r in selected),
            "scenario_accounting": [r["scenario_accounting"] for r in records],
            "determinism": determinism,
            "latency": latency,
            "maximum_expanded_nodes": max(
                r["search"]["counters"]["expanded_nodes"] for r in records
            ),
            "maximum_rss_kib": max(
                r["process_resources"]["peak_rss_kib"] for r in selected
            ),
            "premature_fire_count": sum(r["premature_fire_count"] for r in selected),
            "game_over_count": sum(r["game_over"] for r in selected),
            "fully_evaluated": complete,
            "runs": metrics,
        }
    return {
        "ticket": "PUYO-238",
        "scope": manifest["scope"],
        "manifest_sha256": manifest["manifest_sha256"],
        "raw_sha256": checksums,
        "targets": targets,
        "future_boundary": baseline.audit_future_isolation((146,), max_steps=40),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "worker", "finalize", "verify"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--index", type=int, choices=range(len(IDENTITIES)))
    args = parser.parse_args()
    root = args.output.resolve()
    if args.command == "worker":
        if args.index is None:
            parser.error("worker requires --index")
        worker(root, args.index)
    elif args.command == "run":
        manifest = initialize(root)
        for index, identity in enumerate(IDENTITIES):
            path = ablation.run_path(root, identity)
            if path.exists():
                validate(baseline._read_json(path), identity, manifest)
                continue
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "worker",
                    str(root),
                    "--index",
                    str(index),
                ],
                check=True,
            )
    elif args.command == "finalize":
        baseline._write_json(root / "summary.json", summarize(root))
    else:
        if baseline._read_json(root / "summary.json") != summarize(root):
            raise ValueError("summary/checksums differ from raw")
        print("PUYO-238 evidence verified (not a full baseline quality gate)")


if __name__ == "__main__":
    main()
