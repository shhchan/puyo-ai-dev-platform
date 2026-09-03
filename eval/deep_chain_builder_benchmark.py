"""PUYO-189 deep-chain quality, performance, and acceptance evidence.

The canonical ``run`` command never applies a wall-clock search cutoff.  This
preserves the count-authoritative reference profile fixed by PUYO-185.  Runs
are written independently and can be resumed.  ``preflight`` is a separate,
explicitly non-canonical diagnostic used to prove a latency-gate failure
without misrepresenting a terminated search as quality evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from queue import Empty
from typing import Any

from agents.deep_chain_builder import (
    DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH,
    VISIBLE_INFO_FIELDS,
    VISIBLE_OBSERVATION_FIELDS,
    build_visible_runtime_input,
    load_deep_chain_builder_config,
)
from agents.deep_chain_search_backend import (
    DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH,
    load_long_horizon_backend_config,
)
from puyo_env.actions import action_to_placement, legal_action_mask
from puyo_env.obs import encode_observation
from selfplay.policies import make_policy
from src.core.constants import GRID_HEIGHT, GRID_WIDTH
from src.core.headless import HeadlessPuyoSimulator
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

RUN_SCHEMA_VERSION = "puyo.deep_chain_builder.benchmark_run.v1"
RUN_INDEX_SCHEMA_VERSION = "puyo.deep_chain_builder.run_index.v1"
SUMMARY_SCHEMA_VERSION = "puyo.deep_chain_builder.benchmark_summary.v1"
MANIFEST_SCHEMA_VERSION = "puyo.deep_chain_builder.benchmark_manifest.v1"
PREFLIGHT_SCHEMA_VERSION = "puyo.deep_chain_builder.preflight.v1"
FUTURE_ISOLATION_SCHEMA_VERSION = "puyo.deep_chain_builder.future_isolation.v1"
GUI_QA_SCHEMA_VERSION = "puyo.deep_chain_builder.gui_qa.v1"
LINEAGE_SCHEMA_VERSION = "puyo.deep_chain_builder.lineage.v1"

DEFAULT_OUTPUT_DIR = Path("docs/benchmarks/puyo-189-deep-chain-builder-baseline")
REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SOURCE_COMMIT = "dea210bcd92965ae08fbc311f23565b0fab6dbbb"
REFERENCE_SOURCE_URL = (
    f"https://github.com/citrus610/ama/tree/{REFERENCE_SOURCE_COMMIT}"
)
CANONICAL_BACKEND = "native"


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_ready(value.tolist())
    if hasattr(value, "to_dict"):
        return _json_ready(value.to_dict())
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return repr(value)


def _stable_digest(value: Any, *, prefix: str) -> str:
    encoded = json.dumps(
        _json_ready(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(prefix.encode("utf-8") + b":" + encoded).hexdigest()


def percentile(values: Sequence[float | int], quantile: float) -> float | None:
    """Return a deterministic linearly interpolated percentile."""

    if not values:
        return None
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _mean(values: Sequence[float | int]) -> float | None:
    if not values:
        return None
    return sum(float(value) for value in values) / len(values)


def _board_snapshot(simulator: HeadlessPuyoSimulator) -> list[list[str]]:
    return [
        [simulator.game.field.grid[y][x].color.name for x in range(GRID_WIDTH)]
        for y in range(GRID_HEIGHT)
    ]


def _visible_input_payload(value: Any) -> dict[str, Any]:
    return {
        "board": _json_ready(value.board),
        "next_pairs": _json_ready(value.next_pairs),
        "action_mask": list(value.action_mask),
        "scalars": _json_ready(value.scalars),
        "realtime_scalars": _json_ready(value.realtime_scalars),
        "observation_schema_version": value.observation_schema_version,
        "action_contract_version": value.action_contract_version,
        "action_mask_source": value.action_mask_source,
        "score": int(value.score),
        "step_count": int(value.step_count),
        "max_steps": value.max_steps,
        "max_ticks": value.max_ticks,
        "last_chain_end_score": int(value.last_chain_end_score),
        "last_chain_score_delta": int(value.last_chain_score_delta),
    }


def _initial_observation_and_info(
    seed: int,
    *,
    max_steps: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    simulator = HeadlessPuyoSimulator(seed=int(seed))
    mask = legal_action_mask(simulator)
    observation = encode_observation(
        simulator,
        step_count=0,
        max_steps=max_steps,
    )
    info = {
        "action_mask": mask,
        "action_mask_source": "headless_simulator",
        "score": int(simulator.game.score),
        "step_count": 0,
        "max_steps": int(max_steps),
        "last_chain_end_score": int(simulator.game.last_chain_end_score),
    }
    return observation, info


def audit_future_isolation(
    seeds: Sequence[int],
    *,
    max_steps: int,
) -> dict[str, Any]:
    """Prove that private sentinels do not cross the policy input boundary."""

    records = []
    for seed in seeds:
        observation, info = _initial_observation_and_info(seed, max_steps=max_steps)
        left_observation = dict(observation)
        right_observation = dict(observation)
        left_info = dict(info)
        right_info = dict(info)
        left_observation["private_future_queue"] = "private-future-left"
        right_observation["private_future_queue"] = "private-future-right"
        left_info.update(
            {
                "simulator": "private-simulator-left",
                "future_queue": "private-future-left",
            }
        )
        right_info.update(
            {
                "simulator": "private-simulator-right",
                "future_queue": "private-future-right",
            }
        )
        left_payload = _visible_input_payload(
            build_visible_runtime_input(left_observation, left_info)
        )
        right_payload = _visible_input_payload(
            build_visible_runtime_input(right_observation, right_info)
        )
        left_digest = _stable_digest(left_payload, prefix="visible-runtime-input")
        right_digest = _stable_digest(right_payload, prefix="visible-runtime-input")
        serialized = json.dumps(left_payload, sort_keys=True)
        leaked = left_digest != right_digest or "private-future" in serialized
        records.append(
            {
                "seed": int(seed),
                "left_digest": left_digest,
                "right_digest": right_digest,
                "digests_match": left_digest == right_digest,
                "private_marker_absent": "private-future" not in serialized,
                "leaked": leaked,
            }
        )
    leak_count = sum(bool(record["leaked"]) for record in records)
    return {
        "schema_version": FUTURE_ISOLATION_SCHEMA_VERSION,
        "method": "counterfactual_private_sentinel_boundary_audit",
        "visible_observation_fields": list(VISIBLE_OBSERVATION_FIELDS),
        "visible_info_fields": list(VISIBLE_INFO_FIELDS),
        "seed_count": len(records),
        "private_future_leak_count": int(leak_count),
        "passed": leak_count == 0 and len(records) == len(seeds),
        "records": records,
    }


def _plan_summary(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        return {}
    steps = []
    for step in plan.get("steps", ()):
        if not isinstance(step, Mapping):
            continue
        steps.append(
            {
                key: _json_ready(step.get(key))
                for key in (
                    "step_index",
                    "action",
                    "known_tsumo",
                    "scenario_id",
                    "tsumo",
                    "predicted_chain_count",
                    "predicted_score",
                    "predicted_attack",
                    "game_over",
                    "placement_cells",
                    "state_fingerprint",
                    "reason",
                )
            }
        )
    return {
        "schema_version": plan.get("schema_version"),
        "plan_id": str(plan.get("plan_id", "")),
        "replan_reason": str(plan.get("replan_reason", "")),
        "prediction_summary": _json_ready(plan.get("prediction_summary", {})),
        "steps": steps,
    }


def _selected_score_summary(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    selected_action = diagnostics.get("selected_action")
    for item in diagnostics.get("scenario_aggregation", ()):
        if not isinstance(item, Mapping):
            continue
        try:
            matches = int(item.get("root_action")) == int(selected_action)
        except (TypeError, ValueError):
            matches = False
        if matches:
            return {
                "root_action": int(selected_action),
                "ranking_key": _json_ready(item.get("ranking_key", ())),
                "score_breakdown": _json_ready(item.get("score_breakdown", {})),
                "evidence": _json_ready(item.get("evidence", {})),
            }
    return {}


def _parity_result(
    *,
    action: int,
    plan: Mapping[str, Any],
    actual: Any,
    board_after: Sequence[Sequence[str]],
) -> dict[str, Any]:
    steps = plan.get("steps", ()) if isinstance(plan, Mapping) else ()
    predicted = steps[0] if steps and isinstance(steps[0], Mapping) else None
    mismatches = []
    if predicted is None:
        mismatches.append("selected_plan_step_missing")
    else:
        comparisons = {
            "action": (predicted.get("action"), int(action)),
            "chain_count": (
                predicted.get("predicted_chain_count"),
                int(actual.chain_count),
            ),
            "score_delta": (predicted.get("predicted_score"), int(actual.score_delta)),
            "game_over": (predicted.get("game_over"), bool(actual.game_over)),
            "board": (predicted.get("predicted_board"), _json_ready(board_after)),
        }
        for name, (expected, observed) in comparisons.items():
            if expected != observed:
                mismatches.append(name)
    return {
        "passed": not mismatches,
        "mismatches": mismatches,
        "predicted_state_fingerprint": (
            "" if predicted is None else str(predicted.get("state_fingerprint", ""))
        ),
        "actual_board_digest": _stable_digest(
            board_after,
            prefix="authoritative-board",
        ),
    }


def _policy_factory(seed: int, profile: str, backend: str = "python") -> Any:
    return make_policy(
        "deep_chain_builder",
        seed=int(seed),
        deep_chain_profile=profile,
        deep_chain_backend=backend,
    )


def run_benchmark_run(
    *,
    seed: int,
    repeat: int,
    profile: str,
    max_steps: int,
    backend: str = "python",
    policy_factory: Callable[[int, str], Any] | None = None,
) -> dict[str, Any]:
    """Execute one safe/no-threat trajectory without a wall-clock cutoff."""

    evaluated_commit = git_commit(REPO_ROOT)
    configuration_sha256 = file_sha256(DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH)
    backend_configuration_sha256 = file_sha256(
        DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH
    )
    simulator = HeadlessPuyoSimulator(seed=int(seed))
    policy = (
        _policy_factory(int(seed), str(profile), str(backend))
        if policy_factory is None
        else policy_factory(int(seed), str(profile))
    )
    records = []
    actual_fire_chain_counts = []
    premature_fire_count = 0
    parity_mismatch_count = 0
    fallback_count = 0
    termination_reason = "turn_limit"
    started_at = time.perf_counter()

    for step_count in range(int(max_steps)):
        mask = legal_action_mask(simulator)
        if not any(mask):
            termination_reason = "no_legal_actions"
            break
        observation = encode_observation(
            simulator,
            step_count=step_count,
            max_steps=max_steps,
        )
        info = {
            "action_mask": mask,
            "action_mask_source": "headless_simulator",
            "score": int(simulator.game.score),
            "step_count": int(step_count),
            "max_steps": int(max_steps),
            "last_chain_end_score": int(simulator.game.last_chain_end_score),
        }
        board_before = _board_snapshot(simulator)
        decision_started = time.perf_counter()
        try:
            action = int(policy.select_action(observation, info))
        except Exception as exc:  # noqa: BLE001 - benchmark must retain failure evidence
            records.append(
                {
                    "turn": int(step_count),
                    "decision_error": {
                        "type": type(exc).__name__,
                        "detail": str(exc),
                    },
                    "elapsed_seconds": float(time.perf_counter() - decision_started),
                    "board_before": board_before,
                }
            )
            termination_reason = "policy_error"
            break
        elapsed = time.perf_counter() - decision_started
        diagnostics = getattr(policy, "tactical_diagnostics", {})
        if not isinstance(diagnostics, Mapping):
            diagnostics = {}
        plan = diagnostics.get("plan", {})
        fallback = diagnostics.get("fallback", {})
        fallback_used = bool(
            isinstance(fallback, Mapping) and fallback.get("used", False)
        )
        fallback_count += int(fallback_used)

        if not 0 <= action < len(mask) or not mask[action]:
            records.append(
                {
                    "turn": int(step_count),
                    "decision_error": {
                        "type": "IllegalAction",
                        "detail": str(action),
                    },
                    "elapsed_seconds": float(elapsed),
                    "board_before": board_before,
                }
            )
            termination_reason = "illegal_action"
            break

        actual = simulator.step(action_to_placement(action), capture_visuals=True)
        board_after = _board_snapshot(simulator)
        parity = _parity_result(
            action=action,
            plan=plan if isinstance(plan, Mapping) else {},
            actual=actual,
            board_after=board_after,
        )
        parity_mismatch_count += int(not parity["passed"])
        chain_count = int(actual.chain_count)
        if chain_count > 0:
            actual_fire_chain_counts.append(chain_count)
        if 1 <= chain_count < 10:
            premature_fire_count += 1

        trace = diagnostics.get("decision_trace", {})
        search = diagnostics.get("search", {})
        backend_diagnostics = diagnostics.get("backend", {})
        record = {
            "turn": int(step_count),
            "action": int(action),
            "elapsed_seconds": float(elapsed),
            "board_before": board_before,
            "board_after": board_after,
            "actual_result": {
                "valid": bool(actual.valid),
                "chain_count": chain_count,
                "score_delta": int(actual.score_delta),
                "game_over": bool(actual.game_over),
            },
            "plan": _plan_summary(plan),
            "selection": {
                "candidate_count": diagnostics.get("candidate_count"),
                "selection_reason": diagnostics.get("selection_reason"),
                "selected_score": _selected_score_summary(diagnostics),
            },
            "search": {
                "deterministic_digest": (
                    search.get("deterministic_digest", "")
                    if isinstance(search, Mapping)
                    else ""
                ),
                "counters": _json_ready(
                    search.get("counters", {}) if isinstance(search, Mapping) else {}
                ),
                "backend": _json_ready(
                    backend_diagnostics
                    if isinstance(backend_diagnostics, Mapping)
                    else {}
                ),
            },
            "flow": {
                "elapsed_seconds": (
                    trace.get("elapsed_seconds", 0.0)
                    if isinstance(trace, Mapping)
                    else 0.0
                ),
                "steps": _json_ready(
                    trace.get("steps", ()) if isinstance(trace, Mapping) else ()
                ),
            },
            "fallback": _json_ready(fallback),
            "parity": parity,
        }
        record["decision_digest"] = _stable_digest(
            {
                "turn": record["turn"],
                "action": record["action"],
                "board_before": record["board_before"],
                "board_after": record["board_after"],
                "actual_result": record["actual_result"],
                "plan": record["plan"],
                "search_digest": record["search"]["deterministic_digest"],
                "fallback": record["fallback"],
            },
            prefix="deep-chain-decision",
        )
        records.append(record)
        if fallback_used:
            termination_reason = "policy_fallback"
            break
        if actual.game_over:
            termination_reason = "game_over"
            break

    action_payload = [record.get("action") for record in records]
    plan_payload = [
        {
            "turn": record.get("turn"),
            "plan_id": record.get("plan", {}).get("plan_id", ""),
            "actions": [
                step.get("action") for step in record.get("plan", {}).get("steps", ())
            ],
            "state_fingerprints": [
                step.get("state_fingerprint")
                for step in record.get("plan", {}).get("steps", ())
            ],
        }
        for record in records
        if "action" in record
    ]
    completed_turns = sum("action" in record for record in records)
    fully_evaluated = termination_reason in {"turn_limit", "game_over"}
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "ticket": "PUYO-189",
        "run_id": run_identity(seed, repeat),
        "evaluated_commit": evaluated_commit,
        "configuration_sha256": configuration_sha256,
        "backend_configuration_sha256": backend_configuration_sha256,
        "seed": int(seed),
        "repeat": int(repeat),
        "profile": str(profile),
        "backend": str(backend),
        "environment": "safe_no_threat",
        "max_steps": int(max_steps),
        "completed_turns": int(completed_turns),
        "fully_evaluated": bool(fully_evaluated),
        "termination_reason": termination_reason,
        "elapsed_seconds": float(time.perf_counter() - started_at),
        "maximum_actual_fire_chain_count": max(actual_fire_chain_counts, default=0),
        "actual_fire_chain_counts": actual_fire_chain_counts,
        "premature_fire_count": int(premature_fire_count),
        "game_over": bool(simulator.game.game_over),
        "simulator_parity_mismatch_count": int(parity_mismatch_count),
        "fallback_count": int(fallback_count),
        "action_digest": _stable_digest(
            action_payload,
            prefix="deep-chain-run-actions",
        ),
        "plan_digest": _stable_digest(
            plan_payload,
            prefix="deep-chain-run-plans",
        ),
        "trajectory_digest": _stable_digest(
            [record.get("decision_digest") for record in records],
            prefix="deep-chain-run-trajectory",
        ),
        "final_board_digest": _stable_digest(
            _board_snapshot(simulator),
            prefix="deep-chain-final-board",
        ),
        "records": records,
    }


def run_identity(seed: int, repeat: int) -> str:
    return f"seed-{int(seed):03d}-repeat-{int(repeat):02d}"


def expected_run_identities(contract: Any) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run_identity(seed, repeat),
            "seed": int(seed),
            "repeat": int(repeat),
        }
        for seed in range(
            contract.seed_start, contract.seed_start + contract.seed_count
        )
        for repeat in range(1, contract.repeats_per_seed + 1)
    ]


def _run_path(output_dir: Path, seed: int, repeat: int) -> Path:
    return output_dir / "runs" / f"{run_identity(seed, repeat)}.json"


def _load_completed_runs(output_dir: Path, contract: Any) -> list[dict[str, Any]]:
    runs = []
    for identity in expected_run_identities(contract):
        path = _run_path(output_dir, identity["seed"], identity["repeat"])
        if not path.is_file():
            continue
        payload = _read_json(path)
        if payload.get("schema_version") != RUN_SCHEMA_VERSION:
            raise ValueError(f"unsupported run schema: {path}")
        if payload.get("run_id") != identity["run_id"]:
            raise ValueError(f"run identity mismatch: {path}")
        runs.append(payload)
    return runs


def run_pending_benchmark(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    backend: str,
    max_runs: int | None = None,
) -> dict[str, Any]:
    """Run pending canonical identities, writing each result immediately."""

    if backend != CANONICAL_BACKEND:
        raise ValueError("canonical deep-chain benchmark requires backend=native")
    if max_runs is not None and max_runs <= 0:
        raise ValueError("max_runs must be positive when provided")
    target = Path(output_dir)
    config = load_deep_chain_builder_config()
    contract = config.benchmark
    completed_now = 0
    for identity in expected_run_identities(contract):
        path = _run_path(target, identity["seed"], identity["repeat"])
        if path.is_file():
            continue
        if max_runs is not None and completed_now >= max_runs:
            break
        payload = run_benchmark_run(
            seed=identity["seed"],
            repeat=identity["repeat"],
            profile="reference",
            max_steps=contract.max_steps,
            backend=backend,
        )
        _write_json(path, payload)
        completed_now += 1
        print(
            json.dumps(
                {
                    "run_id": payload["run_id"],
                    "termination_reason": payload["termination_reason"],
                    "maximum_actual_fire_chain_count": payload[
                        "maximum_actual_fire_chain_count"
                    ],
                    "elapsed_seconds": payload["elapsed_seconds"],
                    "path": str(path),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    return finalize_evidence(target)


def _aggregate_latency(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [
        float(record["elapsed_seconds"])
        for run in runs
        for record in run.get("records", ())
        if "action" in record and record.get("elapsed_seconds") is not None
    ]
    return {
        "sample_count": len(values),
        "mean_seconds": _mean(values),
        "p50_seconds": percentile(values, 0.50),
        "p95_seconds": percentile(values, 0.95),
        "max_seconds": max(values) if values else None,
        "wall_clock_mode": "observational_no_timeout",
    }


def _aggregate_search(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counter_values: dict[str, list[float]] = defaultdict(list)
    flow_values: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        for record in run.get("records", ()):
            for key, value in record.get("search", {}).get("counters", {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    counter_values[str(key)].append(float(value))
            for step in record.get("flow", {}).get("steps", ()):
                if isinstance(step, Mapping) and step.get("step_id"):
                    flow_values[str(step["step_id"])].append(
                        float(step.get("elapsed_seconds", 0.0))
                    )
    counters = {
        key: {
            "sample_count": len(values),
            "total": sum(values),
            "mean": _mean(values),
            "p50": percentile(values, 0.50),
            "p95": percentile(values, 0.95),
            "max": max(values) if values else None,
        }
        for key, values in sorted(counter_values.items())
    }
    hits = sum(counter_values.get("transposition_hits", ()))
    generated = sum(counter_values.get("generated_nodes", ()))
    return {
        "counters": counters,
        "cache_hit_rate_per_generated_node": (
            None if generated <= 0 else float(hits / generated)
        ),
        "flow_step_latency_seconds": {
            key: {
                "sample_count": len(values),
                "mean": _mean(values),
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
                "max": max(values) if values else None,
            }
            for key, values in sorted(flow_values.items())
        },
    }


def _determinism_summary(
    runs: Sequence[Mapping[str, Any]],
    *,
    seeds: Sequence[int],
) -> dict[str, Any]:
    by_seed: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for run in runs:
        by_seed[int(run["seed"])].append(run)
    records = []
    for seed in seeds:
        pair = sorted(by_seed.get(int(seed), ()), key=lambda item: int(item["repeat"]))
        action_digests = [str(item.get("action_digest", "")) for item in pair]
        plan_digests = [str(item.get("plan_digest", "")) for item in pair]
        trajectory_digests = [str(item.get("trajectory_digest", "")) for item in pair]
        complete_pair = len(pair) == 2
        matches = (
            complete_pair
            and len(set(action_digests)) == 1
            and len(set(plan_digests)) == 1
            and len(set(trajectory_digests)) == 1
        )
        records.append(
            {
                "seed": int(seed),
                "repeat_count": len(pair),
                "complete_pair": complete_pair,
                "action_digests": action_digests,
                "plan_digests": plan_digests,
                "trajectory_digests": trajectory_digests,
                "matches": matches,
            }
        )
    return {
        "seed_count": len(records),
        "complete_pair_count": sum(record["complete_pair"] for record in records),
        "matching_pair_count": sum(record["matches"] for record in records),
        "passed": bool(records) and all(record["matches"] for record in records),
        "records": records,
    }


def _check(name: str, passed: bool, actual: Any, expected: Any) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "expected": expected,
    }


def _default_gui_qa() -> dict[str, Any]:
    checks = [
        "n_turn_ghost_matches_selected_action",
        "selection_reason_and_decision_trace_visible",
        "replan_replaces_stale_ghosts",
        "overlay_toggle_preserves_policy_decision",
    ]
    return {
        "schema_version": GUI_QA_SCHEMA_VERSION,
        "ticket": "PUYO-189",
        "automated": {
            "status": "not_recorded",
            "passed": False,
            "command": "",
            "notes": "",
        },
        "manual": {
            "status": "pending",
            "reviewer": None,
            "checks": [{"name": name, "passed": None} for name in checks],
            "notes": "A normal-window python main.py review is still required.",
        },
        "passed": False,
    }


def record_gui_qa(
    output_dir: str | Path,
    *,
    automated_passed: bool,
    automated_command: str,
    manual_status: str,
    reviewer: str | None,
    notes: str,
) -> dict[str, Any]:
    if manual_status not in {"pending", "passed", "failed"}:
        raise ValueError("manual_status must be pending, passed, or failed")
    if manual_status == "passed" and not reviewer:
        raise ValueError("reviewer is required when manual GUI QA passes")
    check_names = [
        "n_turn_ghost_matches_selected_action",
        "selection_reason_and_decision_trace_visible",
        "replan_replaces_stale_ghosts",
        "overlay_toggle_preserves_policy_decision",
    ]
    manual_value = (
        True
        if manual_status == "passed"
        else (False if manual_status == "failed" else None)
    )
    payload = {
        "schema_version": GUI_QA_SCHEMA_VERSION,
        "ticket": "PUYO-189",
        "recorded_at_utc": utc_timestamp(),
        "automated": {
            "status": "passed" if automated_passed else "failed",
            "passed": bool(automated_passed),
            "command": automated_command,
            "notes": notes,
        },
        "manual": {
            "status": manual_status,
            "reviewer": reviewer,
            "checks": [{"name": name, "passed": manual_value} for name in check_names],
            "notes": notes,
        },
        "passed": bool(automated_passed and manual_status == "passed"),
    }
    _write_json(Path(output_dir) / "gui_qa.json", payload)
    return payload


def _tracked_worktree_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and not result.stdout.strip()


def _lineage_payload(config: Any) -> dict[str, Any]:
    backend_config = load_long_horizon_backend_config()
    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "ticket": "PUYO-189",
        "evaluated_commit": git_commit(REPO_ROOT),
        "tracked_worktree_clean_at_finalize": _tracked_worktree_clean(),
        "configuration": {
            "path": str(DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH.relative_to(REPO_ROOT)),
            "sha256": file_sha256(DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH),
            "config_version": config.config_version,
            "profile_id": config.profile("reference").profile_id,
            "backend": {
                "path": str(
                    DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH.relative_to(REPO_ROOT)
                ),
                "sha256": file_sha256(DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH),
                "config_version": backend_config.config_version,
                "required_backend": CANONICAL_BACKEND,
            },
        },
        "policy": {
            "policy_id": config.policy_id,
            "profile": config.profile("reference").to_dict(),
            "environment": config.benchmark.environment,
        },
        "reference_attribution": {
            "source_url": REFERENCE_SOURCE_URL,
            "source_commit": REFERENCE_SOURCE_COMMIT,
            "license": "MIT License",
            "copyright": "Copyright (c) 2023 citrus610",
            "usage": "architectural input; no reference implementation code copied",
        },
        "reproduction": {
            "canonical_run": (
                ".venv/bin/python -m eval.deep_chain_builder_benchmark run "
                "--backend native "
                "--output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline"
            ),
            "resume_one_run": (
                ".venv/bin/python -m eval.deep_chain_builder_benchmark run "
                "--backend native --max-runs 1 --output-dir "
                "docs/benchmarks/puyo-189-deep-chain-builder-baseline"
            ),
            "verify": (
                ".venv/bin/python -m eval.deep_chain_builder_benchmark verify "
                "--output-dir docs/benchmarks/puyo-189-deep-chain-builder-baseline"
            ),
        },
        "promotion_constraints": {
            "formal_version_created": False,
            "git_tag_created": False,
            "stable_promoted": False,
            "champion_promoted": False,
        },
    }


def _preflight_worker(
    queue: Any,
    seed: int,
    profile: str,
    backend: str,
    max_steps: int,
) -> None:
    observation, info = _initial_observation_and_info(seed, max_steps=max_steps)
    started = time.perf_counter()
    try:
        policy = _policy_factory(seed, profile, backend)
        action = int(policy.select_action(observation, info))
        diagnostics = getattr(policy, "tactical_diagnostics", {})
        queue.put(
            {
                "completed": True,
                "action": action,
                "elapsed_seconds": float(time.perf_counter() - started),
                "fallback": _json_ready(
                    diagnostics.get("fallback", {})
                    if isinstance(diagnostics, Mapping)
                    else {}
                ),
                "search": _json_ready(
                    diagnostics.get("search", {})
                    if isinstance(diagnostics, Mapping)
                    else {}
                ),
                "decision_trace": _json_ready(
                    diagnostics.get("decision_trace", {})
                    if isinstance(diagnostics, Mapping)
                    else {}
                ),
                "backend": _json_ready(
                    diagnostics.get("backend", {})
                    if isinstance(diagnostics, Mapping)
                    else {}
                ),
            }
        )
    except BaseException as exc:  # noqa: BLE001 - child must report diagnostic
        queue.put(
            {
                "completed": False,
                "error": {"type": type(exc).__name__, "detail": str(exc)},
                "elapsed_seconds": float(time.perf_counter() - started),
            }
        )


def run_preflight(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    backend: str,
    seed: int | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Run one non-canonical supervised decision latency probe."""

    if backend != CANONICAL_BACKEND:
        raise ValueError("reference preflight requires backend=native")
    config = load_deep_chain_builder_config()
    contract = config.benchmark
    selected_seed = contract.seed_start if seed is None else int(seed)
    if timeout_seconds < contract.maximum_decision_p95_seconds:
        raise ValueError(
            "preflight timeout must be at least the locked decision p95 threshold"
        )
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(
        target=_preflight_worker,
        args=(queue, selected_seed, "reference", backend, contract.max_steps),
    )
    started = time.perf_counter()
    process.start()
    process.join(timeout=float(timeout_seconds))
    timed_out = process.is_alive()
    if timed_out:
        process.terminate()
        process.join()
    observed_elapsed = time.perf_counter() - started
    result = None
    if not timed_out:
        try:
            result = queue.get(timeout=1.0)
        except Empty:
            result = None
    queue.close()
    completed = bool(result and result.get("completed"))
    measured_elapsed = float(result["elapsed_seconds"]) if completed else None
    latency_lower_bound = float(timeout_seconds) if timed_out else measured_elapsed
    performance_passed = bool(
        completed
        and measured_elapsed is not None
        and measured_elapsed <= contract.maximum_decision_p95_seconds
    )
    payload = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "ticket": "PUYO-189",
        "created_at_utc": utc_timestamp(),
        "canonical_quality_evidence": False,
        "purpose": "bounded latency diagnosis before the canonical resumable run",
        "seed": int(selected_seed),
        "evaluated_commit": git_commit(REPO_ROOT),
        "configuration_sha256": file_sha256(DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH),
        "backend_configuration_sha256": file_sha256(
            DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH
        ),
        "backend": backend,
        "profile": config.profile("reference").to_dict(),
        "timeout_seconds": float(timeout_seconds),
        "timed_out": bool(timed_out),
        "child_exit_code": process.exitcode,
        "supervisor_elapsed_seconds": float(observed_elapsed),
        "decision_elapsed_seconds": measured_elapsed,
        "decision_latency_lower_bound_seconds": latency_lower_bound,
        "locked_maximum_decision_p95_seconds": float(
            contract.maximum_decision_p95_seconds
        ),
        "performance_gate_passed": performance_passed,
        "result": result,
        "interpretation": (
            "reference decision exceeded the diagnostic budget; quality was not "
            "inferred from the terminated process"
            if timed_out
            else "reference decision completed inside the diagnostic budget"
        ),
    }
    _write_json(Path(output_dir) / "preflight.json", payload)
    return payload


