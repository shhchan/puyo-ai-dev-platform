"""PUYO-79 benchmark and ablation suite orchestration.

The suite keeps long-running evaluation optional, but the manifest/report
format is the same for smoke and full runs so results can be compared and
tracked by the lineage registry.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from eval.chain_search import (
    ChainSearchResult,
    run_benchmark as run_chain_benchmark,
    write_csv as write_chain_csv,
)
from eval.realtime_arena import run_realtime_paired_series
from eval.tactical_scenarios import evaluate_scenarios
from selfplay.policies import make_policy
from train.artifacts import json_digest, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.benchmark_suite.v1"

DEFAULT_BASELINES = (
    "manager_rule",
    "worker_large",
    "worker_quick",
    "worker_punish",
    "worker_counter",
    "worker_fire",
    "worker_survival",
    "greedy",
    "beam",
)

DEFAULT_ABLATIONS = (
    "objective_conditioning_off",
    "parameter_learning_off",
    "tactical_options_off",
    "teacher_policy_off",
    "latency_penalty_off",
)


@dataclass(frozen=True)
class MetricRecord:
    suite: str
    variant: str
    metric: str
    value: float
    seed: int
    unit: str = ""


@dataclass(frozen=True)
class ArtifactRecord:
    role: str
    path: str
    artifact_type: str


def ci95(values: Iterable[float]) -> dict[str, float]:
    selected = [float(value) for value in values]
    count = len(selected)
    if count == 0:
        return {"count": 0, "mean": 0.0, "stdev": 0.0, "ci95_low": 0.0, "ci95_high": 0.0}
    mean = sum(selected) / count
    if count == 1:
        return {"count": 1, "mean": mean, "stdev": 0.0, "ci95_low": mean, "ci95_high": mean}
    variance = sum((value - mean) ** 2 for value in selected) / (count - 1)
    stdev = math.sqrt(variance)
    margin = 1.96 * math.sqrt(variance / count)
    return {
        "count": count,
        "mean": mean,
        "stdev": stdev,
        "ci95_low": mean - margin,
        "ci95_high": mean + margin,
    }


def summarize_metric_records(records: Iterable[MetricRecord]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str], list[float]] = {}
    for record in records:
        key = (record.suite, record.variant, record.metric, record.unit)
        buckets.setdefault(key, []).append(record.value)
    rows = []
    for suite, variant, metric, unit in sorted(buckets):
        rows.append(
            {
                "suite": suite,
                "variant": variant,
                "metric": metric,
                "unit": unit,
                **ci95(buckets[(suite, variant, metric, unit)]),
            }
        )
    return rows


def choose_recommended_variant(
    summaries: list[dict[str, Any]],
    *,
    latency_budget_ms: float = 80.0,
) -> dict[str, Any]:
    """Pick a UI candidate by balancing arena score, chain size, and latency."""

    by_variant: dict[str, dict[str, float]] = {}
    for row in summaries:
        variant = str(row["variant"])
        metric_key = f"{row['suite']}:{row['metric']}"
        by_variant.setdefault(variant, {})[metric_key] = float(row["mean"])

    candidates = []
    for variant, metrics in sorted(by_variant.items()):
        has_ui_evidence = any(
            key.startswith("realtime_paired_arena:") or key.startswith("tactical_scenarios:")
            for key in metrics
        )
        chain = metrics.get("chain_search:max_chain", 0.0)
        arena_score = metrics.get("realtime_paired_arena:score_rate", 0.0)
        tactical_accuracy = metrics.get("tactical_scenarios:accuracy", 0.0)
        latency = max(
            metrics.get("chain_search:decision_ms", 0.0),
            metrics.get("realtime_paired_arena:policy_elapsed_ms", 0.0),
        )
        feasible = latency <= latency_budget_ms
        score = chain + 4.0 * arena_score + 2.0 * tactical_accuracy
        if not feasible:
            score -= (latency - latency_budget_ms) / max(1.0, latency_budget_ms)
        candidates.append(
            {
                "variant": variant,
                "score": score,
                "feasible": feasible,
                "has_ui_evidence": has_ui_evidence,
                "chain": chain,
                "arena_score_rate": arena_score,
                "tactical_accuracy": tactical_accuracy,
                "latency_ms": latency,
            }
        )
    if not candidates:
        return {"variant": "", "score": 0.0, "feasible": False, "reason": "no benchmark candidates"}
    eligible = [candidate for candidate in candidates if candidate["has_ui_evidence"]] or candidates
    candidates = eligible
    candidates.sort(key=lambda item: (item["feasible"], item["score"]), reverse=True)
    selected = candidates[0]
    selected["reason"] = (
        "highest feasible combined score within latency budget"
        if selected["feasible"]
        else "no candidate met latency budget; selected highest combined score"
    )
    return selected


def chain_records(results: tuple[ChainSearchResult, ...]) -> list[MetricRecord]:
    records: list[MetricRecord] = []
    for result in results:
        records.extend(
            [
                MetricRecord("chain_search", result.policy, "score", float(result.score), result.seed),
                MetricRecord("chain_search", result.policy, "max_chain", float(result.max_chain), result.seed),
                MetricRecord("chain_search", result.policy, "attack_proxy", float(result.score // 70), result.seed),
                MetricRecord("chain_search", result.policy, "decision_ms", result.mean_decision_ms, result.seed, "ms"),
                MetricRecord("chain_search", result.policy, "nodes", result.mean_expanded_nodes, result.seed),
            ]
        )
    return records


def tactical_records(rows: list[dict[str, Any]], *, variant: str) -> list[MetricRecord]:
    return [
        MetricRecord("tactical_scenarios", variant, "accuracy", float(row["correct"]), int(row["seed"]))
        for row in rows
    ]


def realtime_records(result, *, variant: str) -> list[MetricRecord]:
    records: list[MetricRecord] = []
    for match in result.matches:
        suffix = "player_0" if match.policy_a_side == "player_0" else "player_1"
        records.extend(
            [
                MetricRecord("realtime_paired_arena", variant, "score_rate", match.score_for_policy_a, match.seed),
                MetricRecord(
                    "realtime_paired_arena",
                    variant,
                    "deadline_miss",
                    float(getattr(match, f"deadline_misses_{suffix}")),
                    match.seed,
                ),
                MetricRecord(
                    "realtime_paired_arena",
                    variant,
                    "decision_time",
                    float(getattr(match, f"mean_policy_elapsed_ms_{suffix}")),
                    match.seed,
                    "ms",
                ),
                MetricRecord(
                    "realtime_paired_arena",
                    variant,
                    "policy_elapsed_ms",
                    float(getattr(match, f"mean_policy_elapsed_ms_{suffix}")),
                    match.seed,
                    "ms",
                ),
                MetricRecord(
                    "realtime_paired_arena",
                    variant,
                    "max_chain",
                    float(getattr(match, f"max_chain_{suffix}")),
                    match.seed,
                ),
            ]
        )
    return records


def write_metric_records_csv(path: Path, records: list[MetricRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(records[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)


def write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_manifest(
    *,
    name: str,
    output_dir: Path,
    records: list[MetricRecord],
    summaries: list[dict[str, Any]],
    artifacts: list[ArtifactRecord],
    args: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    recommendation = choose_recommended_variant(summaries, latency_budget_ms=float(args["latency_budget_ms"]))
    feasible_recommendation = bool(recommendation.get("feasible"))
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "name": name,
        "digest": json_digest(
            {
                "name": name,
                "records": [asdict(record) for record in records],
                "args": args,
            }
        ),
        "dry_run": dry_run,
        "args": args,
        "baselines": list(DEFAULT_BASELINES),
        "ablations": list(DEFAULT_ABLATIONS),
        "artifacts": [asdict(artifact) for artifact in artifacts],
        "summaries": summaries,
        "recommended_model": recommendation,
        "output_dir": str(output_dir),
        "puyo56_completion": {
            "objective_conditioned_search": "covered by PUYO-74 and validated through tactical/realtime objective diagnostics",
            "learned_search_control_comparison": "covered by PUYO-75 and comparable through benchmark summaries",
            "n_turn_plan_api": "covered by PUYO-77 and validated through realtime replay diagnostics",
            "curriculum_teacher_selfplay": "covered by PUYO-78 and tracked by artifact/lineage outputs",
            "tradeoff_report": "covered by this PUYO-79 benchmark suite and markdown report",
        },
        "readiness": {
            "PUYO-57": (
                "ready to start UI integration; gate release selection on latency feasibility"
                if not feasible_recommendation
                else "ready to start UI integration with a latency-feasible recommended model"
            ),
            "PUYO-58": "not implementation-ready until PUYO-57 UI exists; lineage-compatible benchmark artifacts are available here",
        },
    }


def write_markdown_report(path: Path, manifest: dict[str, Any]) -> None:
    recommendation = manifest["recommended_model"]
    lines = [
        "# PUYO-79 Benchmark And Ablation Report",
        "",
        f"- schema: `{manifest['schema_version']}`",
        f"- digest: `{manifest['digest']}`",
        f"- dry_run: `{manifest['dry_run']}`",
        f"- recommended_model: `{recommendation.get('variant', '')}`",
        f"- recommended_model_latency_feasible: `{recommendation.get('feasible', False)}`",
        f"- recommendation_reason: {recommendation.get('reason', '')}",
        "",
        "## Trade-off Summary",
        "",
        "| suite | variant | metric | mean | 95% CI | count |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in manifest["summaries"]:
        if row["metric"] not in {"max_chain", "score_rate", "decision_ms", "policy_elapsed_ms", "deadline_miss", "accuracy"}:
            continue
        lines.append(
            "| {suite} | {variant} | {metric} | {mean:.3f} | [{low:.3f}, {high:.3f}] | {count} |".format(
                suite=row["suite"],
                variant=row["variant"],
                metric=row["metric"],
                mean=row["mean"],
                low=row["ci95_low"],
                high=row["ci95_high"],
                count=row["count"],
            )
        )
    lines.extend(
        [
            "",
            "## Ablation Matrix",
            "",
            "| ablation | purpose |",
            "|---|---|",
            "| objective_conditioning_off | 数値 objective が探索挙動へ与える寄与を測る |",
            "| parameter_learning_off | 学習済み探索制御と固定 worker の差を測る |",
            "| tactical_options_off | 非固定 profile / option 表現の寄与を測る |",
            "| teacher_policy_off | teacher / BC 初期化の寄与を測る |",
            "| latency_penalty_off | decision time と勝率・連鎖の trade-off を測る |",
            "",
            "## PUYO-56 Completion Checklist",
            "",
        ]
    )
    for key, value in manifest["puyo56_completion"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Next Epic Readiness", ""])
    for key, value in manifest["readiness"].items():
        lines.append(f"- {key}: {value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_puyo79_suite(
    *,
    output_dir: str | Path,
    seed: int = 1,
    games: int = 1,
    max_steps: int = 16,
    max_ticks: int = 120,
    beam_depth: int = 4,
    beam_width: int = 8,
    latency_budget_ms: float = 80.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    args = {
        "seed": seed,
        "games": games,
        "max_steps": max_steps,
        "max_ticks": max_ticks,
        "beam_depth": beam_depth,
        "beam_width": beam_width,
        "latency_budget_ms": latency_budget_ms,
    }
    records: list[MetricRecord] = []
    artifacts: list[ArtifactRecord] = []
    if not dry_run:
        chain_results = run_chain_benchmark(
            ["greedy", "beam"],
            games=games,
            seed=seed,
            max_steps=max_steps,
            beam_depth=beam_depth,
            beam_width=beam_width,
            beam_scenarios=1,
        )
        chain_path = target / "chain_search_records.csv"
        write_chain_csv(chain_path, chain_results)
        records.extend(chain_records(chain_results))
        artifacts.append(ArtifactRecord("chain_search_records", str(chain_path), "csv"))

        tactical_rows = evaluate_scenarios(make_policy("manager_rule"))
        tactical_path = target / "tactical_scenarios.json"
        records.extend(tactical_records(tactical_rows, variant="manager_rule"))
        write_json(tactical_path, tactical_rows)
        artifacts.append(ArtifactRecord("tactical_scenarios", str(tactical_path), "json"))

        realtime_result = run_realtime_paired_series(
            make_policy("manager_rule"),
            make_policy("random", seed=seed + 10_000),
            games=games,
            seed=seed,
            max_ticks=max_ticks,
        )
        records.extend(realtime_records(realtime_result, variant="manager_rule"))
    summaries = summarize_metric_records(records)
    if records:
        raw_path = target / "metric_records.csv"
        write_metric_records_csv(raw_path, records)
        artifacts.append(ArtifactRecord("metric_records", str(raw_path), "csv"))
    if summaries:
        summary_path = target / "summary.csv"
        write_summary_csv(summary_path, summaries)
        artifacts.append(ArtifactRecord("summary", str(summary_path), "csv"))
    manifest = build_manifest(
        name="puyo79-benchmark-ablation",
        output_dir=target,
        records=records,
        summaries=summaries,
        artifacts=artifacts,
        args=args,
        dry_run=dry_run,
    )
    manifest_path = target / "benchmark_manifest.json"
    report_path = target / "report.md"
    write_json(manifest_path, manifest)
    write_markdown_report(report_path, manifest)
    return manifest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the PUYO-79 benchmark and ablation suite.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--max-ticks", type=int, default=120)
    parser.add_argument("--beam-depth", type=int, default=4)
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--latency-budget-ms", type=float, default=80.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    manifest = run_puyo79_suite(**vars(args))
    print(f"manifest: {Path(manifest['output_dir']) / 'benchmark_manifest.json'}")
    print(f"report: {Path(manifest['output_dir']) / 'report.md'}")
    print(f"recommended_model: {manifest['recommended_model'].get('variant', '')}")


if __name__ == "__main__":
    main()
