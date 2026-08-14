"""Composable, typed decision-flow execution primitives.

The framework is intentionally policy-neutral.  A policy supplies typed input
artifacts and a sequence of ``DecisionStep`` implementations; the executor
owns ordering, contract validation, and trace timing.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

DECISION_TRACE_SCHEMA_VERSION = "puyo.decision_trace.v1"


class DecisionProfile(Protocol):
    """Minimum profile surface required by a decision flow."""

    @property
    def profile_id(self) -> str: ...

    def to_dict(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class DecisionTraceEntry:
    """One completed step in a decision trace."""

    index: int
    step_id: str
    step_type: str
    input_summary: Mapping[str, Any]
    output_keys: tuple[str, ...]
    candidate_count: int | None
    selection_reason: str | None
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("decision trace index must be non-negative")
        if not self.step_id or not self.step_type:
            raise ValueError("decision trace step identity is required")
        if self.candidate_count is not None and self.candidate_count < 0:
            raise ValueError("decision trace candidate count must be non-negative")
        if self.elapsed_seconds < 0.0:
            raise ValueError("decision trace duration must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "step_id": self.step_id,
            "step_type": self.step_type,
            "input_summary": _plain(self.input_summary),
            "output_keys": list(self.output_keys),
            "candidate_count": self.candidate_count,
            "selection_reason": self.selection_reason,
            "elapsed_seconds": float(self.elapsed_seconds),
            "elapsed_ms": float(self.elapsed_seconds * 1_000.0),
        }


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    """Typed, serializable trace for one policy decision."""

    decision_id: str
    profile_id: str
    steps: tuple[DecisionTraceEntry, ...] = ()
    schema_version: str = DECISION_TRACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DECISION_TRACE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported decision trace schema: {self.schema_version}"
            )
        if not self.decision_id or not self.profile_id:
            raise ValueError("decision trace identity is required")

    @property
    def elapsed_seconds(self) -> float:
        return sum(step.elapsed_seconds for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "profile_id": self.profile_id,
            "step_count": len(self.steps),
            "elapsed_seconds": float(self.elapsed_seconds),
            "elapsed_ms": float(self.elapsed_seconds * 1_000.0),
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Immutable decision state passed from one step to the next."""

    decision_id: str
    profile: DecisionProfile
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    trace_entries: tuple[DecisionTraceEntry, ...] = ()

    def __post_init__(self) -> None:
        if not self.decision_id:
            raise ValueError("decision context requires a decision id")
        if not self.profile.profile_id:
            raise ValueError("decision context requires a profile id")

    def require(self, artifact_key: str) -> Any:
        try:
            return self.artifacts[artifact_key]
        except KeyError as exc:
            raise KeyError(
                f"decision artifact is not available: {artifact_key}"
            ) from exc

    @property
    def trace(self) -> DecisionTrace:
        return DecisionTrace(
            decision_id=self.decision_id,
            profile_id=self.profile.profile_id,
            steps=self.trace_entries,
        )

    def with_step_result(
        self,
        result: StepResult,
        trace_entry: DecisionTraceEntry,
    ) -> DecisionContext:
        artifacts = dict(self.artifacts)
        artifacts.update(result.outputs)
        return DecisionContext(
            decision_id=self.decision_id,
            profile=self.profile,
            artifacts=artifacts,
            trace_entries=(*self.trace_entries, trace_entry),
        )


@dataclass(frozen=True, slots=True)
class StepResult:
    """Artifacts and trace evidence emitted by one decision step."""

    outputs: Mapping[str, Any] = field(default_factory=dict)
    candidate_count: int | None = None
    selection_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outputs, Mapping):
            raise TypeError("step result outputs must be a mapping")
        if self.candidate_count is not None and self.candidate_count < 0:
            raise ValueError("step result candidate count must be non-negative")
        if self.selection_reason is not None and not self.selection_reason:
            raise ValueError("step result selection reason must be non-empty")
        if any(not str(key) for key in self.outputs):
            raise ValueError("step result output keys must be non-empty")


@dataclass(frozen=True, slots=True)
class DecisionStepContract:
    """Reviewable input/output contract for a flow step."""

    step_id: str
    requires: tuple[str, ...]
    provides: tuple[str, ...]
    purpose: str

    def __post_init__(self) -> None:
        if not self.step_id or not self.purpose:
            raise ValueError("decision step id and purpose are required")
        if len(self.requires) != len(set(self.requires)):
            raise ValueError(f"duplicate required artifact in step {self.step_id}")
        if len(self.provides) != len(set(self.provides)):
            raise ValueError(f"duplicate provided artifact in step {self.step_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "requires": list(self.requires),
            "provides": list(self.provides),
            "purpose": self.purpose,
        }


