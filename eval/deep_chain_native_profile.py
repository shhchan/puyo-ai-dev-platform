"""PUYO-198 reproducible profiling and native-boundary evidence.

This module is diagnostic-only. It profiles the existing pure-Python policy
without modifying search semantics or selecting a production backend.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import math
import multiprocessing
import os
import platform
import pstats
import resource
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from queue import Empty
from typing import Any

from agents.chain_structure import ChainStructureEvaluator
from agents.compact_search import (
    CompactSearchState,
    CompactTranspositionKey,
    legal_action_indices,
    transition,
)
from agents.deep_chain_builder import (
    DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH,
    DeepChainBuilderPolicy,
    DeepChainBuilderProfile,
    load_deep_chain_builder_config,
)
from agents.long_horizon_search import (
    FUTURE_SAMPLING_LEGACY_FIXED_SIX,
    LongHorizonSearchConfig,
    run_compact_long_horizon_search,
)
from puyo_env.actions import legal_action_mask
from puyo_env.obs import encode_observation
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor
from src.core.headless import HeadlessPuyoSimulator
from train.artifacts import (
    describe_artifact,
    file_sha256,
    git_commit,
    utc_timestamp,
)

TICKET = "PUYO-198"
PROFILE_RUN_SCHEMA_VERSION = "puyo.deep_chain_native.profile_run.v1"
REFERENCE_PROBE_SCHEMA_VERSION = "puyo.deep_chain_native.reference_probe.v1"
MICROBENCHMARK_SCHEMA_VERSION = "puyo.deep_chain_native.microbenchmark.v1"
CORPUS_SCHEMA_VERSION = "puyo.deep_chain_native.corpus.v1"
SUMMARY_SCHEMA_VERSION = "puyo.deep_chain_native.profile_summary.v1"
MANIFEST_SCHEMA_VERSION = "puyo.deep_chain_native.profile_manifest.v1"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path(
    "docs/benchmarks/puyo-198-deep-chain-native-profile"
)
DEFAULT_CORPUS_PATH = Path(__file__).with_name("deep_chain_native_corpus.json")
SOURCE_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "compact_search_kernel_cases.json"
PUYO_189_PREFLIGHT_PATH = (
    REPO_ROOT
    / "docs"
    / "benchmarks"
    / "puyo-189-deep-chain-builder-baseline"
    / "preflight.json"
)

_PLANE_COLORS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
    PuyoColor.PURPLE,
    PuyoColor.OJAMA,
)
_BOARD_CHARACTERS = {
    ".": None,
    "R": PuyoColor.RED,
    "B": PuyoColor.BLUE,
    "G": PuyoColor.GREEN,
    "Y": PuyoColor.YELLOW,
    "P": PuyoColor.PURPLE,
    "O": PuyoColor.OJAMA,
}
_PLANE_INDEX = {color: index for index, color in enumerate(_PLANE_COLORS)}

INTERMEDIATE_PROFILE = DeepChainBuilderProfile(
    name="intermediate",
    version="1.0",
    purpose="PUYO-198 completed profiling workload between smoke and reference",
    depth=5,
    width=12,
    scenarios=3,
    max_expanded_nodes=8192,
)

CORPUS_SEARCH_CONFIG = {
    "depth": 3,
    "width": 4,
    "scenarios": 2,
    "minimum_chain_count": 6,
    "max_expanded_nodes": 512,
    "decision_seed": 123,
    "future_sampling_mode": FUTURE_SAMPLING_LEGACY_FIXED_SIX,
}

_PROFILE_ARTIFACTS = (
    ("raw/smoke_profile.json", "smoke_profile"),
    ("raw/intermediate_profile.json", "intermediate_profile"),
    ("raw/reference_profile.json", "reference_profile"),
    ("raw/microbenchmarks.json", "microbenchmarks"),
    ("corpus.json", "frozen_corpus"),
    ("benchmark_summary.json", "benchmark_summary"),
    ("benchmark_report.md", "benchmark_report"),
)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_ready(item) for item in value]
    if hasattr(value, "to_dict"):
        return _json_ready(value.to_dict())
    if hasattr(value, "tolist"):
        return _json_ready(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0.0 else "-Infinity"
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


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name") and ":" in line:
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def system_metadata() -> dict[str, Any]:
    thread_env = {
        name: os.environ.get(name)
        for name in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "RAYON_NUM_THREADS",
        )
    }
    return {
        "platform": platform.platform(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "cpu_model": _cpu_model(),
        "logical_cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "configured_thread_environment": thread_env,
        "process_thread_count": _process_thread_count(),
    }


def _process_thread_count() -> int | None:
    task_dir = Path("/proc/self/task")
    if not task_dir.is_dir():
        return None
    return len(tuple(task_dir.iterdir()))


def _state_from_rows(
    rows: Sequence[str],
    *,
    all_clear_bonus_pending: bool = False,
    game_over: bool = False,
    score: int = 0,
    last_chain_end_score: int = 0,
) -> CompactSearchState:
    if len(rows) != GRID_HEIGHT or any(len(row) != GRID_WIDTH for row in rows):
        raise ValueError("corpus board must contain 14 bottom-to-top rows of width 6")
    planes = [0] * len(_PLANE_COLORS)
    for y, row in enumerate(rows):
        for x, character in enumerate(row):
            if character not in _BOARD_CHARACTERS:
                raise ValueError(f"unsupported corpus board character: {character}")
            color = _BOARD_CHARACTERS[character]
            if color is not None:
                planes[_PLANE_INDEX[color]] |= 1 << (y * GRID_WIDTH + x)
    return CompactSearchState(
        planes=tuple(planes),
        all_clear_bonus_pending=bool(all_clear_bonus_pending),
        game_over=bool(game_over),
        score=int(score),
        last_chain_end_score=int(last_chain_end_score),
    )


def _state_payload(state: CompactSearchState) -> dict[str, Any]:
    return {
        "schema_version": "puyo.compact_search_state.v1",
        "plane_order": [color.name for color in _PLANE_COLORS],
        "planes_hex": [f"{int(plane):021x}" for plane in state.planes],
        "all_clear_bonus_pending": bool(state.all_clear_bonus_pending),
        "game_over": bool(state.game_over),
        "score": int(state.score),
        "last_chain_end_score": int(state.last_chain_end_score),
        "column_heights": list(state.column_heights),
        "bytes_sha256": hashlib.sha256(state.to_bytes()).hexdigest(),
    }


def _state_from_payload(payload: Mapping[str, Any]) -> CompactSearchState:
    return CompactSearchState(
        planes=tuple(int(value, 16) for value in payload["planes_hex"]),
        all_clear_bonus_pending=bool(payload.get("all_clear_bonus_pending", False)),
        game_over=bool(payload.get("game_over", False)),
        score=int(payload.get("score", 0)),
        last_chain_end_score=int(payload.get("last_chain_end_score", 0)),
    )


def _pair_from_names(pair: Sequence[str]) -> tuple[PuyoColor, PuyoColor]:
    if len(pair) != 2:
        raise ValueError("pair must contain two color names")
    return (PuyoColor[str(pair[0])], PuyoColor[str(pair[1])])


def _transition_payload(result: Any) -> dict[str, Any]:
    return {
        "action_id": result.action_id,
        "valid": bool(result.valid),
        "axis_y": result.axis_y,
        "score_delta": int(result.score_delta),
        "attack_score_delta": int(result.attack_score_delta),
        "chain_count": int(result.chain_count),
        "vanished_count": int(result.vanished_count),
        "garbage_cleared_count": int(result.garbage_cleared_count),
        "game_over": bool(result.game_over),
        "all_clear_achieved": bool(result.all_clear_achieved),
        "all_clear_bonus_pending": bool(result.all_clear_bonus_pending),
        "all_clear_bonus_consumed": bool(result.all_clear_bonus_consumed),
        "all_clear_bonus_score": int(result.all_clear_bonus_score),
        "chains": [
            {
                "chain_index": int(step.chain_index),
                "vanished_count": int(step.vanished_count),
                "garbage_cleared_count": int(step.garbage_cleared_count),
                "score": int(step.score),
                "base": int(step.base),
                "bonus": int(step.bonus),
                "all_clear_bonus_score": int(step.all_clear_bonus_score),
            }
            for step in result.chains
        ],
        "state": _state_payload(result.state),
    }


def _evaluation_payload(result: Any) -> dict[str, Any]:
    return {
        "evaluation_status": result.evaluation_status,
        "evaluated": bool(result.evaluated),
        "score": result.score,
        "tie_break_digest": result.tie_break_digest,
        "weight_version": result.weight_version,
        "truncation_reason": result.truncation_reason,
        "features": result.features.to_dict(),
        "quiescence": result.quiescence.to_dict(),
        "score_breakdown": result.score_breakdown.to_dict(),
    }


def _known_pairs(seed: int) -> tuple[tuple[PuyoColor, PuyoColor], ...]:
    simulator = HeadlessPuyoSimulator(seed=int(seed))
    game = simulator.game
    current = (game.current_puyo_1, game.current_puyo_2)
    if any(puyo is None for puyo in current):
        raise RuntimeError("seeded simulator did not expose a current pair")
    pairs = [tuple(puyo.color for puyo in current)]
    pairs.extend(tuple(puyo.color for puyo in pair) for pair in game.next_puyo_queue)
    return tuple(pairs[:3])  # type: ignore[return-value]


def _search_payload(result: Any) -> dict[str, Any]:
    ranked = result.ranked_roots
    return {
        "selected_action": int(ranked[0].root_action),
        "ranked_root_actions": [int(item.root_action) for item in ranked],
        "deterministic_digest": result.deterministic_digest,
        "counters": result.counters.to_dict(),
        "root_values": [
            {
                "root_action": int(item.root_action),
                "ranking_key": _json_ready(item.ranking_key),
                "value_breakdown": item.value_breakdown(),
            }
            for item in ranked
        ],
    }


def _run_corpus_search(
    state: CompactSearchState,
    known_pairs: Sequence[Sequence[PuyoColor]],
) -> dict[str, Any]:
    result = run_compact_long_horizon_search(
        state,
        known_pairs,
        LongHorizonSearchConfig(**CORPUS_SEARCH_CONFIG),
        evaluator=ChainStructureEvaluator(),
    )
    return _search_payload(result)


def build_frozen_corpus() -> dict[str, Any]:
    """Build the reviewed differential corpus from pre-existing parity fixtures."""

    source = _read_json(SOURCE_FIXTURE_PATH)
    selected_ids = ("empty_quiet", "two_chain", "hidden_rows_preserved")
    by_id = {str(case["id"]): case for case in source.get("cases", ())}
    evaluator = ChainStructureEvaluator()
    cases = []
    for case_id in selected_ids:
        source_case = by_id[case_id]
        state = _state_from_rows(
            source_case["board"],
            all_clear_bonus_pending=source_case.get(
                "all_clear_bonus_pending", False
            ),
            game_over=source_case.get("game_over", False),
            score=source_case.get("score", 0),
            last_chain_end_score=source_case.get("last_chain_end_score", 0),
        )
        pair = _pair_from_names(source_case["pair"])
        action = int(source_case["action"])
        transition_result = transition(state, pair, action)
        evaluation_result = evaluator.evaluate(state, target_chain_count=6)
        transition_value = _transition_payload(transition_result)
        evaluation_value = _evaluation_payload(evaluation_result)
        cases.append(
            {
                "case_id": case_id,
                "source": {
                    "path": str(SOURCE_FIXTURE_PATH.relative_to(REPO_ROOT)),
                    "fixture_schema_version": source.get("schema_version"),
                },
                "state": _state_payload(state),
                "pair": [color.name for color in pair],
                "action_id": action,
                "scenario": {
                    "decision_seed": 123,
                    "known_pairs": [
                        [color.name for color in known_pair]
                        for known_pair in _known_pairs(123)
                    ],
                    "future_sampling_mode": FUTURE_SAMPLING_LEGACY_FIXED_SIX,
                },
                "expected": {
                    "legal_action_ids": list(legal_action_indices(state)),
                    "transition": transition_value,
                    "transition_digest": _stable_digest(
                        transition_value,
                        prefix="puyo-198-transition-result",
                    ),
                    "evaluation": evaluation_value,
                    "evaluation_digest": _stable_digest(
                        evaluation_value,
                        prefix="puyo-198-evaluation-result",
                    ),
                },
            }
        )

    search_state = CompactSearchState.empty()
    known_pairs = _known_pairs(123)
    search_result = _run_corpus_search(search_state, known_pairs)
    payload = {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "ticket": TICKET,
        "purpose": (
            "Frozen transition, evaluator, and end-to-end search differential "
            "inputs for native implementation work"
        ),
        "row_order": "bottom_to_top",
        "config_path": str(
            DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH.relative_to(REPO_ROOT)
        ),
        "config_sha256": file_sha256(DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH),
        "source_fixture_sha256": file_sha256(SOURCE_FIXTURE_PATH),
        "seed": 123,
        "cases": cases,
        "search_case": {
            "case_id": "seed-123-initial-mini-search",
            "state": _state_payload(search_state),
            "known_pairs": [
                [color.name for color in pair] for pair in known_pairs
            ],
            "config": dict(CORPUS_SEARCH_CONFIG),
            "expected_action_id": int(search_result["selected_action"]),
            "expected_result_digest": _stable_digest(
                search_result,
                prefix="puyo-198-search-result",
            ),
            "expected": search_result,
        },
    }
    payload["corpus_digest"] = _stable_digest(
        payload,
        prefix="puyo-198-frozen-corpus",
    )
    return payload


def verify_frozen_corpus(
    path: str | Path = DEFAULT_CORPUS_PATH,
    *,
    execute_search: bool = True,
) -> list[str]:
    issues: list[str] = []
    payload = _read_json(path)
    if payload.get("schema_version") != CORPUS_SCHEMA_VERSION:
        issues.append("unsupported corpus schema_version")
    expected_digest = payload.get("corpus_digest")
    digest_payload = dict(payload)
    digest_payload.pop("corpus_digest", None)
    actual_digest = _stable_digest(
        digest_payload,
        prefix="puyo-198-frozen-corpus",
    )
    if expected_digest != actual_digest:
        issues.append("corpus_digest mismatch")
    if payload.get("config_sha256") != file_sha256(
        DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH
    ):
        issues.append("deep-chain config checksum mismatch")
    evaluator = ChainStructureEvaluator()
    for case in payload.get("cases", ()):
        case_id = str(case.get("case_id", "unknown"))
        try:
            state = _state_from_payload(case["state"])
            pair = _pair_from_names(case["pair"])
            action = int(case["action_id"])
            expected = case["expected"]
            transition_value = _transition_payload(transition(state, pair, action))
            evaluation_value = _evaluation_payload(
                evaluator.evaluate(state, target_chain_count=6)
            )
        except Exception as exc:  # noqa: BLE001 - verifier reports all cases
            issues.append(f"{case_id}: execution failed: {type(exc).__name__}: {exc}")
            continue
        if list(legal_action_indices(state)) != expected.get("legal_action_ids"):
            issues.append(f"{case_id}: legal action IDs mismatch")
        if _stable_digest(
            transition_value,
            prefix="puyo-198-transition-result",
        ) != expected.get("transition_digest"):
            issues.append(f"{case_id}: transition digest mismatch")
        if _stable_digest(
            evaluation_value,
            prefix="puyo-198-evaluation-result",
        ) != expected.get("evaluation_digest"):
            issues.append(f"{case_id}: evaluator digest mismatch")

    if execute_search:
        case = payload.get("search_case", {})
        try:
            state = _state_from_payload(case["state"])
            known_pairs = tuple(
                _pair_from_names(pair) for pair in case["known_pairs"]
            )
            actual = _run_corpus_search(state, known_pairs)
            actual_digest = _stable_digest(
                actual,
                prefix="puyo-198-search-result",
            )
            if int(actual["selected_action"]) != int(case["expected_action_id"]):
                issues.append("search case selected action mismatch")
            if actual_digest != case.get("expected_result_digest"):
                issues.append("search case result digest mismatch")
        except Exception as exc:  # noqa: BLE001 - verifier emits useful failure
            issues.append(
                f"search case execution failed: {type(exc).__name__}: {exc}"
            )
    return issues


def _profile_for_name(name: str) -> DeepChainBuilderProfile:
    if name == "intermediate":
        return INTERMEDIATE_PROFILE
    return load_deep_chain_builder_config().profile(name)


def _initial_observation_and_info(
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_deep_chain_builder_config()
    max_steps = config.benchmark.max_steps
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


def _relative_filename(filename: str) -> str:
    try:
        return str(Path(filename).resolve().relative_to(REPO_ROOT))
    except (OSError, ValueError):
        return filename


def classify_hotspot(
    filename: str,
    function_name: str,
    *,
    line: int | None = None,
) -> str:
    """Map profiler rows to non-overlapping native-boundary work units."""

    path = filename.replace("\\", "/")
    name = function_name
    digest_names = (
        "digest",
        "fingerprint",
        "to_bytes",
        "canonical_signature",
    )
    if any(token in name.lower() for token in digest_names):
        return "digest"
    if path.endswith("agents/compact_search.py"):
        if "CompactTranspositionKey" in name or (
            line is not None and 212 <= line < 240
        ):
            return "transposition_table"
        return "transition"
    if path.endswith("agents/chain_structure.py"):
        if name in {
            "extract_components",
            "connection_candidates",
            "_component_extensions",
            "_reachable_columns",
        }:
            return "component_extraction"
        if name == "_score":
            return "evaluator_score"
        if name in {"evaluate", "_build_features", "_action_features"}:
            return "evaluator_orchestration"
        return "bounded_quiescence"
    if path.endswith("agents/long_horizon_search.py"):
        if name in {"_prune_survivors", "_survivor_sort_key"}:
            return "beam_prune"
        if name in {"_new_node", "__init__", "__post_init__"}:
            return "candidate_generation"
        if name in {
            "aggregate_expected_chain_evidence",
            "to_evidence",
            "finish",
        }:
            return "scenario_aggregation"
        if name == "_root_build_diagnostics":
            return "diagnostics"
        if name.startswith("build_scenario") or "scenario_pairs" in name:
            return "scenario_generation"
        return "search_control"
    if path.endswith("agents/deep_chain_builder.py"):
        if name in {
            "run",
            "_representative_payload",
            "_selected_plan",
            "_selection_evidence",
        }:
            return "aggregation_serialization"
        return "decision_orchestration"
    if path.endswith("agents/decision_flow.py"):
        return "decision_orchestration"
    if "/json/" in path or path.endswith("json/__init__.py"):
        return "aggregation_serialization"
    return "other"


def summarize_cprofile(
    profiler: cProfile.Profile,
    *,
    expanded_nodes: int,
    evaluated_nodes: int,
) -> dict[str, Any]:
    stats = pstats.Stats(profiler)
    rows = []
    groups: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "primitive_calls": 0,
            "total_calls": 0,
            "exclusive_seconds": 0.0,
        }
    )
    resolved_groups: dict[tuple[str, int, str], str] = {}

    def resolve_group(
        key: tuple[str, int, str],
        trail: frozenset[tuple[str, int, str]] = frozenset(),
    ) -> str:
        cached = resolved_groups.get(key)
        if cached is not None:
            return cached
        filename, _line, function_name = key
        direct = classify_hotspot(
            _relative_filename(filename),
            function_name,
            line=_line,
        )
        if direct != "other":
            resolved_groups[key] = direct
            return direct
        if key in trail or len(trail) >= 6:
            return "other"
        values = stats.stats.get(key)
        callers = {} if values is None else values[4]
        candidates: Counter[str] = Counter()
        for caller_key, caller_value in callers.items():
            caller_group = resolve_group(caller_key, trail | {key})
            if caller_group == "other":
                continue
            if isinstance(caller_value, tuple):
                call_count = float(caller_value[1])
                caller_time = float(caller_value[3])
            else:
                call_count = float(caller_value)
                caller_time = 0.0
            candidates[caller_group] += caller_time or call_count
        resolved = (
            max(candidates, key=lambda group: (candidates[group], group))
            if candidates
            else "other"
        )
        resolved_groups[key] = resolved
        return resolved

    for key, values in stats.stats.items():
        filename, line, function_name = key
        primitive_calls, total_calls, exclusive, inclusive, _callers = values
        relative = _relative_filename(filename)
        direct_group = classify_hotspot(relative, function_name, line=line)
        group = resolve_group(key)
        row = {
            "function": f"{relative}:{line}:{function_name}",
            "group": group,
            "direct_group": direct_group,
            "caller_attributed": direct_group == "other" and group != "other",
            "primitive_calls": int(primitive_calls),
            "total_calls": int(total_calls),
            "exclusive_seconds": float(exclusive),
            "inclusive_seconds": float(inclusive),
            "exclusive_per_call_us": (
                None
                if total_calls <= 0
                else float(exclusive * 1_000_000.0 / total_calls)
            ),
            "exclusive_per_expanded_node_us": (
                None
                if expanded_nodes <= 0
                else float(exclusive * 1_000_000.0 / expanded_nodes)
            ),
            "exclusive_per_evaluated_node_us": (
                None
                if evaluated_nodes <= 0
                else float(exclusive * 1_000_000.0 / evaluated_nodes)
            ),
        }
        rows.append(row)
        groups[group]["primitive_calls"] += int(primitive_calls)
        groups[group]["total_calls"] += int(total_calls)
        groups[group]["exclusive_seconds"] += float(exclusive)

    total_exclusive = float(stats.total_tt)
    group_rows = []
    for name, values in groups.items():
        exclusive = float(values["exclusive_seconds"])
        group_rows.append(
            {
                "group": name,
                **values,
                "exclusive_share": (
                    0.0 if total_exclusive <= 0.0 else exclusive / total_exclusive
                ),
                "exclusive_per_expanded_node_us": (
                    None
                    if expanded_nodes <= 0
                    else exclusive * 1_000_000.0 / expanded_nodes
                ),
            }
        )
    return {
        "profiler": "cProfile deterministic call profiler",
        "total_primitive_calls": int(stats.prim_calls),
        "total_calls": int(stats.total_calls),
        "total_exclusive_seconds": total_exclusive,
        "inclusive_time_note": (
            "Function inclusive times overlap and must not be summed; group shares "
            "use non-overlapping exclusive time."
        ),
        "groups": sorted(
            group_rows,
            key=lambda item: float(item["exclusive_seconds"]),
            reverse=True,
        ),
        "functions": sorted(
            rows,
            key=lambda item: float(item["exclusive_seconds"]),
            reverse=True,
        ),
    }


def _frame_name(frame: Any) -> str:
    filename = _relative_filename(frame.f_code.co_filename)
    return f"{filename}:{frame.f_lineno}:{frame.f_code.co_name}"


def _sample_stack(thread_id: int) -> tuple[str, ...]:
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return ()
    stack = []
    while frame is not None:
        stack.append(_frame_name(frame))
        frame = frame.f_back
    return tuple(reversed(stack))


class _StackSampler:
    def __init__(
        self,
        thread_id: int,
        *,
        interval_seconds: float,
        sink: Callable[[tuple[str, ...]], None] | None = None,
    ) -> None:
        if interval_seconds <= 0.0:
            raise ValueError("sampling interval must be positive")
        self.thread_id = int(thread_id)
        self.interval_seconds = float(interval_seconds)
        self.sink = sink
        self.samples: list[tuple[str, ...]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="puyo-198-stack-sampler",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds * 4.0))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            stack = _sample_stack(self.thread_id)
            if not stack:
                continue
            if self.sink is None:
                self.samples.append(stack)
            else:
                self.sink(stack)


def summarize_samples(
    samples: Sequence[Sequence[str]],
    *,
    interval_seconds: float,
) -> dict[str, Any]:
    leaf_counts: Counter[str] = Counter()
    stack_counts: Counter[tuple[str, ...]] = Counter()
    group_counts: Counter[str] = Counter()
    for sample in samples:
        relevant = tuple(
            frame
            for frame in sample
            if frame.startswith(("agents/", "eval/"))
        )
        selected = relevant or tuple(sample)
        if not selected:
            continue
        leaf = selected[-1]
        leaf_counts[leaf] += 1
        stack_counts[selected] += 1
        parts = leaf.rsplit(":", 2)
        filename = parts[0] if parts else leaf
        function_name = parts[-1] if len(parts) >= 2 else leaf
        try:
            line = int(parts[1]) if len(parts) >= 3 else None
        except ValueError:
            line = None
        group_counts[
            classify_hotspot(filename, function_name, line=line)
        ] += 1
    total = sum(leaf_counts.values())
    return {
        "profiler": "periodic Python frame stack sampler",
        "interval_seconds": float(interval_seconds),
        "sample_count": int(total),
        "sampled_duration_seconds": float(total * interval_seconds),
        "groups": [
            {
                "group": group,
                "samples": int(count),
                "share": 0.0 if total == 0 else count / float(total),
            }
            for group, count in group_counts.most_common()
        ],
        "leaf_functions": [
            {
                "function": function,
                "samples": int(count),
                "share": 0.0 if total == 0 else count / float(total),
            }
            for function, count in leaf_counts.most_common(100)
        ],
        "stacks": [
            {
                "stack": list(stack),
                "samples": int(count),
                "share": 0.0 if total == 0 else count / float(total),
            }
            for stack, count in stack_counts.most_common(100)
        ],
    }


def run_profile_workload(
    profile_name: str,
    *,
    seed: int = 123,
    sample_interval_seconds: float = 0.01,
) -> dict[str, Any]:
    profile = _profile_for_name(profile_name)
    observation, info = _initial_observation_and_info(seed)
    policy = DeepChainBuilderPolicy(profile=profile)
    profiler = cProfile.Profile()
    main_thread = threading.get_ident()
    sampler = _StackSampler(
        main_thread,
        interval_seconds=sample_interval_seconds,
    )
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    wall_started = time.perf_counter()
    process_started = time.process_time()
    sampler.start()
    profiler.enable()
    action = int(policy.select_action(observation, info))
    diagnostics = policy.tactical_diagnostics
    profiler.disable()
    sampler.stop()
    wall_seconds = time.perf_counter() - wall_started
    process_seconds = time.process_time() - process_started
    usage_after = resource.getrusage(resource.RUSAGE_SELF)
    search = diagnostics.get("search", {})
    counters = search.get("counters", {}) if isinstance(search, Mapping) else {}
    expanded = int(counters.get("expanded_nodes", 0))
    evaluated = int(counters.get("evaluated_nodes", 0))
    profile_stats = summarize_cprofile(
        profiler,
        expanded_nodes=expanded,
        evaluated_nodes=evaluated,
    )
    result_identity = {
        "action": action,
        "search_digest": search.get("deterministic_digest"),
        "plan_id": diagnostics.get("plan_id"),
        "fallback": diagnostics.get("fallback"),
    }
    return {
        "schema_version": PROFILE_RUN_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "evaluated_commit": git_commit(REPO_ROOT),
        "configuration_sha256": file_sha256(
            DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH
        ),
        "profile": profile.to_dict(),
        "canonical_quality_evidence": False,
        "seed": int(seed),
        "warmup_iterations": 0,
        "measured_iterations": 1,
        "system": system_metadata(),
        "timing": {
            "wall_seconds": float(wall_seconds),
            "process_seconds": float(process_seconds),
            "user_seconds": float(usage_after.ru_utime - usage_before.ru_utime),
            "system_seconds": float(usage_after.ru_stime - usage_before.ru_stime),
            "peak_rss_kib": int(usage_after.ru_maxrss),
        },
        "result": {
            **_json_ready(result_identity),
            "result_digest": _stable_digest(
                result_identity,
                prefix="puyo-198-profile-result",
            ),
            "search_counters": _json_ready(counters),
            "decision_trace": _json_ready(diagnostics.get("decision_trace", {})),
        },
        "deterministic_profile": profile_stats,
        "sampling_profile": summarize_samples(
            sampler.samples,
            interval_seconds=sample_interval_seconds,
        ),
    }


def _reference_worker(
    queue: Any,
    seed: int,
    sample_interval_seconds: float,
) -> None:
    observation, info = _initial_observation_and_info(seed)
    policy = DeepChainBuilderPolicy(profile="reference")
    thread_id = threading.get_ident()
    sampler = _StackSampler(
        thread_id,
        interval_seconds=sample_interval_seconds,
        sink=lambda stack: queue.put({"kind": "sample", "stack": stack}),
    )
    queue.put({"kind": "started", "monotonic": time.perf_counter()})
    started = time.perf_counter()
    sampler.start()
    try:
        action = int(policy.select_action(observation, info))
        diagnostics = policy.tactical_diagnostics
        queue.put(
            {
                "kind": "result",
                "completed": True,
                "elapsed_seconds": float(time.perf_counter() - started),
                "action": action,
                "search": _json_ready(diagnostics.get("search", {})),
                "fallback": _json_ready(diagnostics.get("fallback", {})),
            }
        )
    except BaseException as exc:  # noqa: BLE001 - child must report failures
        queue.put(
            {
                "kind": "result",
                "completed": False,
                "elapsed_seconds": float(time.perf_counter() - started),
                "error": {"type": type(exc).__name__, "detail": str(exc)},
            }
        )
    finally:
        sampler.stop()


def run_reference_probe(
    *,
    seed: int = 123,
    timeout_seconds: float = 10.0,
    sample_interval_seconds: float = 0.01,
) -> dict[str, Any]:
    config = load_deep_chain_builder_config()
    gate = float(config.benchmark.maximum_decision_p95_seconds)
    if timeout_seconds < gate:
        raise ValueError("reference timeout must be at least the locked p95 gate")
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(
        target=_reference_worker,
        args=(queue, int(seed), float(sample_interval_seconds)),
    )
    supervisor_started = time.perf_counter()
    process.start()
    samples: list[tuple[str, ...]] = []
    result: dict[str, Any] | None = None
    worker_started = False
    startup_deadline = time.perf_counter() + 60.0
    decision_deadline: float | None = None
    while process.is_alive():
        now = time.perf_counter()
        deadline = decision_deadline if decision_deadline is not None else startup_deadline
        if now >= deadline:
            break
        try:
            message = queue.get(timeout=min(0.25, max(0.01, deadline - now)))
        except Empty:
            continue
        kind = message.get("kind")
        if kind == "started":
            worker_started = True
            decision_deadline = time.perf_counter() + float(timeout_seconds)
        elif kind == "sample":
            samples.append(tuple(message.get("stack", ())))
        elif kind == "result":
            result = dict(message)
    timed_out = process.is_alive()
    if timed_out:
        process.terminate()
    process.join(timeout=5.0)
    while True:
        try:
            message = queue.get_nowait()
        except Empty:
            break
        if message.get("kind") == "sample":
            samples.append(tuple(message.get("stack", ())))
        elif message.get("kind") == "result":
            result = dict(message)
    queue.close()
    supervisor_seconds = time.perf_counter() - supervisor_started
    completed = bool(result and result.get("completed"))
    measured = float(result["elapsed_seconds"]) if completed else None
    lower_bound = float(timeout_seconds) if timed_out and worker_started else measured
    return {
        "schema_version": REFERENCE_PROBE_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "evaluated_commit": git_commit(REPO_ROOT),
        "configuration_sha256": file_sha256(
            DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH
        ),
        "canonical_quality_evidence": False,
        "profile": config.profile("reference").to_dict(),
        "seed": int(seed),
        "timeout_seconds": float(timeout_seconds),
        "worker_started": bool(worker_started),
        "timed_out": bool(timed_out),
        "child_exit_code": process.exitcode,
        "supervisor_elapsed_seconds": float(supervisor_seconds),
        "decision_elapsed_seconds": measured,
        "decision_latency_lower_bound_seconds": lower_bound,
        "locked_maximum_decision_p95_seconds": gate,
        "performance_gate_passed": bool(completed and measured is not None and measured <= gate),
        "result": result,
        "sampling_profile": summarize_samples(
            samples,
            interval_seconds=sample_interval_seconds,
        ),
        "interpretation": (
            "reference first decision exceeded the supervised budget; the "
            "terminated process contributes latency and sampling evidence only"
            if timed_out
            else "reference first decision completed under supervision"
        ),
    }


def _measure_operation(
    operation: Callable[[], Any],
    *,
    iterations: int,
    warmup_iterations: int,
    digest_prefix: str,
) -> dict[str, Any]:
    if iterations <= 0 or warmup_iterations < 0:
        raise ValueError("microbenchmark iterations are invalid")
    for _ in range(warmup_iterations):
        operation()
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    wall_started = time.perf_counter()
    process_started = time.process_time()
    outputs = [operation() for _ in range(iterations)]
    wall_seconds = time.perf_counter() - wall_started
    process_seconds = time.process_time() - process_started
    usage_after = resource.getrusage(resource.RUSAGE_SELF)
    output_digest = _stable_digest(outputs, prefix=digest_prefix)
    return {
        "warmup_iterations": int(warmup_iterations),
        "iterations": int(iterations),
        "wall_seconds": float(wall_seconds),
        "process_seconds": float(process_seconds),
        "user_seconds": float(usage_after.ru_utime - usage_before.ru_utime),
        "system_seconds": float(usage_after.ru_stime - usage_before.ru_stime),
        "mean_wall_us": float(wall_seconds * 1_000_000.0 / iterations),
        "operations_per_second": float(iterations / wall_seconds),
        "peak_rss_kib": int(usage_after.ru_maxrss),
        "output_digest": output_digest,
    }


def run_microbenchmarks(
    corpus_path: str | Path = DEFAULT_CORPUS_PATH,
    *,
    transition_iterations: int = 500,
    evaluator_iterations: int = 12,
    serialization_iterations: int = 1000,
) -> dict[str, Any]:
    corpus = _read_json(corpus_path)
    cases = []
    for case in corpus["cases"]:
        cases.append(
            (
                _state_from_payload(case["state"]),
                _pair_from_names(case["pair"]),
                int(case["action_id"]),
            )
        )
    cursor = {"value": 0}

    def transition_operation() -> tuple[Any, ...]:
        index = cursor["value"] % len(cases)
        cursor["value"] += 1
        state, pair, action = cases[index]
        result = transition(state, pair, action)
        return (
            result.action_id,
            result.valid,
            result.chain_count,
            result.score_delta,
            result.game_over,
            result.state.to_bytes().hex(),
        )

    evaluator = ChainStructureEvaluator()

    def evaluator_operation() -> tuple[Any, ...]:
        index = cursor["value"] % len(cases)
        cursor["value"] += 1
        state = cases[index][0]
        result = evaluator.evaluate(state, target_chain_count=6)
        return (
            result.evaluation_status,
            result.score,
            result.tie_break_digest,
            result.quiescence.pattern_nodes,
            result.quiescence.resolution_nodes,
        )

    serialization_payload = {
        "state": corpus["search_case"]["state"],
        "known_pairs": corpus["search_case"]["known_pairs"],
        "config": corpus["search_case"]["config"],
        "expected": corpus["search_case"]["expected"],
    }

    def serialization_operation() -> str:
        return _stable_digest(
            serialization_payload,
            prefix="puyo-198-boundary-serialization",
        )

    def transposition_key_operation() -> int:
        index = cursor["value"] % len(cases)
        cursor["value"] += 1
        key = CompactTranspositionKey(
            cases[index][0],
            scenario_id=index,
            pair_cursor=3,
            depth=3,
        )
        return hash(key)

    search_case = corpus["search_case"]
    search_state = _state_from_payload(search_case["state"])
    search_pairs = tuple(
        _pair_from_names(pair) for pair in search_case["known_pairs"]
    )

    def search_operation() -> str:
        result = _run_corpus_search(search_state, search_pairs)
        return _stable_digest(result, prefix="puyo-198-search-result")

    benchmarks = {
        "transition": _measure_operation(
            transition_operation,
            iterations=transition_iterations,
            warmup_iterations=len(cases),
            digest_prefix="puyo-198-transition-microbenchmark",
        ),
        "evaluator": _measure_operation(
            evaluator_operation,
            iterations=evaluator_iterations,
            warmup_iterations=len(cases),
            digest_prefix="puyo-198-evaluator-microbenchmark",
        ),
        "serialization": _measure_operation(
            serialization_operation,
            iterations=serialization_iterations,
            warmup_iterations=10,
            digest_prefix="puyo-198-serialization-microbenchmark",
        ),
        "transposition_key": _measure_operation(
            transposition_key_operation,
            iterations=transition_iterations,
            warmup_iterations=len(cases),
            digest_prefix="puyo-198-transposition-key-microbenchmark",
        ),
        "mini_search": _measure_operation(
            search_operation,
            iterations=1,
            warmup_iterations=0,
            digest_prefix="puyo-198-search-microbenchmark",
        ),
    }
    return {
        "schema_version": MICROBENCHMARK_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "evaluated_commit": git_commit(REPO_ROOT),
        "configuration_sha256": file_sha256(
            DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH
        ),
        "corpus_path": str(Path(corpus_path)),
        "corpus_digest": corpus.get("corpus_digest"),
        "canonical_quality_evidence": False,
        "system": system_metadata(),
        "benchmarks": benchmarks,
    }


_BUDGET_GROUPS = {
    "transition": ("transition",),
    "evaluator": (
        "component_extraction",
        "bounded_quiescence",
        "evaluator_score",
        "evaluator_orchestration",
    ),
    "search": (
        "candidate_generation",
        "digest",
        "beam_prune",
        "transposition_table",
        "scenario_generation",
        "search_control",
    ),
    "serialization": ("aggregation_serialization",),
    "aggregation": (
        "scenario_aggregation",
        "diagnostics",
        "decision_orchestration",
    ),
}


def derive_performance_budgets(
    intermediate_profile: Mapping[str, Any],
    *,
    gate_seconds: float,
    canonical_max_expanded_nodes: int,
) -> dict[str, Any]:
    groups = {
        str(row["group"]): float(row["exclusive_seconds"])
        for row in intermediate_profile["deterministic_profile"]["groups"]
    }
    category_times = {
        category: sum(groups.get(group, 0.0) for group in selected)
        for category, selected in _BUDGET_GROUPS.items()
    }
    measured_total = sum(category_times.values())
    if measured_total <= 0.0:
        raise ValueError("intermediate profile has no classified timing")
    shares = {
        category: seconds / measured_total
        for category, seconds in category_times.items()
    }
    safety_margin = gate_seconds * 0.10
    allocatable = gate_seconds - safety_margin
    floors = {"serialization": 0.02, "aggregation": 0.03}
    remaining = allocatable - sum(floors.values())
    weighted_categories = ("transition", "evaluator", "search")
    weighted_total = sum(shares[name] for name in weighted_categories)
    budgets = dict(floors)
    for category in weighted_categories:
        budgets[category] = (
            remaining / len(weighted_categories)
            if weighted_total <= 0.0
            else remaining * shares[category] / weighted_total
        )
    category_rows = []
    for category in ("transition", "evaluator", "search", "serialization", "aggregation"):
        budget = budgets[category]
        category_rows.append(
            {
                "category": category,
                "measured_exclusive_seconds": category_times[category],
                "measured_exclusive_share": shares[category],
                "decision_budget_seconds": budget,
                "canonical_per_expanded_node_budget_us": (
                    None
                    if category in {"serialization", "aggregation"}
                    else budget * 1_000_000.0 / canonical_max_expanded_nodes
                ),
            }
        )
    return {
        "locked_gate_seconds": float(gate_seconds),
        "safety_margin_seconds": float(safety_margin),
        "executable_budget_seconds": float(allocatable),
        "allocation_method": (
            "Reserve fixed one-call serialization/aggregation floors, then "
            "allocate the remaining gate by intermediate exclusive-time share."
        ),
        "canonical_max_expanded_nodes": int(canonical_max_expanded_nodes),
        "categories": category_rows,
        "integration_go_no_go": {
            "native_first_decision_p95_seconds": float(allocatable),
            "end_to_end_first_decision_p95_seconds": float(gate_seconds),
            "required_deterministic_digest_match": True,
            "required_parity_mismatch_count": 0,
            "required_private_future_leak_count": 0,
        },
    }


def _profile_group_table(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in profile["deterministic_profile"]["groups"]
        if row.get("group") != "other"
    ]


def finalize_evidence(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    root = Path(output_dir)
    smoke = _read_json(root / "raw" / "smoke_profile.json")
    intermediate = _read_json(root / "raw" / "intermediate_profile.json")
    reference = _read_json(root / "raw" / "reference_profile.json")
    micro = _read_json(root / "raw" / "microbenchmarks.json")
    corpus = _read_json(root / "corpus.json")
    config = load_deep_chain_builder_config()
    gate = config.benchmark.maximum_decision_p95_seconds
    budgets = derive_performance_budgets(
        intermediate,
        gate_seconds=gate,
        canonical_max_expanded_nodes=config.profile("reference").max_expanded_nodes,
    )
    baseline_lower_bound = None
    if PUYO_189_PREFLIGHT_PATH.exists():
        baseline_lower_bound = _read_json(PUYO_189_PREFLIGHT_PATH).get(
            "decision_latency_lower_bound_seconds"
        )
    current_lower_bound = reference.get("decision_latency_lower_bound_seconds")
    effective_lower_bound = max(
        float(value)
        for value in (baseline_lower_bound, current_lower_bound)
        if isinstance(value, (int, float))
    )
    classified = {
        row["group"]: float(row["exclusive_seconds"])
        for row in intermediate["deterministic_profile"]["groups"]
    }
    total = sum(classified.values())
    outside_native_groups = {"decision_orchestration", "other"}
    outside_seconds = sum(classified.get(group, 0.0) for group in outside_native_groups)
    outside_share = 0.0 if total <= 0.0 else outside_seconds / total
    native_share = 1.0 - outside_share
    target_ratio = gate / effective_lower_bound
    denominator = target_ratio - outside_share
    required_native_speedup = (
        None if denominator <= 0.0 else native_share / denominator
    )
    amdahl = {
        "reference_latency_lower_bound_seconds": effective_lower_bound,
        "locked_gate_seconds": float(gate),
        "required_end_to_end_speedup_lower_bound": effective_lower_bound / gate,
        "measured_native_boundary_share": native_share,
        "measured_python_boundary_share": outside_share,
        "maximum_speedup_if_native_work_were_free": (
            None if outside_share <= 0.0 else 1.0 / outside_share
        ),
        "required_native_work_speedup": required_native_speedup,
        "go": required_native_speedup is not None,
        "interpretation": (
            "The one-call boundary leaves a small enough measured Python share "
            "for the locked gate to remain mathematically reachable."
            if required_native_speedup is not None
            else "The measured Python remainder makes the locked gate unreachable."
        ),
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "evaluated_commit": intermediate.get("evaluated_commit"),
        "configuration_sha256": file_sha256(
            DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH
        ),
        "corpus_digest": corpus.get("corpus_digest"),
        "canonical_quality_evidence": False,
        "production_backend_changed": False,
        "profiles": {
            "smoke": {
                "completed": True,
                "wall_seconds": smoke["timing"]["wall_seconds"],
                "result": smoke["result"],
                "groups": _profile_group_table(smoke),
            },
            "intermediate": {
                "completed": True,
                "wall_seconds": intermediate["timing"]["wall_seconds"],
                "result": intermediate["result"],
                "groups": _profile_group_table(intermediate),
            },
            "reference": {
                "completed": bool(
                    reference.get("result")
                    and reference["result"].get("completed")
                ),
                "timed_out": bool(reference.get("timed_out")),
                "latency_lower_bound_seconds": current_lower_bound,
                "sample_count": reference["sampling_profile"]["sample_count"],
            },
        },
        "microbenchmarks": micro["benchmarks"],
        "hotspot_conclusion": {
            "deterministic_profile_basis": "intermediate",
            "exclusive_time_groups": _profile_group_table(intermediate),
            "sampling_groups": intermediate["sampling_profile"]["groups"],
            "single_stack_sample_used_as_statistic": False,
            "puyo_189_sample_role": "supporting prior evidence only",
        },
        "amdahl": amdahl,
        "performance_budgets": budgets,
        "native_boundary_decision": {
            "status": "go" if amdahl["go"] else "no_go",
            "calls_per_decision": 1,
            "includes": [
                "scenario completion",
                "compact transition",
                "chain evaluator and bounded quiescence",
                "candidate generation",
                "beam pruning and transposition table",
                "root aggregation and deterministic selection",
                "representative path and bounded diagnostics",
            ],
            "excludes": [
                "authoritative simulator mutation",
                "private future queue",
                "GUI rendering",
                "Python callbacks from native search nodes",
            ],
        },
    }
    summary["summary_digest"] = _stable_digest(
        summary,
        prefix="puyo-198-profile-summary",
    )
    _write_json(root / "benchmark_summary.json", summary)
    (root / "benchmark_report.md").write_text(
        _render_report(summary),
        encoding="utf-8",
    )
    _write_manifest(root, summary)
    return summary


def _render_report(summary: Mapping[str, Any]) -> str:
    profiles = summary["profiles"]
    lines = [
        "# PUYO-198 deep-chain native profile",
        "",
        "## Result",
        "",
        (
            "Smoke and intermediate profiles completed. The locked reference "
            f"first decision recorded a latency lower bound of "
            f"{profiles['reference']['latency_lower_bound_seconds']:.3f} seconds "
            "under supervision. This is performance evidence, not canonical "
            "quality evidence."
        ),
        "",
        "## Completed profiles",
        "",
        "| Workload | Wall seconds | Expanded nodes | Evaluated nodes |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name in ("smoke", "intermediate"):
        value = profiles[name]
        counters = value["result"]["search_counters"]
        lines.append(
            f"| {name} | {value['wall_seconds']:.6f} | "
            f"{counters.get('expanded_nodes', 0)} | {counters.get('evaluated_nodes', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Intermediate deterministic profile",
            "",
            (
                "Exclusive time is non-overlapping. Inclusive time and "
                "per-call/node costs remain available in "
                "`raw/intermediate_profile.json`."
            ),
            "",
            "| Group | Calls | Exclusive seconds | Share | us / expanded node |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in summary["hotspot_conclusion"]["exclusive_time_groups"]:
        per_node = row.get("exclusive_per_expanded_node_us")
        display_node = "n/a" if per_node is None else f"{per_node:.3f}"
        lines.append(
            f"| {row['group']} | {row['total_calls']} | "
            f"{row['exclusive_seconds']:.6f} | {row['exclusive_share']:.2%} | "
            f"{display_node} |"
        )
    lines.extend(
        [
            "",
            "## Gate budget",
            "",
            "| Category | Decision budget (s) | Canonical us / expanded node |",
            "| --- | ---: | ---: |",
        ]
    )
    for row in summary["performance_budgets"]["categories"]:
        per_node = row["canonical_per_expanded_node_budget_us"]
        display_node = "n/a" if per_node is None else f"{per_node:.6f}"
        lines.append(
            f"| {row['category']} | {row['decision_budget_seconds']:.6f} | "
            f"{display_node} |"
        )
    amdahl = summary["amdahl"]
    lines.extend(
        [
            "",
            "## Boundary decision",
            "",
            (
                f"The measured native candidate share is "
                f"{amdahl['measured_native_boundary_share']:.4%}. The prior "
                f"reference lower bound requires at least "
                f"{amdahl['required_end_to_end_speedup_lower_bound']:.1f}x end-to-end."
            ),
            "",
            (
                "Adopt one native call per decision covering transition through "
                "root aggregation. Per-node Python callbacks and Python object "
                "conversion are outside the accepted boundary."
            ),
            "",
            (
                "The PUYO-189 single sampled stack is retained only as supporting "
                "prior evidence; all percentages above come from PUYO-198 "
                "statistical and deterministic profiles."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _write_manifest(root: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = [
        describe_artifact(root / path, run_dir=root, role=role)
        for path, role in _PROFILE_ARTIFACTS
    ]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "evaluated_commit": summary.get("evaluated_commit"),
        "configuration_sha256": summary.get("configuration_sha256"),
        "corpus_digest": summary.get("corpus_digest"),
        "summary_digest": summary.get("summary_digest"),
        "artifacts": artifacts,
    }
    manifest["manifest_digest"] = _stable_digest(
        manifest,
        prefix="puyo-198-profile-manifest",
    )
    _write_json(root / "benchmark_manifest.json", manifest)
    return manifest


def verify_evidence(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> list[str]:
    root = Path(output_dir)
    issues: list[str] = []
    manifest_path = root / "benchmark_manifest.json"
    if not manifest_path.exists():
        return ["benchmark_manifest.json is missing"]
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        issues.append("unsupported manifest schema_version")
    digest_payload = dict(manifest)
    expected_manifest_digest = digest_payload.pop("manifest_digest", None)
    if _stable_digest(
        digest_payload,
        prefix="puyo-198-profile-manifest",
    ) != expected_manifest_digest:
        issues.append("manifest_digest mismatch")
    for record in manifest.get("artifacts", ()):
        path = root / str(record.get("path", ""))
        if not path.exists():
            issues.append(f"required artifact is missing: {record.get('path')}")
            continue
        if file_sha256(path) != record.get("sha256"):
            issues.append(f"artifact checksum mismatch: {record.get('path')}")
    summary_path = root / "benchmark_summary.json"
    if summary_path.exists():
        summary = _read_json(summary_path)
        if summary.get("schema_version") != SUMMARY_SCHEMA_VERSION:
            issues.append("unsupported summary schema_version")
        digest_payload = dict(summary)
        expected = digest_payload.pop("summary_digest", None)
        actual = _stable_digest(
            digest_payload,
            prefix="puyo-198-profile-summary",
        )
        if expected != actual or expected != manifest.get("summary_digest"):
            issues.append("summary_digest mismatch")
        if summary.get("production_backend_changed") is not False:
            issues.append("summary must confirm production backend is unchanged")
    corpus_path = root / "corpus.json"
    if corpus_path.exists():
        issues.extend(
            f"corpus: {issue}"
            for issue in verify_frozen_corpus(corpus_path, execute_search=False)
        )
    return issues


def run_all(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    reference_timeout_seconds: float = 10.0,
    sample_interval_seconds: float = 0.01,
) -> dict[str, Any]:
    root = Path(output_dir)
    corpus_issues = verify_frozen_corpus(DEFAULT_CORPUS_PATH, execute_search=True)
    if corpus_issues:
        raise AssertionError(f"frozen corpus verification failed: {corpus_issues}")
    corpus = _read_json(DEFAULT_CORPUS_PATH)
    _write_json(root / "corpus.json", corpus)
    _write_json(
        root / "raw" / "microbenchmarks.json",
        run_microbenchmarks(DEFAULT_CORPUS_PATH),
    )
    for profile_name in ("smoke", "intermediate"):
        _write_json(
            root / "raw" / f"{profile_name}_profile.json",
            _run_isolated_profile(
                profile_name,
                sample_interval_seconds=sample_interval_seconds,
            ),
        )
    _write_json(
        root / "raw" / "reference_profile.json",
        run_reference_probe(
            timeout_seconds=reference_timeout_seconds,
            sample_interval_seconds=sample_interval_seconds,
        ),
    )
    return finalize_evidence(root)


def _profile_worker(
    queue: Any,
    profile_name: str,
    sample_interval_seconds: float,
) -> None:
    try:
        queue.put(
            {
                "ok": True,
                "payload": run_profile_workload(
                    profile_name,
                    sample_interval_seconds=sample_interval_seconds,
                ),
            }
        )
    except BaseException as exc:  # noqa: BLE001 - parent needs child diagnostics
        queue.put(
            {
                "ok": False,
                "error": {"type": type(exc).__name__, "detail": str(exc)},
            }
        )


def _run_isolated_profile(
    profile_name: str,
    *,
    sample_interval_seconds: float,
) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(
        target=_profile_worker,
        args=(queue, profile_name, float(sample_interval_seconds)),
    )
    process.start()
    message = None
    deadline = time.perf_counter() + 900.0
    while process.is_alive() and time.perf_counter() < deadline:
        try:
            message = queue.get(timeout=0.25)
            break
        except Empty:
            continue
    if message is None:
        try:
            message = queue.get_nowait()
        except Empty:
            message = None
    if message is None:
        timed_out = process.is_alive()
        if timed_out:
            process.terminate()
        process.join(timeout=5.0)
        reason = "timed out" if timed_out else f"exited with {process.exitcode}"
        raise RuntimeError(f"isolated {profile_name} profile {reason}")
    process.join(timeout=10.0)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5.0)
    queue.close()
    if not message.get("ok"):
        error = message.get("error", {})
        raise RuntimeError(
            f"isolated {profile_name} profile failed: "
            f"{error.get('type')}: {error.get('detail')}"
        )
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise TypeError("isolated profile worker returned an invalid payload")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze-corpus")
    freeze.add_argument("--output", type=Path, default=DEFAULT_CORPUS_PATH)

    corpus = subparsers.add_parser("verify-corpus")
    corpus.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    corpus.add_argument("--skip-search", action="store_true")

    profile = subparsers.add_parser("profile")
    profile.add_argument("profile", choices=("smoke", "intermediate"))
    profile.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    profile.add_argument("--sample-interval", type=float, default=0.01)

    reference = subparsers.add_parser("reference")
    reference.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    reference.add_argument("--timeout-seconds", type=float, default=10.0)
    reference.add_argument("--sample-interval", type=float, default=0.01)

    micro = subparsers.add_parser("microbenchmark")
    micro.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    micro.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)

    all_parser = subparsers.add_parser("all")
    all_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    all_parser.add_argument("--reference-timeout", type=float, default=10.0)
    all_parser.add_argument("--sample-interval", type=float, default=0.01)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "freeze-corpus":
        result = build_frozen_corpus()
        _write_json(args.output, result)
    elif args.command == "verify-corpus":
        issues = verify_frozen_corpus(
            args.corpus,
            execute_search=not args.skip_search,
        )
        result = {"passed": not issues, "issues": issues}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if not issues else 1
    elif args.command == "profile":
        result = run_profile_workload(
            args.profile,
            sample_interval_seconds=args.sample_interval,
        )
        _write_json(
            args.output_dir / "raw" / f"{args.profile}_profile.json",
            result,
        )
    elif args.command == "reference":
        result = run_reference_probe(
            timeout_seconds=args.timeout_seconds,
            sample_interval_seconds=args.sample_interval,
        )
        _write_json(args.output_dir / "raw" / "reference_profile.json", result)
    elif args.command == "microbenchmark":
        result = run_microbenchmarks(args.corpus)
        _write_json(args.output_dir / "raw" / "microbenchmarks.json", result)
    elif args.command == "finalize":
        result = finalize_evidence(args.output_dir)
    elif args.command == "all":
        result = run_all(
            args.output_dir,
            reference_timeout_seconds=args.reference_timeout,
            sample_interval_seconds=args.sample_interval,
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