def _failure_taxonomy(
    *,
    executed_runs: int,
    expected_runs: int,
    parity_mismatches: int,
    fallback_count: int,
    latency: Mapping[str, Any],
    preflight: Mapping[str, Any] | None,
) -> dict[str, Any]:
    preflight_timeout = bool(preflight and preflight.get("timed_out"))
    no_complete_search = executed_runs == 0
    return {
        "search": {
            "status": "not_evaluable" if no_complete_search else "evaluated",
            "evidence": (
                "no canonical trajectories completed"
                if no_complete_search
                else f"{executed_runs} canonical trajectories available"
            ),
        },
        "evaluator": {
            "status": "not_evaluable" if no_complete_search else "evaluated",
            "evidence": (
                "search did not emit candidates for quality calibration"
                if no_complete_search
                else "actual-fire results are available in run artifacts"
            ),
        },
        "flow": {
            "status": "not_evaluable" if no_complete_search else "evaluated",
            "fallback_count": int(fallback_count),
        },
        "simulator": {
            "status": "not_evaluable"
            if no_complete_search
            else ("mismatch" if parity_mismatches else "matched"),
            "parity_mismatch_count": int(parity_mismatches),
        },
        "performance": {
            "status": "confirmed_fail"
            if preflight_timeout
            else ("evaluated" if latency.get("sample_count", 0) else "not_evaluable"),
            "preflight_timed_out": preflight_timeout,
            "decision_p95_seconds": latency.get("p95_seconds"),
            "expected_run_count": int(expected_runs),
        },
    }


