"""PUYO-167 paired benchmark for deterministic diverse beam candidates."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import (
    BUILD_POTENTIAL_SCHEMA_VERSION,
    BUILD_SCORING_V2,
    DIVERSE_CANDIDATE_MODE,
    LEGACY_CANDIDATE_MODE,
    BeamSearchConfig,
    BeamSearchPolicy,
    DiverseBeamCandidate,
    clone_simulator,
)
from eval.v1_7_benchmark import percentile
from puyo_env.actions import action_to_placement, legal_action_indices, legal_action_mask
from src.core.headless import HeadlessPuyoSimulator
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_diverse_beam_benchmark.v1"
DECISION_SCHEMA_VERSION = "puyo.v1_7_diverse_beam_decision.v1"
DEFAULT_SOURCE = (
    "docs/benchmarks/puyo-v1-7-2-search-diagnostics/decision_records.json"
)
DEFAULT_SOURCE_SHA256 = (
    "460f5fb26890d50117107269c342002750ecc84e0ab2d263044fe923502222c6"
)
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-diverse-beam"
LATENCY_MODE = "offline_wall_clock"


@dataclass(frozen=True)
class CandidateConfiguration:
    config_id: str
    candidate_mode: str
    depth: int
    width: int
    probe_width: int
    candidate_limit: int = 16

    def __post_init__(self) -> None:
        if self.candidate_mode not in {
            LEGACY_CANDIDATE_MODE,
            DIVERSE_CANDIDATE_MODE,
        }:
            raise ValueError(f"unsupported candidate mode: {self.candidate_mode}")
        if min(self.depth, self.width, self.probe_width, self.candidate_limit) <= 0:
            raise ValueError("candidate benchmark budgets must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "candidate_mode": self.candidate_mode,
            "depth": int(self.depth),
            "width": int(self.width),
            "probe_width": int(self.probe_width),
            "candidate_limit": int(self.candidate_limit),
            "scenarios": 1,
            "scoring_mode": BUILD_SCORING_V2,
            "build_potential_schema_version": BUILD_POTENTIAL_SCHEMA_VERSION,
        }

    def beam_config(self, *, use_cache: bool = True) -> BeamSearchConfig:
        return BeamSearchConfig(
            depth=self.depth,
            width=self.width,
            scenarios=1,
            minimum_chain_count=10,
            premature_chain_penalty=525.0,
            trigger_preservation="required",
            probe_width=self.probe_width,
            trace_paths=True,
            scoring_mode=BUILD_SCORING_V2,
            future_potential_weight=1.0,
            chain_shape_weight=1.0,
            danger_tolerance=0.65,
            build_potential_schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
            potential_probe_budget=self.probe_width * self.depth + 1,
            candidate_mode=self.candidate_mode,
            candidate_limit=self.candidate_limit,
            use_potential_cache=use_cache,
        )


BASELINE_CONFIGURATION = CandidateConfiguration(
    "scalar-d6-w48-p16-k16",
    LEGACY_CANDIDATE_MODE,
    6,
    48,
    16,
)
DIVERSE_CONFIGURATION = CandidateConfiguration(
    "diverse-d6-w48-p16-k16",
    DIVERSE_CANDIDATE_MODE,
    6,
    48,
    16,
)
RAW_SCALE_CONFIGURATION = CandidateConfiguration(
    "scalar-d8-w64-p32-k16",
    LEGACY_CANDIDATE_MODE,
    8,
    64,
    32,
)
DEFAULT_CONFIGURATIONS = (
    BASELINE_CONFIGURATION,
    DIVERSE_CONFIGURATION,
    RAW_SCALE_CONFIGURATION,
)


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    else:
        serialized = json.dumps(payload, indent=2, sort_keys=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def load_frozen_decisions(
    source: str | Path,
    *,
    games: int,
    max_steps: int,
    expected_sha256: str | None = DEFAULT_SOURCE_SHA256,
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    path = Path(source)
    source_sha256 = file_sha256(path)
    if expected_sha256 and source_sha256 != expected_sha256:
        raise ValueError(
            "PUYO-165 frozen source hash differs: "
            f"expected {expected_sha256}, got {source_sha256}"
        )
    payload = _read_json(path)
    if payload.get("schema_version") != "puyo.v1_7_search_diagnostics_decision.v1":
        raise ValueError("unsupported PUYO-165 decision source schema")
    grouped: dict[int, list[dict[str, Any]]] = {}
    for record in payload.get("records", []):
        grouped.setdefault(int(record["seed"]), []).append(record)
    selected_seeds = sorted(grouped)[: int(games)]
    if len(selected_seeds) < games:
        raise ValueError(
            f"frozen source has {len(selected_seeds)} seeds; expected {games}"
        )
    selected: dict[int, list[dict[str, Any]]] = {}
    for seed in selected_seeds:
        rows = sorted(grouped[seed], key=lambda item: int(item["step"]))
        selected[seed] = rows[: int(max_steps)]
    return selected, {
        "path": str(path),
        "sha256": source_sha256,
        "schema_version": payload["schema_version"],
        "available_decisions": sum(len(rows) for rows in grouped.values()),
        "selected_decisions": sum(len(rows) for rows in selected.values()),
        "seeds": selected_seeds,
        "max_steps": int(max_steps),
        "immutable_source": True,
    }


def _compact_candidate(candidate: DiverseBeamCandidate) -> dict[str, Any]:
    potential = candidate.build_potential
    return {
        "schema_version": candidate.schema_version,
        "rank": int(candidate.rank),
        "root_action": int(candidate.action),
        "plan": [int(action) for action in candidate.plan],
        "candidate_value": float(candidate.candidate_value),
        "predicted_max_chain": int(candidate.predicted_max_chain),
        "best_chain_depth": int(candidate.best_chain_depth),
        "build_potential": {
            "schema_version": potential.schema_version,
            "evaluation_status": potential.evaluation_status,
            "predicted_chain_count": (
                int(potential.chain_count) if potential.evaluated else None
            ),
            "predicted_chain_potential": potential.predicted_chain_potential,
            "continuation_flexibility": potential.continuation_flexibility,
            "danger_margin": potential.danger_margin,
            "truncation_reason": potential.truncation_reason,
        },
        "danger": float(candidate.danger),
        "continuation_flexibility": float(candidate.continuation_flexibility),
        "trigger_recoverability": candidate.trigger_recoverability.to_dict(),
        "value_breakdown": {
            key: float(value) for key, value in candidate.value_breakdown.items()
        },
        "reasons": {
            "generated": list(candidate.generation_reasons),
            "retained": list(candidate.retention_reasons),
            "pruned": list(candidate.pruning_reasons),
        },
        "scenario_support": int(candidate.scenario_support),
        "scenario_ids": [int(value) for value in candidate.scenario_ids],
    }


def _evaluate_configuration(
    policy: BeamSearchPolicy,
    simulator: HeadlessPuyoSimulator,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    legal = legal_action_indices(simulator)
    info = {
        "simulator": simulator,
        "action_mask": legal_action_mask(simulator),
    }
    started = time.perf_counter()
    candidates = policy.generate_candidates({}, info)
    latency_ms = (time.perf_counter() - started) * 1000.0
    diagnostics = policy.last_diagnostics
    if diagnostics is None or not candidates:
        raise RuntimeError("beam candidate generation produced no diagnostics")
    candidate_actions = [int(candidate.action) for candidate in candidates]
    illegal_actions = [action for action in candidate_actions if action not in legal]
    game_over_actions = []
    for action in candidate_actions:
        preview = clone_simulator(simulator).step(action_to_placement(action))
        if not preview.valid or preview.game_over:
            game_over_actions.append(action)
    comparison = source["comparison"]
    reference_action = int(source["reference"]["selected_action"])
    reference_path = tuple(int(value) for value in comparison["reference_best_path"])
    comparison_depth = min(policy.config.depth, len(reference_path))
    path_trace = policy.candidate_path_diagnostics(reference_path[:comparison_depth])
    path_covered = bool(path_trace and path_trace[-1]["final_prune"])
    opportunity = int(comparison["reference_best_chain"]) > int(
        comparison["current_selected_chain"]
    )
    return {
        "selected_action": int(candidates[0].action),
        "latency_ms": float(latency_ms),
        "expanded_nodes": int(diagnostics.expanded_nodes),
        "generated_nodes": int(diagnostics.generated_nodes),
        "transposition_hits": int(diagnostics.transposition_hits),
        "potential_probe_count": int(diagnostics.potential_probe_count),
        "potential_cache_hits": int(diagnostics.potential_cache_hits),
        "potential_budget_exhaustions": int(
            diagnostics.potential_budget_exhaustions
        ),
        "budget_exhausted": bool(diagnostics.budget_exhausted),
        "fallback_reason": diagnostics.fallback_reason,
        "reached_depth": int(diagnostics.reached_depth),
        "scenario_budget": dict(diagnostics.scenario_budget),
        "candidate_actions": candidate_actions,
        "candidates": [_compact_candidate(candidate) for candidate in candidates],
        "reference_action_covered": reference_action in candidate_actions,
        "reference_path_covered": path_covered,
        "reference_path_trace": list(path_trace),
        "long_chain_opportunity": bool(opportunity),
        "max_candidate_chain": max(
            (int(candidate.predicted_max_chain) for candidate in candidates),
            default=0,
        ),
        "selected_chain": int(candidates[0].predicted_max_chain),
        "illegal_actions": illegal_actions,
        "game_over_actions": game_over_actions,
    }


def _evaluate_seed_task(
    task: tuple[
        int,
        Sequence[Mapping[str, Any]],
        tuple[CandidateConfiguration, ...],
        bool,
        str,
    ],
) -> dict[str, Any]:
    seed, source_records, configurations, use_cache, scope = task
    simulator = HeadlessPuyoSimulator(seed=seed)
    policies = {
        config.config_id: BeamSearchPolicy(config.beam_config(use_cache=use_cache))
        for config in configurations
    }
    records = []
    for source in source_records:
        legal = legal_action_indices(simulator)
        replay_action = int(source["current"]["selected_action"])
        if replay_action not in legal:
            raise ValueError(
                f"frozen replay action {replay_action} is illegal at {seed}:{source['step']}"
            )
        comparison = source["comparison"]
        is_long_chain_opportunity = (
            int(comparison["reference_best_chain"]) >= 10
            and int(comparison["reference_best_chain"])
            > int(comparison["current_selected_chain"])
        )
        should_evaluate = scope == "all" or is_long_chain_opportunity
        if should_evaluate:
            configurations_payload = {
                config.config_id: _evaluate_configuration(
                    policies[config.config_id],
                    simulator,
                    source,
                )
                for config in configurations
            }
            record = {
                "schema_version": DECISION_SCHEMA_VERSION,
                "seed": int(seed),
                "step": int(source["step"]),
                "frozen_reference": {
                    "selected_action": int(source["reference"]["selected_action"]),
                    "best_path": [
                        int(value)
                        for value in comparison["reference_best_path"]
                    ],
                    "best_chain": int(comparison["reference_best_chain"]),
                    "current_selected_chain": int(
                        comparison["current_selected_chain"]
                    ),
                    "failure_class": comparison["failure_class"],
                    "long_chain_opportunity": is_long_chain_opportunity,
                },
                "replay_action": replay_action,
                "configurations": configurations_payload,
            }
            records.append(record)
        result = simulator.step(action_to_placement(replay_action))
        if not result.valid:
            raise RuntimeError(f"frozen replay failed at {seed}:{source['step']}")
        if result.game_over:
            break
    deterministic_projection = [
        {
            **record,
            "configurations": {
                config_id: {
                    key: value
                    for key, value in payload.items()
                    if key != "latency_ms"
                }
                for config_id, payload in record["configurations"].items()
            },
        }
        for record in records
    ]
    return {
        "seed": int(seed),
        "records": records,
        "digest": _digest(deterministic_projection),
    }


def _aggregate_configuration(
    records: Sequence[Mapping[str, Any]],
    config_id: str,
) -> dict[str, Any]:
    rows = [record["configurations"][config_id] for record in records]
    decisions = len(rows)
    opportunities = [row for row in rows if row["long_chain_opportunity"]]
    latencies = [float(row["latency_ms"]) for row in rows]
    return {
        "decisions": decisions,
        "reference_action_coverage": (
            0.0
            if decisions == 0
            else sum(bool(row["reference_action_covered"]) for row in rows)
            / decisions
        ),
        "reference_path_coverage": (
            0.0
            if decisions == 0
            else sum(bool(row["reference_path_covered"]) for row in rows)
            / decisions
        ),
        "long_chain_opportunities": len(opportunities),
        "long_chain_action_coverage": (
            None
            if not opportunities
            else sum(
                bool(row["reference_action_covered"]) for row in opportunities
            )
            / len(opportunities)
        ),
        "max_candidate_chain_mean": (
            0.0
            if decisions == 0
            else sum(int(row["max_candidate_chain"]) for row in rows) / decisions
        ),
        "selected_chain_mean": (
            0.0
            if decisions == 0
            else sum(int(row["selected_chain"]) for row in rows) / decisions
        ),
        "expanded_nodes_mean": (
            0.0
            if decisions == 0
            else sum(int(row["expanded_nodes"]) for row in rows) / decisions
        ),
        "expanded_nodes_total": sum(int(row["expanded_nodes"]) for row in rows),
        "transposition_hits": sum(int(row["transposition_hits"]) for row in rows),
        "potential_cache_hits": sum(int(row["potential_cache_hits"]) for row in rows),
        "potential_budget_exhaustions": sum(
            int(row["potential_budget_exhaustions"]) for row in rows
        ),
        "budget_fallbacks": sum(bool(row["budget_exhausted"]) for row in rows),
        "illegal_candidate_actions": sum(len(row["illegal_actions"]) for row in rows),
        "game_over_candidate_actions": sum(
            len(row["game_over_actions"]) for row in rows
        ),
        "latency": {
            "mode": LATENCY_MODE,
            "p50_ms": percentile(latencies, 0.50),
            "p95_ms": percentile(latencies, 0.95),
        },
    }


def _seed_comparisons(
    records: Sequence[Mapping[str, Any]],
    configurations: Sequence[CandidateConfiguration],
    *,
    seeds: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(int(record["seed"]), []).append(record)
    results = []
    for seed in sorted(set(grouped) | set(seeds or ())):
        aggregates = {
            config.config_id: _aggregate_configuration(
                grouped.get(seed, ()),
                config.config_id,
            )
            for config in configurations
        }
        baseline = aggregates[BASELINE_CONFIGURATION.config_id]
        diverse = aggregates[DIVERSE_CONFIGURATION.config_id]
        delta = (
            diverse["reference_action_coverage"]
            - baseline["reference_action_coverage"]
        )
        path_delta = (
            diverse["reference_path_coverage"]
            - baseline["reference_path_coverage"]
        )
        chain_delta = (
            diverse["max_candidate_chain_mean"]
            - baseline["max_candidate_chain_mean"]
        )
        if not grouped.get(seed):
            explanation = "no_reference_long_chain_opportunity"
        elif delta > 0.0 or path_delta > 0.0 or chain_delta > 0.0:
            explanation = "improved_coverage_or_candidate_quality"
        elif delta == 0.0 and path_delta == 0.0 and chain_delta == 0.0:
            explanation = "unchanged_on_bounded_sample"
        else:
            explanation = "coverage_regression"
        results.append(
            {
                "seed": seed,
                "decisions": len(grouped.get(seed, ())),
                "configurations": aggregates,
                "diverse_vs_baseline": {
                    "reference_action_coverage_delta": delta,
                    "reference_path_coverage_delta": path_delta,
                    "max_candidate_chain_delta": chain_delta,
                    "explanation": explanation,
                },
            }
        )
    return results


def _pareto_summary(
    aggregates: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for config_id, value in aggregates.items():
        dominated_by = []
        for other_id, other in aggregates.items():
            if other_id == config_id:
                continue
            no_worse = (
                other["reference_action_coverage"]
                >= value["reference_action_coverage"]
                and other["reference_path_coverage"]
                >= value["reference_path_coverage"]
                and other["expanded_nodes_mean"] <= value["expanded_nodes_mean"]
                and other["latency"]["p95_ms"] <= value["latency"]["p95_ms"]
            )
            strictly_better = (
                other["reference_action_coverage"]
                > value["reference_action_coverage"]
                or other["reference_path_coverage"]
                > value["reference_path_coverage"]
                or other["expanded_nodes_mean"] < value["expanded_nodes_mean"]
                or other["latency"]["p95_ms"] < value["latency"]["p95_ms"]
            )
            if no_worse and strictly_better:
                dominated_by.append(other_id)
        result.append(
            {
                "config_id": config_id,
                "pareto_frontier": not dominated_by,
                "dominated_by": sorted(dominated_by),
                "quality": {
                    "reference_action_coverage": value[
                        "reference_action_coverage"
                    ],
                    "reference_path_coverage": value[
                        "reference_path_coverage"
                    ],
                    "max_candidate_chain_mean": value[
                        "max_candidate_chain_mean"
                    ],
                },
                "cost": {
                    "expanded_nodes_mean": value["expanded_nodes_mean"],
                    "p50_ms": value["latency"]["p50_ms"],
                    "p95_ms": value["latency"]["p95_ms"],
                },
            }
        )
    return result


def evaluate_repetition(
    decisions: Mapping[int, Sequence[Mapping[str, Any]]],
    *,
    configurations: tuple[CandidateConfiguration, ...] = DEFAULT_CONFIGURATIONS,
    workers: int = 1,
    use_cache: bool = True,
    scope: str = "all",
) -> dict[str, Any]:
    if scope not in {"all", "long-chain"}:
        raise ValueError(f"unsupported benchmark scope: {scope}")
    tasks = [
        (seed, rows, configurations, use_cache, scope)
        for seed, rows in sorted(decisions.items())
    ]
    if workers == 1:
        results = [_evaluate_seed_task(task) for task in tasks]
    else:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context("spawn"),
        ) as executor:
            results = list(executor.map(_evaluate_seed_task, tasks, chunksize=1))
    results.sort(key=lambda item: int(item["seed"]))
    records = [record for result in results for record in result["records"]]
    aggregates = {
        config.config_id: _aggregate_configuration(records, config.config_id)
        for config in configurations
    }
    digest_payload = {
        "seed_digests": [
            {"seed": result["seed"], "digest": result["digest"]}
            for result in results
        ],
        "aggregates": {
            config_id: {
                key: value
                for key, value in aggregate.items()
                if key != "latency"
            }
            for config_id, aggregate in aggregates.items()
        },
    }
    return {
        "digest": _digest(digest_payload),
        "seed_digests": digest_payload["seed_digests"],
        "records": records,
        "aggregates": aggregates,
        "seed_comparisons": _seed_comparisons(
            records,
            configurations,
            seeds=sorted(decisions),
        ),
        "pareto": _pareto_summary(aggregates),
    }


def _quality_gate(
    aggregate: Mapping[str, Mapping[str, Any]],
    *,
    deterministic: bool,
) -> dict[str, Any]:
    baseline = aggregate[BASELINE_CONFIGURATION.config_id]
    diverse = aggregate[DIVERSE_CONFIGURATION.config_id]
    baseline_long = baseline["long_chain_action_coverage"]
    diverse_long = diverse["long_chain_action_coverage"]
    checks = {
        "deterministic": bool(deterministic),
        "reference_action_coverage_non_regression": (
            diverse["reference_action_coverage"]
            >= baseline["reference_action_coverage"]
        ),
        "long_chain_action_coverage_non_regression": (
            True
            if baseline_long is None or diverse_long is None
            else diverse_long >= baseline_long
        ),
        "max_candidate_chain_non_regression": (
            diverse["max_candidate_chain_mean"]
            >= baseline["max_candidate_chain_mean"]
        ),
        "no_illegal_candidates": all(
            value["illegal_candidate_actions"] == 0 for value in aggregate.values()
        ),
        "no_game_over_candidates": all(
            value["game_over_candidate_actions"] == 0
            for value in aggregate.values()
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failure_artifact_persisted": True,
        "policy": (
            "A failed gate remains in benchmark_summary.json and is not hidden "
            "by retuning fixed scalar weights."
        ),
    }


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    aggregate = summary["aggregate"]
    gate = summary["quality_gate"]
    lines = [
        "# PUYO-167 diverse beam candidate benchmark",
        "",
        f"- paired decisions: {summary['evaluated_decisions']}",
        f"- deterministic replay: **{'PASS' if summary['determinism']['passed'] else 'FAIL'}**",
        f"- quality gate: **{'PASS' if gate['passed'] else 'FAIL'}**",
        f"- frozen source: `{summary['source']['path']}` (`{summary['source']['sha256']}`)",
        "",
        "## Quality / cost comparison",
        "",
        "| config | root coverage | path coverage | long-chain coverage | max chain | nodes | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for config in summary["configurations"]:
        value = aggregate[config["config_id"]]
        long_chain = value["long_chain_action_coverage"]
        lines.append(
            f"| `{config['config_id']}` | {value['reference_action_coverage']:.3f} | "
            f"{value['reference_path_coverage']:.3f} | "
            f"{'n/a' if long_chain is None else f'{long_chain:.3f}'} | "
            f"{value['max_candidate_chain_mean']:.3f} | "
            f"{value['expanded_nodes_mean']:.1f} | "
            f"{value['latency']['p50_ms']:.2f} | {value['latency']['p95_ms']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Seed-level explanation",
            "",
            "| seed | decisions | root delta | path delta | max-chain delta | explanation |",
            "|---:|---:|---:|---:|---:|---|",
        ]
    )
    for seed in summary["seed_comparisons"]:
        comparison = seed["diverse_vs_baseline"]
        lines.append(
            f"| {seed['seed']} | {seed['decisions']} | "
            f"{comparison['reference_action_coverage_delta']:+.3f} | "
            f"{comparison['reference_path_coverage_delta']:+.3f} | "
            f"{comparison['max_candidate_chain_delta']:+.3f} | "
            f"`{comparison['explanation']}` |"
        )
    lines.extend(
        [
            "",
            "## Gate",
            "",
        ]
    )
    for name, passed in gate["checks"].items():
        lines.append(f"- {name}: **{'PASS' if passed else 'FAIL'}**")
    lines.extend(
        [
            "",
            "Wall-clock latency is observational and excluded from the deterministic digest. Expanded-node counts are the deterministic runtime-budget proxy. A failed gate is persisted unchanged in the artifact.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_benchmark(
    args: argparse.Namespace,
    *,
    configurations: tuple[CandidateConfiguration, ...] = DEFAULT_CONFIGURATIONS,
) -> dict[str, Any]:
    decisions, source = load_frozen_decisions(
        args.source,
        games=args.games,
        max_steps=args.max_steps,
        expected_sha256=args.expected_source_sha256,
    )
    repetitions = []
    scope = getattr(args, "scope", "all")
    for repetition in range(args.repetitions):
        result = evaluate_repetition(
            decisions,
            configurations=configurations,
            workers=args.workers,
            use_cache=True,
            scope=scope,
        )
        repetitions.append(result)
        print(
            f"repetition {repetition + 1}/{args.repetitions}: "
            f"digest={result['digest']} records={len(result['records'])}",
            flush=True,
        )
    digests = [result["digest"] for result in repetitions]
    determinism = {
        "passed": len(set(digests)) == 1,
        "repetitions": len(repetitions),
        "digests": digests,
        "excluded_fields": ["latency_ms", "aggregate.latency"],
        "scope": [
            "candidate set and rank",
            "plans and BuildPotential-v2 summaries",
            "generation, retention, and pruning reasons",
            "cache, transposition, scenario, and budget diagnostics",
            "latency-free seed and aggregate metrics",
        ],
    }
    first = repetitions[0]
    gate = _quality_gate(first["aggregates"], deterministic=determinism["passed"])
    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "git_commit": git_commit(),
        "evaluation_completed": bool(first["records"]),
        "latency_mode": LATENCY_MODE,
        "reference_semantics": (
            "PUYO-165 frozen bounded-reference paths; paired states, not an oracle"
        ),
        "scope": scope,
        "evaluated_decisions": len(first["records"]),
        "source": source,
        "configurations": [config.to_dict() for config in configurations],
        "determinism": determinism,
        "aggregate": first["aggregates"],
        "seed_comparisons": first["seed_comparisons"],
        "pareto": first["pareto"],
        "quality_gate": gate,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_dir / "decision_records.json",
        {
            "schema_version": DECISION_SCHEMA_VERSION,
            "records": first["records"],
        },
        compact=True,
    )
    _write_json(
        output_dir / "seed_results.json",
        {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "seeds": first["seed_comparisons"],
        },
    )
    _write_json(output_dir / "determinism.json", determinism)
    _write_json(output_dir / "benchmark_summary.json", summary)
    _write_report(output_dir / "benchmark_report.md", summary)
    artifact_paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "name": "puyo-v1-7-2-diverse-beam",
        "created_at_utc": summary["created_at_utc"],
        "git_commit": summary["git_commit"],
        "evaluation_completed": summary["evaluation_completed"],
        "quality_gate_passed": gate["passed"],
        "source": source,
        "configurations": summary["configurations"],
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifact_paths
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    return summary


def verify_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    artifact_dir = Path(args.artifact_dir)
    manifest = _read_json(artifact_dir / "benchmark_manifest.json")
    issues = []
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected manifest schema")
    for artifact in manifest.get("artifacts", []):
        path = artifact_dir / artifact["path"]
        if not path.exists():
            issues.append(f"missing artifact: {artifact['path']}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"artifact hash mismatch: {artifact['path']}")
    summary = _read_json(artifact_dir / "benchmark_summary.json")
    decisions = _read_json(artifact_dir / "decision_records.json")
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected summary schema")
    if decisions.get("schema_version") != DECISION_SCHEMA_VERSION:
        issues.append("unexpected decision schema")
    if any(
        record.get("schema_version") != DECISION_SCHEMA_VERSION
        for record in decisions.get("records", [])
    ):
        issues.append("decision record schema mismatch")
    if not summary.get("determinism", {}).get("passed"):
        issues.append("deterministic replay failed")
    if not summary.get("quality_gate", {}).get("passed"):
        issues.append("quality gate failed; failure artifact remains available")
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "passed": not issues,
        "issues": issues,
        "quality_gate": summary.get("quality_gate"),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--source", default=DEFAULT_SOURCE)
    run.add_argument("--expected-source-sha256", default=DEFAULT_SOURCE_SHA256)
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--games", type=int, default=30)
    run.add_argument("--max-steps", type=int, default=40)
    run.add_argument("--repetitions", type=int, default=2)
    run.add_argument(
        "--scope",
        choices=("all", "long-chain"),
        default="long-chain",
        help="Evaluate every replay state or only reference >=10-chain opportunities.",
    )
    run.add_argument(
        "--workers",
        type=int,
        default=max(1, min(12, os.cpu_count() or 1)),
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--artifact-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    for name in ("games", "max_steps", "repetitions", "workers"):
        if hasattr(args, name) and getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        result = run_benchmark(args)
    else:
        result = verify_benchmark(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if args.command == "run" or result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
