"""Differential/debug batch adapter for the native compact transition kernel.

The production search boundary remains one decision-level ``bytes`` call in
``agents.deep_chain_native``.  This module deliberately exposes no single-node
Python transition API: it only encodes and decodes bounded QA batches used by
PUYO-200 parity evidence and microbenchmarks.  PUYO-201/202 call the Rust state
and transition functions directly inside the extension.
"""

from __future__ import annotations

import hashlib
import importlib
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from types import ModuleType
from typing import Any

from agents.compact_search import CompactSearchState
from agents.deep_chain_native import (
    COMPACT_HOT_CHILD_STATE_BYTES,
    COMPACT_HOT_RESULT_ABI_VERSION,
    COMPACT_HOT_RESULT_BYTES,
    COMPACT_HOT_RESULT_SCHEMA_VERSION,
    NATIVE_MODULE_NAME,
    DeepChainNativeError,
    IncompatibleSchemaError,
    InvalidNativeInputError,
    NativeBackendUnavailableError,
    NativeDeepChainBackend,
    NativeInternalPanicError,
    NativeResourceExhaustedError,
    _decode_state,
    _encode_state,
)
from puyo_env.actions import NUM_ACTIONS
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor

NATIVE_COMPACT_TRANSITION_SCHEMA_VERSION = "puyo.native_compact_transition_batch.v1"
NATIVE_COMPACT_TRANSITION_ABI_VERSION = 1
NATIVE_COMPACT_KERNEL_PATH = "scalar"
NATIVE_COMPACT_HOT_RESULT_SCHEMA_VERSION = COMPACT_HOT_RESULT_SCHEMA_VERSION
NATIVE_COMPACT_HOT_RESULT_ABI_VERSION = COMPACT_HOT_RESULT_ABI_VERSION
NATIVE_COMPACT_HOT_CHILD_STATE_BYTES = COMPACT_HOT_CHILD_STATE_BYTES
NATIVE_COMPACT_HOT_RESULT_BYTES = COMPACT_HOT_RESULT_BYTES

_REQUEST_MAGIC = b"PCTB"
_SUCCESS_MAGIC = b"PCTS"
_ERROR_MAGIC = b"PCTE"
_SCHEMA_MINOR = 0
_STATE_BYTES = 87
_PLANE_BYTES = 11
_PLANE_COUNT = 6
_REQUEST_RECORD_BYTES = _STATE_BYTES + 3
_SUCCESS_FIXED_RECORD_BYTES = 164
_TRACE_PLACEMENT_BYTES = _PLANE_COUNT * _PLANE_BYTES
_TRACE_CHAIN_BYTES = 108
_MAX_BATCH_RECORDS = 50_000
_MAX_TRACE_BATCH_RECORDS = 4_096
_MAX_BATCH_BYTES = 16 * 1024 * 1024
_FLAG_CAPTURE_TRACE = 0x1
_FLAG_INCLUDE_ACTIONS = 0x2
_FLAG_MEASURE_TIMING = 0x4
_KNOWN_FLAGS = _FLAG_CAPTURE_TRACE | _FLAG_INCLUDE_ACTIONS | _FLAG_MEASURE_TIMING
_MASK_NOT_REQUESTED = 0xFFFFFFFF
_BOARD_MASK = (1 << (GRID_WIDTH * GRID_HEIGHT)) - 1

_REQUEST_HEADER = struct.Struct("<4sHHHHIII")
_SUCCESS_HEADER = struct.Struct("<4sHHHHIIIQQQ")
_ERROR_HEADER = struct.Struct("<4sHHHHIII")
_RECORD_PREFIX = struct.Struct("<I")
_RESULT_SCALARS = struct.Struct("<BBBBBBBBHHIIQQQ")
_TRACE_SCALARS = struct.Struct("<BBBBHHQQ")

_COLOR_TO_ID = {
    PuyoColor.RED: 1,
    PuyoColor.BLUE: 2,
    PuyoColor.GREEN: 3,
    PuyoColor.YELLOW: 4,
    PuyoColor.PURPLE: 5,
}


class NativeCompactErrorCode(IntEnum):
    INVALID_INPUT = 1
    ARITHMETIC_OVERFLOW = 2
    INTERNAL_PANIC = 3
    RESOURCE_EXHAUSTED = 4


