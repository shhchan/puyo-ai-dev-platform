"""PUYO-204 deep-chain native quality, performance, and acceptance evidence.

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
import os
import platform
import resource
import subprocess
import sys
import tempfile
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
    DeepChainBuilderPolicy,
    build_visible_runtime_input,
    load_deep_chain_builder_config,
)
from agents.deep_chain_native import NativeDeepChainBackend
from agents.deep_chain_search_backend import (
    DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH,
    NativeLongHorizonSearchBackend,
    load_long_horizon_backend_config,
)
from puyo_env.actions import action_to_placement, legal_action_mask
from puyo_env.obs import encode_observation
from selfplay.policies import make_policy
from src.core.constants import GRID_HEIGHT, GRID_WIDTH
from src.core.headless import HeadlessPuyoSimulator
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

RUN_SCHEMA_VERSION = "puyo.deep_chain_builder.benchmark_run.v2"
RUN_INDEX_SCHEMA_VERSION = "puyo.deep_chain_builder.run_index.v2"
SUMMARY_SCHEMA_VERSION = "puyo.deep_chain_builder.benchmark_summary.v2"
MANIFEST_SCHEMA_VERSION = "puyo.deep_chain_builder.benchmark_manifest.v2"
PREFLIGHT_SCHEMA_VERSION = "puyo.deep_chain_builder.preflight.v2"
FUTURE_ISOLATION_SCHEMA_VERSION = "puyo.deep_chain_builder.future_isolation.v2"
GUI_QA_SCHEMA_VERSION = "puyo.deep_chain_builder.gui_qa.v2"
LINEAGE_SCHEMA_VERSION = "puyo.deep_chain_builder.lineage.v2"
BUILD_PROVENANCE_SCHEMA_VERSION = "puyo.deep_chain_builder.build_provenance.v1"
HISTORICAL_COMPARISON_SCHEMA_VERSION = "puyo.deep_chain_builder.comparison.v1"

TICKET = "PUYO-204"
PREVIOUS_TICKET = "PUYO-189"
DEFAULT_OUTPUT_DIR = Path("docs/benchmarks/puyo-204-deep-chain-native-baseline")
PREVIOUS_OUTPUT_DIR = Path("docs/benchmarks/puyo-189-deep-chain-builder-baseline")
REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SOURCE_COMMIT = "dea210bcd92965ae08fbc311f23565b0fab6dbbb"
REFERENCE_SOURCE_URL = (
    f"https://github.com/citrus610/ama/tree/{REFERENCE_SOURCE_COMMIT}"
)
CANONICAL_BACKEND = "native"
CANONICAL_EXECUTION_MODE = "scenario-6"
DIAGNOSTIC_EXECUTION_MODE = "oracle-1"
# PUYO-204 canonical evidence is intentionally insulated from runtime/UI
# experiments, even if the interactive default changes in a later task.
CANONICAL_TARGET_CHAIN_COUNT = 6
# PUYO-189's quality contract calls every actual fire below the locked
# ten-chain quality floor premature, independently of the search target.
CANONICAL_PREMATURE_FIRE_CHAIN_COUNT = 10


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(rendered)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, target)


def _read_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def _protect_historical_output(output_dir: str | Path) -> None:
    target = Path(output_dir)
    if not target.is_absolute():
        target = REPO_ROOT / target
    if target.resolve() == (REPO_ROOT / PREVIOUS_OUTPUT_DIR).resolve():
        raise ValueError(
            "PUYO-189 evidence is read-only; use the dedicated PUYO-204 output directory"
        )


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


def _host_environment() -> dict[str, Any]:
    cpu_model = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name") and ":" in line:
                cpu_model = line.split(":", 1)[1].strip()
                break
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_model": cpu_model,
        "logical_cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_abi": f"cp{sys.version_info.major}{sys.version_info.minor}",
    }


def _native_build_provenance(*, strict: bool) -> dict[str, Any]:
    commit = git_commit(REPO_ROOT)
    tracked_clean = _tracked_worktree_clean()
    try:
        capabilities = NativeDeepChainBackend(canonical=True).capabilities.to_dict()
        error = None
    except Exception as exc:  # noqa: BLE001 - evidence must retain build failures
        capabilities = {}
        error = {"type": type(exc).__name__, "detail": str(exc)}
    wheel_paths = sorted((REPO_ROOT / "dist" / "native").glob("*.whl"))
    wheels = [
        {
            "path": str(path.relative_to(REPO_ROOT)),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in wheel_paths
    ]
    source_revision = str(capabilities.get("source_revision", ""))
    backend_config = load_long_horizon_backend_config()
    checks = {
        "tracked_worktree_clean": tracked_clean,
        "capabilities_available": error is None,
        "release_build": capabilities.get("build_profile") == "release",
        "source_revision_matches_commit": source_revision == commit,
        "python_abi_matches": capabilities.get("python_abi")
        == f"cp{sys.version_info.major}{sys.version_info.minor}",
        "gil_detached": capabilities.get("gil_detach") is True,
        "configured_thread_mode_available": CANONICAL_EXECUTION_MODE
        in capabilities.get("thread_modes", ()),
        "configured_thread_mode_locked": (
            backend_config.native_execution_mode == CANONICAL_EXECUTION_MODE
        ),
        "single_release_wheel_present": len(wheels) == 1,
    }
    payload = {
        "schema_version": BUILD_PROVENANCE_SCHEMA_VERSION,
        "ticket": TICKET,
        "recorded_at_utc": utc_timestamp(),
        "evaluated_commit": commit,
        "tracked_worktree_clean": tracked_clean,
        "host": _host_environment(),
        "capabilities": capabilities,
        "wheels": wheels,
        "configuration": {
            "search_sha256": file_sha256(DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH),
            "backend_sha256": file_sha256(
                DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH
            ),
            "backend_version": backend_config.config_version,
            "thread_mode": backend_config.native_execution_mode,
            "thread_count": 6,
        },
        "checks": checks,
        "valid_for_canonical": all(checks.values()),
        "error": error,
    }
    if strict and not payload["valid_for_canonical"]:
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"canonical native build provenance failed: {failed}")
    return payload


def _scenario_accounting(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    search = diagnostics.get("search", {})
    expected = (
        tuple(int(value) for value in search.get("scenario_ids", ()))
        if isinstance(search, Mapping)
        else ()
    )
    aggregates = diagnostics.get("scenario_aggregation", ())
    failures = []
    root_count = 0
    if isinstance(aggregates, (list, tuple)):
        for aggregate in aggregates:
            if not isinstance(aggregate, Mapping):
                continue
            root_count += 1
            evidence = aggregate.get("evidence", {})
            values = (
                evidence.get("scenario_values", ())
                if isinstance(evidence, Mapping)
                else ()
            )
            ids = [
                int(item["scenario_id"])
                for item in values
                if isinstance(item, Mapping) and "scenario_id" in item
            ]
            missing = sorted(set(expected) - set(ids))
            unexpected = sorted(set(ids) - set(expected))
            duplicates = sorted({value for value in ids if ids.count(value) > 1})
            requested = (
                int(evidence.get("requested_scenarios", 0))
                if isinstance(evidence, Mapping)
                else 0
            )
            if missing or unexpected or duplicates or requested != len(expected):
                failures.append(
                    {
                        "root_action": aggregate.get("root_action"),
                        "requested_scenarios": requested,
                        "observed_scenario_ids": ids,
                        "missing_scenario_ids": missing,
                        "unexpected_scenario_ids": unexpected,
                        "duplicate_scenario_ids": duplicates,
                    }
                )
    return {
        "expected_scenario_ids": list(expected),
        "expected_scenario_count": len(expected),
        "root_count": root_count,
        "failure_count": len(failures),
        "failures": failures,
        "passed": bool(expected) and root_count > 0 and not failures,
    }


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
        "ticket": TICKET,
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


def _policy_factory(
    seed: int,
    profile: str,
    backend: str = "python",
    target_chain_count: int = CANONICAL_TARGET_CHAIN_COUNT,
    execution_mode: str | None = None,
) -> Any:
    if backend == CANONICAL_BACKEND and execution_mode is not None:
        backend_config = load_long_horizon_backend_config()
        search_backend = NativeLongHorizonSearchBackend(
            execution_mode=execution_mode,
            max_response_bytes=backend_config.max_response_bytes,
            canonical=True,
        )
        return DeepChainBuilderPolicy(
            profile=profile,
            backend=CANONICAL_BACKEND,
            search_backend=search_backend,
            backend_config=backend_config,
            target_chain_count=target_chain_count,
        )
    return make_policy(
        "deep_chain_builder",
        seed=int(seed),
        deep_chain_profile=profile,
        deep_chain_backend=backend,
        deep_chain_target_chain=target_chain_count,
    )


def run_benchmark_run(
    *,
    seed: int,
    repeat: int,
    profile: str,
    max_steps: int,
    backend: str = "python",
    target_chain_count: int = CANONICAL_TARGET_CHAIN_COUNT,
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
        _policy_factory(
            int(seed),
            str(profile),
            str(backend),
            int(target_chain_count),
        )
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
    usage_started = resource.getrusage(resource.RUSAGE_SELF)

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
        if 1 <= chain_count < CANONICAL_PREMATURE_FIRE_CHAIN_COUNT:
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
                "scenario_ids": _json_ready(
                    search.get("scenario_ids", ())
                    if isinstance(search, Mapping)
                    else ()
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
            "scenario_accounting": _scenario_accounting(diagnostics),
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
    usage_finished = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "ticket": TICKET,
        "run_id": run_identity(seed, repeat),
        "evaluated_commit": evaluated_commit,
        "configuration_sha256": configuration_sha256,
        "backend_configuration_sha256": backend_configuration_sha256,
        "seed": int(seed),
        "repeat": int(repeat),
        "profile": str(profile),
        "backend": str(backend),
        "target_chain_count": int(target_chain_count),
        "environment": "safe_no_threat",
        "max_steps": int(max_steps),
        "completed_turns": int(completed_turns),
        "fully_evaluated": bool(fully_evaluated),
        "termination_reason": termination_reason,
        "elapsed_seconds": float(time.perf_counter() - started_at),
        "process_resources": {
            "user_cpu_seconds": float(
                usage_finished.ru_utime - usage_started.ru_utime
            ),
            "system_cpu_seconds": float(
                usage_finished.ru_stime - usage_started.ru_stime
            ),
            "peak_rss_kib": int(usage_finished.ru_maxrss),
        },
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
        if payload.get("ticket") != TICKET:
            raise ValueError(f"benchmark ticket mismatch: {path}")
        if payload.get("run_id") != identity["run_id"]:
            raise ValueError(f"run identity mismatch: {path}")
        if payload.get("target_chain_count") != CANONICAL_TARGET_CHAIN_COUNT:
            raise ValueError(f"canonical target chain count mismatch: {path}")
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
    _protect_historical_output(output_dir)
    if max_runs is not None and max_runs <= 0:
        raise ValueError("max_runs must be positive when provided")
    target = Path(output_dir)
    _native_build_provenance(strict=True)
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
            target_chain_count=CANONICAL_TARGET_CHAIN_COUNT,
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
    native_timing_values: dict[str, list[float]] = defaultdict(list)
    telemetry_values: dict[str, list[float]] = defaultdict(list)
    python_flow_values: list[float] = []
    scenario_decision_count = 0
    scenario_failure_count = 0
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
            backend = record.get("search", {}).get("backend", {})
            if isinstance(backend, Mapping):
                timing = backend.get("timing", {})
                if isinstance(timing, Mapping):
                    for key, value in timing.items():
                        if (
                            str(key).endswith("_ns")
                            and isinstance(value, (int, float))
                            and not isinstance(value, bool)
                        ):
                            native_timing_values[str(key)].append(
                                float(value) / 1_000_000_000.0
                            )
                    backend_seconds = float(timing.get("total_ns", 0.0)) / 1e9
                    decision_seconds = float(record.get("elapsed_seconds", 0.0))
                    python_flow_values.append(max(0.0, decision_seconds - backend_seconds))
                telemetry = backend.get("telemetry", {})
                if isinstance(telemetry, Mapping):
                    for key, value in telemetry.items():
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            telemetry_values[str(key)].append(float(value))
            accounting = record.get("scenario_accounting", {})
            if isinstance(accounting, Mapping):
                scenario_decision_count += 1
                scenario_failure_count += int(accounting.get("failure_count", 0))
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
    expanded = sum(counter_values.get("expanded_nodes", ()))
    native_compute_seconds = sum(native_timing_values.get("native_compute_ns", ()))
    native_serialization_seconds = sum(
        native_timing_values.get("native_serialization_ns", ())
    )
    backend_total_seconds = sum(native_timing_values.get("total_ns", ()))

    def distribution(values: Sequence[float]) -> dict[str, Any]:
        return {
            "sample_count": len(values),
            "total": sum(values),
            "mean": _mean(values),
            "p50": percentile(values, 0.50),
            "p95": percentile(values, 0.95),
            "max": max(values) if values else None,
        }

    return {
        "counters": counters,
        "native_phase_latency_seconds": {
            key.removesuffix("_ns"): distribution(values)
            for key, values in sorted(native_timing_values.items())
        },
        "python_flow_latency_seconds": distribution(python_flow_values),
        "native_telemetry": {
            key: distribution(values)
            for key, values in sorted(telemetry_values.items())
        },
        "expanded_nodes_per_native_compute_second": (
            None
            if native_compute_seconds <= 0
            else float(expanded / native_compute_seconds)
        ),
        "cache_hit_rate_per_generated_node": (
            None if generated <= 0 else float(hits / generated)
        ),
        "cache_accounting": {
            "transposition_hits": int(hits),
            "transposition_hit_rate_per_generated_node": (
                None if generated <= 0 else float(hits / generated)
            ),
            "evaluator_resolution_cache_hits": None,
            "evaluator_cache_availability": (
                "not_exported_by_the_production_long_horizon_hot_path; "
                "the independent PUYO-221/227 evaluator profile remains authoritative"
            ),
        },
        "native_serialization_ratio": (
            None
            if backend_total_seconds <= 0
            else float(native_serialization_seconds / backend_total_seconds)
        ),
        "scenario_accounting": {
            "decision_count": scenario_decision_count,
            "failure_count": scenario_failure_count,
            "passed": scenario_decision_count > 0 and scenario_failure_count == 0,
        },
        "process_resources": {
            "run_count": len(runs),
            "user_cpu_seconds": sum(
                float(run.get("process_resources", {}).get("user_cpu_seconds", 0.0))
                for run in runs
            ),
            "system_cpu_seconds": sum(
                float(run.get("process_resources", {}).get("system_cpu_seconds", 0.0))
                for run in runs
            ),
            "peak_rss_kib": max(
                (
                    int(run.get("process_resources", {}).get("peak_rss_kib", 0))
                    for run in runs
                ),
                default=None,
            ),
        },
        "flow_step_latency_seconds": {
            key: {
                **distribution(values),
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


def _dummy_gui_summary(
    result_path: str | Path | None,
    replay_path: str | Path | None,
) -> dict[str, Any]:
    if result_path is None or replay_path is None:
        return {
            "status": "not_recorded",
            "passed": False,
            "checks": [],
        }
    result = _read_json(result_path)
    replay = _read_json(replay_path)
    models = result.get("models", {})
    player_model = models.get("player_0", {}) if isinstance(models, Mapping) else {}
    diagnostics = result.get("diagnostics", {})
    policies = diagnostics.get("policy", {}) if isinstance(diagnostics, Mapping) else {}
    player = policies.get("player_0", {}) if isinstance(policies, Mapping) else {}
    backend = player.get("backend", {}) if isinstance(player, Mapping) else {}
    fallback = player.get("fallback", {}) if isinstance(player, Mapping) else {}
    plan = player.get("plan", {}) if isinstance(player, Mapping) else {}
    steps = plan.get("steps", ()) if isinstance(plan, Mapping) else ()
    selected_action = player.get("selected_action") if isinstance(player, Mapping) else None
    first_action = (
        steps[0].get("action")
        if steps and isinstance(steps[0], Mapping)
        else None
    )
    controller = diagnostics.get("controller", {}) if isinstance(diagnostics, Mapping) else {}
    player_controller = (
        controller.get("player_0", {}) if isinstance(controller, Mapping) else {}
    )
    checks = [
        _check(
            "result_schema",
            result.get("schema_version") == "puyo.gui_qa.v1",
            result.get("schema_version"),
            "puyo.gui_qa.v1",
        ),
        _check(
            "replay_schema",
            replay.get("format") == "puyo-realtime-match-v1",
            replay.get("format"),
            "puyo-realtime-match-v1",
        ),
        _check(
            "reference_native_profile",
            player_model.get("deep_chain_profile") == "reference"
            and player_model.get("deep_chain_backend") == CANONICAL_BACKEND,
            {
                "profile": player_model.get("deep_chain_profile"),
                "backend": player_model.get("deep_chain_backend"),
            },
            {"profile": "reference", "backend": CANONICAL_BACKEND},
        ),
        _check(
            "canonical_target",
            player_model.get("deep_chain_target_chain")
            == CANONICAL_TARGET_CHAIN_COUNT,
            player_model.get("deep_chain_target_chain"),
            CANONICAL_TARGET_CHAIN_COUNT,
        ),
        _check(
            "native_without_fallback",
            isinstance(backend, Mapping)
            and backend.get("backend") == CANONICAL_BACKEND
            and isinstance(fallback, Mapping)
            and fallback.get("used") is False,
            {
                "backend": backend.get("backend")
                if isinstance(backend, Mapping)
                else None,
                "fallback": fallback.get("used")
                if isinstance(fallback, Mapping)
                else None,
            },
            {"backend": CANONICAL_BACKEND, "fallback": False},
        ),
        _check(
            "plan_step_one_matches_action",
            selected_action is not None and selected_action == first_action,
            {"selected_action": selected_action, "plan_step_one": first_action},
            "equal",
        ),
        _check(
            "decisions_and_replan_observed",
            int(player_controller.get("decisions_started", 0)) >= 3
            and int(player_controller.get("replans", 0)) >= 1,
            {
                "decisions": player_controller.get("decisions_started"),
                "replans": player_controller.get("replans"),
            },
            {"decisions": ">= 3", "replans": ">= 1"},
        ),
        _check(
            "plan_overlay_enabled",
            result.get("plan_overlay_player_0") is True,
            result.get("plan_overlay_player_0"),
            True,
        ),
    ]
    return {
        "status": "passed" if all(check["passed"] for check in checks) else "failed",
        "passed": all(check["passed"] for check in checks),
        "result_path": str(result_path),
        "replay_path": str(replay_path),
        "checks": checks,
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
        "ticket": TICKET,
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
        "dummy_replay": {
            "status": "not_recorded",
            "passed": False,
            "checks": [],
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
    dummy_result_path: str | Path | None = None,
    dummy_replay_path: str | Path | None = None,
) -> dict[str, Any]:
    _protect_historical_output(output_dir)
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
    dummy_replay = _dummy_gui_summary(dummy_result_path, dummy_replay_path)
    payload = {
        "schema_version": GUI_QA_SCHEMA_VERSION,
        "ticket": TICKET,
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
        "dummy_replay": dummy_replay,
        "passed": bool(
            automated_passed
            and dummy_replay.get("passed")
            and manual_status == "passed"
        ),
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


def _lineage_payload(
    config: Any,
    *,
    build_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    backend_config = load_long_horizon_backend_config()
    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "ticket": TICKET,
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
            "target_chain_count": CANONICAL_TARGET_CHAIN_COUNT,
            "environment": config.benchmark.environment,
        },
        "native_build": _json_ready(build_provenance),
        "host": _host_environment(),
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
                "--output-dir docs/benchmarks/puyo-204-deep-chain-native-baseline"
            ),
            "resume_one_run": (
                ".venv/bin/python -m eval.deep_chain_builder_benchmark run "
                "--backend native --max-runs 1 --output-dir "
                "docs/benchmarks/puyo-204-deep-chain-native-baseline"
            ),
            "verify": (
                ".venv/bin/python -m eval.deep_chain_builder_benchmark verify "
                "--output-dir docs/benchmarks/puyo-204-deep-chain-native-baseline"
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
    execution_mode: str,
    sample_labels: Sequence[str],
) -> None:
    observation, info = _initial_observation_and_info(seed, max_steps=max_steps)
    worker_started = time.perf_counter()
    try:
        samples = []
        policy = None
        for label in sample_labels:
            if policy is not None:
                policy.reset()
            usage_started = resource.getrusage(resource.RUSAGE_SELF)
            started = time.perf_counter()
            if policy is None:
                policy = _policy_factory(
                    seed,
                    profile,
                    backend,
                    CANONICAL_TARGET_CHAIN_COUNT,
                    execution_mode,
                )
            action = int(policy.select_action(observation, info))
            elapsed = time.perf_counter() - started
            usage_finished = resource.getrusage(resource.RUSAGE_SELF)
            diagnostics = getattr(policy, "tactical_diagnostics", {})
            if not isinstance(diagnostics, Mapping):
                diagnostics = {}
            search = diagnostics.get("search", {})
            plan = _plan_summary(diagnostics.get("plan", {}))
            sample = {
                "label": str(label),
                "completed": True,
                "execution_mode": execution_mode,
                "action": action,
                "elapsed_seconds": float(elapsed),
                "fallback": _json_ready(diagnostics.get("fallback", {})),
                "search": _json_ready(search),
                "decision_trace": _json_ready(
                    diagnostics.get("decision_trace", {})
                ),
                "backend": _json_ready(diagnostics.get("backend", {})),
                "scenario_accounting": _scenario_accounting(diagnostics),
                "plan_digest": _stable_digest(plan, prefix="puyo-204-preflight-plan"),
                "decision_digest": _stable_digest(
                    {
                        "action": action,
                        "plan": plan,
                        "search_digest": (
                            search.get("deterministic_digest", "")
                            if isinstance(search, Mapping)
                            else ""
                        ),
                    },
                    prefix="puyo-204-preflight-decision",
                ),
                "resources": {
                    "user_cpu_seconds": float(
                        usage_finished.ru_utime - usage_started.ru_utime
                    ),
                    "system_cpu_seconds": float(
                        usage_finished.ru_stime - usage_started.ru_stime
                    ),
                    "peak_rss_kib": int(usage_finished.ru_maxrss),
                },
            }
            samples.append(sample)
        queue.put(
            {
                "completed": True,
                "execution_mode": execution_mode,
                "samples": samples,
                "elapsed_seconds": float(time.perf_counter() - worker_started),
                "peak_rss_kib": int(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                ),
            }
        )
    except BaseException as exc:  # noqa: BLE001 - child must report diagnostic
        queue.put(
            {
                "completed": False,
                "error": {"type": type(exc).__name__, "detail": str(exc)},
                "execution_mode": execution_mode,
                "elapsed_seconds": float(time.perf_counter() - worker_started),
            }
        )


def run_preflight(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    backend: str,
    seed: int | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Run cold/warm and one-thread non-canonical latency diagnostics."""

    if backend != CANONICAL_BACKEND:
        raise ValueError("reference preflight requires backend=native")
    _protect_historical_output(output_dir)
    build_provenance = _native_build_provenance(strict=True)
    config = load_deep_chain_builder_config()
    contract = config.benchmark
    selected_seed = contract.seed_start if seed is None else int(seed)
    if timeout_seconds < contract.maximum_decision_p95_seconds:
        raise ValueError(
            "preflight timeout must be at least the locked decision p95 threshold"
        )
    context = multiprocessing.get_context("spawn")

    def supervised(
        execution_mode: str,
        labels: Sequence[str],
    ) -> dict[str, Any]:
        queue = context.Queue()
        process = context.Process(
            target=_preflight_worker,
            args=(
                queue,
                selected_seed,
                "reference",
                backend,
                contract.max_steps,
                execution_mode,
                tuple(labels),
            ),
        )
        started = time.perf_counter()
        process.start()
        process.join(timeout=float(timeout_seconds))
        timed_out = process.is_alive()
        if timed_out:
            process.terminate()
            process.join()
        observed = time.perf_counter() - started
        result = None
        if not timed_out:
            try:
                result = queue.get(timeout=1.0)
            except Empty:
                result = None
        queue.close()
        return {
            "execution_mode": execution_mode,
            "timed_out": timed_out,
            "child_exit_code": process.exitcode,
            "supervisor_elapsed_seconds": float(observed),
            "result": result,
        }

    adopted = supervised(CANONICAL_EXECUTION_MODE, ("cold", "warm"))
    adopted_result = adopted.get("result")
    if not isinstance(adopted_result, Mapping):
        adopted_result = {}
    one_thread = (
        supervised(DIAGNOSTIC_EXECUTION_MODE, ("one_thread",))
        if adopted_result.get("completed")
        else {
            "execution_mode": DIAGNOSTIC_EXECUTION_MODE,
            "timed_out": False,
            "child_exit_code": None,
            "supervisor_elapsed_seconds": 0.0,
            "result": {
                "completed": False,
                "error": {
                    "type": "Skipped",
                    "detail": "adopted thread-mode diagnostic did not complete",
                },
            },
        }
    )
    one_thread_result = one_thread.get("result")
    if not isinstance(one_thread_result, Mapping):
        one_thread_result = {}
    adopted_samples = adopted_result.get("samples", ())
    one_thread_samples = one_thread_result.get("samples", ())
    completed = len(adopted_samples) == 2
    measured_values = [
        float(sample["elapsed_seconds"])
        for sample in adopted_samples
        if sample.get("completed") and sample.get("elapsed_seconds") is not None
    ]
    measured_elapsed = measured_values[0] if measured_values else None
    timed_out = bool(adopted["timed_out"])
    latency_lower_bound = float(timeout_seconds) if timed_out else measured_elapsed
    performance_passed = bool(
        completed
        and len(measured_values) == 2
        and all(
            value <= contract.maximum_decision_p95_seconds
            for value in measured_values
        )
    )
    adopted_digest_match = bool(
        len(adopted_samples) == 2
        and len({sample.get("decision_digest") for sample in adopted_samples}) == 1
    )
    one_thread_match = bool(
        adopted_samples
        and one_thread_samples
        and adopted_samples[0].get("decision_digest")
        == one_thread_samples[0].get("decision_digest")
    )
    thread_determinism = {
        "adopted_mode": CANONICAL_EXECUTION_MODE,
        "adopted_thread_count": 6,
        "diagnostic_mode": DIAGNOSTIC_EXECUTION_MODE,
        "diagnostic_thread_count": 1,
        "cold_warm_digest_match": adopted_digest_match,
        "one_thread_matches_adopted": one_thread_match,
        "passed": adopted_digest_match and one_thread_match,
    }
    payload = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "ticket": TICKET,
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
        "target_chain_count": CANONICAL_TARGET_CHAIN_COUNT,
        "profile": config.profile("reference").to_dict(),
        "build_provenance": build_provenance,
        "timeout_seconds": float(timeout_seconds),
        "timed_out": bool(timed_out),
        "child_exit_code": adopted["child_exit_code"],
        "supervisor_elapsed_seconds": adopted["supervisor_elapsed_seconds"],
        "decision_elapsed_seconds": measured_elapsed,
        "decision_latency_lower_bound_seconds": latency_lower_bound,
        "locked_maximum_decision_p95_seconds": float(
            contract.maximum_decision_p95_seconds
        ),
        "performance_gate_passed": performance_passed,
        "thread_determinism": thread_determinism,
        "diagnostics": {
            CANONICAL_EXECUTION_MODE: adopted,
            DIAGNOSTIC_EXECUTION_MODE: one_thread,
        },
        "result": adopted_samples[0] if adopted_samples else adopted.get("result"),
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
        "transition": {
            "status": "not_evaluable"
            if no_complete_search
            else ("mismatch" if parity_mismatches else "matched"),
            "authoritative_parity_mismatch_count": int(parity_mismatches),
        },
        "boundary": {
            "status": "not_evaluable"
            if no_complete_search
            else ("fallback" if fallback_count else "native_only"),
            "fallback_count": int(fallback_count),
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


def _historical_comparison(
    *,
    latency: Mapping[str, Any],
    build_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    prior_preflight_path = REPO_ROOT / PREVIOUS_OUTPUT_DIR / "preflight.json"
    prior_preflight = (
        _read_json(prior_preflight_path) if prior_preflight_path.is_file() else {}
    )
    prior_lower_bound = prior_preflight.get("decision_latency_lower_bound_seconds")
    current_p95 = latency.get("p95_seconds")
    speedup_lower_bound = (
        None
        if not isinstance(prior_lower_bound, (int, float))
        or not isinstance(current_p95, (int, float))
        or current_p95 <= 0
        else float(prior_lower_bound / current_p95)
    )
    return {
        "schema_version": HISTORICAL_COMPARISON_SCHEMA_VERSION,
        "ticket": TICKET,
        "compared_ticket": PREVIOUS_TICKET,
        "profile_contract_equal": True,
        "locked_conditions": {
            "depth": 16,
            "width": 250,
            "scenarios": 6,
            "max_expanded_nodes": 600_000,
            "seeds": "123-152",
            "repeats_per_seed": 2,
            "placements_per_run": 40,
        },
        "previous": {
            "backend": "python",
            "decision_latency_lower_bound_seconds": prior_lower_bound,
            "evaluated_commit": prior_preflight.get("evaluated_commit"),
            "host": "not recorded in the PUYO-189 preflight schema",
        },
        "current": {
            "backend": CANONICAL_BACKEND,
            "decision_p95_seconds": current_p95,
            "evaluated_commit": build_provenance.get("evaluated_commit"),
            "host": build_provenance.get("host"),
            "native_build": build_provenance.get("capabilities"),
        },
        "speedup_lower_bound_from_previous_timeout": speedup_lower_bound,
        "direct_ratio_caveat": (
            "The locked workload is equal, but PUYO-189 did not record equivalent "
            "host/build provenance. The ratio is a lower-bound observation, not a "
            "same-host microbenchmark comparison."
        ),
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
        "# PUYO-204 Deep-chain native baseline evaluation",
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
        f"- Fallbacks: {aggregate['fallback_count']}",
        (
            "- Scenario accounting failures: "
            f"{aggregate['search']['scenario_accounting']['failure_count']}"
        ),
        f"- Decision p95 seconds: {display(aggregate['latency']['p95_seconds'])}",
        (
            "- Expanded nodes / native compute second: "
            f"{display(aggregate['search']['expanded_nodes_per_native_compute_second'])}"
        ),
        (
            "- Native serialization ratio: "
            f"{display(aggregate['search']['native_serialization_ratio'])}"
        ),
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
    _protect_historical_output(output_dir)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    config = load_deep_chain_builder_config()
    backend_config = load_long_horizon_backend_config()
    contract = config.benchmark
    seeds = list(range(contract.seed_start, contract.seed_start + contract.seed_count))
    expected = expected_run_identities(contract)
    runs = _load_completed_runs(target, contract)
    by_id = {str(run["run_id"]): run for run in runs}
    build_provenance = _native_build_provenance(strict=False)
    _write_json(target / "build_provenance.json", build_provenance)
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
        "ticket": TICKET,
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
        and run.get("target_chain_count") == CANONICAL_TARGET_CHAIN_COUNT
        for run in runs
    )
    matching_native_run_count = 0
    for run in runs:
        action_records = [
            record for record in run.get("records", ()) if "action" in record
        ]
        native_records_match = bool(action_records)
        for record in action_records:
            native = record.get("search", {}).get("backend", {})
            provenance = native.get("provenance", {}) if isinstance(native, Mapping) else {}
            native_records_match = native_records_match and bool(
                isinstance(native, Mapping)
                and native.get("backend") == CANONICAL_BACKEND
                and native.get("requested_backend") == CANONICAL_BACKEND
                and native.get("execution_mode") == CANONICAL_EXECUTION_MODE
                and native.get("boundary_call_count") == 1
                and isinstance(provenance, Mapping)
                and provenance.get("build_profile") == "release"
                and provenance.get("source_revision") == run.get("evaluated_commit")
                and provenance.get("thread_mode") == CANONICAL_EXECUTION_MODE
                and provenance.get("thread_count") == 6
            )
        matching_native_run_count += int(native_records_match)
    latency = _aggregate_latency(runs)
    search = _aggregate_search(runs)
    determinism = _determinism_summary(runs, seeds=seeds)
    historical_comparison = _historical_comparison(
        latency=latency,
        build_provenance=build_provenance,
    )
    _write_json(target / "historical_comparison.json", historical_comparison)

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
        _check(
            "canonical_native_build_per_run",
            matching_native_run_count == len(expected),
            matching_native_run_count,
            len(expected),
        ),
    ]
    build_checks = [
        _check(
            name,
            bool(passed),
            passed,
            True,
        )
        for name, passed in build_provenance.get("checks", {}).items()
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
        _check(
            "cold_warm_and_one_thread_determinism",
            bool(preflight)
            and bool(preflight.get("thread_determinism", {}).get("passed")),
            (
                None
                if preflight is None
                else preflight.get("thread_determinism", {}).get("passed")
            ),
            True,
        ),
    ]
    scenario_checks = [
        _check(
            "all_decisions_accounted_for",
            search["scenario_accounting"]["decision_count"]
            == latency["sample_count"],
            search["scenario_accounting"]["decision_count"],
            latency["sample_count"],
        ),
        _check(
            "missing_duplicate_or_unexpected_scenarios",
            search["scenario_accounting"]["failure_count"] == 0
            and latency["sample_count"] > 0,
            search["scenario_accounting"]["failure_count"],
            0,
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
            "dummy_gui_replay_contract",
            bool(gui_qa.get("dummy_replay", {}).get("passed")),
            gui_qa.get("dummy_replay", {}).get("status"),
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
        "native_build": gate(build_checks),
        "coverage": gate(coverage_checks),
        "quality": gate(quality_checks),
        "simulator_parity": gate(parity_checks),
        "future_isolation": gate(isolation_checks),
        "determinism": gate(determinism_checks),
        "scenario_accounting": gate(scenario_checks),
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
        "ticket": TICKET,
        "generated_at_utc": utc_timestamp(),
        "configuration": {
            "policy_id": config.policy_id,
            "profile": config.profile("reference").to_dict(),
            "target_chain_count": CANONICAL_TARGET_CHAIN_COUNT,
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
            "matching_native_run_count": int(matching_native_run_count),
            "latency": latency,
            "search": search,
        },
        "determinism": determinism,
        "historical_comparison": historical_comparison,
        "preflight": preflight,
        "gui_qa": {
            "passed": bool(gui_qa.get("passed")),
            "automated_status": gui_qa.get("automated", {}).get("status"),
            "dummy_replay_status": gui_qa.get("dummy_replay", {}).get("status"),
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
        prefix="puyo-204-benchmark-summary",
    )
    _write_json(target / "benchmark_summary.json", summary)

    configuration = config.to_dict()
    configuration["canonical_target_chain_count"] = CANONICAL_TARGET_CHAIN_COUNT
    configuration["configuration_sha256"] = file_sha256(
        DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH
    )
    configuration["backend"] = {
        **backend_config.to_dict(),
        "selected_backend": CANONICAL_BACKEND,
        "configuration_sha256": backend_configuration_sha256,
    }
    _write_json(target / "configuration.json", configuration)
    lineage = _lineage_payload(config, build_provenance=build_provenance)
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
        "ticket": TICKET,
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
        prefix="puyo-204-benchmark-manifest",
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
    legacy = manifest.get("ticket") == PREVIOUS_TICKET
    expected_manifest_schema = (
        "puyo.deep_chain_builder.benchmark_manifest.v1"
        if legacy
        else MANIFEST_SCHEMA_VERSION
    )
    expected_summary_schema = (
        "puyo.deep_chain_builder.benchmark_summary.v1"
        if legacy
        else SUMMARY_SCHEMA_VERSION
    )
    digest_ticket = "189" if legacy else "204"
    if manifest.get("ticket") not in {TICKET, PREVIOUS_TICKET}:
        issues.append("benchmark ticket mismatch")
    if manifest.get("schema_version") != expected_manifest_schema:
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
        if summary.get("schema_version") != expected_summary_schema:
            issues.append("benchmark summary schema mismatch")
        if summary.get("ticket") != manifest.get("ticket"):
            issues.append("manifest and summary tickets disagree")
        expected_digest = _stable_digest(
            {
                "configuration": summary.get("configuration"),
                "aggregate": summary.get("aggregate"),
                "determinism": summary.get("determinism"),
                "gates": summary.get("gates"),
                "failure_taxonomy": summary.get("failure_taxonomy"),
                "baseline_decision": summary.get("baseline_decision"),
            },
            prefix=f"puyo-{digest_ticket}-benchmark-summary",
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
        prefix=f"puyo-{digest_ticket}-benchmark-manifest",
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
    gui.add_argument("--dummy-result", type=Path)
    gui.add_argument("--dummy-replay", type=Path)

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
            dummy_result_path=args.dummy_result,
            dummy_replay_path=args.dummy_replay,
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
