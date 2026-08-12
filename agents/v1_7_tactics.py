"""Versioned TacticSpec registry for the v1.7 analyzer-driven manager."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from agents.chain_styles import (
    CHAIN_STYLE_SCHEMA_VERSION,
    ChainStyleSelection,
)
from agents.state_analyzer import (
    ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
    ANALYZER_INPUT_SCHEMA_VERSION,
    AnalyzerDiagnostics,
    AnalyzerInput,
)


TACTIC_SCHEMA_VERSION = "tactic-schema-v3"
LEGACY_TACTIC_SCHEMA_VERSION = "tactic-schema-v2"
TACTIC_DIAGNOSTICS_SCHEMA_VERSION = "puyo.tactic_registry.diagnostics.v1"
TACTIC_ARTIFACT_SCHEMA_VERSION = "puyo.tactic_registry.artifact.v1"
DEFAULT_TACTIC_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "train" / "config" / "v1_7_tactic_registry.yaml"
)
_PARAMETER_KINDS = frozenset({"continuous", "integer", "discrete"})
_CONDITION_OPERATORS = frozenset({"eq", "ne", "gt", "ge", "lt", "le", "in"})
_REQUIRED_TACTIC_SECTIONS = (
    "applicability",
    "objective",
    "constraints",
    "planner",
    "termination",
    "fallback",
    "diagnostics",
)


@dataclass(frozen=True)
class ParameterSpec:
    """One tunable continuous, integer, or discrete tactic parameter."""

    name: str
    kind: str
    default: Any
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[Any, ...] = ()

    @classmethod
    def from_dict(cls, name: str, value: Mapping[str, Any]) -> "ParameterSpec":
        kind = str(value.get("kind", ""))
        if kind not in _PARAMETER_KINDS:
            raise ValueError(f"parameter {name} has unsupported kind: {kind}")
        if "default" not in value:
            raise ValueError(f"parameter {name} must define a default")
        minimum = value.get("min")
        maximum = value.get("max")
        choices = tuple(value.get("choices", ()))
        default = value["default"]
        if kind == "continuous":
            _validate_number(name, default)
            if minimum is not None:
                _validate_number(name, minimum)
            if maximum is not None:
                _validate_number(name, maximum)
        elif kind == "integer":
            if not isinstance(default, int) or isinstance(default, bool):
                raise ValueError(f"parameter {name} default must be an integer")
            if minimum is not None and (not isinstance(minimum, int) or isinstance(minimum, bool)):
                raise ValueError(f"parameter {name} min must be an integer")
            if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool)):
                raise ValueError(f"parameter {name} max must be an integer")
        elif not choices or default not in choices:
            raise ValueError(f"discrete parameter {name} must include its default in choices")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"parameter {name} min exceeds max")
        parameter = cls(name, kind, default, minimum, maximum, choices)
        parameter.validate(default)
        return parameter

    def validate(self, value: Any) -> None:
        if self.kind == "continuous":
            _validate_number(self.name, value)
        elif self.kind == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"parameter {self.name} must be an integer")
        elif value not in self.choices:
            raise ValueError(f"parameter {self.name} must be one of {self.choices}")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"parameter {self.name} is below min")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"parameter {self.name} is above max")

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"kind": self.kind, "default": self.default}
        if self.minimum is not None:
            value["min"] = self.minimum
        if self.maximum is not None:
            value["max"] = self.maximum
        if self.choices:
            value["choices"] = list(self.choices)
        return value


@dataclass(frozen=True)
class ConditionSpec:
    """Declarative candidate condition; it does not select an action or tactic."""

    ref: str
    op: str
    value: Any

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConditionSpec":
        ref = str(value.get("ref", ""))
        op = str(value.get("op", "eq"))
        if not ref.startswith(("input.", "diagnostics.")):
            raise ValueError(f"condition ref must use a versioned Analyzer root: {ref}")
        if op not in _CONDITION_OPERATORS:
            raise ValueError(f"unsupported condition operator: {op}")
        if "value" not in value:
            raise ValueError(f"condition {ref} must define a value")
        return cls(ref=ref, op=op, value=value["value"])

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "op": self.op, "value": self.value}


@dataclass(frozen=True)
class ConditionGroup:
    conditions: tuple[ConditionSpec, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConditionGroup":
        raw = value.get("all")
        if not isinstance(raw, list) or not raw:
            raise ValueError("candidate condition group must contain a non-empty all list")
        return cls(tuple(ConditionSpec.from_dict(_mapping(item, "condition")) for item in raw))

    def to_dict(self) -> dict[str, Any]:
        return {"all": [condition.to_dict() for condition in self.conditions]}


@dataclass(frozen=True)
class ContextSpec:
    context_id: str
    conditions: tuple[ConditionSpec, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextSpec":
        context_id = str(value.get("id", ""))
        raw = value.get("when")
        if not context_id or not isinstance(raw, list) or not raw:
            raise ValueError("context must define id and a non-empty when list")
        return cls(
            context_id=context_id,
            conditions=tuple(ConditionSpec.from_dict(_mapping(item, "context condition")) for item in raw),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.context_id, "when": [condition.to_dict() for condition in self.conditions]}


@dataclass(frozen=True)
class TacticIdentity:
    tactic_id: str
    name: str
    version: str
    human_label: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TacticIdentity":
        identity = cls(
            tactic_id=str(value.get("id", "")),
            name=str(value.get("name", "")),
            version=str(value.get("version", "")),
            human_label=str(value.get("human_label", "")),
        )
        if not identity.tactic_id or not identity.name or not identity.version:
            raise ValueError("tactic identity must define id, name, and version")
        return identity

    def to_dict(self) -> dict[str, str]:
        value = {"id": self.tactic_id, "name": self.name, "version": self.version}
        if self.human_label:
            value["human_label"] = self.human_label
        return value


@dataclass(frozen=True)
class TacticSpec:
    """Serializable tactic candidate schema, independent of final arbitration."""

    identity: TacticIdentity
    applicability: Mapping[str, Any]
    objective: Mapping[str, Any]
    constraints: Mapping[str, Any]
    planner: Mapping[str, Any]
    termination: Mapping[str, Any]
    fallback: Mapping[str, Any]
    diagnostics: Mapping[str, Any]
    candidate_groups: tuple[ConditionGroup, ...]
    contexts: tuple[ContextSpec, ...]
    parameters: Mapping[str, Mapping[str, ParameterSpec]]
    chain_style: ChainStyleSelection = ChainStyleSelection()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TacticSpec":
        missing = [section for section in ("identity", *_REQUIRED_TACTIC_SECTIONS) if section not in value]
        if missing:
            raise ValueError(f"tactic is missing sections: {', '.join(missing)}")
        sections = {name: _mapping(value[name], name) for name in _REQUIRED_TACTIC_SECTIONS}
        applicability = sections["applicability"]
        candidate_groups = tuple(
            ConditionGroup.from_dict(_mapping(item, "candidate_when"))
            for item in applicability.get("candidate_when", ())
        )
        contexts = tuple(
            ContextSpec.from_dict(_mapping(item, "context"))
            for item in applicability.get("contexts", ())
        )
        feature_refs = applicability.get("feature_refs", ())
        if not isinstance(feature_refs, list) or not feature_refs:
            raise ValueError("applicability.feature_refs must be a non-empty list")
        _validate_refs(feature_refs)
        parameters: dict[str, Mapping[str, ParameterSpec]] = {}
        for section_name in ("objective", "constraints", "planner"):
            section = sections[section_name]
            _validate_refs(section.get("input_refs", ()))
            raw_parameters = _mapping(section.get("parameters", {}), f"{section_name}.parameters")
            parameters[section_name] = {
                str(name): ParameterSpec.from_dict(str(name), _mapping(spec, f"parameter {name}"))
                for name, spec in raw_parameters.items()
            }
        if not any(parameters.values()):
            raise ValueError("tactic must define at least one tunable parameter")
        termination = sections["termination"]
        if not termination.get("criteria"):
            raise ValueError("termination.criteria must be non-empty")
        fallback = sections["fallback"]
        if not fallback.get("tactic_id") and not fallback.get("safety_behavior"):
            raise ValueError("fallback must define tactic_id or safety_behavior")
        if not sections["diagnostics"].get("fields"):
            raise ValueError("diagnostics.fields must be non-empty")
        return cls(
            identity=TacticIdentity.from_dict(_mapping(value["identity"], "identity")),
            applicability=_plain(applicability),
            objective=_plain(sections["objective"]),
            constraints=_plain(sections["constraints"]),
            planner=_plain(sections["planner"]),
            termination=_plain(termination),
            fallback=_plain(fallback),
            diagnostics=_plain(sections["diagnostics"]),
            candidate_groups=candidate_groups,
            contexts=contexts,
            parameters=parameters,
            chain_style=ChainStyleSelection.from_dict(value.get("chain_style")),
        )

    @property
    def parameter_defaults(self) -> dict[str, dict[str, Any]]:
        return {
            section: {name: spec.default for name, spec in values.items()}
            for section, values in self.parameters.items()
        }

    def resolve_parameters(
        self,
        overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        values = self.parameter_defaults
        for section, section_overrides in (overrides or {}).items():
            if section not in self.parameters:
                raise ValueError(f"unknown parameter section for {self.identity.tactic_id}: {section}")
            if not isinstance(section_overrides, Mapping):
                raise ValueError(f"parameter overrides for {section} must be a mapping")
            for name, value in section_overrides.items():
                if name not in self.parameters[section]:
                    raise ValueError(f"unknown parameter for {self.identity.tactic_id}: {section}.{name}")
                self.parameters[section][name].validate(value)
                values[section][name] = value
        return values

    @property
    def input_refs(self) -> tuple[str, ...]:
        refs = list(self.applicability.get("feature_refs", ()))
        refs.extend(self.objective.get("input_refs", ()))
        refs.extend(self.constraints.get("input_refs", ()))
        refs.extend(self.planner.get("input_refs", ()))
        return tuple(dict.fromkeys(str(ref) for ref in refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "applicability": _plain(self.applicability),
            "objective": _plain(self.objective),
            "constraints": _plain(self.constraints),
            "planner": _plain(self.planner),
            "termination": _plain(self.termination),
            "fallback": _plain(self.fallback),
            "diagnostics": _plain(self.diagnostics),
            "chain_style": self.chain_style.to_dict(),
        }


@dataclass(frozen=True)
class TacticRegistry:
    schema_version: str
    registry_version: str
    analyzer_input_schema_version: str
    analyzer_diagnostics_schema_version: str
    tactics: tuple[TacticSpec, ...]
    source_path: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, source_path: str = "") -> "TacticRegistry":
        value = migrate_tactic_registry_payload(value)
        schema_version = str(value.get("schema_version", ""))
        if schema_version != TACTIC_SCHEMA_VERSION:
            raise ValueError(f"unsupported tactic schema: {schema_version}")
        compatible = _mapping(value.get("compatible_analyzer_schemas", {}), "compatible_analyzer_schemas")
        registry = cls(
            schema_version=schema_version,
            registry_version=str(value.get("registry_version", "")),
            analyzer_input_schema_version=str(compatible.get("input", "")),
            analyzer_diagnostics_schema_version=str(compatible.get("diagnostics", "")),
            tactics=tuple(
                TacticSpec.from_dict(_mapping(item, "tactic"))
                for item in value.get("tactics", ())
            ),
            source_path=source_path,
        )
        if not registry.registry_version or not registry.tactics:
            raise ValueError("registry_version and a non-empty tactics list are required")
        if registry.analyzer_input_schema_version != ANALYZER_INPUT_SCHEMA_VERSION:
            raise ValueError("registry Analyzer input schema is unsupported")
        if registry.analyzer_diagnostics_schema_version != ANALYZER_DIAGNOSTICS_SCHEMA_VERSION:
            raise ValueError("registry Analyzer diagnostics schema is unsupported")
        ids = [tactic.identity.tactic_id for tactic in registry.tactics]
        names = [tactic.identity.name for tactic in registry.tactics]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("tactic ids and names must be unique")
        known_ids = set(ids)
        for tactic in registry.tactics:
            fallback_id = tactic.fallback.get("tactic_id")
            if fallback_id and fallback_id not in known_ids:
                raise ValueError(f"unknown fallback tactic: {fallback_id}")
        return registry

    def tactic(self, tactic_id: str) -> TacticSpec:
        try:
            return next(tactic for tactic in self.tactics if tactic.identity.tactic_id == tactic_id)
        except StopIteration as exc:
            raise KeyError(tactic_id) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_version": self.registry_version,
            "compatible_analyzer_schemas": {
                "input": self.analyzer_input_schema_version,
                "diagnostics": self.analyzer_diagnostics_schema_version,
            },
            "tactics": [tactic.to_dict() for tactic in self.tactics],
        }


def load_tactic_registry(path: str | Path = DEFAULT_TACTIC_REGISTRY_PATH) -> TacticRegistry:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return TacticRegistry.from_dict(_mapping(payload, "registry"), source_path=str(source))


def migrate_tactic_registry_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Add an explicit unconstrained style to the shape-compatible v2 schema."""

    payload = _plain(_mapping(value, "registry"))
    schema_version = str(payload.get("schema_version", ""))
    if schema_version == TACTIC_SCHEMA_VERSION:
        return payload
    if schema_version != LEGACY_TACTIC_SCHEMA_VERSION:
        raise ValueError(f"unsupported tactic schema: {schema_version}")
    payload["schema_version"] = TACTIC_SCHEMA_VERSION
    for tactic in payload.get("tactics", ()):
        if isinstance(tactic, dict):
            tactic.setdefault("chain_style", ChainStyleSelection().to_dict())
    migration = dict(payload.get("schema_migration", {}))
    migration.update(
        {
            "source_schema_version": LEGACY_TACTIC_SCHEMA_VERSION,
            "target_schema_version": TACTIC_SCHEMA_VERSION,
            "chain_style_schema_version": CHAIN_STYLE_SCHEMA_VERSION,
            "default_style": "unconstrained",
        }
    )
    payload["schema_migration"] = migration
    return payload