class NativeCompactArithmeticOverflowError(DeepChainNativeError, OverflowError):
    """A checked native score/counter operation exceeded its fixed width."""

    compact_code = NativeCompactErrorCode.ARITHMETIC_OVERFLOW

    def __init__(self, message: str, *, record_index: int | None = None) -> None:
        super().__init__(
            message,
            provenance={"record_index": record_index},
        )
        self.record_index = record_index


@dataclass(frozen=True, slots=True)
class NativeCompactTransitionInput:
    state: CompactSearchState
    pair: tuple[PuyoColor, PuyoColor]
    action_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.state, CompactSearchState):
            raise InvalidNativeInputError(
                "native compact batch state must be CompactSearchState"
            )
        pair = tuple(self.pair)
        if len(pair) != 2 or any(color not in _COLOR_TO_ID for color in pair):
            raise InvalidNativeInputError(
                "native compact batch pair must contain two normal colors"
            )
        if not 0 <= int(self.action_id) < NUM_ACTIONS:
            raise InvalidNativeInputError(
                f"native compact action ID must be in [0, {NUM_ACTIONS})"
            )
        object.__setattr__(self, "pair", pair)
        object.__setattr__(self, "action_id", int(self.action_id))


@dataclass(frozen=True, slots=True)
class NativeCompactChainStep:
    chain_index: int
    vanished_count: int
    garbage_cleared_count: int
    base: int
    bonus: int
    score: int
    all_clear_bonus_score: int
    board_planes: tuple[int, ...]
    vanished_mask: int
    garbage_mask: int


@dataclass(frozen=True, slots=True)
class NativeCompactTransitionResult:
    state: CompactSearchState
    action_id: int
    valid: bool
    axis_y: int | None
    score_delta: int
    attack_score_delta: int
    chain_count: int
    vanished_count: int
    garbage_cleared_count: int
    game_over: bool
    all_clear_achieved: bool
    all_clear_bonus_pending: bool
    all_clear_bonus_consumed: bool
    all_clear_bonus_score: int
    legal_action_indices: tuple[int, ...] | None
    symmetry_reduced_action_indices: tuple[int, ...] | None
    occupied_mask: int
    column_heights: tuple[int, ...]
    board_fingerprint: bytes
    placement_planes: tuple[int, ...] | None
    chains: tuple[NativeCompactChainStep, ...]


@dataclass(frozen=True, slots=True)
class NativeCompactBatchTiming:
    parse_ns: int
    kernel_ns: int
    encode_ns: int


@dataclass(frozen=True, slots=True)
class NativeCompactBatchResult:
    records: tuple[NativeCompactTransitionResult, ...]
    timing: NativeCompactBatchTiming | None
    response_bytes: int
    response_sha256: str


def _planes_from_wire(payload: bytes) -> tuple[int, ...]:
    if len(payload) != _PLANE_COUNT * _PLANE_BYTES:
        raise InvalidNativeInputError("invalid native compact plane payload length")
    planes = tuple(
        int.from_bytes(
            payload[index * _PLANE_BYTES : (index + 1) * _PLANE_BYTES],
            "little",
        )
        for index in range(_PLANE_COUNT)
    )
    occupied = 0
    for plane in planes:
        if plane & ~_BOARD_MASK:
            raise IncompatibleSchemaError(
                "native compact trace plane exceeds the 6x14 board"
            )
        if occupied & plane:
            raise IncompatibleSchemaError("native compact trace planes overlap")
        occupied |= plane
    return planes


def _mask_indices(mask: int, *, name: str) -> tuple[int, ...] | None:
    if mask == _MASK_NOT_REQUESTED:
        return None
    if mask & ~((1 << NUM_ACTIONS) - 1):
        raise IncompatibleSchemaError(f"native {name} mask contains unknown actions")
    return tuple(index for index in range(NUM_ACTIONS) if mask & (1 << index))


def _error_record_index(value: int) -> int | None:
    return None if value == 0xFFFFFFFF else int(value)


