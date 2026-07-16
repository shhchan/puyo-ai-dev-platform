"""PUYO-174 long-horizon expected-chain validation and ablation benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import BeamSearchConfig, BeamSearchPolicy
from agents.chain_structure import ChainStructureEvaluator
from agents.long_horizon_search import (
    EXPECTED_CHAIN_EVIDENCE_SCHEMA_VERSION,
    EXPECTED_CHAIN_RANKING_RULE_VERSION,
    LONG_HORIZON_SEARCH_PROFILES,
    SCENARIO_SEQUENCE_SCHEMA_VERSION,
    TERMINAL_FIRE_RECORD_AND_STOP,
    LongHorizonSearchConfig,
    build_scenario_sequences,
    run_long_horizon_search,
)
from puyo_env.actions import legal_action_mask
from src.core.headless import HeadlessPuyoSimulator
from train.artifacts import git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_long_horizon_search_benchmark.v1"
MANIFEST_SCHEMA_VERSION = "puyo.v1_7_long_horizon_search_manifest.v1"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-long-horizon-search"
DEFAULT_VALIDATION_SEEDS = (174, 1_174, 2_174)


def _stable_digest(value: Any, *, prefix: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class AblationConfiguration:
    config_id: str
    stage: str
    backend: str
    evaluator: str
    depth: int
    width: int
    scenarios: int
    max_expanded_nodes: int
    use_transposition_table: bool
    terminal_fire_rule: str = TERMINAL_FIRE_RECORD_AND_STOP

    def __post_init__(self) -> None:
        if not self.config_id or not self.stage:
            raise ValueError("ablation configuration id and stage are required")
        if self.backend not in {"legacy_simulator", "compact"}:
            raise ValueError(f"unsupported ablation backend: {self.backend}")
        if self.evaluator not in {"legacy", "height", "chain_structure_v1"}:
            raise ValueError(f"unsupported ablation evaluator: {self.evaluator}")
        if (
            min(
                self.depth,
                self.width,
                self.scenarios,
                self.max_expanded_nodes,
            )
            <= 0
        ):
            raise ValueError("ablation budgets must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "stage": self.stage,
            "backend": self.backend,
            "evaluator": self.evaluator,
            "depth": int(self.depth),
            "width": int(self.width),
            "scenarios": int(self.scenarios),
            "max_expanded_nodes": int(self.max_expanded_nodes),
            "use_transposition_table": bool(self.use_transposition_table),
            "terminal_fire_rule": self.terminal_fire_rule,
            "budget_authority": "expanded_nodes",
            "wall_clock_mode": "observational",
        }


DEFAULT_CONFIG_MATRIX = (
    AblationConfiguration(
        "baseline",
        "baseline",
        "legacy_simulator",
        "legacy",
        3,
        24,
        1,
        1_200,
        True,
    ),
    AblationConfiguration(
        "compact",
        "compact_kernel",
        "compact",
        "height",
        3,
        24,
        1,
        1_200,
        False,
    ),
    AblationConfiguration(
        "lightweight",
        "lightweight_evaluator",
        "compact",
        "chain_structure_v1",
        3,
        24,
        1,
        1_200,
        False,
    ),
    AblationConfiguration(
        "six-scenario",
        "six_scenario",
        "compact",
        "chain_structure_v1",
        3,
        8,
        6,
        3_600,
        False,
    ),
    AblationConfiguration(
        "long-horizon",
        "long_horizon",
        "compact",
        "chain_structure_v1",
        6,
        8,
        6,
        8_000,
        False,
    ),
    AblationConfiguration(
        "long-horizon-tt",
        "transposition_table",
        "compact",
        "chain_structure_v1",
        6,
        8,
        6,
        8_000,
        True,
    ),
)


@dataclass(frozen=True)
class _HeightEvaluation:
    score: float
    danger: float
    continuation_flexibility: float
    tie_break_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_status": "available",
            "score": float(self.score),
            "danger": float(self.danger),
            "continuation_flexibility": float(self.continuation_flexibility),
            "tie_break_digest": self.tie_break_digest,
            "evaluator": "compact_height_v1",
        }


class CompactHeightEvaluator:
    """Cheap compact control used to isolate the transition-kernel stage."""

    def evaluate(self, state, **_kwargs):
        height_cost = sum(height * height for height in state.column_heights)
        danger = min(1.0, max(state.column_heights, default=0) / 14.0)
        return _HeightEvaluation(
            score=float(state.cell_count * 4 - height_cost),
            danger=danger,
            continuation_flexibility=max(0.0, 1.0 - danger),
            tie_break_digest=hashlib.sha256(state.to_bytes()).hexdigest()[:24],
        )


def _compact_record(
    seed: int,
    config: AblationConfiguration,
) -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=int(seed))
    evaluator = (
        CompactHeightEvaluator()
        if config.evaluator == "height"
        else ChainStructureEvaluator()
    )
    started = time.perf_counter()
    result = run_long_horizon_search(
        simulator,
        LongHorizonSearchConfig(
            depth=config.depth,
            width=config.width,
            scenarios=config.scenarios,
            minimum_chain_count=10,
            max_expanded_nodes=config.max_expanded_nodes,
            terminal_fire_rule=config.terminal_fire_rule,
            terminal_fire_chain_count=1,
            use_transposition_table=config.use_transposition_table,
        ),
        evaluator=evaluator,
    )
    elapsed = time.perf_counter() - started
    selected = result.ranked_roots[0]
    representative = result.representatives.get(selected.root_action)
    sequence_payload = [item.to_dict() for item in result.scenario_sequences]
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "config_id": config.config_id,
        "stage": config.stage,
        "seed": int(seed),
        "selected_action": int(selected.root_action),
        "mean_max_chain": float(selected.chain_count_mean),
        "max_chain": int(selected.max_chain_count),
        "expected_chain_score": int(selected.chain_score_sum),
        "scenario_support": int(selected.support),
        "scenario_coverage": float(selected.coverage),
        "survival": int(
            representative is not None and not representative.state.game_over
        ),
        "dead_end": int(
            selected.max_chain_count == 0
            and (
                representative is None or representative.continuation_flexibility <= 0.0
            )
        ),
        "expanded_nodes": int(result.counters.expanded_nodes),
        "generated_nodes": int(result.counters.generated_nodes),
        "pruned_nodes": int(result.counters.pruned_nodes),
        "transposition_hits": int(result.counters.transposition_hits),
        "reached_depth": int(result.counters.reached_depth),
        "budget_exhausted": bool(result.counters.budget_exhausted),
        "truncation_reason": (
            "expanded_node_budget" if result.counters.budget_exhausted else None
        ),
        "elapsed_seconds": elapsed,
        "known_queue": [
            [color.name for color in pair]
            for pair in result.scenario_sequences[0].known_pairs
        ],
        "known_pair_count": int(result.scenario_sequences[0].known_pair_count),
        "scenario_sequence_digests": [
            item["sequence_digest"] for item in sequence_payload
        ],
        "scenario_sequences": sequence_payload,
        "root_evidence": selected.to_dict(),
        "deterministic_digest": result.deterministic_digest,
    }


def _legacy_record(
    seed: int,
    config: AblationConfiguration,
) -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=int(seed))
    policy = BeamSearchPolicy(
        BeamSearchConfig(
            depth=config.depth,
            width=config.width,
            scenarios=config.scenarios,
            max_expanded_nodes=config.max_expanded_nodes,
        )
    )
    started = time.perf_counter()
    candidates = policy.generate_candidates(
        {},
        {
            "simulator": simulator,
            "action_mask": legal_action_mask(simulator),
        },
    )
    elapsed = time.perf_counter() - started
    diagnostics = policy.last_diagnostics
    selected = candidates[0]
    sequences = build_scenario_sequences(
        simulator,
        scenarios=config.scenarios,
        depth=config.depth,
    )
    deterministic = {
        "selected_action": selected.action,
        "candidate_values": diagnostics.candidate_values,
        "predicted_max_chain": selected.predicted_max_chain,
        "best_chain_depth": selected.best_chain_depth,
    }
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "config_id": config.config_id,
        "stage": config.stage,
        "seed": int(seed),
        "selected_action": int(selected.action),
        "mean_max_chain": float(selected.predicted_max_chain),
        "max_chain": int(selected.predicted_max_chain),
        "expected_chain_score": 0,
        "scenario_support": int(selected.scenario_support),
        "scenario_coverage": 1.0,
        "survival": int(selected.danger < 1.0),
        "dead_end": int(selected.continuation_flexibility <= 0.0),
        "expanded_nodes": int(diagnostics.expanded_nodes),
        "generated_nodes": int(diagnostics.generated_nodes),
        "pruned_nodes": 0,
        "transposition_hits": int(diagnostics.transposition_hits),
        "reached_depth": int(diagnostics.reached_depth),
        "budget_exhausted": bool(diagnostics.budget_exhausted),
        "truncation_reason": diagnostics.fallback_reason,
        "elapsed_seconds": elapsed,
        "known_queue": [
            [color.name for color in pair] for pair in sequences[0].known_pairs
        ],
        "known_pair_count": int(sequences[0].known_pair_count),
        "scenario_sequence_digests": [item.sequence_digest for item in sequences],
        "scenario_sequences": [item.to_dict() for item in sequences],
        "root_evidence": None,
        "deterministic_digest": _stable_digest(
            deterministic,
            prefix="legacy-search",
        ),
    }


def run_configuration(seed: int, config: AblationConfiguration) -> dict[str, Any]:
    if config.backend == "legacy_simulator":
        return _legacy_record(seed, config)
    return _compact_record(seed, config)


def _aggregate(
    records: Sequence[Mapping[str, Any]],
    matrix: Sequence[AblationConfiguration],
) -> list[dict[str, Any]]:
    result = []
    previous: dict[str, Any] | None = None
    for config in matrix:
        rows = [row for row in records if row["config_id"] == config.config_id]
        count = len(rows)
        summary = {
            "config_id": config.config_id,
            "stage": config.stage,
            "seeds": count,
            "mean_max_chain": sum(float(row["max_chain"]) for row in rows) / count,
            "mean_expected_chain_score": sum(
                float(row["expected_chain_score"]) for row in rows
            )
            / count,
            "survival_rate": sum(int(row["survival"]) for row in rows) / count,
            "dead_end_rate": sum(int(row["dead_end"]) for row in rows) / count,
            "mean_scenario_coverage": sum(
                float(row["scenario_coverage"]) for row in rows
            )
            / count,
            "mean_expanded_nodes": sum(int(row["expanded_nodes"]) for row in rows)
            / count,
            "mean_elapsed_seconds": sum(float(row["elapsed_seconds"]) for row in rows)
            / count,
            "transposition_hits": sum(int(row["transposition_hits"]) for row in rows),
            "budget_exhaustions": sum(
                int(bool(row["budget_exhausted"])) for row in rows
            ),
        }
        summary["delta_from_previous"] = (
            None
            if previous is None
            else {
                "mean_max_chain": (
                    summary["mean_max_chain"] - previous["mean_max_chain"]
                ),
                "mean_expected_chain_score": (
                    summary["mean_expected_chain_score"]
                    - previous["mean_expected_chain_score"]
                ),
                "survival_rate": (summary["survival_rate"] - previous["survival_rate"]),
                "dead_end_rate": (summary["dead_end_rate"] - previous["dead_end_rate"]),
                "mean_expanded_nodes": (
                    summary["mean_expanded_nodes"] - previous["mean_expanded_nodes"]
                ),
            }
        )
        result.append(summary)
        previous = summary
    return result


def _determinism_check(
    seed: int,
    config: AblationConfiguration,
) -> dict[str, Any]:
    first = run_configuration(seed, config)
    second = run_configuration(seed, config)
    return {
        "config_id": config.config_id,
        "seed": int(seed),
        "first_digest": first["deterministic_digest"],
        "second_digest": second["deterministic_digest"],
        "match": first["deterministic_digest"] == second["deterministic_digest"],
        "first_selected_action": int(first["selected_action"]),
        "second_selected_action": int(second["selected_action"]),
    }


def _report(
    summaries: Sequence[Mapping[str, Any]],
    determinism: Mapping[str, Any],
) -> str:
    lines = [
        "# PUYO-174 Long-Horizon Expected-Chain Search",
        "",
        "This validation uses PUYO-174 held-out seeds, not the canonical safe-build gate seeds.",
        "Wall-clock time is observational; expanded-node counts are authoritative.",
        "",
        "| stage | mean max chain | mean expected score | survival | dead-end | nodes | elapsed (s) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            "| {stage} | {chain:.3f} | {score:.1f} | {survival:.3f} | "
            "{dead_end:.3f} | {nodes:.1f} | {elapsed:.4f} |".format(
                stage=row["stage"],
                chain=row["mean_max_chain"],
                score=row["mean_expected_chain_score"],
                survival=row["survival_rate"],
                dead_end=row["dead_end_rate"],
                nodes=row["mean_expanded_nodes"],
                elapsed=row["mean_elapsed_seconds"],
            )
        )
    final = summaries[-1]
    baseline = summaries[0]
    improved = final["mean_max_chain"] > baseline["mean_max_chain"]
    ten_chain_visible = final["mean_max_chain"] >= 10.0
    go = improved and ten_chain_visible
    lines.extend(
        [
            "",
            "## Determinism",
            "",
            f"- two-repeat digest match: {bool(determinism['match'])}",
            f"- selected action stable: {determinism['first_selected_action'] == determinism['second_selected_action']}",
            "",
            "## Stop / Go",
            "",
            f"- capability improved over baseline: {improved}",
            f"- 10-chain reachability visible in this validation: {ten_chain_visible}",
            "- verdict: GO to latency review"
            if go
            else "- verdict: STOP for evaluator/pruning diagnosis",
            "",
            "The registered quality-d16 contract remains depth=16, width=250, scenarios=6; "
            "count-budget truncation is reported rather than hidden.",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    seeds: Sequence[int] = DEFAULT_VALIDATION_SEEDS,
    matrix: Sequence[AblationConfiguration] = DEFAULT_CONFIG_MATRIX,
) -> dict[str, Any]:
    if not seeds or not matrix:
        raise ValueError("benchmark requires at least one seed and configuration")
    target = Path(output_dir)
    started = time.perf_counter()
    records = [
        run_configuration(int(seed), config) for config in matrix for seed in seeds
    ]
    summaries = _aggregate(records, matrix)
    determinism = _determinism_check(int(seeds[0]), matrix[-1])
    elapsed = time.perf_counter() - started
    config_matrix = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "validation_seed_source": "PUYO-174 held-out fixed seeds",
        "canonical_gate_reused": False,
        "seeds": [int(seed) for seed in seeds],
        "registered_profiles": {
            name: profile.to_dict()
            for name, profile in LONG_HORIZON_SEARCH_PROFILES.items()
        },
        "ablation": [config.to_dict() for config in matrix],
    }
    ablation = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "summaries": summaries,
    }
    manifest_records = [
        record for record in records if record["config_id"] == matrix[-1].config_id
    ]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": utc_timestamp(),
        "commit": git_commit(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "ranking_rule_version": EXPECTED_CHAIN_RANKING_RULE_VERSION,
        "evidence_schema_version": EXPECTED_CHAIN_EVIDENCE_SCHEMA_VERSION,
        "validation_seed_source": "PUYO-174 held-out fixed seeds",
        "canonical_gate_reused": False,
        "seeds": [int(seed) for seed in seeds],
        "config_ids": [config.config_id for config in matrix],
        "registered_profiles": {
            name: profile.to_dict()
            for name, profile in LONG_HORIZON_SEARCH_PROFILES.items()
        },
        "known_queue": {
            "contract": "current_plus_next2",
            "known_pair_count": 3,
            "unknown_boundary_cursor": 3,
            "records": [
                {
                    "seed": int(record["seed"]),
                    "pairs": record["known_queue"],
                }
                for record in manifest_records
            ],
        },
        "scenario_sequences": {
            "schema_version": SCENARIO_SEQUENCE_SCHEMA_VERSION,
            "config_id": matrix[-1].config_id,
            "records": [
                {
                    "seed": int(record["seed"]),
                    "scenario_ids": [
                        int(sequence["scenario_id"])
                        for sequence in record["scenario_sequences"]
                    ],
                    "sequence_digests": record["scenario_sequence_digests"],
                }
                for record in manifest_records
            ],
        },
        "count_budgets": [
            {
                "config_id": config.config_id,
                "authority": "expanded_nodes",
                "max_expanded_nodes": int(config.max_expanded_nodes),
                "wall_clock_mode": "observational",
            }
            for config in matrix
        ],
        "count_budget_authoritative": True,
        "wall_clock_mode": "observational",
        "elapsed_seconds": elapsed,
        "artifacts": [
            "config_matrix.json",
            "seed_results.json",
            "ablation.json",
            "determinism.json",
            "benchmark_report.md",
        ],
    }
    _write_json(target / "config_matrix.json", config_matrix)
    _write_json(target / "seed_results.json", records)
    _write_json(target / "ablation.json", ablation)
    _write_json(target / "determinism.json", determinism)
    _write_json(target / "benchmark_manifest.json", manifest)
    (target / "benchmark_report.md").write_text(
        _report(summaries, determinism),
        encoding="utf-8",
    )
    return {
        "config_matrix": config_matrix,
        "records": records,
        "ablation": ablation,
        "determinism": determinism,
        "manifest": manifest,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--seeds",
        default=",".join(str(seed) for seed in DEFAULT_VALIDATION_SEEDS),
        help="comma-separated PUYO-174 validation seeds",
    )
    args = parser.parse_args()
    seeds = tuple(int(value) for value in args.seeds.split(",") if value.strip())
    result = run_benchmark(output_dir=args.output_dir, seeds=seeds)
    final = result["ablation"]["summaries"][-1]
    print(f"records: {len(result['records'])}")
    print(f"deterministic: {result['determinism']['match']}")
    print(f"final mean max chain: {final['mean_max_chain']:.3f}")


if __name__ == "__main__":
    main()
