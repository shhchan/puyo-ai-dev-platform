"""Versioned Python boundary for the deep-chain native extension.

The production deep-chain builder remains Python-only.  This module owns the
decision-level binary contract and the strict adapter that later native kernel
tickets extend.  Search nodes never cross this boundary or carry Python
callbacks.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import struct
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from src.core.constants import PuyoColor

if TYPE_CHECKING:
    from agents.chain_structure import ChainStructureConfig
    from agents.compact_search import CompactSearchState
    from agents.long_horizon_search import LongHorizonSearchConfig

NATIVE_MODULE_NAME = "_puyo_deep_chain_native"
WIRE_NAME = "puyo.deep_chain_native.envelope.v1"
REQUEST_SCHEMA_VERSION = "puyo.deep_chain_native.request.v1"
RESULT_SCHEMA_VERSION = "puyo.deep_chain_native.result.v1"
ACTION_LAYOUT_VERSION = "puyo.placement_actions.v1"
COMPACT_SEARCH_SCHEMA_VERSION = "puyo.compact_search_state.v1"
FUTURE_SAMPLING_SCHEMA_VERSION = "puyo.future_tsumo_sampling.v1"
EXPECTED_CHAIN_RANKING_RULE_VERSION = "puyo.expected_chain_ranking.v2"
TERMINAL_FIRE_SCORE_VERSION = "puyo.build_main_terminal_score.v1"
DEEP_CHAIN_DIAGNOSTICS_SCHEMA_VERSION = "puyo.deep_chain_builder.diagnostics.v1"
CHAIN_STRUCTURE_FEATURE_VERSION = "puyo.chain_structure_features.v1"
CHAIN_STRUCTURE_WEIGHT_SCHEMA_VERSION = "puyo.chain_structure_weights.v1"
ABI_VERSION = 1
SCHEMA_MAJOR = 1
SCHEMA_MINOR = 0
NUM_ACTIONS = 22
REQUEST_SCHEMA_DIGEST = (
    "fab9cfdae1b6a88a21fdfd2358df9e6f7276bd543f393ee095f581dd8f01c05e"
)
RESULT_SCHEMA_DIGEST = (
    "eb94050789560a99296ee574f210c7cbe945f85b953f3b27801d7c9a7f800c0b"
)

_MAGIC = b"PDCN"
_BYTE_ORDER_LITTLE = 1
_HEADER = struct.Struct("<4sHHBBHIIHHQ")
_TLV = struct.Struct("<HHI")
_HEADER_BYTES = _HEADER.size
_MAX_REQUEST_BYTES = 16 * 1024 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_SECTIONS = 64
_MAX_KNOWN_PAIRS = 256
_MAX_STRING_BYTES = 4_096
_REQUIRED_TAG = 0x8000
FUTURE_SAMPLING_MODES = frozenset({"seeded-authoritative", "legacy-fixed-six"})
TERMINAL_FIRE_RULES = frozenset({"continue", "record_and_stop"})
FIRE_CONTEXTS = frozenset({"safe_build", "forced_safety"})

REQUEST_ROOT_STATE_TAG = 0x8001
REQUEST_KNOWN_PAIRS_TAG = 0x8002
REQUEST_SEARCH_CONFIG_TAG = 0x8003
REQUEST_EVALUATOR_CONFIG_TAG = 0x8004
REQUEST_SCHEMA_IDENTITIES_TAG = 0x8005
REQUEST_EXECUTION_TAG = 0x8006

CAPABILITIES_METADATA_TAG = 0x8101
ERROR_DETAILS_TAG = 0x8201

RESULT_DECISION_TAG = 0x8301
RESULT_COUNTERS_TAG = 0x8302
RESULT_ROOT_EVIDENCE_TAG = 0x8303
RESULT_REPRESENTATIVES_TAG = 0x8304
RESULT_DIAGNOSTICS_TAG = 0x8305
RESULT_PROVENANCE_TAG = 0x8306

_REQUEST_TAGS = frozenset(
    {
        REQUEST_ROOT_STATE_TAG,
        REQUEST_KNOWN_PAIRS_TAG,
        REQUEST_SEARCH_CONFIG_TAG,
        REQUEST_EVALUATOR_CONFIG_TAG,
        REQUEST_SCHEMA_IDENTITIES_TAG,
        REQUEST_EXECUTION_TAG,
    }
)
_RESULT_TAGS = frozenset(
    {
        RESULT_DECISION_TAG,
        RESULT_COUNTERS_TAG,
        RESULT_ROOT_EVIDENCE_TAG,
        RESULT_REPRESENTATIVES_TAG,
        RESULT_DIAGNOSTICS_TAG,
        RESULT_PROVENANCE_TAG,
    }
)
_SCHEMA_IDENTITIES = (
    ACTION_LAYOUT_VERSION,
    COMPACT_SEARCH_SCHEMA_VERSION,
    FUTURE_SAMPLING_SCHEMA_VERSION,
    EXPECTED_CHAIN_RANKING_RULE_VERSION,
    TERMINAL_FIRE_SCORE_VERSION,
    DEEP_CHAIN_DIAGNOSTICS_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
)
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

_COLOR_TO_ID = {
    PuyoColor.RED: 1,
    PuyoColor.BLUE: 2,
    PuyoColor.GREEN: 3,
    PuyoColor.YELLOW: 4,
    PuyoColor.PURPLE: 5,
}
_ID_TO_COLOR = {value: key for key, value in _COLOR_TO_ID.items()}


class EnvelopeKind(IntEnum):
    REQUEST = 1
    SUCCESS = 2
    ERROR = 3
    CAPABILITIES = 4


class NativeErrorCode(IntEnum):
    INCOMPATIBLE_SCHEMA = 1
    INVALID_INPUT = 2
    UNSUPPORTED_CONFIG = 3
    RESOURCE_EXHAUSTED = 4
    INTERNAL_PANIC = 5
    BACKEND_UNAVAILABLE = 6


class DeepChainNativeError(RuntimeError):
    """Stable base exception for the Python/native boundary."""

    code = NativeErrorCode.INTERNAL_PANIC

    def __init__(
        self,
        message: str,
        *,
        failing_tag: int = 0,
        retry_safe: bool = False,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(message))
        self.failing_tag = int(failing_tag)
        self.retry_safe = bool(retry_safe)
        self.provenance = dict(provenance or {})


class IncompatibleSchemaError(DeepChainNativeError):
    code = NativeErrorCode.INCOMPATIBLE_SCHEMA


class InvalidNativeInputError(DeepChainNativeError, ValueError):
    code = NativeErrorCode.INVALID_INPUT


class UnsupportedNativeConfigError(DeepChainNativeError):
    code = NativeErrorCode.UNSUPPORTED_CONFIG


class NativeResourceExhaustedError(DeepChainNativeError):
    code = NativeErrorCode.RESOURCE_EXHAUSTED


class NativeInternalPanicError(DeepChainNativeError):
    code = NativeErrorCode.INTERNAL_PANIC


class NativeBackendUnavailableError(DeepChainNativeError, ImportError):
    code = NativeErrorCode.BACKEND_UNAVAILABLE


_ERROR_TYPES: dict[NativeErrorCode, type[DeepChainNativeError]] = {
    NativeErrorCode.INCOMPATIBLE_SCHEMA: IncompatibleSchemaError,
    NativeErrorCode.INVALID_INPUT: InvalidNativeInputError,
    NativeErrorCode.UNSUPPORTED_CONFIG: UnsupportedNativeConfigError,
    NativeErrorCode.RESOURCE_EXHAUSTED: NativeResourceExhaustedError,
    NativeErrorCode.INTERNAL_PANIC: NativeInternalPanicError,
    NativeErrorCode.BACKEND_UNAVAILABLE: NativeBackendUnavailableError,
}


@dataclass(frozen=True, slots=True)
class EnvelopeSection:
    tag: int
    version: int
    payload: bytes

    def __post_init__(self) -> None:
        if not 0 < int(self.tag) <= 0xFFFF:
            raise InvalidNativeInputError("section tag must be a non-zero u16")
        if not 0 < int(self.version) <= 0xFFFF:
            raise InvalidNativeInputError("section version must be a non-zero u16")
        object.__setattr__(self, "payload", bytes(self.payload))


@dataclass(frozen=True, slots=True)
class Envelope:
    kind: EnvelopeKind
    request_id: int
    schema_major: int
    schema_minor: int
    sections: tuple[EnvelopeSection, ...]

    @property
    def section_map(self) -> dict[int, EnvelopeSection]:
        return {section.tag: section for section in self.sections}


class _Writer:
    def __init__(self) -> None:
        self.data = bytearray()

    def u8(self, value: int, name: str) -> None:
        self._bounded(value, 0xFF, name)
        self.data.extend(struct.pack("<B", int(value)))

    def u16(self, value: int, name: str) -> None:
        self._bounded(value, 0xFFFF, name)
        self.data.extend(struct.pack("<H", int(value)))

    def u32(self, value: int, name: str) -> None:
        self._bounded(value, 0xFFFFFFFF, name)
        self.data.extend(struct.pack("<I", int(value)))

    def u64(self, value: int, name: str) -> None:
        self._bounded(value, 0xFFFFFFFFFFFFFFFF, name)
        self.data.extend(struct.pack("<Q", int(value)))

    def f64(self, value: float, name: str) -> None:
        if not math.isfinite(float(value)):
            raise InvalidNativeInputError(f"{name} must be finite")
        self.data.extend(struct.pack("<d", float(value)))

    def string(self, value: str, name: str) -> None:
        encoded = str(value).encode("utf-8")
        if not encoded:
            raise InvalidNativeInputError(f"{name} must not be empty")
        if len(encoded) > _MAX_STRING_BYTES:
            raise InvalidNativeInputError(f"{name} exceeds the string limit")
        self.u16(len(encoded), f"{name} length")
        self.data.extend(encoded)

    def raw(self, value: bytes) -> None:
        self.data.extend(value)

    @staticmethod
    def _bounded(value: int, maximum: int, name: str) -> None:
        try:
            numeric = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise InvalidNativeInputError(f"{name} must be an integer") from exc
        if not 0 <= numeric <= maximum:
            raise InvalidNativeInputError(f"{name} is outside its wire range")


class _Reader:
    def __init__(self, payload: bytes, *, failing_tag: int) -> None:
        self.payload = memoryview(payload)
        self.offset = 0
        self.failing_tag = int(failing_tag)

    def take(self, length: int, name: str) -> bytes:
        end = self.offset + int(length)
        if length < 0 or end < self.offset or end > len(self.payload):
            raise InvalidNativeInputError(
                f"truncated {name}", failing_tag=self.failing_tag
            )
        value = bytes(self.payload[self.offset : end])
        self.offset = end
        return value

    def u8(self, name: str) -> int:
        return struct.unpack("<B", self.take(1, name))[0]

    def u16(self, name: str) -> int:
        return struct.unpack("<H", self.take(2, name))[0]

    def u32(self, name: str) -> int:
        return struct.unpack("<I", self.take(4, name))[0]

    def u64(self, name: str) -> int:
        return struct.unpack("<Q", self.take(8, name))[0]

    def f64(self, name: str) -> float:
        value = struct.unpack("<d", self.take(8, name))[0]
        if not math.isfinite(value):
            raise InvalidNativeInputError(
                f"{name} must be finite", failing_tag=self.failing_tag
            )
        return value

    def string(self, name: str) -> str:
        length = self.u16(f"{name} length")
        if not length or length > _MAX_STRING_BYTES:
            raise InvalidNativeInputError(
                f"invalid {name} length", failing_tag=self.failing_tag
            )
        try:
            return self.take(length, name).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidNativeInputError(
                f"{name} is not valid UTF-8", failing_tag=self.failing_tag
            ) from exc

    def finish(self) -> None:
        if self.offset != len(self.payload):
            raise InvalidNativeInputError(
                "section contains trailing data", failing_tag=self.failing_tag
            )


def encode_envelope(
    kind: EnvelopeKind,
    request_id: int,
    sections: Sequence[EnvelopeSection],
    *,
    schema_major: int = SCHEMA_MAJOR,
    schema_minor: int = SCHEMA_MINOR,
) -> bytes:
    """Encode the fixed header and aligned singleton TLV sections."""

    if not 0 <= int(request_id) <= 0xFFFFFFFFFFFFFFFF:
        raise InvalidNativeInputError("request_id is outside the u64 range")
    if len(sections) > _MAX_SECTIONS:
        raise InvalidNativeInputError("too many envelope sections")
    seen: set[int] = set()
    body = bytearray()
    for section in sections:
        if section.tag in seen:
            raise InvalidNativeInputError(
                "duplicate singleton section", failing_tag=section.tag
            )
        seen.add(section.tag)
        if len(section.payload) > _MAX_REQUEST_BYTES:
            raise InvalidNativeInputError(
                "section exceeds the envelope limit", failing_tag=section.tag
            )
        body.extend(_TLV.pack(section.tag, section.version, len(section.payload)))
        body.extend(section.payload)
        padding = (-len(section.payload)) % 8
        body.extend(b"\x00" * padding)
    if len(body) > _MAX_REQUEST_BYTES:
        raise InvalidNativeInputError("envelope body exceeds the configured limit")
    return _HEADER.pack(
        _MAGIC,
        int(schema_major),
        int(schema_minor),
        int(kind),
        _BYTE_ORDER_LITTLE,
        0,
        _HEADER_BYTES,
        len(body),
        len(sections),
        0,
        int(request_id),
    ) + bytes(body)


def decode_envelope(
    value: bytes | bytearray | memoryview,
    *,
    known_tags: frozenset[int] | None = None,
    maximum_bytes: int = _MAX_RESPONSE_BYTES,
) -> Envelope:
    """Decode and fail closed on malformed or incompatible framing."""

    payload = bytes(value)
    if len(payload) < _HEADER_BYTES:
        raise InvalidNativeInputError("envelope is shorter than the fixed header")
    if len(payload) > int(maximum_bytes):
        raise InvalidNativeInputError("envelope exceeds the configured limit")
    (
        magic,
        schema_major,
        schema_minor,
        raw_kind,
        byte_order,
        flags,
        header_bytes,
        body_bytes,
        section_count,
        reserved,
        request_id,
    ) = _HEADER.unpack_from(payload)
    if magic != _MAGIC:
        raise InvalidNativeInputError("invalid native envelope magic")
    if schema_major != SCHEMA_MAJOR or schema_minor < SCHEMA_MINOR:
        raise IncompatibleSchemaError(
            f"unsupported envelope schema {schema_major}.{schema_minor}"
        )
    if byte_order != _BYTE_ORDER_LITTLE:
        raise IncompatibleSchemaError("only little-endian envelopes are supported")
    if flags or reserved or header_bytes != _HEADER_BYTES:
        raise InvalidNativeInputError("invalid fixed-header control field")
    if section_count > _MAX_SECTIONS:
        raise InvalidNativeInputError("envelope declares too many sections")
    if body_bytes != len(payload) - _HEADER_BYTES:
        raise InvalidNativeInputError("envelope body length does not match framing")
    try:
        kind = EnvelopeKind(raw_kind)
    except ValueError as exc:
        raise IncompatibleSchemaError(f"unknown envelope kind: {raw_kind}") from exc

    sections: list[EnvelopeSection] = []
    seen: set[int] = set()
    offset = _HEADER_BYTES
    for _ in range(section_count):
        if offset + _TLV.size > len(payload):
            raise InvalidNativeInputError("truncated section header")
        tag, version, length = _TLV.unpack_from(payload, offset)
        offset += _TLV.size
        end = offset + length
        if end < offset or end > len(payload):
            raise InvalidNativeInputError(
                "section length exceeds envelope", failing_tag=tag
            )
        if tag in seen:
            raise InvalidNativeInputError(
                "duplicate singleton section", failing_tag=tag
            )
        seen.add(tag)
        section_payload = payload[offset:end]
        offset = end
        padding = (-length) % 8
        padding_end = offset + padding
        if padding_end > len(payload) or any(payload[offset:padding_end]):
            raise InvalidNativeInputError(
                "section padding must be present and zero", failing_tag=tag
            )
        offset = padding_end
        if known_tags is not None and tag not in known_tags:
            if tag & _REQUIRED_TAG:
                raise IncompatibleSchemaError(
                    "unknown required section", failing_tag=tag
                )
            continue
        sections.append(EnvelopeSection(tag, version, section_payload))
    if offset != len(payload):
        raise InvalidNativeInputError("envelope contains trailing data")
    return Envelope(kind, request_id, schema_major, schema_minor, tuple(sections))


@dataclass(frozen=True, slots=True)
class NativeDecisionRequest:
    state: CompactSearchState
    known_pairs: tuple[tuple[PuyoColor, PuyoColor], ...]
    search_config: LongHorizonSearchConfig
    evaluator_config: ChainStructureConfig
    config_digest: str
    profile_name: str
    profile_version: str
    config_version: str
    request_id: int = 0
    pair_cursor: int = 0
    scenario_cursor: int = 0
    execution_mode: str = "oracle-1"
    response_detail_flags: int = 0
    max_response_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        pairs = tuple(tuple(pair) for pair in self.known_pairs)
        if not pairs or len(pairs) > _MAX_KNOWN_PAIRS:
            raise InvalidNativeInputError("known_pairs has an invalid count")
        if any(len(pair) != 2 for pair in pairs):
            raise InvalidNativeInputError("each known pair must contain two colors")
        if any(color not in _COLOR_TO_ID for pair in pairs for color in pair):
            raise InvalidNativeInputError("known pairs accept normal colors only")
        if not 0 <= int(self.pair_cursor) < len(pairs):
            raise InvalidNativeInputError("pair_cursor is outside known_pairs")
        if not 0 <= int(self.scenario_cursor) < int(self.search_config.scenarios):
            raise InvalidNativeInputError("scenario_cursor is outside scenarios")
        if self.execution_mode != "oracle-1":
            raise UnsupportedNativeConfigError(
                "PUYO-199 supports only the deterministic oracle-1 mode"
            )
        if not 0 < int(self.max_response_bytes) <= _MAX_RESPONSE_BYTES:
            raise InvalidNativeInputError("max_response_bytes is outside its limit")
        if int(self.response_detail_flags) & ~0x7:
            raise InvalidNativeInputError("unknown response detail flag")
        digest = str(self.config_digest).lower()
        if len(digest) != 64:
            raise InvalidNativeInputError("config_digest must be SHA-256 hex")
        try:
            bytes.fromhex(digest)
        except ValueError as exc:
            raise InvalidNativeInputError("config_digest must be SHA-256 hex") from exc
        if any(
            not str(value)
            for value in (self.profile_name, self.profile_version, self.config_version)
        ):
            raise InvalidNativeInputError("profile and config identities are required")
        if not 0 <= int(self.request_id) <= 0xFFFFFFFFFFFFFFFF:
            raise InvalidNativeInputError("request_id is outside the u64 range")
        object.__setattr__(self, "known_pairs", pairs)
        object.__setattr__(self, "config_digest", digest)


def _encode_state(state: CompactSearchState) -> bytes:
    try:
        encoded = state.to_bytes()
    except (OverflowError, ValueError) as exc:
        raise InvalidNativeInputError(
            "compact state is outside its wire range"
        ) from exc
    if len(encoded) != 87:
        raise InvalidNativeInputError("compact state has an unexpected byte length")
    return encoded


def _decode_state(payload: bytes) -> CompactSearchState:
    from agents.compact_search import CompactSearchState

    if len(payload) != 87 or payload[:4] != b"CSK1":
        raise InvalidNativeInputError(
            "invalid compact state framing", failing_tag=REQUEST_ROOT_STATE_TAG
        )
    offset = 4
    planes = []
    for _ in range(6):
        planes.append(int.from_bytes(payload[offset : offset + 11], "little"))
        offset += 11
    flags = payload[offset]
    offset += 1
    if flags & ~0x3:
        raise InvalidNativeInputError(
            "compact state contains unknown flags", failing_tag=REQUEST_ROOT_STATE_TAG
        )
    score = int.from_bytes(payload[offset : offset + 8], "little")
    offset += 8
    last_chain_end_score = int.from_bytes(payload[offset : offset + 8], "little")
    try:
        return CompactSearchState(
            planes=tuple(planes),
            all_clear_bonus_pending=bool(flags & 0x1),
            game_over=bool(flags & 0x2),
            score=score,
            last_chain_end_score=last_chain_end_score,
        )
    except ValueError as exc:
        raise InvalidNativeInputError(
            str(exc), failing_tag=REQUEST_ROOT_STATE_TAG
        ) from exc


def _encode_known_pairs(
    pairs: Sequence[Sequence[PuyoColor]],
) -> bytes:
    writer = _Writer()
    writer.u16(len(pairs), "known pair count")
    for pair in pairs:
        for color in pair:
            try:
                color_id = _COLOR_TO_ID[color]
            except KeyError as exc:
                raise InvalidNativeInputError(
                    "known pairs accept normal colors only",
                    failing_tag=REQUEST_KNOWN_PAIRS_TAG,
                ) from exc
            writer.u8(color_id, "known pair color")
    return bytes(writer.data)


def _decode_known_pairs(
    payload: bytes,
) -> tuple[tuple[PuyoColor, PuyoColor], ...]:
    reader = _Reader(payload, failing_tag=REQUEST_KNOWN_PAIRS_TAG)
    count = reader.u16("known pair count")
    if not 0 < count <= _MAX_KNOWN_PAIRS:
        raise InvalidNativeInputError(
            "known_pairs has an invalid count", failing_tag=REQUEST_KNOWN_PAIRS_TAG
        )
    pairs = []
    for _ in range(count):
        raw = (reader.u8("axis color"), reader.u8("child color"))
        try:
            pairs.append((_ID_TO_COLOR[raw[0]], _ID_TO_COLOR[raw[1]]))
        except KeyError as exc:
            raise InvalidNativeInputError(
                "known pair contains an invalid color ID",
                failing_tag=REQUEST_KNOWN_PAIRS_TAG,
            ) from exc
    reader.finish()
    return tuple(pairs)


@dataclass(frozen=True, slots=True)
class _SearchSection:
    config: LongHorizonSearchConfig
    pair_cursor: int
    scenario_cursor: int
    profile_name: str
    profile_version: str
    config_version: str


def _encode_search(request: NativeDecisionRequest) -> bytes:
    config = request.search_config
    seed = config.resolved_decision_seed
    writer = _Writer()
    writer.u16(config.depth, "search depth")
    writer.u16(config.width, "search width")
    writer.u8(config.scenarios, "scenario count")
    writer.u8(config.minimum_chain_count, "minimum chain count")
    writer.u16(config.terminal_fire_chain_count, "terminal fire chain count")
    writer.u32(config.max_expanded_nodes, "max expanded nodes")
    writer.u16(config.root_survivor_quota, "root survivor quota")
    writer.u16(request.pair_cursor, "pair cursor")
    writer.u16(request.scenario_cursor, "scenario cursor")
    writer.u8(seed is not None, "seed present")
    writer.u8(config.winning_score_threshold is not None, "winning score present")
    writer.u8(config.use_transposition_table, "transposition table flag")
    writer.u8(0, "search reserved")
    writer.u64(0 if seed is None else seed, "decision seed")
    writer.u64(
        0 if config.winning_score_threshold is None else config.winning_score_threshold,
        "winning score threshold",
    )
    writer.f64(config.premature_target_gap_penalty, "premature target gap penalty")
    writer.string(config.future_sampling_mode, "future sampling mode")
    writer.string(config.terminal_fire_rule, "terminal fire rule")
    writer.string(config.fire_context, "fire context")
    writer.string(request.profile_name, "profile name")
    writer.string(request.profile_version, "profile version")
    writer.string(request.config_version, "config version")
    return bytes(writer.data)


def _decode_search(payload: bytes) -> _SearchSection:
    from agents.long_horizon_search import LongHorizonSearchConfig

    reader = _Reader(payload, failing_tag=REQUEST_SEARCH_CONFIG_TAG)
    depth = reader.u16("search depth")
    width = reader.u16("search width")
    scenarios = reader.u8("scenario count")
    minimum_chain_count = reader.u8("minimum chain count")
    terminal_fire_chain_count = reader.u16("terminal fire chain count")
    max_expanded_nodes = reader.u32("max expanded nodes")
    root_survivor_quota = reader.u16("root survivor quota")
    pair_cursor = reader.u16("pair cursor")
    scenario_cursor = reader.u16("scenario cursor")
    seed_present = reader.u8("seed present")
    winning_present = reader.u8("winning score present")
    use_transposition_table = reader.u8("transposition table flag")
    reserved = reader.u8("search reserved")
    decision_seed = reader.u64("decision seed")
    winning_score_threshold = reader.u64("winning score threshold")
    premature_target_gap_penalty = reader.f64("premature target gap penalty")
    future_sampling_mode = reader.string("future sampling mode")
    terminal_fire_rule = reader.string("terminal fire rule")
    fire_context = reader.string("fire context")
    profile_name = reader.string("profile name")
    profile_version = reader.string("profile version")
    config_version = reader.string("config version")
    reader.finish()
    if seed_present not in (0, 1) or winning_present not in (0, 1):
        raise InvalidNativeInputError(
            "invalid search option flag", failing_tag=REQUEST_SEARCH_CONFIG_TAG
        )
    if use_transposition_table not in (0, 1) or reserved:
        raise InvalidNativeInputError(
            "invalid search control flag", failing_tag=REQUEST_SEARCH_CONFIG_TAG
        )
    if not seed_present and decision_seed:
        raise InvalidNativeInputError(
            "absent decision seed must be zero", failing_tag=REQUEST_SEARCH_CONFIG_TAG
        )
    if not winning_present and winning_score_threshold:
        raise InvalidNativeInputError(
            "absent winning score threshold must be zero",
            failing_tag=REQUEST_SEARCH_CONFIG_TAG,
        )
    if future_sampling_mode not in FUTURE_SAMPLING_MODES:
        raise UnsupportedNativeConfigError(
            f"unsupported future sampling mode: {future_sampling_mode}",
            failing_tag=REQUEST_SEARCH_CONFIG_TAG,
        )
    if (
        terminal_fire_rule not in TERMINAL_FIRE_RULES
        or fire_context not in FIRE_CONTEXTS
    ):
        raise UnsupportedNativeConfigError(
            "unsupported terminal-fire configuration",
            failing_tag=REQUEST_SEARCH_CONFIG_TAG,
        )
    try:
        config = LongHorizonSearchConfig(
            depth=depth,
            width=width,
            scenarios=scenarios,
            minimum_chain_count=minimum_chain_count,
            max_expanded_nodes=max_expanded_nodes,
            decision_seed=decision_seed if seed_present else None,
            future_sampling_mode=future_sampling_mode,
            terminal_fire_rule=terminal_fire_rule,
            terminal_fire_chain_count=terminal_fire_chain_count,
            root_survivor_quota=root_survivor_quota,
            fire_context=fire_context,
            premature_target_gap_penalty=premature_target_gap_penalty,
            winning_score_threshold=(
                winning_score_threshold if winning_present else None
            ),
            use_transposition_table=bool(use_transposition_table),
        )
    except ValueError as exc:
        raise InvalidNativeInputError(
            str(exc), failing_tag=REQUEST_SEARCH_CONFIG_TAG
        ) from exc
    return _SearchSection(
        config,
        pair_cursor,
        scenario_cursor,
        profile_name,
        profile_version,
        config_version,
    )


def _encode_evaluator(config: ChainStructureConfig) -> bytes:
    writer = _Writer()
    writer.string(config.schema_version, "evaluator schema version")
    writer.string(config.feature_version, "evaluator feature version")
    writer.string(config.weight_version, "evaluator weight version")
    writer.u32(config.budget.max_added_puyos, "max added puyos")
    writer.u32(config.budget.max_pattern_nodes, "max pattern nodes")
    writer.u32(config.budget.max_resolution_nodes, "max resolution nodes")
    writer.u32(config.budget.max_candidates, "max evaluator candidates")
    writer.u16(len(_WEIGHT_NAMES), "weight count")
    writer.u16(0, "evaluator reserved")
    for name in _WEIGHT_NAMES:
        writer.f64(getattr(config.weights, name), f"weight {name}")
    writer.f64(config.fatal_score, "fatal score")
    return bytes(writer.data)


def _decode_evaluator(payload: bytes) -> ChainStructureConfig:
    from agents.chain_structure import (
        ChainStructureBudget,
        ChainStructureConfig,
        ChainStructureWeights,
    )

    reader = _Reader(payload, failing_tag=REQUEST_EVALUATOR_CONFIG_TAG)
    schema_version = reader.string("evaluator schema version")
    feature_version = reader.string("evaluator feature version")
    weight_version = reader.string("evaluator weight version")
    budget_values = {
        "max_added_puyos": reader.u32("max added puyos"),
        "max_pattern_nodes": reader.u32("max pattern nodes"),
        "max_resolution_nodes": reader.u32("max resolution nodes"),
        "max_candidates": reader.u32("max evaluator candidates"),
    }
    weight_count = reader.u16("weight count")
    reserved = reader.u16("evaluator reserved")
    if weight_count != len(_WEIGHT_NAMES) or reserved:
        raise IncompatibleSchemaError(
            "evaluator weight layout does not match",
            failing_tag=REQUEST_EVALUATOR_CONFIG_TAG,
        )
    weight_values = {name: reader.f64(f"weight {name}") for name in _WEIGHT_NAMES}
    fatal_score = reader.f64("fatal score")
    reader.finish()
    if schema_version != CHAIN_STRUCTURE_WEIGHT_SCHEMA_VERSION:
        raise IncompatibleSchemaError(
            "evaluator weight schema does not match",
            failing_tag=REQUEST_EVALUATOR_CONFIG_TAG,
        )
    if feature_version != CHAIN_STRUCTURE_FEATURE_VERSION:
        raise IncompatibleSchemaError(
            "evaluator feature schema does not match",
            failing_tag=REQUEST_EVALUATOR_CONFIG_TAG,
        )
    try:
        return ChainStructureConfig(
            schema_version=schema_version,
            feature_version=feature_version,
            weight_version=weight_version,
            budget=ChainStructureBudget(**budget_values),
            weights=ChainStructureWeights(**weight_values),
            fatal_score=fatal_score,
        )
    except ValueError as exc:
        raise InvalidNativeInputError(
            str(exc), failing_tag=REQUEST_EVALUATOR_CONFIG_TAG
        ) from exc


def _encode_schema_identities(config_digest: str) -> bytes:
    writer = _Writer()
    writer.u16(len(_SCHEMA_IDENTITIES), "schema identity count")
    writer.u16(0, "schema identity reserved")
    for index, value in enumerate(_SCHEMA_IDENTITIES):
        writer.string(value, f"schema identity {index}")
    writer.raw(bytes.fromhex(config_digest))
    return bytes(writer.data)


def _decode_schema_identities(payload: bytes) -> str:
    reader = _Reader(payload, failing_tag=REQUEST_SCHEMA_IDENTITIES_TAG)
    count = reader.u16("schema identity count")
    reserved = reader.u16("schema identity reserved")
    if count != len(_SCHEMA_IDENTITIES) or reserved:
        raise IncompatibleSchemaError(
            "schema identity layout does not match",
            failing_tag=REQUEST_SCHEMA_IDENTITIES_TAG,
        )
    identities = tuple(
        reader.string(f"schema identity {index}") for index in range(count)
    )
    digest = reader.take(32, "config digest").hex()
    reader.finish()
    if identities != _SCHEMA_IDENTITIES:
        raise IncompatibleSchemaError(
            "request schema identity does not match",
            failing_tag=REQUEST_SCHEMA_IDENTITIES_TAG,
        )
    return digest


def _encode_execution(request: NativeDecisionRequest) -> bytes:
    writer = _Writer()
    writer.string(request.execution_mode, "execution mode")
    writer.u32(request.response_detail_flags, "response detail flags")
    writer.u32(request.max_response_bytes, "maximum response bytes")
    writer.u8(0, "Python callback flag")
    writer.u8(1, "scalar fallback requirement")
    writer.u16(0, "execution reserved")
    return bytes(writer.data)


def _decode_execution(payload: bytes) -> tuple[str, int, int]:
    reader = _Reader(payload, failing_tag=REQUEST_EXECUTION_TAG)
    execution_mode = reader.string("execution mode")
    detail_flags = reader.u32("response detail flags")
    max_response_bytes = reader.u32("maximum response bytes")
    allows_python_callbacks = reader.u8("Python callback flag")
    requires_scalar_fallback = reader.u8("scalar fallback requirement")
    reserved = reader.u16("execution reserved")
    reader.finish()
    if allows_python_callbacks or requires_scalar_fallback != 1 or reserved:
        raise InvalidNativeInputError(
            "invalid execution boundary control", failing_tag=REQUEST_EXECUTION_TAG
        )
    if execution_mode != "oracle-1":
        raise UnsupportedNativeConfigError(
            f"unsupported execution mode: {execution_mode}",
            failing_tag=REQUEST_EXECUTION_TAG,
        )
    if detail_flags & ~0x7 or not 0 < max_response_bytes <= _MAX_RESPONSE_BYTES:
        raise InvalidNativeInputError(
            "invalid response limits", failing_tag=REQUEST_EXECUTION_TAG
        )
    return execution_mode, detail_flags, max_response_bytes


def encode_request(request: NativeDecisionRequest) -> bytes:
    sections = (
        EnvelopeSection(REQUEST_ROOT_STATE_TAG, 1, _encode_state(request.state)),
        EnvelopeSection(
            REQUEST_KNOWN_PAIRS_TAG,
            1,
            _encode_known_pairs(request.known_pairs),
        ),
        EnvelopeSection(REQUEST_SEARCH_CONFIG_TAG, 1, _encode_search(request)),
        EnvelopeSection(
            REQUEST_EVALUATOR_CONFIG_TAG,
            1,
            _encode_evaluator(request.evaluator_config),
        ),
        EnvelopeSection(
            REQUEST_SCHEMA_IDENTITIES_TAG,
            1,
            _encode_schema_identities(request.config_digest),
        ),
        EnvelopeSection(REQUEST_EXECUTION_TAG, 1, _encode_execution(request)),
    )
    return encode_envelope(EnvelopeKind.REQUEST, request.request_id, sections)


def decode_request(payload: bytes | bytearray | memoryview) -> NativeDecisionRequest:
    envelope = decode_envelope(
        payload, known_tags=_REQUEST_TAGS, maximum_bytes=_MAX_REQUEST_BYTES
    )
    if envelope.kind != EnvelopeKind.REQUEST:
        raise InvalidNativeInputError("expected a request envelope")
    sections = envelope.section_map
    missing = _REQUEST_TAGS - sections.keys()
    if missing:
        raise IncompatibleSchemaError(
            f"request is missing required sections: {sorted(missing)}"
        )
    if any(section.version != 1 for section in sections.values()):
        raise IncompatibleSchemaError("unsupported request section version")
    state = _decode_state(sections[REQUEST_ROOT_STATE_TAG].payload)
    known_pairs = _decode_known_pairs(sections[REQUEST_KNOWN_PAIRS_TAG].payload)
    search = _decode_search(sections[REQUEST_SEARCH_CONFIG_TAG].payload)
    evaluator = _decode_evaluator(sections[REQUEST_EVALUATOR_CONFIG_TAG].payload)
    config_digest = _decode_schema_identities(
        sections[REQUEST_SCHEMA_IDENTITIES_TAG].payload
    )
    execution_mode, detail_flags, max_response_bytes = _decode_execution(
        sections[REQUEST_EXECUTION_TAG].payload
    )
    if search.pair_cursor >= len(known_pairs):
        raise InvalidNativeInputError(
            "pair_cursor is outside known_pairs", failing_tag=REQUEST_SEARCH_CONFIG_TAG
        )
    if search.scenario_cursor >= search.config.scenarios:
        raise InvalidNativeInputError(
            "scenario_cursor is outside scenarios",
            failing_tag=REQUEST_SEARCH_CONFIG_TAG,
        )
    return NativeDecisionRequest(
        state=state,
        known_pairs=known_pairs,
        search_config=search.config,
        evaluator_config=evaluator,
        config_digest=config_digest,
        profile_name=search.profile_name,
        profile_version=search.profile_version,
        config_version=search.config_version,
        request_id=envelope.request_id,
        pair_cursor=search.pair_cursor,
        scenario_cursor=search.scenario_cursor,
        execution_mode=execution_mode,
        response_detail_flags=detail_flags,
        max_response_bytes=max_response_bytes,
    )


@dataclass(frozen=True, slots=True)
class NativeCapabilities:
    abi_version: int
    schema_major: int
    schema_minor_min: int
    schema_minor_max: int
    max_request_bytes: int
    max_response_bytes: int
    max_sections: int
    scalar_fallback: bool
    gil_detach: bool
    parallel: bool
    wheel_hash_hook: bool
    max_threads: int
    wire_name: str
    request_schema_digest: str
    result_schema_digest: str
    crate_version: str
    source_revision: str
    compiler: str
    build_profile: str
    target: str
    python_abi: str
    simd_path: str
    cpu_features: tuple[str, ...]
    thread_modes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "abi_version": self.abi_version,
            "schema": {
                "major": self.schema_major,
                "minor_min": self.schema_minor_min,
                "minor_max": self.schema_minor_max,
                "request_digest": self.request_schema_digest,
                "result_digest": self.result_schema_digest,
            },
            "limits": {
                "max_request_bytes": self.max_request_bytes,
                "max_response_bytes": self.max_response_bytes,
                "max_sections": self.max_sections,
            },
            "source_revision": self.source_revision,
            "compiler": self.compiler,
            "build_profile": self.build_profile,
            "target": self.target,
            "python_abi": self.python_abi,
            "cpu_features": list(self.cpu_features),
            "scalar_fallback": self.scalar_fallback,
            "simd_path": self.simd_path,
            "gil_detach": self.gil_detach,
            "parallel": self.parallel,
            "wheel_hash_hook": self.wheel_hash_hook,
            "thread_modes": list(self.thread_modes),
            "max_threads": self.max_threads,
            "crate_version": self.crate_version,
            "wire_name": self.wire_name,
        }


def decode_capabilities(payload: bytes | bytearray | memoryview) -> NativeCapabilities:
    envelope = decode_envelope(
        payload, known_tags=frozenset({CAPABILITIES_METADATA_TAG})
    )
    if envelope.kind != EnvelopeKind.CAPABILITIES:
        raise InvalidNativeInputError("expected a capabilities envelope")
    sections = envelope.section_map
    if set(sections) != {CAPABILITIES_METADATA_TAG}:
        raise IncompatibleSchemaError("capabilities metadata section is required")
    section = sections[CAPABILITIES_METADATA_TAG]
    if section.version != 1:
        raise IncompatibleSchemaError("unsupported capabilities section version")
    reader = _Reader(section.payload, failing_tag=CAPABILITIES_METADATA_TAG)
    abi_version = reader.u16("ABI version")
    schema_major = reader.u16("schema major")
    schema_minor_min = reader.u16("schema minor minimum")
    schema_minor_max = reader.u16("schema minor maximum")
    max_request_bytes = reader.u32("maximum request bytes")
    max_response_bytes = reader.u32("maximum response bytes")
    max_sections = reader.u16("maximum sections")
    scalar_fallback = reader.u8("scalar fallback")
    gil_detach = reader.u8("GIL detach")
    parallel = reader.u8("parallel capability")
    wheel_hash_hook = reader.u8("wheel hash hook")
    max_threads = reader.u16("maximum threads")
    string_count = reader.u16("capability string count")
    if (
        scalar_fallback not in (0, 1)
        or gil_detach not in (0, 1)
        or parallel not in (0, 1)
        or wheel_hash_hook not in (0, 1)
        or string_count != 12
    ):
        raise InvalidNativeInputError(
            "invalid capability controls", failing_tag=CAPABILITIES_METADATA_TAG
        )
    values = [reader.string(f"capability string {index}") for index in range(12)]
    reader.finish()
    cpu_features = tuple(item for item in values[10].split(",") if item)
    thread_modes = tuple(item for item in values[11].split(",") if item)
    return NativeCapabilities(
        abi_version=abi_version,
        schema_major=schema_major,
        schema_minor_min=schema_minor_min,
        schema_minor_max=schema_minor_max,
        max_request_bytes=max_request_bytes,
        max_response_bytes=max_response_bytes,
        max_sections=max_sections,
        scalar_fallback=bool(scalar_fallback),
        gil_detach=bool(gil_detach),
        parallel=bool(parallel),
        wheel_hash_hook=bool(wheel_hash_hook),
        max_threads=max_threads,
        wire_name=values[0],
        request_schema_digest=values[1],
        result_schema_digest=values[2],
        crate_version=values[3],
        source_revision=values[4],
        compiler=values[5],
        build_profile=values[6],
        target=values[7],
        python_abi=values[8],
        simd_path=values[9],
        cpu_features=cpu_features,
        thread_modes=thread_modes,
    )


def _decode_error(envelope: Envelope) -> DeepChainNativeError:
    sections = envelope.section_map
    if set(sections) != {ERROR_DETAILS_TAG}:
        return NativeInternalPanicError("native error envelope is malformed")
    section = sections[ERROR_DETAILS_TAG]
    if section.version != 1:
        return IncompatibleSchemaError("unsupported native error section version")
    reader = _Reader(section.payload, failing_tag=ERROR_DETAILS_TAG)
    raw_code = reader.u16("error code")
    failing_tag = reader.u16("failing tag")
    retry_safe = reader.u8("retry-safe flag")
    reserved = reader.take(3, "error reserved")
    message = reader.string("error message")
    source_revision = reader.string("error source revision")
    build_profile = reader.string("error build profile")
    reader.finish()
    if retry_safe not in (0, 1) or any(reserved):
        return NativeInternalPanicError("native error control field is malformed")
    try:
        code = NativeErrorCode(raw_code)
    except ValueError:
        return NativeInternalPanicError(
            f"native returned unknown error code {raw_code}"
        )
    error_type = _ERROR_TYPES[code]
    return error_type(
        message,
        failing_tag=failing_tag,
        retry_safe=bool(retry_safe),
        provenance={
            "source_revision": source_revision,
            "build_profile": build_profile,
        },
    )


@dataclass(frozen=True, slots=True)
class NativeDecisionResult:
    request_id: int
    selected_action: int
    ranked_root_actions: tuple[int, ...]
    search_complete: bool
    budget_exhausted: bool
    deterministic_digest: str
    counters: Mapping[str, int]
    root_evidence: bytes
    representatives: bytes
    diagnostics: bytes
    provenance: Mapping[str, Any]


def _decode_record_section(payload: bytes, *, tag: int, name: str) -> bytes:
    """Validate reserved result-record framing before exposing its body."""

    reader = _Reader(payload, failing_tag=tag)
    schema_version = reader.u16(f"{name} schema version")
    reserved = reader.u16(f"{name} reserved")
    _record_count = reader.u32(f"{name} record count")
    body_bytes = reader.u32(f"{name} body bytes")
    body = reader.take(body_bytes, f"{name} body")
    reader.finish()
    if schema_version != 1 or reserved:
        raise IncompatibleSchemaError(
            f"unsupported {name} record framing",
            failing_tag=tag,
        )
    return body


def _decode_result(envelope: Envelope) -> NativeDecisionResult:
    sections = envelope.section_map
    missing = _RESULT_TAGS - sections.keys()
    if missing:
        raise IncompatibleSchemaError(
            f"native result is missing required sections: {sorted(missing)}"
        )
    if any(section.version != 1 for section in sections.values()):
        raise IncompatibleSchemaError("unsupported result section version")

    decision_reader = _Reader(
        sections[RESULT_DECISION_TAG].payload,
        failing_tag=RESULT_DECISION_TAG,
    )
    selected_action = decision_reader.u8("selected action")
    search_complete = decision_reader.u8("search-complete flag")
    budget_exhausted = decision_reader.u8("budget-exhausted flag")
    reserved = decision_reader.u8("decision reserved")
    ranked_count = decision_reader.u16("ranked root count")
    reserved_2 = decision_reader.u16("decision reserved 2")
    deterministic_digest = decision_reader.string("deterministic result digest")
    ranked_root_actions = tuple(
        decision_reader.u8("ranked root action") for _ in range(ranked_count)
    )
    decision_reader.finish()
    if (
        selected_action >= NUM_ACTIONS
        or search_complete not in (0, 1)
        or budget_exhausted not in (0, 1)
        or reserved
        or reserved_2
        or len(set(ranked_root_actions)) != len(ranked_root_actions)
        or any(action >= NUM_ACTIONS for action in ranked_root_actions)
    ):
        raise InvalidNativeInputError(
            "native result contains an invalid action or decision control",
            failing_tag=RESULT_DECISION_TAG,
        )

    counter_names = (
        "expanded_nodes",
        "generated_nodes",
        "evaluated_nodes",
        "invalid_nodes",
        "pruned_nodes",
        "terminal_fire_nodes",
        "game_over_nodes",
        "transposition_hits",
        "reached_depth",
    )
    counter_reader = _Reader(
        sections[RESULT_COUNTERS_TAG].payload,
        failing_tag=RESULT_COUNTERS_TAG,
    )
    counters = {name: counter_reader.u64(name) for name in counter_names}
    counter_reader.finish()

    provenance_reader = _Reader(
        sections[RESULT_PROVENANCE_TAG].payload,
        failing_tag=RESULT_PROVENANCE_TAG,
    )
    provenance_names = (
        "backend",
        "crate_version",
        "source_revision",
        "compiler",
        "build_profile",
        "target",
        "wheel_hash",
        "thread_mode",
        "simd_path",
    )
    provenance = {name: provenance_reader.string(name) for name in provenance_names}
    provenance["thread_count"] = provenance_reader.u16("thread count")
    provenance_reader.finish()
    root_evidence = _decode_record_section(
        sections[RESULT_ROOT_EVIDENCE_TAG].payload,
        tag=RESULT_ROOT_EVIDENCE_TAG,
        name="root evidence",
    )
    representatives = _decode_record_section(
        sections[RESULT_REPRESENTATIVES_TAG].payload,
        tag=RESULT_REPRESENTATIVES_TAG,
        name="representatives",
    )
    diagnostics = _decode_record_section(
        sections[RESULT_DIAGNOSTICS_TAG].payload,
        tag=RESULT_DIAGNOSTICS_TAG,
        name="diagnostics",
    )
    return NativeDecisionResult(
        request_id=envelope.request_id,
        selected_action=selected_action,
        ranked_root_actions=ranked_root_actions,
        search_complete=bool(search_complete),
        budget_exhausted=bool(budget_exhausted),
        deterministic_digest=deterministic_digest,
        counters=counters,
        root_evidence=root_evidence,
        representatives=representatives,
        diagnostics=diagnostics,
        provenance=provenance,
    )


def decode_response(payload: bytes | bytearray | memoryview) -> NativeDecisionResult:
    envelope = decode_envelope(
        payload,
        known_tags=_RESULT_TAGS | frozenset({ERROR_DETAILS_TAG}),
        maximum_bytes=_MAX_RESPONSE_BYTES,
    )
    if envelope.kind == EnvelopeKind.ERROR:
        raise _decode_error(envelope)
    if envelope.kind != EnvelopeKind.SUCCESS:
        raise InvalidNativeInputError("expected a success or error response")
    return _decode_result(envelope)


@runtime_checkable
class DeepChainDecisionBackend(Protocol):
    @property
    def capabilities(self) -> NativeCapabilities | None: ...

    def decide(self, request: NativeDecisionRequest) -> NativeDecisionResult: ...


class NativeDeepChainBackend:
    """Strict adapter for the one-call native decision boundary."""

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
        try:
            raw_capabilities = module.capabilities()
        except Exception as exc:
            raise NativeBackendUnavailableError(
                f"native capabilities call failed: {exc}", retry_safe=True
            ) from exc
        capabilities = decode_capabilities(raw_capabilities)
        self._validate_capabilities(capabilities, canonical=canonical)
        self._module = module
        self._capabilities = capabilities
        self._canonical = bool(canonical)

    @property
    def capabilities(self) -> NativeCapabilities:
        return self._capabilities

    @staticmethod
    def _validate_capabilities(
        capabilities: NativeCapabilities,
        *,
        canonical: bool,
    ) -> None:
        expected_python_abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
        mismatches = []
        if capabilities.abi_version != ABI_VERSION:
            mismatches.append("ABI version")
        if capabilities.schema_major != SCHEMA_MAJOR:
            mismatches.append("schema major")
        if not (
            capabilities.schema_minor_min
            <= SCHEMA_MINOR
            <= capabilities.schema_minor_max
        ):
            mismatches.append("schema minor")
        if capabilities.wire_name != WIRE_NAME:
            mismatches.append("wire name")
        if capabilities.request_schema_digest != REQUEST_SCHEMA_DIGEST:
            mismatches.append("request schema digest")
        if capabilities.result_schema_digest != RESULT_SCHEMA_DIGEST:
            mismatches.append("result schema digest")
        if capabilities.python_abi != expected_python_abi:
            mismatches.append("Python ABI")
        if not capabilities.scalar_fallback:
            mismatches.append("scalar fallback")
        if not capabilities.gil_detach:
            mismatches.append("GIL detach")
        if not capabilities.wheel_hash_hook:
            mismatches.append("wheel hash hook")
        if "oracle-1" not in capabilities.thread_modes:
            mismatches.append("oracle-1 thread mode")
        if mismatches:
            raise IncompatibleSchemaError(
                "native capabilities mismatch: " + ", ".join(mismatches),
                retry_safe=True,
                provenance=capabilities.to_dict(),
            )
        if canonical and capabilities.build_profile != "release":
            raise NativeBackendUnavailableError(
                "canonical native execution requires a release build",
                retry_safe=True,
                provenance={
                    "source_revision": capabilities.source_revision,
                    "build_profile": capabilities.build_profile,
                },
            )

    def decide(self, request: NativeDecisionRequest) -> NativeDecisionResult:
        encoded = encode_request(request)
        if len(encoded) > self.capabilities.max_request_bytes:
            raise InvalidNativeInputError("request exceeds native capability limit")
        try:
            response = self._module.decide(encoded)
        except Exception as exc:
            raise NativeInternalPanicError(
                f"native decide call raised outside its error envelope: {exc}",
                provenance={
                    "source_revision": self.capabilities.source_revision,
                    "build_profile": self.capabilities.build_profile,
                },
            ) from exc
        result = decode_response(response)
        if result.request_id != request.request_id:
            raise IncompatibleSchemaError("native response request_id does not match")
        return result

    def round_trip_request(
        self, request: NativeDecisionRequest
    ) -> NativeDecisionRequest:
        """Exercise the native codec validator; intended for boundary QA only."""

        encoded = encode_request(request)
        try:
            returned = self._module._round_trip_request(encoded)
        except Exception as exc:
            raise NativeInternalPanicError(
                f"native request round-trip failed: {exc}"
            ) from exc
        if bytes(returned) != encoded:
            raise IncompatibleSchemaError("native round-trip changed canonical bytes")
        return decode_request(returned)


def native_fallback_allowed(
    mode: str,
    error: DeepChainNativeError,
    *,
    canonical: bool,
) -> bool:
    """Return the explicit PUYO-198 fallback decision without performing it."""

    if mode not in {"python", "native", "auto"}:
        raise ValueError(f"unsupported backend mode: {mode}")
    if canonical or mode != "auto":
        return False
    return error.code in {
        NativeErrorCode.BACKEND_UNAVAILABLE,
        NativeErrorCode.INCOMPATIBLE_SCHEMA,
        NativeErrorCode.UNSUPPORTED_CONFIG,
        NativeErrorCode.RESOURCE_EXHAUSTED,
        NativeErrorCode.INTERNAL_PANIC,
    }


def request_sha256(request: NativeDecisionRequest) -> str:
    return hashlib.sha256(encode_request(request)).hexdigest()


__all__ = [
    "ABI_VERSION",
    "ACTION_LAYOUT_VERSION",
    "NATIVE_MODULE_NAME",
    "REQUEST_SCHEMA_DIGEST",
    "REQUEST_SCHEMA_VERSION",
    "RESULT_SCHEMA_DIGEST",
    "RESULT_SCHEMA_VERSION",
    "SCHEMA_MAJOR",
    "SCHEMA_MINOR",
    "WIRE_NAME",
    "DeepChainDecisionBackend",
    "DeepChainNativeError",
    "Envelope",
    "EnvelopeKind",
    "EnvelopeSection",
    "IncompatibleSchemaError",
    "InvalidNativeInputError",
    "NativeBackendUnavailableError",
    "NativeCapabilities",
    "NativeDecisionRequest",
    "NativeDecisionResult",
    "NativeDeepChainBackend",
    "NativeErrorCode",
    "NativeInternalPanicError",
    "NativeResourceExhaustedError",
    "UnsupportedNativeConfigError",
    "decode_capabilities",
    "decode_envelope",
    "decode_request",
    "decode_response",
    "encode_envelope",
    "encode_request",
    "native_fallback_allowed",
    "request_sha256",
]
