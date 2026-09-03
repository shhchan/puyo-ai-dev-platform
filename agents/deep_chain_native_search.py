"""Materialize one native long-horizon decision into Python oracle types.

The native result keeps hot-loop data in bounded binary records. This adapter
performs the one allowed post-search materialization step and verifies that the
native ranking, scenario completion, and representative records agree with the
versioned Python semantics.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping

from agents.compact_search import legal_action_indices
from agents.deep_chain_native import (
    InvalidNativeInputError,
    NativeDecisionRequest,
    NativeDecisionResult,
    _decode_state,
)
from agents.deep_chain_native_evaluator import (
    NativeChainStructureRecord,
    decode_native_chain_structure_record,
    materialize_native_chain_structure_result,
)
from agents.long_horizon_search import (
    ALLOWED_FIRE_CLASSES,
    FIRE_CLASS_FORCED_SAFETY,
    FIRE_CLASS_PREMATURE,
    FIRE_CLASS_QUIET,
    FIRE_CLASS_TARGET,
    FIRE_CLASS_UNAVAILABLE,
    FIRE_CLASS_WINNING,
    TERMINAL_FIRE_CONTINUE,
    TERMINAL_FIRE_RECORD_AND_STOP,
    ChainFireEvidence,
    LongHorizonNode,
    LongHorizonSearchCounters,
    LongHorizonSearchResult,
    ScenarioRootEvidence,
    _root_build_diagnostics,
    aggregate_expected_chain_evidence,
    build_scenario_sequences_from_known_pairs,
    compact_state_fingerprint,
)
from puyo_env.actions import NUM_ACTIONS
from src.core.constants import PuyoColor

NATIVE_LONG_HORIZON_RECORD_SCHEMA_VERSION = "puyo.native_long_horizon_records.v1"

_FIRE_CLASSES = (
    FIRE_CLASS_UNAVAILABLE,
    FIRE_CLASS_PREMATURE,
    FIRE_CLASS_QUIET,
    FIRE_CLASS_FORCED_SAFETY,
    FIRE_CLASS_TARGET,
    FIRE_CLASS_WINNING,
)
_TRUNCATION_REASONS = {
    0: None,
    1: "expanded_node_budget",
    2: "not_evaluated",
}
_SHORTFALL_REASONS = {
    0: None,
    1: "expanded_node_budget",
    2: "beam_width_below_root_quota",
    3: "terminal_fire_without_quiet_survivor",
    4: "game_over_without_survivor",
    5: "invalid_transition_without_survivor",
    6: "no_non_terminal_survivor",
}
_COLOR_IDS = {
    1: PuyoColor.RED,
    2: PuyoColor.BLUE,
    3: PuyoColor.GREEN,
    4: PuyoColor.YELLOW,
    5: PuyoColor.PURPLE,
}


class _Reader:
    def __init__(self, payload: bytes, name: str) -> None:
        self.payload = memoryview(payload)
        self.offset = 0
        self.name = name

    @property
    def remaining(self) -> int:
        return len(self.payload) - self.offset

    def take(self, length: int, field: str) -> bytes:
        end = self.offset + int(length)
        if length < 0 or end < self.offset or end > len(self.payload):
            raise InvalidNativeInputError(f"truncated native {self.name} {field}")
        value = bytes(self.payload[self.offset : end])
        self.offset = end
        return value

    def u8(self, field: str) -> int:
        return self.take(1, field)[0]

    def u16(self, field: str) -> int:
        return struct.unpack("<H", self.take(2, field))[0]

    def u32(self, field: str) -> int:
        return struct.unpack("<I", self.take(4, field))[0]

    def u64(self, field: str) -> int:
        return struct.unpack("<Q", self.take(8, field))[0]

    def f64(self, field: str) -> float:
        value = struct.unpack("<d", self.take(8, field))[0]
        if not math.isfinite(value):
            raise InvalidNativeInputError(f"native {self.name} {field} is not finite")
        return value

    def finish(self) -> None:
        if self.remaining:
            raise InvalidNativeInputError(f"native {self.name} contains trailing data")


def _fire_evaluation_details(record: NativeChainStructureRecord) -> dict[str, object]:
    features = record.features
    action = record.action_features
    return {
        "evaluation_status": record.evaluation_status,
        "danger": float(features.danger_ratio),
        "trigger_damage": max(0, int(action.trigger_damage)),
        "trigger_preserved": max(0, int(action.trigger_damage)) == 0,
        "structural_potential": {
            "chain_count": int(features.potential_chain_count),
            "chain_score": int(features.potential_chain_score),
            "required_key_count": features.required_key_count,
        },
        "score_breakdown": record.score_breakdown.to_dict(),
    }


def _decode_fire(
    reader: _Reader,
    *,
    root_action: int,
    request: NativeDecisionRequest,
) -> ChainFireEvidence | None:
    present = reader.u8("fire presence")
    if present == 0:
        return None
    if present != 1:
        raise InvalidNativeInputError("native fire presence flag is invalid")
    scenario_id = reader.u8("fire scenario")
    raw_class = reader.u8("fire class")
    flags = reader.u8("fire flags")
    chain_count = reader.u8("fire chain count")
    chain_score = reader.u64("fire chain score")
    depth = reader.u16("fire depth")
    trigger_action = reader.u8("fire trigger action")
    reserved = reader.u8("fire reserved")
    target_chain_count = reader.u16("fire target chain count")
    target_chain_gap = reader.u16("fire target chain gap")
    terminal_score = reader.f64("fire terminal score")
    breakdown_values = tuple(
        reader.f64(f"fire terminal breakdown {index}") for index in range(5)
    )
    state = _decode_state(reader.take(87, "fire compact state"))
    path_count = reader.u16("fire path count")
    path = tuple(reader.u8("fire path action") for _ in range(path_count))
    evaluation_bytes = reader.u32("fire evaluation bytes")
    evaluation = decode_native_chain_structure_record(
        reader.take(evaluation_bytes, "fire evaluation")
    )
    if (
        raw_class >= len(_FIRE_CLASSES)
        or flags & ~0x3
        or reserved
        or not depth
        or depth != path_count
        or not path
        or path[0] != root_action
        or path[-1] != trigger_action
        or any(action >= NUM_ACTIONS for action in path)
        or target_chain_count != request.search_config.minimum_chain_count
        or target_chain_gap != max(0, target_chain_count - chain_count)
    ):
        raise InvalidNativeInputError("native fire record is inconsistent")
    fire_class = _FIRE_CLASSES[raw_class]
    terminal = bool(flags & 0x1)
    allowed = bool(flags & 0x2)
    if allowed != (fire_class in ALLOWED_FIRE_CLASSES):
        raise InvalidNativeInputError("native fire allowed flag is inconsistent")
    if terminal != (
        request.search_config.terminal_fire_rule == TERMINAL_FIRE_RECORD_AND_STOP
        and chain_count >= request.search_config.terminal_fire_chain_count
    ):
        raise InvalidNativeInputError("native fire terminal flag is inconsistent")
    breakdown = dict(
        zip(
            (
                "structural_score",
                "official_score",
                "target_chain_gap",
                "target_gap_penalty",
                "total",
            ),
            breakdown_values,
            strict=True,
        )
    )
    if not math.isclose(terminal_score, breakdown["total"], abs_tol=1e-9):
        raise InvalidNativeInputError("native fire score breakdown is inconsistent")
    return ChainFireEvidence(
        root_action=root_action,
        scenario_id=scenario_id,
        chain_count=chain_count,
        chain_score=chain_score,
        depth=depth,
        trigger_action=trigger_action,
        state_fingerprint=compact_state_fingerprint(state),
        path=path,
        terminal=terminal,
        terminal_reason=(
            f"chain_count_gte_{request.search_config.terminal_fire_chain_count}"
            if terminal
            else None
        ),
        fire_class=fire_class,
        target_chain_count=target_chain_count,
        target_chain_gap=target_chain_gap,
        allowed=allowed,
        terminal_score=terminal_score,
        terminal_score_breakdown=breakdown,
        terminal_evaluation=_fire_evaluation_details(evaluation),
    )


def _decode_tracker(
    reader: _Reader,
    request: NativeDecisionRequest,
) -> ScenarioRootEvidence:
    root_action = reader.u8("tracker root action")
    scenario_id = reader.u8("tracker scenario")
    flags = reader.u8("tracker flags")
    raw_selected_class = reader.u8("tracker selected class")
    raw_terminal_rule = reader.u8("tracker terminal rule")
    raw_truncation = reader.u8("tracker truncation")
    observed_mask = reader.u8("tracker observed classes")
    reserved = reader.u8("tracker reserved")
    reached_depth = reader.u16("tracker reached depth")
    terminal_fire_chain_count = reader.u16("tracker terminal fire count threshold")
    survivor_quota = reader.u16("tracker survivor quota")
    max_chain_count = reader.u16("tracker maximum chain count")
    max_chain_score = reader.u64("tracker maximum chain score")
    fire_count = reader.u32("tracker fire count")
    terminal_fire_count = reader.u32("tracker terminal fire count")
    raw_survivor_score = reader.f64("tracker survivor score")
    expanded_nodes = reader.u64("tracker expanded nodes")
    pruned_nodes = reader.u64("tracker pruned nodes")
    transposition_hits = reader.u64("tracker transposition hits")
    invalid_nodes = reader.u64("tracker invalid nodes")
    game_over_nodes = reader.u64("tracker game-over nodes")
    coverage_count = reader.u16("tracker coverage count")
    reserved_2 = reader.u16("tracker coverage reserved")
    best_fire = _decode_fire(reader, root_action=root_action, request=request)
    selected_fire = _decode_fire(reader, root_action=root_action, request=request)
    candidate_counts = []
    retained_counts = []
    shortfalls = []
    for _ in range(coverage_count):
        depth = reader.u16("coverage depth")
        candidate_count = reader.u32("coverage candidates")
        retained_count = reader.u32("coverage retained")
        raw_shortfall = reader.u8("coverage shortfall")
        coverage_reserved = reader.take(3, "coverage reserved")
        if raw_shortfall not in _SHORTFALL_REASONS or any(coverage_reserved):
            raise InvalidNativeInputError("native survivor coverage is invalid")
        candidate_counts.append((depth, candidate_count))
        retained_counts.append((depth, retained_count))
        reason = _SHORTFALL_REASONS[raw_shortfall]
        if reason is not None:
            shortfalls.append((depth, reason))
    if (
        root_action >= NUM_ACTIONS
        or raw_selected_class >= len(_FIRE_CLASSES)
        or flags & ~0x7
        or raw_terminal_rule not in (0, 1)
        or raw_truncation not in _TRUNCATION_REASONS
        or observed_mask & ~0x3F
        or reserved
        or reserved_2
        or reached_depth > request.search_config.depth
        or terminal_fire_chain_count != request.search_config.terminal_fire_chain_count
        or survivor_quota != request.search_config.root_survivor_quota
        or raw_terminal_rule
        != int(
            request.search_config.terminal_fire_rule == TERMINAL_FIRE_RECORD_AND_STOP
        )
        or (best_fire is not None and best_fire.scenario_id != scenario_id)
        or (selected_fire is not None and selected_fire.scenario_id != scenario_id)
    ):
        raise InvalidNativeInputError("native scenario tracker is inconsistent")
    survivor_score = raw_survivor_score if flags & 0x4 else None
    if survivor_score is None and raw_survivor_score != 0.0:
        raise InvalidNativeInputError("native absent survivor score is non-zero")
    selected_class = _FIRE_CLASSES[raw_selected_class]
    if selected_fire is not None and selected_fire.fire_class != selected_class:
        raise InvalidNativeInputError("native selected fire class is inconsistent")
    observed_classes = tuple(
        sorted(
            _FIRE_CLASSES[index]
            for index in range(len(_FIRE_CLASSES))
            if observed_mask & (1 << index)
        )
    )
    if FIRE_CLASS_WINNING in observed_classes:
        expected_selected_class = FIRE_CLASS_WINNING
    elif FIRE_CLASS_TARGET in observed_classes:
        expected_selected_class = FIRE_CLASS_TARGET
    elif FIRE_CLASS_FORCED_SAFETY in observed_classes:
        expected_selected_class = FIRE_CLASS_FORCED_SAFETY
    elif flags & 0x4:
        expected_selected_class = FIRE_CLASS_QUIET
    elif FIRE_CLASS_PREMATURE in observed_classes:
        expected_selected_class = FIRE_CLASS_PREMATURE
    else:
        expected_selected_class = FIRE_CLASS_UNAVAILABLE
    if (
        selected_class != expected_selected_class
        or terminal_fire_count > fire_count
        or (fire_count == 0) != (best_fire is None)
        or (best_fire is not None and best_fire.fire_class not in observed_classes)
    ):
        raise InvalidNativeInputError("native fire tracker summary is inconsistent")
    return ScenarioRootEvidence(
        root_action=root_action,
        scenario_id=scenario_id,
        evaluated=bool(flags & 0x1),
        search_complete=bool(flags & 0x2),
        reached_depth=reached_depth,
        max_chain_count=max_chain_count,
        max_chain_score=max_chain_score,
        best_fire=best_fire,
        fire_count=fire_count,
        terminal_fire_count=terminal_fire_count,
        survivor_evaluator_score=survivor_score,
        expanded_nodes=expanded_nodes,
        pruned_nodes=pruned_nodes,
        transposition_hits=transposition_hits,
        truncation_reason=_TRUNCATION_REASONS[raw_truncation],
        terminal_fire_rule=(
            TERMINAL_FIRE_RECORD_AND_STOP
            if raw_terminal_rule
            else TERMINAL_FIRE_CONTINUE
        ),
        terminal_fire_chain_count=terminal_fire_chain_count,
        selected_fire_class=selected_class,
        selected_fire=selected_fire,
        observed_fire_classes=observed_classes,
        quiet_survivor=bool(flags & 0x4),
        survivor_quota=survivor_quota,
        survivor_candidate_counts=tuple(candidate_counts),
        survivor_counts=tuple(retained_counts),
        survivor_shortfalls=tuple(shortfalls),
        invalid_nodes=invalid_nodes,
        game_over_nodes=game_over_nodes,
    )


def _decode_trackers(
    payload: bytes,
    request: NativeDecisionRequest,
) -> dict[tuple[int, int], ScenarioRootEvidence]:
    reader = _Reader(payload, "root evidence")
    result = {}
    while reader.remaining:
        tracker = _decode_tracker(reader, request)
        key = (tracker.root_action, tracker.scenario_id)
        if key in result:
            raise InvalidNativeInputError("native root evidence contains a duplicate")
        result[key] = tracker
    return result


def _decode_representatives(
    payload: bytes,
    request: NativeDecisionRequest,
) -> dict[int, LongHorizonNode]:
    reader = _Reader(payload, "representatives")
    result = {}
    while reader.remaining:
        root_action = reader.u8("representative root action")
        scenario_id = reader.u8("representative scenario")
        last_action = reader.u8("representative last action")
        reserved = reader.u8("representative reserved")
        pair_cursor = reader.u16("representative pair cursor")
        path_count = reader.u16("representative path count")
        evaluator_score = reader.f64("representative evaluator score")
        cumulative_action_score = reader.u64("representative cumulative score")
        state = _decode_state(reader.take(87, "representative compact state"))
        path = tuple(reader.u8("representative path action") for _ in range(path_count))
        evaluation_bytes = reader.u32("representative evaluation bytes")
        native_evaluation = decode_native_chain_structure_record(
            reader.take(evaluation_bytes, "representative evaluation")
        )
        evaluation = materialize_native_chain_structure_result(
            native_evaluation,
            state=state,
            config=request.evaluator_config,
        )
        if (
            root_action >= NUM_ACTIONS
            or scenario_id >= 6
            or last_action >= NUM_ACTIONS
            or reserved
            or not path
            or path_count != pair_cursor
            or path[0] != root_action
            or path[-1] != last_action
            or any(action >= NUM_ACTIONS for action in path)
            or not math.isclose(evaluator_score, float(evaluation.score), abs_tol=1e-9)
            or root_action in result
        ):
            raise InvalidNativeInputError("native representative is inconsistent")
        result[root_action] = LongHorizonNode(
            state=state,
            root_action=root_action,
            scenario_id=scenario_id,
            pair_cursor=pair_cursor,
            path=path,
            evaluator_score=evaluator_score,
            evaluator_result=evaluation,
            cumulative_action_score=cumulative_action_score,
            last_action=last_action,
        )
    return result


def _decode_diagnostics(
    payload: bytes,
    request: NativeDecisionRequest,
):
    reader = _Reader(payload, "diagnostics")
    scenario_count = reader.u16("scenario count")
    depth = reader.u16("scenario depth")
    known_count = reader.u16("known pair count")
    reserved = reader.u16("scenario reserved")
    expected = build_scenario_sequences_from_known_pairs(
        request.known_pairs,
        scenarios=request.search_config.scenarios,
        depth=request.search_config.depth,
        decision_seed=request.search_config.resolved_decision_seed,
        sampling_mode=request.search_config.future_sampling_mode,
    )
    if (
        scenario_count != len(expected)
        or depth != request.search_config.depth
        or known_count != min(len(request.known_pairs), depth)
        or reserved
    ):
        raise InvalidNativeInputError("native scenario diagnostics are inconsistent")
    for sample_index, sequence in enumerate(expected):
        native_scenario_id = reader.u8("scenario ID")
        native_sample_index = reader.u8("sample index")
        rollout_present = reader.u8("rollout seed presence")
        scenario_reserved = reader.u8("scenario reserved")
        native_rollout_seed = reader.u64("rollout seed")
        native_pairs = tuple(
            (
                _COLOR_IDS.get(reader.u8("scenario axis color")),
                _COLOR_IDS.get(reader.u8("scenario child color")),
            )
            for _ in range(depth)
        )
        if (
            native_scenario_id != sequence.scenario_id
            or native_sample_index != sample_index
            or rollout_present != int(sequence.rollout_seed is not None)
            or scenario_reserved
            or native_rollout_seed != (sequence.rollout_seed or 0)
            or native_pairs != sequence.pairs
        ):
            raise InvalidNativeInputError(
                "native future sequence differs from the Python authority"
            )
    root_evaluation_bytes = reader.u32("root evaluation bytes")
    native_root_evaluation = decode_native_chain_structure_record(
        reader.take(root_evaluation_bytes, "root evaluation")
    )
    reader.finish()
    root_evaluation = materialize_native_chain_structure_result(
        native_root_evaluation,
        state=request.state,
        config=request.evaluator_config,
    )
    return expected, root_evaluation


def materialize_native_long_horizon_result(
    result: NativeDecisionResult,
    request: NativeDecisionRequest,
) -> LongHorizonSearchResult:
    """Validate and materialize a native decision as `LongHorizonSearchResult`."""

    if result.request_id != request.request_id:
        raise InvalidNativeInputError("native decision request ID does not match")
    scenario_sequences, root_evaluation = _decode_diagnostics(
        result.diagnostics,
        request,
    )
    trackers = _decode_trackers(result.root_evidence, request)
    representatives = _decode_representatives(result.representatives, request)
    root_actions = tuple(sorted(result.ranked_root_actions))
    if root_actions != legal_action_indices(request.state):
        raise InvalidNativeInputError("native ranked roots differ from legal actions")
    expected_scenario_ids = tuple(
        sorted(sequence.scenario_id for sequence in scenario_sequences)
    )
    scenario_order = tuple(sequence.scenario_id for sequence in scenario_sequences)
    expected_keys = {
        (root_action, scenario_id)
        for root_action in root_actions
        for scenario_id in expected_scenario_ids
    }
    if set(trackers) != expected_keys:
        raise InvalidNativeInputError("native tracker matrix is incomplete")
    expected_record_counts = {
        "root_evidence": len(expected_keys),
        "representatives": len(representatives),
        "diagnostics": 1 + len(scenario_sequences),
    }
    if dict(result.record_counts) != expected_record_counts:
        raise InvalidNativeInputError("native result record counts are inconsistent")
    evidence = tuple(
        aggregate_expected_chain_evidence(
            root_action,
            tuple(
                trackers[(root_action, scenario_id)]
                for scenario_id in expected_scenario_ids
            ),
            requested_scenarios=request.search_config.scenarios,
        )
        for root_action in root_actions
    )
    evidence_by_action = {item.root_action: item for item in evidence}
    for root_action, representative in representatives.items():
        if (
            root_action not in evidence_by_action
            or representative.scenario_id not in expected_scenario_ids
            or trackers[(root_action, representative.scenario_id)].selected_fire_class
            != evidence_by_action[root_action].fire_class
        ):
            raise InvalidNativeInputError(
                "native representative does not belong to selected evidence"
            )
    root_diagnostics: dict[int, Mapping[str, object]] = {}
    for root_action in root_actions:
        root_diagnostics[root_action] = _root_build_diagnostics(
            root_evaluation=root_evaluation,
            representative=representatives.get(root_action),
            evidence=evidence_by_action[root_action],
        )
    materialized = LongHorizonSearchResult(
        root_evidence=evidence,
        representatives=representatives,
        scenario_sequences=scenario_sequences,
        root_evaluation=root_evaluation,
        counters=LongHorizonSearchCounters(
            expanded_nodes=int(result.counters["expanded_nodes"]),
            generated_nodes=int(result.counters["generated_nodes"]),
            invalid_nodes=int(result.counters["invalid_nodes"]),
            game_over_nodes=int(result.counters["game_over_nodes"]),
            evaluated_nodes=int(result.counters["evaluated_nodes"]),
            pruned_nodes=int(result.counters["pruned_nodes"]),
            transposition_hits=int(result.counters["transposition_hits"]),
            terminal_fire_nodes=int(result.counters["terminal_fire_nodes"]),
            reached_depth=int(result.counters["reached_depth"]),
            budget_exhausted=result.budget_exhausted,
        ),
        root_transposition_hits={
            root_action: sum(
                trackers[(root_action, scenario_id)].transposition_hits
                for scenario_id in expected_scenario_ids
            )
            for root_action in root_actions
        },
        root_pruned_nodes={
            root_action: sum(
                trackers[(root_action, scenario_id)].pruned_nodes
                for scenario_id in expected_scenario_ids
            )
            for root_action in root_actions
        },
        root_reached_depth={
            root_action: max(
                trackers[(root_action, scenario_id)].reached_depth
                for scenario_id in expected_scenario_ids
            )
            for root_action in root_actions
        },
        root_generated_scenarios={
            root_action: tuple(
                scenario_id
                for scenario_id in scenario_order
                if trackers[(root_action, scenario_id)].evaluated
            )
            for root_action in root_actions
        },
        root_diagnostics=root_diagnostics,
    )
    materialized_ranking = tuple(
        evidence.root_action for evidence in materialized.ranked_roots
    )
    if materialized_ranking != result.ranked_root_actions:
        raise InvalidNativeInputError("native aggregate ranking differs from Python")
    if not materialized_ranking or materialized_ranking[0] != result.selected_action:
        raise InvalidNativeInputError("native selected action differs from Python")
    search_complete = all(tracker.search_complete for tracker in trackers.values())
    if search_complete != result.search_complete:
        raise InvalidNativeInputError("native search-complete flag is inconsistent")
    tracker_values = tuple(trackers.values())
    tracker_counter_totals = {
        "expanded_nodes": sum(item.expanded_nodes for item in tracker_values),
        "pruned_nodes": sum(item.pruned_nodes for item in tracker_values),
        "transposition_hits": sum(item.transposition_hits for item in tracker_values),
        "invalid_nodes": sum(item.invalid_nodes for item in tracker_values),
        "game_over_nodes": sum(item.game_over_nodes for item in tracker_values),
        "terminal_fire_nodes": sum(item.terminal_fire_count for item in tracker_values),
        "reached_depth": max(
            (item.reached_depth for item in tracker_values), default=0
        ),
    }
    if any(
        int(result.counters[name]) != value
        for name, value in tracker_counter_totals.items()
    ):
        raise InvalidNativeInputError("native result counters disagree with trackers")
    return materialized


__all__ = [
    "NATIVE_LONG_HORIZON_RECORD_SCHEMA_VERSION",
    "materialize_native_long_horizon_result",
]