def _raise_error_response(payload: bytes) -> None:
    if len(payload) < _ERROR_HEADER.size:
        raise InvalidNativeInputError(
            "native compact error response is shorter than its header"
        )
    (
        magic,
        schema_major,
        schema_minor,
        raw_code,
        reserved,
        raw_record_index,
        message_bytes,
        reserved_tail,
    ) = _ERROR_HEADER.unpack_from(payload)
    if magic != _ERROR_MAGIC:
        raise InvalidNativeInputError("invalid native compact error magic")
    if (
        schema_major != NATIVE_COMPACT_TRANSITION_ABI_VERSION
        or schema_minor != _SCHEMA_MINOR
    ):
        raise IncompatibleSchemaError(
            f"unsupported native compact error schema {schema_major}.{schema_minor}"
        )
    if reserved or reserved_tail or len(payload) != _ERROR_HEADER.size + message_bytes:
        raise InvalidNativeInputError("malformed native compact error framing")
    try:
        message = payload[_ERROR_HEADER.size :].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidNativeInputError(
            "native compact error message is not UTF-8"
        ) from exc
    try:
        code = NativeCompactErrorCode(raw_code)
    except ValueError as exc:
        raise IncompatibleSchemaError(
            f"unknown native compact error code {raw_code}"
        ) from exc
    record_index = _error_record_index(raw_record_index)
    provenance = {"record_index": record_index}
    if code == NativeCompactErrorCode.INVALID_INPUT:
        raise InvalidNativeInputError(message, provenance=provenance)
    if code == NativeCompactErrorCode.ARITHMETIC_OVERFLOW:
        raise NativeCompactArithmeticOverflowError(
            message,
            record_index=record_index,
        )
    if code == NativeCompactErrorCode.RESOURCE_EXHAUSTED:
        raise NativeResourceExhaustedError(message, provenance=provenance)
    raise NativeInternalPanicError(message, provenance=provenance)


def encode_native_compact_batch(
    records: Sequence[NativeCompactTransitionInput],
    *,
    capture_trace: bool = False,
    include_actions: bool = False,
    measure_timing: bool = False,
) -> bytes:
    """Encode a bounded batch; no per-record Python native call is exposed."""

    normalized = tuple(records)
    if not normalized or len(normalized) > _MAX_BATCH_RECORDS:
        raise NativeResourceExhaustedError(
            "native compact batch record count is outside its limit"
        )
    if capture_trace and len(normalized) > _MAX_TRACE_BATCH_RECORDS:
        raise NativeResourceExhaustedError(
            "native compact trace batch exceeds its diagnostic limit"
        )
    flags = 0
    flags |= _FLAG_CAPTURE_TRACE if capture_trace else 0
    flags |= _FLAG_INCLUDE_ACTIONS if include_actions else 0
    flags |= _FLAG_MEASURE_TIMING if measure_timing else 0
    body = bytearray()
    for record in normalized:
        if not isinstance(record, NativeCompactTransitionInput):
            raise InvalidNativeInputError(
                "native compact batch contains an invalid input record"
            )
        body.extend(_encode_state(record.state))
        body.extend((_COLOR_TO_ID[record.pair[0]], _COLOR_TO_ID[record.pair[1]]))
        body.append(record.action_id)
    if len(body) > _MAX_BATCH_BYTES:
        raise NativeResourceExhaustedError(
            "native compact batch request exceeds its byte limit"
        )
    return _REQUEST_HEADER.pack(
        _REQUEST_MAGIC,
        NATIVE_COMPACT_TRANSITION_ABI_VERSION,
        _SCHEMA_MINOR,
        flags,
        _REQUEST_RECORD_BYTES,
        len(normalized),
        len(body),
        0,
    ) + bytes(body)


def _decode_trace_step(
    payload: bytes, offset: int
) -> tuple[NativeCompactChainStep, int]:
    end = offset + _TRACE_CHAIN_BYTES
    if end > len(payload):
        raise InvalidNativeInputError("truncated native compact chain trace")
    (
        chain_index,
        vanished_count,
        garbage_count,
        reserved,
        base,
        bonus,
        score,
        all_clear_bonus_score,
    ) = _TRACE_SCALARS.unpack_from(payload, offset)
    if reserved:
        raise InvalidNativeInputError("native compact trace reserved field is not zero")
    offset += _TRACE_SCALARS.size
    board_planes = _planes_from_wire(payload[offset : offset + _TRACE_PLACEMENT_BYTES])
    offset += _TRACE_PLACEMENT_BYTES
    vanished_mask = int.from_bytes(payload[offset : offset + 9], "little")
    offset += 9
    garbage_mask = int.from_bytes(payload[offset : offset + 9], "little")
    offset += 9
    if vanished_mask.bit_count() != vanished_count:
        raise IncompatibleSchemaError(
            "native compact trace vanished mask/count mismatch"
        )
    if garbage_mask.bit_count() != garbage_count:
        raise IncompatibleSchemaError(
            "native compact trace garbage mask/count mismatch"
        )
    return (
        NativeCompactChainStep(
            chain_index=chain_index,
            vanished_count=vanished_count,
            garbage_cleared_count=garbage_count,
            base=base,
            bonus=bonus,
            score=score,
            all_clear_bonus_score=all_clear_bonus_score,
            board_planes=board_planes,
            vanished_mask=vanished_mask,
            garbage_mask=garbage_mask,
        ),
        offset,
    )


