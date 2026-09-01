"""QA adapter for the native chain-structure evaluator prototype.

The native search path calls the Rust evaluator directly.  This module owns a
bounded binary batch boundary used by differential tests and benchmark
evidence.  Detailed Python dataclasses are materialized only when explicitly
requested; no search node uses this adapter or crosses FFI.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import agents.chain_structure as chain_structure_module
from agents.chain_structure import (
    ActionStructureFeatures,
    ChainStructureAction,
    ChainStructureConfig,
    ChainStructureFeatures,
    ChainStructureResult,
    ChainStructureScoreBreakdown,
    QuiescenceCandidate,
    QuiescenceSummary,
    connection_candidates,
    extract_components,
)
from agents.compact_search import CompactSearchState
from agents.deep_chain_native import InvalidNativeInputError
from src.core.constants import PuyoColor

NATIVE_CHAIN_STRUCTURE_BATCH_SCHEMA_VERSION = "puyo.native_chain_structure_batch.v1"
NATIVE_CHAIN_STRUCTURE_HOT_SCHEMA_VERSION = "puyo.native_chain_structure_hot.v1"
NATIVE_CHAIN_STRUCTURE_PROFILE_SCHEMA_VERSION = (
    "puyo.native_chain_structure_combined_profile.v1"
)
NATIVE_CHAIN_STRUCTURE_STAGE_PROFILE_SCHEMA_VERSION = (
    "puyo.native_chain_structure_stage_profile.v2"
)
NATIVE_CHAIN_STRUCTURE_ABI_VERSION = 1
NATIVE_CHAIN_STRUCTURE_MAX_EVIDENCE_CANDIDATES = 96

_REQUEST_MAGIC = b"NCSB"
_SUCCESS_MAGIC = b"NCSS"
_PROFILE_MAGIC = b"NCSP"
_STAGE_PROFILE_MAGIC = b"NCST"
_REQUEST_HEADER_BYTES = 240
_REQUEST_RECORD_BYTES = 184
_FLAG_EVIDENCE = 0x1
_MAX_RECORDS = 50_000
_CANDIDATE_BYTES = 61
_FIXED_RESULT_BYTES = 265
_STAGE_PROFILE_HEADER_BYTES = 224
_STAGE_PROFILE_RECORD_BYTES = 24

NATIVE_CHAIN_STRUCTURE_PROFILE_STAGE_NAMES = (
    "driver_unattributed",
    "transition",
    "base_feature_component_extraction",
    "placement_enumeration_trigger_qualification",
    "virtual_resolve_gravity",
    "remaining_structure_scan",
    "candidate_ranking_sha256",
)

_STRUCTURE_COLORS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
    PuyoColor.PURPLE,
)
_COLOR_TO_ID = {color: index + 1 for index, color in enumerate(_STRUCTURE_COLORS)}
_WEIGHT_NAMES = (
    "potential_chain_count",
    "potential_chain_score",
    "required_key_count",
    "trigger_height",
    "trigger_protection",
    "remaining_link_2",
    "remaining_link_3",
    "connectivity_edge",
    "connection_candidate",
    "reachable_ignition",
    "growth_site",
    "foundation_cell",
    "fold_space",
    "adjacent_roughness",
    "height_spread",
    "well_depth",
    "bump_height",
    "danger_ratio",
    "nuisance_puyo",
    "hidden_row_puyo",
    "tear",
    "waste",
    "trigger_damage",
    "premature_fire",
)
_BREAKDOWN_NAMES = (
    "quiescence_chain",
    "key_cost",
    "trigger_position",
    "remaining_links",
    "component_connectivity",
    "connection_potential",
    "shape",
    "danger",
    "nuisance",
    "tear",
    "waste",
    "trigger_damage",
    "premature_fire",
    "fatal",
    "total",
)
_STATUS_NAMES = {
    1: "available",
    2: "not_found",
    3: "budget_exhausted",
}
_TRUNCATION_NAMES = {
    0: None,
    1: "pattern_nodes",
    2: "resolution_nodes",
}


@dataclass(frozen=True, slots=True)
class NativeChainStructureInput:
    state: CompactSearchState
    parent_state: CompactSearchState | None = None
    action: ChainStructureAction | None = None
    target_chain_count: int = 6
    pair: tuple[PuyoColor, PuyoColor] = (PuyoColor.RED, PuyoColor.BLUE)
    action_id: int = 0

    def __post_init__(self) -> None:
        if (self.parent_state is None) != (self.action is None):
            raise InvalidNativeInputError(
                "native evaluator parent state and action must be supplied together"
            )
        if not 1 <= int(self.target_chain_count) <= 0xFF:
            raise InvalidNativeInputError("target_chain_count is outside the u8 range")
        if len(self.pair) != 2 or any(color not in _COLOR_TO_ID for color in self.pair):
            raise InvalidNativeInputError("profile pair must contain two normal colors")
        if not 0 <= int(self.action_id) < 22:
            raise InvalidNativeInputError("profile action ID is outside the v1 layout")


@dataclass(frozen=True, slots=True)
class NativeQuiescenceCandidate:
    chain_count: int
    chain_score: int
    required_key_count: int
    trigger_color: PuyoColor
    placements_mask: int
    anchor_mask: int
    trigger_column: int
    trigger_height: int
    trigger_protection: float
    remaining_link_2: int
    remaining_link_3: int
    remaining_connection_edges: int
    extension_space: int
    fixed_tie_break: int


@dataclass(frozen=True, slots=True)
class NativeChainStructureRecord:
    evaluation_status: str
    truncation_reason: str | None
    pattern_nodes: int
    resolution_nodes: int
    score: float
    features: ChainStructureFeatures
    action_features: ActionStructureFeatures
    score_breakdown: ChainStructureScoreBreakdown
    best: NativeQuiescenceCandidate | None
    candidates: tuple[NativeQuiescenceCandidate, ...]


@dataclass(frozen=True, slots=True)
class NativeChainStructureBatchResult:
    records: tuple[NativeChainStructureRecord, ...]
    response_bytes: bytes


@dataclass(frozen=True, slots=True)
class NativeCombinedProfileResult:
    operations: int
    record_count: int
    elapsed_ns: int
    checksum: int
    evaluator_abi_version: int

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed_ns / 1_000_000.0

    @property
    def ns_per_operation(self) -> float:
        return self.elapsed_ns / float(self.operations)


@dataclass(frozen=True, slots=True)
class NativeStageProfileCounts:
    pattern_nodes: int
    executed_pattern_probes: int
    resolution_nodes: int
    rank_comparison_calls: int
    rank_tie_calls: int
    sha256_calls: int

    def to_dict(self) -> dict[str, int]:
        return {
            "pattern_nodes": int(self.pattern_nodes),
            "executed_pattern_probes": int(self.executed_pattern_probes),
            "resolution_nodes": int(self.resolution_nodes),
            "rank_comparison_calls": int(self.rank_comparison_calls),
            "rank_tie_calls": int(self.rank_tie_calls),
            "sha256_calls": int(self.sha256_calls),
        }


@dataclass(frozen=True, slots=True)
class NativeChainStructureStageProfileResult:
    operations: int
    record_count: int
    elapsed_ns: int
    cycles: int
    checksum: int
    sample_interval_us: int
    sample_count: int
    mismatch_count: int
    evaluator_abi_version: int
    cycle_counter_available: bool
    sampler_available: bool
    aggregate_counts: NativeStageProfileCounts
    stage_sample_counts: tuple[int, ...]
    stage_entry_counts: tuple[int, ...]
    record_counts: tuple[NativeStageProfileCounts, ...]

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed_ns / 1_000_000.0

    @property
    def ns_per_operation(self) -> float:
        return self.elapsed_ns / float(self.operations)

    @property
    def stage_samples(self) -> dict[str, int]:
        return dict(
            zip(
                NATIVE_CHAIN_STRUCTURE_PROFILE_STAGE_NAMES,
                self.stage_sample_counts,
                strict=True,
            )
        )

    @property
    def stage_entries(self) -> dict[str, int]:
        return dict(
            zip(
                NATIVE_CHAIN_STRUCTURE_PROFILE_STAGE_NAMES,
                self.stage_entry_counts,
                strict=True,
            )
        )


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self.payload = memoryview(payload)
        self.offset = 0

    def take(self, length: int, name: str) -> bytes:
        end = self.offset + int(length)
        if length < 0 or end < self.offset or end > len(self.payload):
            raise InvalidNativeInputError(f"truncated native evaluator {name}")
        value = bytes(self.payload[self.offset : end])
        self.offset = end
        return value

    def u8(self, name: str) -> int:
        return self.take(1, name)[0]

    def i8(self, name: str) -> int:
        return struct.unpack("<b", self.take(1, name))[0]

    def u16(self, name: str) -> int:
        return struct.unpack("<H", self.take(2, name))[0]

    def u32(self, name: str) -> int:
        return struct.unpack("<I", self.take(4, name))[0]

    def u64(self, name: str) -> int:
        return struct.unpack("<Q", self.take(8, name))[0]

    def u128(self, name: str) -> int:
        return int.from_bytes(self.take(16, name), "little")

    def f64(self, name: str) -> float:
        value = struct.unpack("<d", self.take(8, name))[0]
        if not math.isfinite(value):
            raise InvalidNativeInputError(f"native evaluator {name} is not finite")
        return value

    def finish(self) -> None:
        if self.offset != len(self.payload):
            raise InvalidNativeInputError("native evaluator record has trailing data")


def _config_version_key(config: ChainStructureConfig) -> int:
    encoded = json.dumps(
        config.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "little")


def _encode_record(record: NativeChainStructureInput) -> bytes:
    payload = bytearray(record.state.to_bytes())
    if record.parent_state is None:
        payload.append(0)
        payload.extend(b"\x00" * 87)
    else:
        payload.append(1)
        payload.extend(record.parent_state.to_bytes())
    action = record.action
    if action is None:
        payload.extend((0, int(record.target_chain_count), 0))
        payload.extend(struct.pack("<H", 0))
        payload.append(0)
    else:
        payload.extend(
            (
                1,
                int(record.target_chain_count),
                int(action.chain_count),
            )
        )
        payload.extend(struct.pack("<H", int(action.vanished_count)))
        payload.append(int(bool(action.game_over)))
    payload.extend(
        (
            _COLOR_TO_ID[record.pair[0]],
            _COLOR_TO_ID[record.pair[1]],
            int(record.action_id),
        )
    )
    if len(payload) != _REQUEST_RECORD_BYTES:
        raise AssertionError("native evaluator record layout drifted")
    return bytes(payload)


def encode_native_chain_structure_batch(
    records: Sequence[NativeChainStructureInput],
    config: ChainStructureConfig,
    *,
    include_evidence: bool = True,
) -> bytes:
    selected = tuple(records)
    if not selected or len(selected) > _MAX_RECORDS:
        raise InvalidNativeInputError("native evaluator batch has an invalid size")
    if (
        config.budget.max_resolution_nodes
        > NATIVE_CHAIN_STRUCTURE_MAX_EVIDENCE_CANDIDATES
    ):
        raise InvalidNativeInputError(
            "native evaluator resolution budget exceeds the v1 evidence bound"
        )
    flags = _FLAG_EVIDENCE if include_evidence else 0
    payload = bytearray(_REQUEST_MAGIC)
    payload.extend(
        struct.pack("<HHIHH", 1, flags, len(selected), _REQUEST_RECORD_BYTES, 0)
    )
    payload.extend(
        struct.pack(
            "<BBHIIIQd",
            config.budget.max_added_puyos,
            config.budget.max_candidates,
            0,
            config.budget.max_pattern_nodes,
            config.budget.max_resolution_nodes,
            0,
            _config_version_key(config),
            config.fatal_score,
        )
    )
    for name in _WEIGHT_NAMES:
        payload.extend(struct.pack("<d", float(getattr(config.weights, name))))
    if len(payload) != _REQUEST_HEADER_BYTES:
        raise AssertionError("native evaluator header layout drifted")
    for record in selected:
        payload.extend(_encode_record(record))
    return bytes(payload)


def _decode_candidate(reader: _Reader) -> NativeQuiescenceCandidate:
    chain_count = reader.u8("candidate chain count")
    required_key_count = reader.u8("candidate key count")
    color_id = reader.u8("candidate color")
    trigger_column = reader.u8("candidate trigger column")
    trigger_height = reader.u8("candidate trigger height")
    remaining_link_2 = reader.u8("candidate link-2")
    remaining_link_3 = reader.u8("candidate link-3")
    remaining_connection_edges = reader.u8("candidate edges")
    extension_space = reader.u8("candidate extension space")
    try:
        trigger_color = _STRUCTURE_COLORS[color_id]
    except IndexError as exc:
        raise InvalidNativeInputError(
            "native evaluator returned an invalid color"
        ) from exc
    chain_score = reader.u32("candidate score")
    trigger_protection = reader.f64("candidate protection")
    placements_mask = reader.u128("candidate placements")
    anchor_mask = reader.u128("candidate anchors")
    fixed_tie_break = reader.u64("candidate fixed tie-break")
    return NativeQuiescenceCandidate(
        chain_count=chain_count,
        chain_score=chain_score,
        required_key_count=required_key_count,
        trigger_color=trigger_color,
        placements_mask=placements_mask,
        anchor_mask=anchor_mask,
        trigger_column=trigger_column,
        trigger_height=trigger_height,
        trigger_protection=trigger_protection,
        remaining_link_2=remaining_link_2,
        remaining_link_3=remaining_link_3,
        remaining_connection_edges=remaining_connection_edges,
        extension_space=extension_space,
        fixed_tie_break=fixed_tie_break,
    )


def _decode_features(reader: _Reader) -> ChainStructureFeatures:
    heights = tuple(reader.u8("column height") for _ in range(6))
    normal_puyo_count = reader.u8("normal puyo count")
    component_count = reader.u8("component count")
    isolated_count = reader.u8("isolated count")
    link_2 = reader.u8("link-2 count")
    link_3 = reader.u8("link-3 count")
    connectivity_edges = reader.u8("connectivity edges")
    connection_candidate_count = reader.u8("connection candidates")
    reachable_ignition_count = reader.u8("reachable ignitions")
    growth_site_count = reader.u8("growth sites")
    foundation_cell_count = reader.u8("foundation cells")
    fold_space = reader.u16("fold space")
    adjacent_roughness = reader.u8("roughness")
    height_spread = reader.u8("height spread")
    well_depth = reader.u8("well depth")
    bump_height = reader.u8("bump height")
    danger_ratio = reader.f64("danger ratio")
    nuisance_count = reader.u8("nuisance count")
    hidden_row_count = reader.u8("hidden row count")
    flags = reader.u8("feature flags")
    trigger_protection = reader.f64("trigger protection")
    potential_chain_count = reader.u8("potential chain count")
    potential_chain_score = reader.u32("potential chain score")
    required_key_count = reader.i8("required key count")
    trigger_column = reader.i8("trigger column")
    trigger_height = reader.i8("trigger height")
    return ChainStructureFeatures(
        canonical_column_heights=heights,
        normal_puyo_count=normal_puyo_count,
        component_count=component_count,
        isolated_count=isolated_count,
        link_2=link_2,
        link_3=link_3,
        connectivity_edges=connectivity_edges,
        connection_candidate_count=connection_candidate_count,
        reachable_ignition_count=reachable_ignition_count,
        growth_site_count=growth_site_count,
        foundation_cell_count=foundation_cell_count,
        fold_space=fold_space,
        adjacent_roughness=adjacent_roughness,
        height_spread=height_spread,
        well_depth=well_depth,
        bump_height=bump_height,
        danger_ratio=danger_ratio,
        nuisance_count=nuisance_count,
        hidden_row_count=hidden_row_count,
        trigger_reachable=bool(flags & 0x1),
        trigger_protection=trigger_protection,
        potential_chain_count=potential_chain_count,
        potential_chain_score=potential_chain_score,
        required_key_count=(None if required_key_count < 0 else required_key_count),
        trigger_column=(None if trigger_column < 0 else trigger_column),
        trigger_height=(None if trigger_height < 0 else trigger_height),
        remaining_link_2=reader.u8("remaining link-2"),
        remaining_link_3=reader.u8("remaining link-3"),
        remaining_connection_edges=reader.u8("remaining edges"),
        death=bool(flags & 0x2),
        unreachable_trigger=bool(flags & 0x4),
        structural_dead_end=bool(flags & 0x8),
    )


def _decode_action(reader: _Reader) -> ActionStructureFeatures:
    flags = reader.u8("action flags")
    return ActionStructureFeatures(
        evaluated=bool(flags & 0x1),
        tear_count=reader.u8("tear count"),
        waste_count=reader.u8("waste count"),
        trigger_damage=reader.u8("trigger damage"),
        premature_fire=bool(flags & 0x2),
        danger_delta=reader.f64("danger delta"),
        death=bool(flags & 0x4),
    )


def _decode_record(payload: bytes) -> NativeChainStructureRecord:
    reader = _Reader(payload)
    raw_status = reader.u8("evaluation status")
    raw_truncation = reader.u8("truncation reason")
    has_best = reader.u8("best candidate flag")
    candidate_count = reader.u8("candidate count")
    if raw_status not in _STATUS_NAMES or raw_truncation not in _TRUNCATION_NAMES:
        raise InvalidNativeInputError("native evaluator returned an invalid status")
    if (
        has_best not in (0, 1)
        or candidate_count > NATIVE_CHAIN_STRUCTURE_MAX_EVIDENCE_CANDIDATES
    ):
        raise InvalidNativeInputError(
            "native evaluator returned invalid candidate controls"
        )
    pattern_nodes = reader.u32("pattern nodes")
    resolution_nodes = reader.u32("resolution nodes")
    score = reader.f64("total score")
    features = _decode_features(reader)
    action_features = _decode_action(reader)
    breakdown_values = [reader.f64(name) for name in _BREAKDOWN_NAMES]
    score_breakdown = ChainStructureScoreBreakdown(
        **dict(zip(_BREAKDOWN_NAMES, breakdown_values, strict=True))
    )
    raw_best = _decode_candidate(reader)
    candidates = tuple(_decode_candidate(reader) for _ in range(candidate_count))
    reader.finish()
    return NativeChainStructureRecord(
        evaluation_status=_STATUS_NAMES[raw_status],
        truncation_reason=_TRUNCATION_NAMES[raw_truncation],
        pattern_nodes=pattern_nodes,
        resolution_nodes=resolution_nodes,
        score=score,
        features=features,
        action_features=action_features,
        score_breakdown=score_breakdown,
        best=(raw_best if has_best else None),
        candidates=candidates,
    )


def decode_native_chain_structure_batch_response(
    payload: bytes | bytearray | memoryview,
) -> NativeChainStructureBatchResult:
    encoded = bytes(payload)
    if len(encoded) < 16 or encoded[:4] != _SUCCESS_MAGIC:
        raise InvalidNativeInputError("invalid native evaluator response framing")
    abi, flags, record_count, reserved = struct.unpack_from("<HHII", encoded, 4)
    if abi != NATIVE_CHAIN_STRUCTURE_ABI_VERSION or flags & ~_FLAG_EVIDENCE or reserved:
        raise InvalidNativeInputError("invalid native evaluator response controls")
    records = []
    offset = 16
    for _ in range(record_count):
        if offset + 4 > len(encoded):
            raise InvalidNativeInputError("truncated native evaluator record framing")
        length = struct.unpack_from("<I", encoded, offset)[0]
        offset += 4
        end = offset + length
        if end > len(encoded):
            raise InvalidNativeInputError("truncated native evaluator record")
        if (
            length < _FIXED_RESULT_BYTES
            or (length - _FIXED_RESULT_BYTES) % _CANDIDATE_BYTES
        ):
            raise InvalidNativeInputError("native evaluator record length is invalid")
        records.append(_decode_record(encoded[offset:end]))
        offset = end
    if offset != len(encoded):
        raise InvalidNativeInputError("native evaluator response has trailing data")
    return NativeChainStructureBatchResult(tuple(records), encoded)


def decode_native_combined_profile(
    payload: bytes | bytearray | memoryview,
) -> NativeCombinedProfileResult:
    encoded = bytes(payload)
    if len(encoded) != 40 or encoded[:4] != _PROFILE_MAGIC:
        raise InvalidNativeInputError("invalid native combined-profile framing")
    (
        abi,
        flags,
        operations,
        record_count,
        elapsed_ns,
        checksum,
        evaluator_abi,
        reserved,
        reserved_2,
    ) = struct.unpack_from("<HHIIQQHHI", encoded, 4)
    if (
        abi != NATIVE_CHAIN_STRUCTURE_ABI_VERSION
        or flags
        or evaluator_abi != NATIVE_CHAIN_STRUCTURE_ABI_VERSION
        or reserved
        or reserved_2
        or not operations
        or not record_count
    ):
        raise InvalidNativeInputError("invalid native combined-profile controls")
    return NativeCombinedProfileResult(
        operations=operations,
        record_count=record_count,
        elapsed_ns=elapsed_ns,
        checksum=checksum,
        evaluator_abi_version=evaluator_abi,
    )


def decode_native_stage_profile(
    payload: bytes | bytearray | memoryview,
) -> NativeChainStructureStageProfileResult:
    encoded = bytes(payload)
    if len(encoded) < _STAGE_PROFILE_HEADER_BYTES:
        raise InvalidNativeInputError("truncated native stage-profile response")
    reader = _Reader(encoded)
    if reader.take(4, "stage-profile magic") != _STAGE_PROFILE_MAGIC:
        raise InvalidNativeInputError("invalid native stage-profile framing")
    abi = reader.u16("stage-profile ABI")
    flags = reader.u16("stage-profile flags")
    operations = reader.u32("stage-profile operations")
    record_count = reader.u32("stage-profile record count")
    elapsed_ns = reader.u64("stage-profile elapsed time")
    cycles = reader.u64("stage-profile cycles")
    checksum = reader.u64("stage-profile checksum")
    sample_interval_us = reader.u32("stage-profile sample interval")
    stage_count = reader.u32("stage-profile stage count")
    sample_count = reader.u64("stage-profile sample count")
    mismatch_count = reader.u32("stage-profile mismatch count")
    evaluator_abi = reader.u16("stage-profile evaluator ABI")
    record_bytes = reader.u16("stage-profile record bytes")
    if (
        abi != NATIVE_CHAIN_STRUCTURE_ABI_VERSION
        or flags & ~0x3
        or not operations
        or not record_count
        or not elapsed_ns
        or stage_count != len(NATIVE_CHAIN_STRUCTURE_PROFILE_STAGE_NAMES)
        or evaluator_abi != NATIVE_CHAIN_STRUCTURE_ABI_VERSION
        or record_bytes != _STAGE_PROFILE_RECORD_BYTES
    ):
        raise InvalidNativeInputError("invalid native stage-profile controls")
    aggregate_values = tuple(
        reader.u64(f"stage-profile aggregate counter {index}") for index in range(6)
    )
    stage_samples = tuple(
        reader.u64(f"stage-profile sample counter {index}")
        for index in range(stage_count)
    )
    stage_entries = tuple(
        reader.u64(f"stage-profile entry counter {index}")
        for index in range(stage_count)
    )
    expected_length = _STAGE_PROFILE_HEADER_BYTES + record_count * record_bytes
    if (
        not 10 <= sample_interval_us <= 10_000
        or not sample_count
        or sample_count != sum(stage_samples)
        or len(encoded) != expected_length
        or bool(flags & 0x1) != bool(cycles)
        or not flags & 0x2
    ):
        raise InvalidNativeInputError("invalid native stage-profile controls")

    def counts(values: Sequence[int]) -> NativeStageProfileCounts:
        return NativeStageProfileCounts(
            pattern_nodes=int(values[0]),
            executed_pattern_probes=int(values[1]),
            resolution_nodes=int(values[2]),
            rank_comparison_calls=int(values[3]),
            rank_tie_calls=int(values[4]),
            sha256_calls=int(values[5]),
        )

    per_record = tuple(
        counts(
            tuple(
                reader.u32(
                    f"stage-profile record {record_index} counter {counter_index}"
                )
                for counter_index in range(6)
            )
        )
        for record_index in range(record_count)
    )
    reader.finish()
    return NativeChainStructureStageProfileResult(
        operations=operations,
        record_count=record_count,
        elapsed_ns=elapsed_ns,
        cycles=cycles,
        checksum=checksum,
        sample_interval_us=sample_interval_us,
        sample_count=sample_count,
        mismatch_count=mismatch_count,
        evaluator_abi_version=evaluator_abi,
        cycle_counter_available=bool(flags & 0x1),
        sampler_available=bool(flags & 0x2),
        aggregate_counts=counts(aggregate_values),
        stage_sample_counts=stage_samples,
        stage_entry_counts=stage_entries,
        record_counts=per_record,
    )


def _cells_from_internal_mask(mask: int) -> tuple[tuple[int, int], ...]:
    cells = []
    remaining = int(mask)
    while remaining:
        bit_index = (remaining & -remaining).bit_length() - 1
        remaining &= remaining - 1
        x, y = divmod(bit_index, 16)
        if not 0 <= x < 6 or not 0 <= y < 14:
            raise InvalidNativeInputError(
                "native evaluator returned an out-of-board mask"
            )
        cells.append((x, y))
    return tuple(cells)


def _materialize_candidate(
    value: NativeQuiescenceCandidate,
    *,
    state: CompactSearchState,
    component_by_cell: dict[tuple[int, int], Any],
) -> QuiescenceCandidate:
    placements = _cells_from_internal_mask(value.placements_mask)
    anchors = _cells_from_internal_mask(value.anchor_mask)
    planes = list(state.planes)
    plane_index = _STRUCTURE_COLORS.index(value.trigger_color)
    for x, y in placements:
        planes[plane_index] |= 1 << (y * 6 + x)
    resolved = chain_structure_module._resolve_virtual(
        tuple(planes),
        component_by_cell=component_by_cell,
    )
    if resolved.chain_count != value.chain_count or resolved.score != value.chain_score:
        raise InvalidNativeInputError(
            "native scalar candidate disagrees with detailed evidence materialization"
        )
    return QuiescenceCandidate(
        chain_count=value.chain_count,
        chain_score=value.chain_score,
        required_key_count=value.required_key_count,
        trigger_color=value.trigger_color,
        placements=placements,
        anchor_cells=anchors,
        trigger_column=value.trigger_column,
        trigger_height=value.trigger_height,
        trigger_protection=value.trigger_protection,
        remaining_link_2=value.remaining_link_2,
        remaining_link_3=value.remaining_link_3,
        remaining_connection_edges=value.remaining_connection_edges,
        extension_space=value.extension_space,
        relations=resolved.relations,
    )


def materialize_native_chain_structure_result(
    record: NativeChainStructureRecord,
    *,
    state: CompactSearchState,
    config: ChainStructureConfig,
) -> ChainStructureResult:
    components = extract_components(state)
    connections = connection_candidates(state, components)
    component_by_cell = {
        cell: component for component in components for cell in component.cells
    }
    candidates = tuple(
        _materialize_candidate(
            candidate,
            state=state,
            component_by_cell=component_by_cell,
        )
        for candidate in record.candidates
    )
    ranked = tuple(
        sorted(
            candidates,
            key=chain_structure_module._candidate_rank_key,
            reverse=True,
        )
    )
    retained = ranked[: config.budget.max_candidates]
    best = retained[0] if retained else None
    if (record.best is None) != (best is None):
        raise InvalidNativeInputError(
            "native scalar and evidence candidate availability differ"
        )
    if record.best is not None and best is not None:
        native_best = _materialize_candidate(
            record.best,
            state=state,
            component_by_cell=component_by_cell,
        )
        if native_best.canonical_signature != best.canonical_signature:
            raise InvalidNativeInputError(
                "native fixed-width tie-break selected a different best candidate"
            )
    quiescence = QuiescenceSummary(
        best=best,
        candidates=retained,
        pattern_nodes=record.pattern_nodes,
        resolution_nodes=record.resolution_nodes,
        search_complete=record.truncation_reason is None,
        truncation_reason=record.truncation_reason,
    )
    digest = chain_structure_module._evaluation_digest(
        state,
        features=record.features,
        quiescence=quiescence,
        action_features=record.action_features,
        weight_version=config.weight_version,
    )
    return ChainStructureResult(
        evaluation_status=record.evaluation_status,
        evaluated=True,
        score=record.score,
        features=record.features,
        components=components,
        connection_candidates=connections,
        quiescence=quiescence,
        action_features=record.action_features,
        score_breakdown=record.score_breakdown,
        tie_break_digest=digest,
        weight_version=config.weight_version,
        truncation_reason=record.truncation_reason,
    )


class NativeChainStructureBatchClient:
    """Strict QA client; never install this object as a per-node evaluator."""

    def __init__(self, module: ModuleType | None = None) -> None:
        selected = module or importlib.import_module("_puyo_deep_chain_native")
        expected = {
            "CHAIN_STRUCTURE_EVALUATOR_ABI_VERSION": NATIVE_CHAIN_STRUCTURE_ABI_VERSION,
            "CHAIN_STRUCTURE_EVALUATOR_SCHEMA": NATIVE_CHAIN_STRUCTURE_HOT_SCHEMA_VERSION,
            "CHAIN_STRUCTURE_BATCH_SCHEMA": NATIVE_CHAIN_STRUCTURE_BATCH_SCHEMA_VERSION,
            "CHAIN_STRUCTURE_COMBINED_PROFILE_SCHEMA": NATIVE_CHAIN_STRUCTURE_PROFILE_SCHEMA_VERSION,
            "CHAIN_STRUCTURE_STAGE_PROFILE_SCHEMA": NATIVE_CHAIN_STRUCTURE_STAGE_PROFILE_SCHEMA_VERSION,
        }
        for name, value in expected.items():
            if getattr(selected, name, None) != value:
                raise InvalidNativeInputError(f"native capability mismatch: {name}")
        self.module = selected

    def evaluate_batch(
        self,
        records: Sequence[NativeChainStructureInput],
        config: ChainStructureConfig,
        *,
        include_evidence: bool = True,
    ) -> NativeChainStructureBatchResult:
        request = encode_native_chain_structure_batch(
            records,
            config,
            include_evidence=include_evidence,
        )
        return decode_native_chain_structure_batch_response(
            self.module._chain_structure_evaluate_batch(request)
        )

    def combined_profile(
        self,
        records: Sequence[NativeChainStructureInput],
        config: ChainStructureConfig,
        *,
        operations: int,
    ) -> NativeCombinedProfileResult:
        if not 1 <= int(operations) <= 0xFFFFFFFF:
            raise InvalidNativeInputError(
                "profile operations are outside the u32 range"
            )
        request = encode_native_chain_structure_batch(
            records,
            config,
            include_evidence=False,
        )
        return decode_native_combined_profile(
            self.module._chain_structure_combined_profile(request, int(operations))
        )

    def stage_profile(
        self,
        records: Sequence[NativeChainStructureInput],
        config: ChainStructureConfig,
        *,
        operations: int,
        sample_interval_us: int = 100,
    ) -> NativeChainStructureStageProfileResult:
        if not 1 <= int(operations) <= 0xFFFFFFFF:
            raise InvalidNativeInputError(
                "stage-profile operations are outside the u32 range"
            )
        if not 10 <= int(sample_interval_us) <= 10_000:
            raise InvalidNativeInputError(
                "stage-profile sample interval is outside the supported range"
            )
        request = encode_native_chain_structure_batch(
            records,
            config,
            include_evidence=False,
        )
        return decode_native_stage_profile(
            self.module._chain_structure_stage_profile(
                request,
                int(operations),
                int(sample_interval_us),
            )
        )


__all__ = [
    "NATIVE_CHAIN_STRUCTURE_ABI_VERSION",
    "NATIVE_CHAIN_STRUCTURE_BATCH_SCHEMA_VERSION",
    "NATIVE_CHAIN_STRUCTURE_HOT_SCHEMA_VERSION",
    "NATIVE_CHAIN_STRUCTURE_PROFILE_SCHEMA_VERSION",
    "NATIVE_CHAIN_STRUCTURE_PROFILE_STAGE_NAMES",
    "NATIVE_CHAIN_STRUCTURE_STAGE_PROFILE_SCHEMA_VERSION",
    "NativeChainStructureBatchClient",
    "NativeChainStructureBatchResult",
    "NativeChainStructureInput",
    "NativeChainStructureRecord",
    "NativeChainStructureStageProfileResult",
    "NativeCombinedProfileResult",
    "NativeQuiescenceCandidate",
    "NativeStageProfileCounts",
    "decode_native_chain_structure_batch_response",
    "decode_native_combined_profile",
    "decode_native_stage_profile",
    "encode_native_chain_structure_batch",
    "materialize_native_chain_structure_result",
]
