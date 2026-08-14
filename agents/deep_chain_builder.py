"""Public contracts for the configurable deep-chain builder policy.

PUYO-185 defines the visible-input boundary and the replaceable decision-flow
surface.  Search, evaluation, scenario completion, and plan generation remain
deferred to the dependent implementation tasks.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agents.chain_structure import ChainStructureEvaluator
from agents.compact_search import CompactSearchState, legal_action_indices, transition
from agents.decision_flow import (
    DecisionContext,
    DecisionFlow,
    DecisionStep,
    DecisionStepContract,
    StepResult,
)
from agents.long_horizon_search import (
    FUTURE_SAMPLING_LEGACY_FIXED_SIX,
    LongHorizonSearchConfig,
    build_scenario_sequences_from_known_pairs,
    run_compact_long_horizon_search,
)
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, NORMAL_PUYO_COLORS, PuyoColor

VISIBLE_PAIR_COLORS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
    PuyoColor.PURPLE,
)

DEEP_CHAIN_BUILDER_POLICY_ID = "deep_chain_builder"
DEEP_CHAIN_BUILDER_CONFIG_SCHEMA_VERSION = "puyo.deep_chain_builder.config.v1"
DEEP_CHAIN_BUILDER_PROFILE_SCHEMA_VERSION = "puyo.deep_chain_builder.profile.v1"
DEEP_CHAIN_BENCHMARK_SCHEMA_VERSION = "puyo.deep_chain_builder.benchmark.v1"
DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "train" / "config" / "deep_chain_builder.yaml"
)

RUNTIME_INPUT_ARTIFACT = "visible_runtime_input"
NORMALIZED_OBSERVATION_ARTIFACT = "normalized_observation"
SCENARIO_SEQUENCES_ARTIFACT = "scenario_sequences"
ROOT_PLACEMENTS_ARTIFACT = "root_placements"
SCENARIO_SEARCH_RESULTS_ARTIFACT = "scenario_search_results"
AGGREGATED_ROOT_SCORES_ARTIFACT = "aggregated_root_scores"
SELECTED_ACTION_ARTIFACT = "selected_action"
SELECTED_PLAN_ARTIFACT = "selected_plan"
SELECTION_EVIDENCE_ARTIFACT = "selection_evidence"
DECISION_OUTPUT_ARTIFACT = "decision_output"

# Only these fields cross the runtime-to-policy boundary.  In particular, the
# simulator objects in environment info are deliberately not retained.
VISIBLE_OBSERVATION_FIELDS = (
    "own_board",
    "board",
    "next_pairs",
    "scalars",
    "realtime_scalars",
    "action_mask",
    "schema_version",
)
VISIBLE_INFO_FIELDS = (
    "action_mask",
    "action_mask_source",
    "schema_version",
    "action_contract_version",
    "score",
    "step_count",
    "tick_count",
    "max_steps",
    "max_ticks",
    "last_chain_end_score",
    "last_chain_score_delta",
)


@dataclass(frozen=True, slots=True)
class DeepChainBuilderProfile:
    """Externalized search budget for one policy execution mode."""

    name: str
    version: str
    purpose: str
    depth: int
    width: int
    scenarios: int
    max_expanded_nodes: int
    schema_version: str = DEEP_CHAIN_BUILDER_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DEEP_CHAIN_BUILDER_PROFILE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported deep-chain profile schema: {self.schema_version}"
            )
        if not self.name or not self.version or not self.purpose:
            raise ValueError("deep-chain profile identity and purpose are required")
        if min(self.depth, self.width, self.scenarios, self.max_expanded_nodes) <= 0:
            raise ValueError("deep-chain profile budgets must be positive")

    @property
    def profile_id(self) -> str:
        return f"{DEEP_CHAIN_BUILDER_POLICY_ID}:{self.name}@{self.version}"

    @classmethod
    def from_dict(
        cls,
        name: str,
        value: Mapping[str, Any],
    ) -> DeepChainBuilderProfile:
        return cls(
            name=str(name),
            version=str(value.get("version", "")),
            purpose=str(value.get("purpose", "")),
            depth=int(value.get("depth", 0)),
            width=int(value.get("width", 0)),
            scenarios=int(value.get("scenarios", 0)),
            max_expanded_nodes=int(value.get("max_expanded_nodes", 0)),
            schema_version=str(
                value.get(
                    "schema_version",
                    DEEP_CHAIN_BUILDER_PROFILE_SCHEMA_VERSION,
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "profile_id": self.profile_id,
            "purpose": self.purpose,
            "depth": int(self.depth),
            "width": int(self.width),
            "scenarios": int(self.scenarios),
            "max_expanded_nodes": int(self.max_expanded_nodes),
        }


@dataclass(frozen=True, slots=True)
class DeepChainBenchmarkContract:
    """Locked quality and performance gate consumed by later evaluation work."""

    seed_start: int
    seed_count: int
    repeats_per_seed: int
    minimum_mean_actual_fire_chain_count: float
    maximum_premature_fires: int
    maximum_game_overs: int
    maximum_simulator_parity_mismatches: int
    maximum_private_future_leaks: int
    require_repeat_digest_match: bool
    maximum_decision_p95_seconds: float
    environment: str
    schema_version: str = DEEP_CHAIN_BENCHMARK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DEEP_CHAIN_BENCHMARK_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported deep-chain benchmark schema: {self.schema_version}"
            )
        if self.seed_start < 0:
            raise ValueError("benchmark seed start must be non-negative")
        if min(self.seed_count, self.repeats_per_seed) <= 0:
            raise ValueError("benchmark seed and repeat counts must be positive")
        if self.minimum_mean_actual_fire_chain_count <= 0.0:
            raise ValueError("benchmark chain target must be positive")
        if (
            min(
                self.maximum_premature_fires,
                self.maximum_game_overs,
                self.maximum_simulator_parity_mismatches,
                self.maximum_private_future_leaks,
            )
            < 0
        ):
            raise ValueError("benchmark failure allowances must be non-negative")
        if self.maximum_decision_p95_seconds <= 0.0 or not self.environment:
            raise ValueError("benchmark latency and environment are required")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DeepChainBenchmarkContract:
        return cls(
            seed_start=int(value.get("seed_start", -1)),
            seed_count=int(value.get("seed_count", 0)),
            repeats_per_seed=int(value.get("repeats_per_seed", 0)),
            minimum_mean_actual_fire_chain_count=float(
                value.get("minimum_mean_actual_fire_chain_count", 0.0)
            ),
            maximum_premature_fires=int(value.get("maximum_premature_fires", -1)),
            maximum_game_overs=int(value.get("maximum_game_overs", -1)),
            maximum_simulator_parity_mismatches=int(
                value.get("maximum_simulator_parity_mismatches", -1)
            ),
            maximum_private_future_leaks=int(
                value.get("maximum_private_future_leaks", -1)
            ),
            require_repeat_digest_match=bool(
                value.get("require_repeat_digest_match", False)
            ),
            maximum_decision_p95_seconds=float(
                value.get("maximum_decision_p95_seconds", 0.0)
            ),
            environment=str(value.get("environment", "")),
            schema_version=str(
                value.get(
                    "schema_version",
                    DEEP_CHAIN_BENCHMARK_SCHEMA_VERSION,
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "seed_start": int(self.seed_start),
            "seed_count": int(self.seed_count),
            "seeds": list(range(self.seed_start, self.seed_start + self.seed_count)),
            "repeats_per_seed": int(self.repeats_per_seed),
            "run_count": int(self.seed_count * self.repeats_per_seed),
            "environment": self.environment,
            "minimum_mean_actual_fire_chain_count": float(
                self.minimum_mean_actual_fire_chain_count
            ),
            "maximum_premature_fires": int(self.maximum_premature_fires),
            "maximum_game_overs": int(self.maximum_game_overs),
            "maximum_simulator_parity_mismatches": int(
                self.maximum_simulator_parity_mismatches
            ),
            "maximum_private_future_leaks": int(self.maximum_private_future_leaks),
            "require_repeat_digest_match": bool(self.require_repeat_digest_match),
            "maximum_decision_p95_seconds": float(self.maximum_decision_p95_seconds),
        }


@dataclass(frozen=True, slots=True)
class DeepChainBuilderConfig:
    """Versioned profiles and benchmark contract loaded from YAML."""

    config_version: str
    profiles: Mapping[str, DeepChainBuilderProfile]
    benchmark: DeepChainBenchmarkContract
    policy_id: str = DEEP_CHAIN_BUILDER_POLICY_ID
    schema_version: str = DEEP_CHAIN_BUILDER_CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DEEP_CHAIN_BUILDER_CONFIG_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported deep-chain config schema: {self.schema_version}"
            )
        if self.policy_id != DEEP_CHAIN_BUILDER_POLICY_ID:
            raise ValueError(f"unsupported deep-chain policy id: {self.policy_id}")
        if not self.config_version:
            raise ValueError("deep-chain config version is required")
        if "reference" not in self.profiles or "smoke" not in self.profiles:
            raise ValueError("deep-chain config requires reference and smoke profiles")
        if any(name != profile.name for name, profile in self.profiles.items()):
            raise ValueError("deep-chain profile mapping keys must match profile names")
        reference = self.profiles["reference"]
        smoke = self.profiles["smoke"]
        if (
            smoke.depth > reference.depth
            or smoke.width > reference.width
            or smoke.scenarios > reference.scenarios
            or smoke.max_expanded_nodes > reference.max_expanded_nodes
        ):
            raise ValueError("smoke profile must not exceed the reference budget")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DeepChainBuilderConfig:
        profile_values = value.get("profiles")
        benchmark_value = value.get("benchmark")
        if not isinstance(profile_values, Mapping) or not isinstance(
            benchmark_value, Mapping
        ):
            raise TypeError(
                "deep-chain config requires profiles and benchmark mappings"
            )
        profiles = {
            str(name): DeepChainBuilderProfile.from_dict(str(name), profile)
            for name, profile in profile_values.items()
            if isinstance(profile, Mapping)
        }
        return cls(
            config_version=str(value.get("config_version", "")),
            profiles=profiles,
            benchmark=DeepChainBenchmarkContract.from_dict(benchmark_value),
            policy_id=str(value.get("policy_id", "")),
            schema_version=str(value.get("schema_version", "")),
        )

    def profile(self, name: str) -> DeepChainBuilderProfile:
        try:
            return self.profiles[str(name)]
        except KeyError as exc:
            raise ValueError(f"unknown deep-chain builder profile: {name}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "config_version": self.config_version,
            "policy_id": self.policy_id,
            "profiles": {
                name: profile.to_dict() for name, profile in self.profiles.items()
            },
            "benchmark": self.benchmark.to_dict(),
        }


def load_deep_chain_builder_config(
    path: str | Path = DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH,
) -> DeepChainBuilderConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise TypeError("deep-chain builder config must be a mapping")
    return DeepChainBuilderConfig.from_dict(payload)


@dataclass(frozen=True, slots=True)
class VisibleRuntimeInput:
    """Snapshot of fields that the runtime policy is permitted to observe."""

    board: Any
    next_pairs: Any
    action_mask: tuple[bool, ...]
    scalars: Any = None
    realtime_scalars: Any = None
    observation_schema_version: str = ""
    action_contract_version: str = ""
    action_mask_source: str = ""
    score: int = 0
    step_count: int = 0
    max_steps: int | None = None
    max_ticks: int | None = None
    last_chain_end_score: int = 0
    last_chain_score_delta: int = 0

    @property
    def visible_pair_count(self) -> int:
        return _outer_length(self.next_pairs)

    @property
    def legal_action_count(self) -> int:
        return sum(self.action_mask)

    def summary(self) -> dict[str, Any]:
        return {
            "board_shape": list(_shape(self.board)),
            "next_pairs_shape": list(_shape(self.next_pairs)),
            "visible_pair_count": int(self.visible_pair_count),
            "legal_action_count": int(self.legal_action_count),
            "observation_schema_version": self.observation_schema_version,
            "action_contract_version": self.action_contract_version,
            "step_count": int(self.step_count),
            "max_steps": self.max_steps,
            "max_ticks": self.max_ticks,
            "last_chain_end_score": int(self.last_chain_end_score),
            "last_chain_score_delta": int(self.last_chain_score_delta),
        }


def build_visible_runtime_input(
    observation: Mapping[str, Any],
    info: Mapping[str, Any],
) -> VisibleRuntimeInput:
    """Copy only allowlisted visible fields without touching simulator objects."""

    board = observation.get("own_board")
    if board is None:
        board = observation.get("board")
    action_mask = info.get("action_mask")
    if action_mask is None:
        action_mask = observation.get("action_mask")
    step_count = info.get("step_count")
    if step_count is None:
        step_count = info.get("tick_count", 0)
    return VisibleRuntimeInput(
        board=_snapshot_value(board),
        next_pairs=_snapshot_value(observation.get("next_pairs")),
        action_mask=tuple(
            bool(value) for value in (() if action_mask is None else action_mask)
        ),
        scalars=_snapshot_value(observation.get("scalars")),
        realtime_scalars=_snapshot_value(observation.get("realtime_scalars")),
        observation_schema_version=str(
            observation.get("schema_version") or info.get("schema_version") or ""
        ),
        action_contract_version=str(info.get("action_contract_version") or ""),
        action_mask_source=str(info.get("action_mask_source") or ""),
        score=int(info.get("score") or 0),
        step_count=int(step_count or 0),
        max_steps=_optional_int(info.get("max_steps")),
        max_ticks=_optional_int(info.get("max_ticks")),
        last_chain_end_score=int(info.get("last_chain_end_score") or 0),
        last_chain_score_delta=int(info.get("last_chain_score_delta") or 0),
    )


class NormalizeObservationStep(DecisionStep):
    contract = DecisionStepContract(
        step_id="normalize_observation",
        requires=(RUNTIME_INPUT_ARTIFACT,),
        provides=(NORMALIZED_OBSERVATION_ARTIFACT,),
        purpose=(
            "Validate the allowlisted board, visible current/NEXT pairs, and legal "
            "actions without retaining authoritative simulator state."
        ),
    )

    def summarize_inputs(self, context: DecisionContext) -> Mapping[str, Any]:
        runtime_input = context.require(RUNTIME_INPUT_ARTIFACT)
        if not isinstance(runtime_input, VisibleRuntimeInput):
            return {"runtime_input_type": type(runtime_input).__name__}
        return runtime_input.summary()

    def run(self, context: DecisionContext) -> StepResult:
        runtime_input = context.require(RUNTIME_INPUT_ARTIFACT)
        if not isinstance(runtime_input, VisibleRuntimeInput):
            raise TypeError("visible runtime input has an unsupported type")
        if runtime_input.board is None:
            raise ValueError("visible runtime input requires an own board")
        if runtime_input.next_pairs is None or runtime_input.visible_pair_count <= 0:
            raise ValueError("visible runtime input requires current/NEXT pairs")
        return StepResult(
            outputs={NORMALIZED_OBSERVATION_ARTIFACT: runtime_input},
            candidate_count=runtime_input.legal_action_count,
            selection_reason="visible_runtime_boundary_validated",
        )


class _DeferredDeepChainStep(DecisionStep):
    implementation_ticket = "PUYO-186"

    def run(self, context: DecisionContext) -> StepResult:
        _ = context
        raise NotImplementedError(
            f"{type(self).__name__} is a public contract; implementation is "
            f"deferred to {self.implementation_ticket}"
        )


class CompleteVisibleQueueScenariosStep(_DeferredDeepChainStep):
    contract = DecisionStepContract(
        step_id="complete_visible_queue_scenarios",
        requires=(NORMALIZED_OBSERVATION_ARTIFACT,),
        provides=(SCENARIO_SEQUENCES_ARTIFACT,),
        purpose=(
            "Preserve visible pairs and complete only the unknown horizon with "
            "deterministic representative scenario sequences."
        ),
    )

    def run(self, context: DecisionContext) -> StepResult:
        observation = context.require(NORMALIZED_OBSERVATION_ARTIFACT)
        if not isinstance(observation, VisibleRuntimeInput):
            raise TypeError("normalized observation has an unsupported type")
        profile = context.profile
        seed = _visible_decision_seed(observation)
        pairs = _decode_visible_pairs(observation.next_pairs)
        sequences = build_scenario_sequences_from_known_pairs(
            pairs,
            scenarios=profile.scenarios,
            depth=profile.depth,
            decision_seed=seed,
            sampling_mode=FUTURE_SAMPLING_LEGACY_FIXED_SIX,
            decision_seed_source="visible_observation_digest",
        )
        return StepResult(
            outputs={SCENARIO_SEQUENCES_ARTIFACT: sequences},
            candidate_count=len(sequences),
            selection_reason="visible_pairs_preserved_and_unknown_suffix_completed",
        )


class EnumerateRootPlacementsStep(_DeferredDeepChainStep):
    contract = DecisionStepContract(
        step_id="enumerate_root_placements",
        requires=(NORMALIZED_OBSERVATION_ARTIFACT,),
        provides=(ROOT_PLACEMENTS_ARTIFACT,),
        purpose="Enumerate all legal first placements with stable root identities.",
    )

    def run(self, context: DecisionContext) -> StepResult:
        observation = context.require(NORMALIZED_OBSERVATION_ARTIFACT)
        state = _compact_state_from_board(observation.board)
        roots = tuple(
            {
                "action": int(action),
                "root_id": f"root-{int(action):02d}",
            }
            for action in legal_action_indices(state)
        )
        return StepResult(
            outputs={ROOT_PLACEMENTS_ARTIFACT: roots},
            candidate_count=len(roots),
            selection_reason="compact_kernel_legal_root_enumeration",
        )


class RunLongRangeSearchStep(_DeferredDeepChainStep):
    contract = DecisionStepContract(
        step_id="run_long_range_search",
        requires=(
            NORMALIZED_OBSERVATION_ARTIFACT,
            SCENARIO_SEQUENCES_ARTIFACT,
            ROOT_PLACEMENTS_ARTIFACT,
        ),
        provides=(SCENARIO_SEARCH_RESULTS_ARTIFACT,),
        purpose=(
            "Run the configured depth/width search for every scenario while "
            "retaining root identity and representative trajectories."
        ),
    )

    def run(self, context: DecisionContext) -> StepResult:
        observation = context.require(NORMALIZED_OBSERVATION_ARTIFACT)
        sequences = context.require(SCENARIO_SEQUENCES_ARTIFACT)
        roots = context.require(ROOT_PLACEMENTS_ARTIFACT)
        if not isinstance(observation, VisibleRuntimeInput):
            raise TypeError("normalized observation has an unsupported type")
        if not sequences or not roots:
            raise ValueError("long-range search requires scenarios and roots")
        profile = context.profile
        config = LongHorizonSearchConfig(
            depth=int(profile.depth),
            width=int(profile.width),
            scenarios=int(profile.scenarios),
            minimum_chain_count=6,
            max_expanded_nodes=int(profile.max_expanded_nodes),
            decision_seed=_visible_decision_seed(observation),
            future_sampling_mode=FUTURE_SAMPLING_LEGACY_FIXED_SIX,
        )
        result = run_compact_long_horizon_search(
            _compact_state_from_board(observation.board),
            _decode_visible_pairs(observation.next_pairs),
            config,
            evaluator=ChainStructureEvaluator(),
        )
        return StepResult(
            outputs={
                SCENARIO_SEARCH_RESULTS_ARTIFACT: {
                    "schema_version": "puyo.deep_chain_builder.search_results.v1",
                    "result": result,
                    "root_state": _compact_state_from_board(observation.board),
                    "scenario_ids": tuple(
                        sequence.scenario_id for sequence in sequences
                    ),
                    "root_ids": tuple(int(root["action"]) for root in roots),
                }
            },
            candidate_count=len(result.root_evidence),
            selection_reason="count_bounded_compact_long_range_search",
        )


class AggregateScenarioScoresStep(_DeferredDeepChainStep):
    contract = DecisionStepContract(
        step_id="aggregate_scenario_scores",
        requires=(SCENARIO_SEARCH_RESULTS_ARTIFACT,),
        provides=(AGGREGATED_ROOT_SCORES_ARTIFACT,),
        purpose=(
            "Aggregate every scenario exactly once by first placement and keep "
            "the score evidence used for ranking."
        ),
    )

    def run(self, context: DecisionContext) -> StepResult:
        payload = context.require(SCENARIO_SEARCH_RESULTS_ARTIFACT)
        result = payload["result"]
        aggregates = tuple(
            {
                "root_action": int(evidence.root_action),
                "ranking_key": tuple(evidence.ranking_key),
                "score_breakdown": evidence.value_breakdown(),
                "evidence": evidence,
                "representative": _representative_payload(
                    result, int(evidence.root_action), payload["root_state"]
                ),
            }
            for evidence in sorted(
                result.root_evidence,
                key=lambda item: item.ranking_key,
                reverse=True,
            )
        )
        return StepResult(
            outputs={AGGREGATED_ROOT_SCORES_ARTIFACT: aggregates},
            candidate_count=len(aggregates),
            selection_reason="all_scenarios_aggregated_once_by_root_action",
        )


class SelectPlacementStep(_DeferredDeepChainStep):
    contract = DecisionStepContract(
        step_id="select_placement",
        requires=(AGGREGATED_ROOT_SCORES_ARTIFACT,),
        provides=(
            SELECTED_ACTION_ARTIFACT,
            SELECTED_PLAN_ARTIFACT,
            SELECTION_EVIDENCE_ARTIFACT,
        ),
        purpose=(
            "Select one legal root with deterministic tie-breaking and retain "
            "the corresponding N-turn plan and reason."
        ),
    )


class EmitDecisionTraceStep(_DeferredDeepChainStep):
    implementation_ticket = "PUYO-187"
    contract = DecisionStepContract(
        step_id="emit_decision_trace",
        requires=(SELECTED_ACTION_ARTIFACT, SELECTION_EVIDENCE_ARTIFACT),
        provides=(DECISION_OUTPUT_ARTIFACT,),
        purpose=(
            "Serialize the selected action, plan, evidence, and flow trace for "
            "policy diagnostics and replay output."
        ),
    )


class DeepChainBuildFlow(DecisionFlow):
    """Default inheritable flow for ``deep_chain_builder`` decisions."""

    def default_steps(self) -> Sequence[DecisionStep]:
        return (
            NormalizeObservationStep(),
            CompleteVisibleQueueScenariosStep(),
            EnumerateRootPlacementsStep(),
            RunLongRangeSearchStep(),
            AggregateScenarioScoresStep(),
            SelectPlacementStep(),
            EmitDecisionTraceStep(),
        )


class DeepChainBuilderPolicy:
    """Policy adapter around an injectable ``DeepChainBuildFlow``.

    The default flow intentionally stops at the first search contract until the
    dependent implementation tasks land.  Tests and future policies may inject
    a complete flow without changing the placement-policy interface.
    """

    policy_id = DEEP_CHAIN_BUILDER_POLICY_ID

    def __init__(
        self,
        profile: str | DeepChainBuilderProfile = "reference",
        *,
        flow: DecisionFlow | None = None,
        config: DeepChainBuilderConfig | None = None,
        config_path: str | Path = DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH,
    ) -> None:
        self.config = config or load_deep_chain_builder_config(config_path)
        self.profile = (
            profile
            if isinstance(profile, DeepChainBuilderProfile)
            else self.config.profile(profile)
        )
        self.flow = flow or DeepChainBuildFlow()
        self.last_context: DecisionContext | None = None
        self._decision_count = 0

    def reset(self) -> None:
        self.last_context = None
        self._decision_count = 0

    def decide(
        self,
        observation: Mapping[str, Any],
        info: Mapping[str, Any],
    ) -> DecisionContext:
        self._decision_count += 1
        context = DecisionContext(
            decision_id=f"{self.policy_id}-decision-{self._decision_count:08d}",
            profile=self.profile,
            artifacts={
                RUNTIME_INPUT_ARTIFACT: build_visible_runtime_input(
                    observation,
                    info,
                )
            },
        )
        result = self.flow.execute(context)
        self.last_context = result
        return result

    def select_action(
        self,
        observation: Mapping[str, Any],
        info: Mapping[str, Any],
    ) -> int:
        result = self.decide(observation, info)
        action = int(result.require(SELECTED_ACTION_ARTIFACT))
        runtime_input = result.require(RUNTIME_INPUT_ARTIFACT)
        if (
            isinstance(runtime_input, VisibleRuntimeInput)
            and runtime_input.action_mask
            and (
                not 0 <= action < len(runtime_input.action_mask)
                or not runtime_input.action_mask[action]
            )
        ):
            raise ValueError("deep-chain flow selected an illegal placement action")
        return action

    @property
    def tactical_diagnostics(self) -> dict[str, Any]:
        context = self.last_context
        return {
            "policy_id": self.policy_id,
            "profile": self.profile.to_dict(),
            "decision_trace": {} if context is None else context.trace.to_dict(),
            "selected_action": (
                None
                if context is None
                else context.artifacts.get(SELECTED_ACTION_ARTIFACT)
            ),
        }


def _snapshot_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "copy"):
        try:
            return value.copy()
        except (TypeError, ValueError):
            pass
    if isinstance(value, tuple):
        return tuple(_snapshot_value(item) for item in value)
    if isinstance(value, list):
        return tuple(_snapshot_value(item) for item in value)
    return value


def _outer_length(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(value)
    except TypeError:
        return 0


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _shape(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    shape = getattr(value, "shape", None)
    if shape is not None:
        return tuple(int(dimension) for dimension in shape)
    dimensions = []
    current = value
    while isinstance(current, (list, tuple)):
        dimensions.append(len(current))
        if not current:
            break
        current = current[0]
    return tuple(dimensions)


def _visible_decision_seed(observation: VisibleRuntimeInput) -> int:
    """Derive a stable seed from visible state only."""

    payload = {
        "board": _plain_nested(observation.board),
        "next_pairs": _plain_nested(observation.next_pairs),
        "score": int(observation.score),
        "step_count": int(observation.step_count),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _plain_nested(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return _plain_nested(value.tolist())
    if isinstance(value, (list, tuple)):
        return [_plain_nested(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _decode_visible_pairs(value: Any) -> tuple[tuple[PuyoColor, PuyoColor], ...]:
    pairs = []
    for pair in value:
        decoded = []
        for encoded in pair:
            if isinstance(encoded, str):
                try:
                    decoded.append(PuyoColor[encoded])
                except KeyError as exc:
                    raise ValueError(
                        f"unsupported visible pair color: {encoded}"
                    ) from exc
                continue
            if hasattr(encoded, "tolist"):
                encoded = encoded.tolist()
            if len(encoded) != len(VISIBLE_PAIR_COLORS):
                raise ValueError("visible pair has an unsupported color encoding")
            indices = [index for index, item in enumerate(encoded) if float(item) > 0.5]
            if len(indices) != 1:
                raise ValueError("visible pair must contain exactly one color")
            if indices[0] >= len(NORMAL_PUYO_COLORS):
                raise ValueError(
                    "visible pair contains a color unsupported by the simulator"
                )
            decoded.append(NORMAL_PUYO_COLORS[indices[0]])
        pairs.append((decoded[0], decoded[1]))
    if not pairs:
        raise ValueError("visible queue must contain at least one pair")
    return tuple(pairs)


def _compact_state_from_board(board: Any) -> CompactSearchState:
    """Convert the public top-down one-hot board into the compact kernel."""

    shape = _shape(board)
    expected = (6, GRID_HEIGHT - 1, GRID_WIDTH)
    if shape != expected:
        raise ValueError(f"visible board must have shape {expected}, got {shape}")
    planes = [0] * 6
    for channel in range(6):
        for encoded_row in range(GRID_HEIGHT - 1):
            y = (GRID_HEIGHT - 2) - encoded_row
            for x in range(GRID_WIDTH):
                if float(board[channel][encoded_row][x]) > 0.5:
                    bit = 1 << (y * GRID_WIDTH + x)
                    if any(plane & bit for plane in planes):
                        raise ValueError("visible board contains overlapping colors")
                    planes[channel] |= bit
    return CompactSearchState(planes=tuple(planes))


def _representative_payload(
    result: Any, root_action: int, root_state: CompactSearchState
) -> dict[str, Any] | None:
    node = result.representatives.get(int(root_action))
    if node is None:
        return None
    sequence = next(
        (
            item
            for item in result.scenario_sequences
            if item.scenario_id == node.scenario_id
        ),
        None,
    )
    if sequence is None:
        return {"scenario_id": int(node.scenario_id), "actions": list(node.path)}
    state = root_state
    predicted_boards = []
    state_fingerprints = []
    for cursor, action in enumerate(node.path):
        step = transition(
            state,
            sequence.pair_at(cursor),
            int(action),
            capture_visuals=True,
        )
        if not step.valid:
            break
        state = step.state
        state_fingerprints.append(hashlib.sha256(state.to_bytes()).hexdigest()[:24])
        predicted_boards.append(
            [[cell.name for cell in row] for row in step.placement_board]
        )
    return {
        "scenario_id": int(node.scenario_id),
        "sample_id": sequence.sample_id,
        "actions": [int(action) for action in node.path],
        "queue_digest": sequence.queue_digest,
        "predicted_boards": predicted_boards,
        "state_fingerprints": state_fingerprints,
        "final_state_fingerprint": node.state_fingerprint,
    }


__all__ = [
    "AGGREGATED_ROOT_SCORES_ARTIFACT",
    "DECISION_OUTPUT_ARTIFACT",
    "DEEP_CHAIN_BENCHMARK_SCHEMA_VERSION",
    "DEEP_CHAIN_BUILDER_CONFIG_SCHEMA_VERSION",
    "DEEP_CHAIN_BUILDER_POLICY_ID",
    "DEEP_CHAIN_BUILDER_PROFILE_SCHEMA_VERSION",
    "DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH",
    "VISIBLE_INFO_FIELDS",
    "VISIBLE_OBSERVATION_FIELDS",
    "AggregateScenarioScoresStep",
    "CompleteVisibleQueueScenariosStep",
    "DeepChainBenchmarkContract",
    "DeepChainBuildFlow",
    "DeepChainBuilderConfig",
    "DeepChainBuilderPolicy",
    "DeepChainBuilderProfile",
    "EmitDecisionTraceStep",
    "EnumerateRootPlacementsStep",
    "NormalizeObservationStep",
    "RunLongRangeSearchStep",
    "SelectPlacementStep",
    "VisibleRuntimeInput",
    "build_visible_runtime_input",
    "load_deep_chain_builder_config",
]