def build_tactic_diagnostics(
    registry: TacticRegistry,
    analyzer_input: AnalyzerInput | Mapping[str, Any],
    analyzer_diagnostics: AnalyzerDiagnostics | Mapping[str, Any],
    parameter_overrides: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Record candidates and parameters without performing final tactic selection."""

    input_payload = _payload(analyzer_input)
    diagnostics_payload = _payload(analyzer_diagnostics)
    if input_payload.get("schema_version") != registry.analyzer_input_schema_version:
        raise ValueError("Analyzer input schema does not match tactic registry")
    if diagnostics_payload.get("schema_version") != registry.analyzer_diagnostics_schema_version:
        raise ValueError("Analyzer diagnostics schema does not match tactic registry")
    unknown_tactics = set(parameter_overrides or {}).difference(
        tactic.identity.tactic_id for tactic in registry.tactics
    )
    if unknown_tactics:
        raise ValueError(f"parameter overrides contain unknown tactics: {', '.join(sorted(unknown_tactics))}")
    values = {"input": input_payload, "diagnostics": diagnostics_payload}
    candidates = []
    for tactic in registry.tactics:
        group_results = [_evaluate_group(group, values) for group in tactic.candidate_groups]
        eligible = True if not group_results else any(group["passed"] for group in group_results)
        active_contexts = [
            context.context_id
            for context in tactic.contexts
            if all(_evaluate_condition(condition, values)["passed"] for condition in context.conditions)
        ]
        candidates.append(
            {
                "tactic_id": tactic.identity.tactic_id,
                "name": tactic.identity.name,
                "version": tactic.identity.version,
                "eligible": eligible,
                "candidate_condition_groups": group_results,
                "active_contexts": active_contexts,
                "analyzer_inputs": {ref: _resolve_ref(values, ref) for ref in tactic.input_refs},
                "parameters": tactic.resolve_parameters(
                    None if parameter_overrides is None else parameter_overrides.get(tactic.identity.tactic_id)
                ),
                "chain_style": tactic.chain_style.to_dict(),
            }
        )
    return {
        "schema_version": TACTIC_DIAGNOSTICS_SCHEMA_VERSION,
        "tactic_schema_version": registry.schema_version,
        "registry_version": registry.registry_version,
        "analyzer_input_schema_version": registry.analyzer_input_schema_version,
        "analyzer_diagnostics_schema_version": registry.analyzer_diagnostics_schema_version,
        "chain_style_schema_version": CHAIN_STYLE_SCHEMA_VERSION,
        "selection_performed": False,
        "candidates": candidates,
    }


def build_tactic_registry_artifact(
    registry: TacticRegistry,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = {
        "schema_version": TACTIC_ARTIFACT_SCHEMA_VERSION,
        "tactic_schema_version": registry.schema_version,
        "registry_version": registry.registry_version,
        "analyzer_input_schema_version": registry.analyzer_input_schema_version,
        "analyzer_diagnostics_schema_version": registry.analyzer_diagnostics_schema_version,
        "chain_style_schema_version": CHAIN_STYLE_SCHEMA_VERSION,
        "source_path": registry.source_path,
        "registry": registry.to_dict(),
    }
    if diagnostics is not None:
        if diagnostics.get("schema_version") != TACTIC_DIAGNOSTICS_SCHEMA_VERSION:
            raise ValueError("unsupported tactic diagnostics schema")
        artifact["diagnostics"] = _plain(diagnostics)
    return artifact


def write_tactic_registry_artifact(
    path: str | Path,
    registry: TacticRegistry,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = build_tactic_registry_artifact(registry, diagnostics)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def _evaluate_group(group: ConditionGroup, values: Mapping[str, Any]) -> dict[str, Any]:
    conditions = [_evaluate_condition(condition, values) for condition in group.conditions]
    return {"passed": all(result["passed"] for result in conditions), "conditions": conditions}


def _evaluate_condition(condition: ConditionSpec, values: Mapping[str, Any]) -> dict[str, Any]:
    actual = _resolve_ref(values, condition.ref)
    expected = condition.value
    if condition.op == "eq":
        passed = actual == expected
    elif condition.op == "ne":
        passed = actual != expected
    elif condition.op == "gt":
        passed = actual > expected
    elif condition.op == "ge":
        passed = actual >= expected
    elif condition.op == "lt":
        passed = actual < expected
    elif condition.op == "le":
        passed = actual <= expected
    else:
        passed = actual in expected
    return {
        "ref": condition.ref,
        "op": condition.op,
        "expected": expected,
        "actual": actual,
        "passed": bool(passed),
    }


def _resolve_ref(values: Mapping[str, Any], ref: str) -> Any:
    current: Any = values
    for part in ref.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"Analyzer field is missing: {ref}")
        current = current[part]
    return current


def _payload(value: Any) -> Mapping[str, Any]:
    payload = value.to_dict() if hasattr(value, "to_dict") else value
    return _mapping(payload, "Analyzer payload")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _validate_number(name: str, value: Any) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"parameter {name} must be numeric")


def _validate_refs(refs: Sequence[Any]) -> None:
    if not isinstance(refs, (list, tuple)):
        raise ValueError("input_refs must be a list")
    invalid = [str(ref) for ref in refs if not str(ref).startswith(("input.", "diagnostics."))]
    if invalid:
        raise ValueError(f"Analyzer refs must use a versioned root: {', '.join(invalid)}")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
