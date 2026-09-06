"""Read-only diagnostic: retain failing materializer locals, then re-raise unchanged."""

import functools
import math
import operator
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from agents import chain_structure
from agents import deep_chain_search_backend as backend
from eval import deep_chain_builder_benchmark as baseline


def safe(value):
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


original = backend.materialize_native_long_horizon_result
captures = []


def materialize(result, request):
    try:
        return original(result, request)
    except Exception as exc:
        trace = exc.__traceback__
        failure = {
            "error": {"type": type(exc).__name__, "detail": str(exc)},
            "state": asdict(request.state),
            "search_config": asdict(request.search_config),
        }
        while trace:
            local = trace.tb_frame.f_locals
            name = trace.tb_frame.f_code.co_name
            if (
                name == "materialize_native_long_horizon_result"
                and "materialized" in local
            ):
                materialized = local["materialized"]
                roots = []
                fold_ranks = []
                for e in materialized.ranked_roots:
                    scores = (
                        [
                            s.survivor_evaluator_score
                            for s in e.scenario_values
                            if s.evaluated
                            and s.selected_fire_class == "quiet_continuation"
                            and s.survivor_evaluator_score is not None
                        ]
                        if e.fire_class == "quiet_continuation"
                        else [
                            s.selected_fire.terminal_score
                            for s in e.scenario_values
                            if s.evaluated
                            and s.selected_fire_class == e.fire_class
                            and s.selected_fire is not None
                        ]
                    )
                    folded = functools.reduce(operator.add, scores, 0.0)
                    py = sum(scores)
                    replacement = (
                        (
                            folded / len(scores)
                            if e.fire_class == "quiet_continuation"
                            else folded
                        )
                        if scores
                        else e.candidate_value
                    )
                    key = list(e.ranking_key)
                    key[3] = replacement
                    if e.fire_class == "quiet_continuation" and scores:
                        key[11] = replacement
                    continuation_scores = [
                        v.survivor_evaluator_score
                        for v in e.scenario_values
                        if v.evaluated
                        and v.selected_fire_class == "quiet_continuation"
                        and v.survivor_evaluator_score is not None
                    ]
                    if continuation_scores:
                        key[11] = functools.reduce(
                            operator.add, continuation_scores, 0.0
                        ) / len(continuation_scores)
                    chain_scores = [
                        float(v.selected_fire.chain_score)
                        if v.selected_fire is not None
                        and v.selected_fire_class == e.fire_class
                        else 0.0
                        for v in e.scenario_values
                        if v.evaluated
                    ]
                    chain_counts = [
                        float(v.selected_fire.chain_count)
                        if v.selected_fire is not None
                        and v.selected_fire_class == e.fire_class
                        else 0.0
                        for v in e.scenario_values
                        if v.evaluated
                    ]
                    for index, values in ((9, chain_scores), (10, chain_counts)):
                        if values:
                            mean = functools.reduce(operator.add, values, 0.0) / len(
                                values
                            )
                            key[index] = -math.sqrt(
                                functools.reduce(
                                    operator.add,
                                    [(v - mean) * (v - mean) for v in values],
                                    0.0,
                                )
                                / len(values)
                            )
                    fold_ranks.append((tuple(key), e.root_action))
                    roots.append(
                        {
                            "root_action": e.root_action,
                            "class": e.fire_class,
                            "ranking_key": e.ranking_key,
                            "candidate_value_hex": e.candidate_value.hex(),
                            "scenario_scores": scores,
                            "python_sum": py,
                            "left_fold_sum": folded,
                            "sum_diff": py - folded,
                            "chain_scores": chain_scores,
                            "chain_counts": chain_counts,
                            "continuation_scores": continuation_scores,
                            "left_fold_ranking_key": key,
                        }
                    )
                failure["aggregation"] = {
                    "native_ranking": list(result.ranked_root_actions),
                    "python_ranking": [
                        e.root_action for e in materialized.ranked_roots
                    ],
                    "python_ranking_with_left_fold": [
                        action for key, action in sorted(fold_ranks, reverse=True)
                    ],
                    "native_selected_action": result.selected_action,
                    "roots": roots,
                }
            if (
                name == "materialize_native_chain_structure_result"
                and "native_best" in local
            ):
                native_best, python_best = local["native_best"], local["best"]
                failure["evaluator"] = {
                    "native_best_signature": native_best.canonical_signature,
                    "python_best_signature": python_best.canonical_signature,
                    "native_best_key": chain_structure._candidate_rank_key(native_best),
                    "python_best_key": chain_structure._candidate_rank_key(python_best),
                    "native_best": native_best.to_dict(),
                    "python_best": python_best.to_dict(),
                    "exported_candidates": [c.to_dict() for c in local["candidates"]],
                    "native_best_in_exported_candidates": any(
                        c.canonical_signature == native_best.canonical_signature
                        for c in local["candidates"]
                    ),
                    "max_candidates": local["config"].budget.max_candidates,
                    "state": asdict(local["state"]),
                }
            trace = trace.tb_next
        captures.append(safe(failure))
        raise


output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
for seed, target in ((137, 6), (141, 6), (151, 10), (146, 12)):
    captures.clear()
    with patch.object(backend, "materialize_native_long_horizon_result", materialize):
        run = baseline.run_benchmark_run(
            seed=seed,
            repeat=1,
            profile="reference",
            max_steps=40,
            backend="native",
            target_chain_count=target,
        )
    payload = {
        "kind": "failure_diagnostic_excluded_from_quality_and_performance",
        "source_commit": run["evaluated_commit"],
        "seed": seed,
        "target": target,
        "termination": run["termination_reason"],
        "completed_turns": run["completed_turns"],
        "captures": captures,
    }
    path = output / f"seed-{seed}-target-{target:02d}.json"
    baseline._write_json(path, payload)
    print(path, payload["termination"], len(captures), flush=True)
