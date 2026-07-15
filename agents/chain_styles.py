"""Versioned, opt-in named chain-style contracts.

This module deliberately owns style-specific extension points.  Generic build
potential and beam scoring only consume the provider result and never name a
concrete style such as GTR.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

import yaml


CHAIN_STYLE_SCHEMA_VERSION = "puyo.chain_style.v1"
CHAIN_STYLE_REGISTRY_SCHEMA_VERSION = "puyo.chain_style_registry.v1"
CHAIN_STYLE_EVALUATION_SCHEMA_VERSION = "puyo.chain_style_evaluation.v1"
UNCONSTRAINED_STYLE_ID = "unconstrained"
UNCONSTRAINED_STYLE_VERSION = "1.0"
DEFAULT_CHAIN_STYLE_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "train" / "config" / "v1_7_chain_styles.yaml"
)
_CONSTRAINT_MODES = frozenset({"unconstrained", "soft_preference", "hard_constraint"})


@dataclass(frozen=True)
class ChainStyleSelection:
    style_id: str = UNCONSTRAINED_STYLE_ID
    style_version: str = UNCONSTRAINED_STYLE_VERSION
    constraint_mode: str = "unconstrained"
    weight: float = 0.0
    schema_version: str = CHAIN_STYLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CHAIN_STYLE_SCHEMA_VERSION:
            raise ValueError(f"unsupported chain-style schema: {self.schema_version}")
        if not self.style_id or not self.style_version:
            raise ValueError("chain style id and version are required")
        if self.constraint_mode not in _CONSTRAINT_MODES:
            raise ValueError(f"unsupported chain-style constraint mode: {self.constraint_mode}")
        if self.weight < 0.0:
            raise ValueError("chain-style weight must be non-negative")
        if self.style_id == UNCONSTRAINED_STYLE_ID:
            if self.constraint_mode != "unconstrained" or self.weight != 0.0:
                raise ValueError("unconstrained style must use unconstrained mode and zero weight")
        elif self.constraint_mode == "unconstrained":
            raise ValueError("a named style must use soft_preference or hard_constraint")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "ChainStyleSelection":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("chain_style must be a mapping")
        return cls(
            style_id=str(value.get("style_id", UNCONSTRAINED_STYLE_ID)),
            style_version=str(value.get("style_version", UNCONSTRAINED_STYLE_VERSION)),
            constraint_mode=str(value.get("constraint_mode", "unconstrained")),
            weight=float(value.get("weight", 0.0)),
            schema_version=str(value.get("schema_version", CHAIN_STYLE_SCHEMA_VERSION)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "style_id": self.style_id,
            "style_version": self.style_version,
            "constraint_mode": self.constraint_mode,
            "weight": float(self.weight),
        }


@dataclass(frozen=True)
class ChainStyleDefinition:
    style_id: str
    style_version: str
    provider_id: str
    deprecated: bool = False
    replacement_style_id: str = UNCONSTRAINED_STYLE_ID

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChainStyleDefinition":
        definition = cls(
            style_id=str(value.get("style_id", "")),
            style_version=str(value.get("style_version", "")),
            provider_id=str(value.get("provider_id", "")),
            deprecated=bool(value.get("deprecated", False)),
            replacement_style_id=str(value.get("replacement_style_id", UNCONSTRAINED_STYLE_ID)),
        )
        if not definition.style_id or not definition.style_version or not definition.provider_id:
            raise ValueError("chain-style definition requires id, version, and provider")
        return definition

    def to_dict(self) -> dict[str, Any]:
        return {
            "style_id": self.style_id,
            "style_version": self.style_version,
            "provider_id": self.provider_id,
            "deprecated": bool(self.deprecated),
            "replacement_style_id": self.replacement_style_id,
        }


@dataclass(frozen=True)
class ChainStyleProviderResult:
    applicable: bool
    adherence_score: float = 0.0
    hard_constraint_satisfied: bool = True
    diagnostics: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.adherence_score <= 1.0:
            raise ValueError("style adherence score must be in [0, 1]")


class ChainStyleProvider(Protocol):
    def evaluate(self, simulator: Any, definition: ChainStyleDefinition) -> ChainStyleProviderResult: ...


class _UnconstrainedProvider:
    def evaluate(self, simulator: Any, definition: ChainStyleDefinition) -> ChainStyleProviderResult:
        _ = simulator, definition
        return ChainStyleProviderResult(False, diagnostics={"status": "disabled"})


class _FixtureStubProvider:
    """Schema fixture only; it intentionally contains no style detector logic."""

    def evaluate(self, simulator: Any, definition: ChainStyleDefinition) -> ChainStyleProviderResult:
        _ = simulator
        return ChainStyleProviderResult(
            applicable=False,
            diagnostics={"status": "stub_provider", "style_id": definition.style_id},
        )


DEFAULT_CHAIN_STYLE_PROVIDERS: Mapping[str, ChainStyleProvider] = {
    "builtin.unconstrained.v1": _UnconstrainedProvider(),
    "fixture.named-style-stub.v1": _FixtureStubProvider(),
}


@dataclass(frozen=True)
class ChainStyleResolution:
    requested: ChainStyleSelection
    selected: ChainStyleSelection
    provider_id: str
    status: str
    diagnostic_code: str

    @property
    def enabled(self) -> bool:
        return self.selected.style_id != UNCONSTRAINED_STYLE_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CHAIN_STYLE_SCHEMA_VERSION,
            "requested": self.requested.to_dict(),
            "selected": self.selected.to_dict(),
            "provider_id": self.provider_id,
            "status": self.status,
            "diagnostic_code": self.diagnostic_code,
            "fallback_applied": self.requested != self.selected,
        }


@dataclass(frozen=True)
class ChainStyleEvaluation:
    resolution: ChainStyleResolution
    applicable: bool
    adherence_score: float
    hard_constraint_satisfied: bool
    score_contribution: float
    diagnostics: Mapping[str, Any]
    schema_version: str = CHAIN_STYLE_EVALUATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "metric_namespace": "style_adherence",
            "resolution": self.resolution.to_dict(),
            "applicable": bool(self.applicable),
            "adherence_score": float(self.adherence_score),
            "hard_constraint_satisfied": bool(self.hard_constraint_satisfied),
            "score_contribution": float(self.score_contribution),
            "diagnostics": _plain(self.diagnostics),
        }


@dataclass(frozen=True)
class ChainStyleRegistry:
    registry_version: str
    styles: tuple[ChainStyleDefinition, ...]
    schema_version: str = CHAIN_STYLE_REGISTRY_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChainStyleRegistry":
        registry = cls(
            schema_version=str(value.get("schema_version", "")),
            registry_version=str(value.get("registry_version", "")),
            styles=tuple(
                ChainStyleDefinition.from_dict(item)
                for item in value.get("styles", ())
                if isinstance(item, Mapping)
            ),
        )
        if registry.schema_version != CHAIN_STYLE_REGISTRY_SCHEMA_VERSION:
            raise ValueError(f"unsupported chain-style registry: {registry.schema_version}")
        if not registry.registry_version or not registry.styles:
            raise ValueError("chain-style registry version and styles are required")
        ids = [style.style_id for style in registry.styles]
        if len(ids) != len(set(ids)):
            raise ValueError("chain-style ids must be unique")
        if UNCONSTRAINED_STYLE_ID not in ids:
            raise ValueError("chain-style registry must define unconstrained")
        return registry

    def definition(self, style_id: str) -> ChainStyleDefinition | None:
        return next((style for style in self.styles if style.style_id == style_id), None)

    def resolve(
        self,
        requested: ChainStyleSelection | Mapping[str, Any] | None,
        providers: Mapping[str, ChainStyleProvider] | None = None,
    ) -> ChainStyleResolution:
        selection = (
            requested
            if isinstance(requested, ChainStyleSelection)
            else ChainStyleSelection.from_dict(requested)
        )
        available = {**DEFAULT_CHAIN_STYLE_PROVIDERS, **dict(providers or {})}
        definition = self.definition(selection.style_id)
        diagnostic = "selected"
        if definition is None:
            diagnostic = "unknown_style"
        elif definition.style_version != selection.style_version:
            diagnostic = "version_mismatch"
        elif definition.deprecated:
            diagnostic = "deprecated_style"
        elif definition.provider_id not in available:
            diagnostic = "missing_provider"
        if diagnostic != "selected":
            fallback = ChainStyleSelection()
            fallback_definition = self.definition(UNCONSTRAINED_STYLE_ID)
            assert fallback_definition is not None
            return ChainStyleResolution(
                requested=selection,
                selected=fallback,
                provider_id=fallback_definition.provider_id,
                status="fallback_unconstrained",
                diagnostic_code=diagnostic,
            )
        assert definition is not None
        return ChainStyleResolution(
            requested=selection,
            selected=selection,
            provider_id=definition.provider_id,
            status=(
                "unconstrained"
                if selection.style_id == UNCONSTRAINED_STYLE_ID
                else "selected"
            ),
            diagnostic_code="none",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_version": self.registry_version,
            "styles": [style.to_dict() for style in self.styles],
        }


class ChainStyleEvaluator:
    """Provider-backed scorer suitable for injection into generic search."""

    def __init__(
        self,
        registry: ChainStyleRegistry,
        selection: ChainStyleSelection | Mapping[str, Any] | None,
        *,
        providers: Mapping[str, ChainStyleProvider] | None = None,
        contribution_scale: float = 1.0,
    ) -> None:
        self.registry = registry
        self.providers = {**DEFAULT_CHAIN_STYLE_PROVIDERS, **dict(providers or {})}
        self.resolution = registry.resolve(selection, self.providers)
        self.contribution_scale = max(0.0, float(contribution_scale))

    def evaluate(self, simulator: Any) -> ChainStyleEvaluation:
        provider = self.providers[self.resolution.provider_id]
        definition = self.registry.definition(self.resolution.selected.style_id)
        assert definition is not None
        result = provider.evaluate(simulator, definition)
        selection = self.resolution.selected
        contribution = (
            self.contribution_scale * selection.weight * result.adherence_score
            if result.applicable and selection.constraint_mode == "soft_preference"
            else 0.0
        )
        return ChainStyleEvaluation(
            resolution=self.resolution,
            applicable=result.applicable,
            adherence_score=result.adherence_score,
            hard_constraint_satisfied=(
                result.hard_constraint_satisfied
                if result.applicable and selection.constraint_mode == "hard_constraint"
                else True
            ),
            score_contribution=contribution,
            diagnostics=dict(result.diagnostics or {}),
        )


def load_chain_style_registry(
    path: str | Path = DEFAULT_CHAIN_STYLE_REGISTRY_PATH,
) -> ChainStyleRegistry:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise ValueError("chain-style registry must be a mapping")
    return ChainStyleRegistry.from_dict(payload)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
