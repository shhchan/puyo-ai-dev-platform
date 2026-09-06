"""Decision-level backends for the deep-chain long-horizon search.

The public policy consumes :class:`LongHorizonSearchResult` regardless of the
selected implementation.  Native execution crosses the extension boundary
exactly once, then validates and materializes only the bounded result records.
"""

from __future__ import annotations

import hashlib
import json
import platform
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from agents.chain_structure import ChainStructureConfig, ChainStructureEvaluator
from agents.compact_search import CompactSearchState
from agents.deep_chain_native import (
    DeepChainNativeError,
    NativeDecisionRequest,
    NativeDeepChainBackend,
    native_fallback_allowed,
)
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from agents.long_horizon_search import (
    LongHorizonSearchConfig,
    LongHorizonSearchResult,
    run_compact_long_horizon_search,
)
from src.core.constants import PuyoColor

LONG_HORIZON_BACKEND_CONFIG_SCHEMA_VERSION = "puyo.deep_chain_builder.backend_config.v1"
LONG_HORIZON_BACKEND_DIAGNOSTICS_SCHEMA_VERSION = (
    "puyo.deep_chain_builder.backend_diagnostics.v1"
)
LONG_HORIZON_BACKEND_RESULT_CONTRACT = "puyo.long_horizon_search_result.v1"
LONG_HORIZON_BACKEND_CHOICES = ("python", "native", "auto")
NATIVE_EXECUTION_MODES = ("oracle-1", "scenario-6")
DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "train" / "config" / "deep_chain_backend.yaml"
)


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def semantic_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LongHorizonBackendConfig:
    """Versioned routing and fail-closed settings for policy integration."""

    config_version: str
    default_backend: str
    canonical_backend: str
    canonical_profiles: tuple[str, ...]
    auto_fallback_profiles: tuple[str, ...]
    native_execution_mode: str
    max_response_bytes: int
    schema_version: str = LONG_HORIZON_BACKEND_CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LONG_HORIZON_BACKEND_CONFIG_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported deep-chain backend config schema: {self.schema_version}"
            )
        if not self.config_version:
            raise ValueError("deep-chain backend config version is required")
        if self.default_backend not in LONG_HORIZON_BACKEND_CHOICES:
            raise ValueError(f"unsupported default backend: {self.default_backend}")
        if self.canonical_backend != "native":
            raise ValueError("canonical deep-chain backend must be native")
        if not self.canonical_profiles:
            raise ValueError("at least one canonical profile is required")
        if set(self.auto_fallback_profiles) & set(self.canonical_profiles):
            raise ValueError("canonical profiles cannot allow automatic fallback")
        if self.native_execution_mode not in NATIVE_EXECUTION_MODES:
            raise ValueError(
                f"unsupported native execution mode: {self.native_execution_mode}"
            )
        if not 0 < self.max_response_bytes <= 16 * 1024 * 1024:
            raise ValueError("native maximum response bytes must be in (0, 16 MiB]")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LongHorizonBackendConfig:
        canonical = value.get("canonical", {})
        native = value.get("native", {})
        auto = value.get("auto", {})
        if not all(isinstance(item, Mapping) for item in (canonical, native, auto)):
            raise TypeError("backend canonical, native, and auto sections are required")
        return cls(
            schema_version=str(value.get("schema_version", "")),
            config_version=str(value.get("config_version", "")),
            default_backend=str(value.get("default_backend", "")),
            canonical_backend=str(canonical.get("required_backend", "")),
            canonical_profiles=tuple(
                str(item) for item in canonical.get("profiles", ())
            ),
            auto_fallback_profiles=tuple(
                str(item) for item in auto.get("fallback_profiles", ())
            ),
            native_execution_mode=str(native.get("execution_mode", "")),
            max_response_bytes=int(native.get("max_response_bytes", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "config_version": self.config_version,
            "default_backend": self.default_backend,
            "canonical": {
                "required_backend": self.canonical_backend,
                "profiles": list(self.canonical_profiles),
            },
            "native": {
                "execution_mode": self.native_execution_mode,
                "max_response_bytes": int(self.max_response_bytes),
            },
            "auto": {"fallback_profiles": list(self.auto_fallback_profiles)},
        }

    def is_canonical_profile(self, profile_name: str) -> bool:
        return str(profile_name) in self.canonical_profiles

    def allows_auto_fallback(self, profile_name: str) -> bool:
        return str(profile_name) in self.auto_fallback_profiles


def load_long_horizon_backend_config(
    path: str | Path = DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH,
) -> LongHorizonBackendConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise TypeError("deep-chain backend config must be a mapping")
    return LongHorizonBackendConfig.from_dict(payload)


@dataclass(frozen=True, slots=True)
class LongHorizonBackendRequest:
    root_state: CompactSearchState
    known_pairs: tuple[tuple[PuyoColor, PuyoColor], ...]
    search_config: LongHorizonSearchConfig
    evaluator_config: ChainStructureConfig
    profile_name: str
    profile_version: str
    search_config_version: str
    search_config_sha256: str
    evaluator_config_version: str
    evaluator_config_sha256: str
    backend_config_version: str
    backend_config_sha256: str
    request_id: int
    canonical: bool
    allow_auto_fallback: bool

    def __post_init__(self) -> None:
        pairs = tuple(tuple(pair) for pair in self.known_pairs)
        if not pairs or any(len(pair) != 2 for pair in pairs):
            raise ValueError("long-horizon backend requires visible color pairs")
        for name, digest in (
            ("search config", self.search_config_sha256),
            ("evaluator config", self.evaluator_config_sha256),
            ("backend config", self.backend_config_sha256),
        ):
            try:
                decoded = bytes.fromhex(str(digest))
            except ValueError as exc:
                raise ValueError(f"{name} digest must be SHA-256 hex") from exc
            if len(decoded) != 32:
                raise ValueError(f"{name} digest must be SHA-256 hex")
        if not 0 <= int(self.request_id) <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("backend request ID is outside the u64 range")
        object.__setattr__(self, "known_pairs", pairs)


@dataclass(frozen=True, slots=True)
class LongHorizonBackendExecution:
    result: LongHorizonSearchResult
    diagnostics: Mapping[str, Any]


@runtime_checkable
class LongHorizonSearchBackend(Protocol):
    @property
    def backend_id(self) -> str: ...

    def describe(self) -> Mapping[str, Any]: ...

    def search(
        self, request: LongHorizonBackendRequest
    ) -> LongHorizonBackendExecution: ...


def _base_diagnostics(
    request: LongHorizonBackendRequest,
    *,
    requested_backend: str,
    resolved_backend: str,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": LONG_HORIZON_BACKEND_DIAGNOSTICS_SCHEMA_VERSION,
        "result_contract": LONG_HORIZON_BACKEND_RESULT_CONTRACT,
        "requested_backend": requested_backend,
        "backend": resolved_backend,
        "canonical": bool(request.canonical),
        "request_id": int(request.request_id),
        "profile": {
            "name": request.profile_name,
            "version": request.profile_version,
        },
        "configuration": {
            "search_config_version": request.search_config_version,
            "search_config_sha256": request.search_config_sha256,
            "minimum_chain_count": int(
                request.search_config.minimum_chain_count
            ),
            "evaluator_config_version": request.evaluator_config_version,
            "evaluator_config_sha256": request.evaluator_config_sha256,
            "backend_config_version": request.backend_config_version,
            "backend_config_sha256": request.backend_config_sha256,
        },
        "fallback": dict(fallback or {"used": False, "reason": None, "detail": ""}),
    }


def _error_diagnostics(error: DeepChainNativeError) -> dict[str, Any]:
    return {
        "type": type(error).__name__,
        "code": error.code.name.lower(),
        "detail": str(error),
        "retry_safe": bool(error.retry_safe),
        "provenance": dict(error.provenance),
    }


class PythonLongHorizonSearchBackend:
    """Existing deterministic Python implementation behind the shared contract."""

    backend_id = "python"

    def describe(self) -> Mapping[str, Any]:
        return {
            "schema_version": LONG_HORIZON_BACKEND_DIAGNOSTICS_SCHEMA_VERSION,
            "requested_backend": "python",
            "backend": "python",
            "fallback": {"used": False, "reason": None, "detail": ""},
            "provenance": {
                "implementation": (
                    "agents.long_horizon_search.run_compact_long_horizon_search"
                ),
                "python_version": platform.python_version(),
                "thread_count": 1,
            },
        }

    def search(self, request: LongHorizonBackendRequest) -> LongHorizonBackendExecution:
        started = time.perf_counter_ns()
        result = run_compact_long_horizon_search(
            request.root_state,
            request.known_pairs,
            request.search_config,
            evaluator=ChainStructureEvaluator(request.evaluator_config),
        )
        total_ns = time.perf_counter_ns() - started
        diagnostics = _base_diagnostics(
            request,
            requested_backend="python",
            resolved_backend="python",
        )
        diagnostics.update(
            {
                "provenance": {
                    "implementation": (
                        "agents.long_horizon_search.run_compact_long_horizon_search"
                    ),
                    "python_version": platform.python_version(),
                    "thread_count": 1,
                },
                "timing": {
                    "compute_ns": int(total_ns),
                    "serialization_ns": 0,
                    "materialization_ns": 0,
                    "total_ns": int(total_ns),
                    "total_seconds": float(total_ns / 1_000_000_000.0),
                },
                "telemetry": {},
                "boundary_call_count": 0,
                "counters": result.counters.to_dict(),
                "search_complete": not bool(result.counters.budget_exhausted),
            }
        )
        return LongHorizonBackendExecution(result=result, diagnostics=diagnostics)


class NativeLongHorizonSearchBackend:
    """Strict one-call adapter around the release native search extension."""

    backend_id = "native"

    def __init__(
        self,
        *,
        execution_mode: str = "scenario-6",
        max_response_bytes: int = 16 * 1024 * 1024,
        canonical: bool = True,
        native_backend: NativeDeepChainBackend | None = None,
    ) -> None:
        if execution_mode not in NATIVE_EXECUTION_MODES:
            raise ValueError(f"unsupported native execution mode: {execution_mode}")
        self.execution_mode = execution_mode
        self.max_response_bytes = int(max_response_bytes)
        self.canonical = bool(canonical)
        self._native_backend = native_backend or NativeDeepChainBackend(
            canonical=self.canonical
        )
        self._capabilities = self._native_backend.capabilities.to_dict()

    def __getstate__(self) -> dict[str, Any]:
        # Extension modules are not pickleable. Recreate the validated adapter in
        # the dedicated realtime policy process on its first decision.
        return {
            "execution_mode": self.execution_mode,
            "max_response_bytes": self.max_response_bytes,
            "canonical": self.canonical,
            "_native_backend": None,
            "_capabilities": self._capabilities,
        }

    def __setstate__(self, state: Mapping[str, Any]) -> None:
        self.execution_mode = str(state["execution_mode"])
        self.max_response_bytes = int(state["max_response_bytes"])
        self.canonical = bool(state["canonical"])
        self._native_backend = None
        self._capabilities = dict(state["_capabilities"])

    def _client(self) -> NativeDeepChainBackend:
        if self._native_backend is None:
            self._native_backend = NativeDeepChainBackend(canonical=self.canonical)
            self._capabilities = self._native_backend.capabilities.to_dict()
        return self._native_backend

    def describe(self) -> Mapping[str, Any]:
        return {
            "schema_version": LONG_HORIZON_BACKEND_DIAGNOSTICS_SCHEMA_VERSION,
            "requested_backend": "native",
            "backend": "native",
            "fallback": {"used": False, "reason": None, "detail": ""},
            "execution_mode": self.execution_mode,
            "capabilities": dict(self._capabilities),
        }

    def search(self, request: LongHorizonBackendRequest) -> LongHorizonBackendExecution:
        native_request = NativeDecisionRequest(
            state=request.root_state,
            known_pairs=request.known_pairs,
            search_config=request.search_config,
            evaluator_config=request.evaluator_config,
            config_digest=request.search_config_sha256,
            profile_name=request.profile_name,
            profile_version=request.profile_version,
            config_version=request.search_config_version,
            request_id=request.request_id,
            execution_mode=self.execution_mode,
            max_response_bytes=self.max_response_bytes,
        )
        total_started = time.perf_counter_ns()
        call_started = time.perf_counter_ns()
        native_result = self._client().decide(native_request)
        boundary_call_ns = time.perf_counter_ns() - call_started
        materialization_started = time.perf_counter_ns()
        result = materialize_native_long_horizon_result(
            native_result,
            native_request,
        )
        materialization_ns = time.perf_counter_ns() - materialization_started
        total_ns = time.perf_counter_ns() - total_started
        telemetry = {
            str(key): int(value) for key, value in native_result.telemetry.items()
        }
        native_search_ns = int(telemetry.get("search_ns", 0))
        native_aggregation_ns = int(telemetry.get("aggregation_ns", 0))
        native_serialization_ns = int(telemetry.get("serialization_ns", 0))
        diagnostics = _base_diagnostics(
            request,
            requested_backend="native",
            resolved_backend="native",
        )
        diagnostics.update(
            {
                "execution_mode": self.execution_mode,
                "capabilities": dict(self._capabilities),
                "provenance": dict(native_result.provenance),
                "timing": {
                    "native_search_ns": native_search_ns,
                    "native_aggregation_ns": native_aggregation_ns,
                    "native_compute_ns": native_search_ns + native_aggregation_ns,
                    "native_serialization_ns": native_serialization_ns,
                    "native_total_ns": (
                        native_search_ns
                        + native_aggregation_ns
                        + native_serialization_ns
                    ),
                    "boundary_call_ns": int(boundary_call_ns),
                    "materialization_ns": int(materialization_ns),
                    "total_ns": int(total_ns),
                    "total_seconds": float(total_ns / 1_000_000_000.0),
                },
                "telemetry": telemetry,
                "boundary_call_count": 1,
                "counters": result.counters.to_dict(),
                "native_result_digest": native_result.deterministic_digest,
                "search_complete": bool(native_result.search_complete),
                "budget_exhausted": bool(native_result.budget_exhausted),
                "record_counts": dict(native_result.record_counts),
            }
        )
        return LongHorizonBackendExecution(result=result, diagnostics=diagnostics)


class AutoLongHorizonSearchBackend:
    """Explicit non-canonical native-to-Python rollback router."""

    backend_id = "auto"

    def __init__(
        self,
        native_backend: NativeLongHorizonSearchBackend | None,
        *,
        initialization_error: DeepChainNativeError | None = None,
    ) -> None:
        self.native_backend = native_backend
        self.initialization_error = initialization_error
        self.python_backend = PythonLongHorizonSearchBackend()

    def describe(self) -> Mapping[str, Any]:
        native = (
            {} if self.native_backend is None else dict(self.native_backend.describe())
        )
        return {
            "schema_version": LONG_HORIZON_BACKEND_DIAGNOSTICS_SCHEMA_VERSION,
            "requested_backend": "auto",
            "backend": "native" if self.native_backend is not None else "python",
            "fallback": {
                "used": self.native_backend is None,
                "reason": (
                    None
                    if self.initialization_error is None
                    else self.initialization_error.code.name.lower()
                ),
                "detail": (
                    ""
                    if self.initialization_error is None
                    else str(self.initialization_error)
                ),
            },
            "native": native,
        }

    def _python_fallback(
        self,
        request: LongHorizonBackendRequest,
        error: DeepChainNativeError,
    ) -> LongHorizonBackendExecution:
        if not request.allow_auto_fallback or not native_fallback_allowed(
            "auto", error, canonical=request.canonical
        ):
            raise error
        execution = self.python_backend.search(request)
        diagnostics = dict(execution.diagnostics)
        diagnostics.update(
            {
                "requested_backend": "auto",
                "backend": "python",
                "fallback": {
                    "used": True,
                    "reason": error.code.name.lower(),
                    "detail": str(error),
                },
                "native_error": _error_diagnostics(error),
            }
        )
        return LongHorizonBackendExecution(
            result=execution.result,
            diagnostics=diagnostics,
        )

    def search(self, request: LongHorizonBackendRequest) -> LongHorizonBackendExecution:
        if self.native_backend is None:
            if self.initialization_error is None:
                raise RuntimeError("auto backend has no native adapter or error")
            return self._python_fallback(request, self.initialization_error)
        try:
            execution = self.native_backend.search(request)
        except DeepChainNativeError as error:
            return self._python_fallback(request, error)
        diagnostics = dict(execution.diagnostics)
        diagnostics["requested_backend"] = "auto"
        return LongHorizonBackendExecution(
            result=execution.result,
            diagnostics=diagnostics,
        )


def make_long_horizon_search_backend(
    mode: str,
    *,
    profile_name: str,
    config: LongHorizonBackendConfig,
    native_backend: NativeDeepChainBackend | None = None,
) -> LongHorizonSearchBackend:
    selected = str(mode)
    if selected not in LONG_HORIZON_BACKEND_CHOICES:
        raise ValueError(f"unsupported deep-chain backend: {selected}")
    if selected == "python":
        return PythonLongHorizonSearchBackend()
    canonical = selected == "native" or config.is_canonical_profile(profile_name)
    try:
        native = NativeLongHorizonSearchBackend(
            execution_mode=config.native_execution_mode,
            max_response_bytes=config.max_response_bytes,
            canonical=canonical,
            native_backend=native_backend,
        )
    except DeepChainNativeError as error:
        if (
            selected == "auto"
            and config.allows_auto_fallback(profile_name)
            and native_fallback_allowed("auto", error, canonical=canonical)
        ):
            return AutoLongHorizonSearchBackend(
                None,
                initialization_error=error,
            )
        raise
    if selected == "native":
        return native
    return AutoLongHorizonSearchBackend(native)


__all__ = [
    "DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH",
    "LONG_HORIZON_BACKEND_CHOICES",
    "LONG_HORIZON_BACKEND_CONFIG_SCHEMA_VERSION",
    "LONG_HORIZON_BACKEND_DIAGNOSTICS_SCHEMA_VERSION",
    "LONG_HORIZON_BACKEND_RESULT_CONTRACT",
    "AutoLongHorizonSearchBackend",
    "LongHorizonBackendConfig",
    "LongHorizonBackendExecution",
    "LongHorizonBackendRequest",
    "LongHorizonSearchBackend",
    "NativeLongHorizonSearchBackend",
    "PythonLongHorizonSearchBackend",
    "file_sha256",
    "load_long_horizon_backend_config",
    "make_long_horizon_search_backend",
    "semantic_sha256",
]