def _decode_result_record(
    payload: bytes, *, batch_flags: int
) -> NativeCompactTransitionResult:
    if len(payload) < _SUCCESS_FIXED_RECORD_BYTES:
        raise InvalidNativeInputError("native compact result record is truncated")
    state = _decode_state(payload[:_STATE_BYTES])
    (
        action_id,
        valid,
        raw_axis_y,
        chain_count,
        result_flags,
        trace_count,
        placement_present,
        reserved,
        vanished_count,
        garbage_cleared_count,
        legal_mask,
        reduced_mask,
        score_delta,
        attack_score_delta,
        all_clear_bonus_score,
    ) = _RESULT_SCALARS.unpack_from(payload, _STATE_BYTES)
    if (
        action_id >= NUM_ACTIONS
        or valid not in (0, 1)
        or result_flags & ~0xF
        or placement_present not in (0, 1)
        or reserved
    ):
        raise InvalidNativeInputError("invalid native compact result control field")
    axis_y = None if raw_axis_y == 0xFF else int(raw_axis_y)
    if (bool(valid) and (axis_y is None or axis_y > 12)) or (
        not valid and axis_y is not None
    ):
        raise IncompatibleSchemaError("native compact result axis/valid mismatch")
    game_over = bool(result_flags & 0x1)
    all_clear_achieved = bool(result_flags & 0x2)
    all_clear_bonus_pending = bool(result_flags & 0x4)
    all_clear_bonus_consumed = bool(result_flags & 0x8)
    if (
        game_over != state.game_over
        or all_clear_bonus_pending != state.all_clear_bonus_pending
    ):
        raise IncompatibleSchemaError(
            "native compact result lifecycle flags disagree with state"
        )

    offset = _STATE_BYTES + _RESULT_SCALARS.size
    occupied_mask = int.from_bytes(payload[offset : offset + _PLANE_BYTES], "little")
    offset += _PLANE_BYTES
    column_heights = tuple(payload[offset : offset + GRID_WIDTH])
    offset += GRID_WIDTH
    board_fingerprint = bytes(payload[offset : offset + 16])
    offset += 16
    if occupied_mask != state.occupied_mask:
        raise IncompatibleSchemaError(
            "native compact occupied cache disagrees with canonical state"
        )
    if column_heights != state.column_heights:
        raise IncompatibleSchemaError(
            "native compact height cache disagrees with canonical state"
        )

    capture_trace = bool(batch_flags & _FLAG_CAPTURE_TRACE)
    include_actions = bool(batch_flags & _FLAG_INCLUDE_ACTIONS)
    if not capture_trace and (trace_count or placement_present):
        raise IncompatibleSchemaError(
            "native compact result materialized an unrequested trace"
        )
    if capture_trace and bool(valid) != bool(placement_present):
        raise IncompatibleSchemaError(
            "native compact trace placement presence disagrees with validity"
        )
    placement_planes = None
    if placement_present:
        placement_planes = _planes_from_wire(
            payload[offset : offset + _TRACE_PLACEMENT_BYTES]
        )
        offset += _TRACE_PLACEMENT_BYTES
    chains = []
    for _ in range(trace_count):
        step, offset = _decode_trace_step(payload, offset)
        chains.append(step)
    if offset != len(payload):
        raise InvalidNativeInputError(
            "native compact result record contains trailing data"
        )
    if capture_trace and trace_count != chain_count:
        raise IncompatibleSchemaError(
            "native compact trace count disagrees with chain count"
        )
    legal_action_indices = _mask_indices(legal_mask, name="legal-action")
    reduced_action_indices = _mask_indices(reduced_mask, name="reduced-action")
    if include_actions != (legal_action_indices is not None):
        raise IncompatibleSchemaError(
            "native compact legal-action materialization disagrees with request"
        )
    if include_actions != (reduced_action_indices is not None):
        raise IncompatibleSchemaError(
            "native compact reduced-action materialization disagrees with request"
        )
    return NativeCompactTransitionResult(
        state=state,
        action_id=action_id,
        valid=bool(valid),
        axis_y=axis_y,
        score_delta=score_delta,
        attack_score_delta=attack_score_delta,
        chain_count=chain_count,
        vanished_count=vanished_count,
        garbage_cleared_count=garbage_cleared_count,
        game_over=game_over,
        all_clear_achieved=all_clear_achieved,
        all_clear_bonus_pending=all_clear_bonus_pending,
        all_clear_bonus_consumed=all_clear_bonus_consumed,
        all_clear_bonus_score=all_clear_bonus_score,
        legal_action_indices=legal_action_indices,
        symmetry_reduced_action_indices=reduced_action_indices,
        occupied_mask=occupied_mask,
        column_heights=column_heights,
        board_fingerprint=board_fingerprint,
        placement_planes=placement_planes,
        chains=tuple(chains),
    )