def _report(summary: Mapping[str, Any], lineage: Mapping[str, Any]) -> str:
    aggregate = summary["aggregate"]
    gates = summary["gates"]
    preflight = summary.get("preflight", {})
    baseline = summary["baseline_decision"]

    def display(value: Any) -> str:
        if value is None:
            return "not evaluable"
        if isinstance(value, float):
            return f"{value:.6f}"
        return str(value)

    lines = [
        "# PUYO-189 Deep-chain baseline evaluation",
        "",
        f"Decision: **{baseline['decision']}**",
        "",
        (
            "The baseline was not promoted. A FAIL result is an evaluation outcome, "
            "not evidence that the locked thresholds were changed."
        ),
        "",
        "## Coverage",
        "",
        f"- Expected runs: {aggregate['expected_run_count']}",
        f"- Executed canonical runs: {aggregate['executed_run_count']}",
        f"- Fully evaluated runs: {aggregate['fully_evaluated_run_count']}",
        f"- Preflight timeout: {preflight.get('timed_out')}",
        "",
        "## Metrics",
        "",
        f"- Mean maximum actual fire chain: {display(aggregate['mean_maximum_actual_fire_chain_count'])}",
        f"- Premature fires: {aggregate['premature_fire_count']}",
        f"- Game overs: {aggregate['game_over_count']}",
        f"- Simulator parity mismatches: {aggregate['simulator_parity_mismatch_count']}",
        f"- Private future leaks: {aggregate['private_future_leak_count']}",
        f"- Decision p95 seconds: {display(aggregate['latency']['p95_seconds'])}",
        f"- Preflight latency lower bound seconds: {display(preflight.get('decision_latency_lower_bound_seconds'))}",
        "",
        "## Gate results",
        "",
    ]
    for name, gate in gates.items():
        lines.append(f"- {name}: {'PASS' if gate['passed'] else 'FAIL'}")
        for check in gate.get("checks", ()):
            lines.append(
                f"  - {check['name']}: {'PASS' if check['passed'] else 'FAIL'} "
                f"(actual={display(check['actual'])}, expected={display(check['expected'])})"
            )
    lines.extend(
        [
            "",
            "## Failure classification",
            "",
        ]
    )
    for category, evidence in summary["failure_taxonomy"].items():
        lines.append(f"- {category}: {evidence['status']}")
    lines.extend(
        [
            "",
            "## Lineage and reproduction",
            "",
            f"- Evaluated commit: `{lineage['evaluated_commit']}`",
            f"- Config checksum: `{lineage['configuration']['sha256']}`",
            f"- Reference source: {lineage['reference_attribution']['source_url']}",
            f"- Canonical resume: `{lineage['reproduction']['resume_one_run']}`",
            f"- Verification: `{lineage['reproduction']['verify']}`",
            "",
            "## Human review",
            "",
            (
                "Review the run index, failure taxonomy, preflight evidence, latency "
                "hotspot evidence, and GUI QA state before deciding whether to open a "
                "separate search/evaluator/performance task. Do not promote this result "
                "to a formal version, tag, stable policy, or champion."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def finalize_evidence(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    config = load_deep_chain_builder_config()
    backend_config = load_long_horizon_backend_config()
    contract = config.benchmark
    seeds = list(range(contract.seed_start, contract.seed_start + contract.seed_count))
    expected = expected_run_identities(contract)
    runs = _load_completed_runs(target, contract)
    by_id = {str(run["run_id"]): run for run in runs}
    preflight_path = target / "preflight.json"
    preflight = _read_json(preflight_path) if preflight_path.is_file() else None
    gui_path = target / "gui_qa.json"
    if not gui_path.is_file():
        _write_json(gui_path, _default_gui_qa())
    gui_qa = _read_json(gui_path)
    future_isolation = audit_future_isolation(seeds, max_steps=contract.max_steps)
    _write_json(target / "future_isolation.json", future_isolation)

    run_index_records = []
    for identity in expected:
        run = by_id.get(identity["run_id"])
        run_index_records.append(
            {
                **identity,
                "status": "executed" if run is not None else "not_executed",
                "path": (
                    str(Path("runs") / f"{identity['run_id']}.json")
                    if run is not None
                    else None
                ),
                "reason": (
                    None
                    if run is not None
                    else (
                        "preflight_reference_decision_timeout"
                        if preflight and preflight.get("timed_out")
                        else "canonical_run_pending"
                    )
                ),
            }
        )
    run_index = {
        "schema_version": RUN_INDEX_SCHEMA_VERSION,
        "expected_run_count": len(expected),
        "executed_run_count": len(runs),
        "all_identities_accounted_for": len(run_index_records) == len(expected),
        "records": run_index_records,
    }
    _write_json(target / "run_index.json", run_index)

    maximum_chains = [int(run["maximum_actual_fire_chain_count"]) for run in runs]
    premature_fire_count = sum(int(run["premature_fire_count"]) for run in runs)
    game_over_count = sum(bool(run["game_over"]) for run in runs)
    parity_mismatches = sum(int(run["simulator_parity_mismatch_count"]) for run in runs)
    fallback_count = sum(int(run.get("fallback_count", 0)) for run in runs)
    fully_evaluated_count = sum(bool(run.get("fully_evaluated")) for run in runs)
    lineage_commit = git_commit(REPO_ROOT)
    configuration_sha256 = file_sha256(DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH)
    backend_configuration_sha256 = file_sha256(
        DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH
    )
    matching_lineage_count = sum(
        run.get("evaluated_commit") == lineage_commit
        and run.get("configuration_sha256") == configuration_sha256
        and run.get("backend_configuration_sha256")
        == backend_configuration_sha256
        and run.get("backend") == CANONICAL_BACKEND
        for run in runs
    )
    latency = _aggregate_latency(runs)
    search = _aggregate_search(runs)
    determinism = _determinism_summary(runs, seeds=seeds)

    coverage_checks = [
        _check(
            "expected_run_identities_present",
            len(run_index_records) == len(expected),
            len(run_index_records),
            len(expected),
        ),
        _check(
            "canonical_runs_executed",
            len(runs) == len(expected),
            len(runs),
            len(expected),
        ),
        _check(
            "canonical_runs_fully_evaluated",
            fully_evaluated_count == len(expected),
            fully_evaluated_count,
            len(expected),
        ),
        _check(
            "run_lineage_consistent",
            matching_lineage_count == len(expected),
            matching_lineage_count,
            len(expected),
        ),
    ]
    mean_maximum_chain = _mean(maximum_chains)
    quality_checks = [
        _check(
            "coverage_complete",
            all(check["passed"] for check in coverage_checks),
            fully_evaluated_count,
            len(expected),
        ),
        _check(
            "mean_maximum_actual_fire_chain_count",
            mean_maximum_chain is not None
            and mean_maximum_chain >= contract.minimum_mean_actual_fire_chain_count,
            mean_maximum_chain,
            f">= {contract.minimum_mean_actual_fire_chain_count}",
        ),
        _check(
            "premature_fire_count",
            premature_fire_count <= contract.maximum_premature_fires,
            premature_fire_count,
            f"<= {contract.maximum_premature_fires}",
        ),
        _check(
            "game_over_count",
            game_over_count <= contract.maximum_game_overs,
            game_over_count,
            f"<= {contract.maximum_game_overs}",
        ),
        _check("fallback_count", fallback_count == 0, fallback_count, 0),
    ]
    parity_checks = [
        _check(
            "coverage_complete",
            fully_evaluated_count == len(expected),
            fully_evaluated_count,
            len(expected),
        ),
        _check(
            "simulator_parity_mismatch_count",
            parity_mismatches <= contract.maximum_simulator_parity_mismatches,
            parity_mismatches,
            f"<= {contract.maximum_simulator_parity_mismatches}",
        ),
    ]
    isolation_checks = [
        _check(
            "seed_coverage",
            future_isolation["seed_count"] == contract.seed_count,
            future_isolation["seed_count"],
            contract.seed_count,
        ),
        _check(
            "private_future_leak_count",
            future_isolation["private_future_leak_count"]
            <= contract.maximum_private_future_leaks,
            future_isolation["private_future_leak_count"],
            f"<= {contract.maximum_private_future_leaks}",
        ),
    ]
    determinism_checks = [
        _check(
            "complete_repeat_pairs",
            determinism["complete_pair_count"] == contract.seed_count,
            determinism["complete_pair_count"],
            contract.seed_count,
        ),
        _check(
            "matching_action_and_plan_digests",
            determinism["passed"] if contract.require_repeat_digest_match else True,
            determinism["matching_pair_count"],
            contract.seed_count,
        ),
    ]
    preflight_failed = bool(preflight and not preflight.get("performance_gate_passed"))
    p95 = latency["p95_seconds"]
    performance_checks = [
        _check(
            "canonical_latency_coverage",
            latency["sample_count"] > 0 and fully_evaluated_count == len(expected),
            latency["sample_count"],
            f"> 0 across {len(expected)} fully evaluated runs",
        ),
        _check(
            "preflight_within_gate",
            not preflight_failed,
            None
            if preflight is None
            else preflight.get("decision_latency_lower_bound_seconds"),
            f"<= {contract.maximum_decision_p95_seconds}",
        ),
        _check(
            "decision_p95_seconds",
            p95 is not None and p95 <= contract.maximum_decision_p95_seconds,
            p95,
            f"<= {contract.maximum_decision_p95_seconds}",
        ),
    ]
    gui_checks = [
        _check(
            "automated_gui_contract",
            bool(gui_qa.get("automated", {}).get("passed")),
            gui_qa.get("automated", {}).get("status"),
            "passed",
        ),
        _check(
            "manual_gui_review",
            gui_qa.get("manual", {}).get("status") == "passed",
            gui_qa.get("manual", {}).get("status"),
            "passed",
        ),
    ]

    def gate(checks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "passed": all(bool(check["passed"]) for check in checks),
            "checks": list(checks),
        }

    gates = {
        "coverage": gate(coverage_checks),
        "quality": gate(quality_checks),
        "simulator_parity": gate(parity_checks),
        "future_isolation": gate(isolation_checks),
        "determinism": gate(determinism_checks),
        "performance": gate(performance_checks),
        "gui_human_qa": gate(gui_checks),
    }
    accepted = all(item["passed"] for item in gates.values())
    failure_taxonomy = _failure_taxonomy(
        executed_runs=len(runs),
        expected_runs=len(expected),
        parity_mismatches=parity_mismatches,
        fallback_count=fallback_count,
        latency=latency,
        preflight=preflight,
    )
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": "PUYO-189",
        "generated_at_utc": utc_timestamp(),
        "configuration": {
            "policy_id": config.policy_id,
            "profile": config.profile("reference").to_dict(),
            "backend": {
                **backend_config.to_dict(),
                "selected_backend": CANONICAL_BACKEND,
                "configuration_sha256": backend_configuration_sha256,
            },
            "benchmark": contract.to_dict(),
        },
        "aggregate": {
            "expected_run_count": len(expected),
            "executed_run_count": len(runs),
            "fully_evaluated_run_count": int(fully_evaluated_count),
            "mean_maximum_actual_fire_chain_count": mean_maximum_chain,
            "premature_fire_count": int(premature_fire_count),
            "game_over_count": int(game_over_count),
            "simulator_parity_mismatch_count": int(parity_mismatches),
            "private_future_leak_count": int(
                future_isolation["private_future_leak_count"]
            ),
            "fallback_count": int(fallback_count),
            "matching_lineage_run_count": int(matching_lineage_count),
            "latency": latency,
            "search": search,
        },
        "determinism": determinism,
        "preflight": preflight,
        "gui_qa": {
            "passed": bool(gui_qa.get("passed")),
            "automated_status": gui_qa.get("automated", {}).get("status"),
            "manual_status": gui_qa.get("manual", {}).get("status"),
        },
        "gates": gates,
        "failure_taxonomy": failure_taxonomy,
        "baseline_decision": {
            "decision": "PASS" if accepted else "FAIL",
            "accepted_as_experimental_baseline": bool(accepted),
            "promotion_performed": False,
            "next_human_decision": (
                "review failure evidence before creating any corrective task"
                if not accepted
                else "review experimental baseline acceptance"
            ),
        },
    }
    summary["summary_digest"] = _stable_digest(
        {
            "configuration": summary["configuration"],
            "aggregate": summary["aggregate"],
            "determinism": summary["determinism"],
            "gates": summary["gates"],
            "failure_taxonomy": summary["failure_taxonomy"],
            "baseline_decision": summary["baseline_decision"],
        },
        prefix="puyo-189-benchmark-summary",
    )
    _write_json(target / "benchmark_summary.json", summary)

    configuration = config.to_dict()
    configuration["configuration_sha256"] = file_sha256(
        DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH
    )
    configuration["backend"] = {
        **backend_config.to_dict(),
        "selected_backend": CANONICAL_BACKEND,
        "configuration_sha256": backend_configuration_sha256,
    }
    _write_json(target / "configuration.json", configuration)
    lineage = _lineage_payload(config)
    _write_json(target / "lineage.json", lineage)
    (target / "benchmark_report.md").write_text(
        _report(summary, lineage),
        encoding="utf-8",
    )

    artifact_paths = [
        path
        for path in sorted(target.rglob("*"))
        if path.is_file() and path.name != "benchmark_manifest.json"
    ]
    artifacts = [
        describe_artifact(
            path,
            run_dir=target,
            role=("canonical_run" if path.parent.name == "runs" else path.stem),
        )
        for path in artifact_paths
    ]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ticket": "PUYO-189",
        "created_at_utc": utc_timestamp(),
        "evaluated_commit": lineage["evaluated_commit"],
        "configuration_sha256": lineage["configuration"]["sha256"],
        "expected_run_count": len(expected),
        "executed_run_count": len(runs),
        "summary_digest": summary["summary_digest"],
        "baseline_decision": summary["baseline_decision"],
        "artifacts": artifacts,
    }
    manifest["manifest_digest"] = _stable_digest(
        {
            "evaluated_commit": manifest["evaluated_commit"],
            "configuration_sha256": manifest["configuration_sha256"],
            "expected_run_count": manifest["expected_run_count"],
            "executed_run_count": manifest["executed_run_count"],
            "summary_digest": manifest["summary_digest"],
            "baseline_decision": manifest["baseline_decision"],
            "artifacts": manifest["artifacts"],
        },
        prefix="puyo-189-benchmark-manifest",
    )
    _write_json(target / "benchmark_manifest.json", manifest)
    return summary


def verify_evidence(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> list[str]:
    target = Path(output_dir)
    issues = []
    manifest_path = target / "benchmark_manifest.json"
    if not manifest_path.is_file():
        return ["benchmark_manifest.json is missing"]
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        issues.append("benchmark manifest schema mismatch")
    for artifact in manifest.get("artifacts", ()):
        path = target / str(artifact.get("path", ""))
        if not path.is_file():
            issues.append(f"artifact is missing: {path}")
        elif artifact.get("sha256") != file_sha256(path):
            issues.append(f"artifact checksum mismatch: {path}")
    if manifest.get("configuration_sha256") != file_sha256(
        DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH
    ):
        issues.append("configuration checksum mismatch")

    summary_path = target / "benchmark_summary.json"
    run_index_path = target / "run_index.json"
    if summary_path.is_file():
        summary = _read_json(summary_path)
        if summary.get("schema_version") != SUMMARY_SCHEMA_VERSION:
            issues.append("benchmark summary schema mismatch")
        expected_digest = _stable_digest(
            {
                "configuration": summary.get("configuration"),
                "aggregate": summary.get("aggregate"),
                "determinism": summary.get("determinism"),
                "gates": summary.get("gates"),
                "failure_taxonomy": summary.get("failure_taxonomy"),
                "baseline_decision": summary.get("baseline_decision"),
            },
            prefix="puyo-189-benchmark-summary",
        )
        if summary.get("summary_digest") != expected_digest:
            issues.append("benchmark summary digest mismatch")
        if manifest.get("summary_digest") != summary.get("summary_digest"):
            issues.append("manifest and summary digests disagree")
        accepted = bool(
            summary.get("baseline_decision", {}).get(
                "accepted_as_experimental_baseline"
            )
        )
        if accepted and not all(
            bool(gate.get("passed")) for gate in summary.get("gates", {}).values()
        ):
            issues.append("accepted baseline has a failed gate")
    else:
        issues.append("benchmark_summary.json is missing")

    if run_index_path.is_file():
        run_index = _read_json(run_index_path)
        config = load_deep_chain_builder_config()
        expected_count = config.benchmark.seed_count * config.benchmark.repeats_per_seed
        if run_index.get("expected_run_count") != expected_count:
            issues.append("run index expected count mismatch")
        if len(run_index.get("records", ())) != expected_count:
            issues.append("run index identity coverage mismatch")
    else:
        issues.append("run_index.json is missing")

    expected_manifest_digest = _stable_digest(
        {
            "evaluated_commit": manifest.get("evaluated_commit"),
            "configuration_sha256": manifest.get("configuration_sha256"),
            "expected_run_count": manifest.get("expected_run_count"),
            "executed_run_count": manifest.get("executed_run_count"),
            "summary_digest": manifest.get("summary_digest"),
            "baseline_decision": manifest.get("baseline_decision"),
            "artifacts": manifest.get("artifacts"),
        },
        prefix="puyo-189-benchmark-manifest",
    )
    if manifest.get("manifest_digest") != expected_manifest_digest:
        issues.append("benchmark manifest digest mismatch")
    return issues


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    preflight.add_argument("--backend", choices=(CANONICAL_BACKEND,), required=True)
    preflight.add_argument("--seed", type=int)
    preflight.add_argument("--timeout-seconds", type=float, default=5.0)

    run = subparsers.add_parser("run")
    run.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--backend", choices=(CANONICAL_BACKEND,), required=True)
    run.add_argument(
        "--max-runs",
        type=int,
        help="Run at most this many pending identities; omitted means all.",
    )

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)

    gui = subparsers.add_parser("record-gui-qa")
    gui.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    gui.add_argument("--automated-passed", action="store_true")
    gui.add_argument("--automated-command", default="")
    gui.add_argument(
        "--manual-status",
        choices=("pending", "passed", "failed"),
        default="pending",
    )
    gui.add_argument("--reviewer")
    gui.add_argument("--notes", default="")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "preflight":
        result = run_preflight(
            args.output_dir,
            backend=args.backend,
            seed=args.seed,
            timeout_seconds=args.timeout_seconds,
        )
    elif args.command == "run":
        result = run_pending_benchmark(
            args.output_dir,
            backend=args.backend,
            max_runs=args.max_runs,
        )
    elif args.command == "finalize":
        result = finalize_evidence(args.output_dir)
    elif args.command == "record-gui-qa":
        result = record_gui_qa(
            args.output_dir,
            automated_passed=args.automated_passed,
            automated_command=args.automated_command,
            manual_status=args.manual_status,
            reviewer=args.reviewer,
            notes=args.notes,
        )
    else:
        issues = verify_evidence(args.output_dir)
        result = {"passed": not issues, "issues": issues}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if not issues else 1
    print(json.dumps(_json_ready(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
