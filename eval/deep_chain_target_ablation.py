"""PUYO-231 fixed-budget target ablation; PUYO-204 remains read-only.

Each identity runs in a fresh process, sequentially (six native scenario threads).
The immutable declaration binds every condition to one clean source/build/host.
Finalization and verification need neither the measured host nor a native rebuild.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from eval import deep_chain_builder_benchmark as baseline
from train.artifacts import file_sha256

TICKET = "PUYO-231"
SCHEMA = "puyo.deep_chain_target_ablation.v1"
TARGETS = (6, 8, 10, 12)
SEEDS = tuple(range(123, 153))
REPEATS = (1, 2)
QUALITY_FLOOR = 10
MAX_STEPS = 40
DEFAULT_OUTPUT_DIR = Path("docs/benchmarks/puyo-231-target-ablation")
CONFIG_PATHS = (
    "train/config/deep_chain_builder.yaml",
    "train/config/deep_chain_backend.yaml",
    "train/config/v1_7_chain_structure.yaml",
)


def digest(value: Any) -> str:
    return baseline._stable_digest(value, prefix=SCHEMA)


def identities() -> list[dict[str, int | str]]:
    # Interleave conditions within each seed/repeat to reduce temporal drift.
    return [
        {
            "run_id": f"target-{target:02d}/{baseline.run_identity(seed, repeat)}",
            "target": target,
            "seed": seed,
            "repeat": repeat,
        }
        for seed in SEEDS
        for repeat in REPEATS
        for target in TARGETS
    ]


def configuration() -> dict[str, Any]:
    config = baseline.load_deep_chain_builder_config()
    profile = config.profiles["reference"].to_dict()
    expected = {"depth": 16, "width": 250, "scenarios": 6, "max_expanded_nodes": 600000}
    if any(profile.get(k) != v for k, v in expected.items()):
        raise ValueError("reference search budget changed")
    contract = config.benchmark
    if (
        contract.seed_start,
        contract.seed_count,
        contract.repeats_per_seed,
        contract.max_steps,
    ) != (123, 30, 2, 40):
        raise ValueError("fixed benchmark identities changed")
    return {
        "profile": profile,
        "backend": "native",
        "execution_mode": "scenario-6",
        "process_mode": "fresh_process_per_identity_sequential",
        "environment": "safe_no_threat",
        "max_steps": MAX_STEPS,
        "quality_floor": QUALITY_FLOOR,
        "maximum_decision_p95_seconds": 1.0,
        "minimum_mean_maximum_actual_chain": 10.0,
        "maximum_premature_fires": 0,
        "maximum_game_overs": 0,
        "parity_contract_version": baseline.PARITY_CONTRACT_VERSION,
        "configuration_sha256": {
            path: file_sha256(baseline.REPO_ROOT / path) for path in CONFIG_PATHS
        },
    }


def load_manifest(root: Path) -> dict[str, Any]:
    manifest = baseline._read_json(root / "experiment_manifest.json")
    payload = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    if manifest.get("manifest_sha256") != digest(payload):
        raise ValueError("experiment manifest checksum mismatch")
    if manifest.get("schema_version") != SCHEMA or manifest.get("ticket") != TICKET:
        raise ValueError("unsupported experiment manifest")
    if (
        manifest.get("targets") != list(TARGETS)
        or manifest.get("identities") != identities()
    ):
        raise ValueError(
            "experiment identities must be the fixed 240 target/seed/repeat combinations"
        )
    return manifest


def initialize(root: Path) -> dict[str, Any]:
    baseline._protect_historical_output(root)
    provenance = baseline._native_build_provenance(strict=True)
    common = configuration()
    path = root / "experiment_manifest.json"
    if path.exists():
        manifest = load_manifest(root)
        old = manifest["build_provenance"]
        for key in (
            "evaluated_commit",
            "host",
            "capabilities",
            "wheels",
            "configuration",
        ):
            if old[key] != provenance[key]:
                raise ValueError(f"resume provenance mismatch: {key}")
        if manifest["common_configuration"] != common:
            raise ValueError("resume configuration mismatch")
        return manifest
    if root.exists() and any(root.iterdir()):
        raise ValueError("new experiment requires an empty output directory")
    manifest = {
        "schema_version": SCHEMA,
        "ticket": TICKET,
        "targets": list(TARGETS),
        "identities": identities(),
        "run_count": 240,
        "unique_seed_count": 30,
        "created_at_utc": baseline.utc_timestamp(),
        "build_provenance": provenance,
        "common_configuration": common,
        "common_configuration_sha256": digest(common),
        "condition_configuration_sha256": {
            str(t): digest({"common": common, "target_chain_count": t}) for t in TARGETS
        },
        "historical_target6": str(baseline.DEFAULT_OUTPUT_DIR),
    }
    manifest["manifest_sha256"] = digest(manifest)
    baseline._write_json(path, manifest)
    return manifest


def run_path(root: Path, identity: dict) -> Path:
    return root / (str(identity["run_id"]) + ".json.gz")


def write_run(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = gzip.compress(
        (json.dumps(payload, sort_keys=True, allow_nan=False) + "\n").encode(), mtime=0
    )
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = stream.name
    os.replace(temporary, path)


def validate_run(run: dict, identity: dict, manifest: dict) -> None:
    expected = {
        "schema_version": SCHEMA,
        "ticket": TICKET,
        "run_id": identity["run_id"],
        "seed": identity["seed"],
        "repeat": identity["repeat"],
        "target_chain_count": identity["target"],
        "quality_floor": QUALITY_FLOOR,
        "manifest_sha256": manifest["manifest_sha256"],
        "evaluated_commit": manifest["build_provenance"]["evaluated_commit"],
        "profile": "reference",
        "backend": "native",
        "max_steps": MAX_STEPS,
        "parity_contract_version": baseline.PARITY_CONTRACT_VERSION,
        "configuration_sha256": manifest["common_configuration"][
            "configuration_sha256"
        ][CONFIG_PATHS[0]],
        "backend_configuration_sha256": manifest["common_configuration"][
            "configuration_sha256"
        ][CONFIG_PATHS[1]],
    }
    for key, value in expected.items():
        if run.get(key) != value:
            raise ValueError(f"{identity['run_id']}: {key} mismatch")
    records = [r for r in run["records"] if "action" in r]
    fires = [
        r["actual_result"]["chain_count"]
        for r in records
        if r["actual_result"]["chain_count"] > 0
    ]
    if run["completed_turns"] != len(records) or len(records) > MAX_STEPS:
        raise ValueError("completed placement count mismatch")
    if run["actual_fire_chain_counts"] != fires or run[
        "maximum_actual_fire_chain_count"
    ] != max(fires, default=0):
        raise ValueError("actual chain summary mismatch")
    if run["premature_fire_count"] != sum(1 <= c < QUALITY_FLOOR for c in fires):
        raise ValueError("premature count must use quality floor 10")
    if run["simulator_parity_mismatch_count"] != sum(
        not r["parity"]["passed"] for r in records
    ):
        raise ValueError("parity summary mismatch")
    if run["fully_evaluated"] != (
        run["termination_reason"] in ("turn_limit", "game_over")
    ):
        raise ValueError("completion status mismatch")
    if run["termination_reason"] == "turn_limit" and len(records) != MAX_STEPS:
        raise ValueError("turn limit without 40 placements")
    for record in records:
        if (
            record.get("target_chain_count") != identity["target"]
            or record["plan"]["objective"].get("minimum_chain_count")
            != identity["target"]
        ):
            raise ValueError("policy/plan target propagation mismatch")
        if record["parity"]["contract_version"] != baseline.PARITY_CONTRACT_VERSION:
            raise ValueError("decision parity contract mismatch")
        if record["search"]["counters"].get("expanded_nodes", 0) > 600000:
            raise ValueError("expanded node budget exceeded")


def load_runs(root: Path, manifest: dict, target: int | None = None) -> list[dict]:
    runs = []
    expected_paths = {run_path(root, i) for i in identities()}
    if set(root.glob("target-*/*.json.gz")) - expected_paths:
        raise ValueError("unexpected run identity")
    for identity in identities():
        if target is not None and identity["target"] != target:
            continue
        path = run_path(root, identity)
        if path.exists():
            run = baseline._read_json(path)
            validate_run(run, identity, manifest)
            runs.append(run)
    return runs


def execute_identity(root: Path, target: int, seed: int, repeat: int) -> dict:
    manifest = initialize(root)
    identity = next(
        i
        for i in identities()
        if (i["target"], i["seed"], i["repeat"]) == (target, seed, repeat)
    )
    path = run_path(root, identity)
    if path.exists():
        raise ValueError("refusing to overwrite an existing identity")
    run = baseline.run_benchmark_run(
        seed=seed,
        repeat=repeat,
        profile="reference",
        max_steps=MAX_STEPS,
        backend="native",
        target_chain_count=target,
    )
    run.update(
        schema_version=SCHEMA,
        ticket=TICKET,
        run_id=identity["run_id"],
        quality_floor=QUALITY_FLOOR,
        manifest_sha256=manifest["manifest_sha256"],
    )
    validate_run(run, identity, manifest)
    write_run(path, run)
    return {
        "run_id": run["run_id"],
        "maximum_chain": run["maximum_actual_fire_chain_count"],
        "premature": run["premature_fire_count"],
        "termination": run["termination_reason"],
        "seconds": run["elapsed_seconds"],
    }


def diagnostic(root: Path, target: int) -> dict:
    """Cold + warm same-root decisions, separate from closed-loop trajectories."""
    manifest = initialize(root)
    path = root / f"diagnostic-target-{target:02d}.json"
    if path.exists():
        raise ValueError("refusing to overwrite existing diagnostic")
    samples = []
    observation, info = baseline._initial_observation_and_info(123, max_steps=MAX_STEPS)
    policy = None
    for index, label in enumerate(("cold", "warm", "private_counterfactual")):
        obs, details = dict(observation), dict(info)
        if index == 2:
            obs["private_future_queue"] = "private-future-counterfactual"
            details.update(
                simulator="private-simulator-counterfactual",
                future_queue="private-future-counterfactual",
            )
        started = time.perf_counter()
        if policy is None:
            policy = baseline._policy_factory(123, "reference", "native", target)
        else:
            policy.reset()
        action = int(policy.select_action(obs, details))
        elapsed = time.perf_counter() - started
        d = policy.tactical_diagnostics
        plan = baseline._plan_summary(d["plan"])
        samples.append(
            {
                "label": label,
                "action": action,
                "elapsed_seconds": elapsed,
                "root_board": baseline._json_ready(observation["board"]),
                "target_chain_count": d["target_chain_count"],
                "plan": plan,
                "search": baseline._json_ready(d["search"]),
                "backend": baseline._json_ready(d["backend"]),
                "flow": baseline._json_ready(d["decision_trace"]),
                "fallback": d["fallback"],
                "scenario_accounting": baseline._scenario_accounting(d),
                "decision_digest": digest(
                    {
                        "action": action,
                        "plan": plan,
                        "search_digest": d["search"]["deterministic_digest"],
                    }
                ),
            }
        )
    payload = {
        "schema_version": SCHEMA,
        "manifest_sha256": manifest["manifest_sha256"],
        "target_chain_count": target,
        "seed": 123,
        "kind": "same_root_cold_warm_not_trajectory_quality",
        "samples": samples,
        "future_boundary": baseline.audit_future_isolation(SEEDS, max_steps=MAX_STEPS),
        "counterfactual_matches": samples[1]["decision_digest"]
        == samples[2]["decision_digest"],
        "cold_warm_matches": samples[0]["decision_digest"]
        == samples[1]["decision_digest"],
    }
    baseline._write_json(path, payload)
    return {
        "target": target,
        "cold_warm_matches": payload["cold_warm_matches"],
        "counterfactual_matches": payload["counterfactual_matches"],
    }


def run_pending(root: Path, target: int | None, max_runs: int | None) -> dict:
    initialize(root)
    manifest = load_manifest(root)
    load_runs(root, manifest)
    completed = 0
    for identity in identities():
        if target is not None and identity["target"] != target:
            continue
        if run_path(root, identity).exists():
            continue
        if max_runs is not None and completed >= max_runs:
            break
        subprocess.run(
            [
                sys.executable,
                "-m",
                "eval.deep_chain_target_ablation",
                "worker",
                "--output-dir",
                str(root),
                "--target",
                str(identity["target"]),
                "--seed",
                str(identity["seed"]),
                "--repeat",
                str(identity["repeat"]),
            ],
            check=True,
            cwd=baseline.REPO_ROOT,
        )
        completed += 1
    return finalize(root, target)


def distribution(values: list[int | float]) -> dict:
    return {
        "count": len(values),
        "mean": baseline._mean(values),
        "p50": baseline.percentile(values, 0.5),
        "p95": baseline.percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def decision_errors(run: dict) -> list[dict]:
    return [
        {"placement": r["turn"] + 1, **r["decision_error"]}
        for r in run["records"]
        if "decision_error" in r
    ]


def run_metrics(run: dict) -> dict:
    records = [r for r in run["records"] if "action" in r]
    fires = [r for r in records if r["actual_result"]["chain_count"] > 0]
    prediction = []
    for record in records:
        future = record["plan"]["prediction_summary"].get("maximum_chain_count")
        # Compare a forecast to actual fires inside its original plan horizon;
        # replanning makes this a forecast-followthrough diagnostic, not parity.
        horizon = len(record["plan"]["steps"])
        window = [
            r for r in records if record["turn"] <= r["turn"] < record["turn"] + horizon
        ]
        actual = max((r["actual_result"]["chain_count"] for r in window), default=0)
        prediction.append(
            {
                "placement": record["turn"] + 1,
                "predicted_maximum": future,
                "actual_maximum_in_plan_horizon": actual,
                "horizon_complete": len(window) == horizon,
                "predicted_minus_actual": None if future is None else future - actual,
            }
        )
    return {
        "run_id": run["run_id"],
        "seed": run["seed"],
        "repeat": run["repeat"],
        "fully_evaluated": run["fully_evaluated"],
        "termination_reason": run["termination_reason"],
        "observed_maximum_actual_chain": run["maximum_actual_fire_chain_count"],
        "maximum_actual_chain": run["maximum_actual_fire_chain_count"]
        if run["fully_evaluated"]
        else None,
        "quality_floor_reached": (
            run["maximum_actual_fire_chain_count"] >= QUALITY_FLOOR
        )
        if run["fully_evaluated"]
        else None,
        "internal_target_reached": (
            run["maximum_actual_fire_chain_count"] >= run["target_chain_count"]
        )
        if run["fully_evaluated"]
        else None,
        "decision_errors": decision_errors(run),
        "first_fire_placement": fires[0]["turn"] + 1 if fires else None,
        "first_quality_fire_placement": next(
            (
                r["turn"] + 1
                for r in fires
                if r["actual_result"]["chain_count"] >= QUALITY_FLOOR
            ),
            None,
        ),
        "observed_no_fire": not fires,
        "no_fire": (not fires) if run["fully_evaluated"] else None,
        "game_over": run["game_over"] if run["fully_evaluated"] else None,
        "premature_fires": [
            {
                "placement": r["turn"] + 1,
                "chain": r["actual_result"]["chain_count"],
                "selection_reason": r["selection"]["selection_reason"],
                "selected_score": r["selection"]["selected_score"],
            }
            for r in fires
            if r["actual_result"]["chain_count"] < QUALITY_FLOOR
        ],
        "forecast_followthrough": prediction,
    }


def summarize_target(root: Path, target: int, manifest: dict, runs: list[dict]) -> dict:
    metrics = [run_metrics(run) for run in runs]
    # Repeat 1 is the prespecified quality estimate; repeat 2 checks determinism.
    unique = [m for m in metrics if m["repeat"] == 1 and m["fully_evaluated"]]
    all_records = [r for run in runs for r in run["records"] if "action" in r]
    complete = len(runs) == 60 and all(r["fully_evaluated"] for r in runs)
    mean = baseline._mean([m["maximum_actual_chain"] for m in unique])
    latency = baseline._aggregate_latency(runs)
    determinism = baseline._determinism_summary(runs, seeds=SEEDS)
    for pair in determinism["records"]:
        pair["observed_digest_matches"] = pair["matches"]
        pair_runs = [run for run in runs if run["seed"] == pair["seed"]]
        pair["complete_pair"] = len(pair_runs) == 2 and all(
            run["fully_evaluated"] for run in pair_runs
        )
        pair["matches"] = pair["observed_digest_matches"] and pair["complete_pair"]
    determinism["complete_pair_count"] = sum(
        p["complete_pair"] for p in determinism["records"]
    )
    determinism["matching_pair_count"] = sum(
        p["matches"] for p in determinism["records"]
    )
    determinism["passed"] = all(p["matches"] for p in determinism["records"])
    successful_records = [
        {**run, "records": [r for r in run["records"] if "action" in r]} for run in runs
    ]
    search = baseline._aggregate_search(successful_records)
    error_count = sum(len(decision_errors(run)) for run in runs)
    accounting = search["scenario_accounting"]
    accounting["unverified_decision_error_count"] = error_count
    accounting["observed_passed"] = accounting["passed"]
    accounting["passed"] = accounting["passed"] and error_count == 0
    premature = sum(r["premature_fire_count"] for r in runs)
    game_over = sum(r["game_over"] for r in runs)
    parity = sum(r["simulator_parity_mismatch_count"] for r in runs)
    fallback = sum(r["fallback_count"] for r in runs)
    diagnostics_path = root / f"diagnostic-target-{target:02d}.json"
    diagnostics = (
        baseline._read_json(diagnostics_path) if diagnostics_path.exists() else None
    )
    if (
        diagnostics
        and diagnostics.get("manifest_sha256") != manifest["manifest_sha256"]
    ):
        raise ValueError("diagnostic provenance mismatch")
    future_passed = (
        diagnostics is not None
        and diagnostics["future_boundary"]["passed"]
        and diagnostics["counterfactual_matches"]
    )
    gates = {
        "coverage": complete,
        "quality": complete
        and mean is not None
        and mean >= 10
        and premature == 0
        and game_over == 0,
        "performance": complete
        and latency["p95_seconds"] is not None
        and latency["p95_seconds"] <= 1.0,
        "determinism": complete and determinism["passed"],
        "parity": complete and bool(all_records) and parity == 0,
        "fallback": complete and bool(all_records) and fallback == 0,
        "scenario_accounting": complete and search["scenario_accounting"]["passed"],
        "future_isolation": bool(future_passed),
    }
    by_id = {r["run_id"]: r for r in runs}
    coverage = [
        {
            **i,
            "decision_errors": decision_errors(by_id[i["run_id"]])
            if i["run_id"] in by_id
            else [],
            "status": (
                "complete" if by_id[i["run_id"]]["fully_evaluated"] else "incomplete"
            )
            if i["run_id"] in by_id
            else "pending",
            "reason": by_id[i["run_id"]]["termination_reason"]
            if i["run_id"] in by_id
            else "identity_not_executed; excluded_from_quality_and_latency_estimates",
        }
        for i in identities()
        if i["target"] == target
    ]
    return {
        "schema_version": SCHEMA,
        "manifest_sha256": manifest["manifest_sha256"],
        "target_chain_count": target,
        "quality_floor": QUALITY_FLOOR,
        "coverage": coverage,
        "executed_runs": len(runs),
        "fully_evaluated_runs": sum(r["fully_evaluated"] for r in runs),
        "incomplete_runs": sum(not r["fully_evaluated"] for r in runs),
        "event_count_scope": "observed events across all repeats, including partial runs; partial counts are lower bounds",
        "quality_distribution_scope": "fully evaluated trajectories only; incomplete outcomes are null, with partial observations retained separately",
        "unique_seed_estimate": {
            "method": "prespecified_repeat_1_only; repeat_2_is_not_an_independent_seed",
            "denominator": len(unique),
            "maximum_actual_chain_distribution": dict(
                sorted(Counter(str(m["maximum_actual_chain"]) for m in unique).items())
            ),
            "mean_maximum_actual_chain": mean,
            "quality_floor_reached_count": sum(
                m["quality_floor_reached"] for m in unique
            ),
            "quality_floor_reached_rate": sum(
                m["quality_floor_reached"] for m in unique
            )
            / len(unique)
            if unique
            else None,
            "internal_target_reached_count": sum(
                m["internal_target_reached"] for m in unique
            ),
            "no_fire_count": sum(m["no_fire"] for m in unique),
            "first_fire_placement": distribution(
                [
                    m["first_fire_placement"]
                    for m in unique
                    if m["first_fire_placement"] is not None
                ]
            ),
            "first_quality_fire_placement": distribution(
                [
                    m["first_quality_fire_placement"]
                    for m in unique
                    if m["first_quality_fire_placement"] is not None
                ]
            ),
        },
        "run_maximum_actual_chain_distribution": dict(
            sorted(
                Counter(
                    str(m["maximum_actual_chain"])
                    for m in metrics
                    if m["fully_evaluated"]
                ).items()
            )
        ),
        "premature_fire_count_all_repeats": premature,
        "game_over_count_all_repeats": game_over,
        "no_fire_run_count": sum(m["no_fire"] is True for m in metrics),
        "premature_reasons": dict(
            Counter(
                p["selection_reason"] for m in metrics for p in m["premature_fires"]
            )
        ),
        "parity_mismatches": parity,
        "fallback_count": fallback,
        "latency": latency,
        "decision_error_latency_seconds": distribution(
            [
                r["elapsed_seconds"]
                for run in runs
                for r in run["records"]
                if "decision_error" in r
            ]
        ),
        "trajectory_first_decision_seconds": distribution(
            [
                r["records"][0]["elapsed_seconds"]
                for r in runs
                if r["records"] and "action" in r["records"][0]
            ]
        ),
        "trajectory_later_decision_seconds": distribution(
            [
                record["elapsed_seconds"]
                for r in runs
                for record in r["records"][1:]
                if "action" in record
            ]
        ),
        "search": search,
        "determinism": determinism,
        "same_root_diagnostic": diagnostics,
        "gates": gates,
        "accepted": all(gates.values()),
        "runs": metrics,
        "failure_seed_135_138": [m for m in metrics if m["seed"] in (135, 138)],
    }


def summaries(root: Path, target: int | None = None) -> dict[str, dict]:
    manifest = load_manifest(root)
    return {
        str(t): summarize_target(root, t, manifest, load_runs(root, manifest, t))
        for t in TARGETS
        if target is None or t == target
    }


def comparison(summaries: dict[str, dict]) -> dict:
    pairs = []
    for seed in SEEDS:
        rows = {}
        for target, summary in summaries.items():
            metrics = [m for m in summary["runs"] if m["seed"] == seed]
            rows[target] = [
                {
                    k: m[k]
                    for k in (
                        "repeat",
                        "maximum_actual_chain",
                        "quality_floor_reached",
                        "internal_target_reached",
                        "first_fire_placement",
                        "first_quality_fire_placement",
                        "game_over",
                        "no_fire",
                        "fully_evaluated",
                        "termination_reason",
                        "decision_errors",
                    )
                }
                for m in metrics
            ]
        pairs.append({"seed": seed, "targets": rows})
    return {
        "schema_version": SCHEMA,
        "quality_floor": QUALITY_FLOOR,
        "unique_seed_count": 30,
        "repeats_per_seed": 2,
        "paired_seeds": pairs,
        "target10_paired_differences": paired_differences(summaries),
    }


def paired_differences(summaries: dict[str, dict]) -> dict:
    primary = {
        t: {
            m["seed"]: m
            for m in summary["runs"]
            if m["repeat"] == 1 and m["fully_evaluated"]
        }
        for t, summary in summaries.items()
    }
    result = {}
    for other in ("6", "8", "12"):
        if "10" not in primary or other not in primary:
            continue
        seeds = sorted(primary["10"].keys() & primary[other].keys())
        deltas = [
            primary["10"][seed]["maximum_actual_chain"]
            - primary[other][seed]["maximum_actual_chain"]
            for seed in seeds
        ]
        result[other] = {
            "basis": "target10 minus comparator; completed repeat1 on common seeds only; repeats are not independent samples",
            "common_seed_count": len(seeds),
            "common_seeds": seeds,
            "excluded_seeds": sorted(set(SEEDS) - set(seeds)),
            "mean_maximum_actual_chain_delta": baseline._mean(deltas),
            "quality_floor_reached_rate_delta": baseline._mean(
                [
                    int(primary["10"][seed]["quality_floor_reached"])
                    - int(primary[other][seed]["quality_floor_reached"])
                    for seed in seeds
                ]
            ),
            "target10_better_count": sum(d > 0 for d in deltas),
            "equal_count": sum(d == 0 for d in deltas),
            "target10_worse_count": sum(d < 0 for d in deltas),
        }
    return result


def report(payload: dict[str, dict]) -> str:
    lines = [
        "# PUYO-231 target 固定探索予算比較",
        "",
        "品質の分母は事前指定の repeat 1（最大30固有seed）。repeat 2 は決定論検証用。",
        "欠損・未完了を0やPASSに補完しない。全条件で品質基準10、p95上限1.0秒。",
        "",
        "| target | 完全評価run/60 | 固有seed | 最大実連鎖平均 | 10以上/固有seed | premature（2 repeats） | game over（2 repeats） | p95秒 | 品質 | 性能 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for target, summary in payload.items():
        q = summary["unique_seed_estimate"]
        mean = q["mean_maximum_actual_chain"]
        p95 = summary["latency"]["p95_seconds"]
        lines.append(
            f"| {target} | {summary['fully_evaluated_runs']} | {q['denominator']} | {mean if mean is None else round(mean, 6)} | {q['quality_floor_reached_count']}/{q['denominator']} | {summary['premature_fire_count_all_repeats']} | {summary['game_over_count_all_repeats']} | {p95 if p95 is None else round(p95, 6)} | {'PASS' if summary['gates']['quality'] else 'FAIL / 未完了'} | {'PASS' if summary['gates']['performance'] else 'FAIL / 未完了'} |"
        )
    lines.extend(
        [
            "",
            "詳細は条件別 summary.json、比較は paired_comparison.json、raw は target-NN/*.json.gz。",
            "same_root_diagnostic は同一初期盤面の cold/warm 診断。trajectory の phase/RSS/node 分布とは分離する。",
            "forecast_followthrough は元planの予測最大と、再計画を伴う同じplacement区間の実発火最大との差。horizon_complete=false は観測打切り。",
            "欠損は条件別 coverage の reason を参照。GUIの通常画面の目視確認は別途人間が行う。",
            "",
        ]
    )
    return "\n".join(lines)


def evidence_paths(
    root: Path, targets: list[str], *, include_shared: bool
) -> list[Path]:
    paths = [root / "experiment_manifest.json"]
    for t in targets:
        paths.extend(sorted((root / f"target-{int(t):02d}").glob("*.json*")))
        diagnostic_path = root / f"diagnostic-target-{int(t):02d}.json"
        if diagnostic_path.exists():
            paths.append(diagnostic_path)
    if include_shared:
        paths.extend([root / "paired_comparison.json", root / "benchmark_report.md"])
        for directory in ("gui", "error_diagnostics"):
            paths.extend(
                path for path in sorted((root / directory).rglob("*")) if path.is_file()
            )
    return paths


def finalize(root: Path, target: int | None = None) -> dict:
    baseline._protect_historical_output(root)
    payload = summaries(root, target)
    for t, summary in payload.items():
        baseline._write_json(root / f"target-{int(t):02d}" / "summary.json", summary)
    if target is None:
        baseline._write_json(root / "paired_comparison.json", comparison(payload))
        (root / "benchmark_report.md").write_text(report(payload))
    paths = evidence_paths(root, list(payload), include_shared=target is None)
    name = (
        "evidence_checksums.json"
        if target is None
        else f"checksums-target-{target:02d}.json"
    )
    baseline._write_json(
        root / name, {str(p.relative_to(root)): file_sha256(p) for p in paths}
    )
    return {
        t: {"executed": s["executed_runs"], "gates": s["gates"]}
        for t, s in payload.items()
    }


def verify(root: Path, target: int | None = None) -> list[str]:
    errors = []
    try:
        name = (
            "evidence_checksums.json"
            if target is None
            else f"checksums-target-{target:02d}.json"
        )
        checksums = baseline._read_json(root / name)
        for name, checksum in checksums.items():
            path = root / name
            if not path.is_file() or file_sha256(path) != checksum:
                errors.append(f"checksum mismatch: {name}")
        payload = summaries(root, target)
        expected_paths = {
            str(p.relative_to(root))
            for p in evidence_paths(root, list(payload), include_shared=target is None)
        }
        if set(checksums) != expected_paths:
            errors.append("checksum index does not cover the exact evidence inputs")
        for t, summary in payload.items():
            if (
                baseline._read_json(root / f"target-{int(t):02d}" / "summary.json")
                != summary
            ):
                errors.append(f"summary mismatch: target {t}")
        if target is None:
            if baseline._read_json(root / "paired_comparison.json") != comparison(
                payload
            ):
                errors.append("paired comparison mismatch")
            if (root / "benchmark_report.md").read_text() != report(payload):
                errors.append("report mismatch")
    except (ValueError, KeyError, OSError, TypeError) as exc:
        errors.append(str(exc))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("init", "run", "worker", "diagnostic", "finalize", "verify")
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--target", type=int, choices=TARGETS)
    parser.add_argument("--seed", type=int, choices=SEEDS)
    parser.add_argument("--repeat", type=int, choices=REPEATS)
    parser.add_argument("--max-runs", type=int)
    args = parser.parse_args()
    if args.max_runs is not None and args.max_runs <= 0:
        parser.error("--max-runs must be positive")
    root = args.output_dir.resolve()
    if args.command == "init":
        result = {"manifest_sha256": initialize(root)["manifest_sha256"]}
    elif args.command == "run":
        result = run_pending(root, args.target, args.max_runs)
    elif args.command == "worker":
        if None in (args.target, args.seed, args.repeat):
            parser.error("worker requires --target, --seed and --repeat")
        result = execute_identity(root, args.target, args.seed, args.repeat)
    elif args.command == "diagnostic":
        if args.target is None:
            parser.error(
                "diagnostic requires --target (one fresh process per condition)"
            )
        result = diagnostic(root, args.target)
    elif args.command == "finalize":
        result = finalize(root, args.target)
    else:
        errors = verify(root, args.target)
        print(
            json.dumps(
                {"evidence_valid": not errors, "errors": errors}, ensure_ascii=False
            )
        )
        return int(bool(errors))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