def decode_native_compact_batch_response(
    value: bytes | bytearray | memoryview,
) -> NativeCompactBatchResult:
    payload = bytes(value)
    if len(payload) < 4:
        raise InvalidNativeInputError("native compact response is truncated")
    if payload[:4] == _ERROR_MAGIC:
        _raise_error_response(payload)
        raise AssertionError("native compact error decoder must raise")
    if len(payload) < _SUCCESS_HEADER.size:
        raise InvalidNativeInputError(
            "native compact success response is shorter than its header"
        )
    (
        magic,
        schema_major,
        schema_minor,
        flags,
        reserved,
        record_count,
        body_bytes,
        reserved_tail,
        parse_ns,
        kernel_ns,
        encode_ns,
    ) = _SUCCESS_HEADER.unpack_from(payload)
    if magic != _SUCCESS_MAGIC:
        raise InvalidNativeInputError("invalid native compact response magic")
    if (
        schema_major != NATIVE_COMPACT_TRANSITION_ABI_VERSION
        or schema_minor != _SCHEMA_MINOR
    ):
        raise IncompatibleSchemaError(
            f"unsupported native compact response schema {schema_major}.{schema_minor}"
        )
    if (
        flags & ~_KNOWN_FLAGS
        or reserved
        or reserved_tail
        or not 0 < record_count <= _MAX_BATCH_RECORDS
        or body_bytes != len(payload) - _SUCCESS_HEADER.size
        or len(payload) > _MAX_BATCH_BYTES + _SUCCESS_HEADER.size
    ):
        raise InvalidNativeInputError("malformed native compact success header")
    measured = bool(flags & _FLAG_MEASURE_TIMING)
    if not measured and any((parse_ns, kernel_ns, encode_ns)):
        raise IncompatibleSchemaError(
            "native compact timing fields disagree with request flag"
        )
    offset = _SUCCESS_HEADER.size
    records = []
    for _ in range(record_count):
        if offset + _RECORD_PREFIX.size > len(payload):
            raise InvalidNativeInputError("truncated native compact record prefix")
        (record_bytes,) = _RECORD_PREFIX.unpack_from(payload, offset)
        offset += _RECORD_PREFIX.size
        end = offset + record_bytes
        if end < offset or end > len(payload):
            raise InvalidNativeInputError("native compact record exceeds response")
        records.append(_decode_result_record(payload[offset:end], batch_flags=flags))
        offset = end
    if offset != len(payload):
        raise InvalidNativeInputError("native compact response contains trailing data")
    timing = (
        NativeCompactBatchTiming(parse_ns, kernel_ns, encode_ns) if measured else None
    )
    return NativeCompactBatchResult(
        records=tuple(records),
        timing=timing,
        response_bytes=len(payload),
        response_sha256=hashlib.sha256(payload).hexdigest(),
    )


