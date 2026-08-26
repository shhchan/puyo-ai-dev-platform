"""PUYO-200 native compact-transition parity and performance evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import platform
import resource
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agents.compact_search import (
    COMPACT_SEARCH_SCHEMA_VERSION,
    CompactSearchSnapshot,
    CompactSearchState,
    legal_action_indices,
    symmetry_reduced_action_indices,
    transition,
)
from agents.deep_chain_native_transition import (
    NATIVE_COMPACT_KERNEL_PATH,
    NATIVE_COMPACT_TRANSITION_SCHEMA_VERSION,
    NativeCompactBatchClient,
    NativeCompactTransitionInput,
)
from puyo_env.actions import action_to_placement
from puyo_env.actions import legal_action_indices as authoritative_legal_actions
from src.core.constants import GRID_WIDTH, PuyoColor
from src.core.game import GameState
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp

TICKET = "PUYO-200"
CORPUS_SCHEMA_VERSION = "puyo.native_compact_transition_corpus.v1"
BENCHMARK_SCHEMA_VERSION = "puyo.native_compact_transition_benchmark.v1"
DEFAULT_CORPUS_PATH = Path("eval/deep_chain_native_transition_corpus.json")
DEFAULT_FIXTURE_PATH = Path("tests/fixtures/compact_search_kernel_cases.json")
DEFAULT_OUTPUT_DIR = Path("docs/benchmarks/puyo-200-native-compact-transition")
DEFAULT_SEED_START = 123
DEFAULT_SEED_COUNT = 64
DEFAULT_MAX_TURNS = 8
MINIMUM_TRANSITIONS = 10_000
CANONICAL_MAX_EXPANDED_NODES = 600_000
TRANSITION_DECISION_P95_BUDGET_MS = 10.596
AMA_REFERENCE_COMMIT = "dea210bcd92965ae08fbc311f23565b0fab6dbbb"
_PLANE_COLORS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
    PuyoColor.PURPLE,
    PuyoColor.OJAMA,
)


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: Any, *, compact: bool = False) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    options = (
        {"sort_keys": True, "separators": (",", ":")}
        if compact
        else {"indent": 2, "sort_keys": True}
    )
    destination.write_text(json.dumps(payload, **options) + "\n", encoding="utf-8")


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state_from_hex(value: str) -> CompactSearchState:
    payload = bytes.fromhex(value)
    if len(payload) != 87 or payload[:4] != b"CSK1":
        raise ValueError("frozen compact state has invalid framing")
    offset = 4
    planes = []
    for _ in range(6):
        planes.append(int.from_bytes(payload[offset : offset + 11], "little"))
        offset += 11
    flags = payload[offset]
    offset += 1
    score = int.from_bytes(payload[offset : offset + 8], "little")
    offset += 8
    last_chain_end_score = int.from_bytes(payload[offset : offset + 8], "little")
    return CompactSearchState(
        planes=tuple(planes),
        all_clear_bonus_pending=bool(flags & 0x1),
        game_over=bool(flags & 0x2),
        score=score,
        last_chain_end_score=last_chain_end_score,
    )


def _pair_from_names(values: Sequence[str]) -> tuple[PuyoColor, PuyoColor]:
    if len(values) != 2:
        raise ValueError("frozen pair must have two colors")
    return (PuyoColor[values[0]], PuyoColor[values[1]])


def _simulator_from_state(
    state: CompactSearchState,
    pair: tuple[PuyoColor, PuyoColor],
) -> HeadlessPuyoSimulator:
    game = GameState(seed=0)
    game.spawn_puyo()
    for plane_index, plane in enumerate(state.planes):
        color = _PLANE_COLORS[plane_index]
        remaining = int(plane)
        while remaining:
            bit_index = (remaining & -remaining).bit_length() - 1
            x = bit_index % GRID_WIDTH
            y = bit_index // GRID_WIDTH
            game.field.place_puyo(x, y, Puyo(color))
            remaining &= remaining - 1
    game.current_puyo_1 = Puyo(pair[0])
    game.current_puyo_2 = Puyo(pair[1])
    game.all_clear_bonus_pending = state.all_clear_bonus_pending
    game.game_over = state.game_over
    game.score = state.score
    game.last_chain_end_score = state.last_chain_end_score
    game.state = "gameover" if state.game_over else "control"
    return HeadlessPuyoSimulator(game_state=game)


def _authoritative_garbage_count(chain) -> int:
    if not chain.board:
        raise ValueError("authoritative garbage parity requires visual capture")
    result = set()
    for x, y in chain.vanished:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            target_x, target_y = x + dx, y + dy
            if not (
                0 <= target_x < GRID_WIDTH
                and 0 <= target_y < 12
                and chain.board[target_y][target_x] == PuyoColor.OJAMA
            ):
                continue
            result.add((target_x, target_y))
    return len(result)


def _summary_payload(
    *,
    state: CompactSearchState,
    valid: bool,
    axis_y: int | None,
    score_delta: int,
    attack_score_delta: int,
    chain_count: int,
    vanished_count: int,
    garbage_cleared_count: int,
    game_over: bool,
    all_clear_achieved: bool,
    all_clear_bonus_pending: bool,
    all_clear_bonus_consumed: bool,
    all_clear_bonus_score: int,
) -> dict[str, Any]:
    return {
        "state": state.to_bytes().hex(),
        "valid": bool(valid),
        "axis_y": axis_y,
        "score_delta": int(score_delta),
        "attack_score_delta": int(attack_score_delta),
        "chain_count": int(chain_count),
        "vanished_count": int(vanished_count),
        "garbage_cleared_count": int(garbage_cleared_count),
        "game_over": bool(game_over),
        "all_clear_achieved": bool(all_clear_achieved),
        "all_clear_bonus_pending": bool(all_clear_bonus_pending),
        "all_clear_bonus_consumed": bool(all_clear_bonus_consumed),
        "all_clear_bonus_score": int(all_clear_bonus_score),
    }


def _python_summary(result) -> dict[str, Any]:
    return _summary_payload(
        state=result.state,
        valid=result.valid,
        axis_y=result.axis_y,
        score_delta=result.score_delta,
        attack_score_delta=result.attack_score_delta,
        chain_count=result.chain_count,
        vanished_count=result.vanished_count,
        garbage_cleared_count=result.garbage_cleared_count,
        game_over=result.game_over,
        all_clear_achieved=result.all_clear_achieved,
        all_clear_bonus_pending=result.all_clear_bonus_pending,
        all_clear_bonus_consumed=result.all_clear_bonus_consumed,
        all_clear_bonus_score=result.all_clear_bonus_score,
    )


def _authoritative_summary(simulator, result) -> dict[str, Any]:
    return _summary_payload(
        state=CompactSearchState.from_simulator(simulator),
        valid=result.valid,
        axis_y=result.axis_y,
        score_delta=result.score_delta,
        attack_score_delta=result.attack_score_delta,
        chain_count=result.chain_count,
        vanished_count=sum(step.vanished_count for step in result.chains),
        garbage_cleared_count=sum(
            _authoritative_garbage_count(step) for step in result.chains
        ),
        game_over=result.game_over,
        all_clear_achieved=result.all_clear_achieved,
        all_clear_bonus_pending=result.all_clear_bonus_pending,
        all_clear_bonus_consumed=result.all_clear_bonus_consumed,
        all_clear_bonus_score=result.all_clear_bonus_score,
    )


def _native_summary(result) -> dict[str, Any]:
    return _summary_payload(
        state=result.state,
        valid=result.valid,
        axis_y=result.axis_y,
        score_delta=result.score_delta,
        attack_score_delta=result.attack_score_delta,
        chain_count=result.chain_count,
        vanished_count=result.vanished_count,
        garbage_cleared_count=result.garbage_cleared_count,
        game_over=result.game_over,
        all_clear_achieved=result.all_clear_achieved,
        all_clear_bonus_pending=result.all_clear_bonus_pending,
        all_clear_bonus_consumed=result.all_clear_bonus_consumed,
        all_clear_bonus_score=result.all_clear_bonus_score,
    )


def _choose_trajectory_action(
    *,
    seed: int,
    turn: int,
    legal: Sequence[int],
    children: Mapping[int, HeadlessPuyoSimulator],
) -> int:
    ordered = list(legal)
    offset = (int(seed) * 31 + int(turn) * 17) % len(ordered)
    ordered = ordered[offset:] + ordered[:offset]
    return next(
        (action for action in ordered if not children[action].game.game_over),
        ordered[0],
    )


def generate_frozen_corpus(
    *,
    output_path: str | Path = DEFAULT_CORPUS_PATH,
    seed_start: int = DEFAULT_SEED_START,
    seed_count: int = DEFAULT_SEED_COUNT,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> dict[str, Any]:
    records = []
    mismatches = []
    transition_count = 0
    for seed in range(seed_start, seed_start + seed_count):
        simulator = HeadlessPuyoSimulator(seed=seed)
        for turn in range(max_turns):
            snapshot = CompactSearchSnapshot.from_simulator(simulator)
            pair = snapshot.current_pair
            if pair is None:
                break
            legal = tuple(authoritative_legal_actions(simulator))
            compact_legal = legal_action_indices(snapshot.state)
            if not legal:
                break
            if legal != compact_legal:
                mismatches.append(
                    {
                        "seed": seed,
                        "turn": turn,
                        "kind": "legal_actions",
                    }
                )
            expected = []
            children = {}
            for action in legal:
                child = copy.deepcopy(simulator)
                authoritative_result = child.step(
                    action_to_placement(action),
                    capture_visuals=True,
                )
                python_result = transition(snapshot.state, pair, action)
                authoritative_payload = _authoritative_summary(
                    child,
                    authoritative_result,
                )
                python_payload = _python_summary(python_result)
                if authoritative_payload != python_payload:
                    mismatches.append(
                        {
                            "seed": seed,
                            "turn": turn,
                            "action": action,
                            "kind": "transition",
                            "authoritative": _digest(authoritative_payload),
                            "python": _digest(python_payload),
                        }
                    )
                expected.append(
                    {
                        "action": int(action),
                        "result_digest": _digest(python_payload),
                    }
                )
                children[int(action)] = child
                transition_count += 1
            selected_action = _choose_trajectory_action(
                seed=seed,
                turn=turn,
                legal=legal,
                children=children,
            )
            records.append(
                {
                    "seed": int(seed),
                    "turn": int(turn),
                    "state": snapshot.state.to_bytes().hex(),
                    "pair": [pair[0].name, pair[1].name],
                    "legal_actions": list(legal),
                    "symmetry_reduced_actions": list(
                        symmetry_reduced_action_indices(snapshot.state, pair)
                    ),
                    "selected_action": int(selected_action),
                    "expected": expected,
                }
            )
            simulator = children[selected_action]
            if simulator.game.game_over:
                break
    if transition_count < MINIMUM_TRANSITIONS:
        raise RuntimeError(
            f"frozen corpus has {transition_count} transitions; "
            f"at least {MINIMUM_TRANSITIONS} are required"
        )
    if mismatches:
        raise RuntimeError(
            f"cannot freeze a corpus with {len(mismatches)} oracle mismatches"
        )
    generator = {
        "seed_start": int(seed_start),
        "seed_count": int(seed_count),
        "max_turns": int(max_turns),
        "trajectory_selection": "rotate legal actions by seed*31+turn*17; prefer first survivor",
        "all_legal_actions_per_state": True,
    }
    payload = {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "source_commit": git_commit(),
        "contracts": {
            "state": COMPACT_SEARCH_SCHEMA_VERSION,
            "native_batch": NATIVE_COMPACT_TRANSITION_SCHEMA_VERSION,
            "authoritative_oracle": "src.core.headless.HeadlessPuyoSimulator",
        },
        "generator": generator,
        "state_count": len(records),
        "transition_count": transition_count,
        "minimum_transition_count": MINIMUM_TRANSITIONS,
        "records": records,
    }
    payload["input_digest"] = _digest(
        [
            {
                "state": record["state"],
                "pair": record["pair"],
                "actions": record["legal_actions"],
            }
            for record in records
        ]
    )
    payload["expected_digest"] = _digest([record["expected"] for record in records])
    payload["corpus_digest"] = _digest(
        {
            "schema_version": payload["schema_version"],
            "generator": generator,
            "records": records,
            "input_digest": payload["input_digest"],
            "expected_digest": payload["expected_digest"],
        }
    )
    _write_json(output_path, payload, compact=True)
    return payload


def _flatten_inputs(
    corpus: Mapping[str, Any],
) -> tuple[NativeCompactTransitionInput, ...]:
    inputs = []
    for record in corpus["records"]:
        state = _state_from_hex(record["state"])
        pair = _pair_from_names(record["pair"])
        for expected in record["expected"]:
            inputs.append(
                NativeCompactTransitionInput(
                    state,
                    pair,
                    int(expected["action"]),
                )
            )
    return tuple(inputs)


def verify_frozen_corpus(
    corpus_path: str | Path = DEFAULT_CORPUS_PATH,
) -> dict[str, Any]:
    corpus = _read_json(corpus_path)
    issues = []
    mismatches = []
    if corpus.get("schema_version") != CORPUS_SCHEMA_VERSION:
        issues.append("unexpected frozen corpus schema")
    records = corpus.get("records", [])
    transition_count = sum(len(record.get("expected", [])) for record in records)
    if transition_count != corpus.get("transition_count"):
        issues.append("frozen transition count does not match records")
    if transition_count < MINIMUM_TRANSITIONS:
        issues.append("frozen corpus does not meet 10,000-transition minimum")
    actual_input_digest = _digest(
        [
            {
                "state": record["state"],
                "pair": record["pair"],
                "actions": record["legal_actions"],
            }
            for record in records
        ]
    )
    actual_expected_digest = _digest([record["expected"] for record in records])
    if actual_input_digest != corpus.get("input_digest"):
        issues.append("frozen input digest mismatch")
    if actual_expected_digest != corpus.get("expected_digest"):
        issues.append("frozen expected digest mismatch")

    checked = 0
    for record in records:
        state = _state_from_hex(record["state"])
        pair = _pair_from_names(record["pair"])
        simulator = _simulator_from_state(state, pair)
        authoritative_legal = tuple(authoritative_legal_actions(simulator))
        python_legal = legal_action_indices(state)
        stored_legal = tuple(int(action) for action in record["legal_actions"])
        if authoritative_legal != stored_legal or python_legal != stored_legal:
            mismatches.append(
                {
                    "seed": record["seed"],
                    "turn": record["turn"],
                    "kind": "legal_actions",
                }
            )
        reduced = symmetry_reduced_action_indices(state, pair)
        if reduced != tuple(record["symmetry_reduced_actions"]):
            mismatches.append(
                {
                    "seed": record["seed"],
                    "turn": record["turn"],
                    "kind": "symmetry_reduced_actions",
                }
            )
        for expected in record["expected"]:
            action = int(expected["action"])
            child = copy.deepcopy(simulator)
            authoritative_result = child.step(
                action_to_placement(action),
                capture_visuals=True,
            )
            python_result = transition(state, pair, action)
            authoritative_payload = _authoritative_summary(
                child,
                authoritative_result,
            )
            python_payload = _python_summary(python_result)
            expected_digest = str(expected["result_digest"])
            if (
                authoritative_payload != python_payload
                or _digest(python_payload) != expected_digest
            ):
                mismatches.append(
                    {
                        "seed": record["seed"],
                        "turn": record["turn"],
                        "action": action,
                        "kind": "transition",
                        "authoritative_digest": _digest(authoritative_payload),
                        "python_digest": _digest(python_payload),
                        "expected_digest": expected_digest,
                    }
                )
            checked += 1
    passed = not issues and not mismatches
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "corpus_path": str(corpus_path),
        "corpus_sha256": file_sha256(corpus_path),
        "corpus_digest": corpus.get("corpus_digest"),
        "state_count": len(records),
        "transition_count": transition_count,
        "checked_transition_count": checked,
        "issue_count": len(issues),
        "issues": issues,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:100],
        "mismatch_digest": _digest(mismatches),
        "passed": passed,
    }


def evaluate_native_parity(
    client: NativeCompactBatchClient,
    corpus: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = _flatten_inputs(corpus)
    first = client.transition_batch(inputs)
    second = client.transition_batch(inputs)
    mismatches = []
    cursor = 0
    for record in corpus["records"]:
        for expected in record["expected"]:
            output = first.records[cursor]
            actual_digest = _digest(_native_summary(output))
            if actual_digest != expected["result_digest"]:
                mismatches.append(
                    {
                        "seed": record["seed"],
                        "turn": record["turn"],
                        "action": expected["action"],
                        "expected_digest": expected["result_digest"],
                        "native_digest": actual_digest,
                    }
                )
            cursor += 1
    action_inputs = tuple(
        NativeCompactTransitionInput(
            _state_from_hex(record["state"]),
            _pair_from_names(record["pair"]),
            int(record["legal_actions"][0]),
        )
        for record in corpus["records"]
    )
    action_results = client.transition_batch(action_inputs, include_actions=True)
    action_mismatch_count = 0
    for stored, output in zip(corpus["records"], action_results.records, strict=True):
        if output.legal_action_indices != tuple(stored["legal_actions"]):
            action_mismatch_count += 1
        if output.symmetry_reduced_action_indices != tuple(
            stored["symmetry_reduced_actions"]
        ):
            action_mismatch_count += 1
    return {
        "transition_count": len(inputs),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:100],
        "mismatch_digest": _digest(mismatches),
        "action_mismatch_count": action_mismatch_count,
        "deterministic_response": first.response_sha256 == second.response_sha256,
        "response_sha256": first.response_sha256,
        "response_bytes": first.response_bytes,
        "passed": (
            not mismatches
            and action_mismatch_count == 0
            and first.response_sha256 == second.response_sha256
        ),
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile from no values")
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(percentile / 100.0 * len(ordered)) - 1)
    return ordered[index]


def _latency_summary(
    wall_ns: Sequence[int],
    kernel_ns: Sequence[int],
    *,
    records_per_call: int,
) -> dict[str, Any]:
    per_record_kernel = [value / records_per_call for value in kernel_ns]
    p50_wall = _percentile(wall_ns, 50)
    p95_wall = _percentile(wall_ns, 95)
    p50_kernel = _percentile(per_record_kernel, 50)
    p95_kernel = _percentile(per_record_kernel, 95)
    return {
        "samples": len(wall_ns),
        "records_per_call": records_per_call,
        "wall_p50_us": p50_wall / 1_000.0,
        "wall_p95_us": p95_wall / 1_000.0,
        "wall_p50_transitions_per_second": records_per_call * 1e9 / p50_wall,
        "wall_p95_transitions_per_second": records_per_call * 1e9 / p95_wall,
        "kernel_per_transition_p50_ns": p50_kernel,
        "kernel_per_transition_p95_ns": p95_kernel,
        "kernel_p50_transitions_per_second": 1e9 / p50_kernel,
        "kernel_p95_transitions_per_second": 1e9 / p95_kernel,
    }


def _measure_batch_calls(
    client: NativeCompactBatchClient,
    inputs: Sequence[NativeCompactTransitionInput],
    *,
    samples: int,
    warmup: int,
) -> tuple[dict[str, Any], list[str]]:
    if not inputs or samples <= 0 or warmup < 0:
        raise ValueError("invalid native transition benchmark dimensions")
    for _ in range(warmup):
        client.transition_batch(inputs, measure_timing=True)
    wall_values = []
    kernel_values = []
    digests = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        result = client.transition_batch(inputs, measure_timing=True)
        wall_values.append(time.perf_counter_ns() - started)
        if result.timing is None:
            raise AssertionError("timed native batch omitted timing counters")
        kernel_values.append(result.timing.kernel_ns)
        digests.append(result.response_sha256)
    return (
        _latency_summary(
            wall_values,
            kernel_values,
            records_per_call=len(inputs),
        ),
        digests,
    )


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def _installed_wheel_sha256() -> str | None:
    wheels = sorted(Path("dist/native").glob("puyo_deep_chain_native-*.whl"))
    return file_sha256(wheels[-1]) if wheels else None


def run_microbenchmark(
    client: NativeCompactBatchClient,
    inputs: Sequence[NativeCompactTransitionInput],
    *,
    single_samples: int,
    batch_samples: int,
    batch_size: int,
) -> dict[str, Any]:
    selected_batch = tuple(inputs[: min(int(batch_size), len(inputs))])
    if not selected_batch:
        raise ValueError("native transition benchmark corpus is empty")
    representative = (selected_batch[0],)
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    cold_started = time.perf_counter_ns()
    cold = client.transition_batch(representative, measure_timing=True)
    cold_wall_ns = time.perf_counter_ns() - cold_started
    if cold.timing is None:
        raise AssertionError("cold native batch omitted timing counters")
    single, single_digests = _measure_batch_calls(
        client,
        representative,
        samples=single_samples,
        warmup=20,
    )
    batch, batch_digests = _measure_batch_calls(
        client,
        selected_batch,
        samples=batch_samples,
        warmup=2,
    )
    classified = client.transition_batch(selected_batch)
    category_inputs: dict[str, list[NativeCompactTransitionInput]] = {
        "quiet": [],
        "one_chain": [],
        "multi_chain": [],
        "invalid": [],
    }
    for transition_input, output in zip(
        selected_batch,
        classified.records,
        strict=True,
    ):
        if not output.valid:
            category = "invalid"
        elif output.chain_count == 0:
            category = "quiet"
        elif output.chain_count == 1:
            category = "one_chain"
        else:
            category = "multi_chain"
        category_inputs[category].append(transition_input)
    profile_samples = max(3, min(batch_samples, 8))
    categories = {}
    for category, category_records in category_inputs.items():
        if not category_records:
            categories[category] = {
                "record_count": 0,
                "record_fraction": 0.0,
                "measurement": None,
            }
            continue
        measurement, _ = _measure_batch_calls(
            client,
            tuple(category_records),
            samples=profile_samples,
            warmup=1,
        )
        categories[category] = {
            "record_count": len(category_records),
            "record_fraction": len(category_records) / len(selected_batch),
            "measurement": measurement,
        }
    usage_after = resource.getrusage(resource.RUSAGE_SELF)
    projected_decision_p95_ms = (
        batch["kernel_per_transition_p95_ns"]
        * CANONICAL_MAX_EXPANDED_NODES
        / 1_000_000.0
    )
    return {
        "cold_single": {
            "wall_us": cold_wall_ns / 1_000.0,
            "kernel_ns": cold.timing.kernel_ns,
            "response_bytes": cold.response_bytes,
        },
        "warm_single": single,
        "warm_batch": batch,
        "timed_response_digest_count": len(set(single_digests + batch_digests)),
        "memory": {
            "peak_rss_kib_before": int(usage_before.ru_maxrss),
            "peak_rss_kib_after": int(usage_after.ru_maxrss),
            "peak_rss_delta_kib": max(
                0,
                int(usage_after.ru_maxrss - usage_before.ru_maxrss),
            ),
        },
        "allocation": {
            "normal_hot_transition_heap_allocations": 0,
            "evidence": "compact::tests::normal_hot_transition_performs_no_heap_allocation",
            "batch_boundary_allocations_included_in_wall_latency": True,
        },
        "internal_profile": {
            "schema_version": "puyo.native_compact_transition_internal_profile.v1",
            "kernel_timing_excludes_binary_parse_and_result_encoding": True,
            "selected_record_count": len(selected_batch),
            "samples_per_category": profile_samples,
            "categories": categories,
            "cause": (
                "The allocation-free scalar transition itself exceeds the "
                "17.66 ns/node allocation on quiet records; chain resolution "
                "adds gravity and repeated component scans. Python/FFI result "
                "materialization is outside the reported kernel timer."
            ),
            "required_follow_up": (
                "Return the native boundary and representation to ADR review; "
                "do not start PUYO-201 or mask the miss with fallback timing."
            ),
        },
        "budget": {
            "canonical_max_expanded_nodes": CANONICAL_MAX_EXPANDED_NODES,
            "decision_p95_budget_ms": TRANSITION_DECISION_P95_BUDGET_MS,
            "per_expanded_node_budget_ns": (
                TRANSITION_DECISION_P95_BUDGET_MS
                * 1_000_000.0
                / CANONICAL_MAX_EXPANDED_NODES
            ),
            "projected_decision_p95_ms": projected_decision_p95_ms,
            "passed": projected_decision_p95_ms <= TRANSITION_DECISION_P95_BUDGET_MS,
        },
    }


def _render_report(summary: Mapping[str, Any]) -> str:
    performance = summary["performance"]
    parity = summary["parity"]
    budget = performance["budget"]
    categories = performance["internal_profile"]["categories"]
    return "\n".join(
        [
            "# PUYO-200 native compact transition",
            "",
            f"- result: **{'PASS' if summary['passed'] else 'FAIL'}**",
            f"- fixed fixtures: {parity['fixed_fixture_count']} / {parity['fixed_fixture_mismatch_count']} mismatches",
            f"- frozen state-actions: {parity['transition_count']} / {parity['mismatch_count']} native mismatches",
            f"- authoritative/Python corpus mismatches: {parity['oracle_mismatch_count']}",
            f"- deterministic serialized response: **{'PASS' if parity['deterministic_response'] else 'FAIL'}**",
            "",
            "## Performance",
            "",
            "| Scope | p50 wall (us) | p95 wall (us) | p50 kernel ns/transition | p95 kernel ns/transition |",
            "| --- | ---: | ---: | ---: | ---: |",
            (
                f"| single | {performance['warm_single']['wall_p50_us']:.3f} | "
                f"{performance['warm_single']['wall_p95_us']:.3f} | "
                f"{performance['warm_single']['kernel_per_transition_p50_ns']:.3f} | "
                f"{performance['warm_single']['kernel_per_transition_p95_ns']:.3f} |"
            ),
            (
                f"| batch | {performance['warm_batch']['wall_p50_us']:.3f} | "
                f"{performance['warm_batch']['wall_p95_us']:.3f} | "
                f"{performance['warm_batch']['kernel_per_transition_p50_ns']:.3f} | "
                f"{performance['warm_batch']['kernel_per_transition_p95_ns']:.3f} |"
            ),
            "",
            (
                f"Projected transition decision p95 is "
                f"{budget['projected_decision_p95_ms']:.3f} ms for "
                f"{budget['canonical_max_expanded_nodes']:,} nodes against the "
                f"{budget['decision_p95_budget_ms']:.3f} ms allocation: "
                f"**{'PASS' if budget['passed'] else 'FAIL'}**."
            ),
            "",
            "The normal Rust transition path recorded zero heap allocations; binary batch parsing/result materialization is QA-boundary overhead and is included in wall latency.",
            "",
            "## Internal native profile",
            "",
            "| Outcome | Records | Fraction | p50 kernel ns | p95 kernel ns |",
            "| --- | ---: | ---: | ---: | ---: |",
            *(
                (
                    f"| {name} | {profile['record_count']} | "
                    f"{profile['record_fraction']:.3%} | "
                    f"{profile['measurement']['kernel_per_transition_p50_ns']:.3f} | "
                    f"{profile['measurement']['kernel_per_transition_p95_ns']:.3f} |"
                )
                if profile["measurement"] is not None
                else f"| {name} | 0 | 0.000% | - | - |"
                for name, profile in categories.items()
            ),
            "",
            performance["internal_profile"]["cause"],
            "",
            f"Stop condition: {performance['internal_profile']['required_follow_up']}",
            "",
            "## Contract",
            "",
            f"- state: `{COMPACT_SEARCH_SCHEMA_VERSION}`",
            f"- batch: `{NATIVE_COMPACT_TRANSITION_SCHEMA_VERSION}`",
            f"- kernel path: `{NATIVE_COMPACT_KERNEL_PATH}`",
            f"- Ama reference: `{AMA_REFERENCE_COMMIT}` (MIT); poppable-mask identity adapted with notice retained",
            "",
        ]
    )


def _fixture_parity(client: NativeCompactBatchClient) -> dict[str, Any]:
    fixture = _read_json(DEFAULT_FIXTURE_PATH)
    inputs = []
    expected = []
    char_to_color = {
        ".": PuyoColor.EMPTY,
        "R": PuyoColor.RED,
        "B": PuyoColor.BLUE,
        "G": PuyoColor.GREEN,
        "Y": PuyoColor.YELLOW,
        "P": PuyoColor.PURPLE,
        "O": PuyoColor.OJAMA,
    }
    for case in fixture["cases"]:
        game = GameState(seed=0)
        game.spawn_puyo()
        for y, row in enumerate(case["board"]):
            for x, char in enumerate(row):
                color = char_to_color[char]
                if color != PuyoColor.EMPTY:
                    game.field.place_puyo(x, y, Puyo(color))
        pair = _pair_from_names(case["pair"])
        game.current_puyo_1 = Puyo(pair[0])
        game.current_puyo_2 = Puyo(pair[1])
        game.all_clear_bonus_pending = bool(case["all_clear_bonus_pending"])
        simulator = HeadlessPuyoSimulator(game_state=game)
        state = CompactSearchState.from_simulator(simulator)
        inputs.append(NativeCompactTransitionInput(state, pair, int(case["action"])))
        expected.append(_python_summary(transition(state, pair, int(case["action"]))))
    result = client.transition_batch(inputs, capture_trace=True, include_actions=True)
    mismatches = [
        fixture["cases"][index]["id"]
        for index, (output, expected_payload) in enumerate(
            zip(result.records, expected, strict=True)
        )
        if _native_summary(output) != expected_payload
    ]
    return {
        "case_count": len(inputs),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    corpus = _read_json(args.corpus)
    oracle = verify_frozen_corpus(args.corpus)
    client = NativeCompactBatchClient()
    inputs = _flatten_inputs(corpus)
    performance = run_microbenchmark(
        client,
        inputs,
        single_samples=args.single_samples,
        batch_samples=args.batch_samples,
        batch_size=args.batch_size,
    )
    native = evaluate_native_parity(client, corpus)
    fixtures = _fixture_parity(client)
    capabilities = client.capabilities.to_dict()
    parity = {
        "fixed_fixture_count": fixtures["case_count"],
        "fixed_fixture_mismatch_count": fixtures["mismatch_count"],
        "fixed_fixture_mismatches": fixtures["mismatches"],
        "state_count": oracle["state_count"],
        "transition_count": native["transition_count"],
        "oracle_mismatch_count": oracle["mismatch_count"],
        "mismatch_count": native["mismatch_count"],
        "action_mismatch_count": native["action_mismatch_count"],
        "mismatch_digest": native["mismatch_digest"],
        "deterministic_response": native["deterministic_response"],
        "response_sha256": native["response_sha256"],
        "response_bytes": native["response_bytes"],
    }
    checks = {
        "fixed_fixture_parity": fixtures["mismatch_count"] == 0,
        "minimum_10k_state_action_parity": (
            native["transition_count"] >= MINIMUM_TRANSITIONS
            and oracle["mismatch_count"] == 0
            and native["mismatch_count"] == 0
        ),
        "legal_action_parity": native["action_mismatch_count"] == 0,
        "deterministic_serialization": native["deterministic_response"],
        "allocation_free_hot_transition": (
            performance["allocation"]["normal_hot_transition_heap_allocations"] == 0
        ),
        "scalar_kernel_path": capabilities["simd_path"] == NATIVE_COMPACT_KERNEL_PATH,
        "transition_performance_budget": performance["budget"]["passed"],
    }
    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": utc_timestamp(),
        "evaluated_commit": git_commit(),
        "command": (
            "python -m eval.deep_chain_native_transition_benchmark run "
            f"--corpus {args.corpus} --output-dir {args.output_dir} "
            f"--single-samples {args.single_samples} "
            f"--batch-samples {args.batch_samples} --batch-size {args.batch_size}"
        ),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu": _cpu_model(),
            "wheel_sha256": _installed_wheel_sha256(),
        },
        "contracts": {
            "compact_state": COMPACT_SEARCH_SCHEMA_VERSION,
            "native_batch": NATIVE_COMPACT_TRANSITION_SCHEMA_VERSION,
            "kernel_path": NATIVE_COMPACT_KERNEL_PATH,
            "normal_hot_path_python_objects": False,
            "normal_hot_path_heap_allocations": 0,
        },
        "capabilities": capabilities,
        "corpus": {
            "path": str(args.corpus),
            "sha256": file_sha256(args.corpus),
            "digest": corpus["corpus_digest"],
        },
        "parity": parity,
        "performance": performance,
        "checks": checks,
        "references": {
            "ama_commit": AMA_REFERENCE_COMMIT,
            "ama_license": "MIT",
            "copied_code": False,
            "adapted_algorithm": "FieldBit::get_mask_pop connectivity identity",
            "license_notice": "native/deep_chain_native/LICENSE-AMA-MIT",
        },
        "passed": all(checks.values()),
    }
    summary["summary_digest"] = _digest(
        {
            key: value
            for key, value in summary.items()
            if key not in {"created_at_utc", "environment", "performance"}
        }
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "corpus_verification.json", oracle)
    _write_json(output_dir / "microbenchmark.json", performance)
    _write_json(output_dir / "internal_profile.json", performance["internal_profile"])
    _write_json(output_dir / "benchmark_summary.json", summary)
    (output_dir / "benchmark_report.md").write_text(
        _render_report(summary),
        encoding="utf-8",
    )
    artifacts = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "ticket": TICKET,
        "created_at_utc": summary["created_at_utc"],
        "evaluated_commit": summary["evaluated_commit"],
        "passed": summary["passed"],
        "command": summary["command"],
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifacts
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    return summary


def verify_benchmark(artifact_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    root = Path(artifact_dir)
    manifest = _read_json(root / "benchmark_manifest.json")
    issues = []
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected benchmark manifest schema")
    for artifact in manifest.get("artifacts", []):
        path = root / artifact["path"]
        if not path.exists():
            issues.append(f"missing artifact: {artifact['path']}")
        elif file_sha256(path) != artifact["sha256"]:
            issues.append(f"artifact digest mismatch: {artifact['path']}")
    summary = _read_json(root / "benchmark_summary.json")
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected benchmark summary schema")
    if not summary.get("passed"):
        issues.append("native compact transition checks did not pass")
    if summary.get("parity", {}).get("transition_count", 0) < MINIMUM_TRANSITIONS:
        issues.append("benchmark does not cover 10,000 transitions")
    return {"passed": not issues, "issues": issues}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate-corpus")
    generate.add_argument("--output", default=str(DEFAULT_CORPUS_PATH))
    generate.add_argument("--seed-start", type=int, default=DEFAULT_SEED_START)
    generate.add_argument("--seed-count", type=int, default=DEFAULT_SEED_COUNT)
    generate.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)

    run = subparsers.add_parser("run")
    run.add_argument("--corpus", default=str(DEFAULT_CORPUS_PATH))
    run.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    run.add_argument("--single-samples", type=int, default=200)
    run.add_argument("--batch-samples", type=int, default=12)
    run.add_argument("--batch-size", type=int, default=10_000)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--artifact-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "generate-corpus":
        payload = generate_frozen_corpus(
            output_path=args.output,
            seed_start=args.seed_start,
            seed_count=args.seed_count,
            max_turns=args.max_turns,
        )
        print(
            json.dumps(
                {
                    "corpus_digest": payload["corpus_digest"],
                    "state_count": payload["state_count"],
                    "transition_count": payload["transition_count"],
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "run":
        summary = run_benchmark(args)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["passed"] else 1
    result = verify_benchmark(args.artifact_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
