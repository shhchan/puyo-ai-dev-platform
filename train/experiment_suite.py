"""Run reproducible seed/scenario experiment suites."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable

import yaml

from agents.flat_ppo import FlatPPOConfig, train_flat_ppo
from agents.manager_ppo import ManagerPPOConfig, train_manager_ppo
from agents.versus_ppo import VersusPPOConfig, train_versus_ppo
from train.artifacts import json_digest, utc_timestamp

SUITE_SCHEMA_VERSION = "puyo.experiment_suite.v1"


@dataclass(frozen=True)
class Scenario:
    name: str
    overrides: dict[str, Any]


@dataclass(frozen=True)
class SuiteDefinition:
    name: str
    trainer: str
    config_path: str
    output_dir: str
    seeds: tuple[int, ...]
    scenarios: tuple[Scenario, ...]
    replicates: int = 1
    overrides: dict[str, Any] | None = None
    metrics: tuple[str, ...] = ()
    max_parallel: int = 1


@dataclass(frozen=True)
class RunSpec:
    suite_name: str
    trainer: str
    run_id: str
    scenario: str
    seed: int
    replicate: int
    config_path: str
    log_dir: str
    overrides: dict[str, Any]


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    scenario: str
    seed: int
    replicate: int
    status: str
    run_dir: str | None
    summary_path: str | None
    metrics: dict[str, float]
    error: str | None = None


TRAINERS: dict[str, tuple[type, Callable[[Any], dict[str, Any]]]] = {
    "flat_ppo": (FlatPPOConfig, train_flat_ppo),
    "versus_ppo": (VersusPPOConfig, train_versus_ppo),
    "manager_ppo": (ManagerPPOConfig, train_manager_ppo),
}


def safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return safe.strip("-") or "run"


def load_mapping(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{source} must contain a mapping")
    return data


def load_suite_definition(path: str | Path) -> SuiteDefinition:
    data = load_mapping(path)
    scenarios = data.get("scenarios") or [{"name": "default", "overrides": {}}]
    parsed_scenarios = []
    for item in scenarios:
        if not isinstance(item, dict):
            raise ValueError("each scenario must be a mapping")
        parsed_scenarios.append(
            Scenario(
                name=str(item["name"]),
                overrides=dict(item.get("overrides") or {}),
            )
        )
    seeds = tuple(int(seed) for seed in data.get("seeds", []))
    if not seeds:
        raise ValueError("suite must define at least one seed")
    return SuiteDefinition(
        name=str(data["name"]),
        trainer=str(data["trainer"]),
        config_path=str(data["config"]),
        output_dir=str(data.get("output_dir", Path(path).with_suffix(""))),
        seeds=seeds,
        scenarios=tuple(parsed_scenarios),
        replicates=max(1, int(data.get("replicates", 1))),
        overrides=dict(data.get("overrides") or {}),
        metrics=tuple(str(metric) for metric in data.get("metrics", [])),
        max_parallel=max(1, int(data.get("max_parallel", 1))),
    )


def build_run_matrix(suite: SuiteDefinition) -> list[RunSpec]:
    if suite.trainer not in TRAINERS:
        valid = ", ".join(sorted(TRAINERS))
        raise ValueError(f"unknown trainer: {suite.trainer}; valid values: {valid}")
    output_dir = Path(suite.output_dir)
    log_dir = output_dir / "runs"
    matrix = []
    for scenario in suite.scenarios:
        scenario_name = safe_name(scenario.name)
        for seed in suite.seeds:
            for replicate in range(1, suite.replicates + 1):
                run_id = safe_name(f"{suite.name}-{scenario_name}-seed{seed}-rep{replicate}")
                overrides = {
                    **dict(suite.overrides or {}),
                    **scenario.overrides,
                    "seed": int(seed),
                }
                run_log_dir = log_dir
                if suite.trainer == "flat_ppo":
                    run_log_dir = log_dir / run_id
                    overrides["log_dir"] = str(run_log_dir)
                    overrides.setdefault("checkpoint_path", str(run_log_dir / "latest.pt"))
                else:
                    overrides.update(
                        {
                            "run_id": run_id,
                            "run_name": run_id,
                            "log_dir": str(log_dir),
                        }
                    )
                matrix.append(
                    RunSpec(
                        suite_name=suite.name,
                        trainer=suite.trainer,
                        run_id=run_id,
                        scenario=scenario.name,
                        seed=int(seed),
                        replicate=replicate,
                        config_path=suite.config_path,
                        log_dir=str(run_log_dir),
                        overrides=overrides,
                    )
                )
    return matrix


def _config_for_run(spec: RunSpec):
    config_class, _ = TRAINERS[spec.trainer]
    values = load_mapping(spec.config_path)
    values.update(spec.overrides)
    valid = {field.name for field in fields(config_class)}
    unknown = sorted(set(values) - valid)
    if unknown:
        raise ValueError(f"unknown config fields for {spec.trainer}: {', '.join(unknown)}")
    return config_class(**values)


def _summary_path(spec: RunSpec) -> Path:
    if spec.trainer == "flat_ppo":
        return Path(spec.log_dir) / "summary.json"
    return Path(spec.log_dir) / spec.run_id / "summary.json"


def _run_dir(spec: RunSpec, result: dict[str, Any] | None = None) -> str | None:
    if result is not None and result.get("run_dir") is not None:
        return str(result["run_dir"])
    if spec.trainer == "flat_ppo":
        return spec.log_dir
    return str(Path(spec.log_dir) / spec.run_id)


def _numeric_metrics(summary: dict[str, Any], selected: tuple[str, ...]) -> dict[str, float]:
    keys = selected or tuple(key for key, value in summary.items() if isinstance(value, (int, float)))
    metrics = {}
    for key in keys:
        value = summary.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[key] = float(value)
    return metrics


def _record_from_summary(spec: RunSpec, summary: dict[str, Any], status: str, metrics: tuple[str, ...]) -> RunRecord:
    return RunRecord(
        run_id=spec.run_id,
        scenario=spec.scenario,
        seed=spec.seed,
        replicate=spec.replicate,
        status=status,
        run_dir=str(summary.get("run_dir") or _run_dir(spec)),
        summary_path=str(_summary_path(spec)),
        metrics=_numeric_metrics(summary, metrics),
    )


def run_one(spec: RunSpec, *, metrics: tuple[str, ...], force: bool = False, dry_run: bool = False) -> RunRecord:
    summary_path = _summary_path(spec)
    if summary_path.exists() and not force:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return _record_from_summary(spec, summary, "skipped", metrics)
    if dry_run:
        return RunRecord(
            run_id=spec.run_id,
            scenario=spec.scenario,
            seed=spec.seed,
            replicate=spec.replicate,
            status="planned",
            run_dir=_run_dir(spec),
            summary_path=str(summary_path),
            metrics={},
        )
    try:
        _, train_fn = TRAINERS[spec.trainer]
        result = train_fn(_config_for_run(spec))
        summary = dict(result)
        if summary_path.exists():
            summary.update(json.loads(summary_path.read_text(encoding="utf-8")))
        else:
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return _record_from_summary(spec, summary, "completed", metrics)
    except Exception as exc:
        return RunRecord(
            run_id=spec.run_id,
            scenario=spec.scenario,
            seed=spec.seed,
            replicate=spec.replicate,
            status="failed",
            run_dir=_run_dir(spec),
            summary_path=str(summary_path),
            metrics={},
            error=f"{type(exc).__name__}: {exc}",
        )


def aggregate_records(records: list[RunRecord]) -> dict[str, Any]:
    completed = [record for record in records if record.status in {"completed", "skipped"}]

    def aggregate(selected: list[RunRecord]) -> dict[str, Any]:
        keys = sorted({key for record in selected for key in record.metrics})
        result = {}
        for key in keys:
            values = [record.metrics[key] for record in selected if key in record.metrics]
            if not values:
                continue
            mean = sum(values) / len(values)
            variance = (
                sum((value - mean) ** 2 for value in values) / (len(values) - 1)
                if len(values) > 1
                else 0.0
            )
            sem = math.sqrt(variance / len(values)) if values else 0.0
            result[key] = {
                "count": len(values),
                "mean": mean,
                "variance": variance,
                "stdev": math.sqrt(variance),
                "ci95_low": mean - 1.96 * sem,
                "ci95_high": mean + 1.96 * sem,
            }
        return result

    by_scenario = {
        scenario: aggregate([record for record in completed if record.scenario == scenario])
        for scenario in sorted({record.scenario for record in completed})
    }
    paired = paired_comparisons(completed)
    return {
        "overall": aggregate(completed),
        "by_scenario": by_scenario,
        "paired": paired,
        "status_counts": {
            status: sum(1 for record in records if record.status == status)
            for status in sorted({record.status for record in records})
        },
    }


def paired_comparisons(records: list[RunRecord]) -> dict[str, Any]:
    scenarios = sorted({record.scenario for record in records})
    if len(scenarios) != 2:
        return {}
    left, right = scenarios
    left_records = {
        (record.seed, record.replicate): record
        for record in records
        if record.scenario == left
    }
    right_records = {
        (record.seed, record.replicate): record
        for record in records
        if record.scenario == right
    }
    pairs = sorted(set(left_records) & set(right_records))
    metrics = sorted(
        {
            key
            for pair in pairs
            for key in set(left_records[pair].metrics) & set(right_records[pair].metrics)
        }
    )
    result = {}
    for metric in metrics:
        diffs = [
            left_records[pair].metrics[metric] - right_records[pair].metrics[metric]
            for pair in pairs
            if metric in left_records[pair].metrics and metric in right_records[pair].metrics
        ]
        if not diffs:
            continue
        mean = sum(diffs) / len(diffs)
        variance = (
            sum((value - mean) ** 2 for value in diffs) / (len(diffs) - 1)
            if len(diffs) > 1
            else 0.0
        )
        sem = math.sqrt(variance / len(diffs)) if diffs else 0.0
        result[metric] = {
            "left": left,
            "right": right,
            "count": len(diffs),
            "mean_diff": mean,
            "ci95_low": mean - 1.96 * sem,
            "ci95_high": mean + 1.96 * sem,
        }
    return result


def write_outputs(
    suite: SuiteDefinition,
    matrix: list[RunSpec],
    records: list[RunRecord],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    output_dir = Path(suite.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    aggregate = aggregate_records(records)
    manifest = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "suite": {
            **asdict(suite),
            "digest": json_digest(asdict(suite)),
            "dry_run": bool(dry_run),
        },
        "matrix": [asdict(spec) for spec in matrix],
        "records": [asdict(record) for record in records],
        "aggregate": aggregate,
    }
    (output_dir / "suite_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "runs.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["run_id", "scenario", "seed", "replicate", "status", "run_dir", "summary_path", "error"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({key: getattr(record, key) for key in fieldnames})
    (output_dir / "summary.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def run_suite(path: str | Path, *, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    suite = load_suite_definition(path)
    matrix = build_run_matrix(suite)
    if suite.max_parallel == 1 or dry_run:
        records = [run_one(spec, metrics=suite.metrics, force=force, dry_run=dry_run) for spec in matrix]
    else:
        records = []
        with ProcessPoolExecutor(max_workers=suite.max_parallel) as executor:
            futures = {
                executor.submit(run_one, spec, metrics=suite.metrics, force=force, dry_run=False): spec
                for spec in matrix
            }
            for future in as_completed(futures):
                records.append(future.result())
        order = {spec.run_id: index for index, spec in enumerate(matrix)}
        records.sort(key=lambda record: order[record.run_id])
    return write_outputs(suite, matrix, records, dry_run=dry_run)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a reproducible training experiment suite.")
    parser.add_argument("--suite", required=True, help="YAML suite definition path.")
    parser.add_argument("--force", action="store_true", help="Re-run completed runs.")
    parser.add_argument("--dry-run", action="store_true", help="Only write the planned matrix.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    manifest = run_suite(args.suite, force=args.force, dry_run=args.dry_run)
    output_dir = manifest["suite"]["output_dir"]
    print(f"suite: {manifest['suite']['name']}")
    print(f"output_dir: {output_dir}")
    print(f"runs: {len(manifest['records'])}")
    print(f"summary: {Path(output_dir) / 'summary.json'}")
    print(f"manifest: {Path(output_dir) / 'suite_manifest.json'}")


if __name__ == "__main__":
    main()