class NativeCompactBatchClient:
    """Strict release-extension client for differential/debug batches only."""

    def __init__(
        self,
        module: ModuleType | Any | None = None,
        *,
        canonical: bool = True,
    ) -> None:
        if module is None:
            try:
                module = importlib.import_module(NATIVE_MODULE_NAME)
            except (ImportError, OSError) as exc:
                raise NativeBackendUnavailableError(
                    f"native module {NATIVE_MODULE_NAME!r} is unavailable: {exc}",
                    retry_safe=True,
                ) from exc
        boundary = NativeDeepChainBackend(module, canonical=canonical)
        if (
            getattr(module, "COMPACT_TRANSITION_ABI_VERSION", None)
            != NATIVE_COMPACT_TRANSITION_ABI_VERSION
            or getattr(module, "COMPACT_TRANSITION_SCHEMA", None)
            != NATIVE_COMPACT_TRANSITION_SCHEMA_VERSION
            or getattr(module, "COMPACT_KERNEL_PATH", None)
            != NATIVE_COMPACT_KERNEL_PATH
            or getattr(module, "COMPACT_HOT_RESULT_ABI_VERSION", None)
            != NATIVE_COMPACT_HOT_RESULT_ABI_VERSION
            or getattr(module, "COMPACT_HOT_RESULT_SCHEMA", None)
            != NATIVE_COMPACT_HOT_RESULT_SCHEMA_VERSION
            or getattr(module, "COMPACT_HOT_CHILD_STATE_BYTES", None)
            != NATIVE_COMPACT_HOT_CHILD_STATE_BYTES
            or getattr(module, "COMPACT_HOT_RESULT_BYTES", None)
            != NATIVE_COMPACT_HOT_RESULT_BYTES
            or not callable(getattr(module, "_compact_transition_batch", None))
        ):
            raise IncompatibleSchemaError(
                "native extension does not expose the PUYO-200 compact batch contract"
            )
        self._module = module
        self._capabilities = boundary.capabilities

    @property
    def capabilities(self):
        return self._capabilities

    def transition_batch(
        self,
        records: Sequence[NativeCompactTransitionInput],
        *,
        capture_trace: bool = False,
        include_actions: bool = False,
        measure_timing: bool = False,
    ) -> NativeCompactBatchResult:
        normalized = tuple(records)
        request = encode_native_compact_batch(
            normalized,
            capture_trace=capture_trace,
            include_actions=include_actions,
            measure_timing=measure_timing,
        )
        try:
            response = self._module._compact_transition_batch(request)
        except Exception as exc:
            raise NativeInternalPanicError(
                f"native compact batch call escaped its error frame: {exc}",
                provenance={
                    "source_revision": self.capabilities.source_revision,
                    "build_profile": self.capabilities.build_profile,
                },
            ) from exc
        result = decode_native_compact_batch_response(response)
        if len(result.records) != len(normalized):
            raise IncompatibleSchemaError(
                "native compact response record count changed"
            )
        for index, (request_record, output) in enumerate(
            zip(normalized, result.records, strict=True)
        ):
            if output.action_id != request_record.action_id:
                raise IncompatibleSchemaError(
                    f"native compact response changed action ID at record {index}"
                )
            if not output.valid and output.state != request_record.state:
                raise IncompatibleSchemaError(
                    f"invalid native transition mutated state at record {index}"
                )
            if output.state.score != request_record.state.score + output.score_delta:
                raise IncompatibleSchemaError(
                    f"native compact score delta mismatch at record {index}"
                )
        return result


__all__ = [
    "NATIVE_COMPACT_HOT_CHILD_STATE_BYTES",
    "NATIVE_COMPACT_HOT_RESULT_ABI_VERSION",
    "NATIVE_COMPACT_HOT_RESULT_BYTES",
    "NATIVE_COMPACT_HOT_RESULT_SCHEMA_VERSION",
    "NATIVE_COMPACT_KERNEL_PATH",
    "NATIVE_COMPACT_TRANSITION_ABI_VERSION",
    "NATIVE_COMPACT_TRANSITION_SCHEMA_VERSION",
    "NativeCompactArithmeticOverflowError",
    "NativeCompactBatchClient",
    "NativeCompactBatchResult",
    "NativeCompactBatchTiming",
    "NativeCompactChainStep",
    "NativeCompactErrorCode",
    "NativeCompactTransitionInput",
    "NativeCompactTransitionResult",
    "decode_native_compact_batch_response",
    "encode_native_compact_batch",
]