class DecisionStep(ABC):
    """One replaceable unit in a decision flow."""

    contract: DecisionStepContract

    @property
    def step_id(self) -> str:
        return self.contract.step_id

    def summarize_inputs(self, context: DecisionContext) -> Mapping[str, Any]:
        return {
            "required_artifacts": list(self.contract.requires),
            "available_artifact_count": len(context.artifacts),
        }

    @abstractmethod
    def run(self, context: DecisionContext) -> StepResult:
        """Execute the step and return only its new or replaced artifacts."""


class DecisionFlow:
    """Execute and compose an ordered sequence of decision steps."""

    def __init__(self, steps: Sequence[DecisionStep] | None = None) -> None:
        selected_steps = tuple(self.default_steps() if steps is None else steps)
        self._validate_steps(selected_steps)
        self._steps = selected_steps

    def default_steps(self) -> Sequence[DecisionStep]:
        """Subclasses override this to define an inheritable default flow."""

        return ()

    @property
    def steps(self) -> tuple[DecisionStep, ...]:
        return self._steps

    @property
    def step_ids(self) -> tuple[str, ...]:
        return tuple(step.step_id for step in self.steps)

    def contracts(self) -> tuple[DecisionStepContract, ...]:
        return tuple(step.contract for step in self.steps)

    def insert_before(self, target_step_id: str, step: DecisionStep) -> DecisionFlow:
        index = self._step_index(target_step_id)
        return self._copy_with_steps((*self.steps[:index], step, *self.steps[index:]))

    def insert_after(self, target_step_id: str, step: DecisionStep) -> DecisionFlow:
        index = self._step_index(target_step_id) + 1
        return self._copy_with_steps((*self.steps[:index], step, *self.steps[index:]))

    def replace_step(
        self,
        target_step_id: str,
        replacement: DecisionStep,
    ) -> DecisionFlow:
        index = self._step_index(target_step_id)
        return self._copy_with_steps(
            (*self.steps[:index], replacement, *self.steps[index + 1 :])
        )

    def reorder(self, step_ids: Sequence[str]) -> DecisionFlow:
        requested = tuple(step_ids)
        if len(requested) != len(self.steps) or set(requested) != set(self.step_ids):
            raise ValueError("reordered step ids must exactly match the current flow")
        by_id = {step.step_id: step for step in self.steps}
        return self._copy_with_steps(tuple(by_id[step_id] for step_id in requested))

    def execute(self, context: DecisionContext) -> DecisionContext:
        current = context
        for index, step in enumerate(self.steps):
            missing = [
                artifact
                for artifact in step.contract.requires
                if artifact not in current.artifacts
            ]
            if missing:
                raise KeyError(
                    f"step {step.step_id} is missing required artifacts: {missing}"
                )
            input_summary = step.summarize_inputs(current)
            if not isinstance(input_summary, Mapping):
                raise TypeError(f"step {step.step_id} input summary must be a mapping")
            started = time.perf_counter()
            result = step.run(current)
            elapsed = time.perf_counter() - started
            if not isinstance(result, StepResult):
                raise TypeError(f"step {step.step_id} must return StepResult")
            missing_outputs = [
                artifact
                for artifact in step.contract.provides
                if artifact not in result.outputs
            ]
            if missing_outputs:
                raise ValueError(
                    f"step {step.step_id} did not provide contracted artifacts: "
                    f"{missing_outputs}"
                )
            trace_entry = DecisionTraceEntry(
                index=index,
                step_id=step.step_id,
                step_type=type(step).__name__,
                input_summary=dict(input_summary),
                output_keys=tuple(str(key) for key in result.outputs),
                candidate_count=result.candidate_count,
                selection_reason=result.selection_reason,
                elapsed_seconds=elapsed,
            )
            current = current.with_step_result(result, trace_entry)
        return current

    def _copy_with_steps(self, steps: Sequence[DecisionStep]) -> DecisionFlow:
        return type(self)(steps=steps)

    def _step_index(self, step_id: str) -> int:
        try:
            return self.step_ids.index(step_id)
        except ValueError as exc:
            raise KeyError(f"decision step is not present: {step_id}") from exc

    @staticmethod
    def _validate_steps(steps: Sequence[DecisionStep]) -> None:
        if not steps:
            raise ValueError("decision flow requires at least one step")
        if any(not isinstance(step, DecisionStep) for step in steps):
            raise TypeError("decision flow steps must implement DecisionStep")
        step_ids = [step.step_id for step in steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("decision flow step ids must be unique")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return repr(value)


__all__ = [
    "DECISION_TRACE_SCHEMA_VERSION",
    "DecisionContext",
    "DecisionFlow",
    "DecisionProfile",
    "DecisionStep",
    "DecisionStepContract",
    "DecisionTrace",
    "DecisionTraceEntry",
    "StepResult",
]
