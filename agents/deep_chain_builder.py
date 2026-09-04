"""Configurable, visible-input-only deep-chain builder policy.

PUYO-185 defines the replaceable decision-flow surface, PUYO-186 supplies the
search core, and PUYO-187 connects selection, plans, diagnostics, and fallback
into the headless placement-policy interface. PUYO-203 routes the search step
through explicit Python/native backends without changing that public contract.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agents.chain_structure import (
    DEFAULT_CHAIN_STRUCTURE_CONFIG_PATH,
    ChainStructureConfig,
    load_chain_structure_config,
)
from agents.compact_search import CompactSearchState, legal_action_indices, transition
from agents.decision_flow import (
    DecisionContext,
    DecisionFlow,
    DecisionStep,
    DecisionStepContract,
    DecisionTraceEntry,
    StepResult,
)
from agents.deep_chain_native import DeepChainNativeError
from agents.deep_chain_search_backend import (
    DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH,
    LONG_HORIZON_BACKEND_CHOICES,
    LongHorizonBackendConfig,
    LongHorizonBackendRequest,
    LongHorizonSearchBackend,
    PythonLongHorizonSearchBackend,
    file_sha256,
    load_long_horizon_backend_config,
    make_long_horizon_search_backend,
    semantic_sha256,
)
from agents.long_horizon_search import (
    FUTURE_SAMPLING_LEGACY_FIXED_SIX,
    LongHorizonSearchConfig,
    build_scenario_sequences_from_known_pairs,
)
from puyo_env.actions import action_to_placement
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
DEEP_CHAIN_DIAGNOSTICS_SCHEMA_VERSION = "puyo.deep_chain_builder.diagnostics.v1"
DEEP_CHAIN_SELECTION_SCHEMA_VERSION = "puyo.deep_chain_builder.selection.v1"
N_TURN_PLAN_SCHEMA_VERSION = "n-turn-plan-v1"
DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT = 6
DEEP_CHAIN_TARGET_CHAIN_CHOICES = (6, 8, 10, 12)
MAX_DEEP_CHAIN_TARGET_CHAIN_COUNT = 255
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
PREVIOUS_PLAN_ARTIFACT = "previous_plan"
TARGET_CHAIN_COUNT_ARTIFACT = "target_chain_count"

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
    max_steps: int
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
        if min(self.seed_count, self.repeats_per_seed, self.max_steps) <= 0:
            raise ValueError(
                "benchmark seed, repeat, and placement counts must be positive"
            )
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
            max_steps=int(value.get("max_steps", 0)),
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
            "max_steps": int(self.max_steps),
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

    def __init__(
        self,
        backend: LongHorizonSearchBackend | None = None,
        *,
        evaluator_config: ChainStructureConfig | None = None,
        evaluator_config_sha256: str | None = None,
        search_config_version: str = "v1.0",
        search_config_sha256: str | None = None,
        backend_config: LongHorizonBackendConfig | None = None,
        backend_config_sha256: str | None = None,
        canonical: bool = False,
        allow_auto_fallback: bool = False,
    ) -> None:
        self.backend = backend or PythonLongHorizonSearchBackend()
        self.evaluator_config = evaluator_config or load_chain_structure_config()
        self.evaluator_config_sha256 = evaluator_config_sha256 or (
            semantic_sha256(self.evaluator_config.to_dict())
            if evaluator_config is not None
            else file_sha256(DEFAULT_CHAIN_STRUCTURE_CONFIG_PATH)
        )
        self.search_config_version = str(search_config_version)
        self.search_config_sha256 = search_config_sha256 or file_sha256(
            DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH
        )
        self.backend_config = backend_config or load_long_horizon_backend_config()
        self.backend_config_sha256 = backend_config_sha256 or file_sha256(
            DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH
        )
        self.canonical = bool(canonical)
        self.allow_auto_fallback = bool(allow_auto_fallback)

    def summarize_inputs(self, context: DecisionContext) -> Mapping[str, Any]:
        summary = dict(super().summarize_inputs(context))
        summary["backend"] = _json_ready(self.backend.describe())
        summary["target_chain_count"] = _validate_target_chain_count(
            context.artifacts.get(
                TARGET_CHAIN_COUNT_ARTIFACT,
                DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
            )
        )
        return summary

    def run(self, context: DecisionContext) -> StepResult:
        observation = context.require(NORMALIZED_OBSERVATION_ARTIFACT)
        sequences = context.require(SCENARIO_SEQUENCES_ARTIFACT)
        roots = context.require(ROOT_PLACEMENTS_ARTIFACT)
        if not isinstance(observation, VisibleRuntimeInput):
            raise TypeError("normalized observation has an unsupported type")
        if not sequences or not roots:
            raise ValueError("long-range search requires scenarios and roots")
        profile = context.profile
        target_chain_count = _validate_target_chain_count(
            context.artifacts.get(
                TARGET_CHAIN_COUNT_ARTIFACT,
                DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
            )
        )
        config = LongHorizonSearchConfig(
            depth=int(profile.depth),
            width=int(profile.width),
            scenarios=int(profile.scenarios),
            minimum_chain_count=target_chain_count,
            max_expanded_nodes=int(profile.max_expanded_nodes),
            decision_seed=_visible_decision_seed(observation),
            future_sampling_mode=FUTURE_SAMPLING_LEGACY_FIXED_SIX,
        )
        root_state = _compact_state_from_observation(observation)
        execution = self.backend.search(
            LongHorizonBackendRequest(
                root_state=root_state,
                known_pairs=_decode_visible_pairs(observation.next_pairs),
                search_config=config,
                evaluator_config=self.evaluator_config,
                profile_name=str(profile.name),
                profile_version=str(profile.version),
                search_config_version=self.search_config_version,
                search_config_sha256=self.search_config_sha256,
                evaluator_config_version=self.evaluator_config.weight_version,
                evaluator_config_sha256=self.evaluator_config_sha256,
                backend_config_version=self.backend_config.config_version,
                backend_config_sha256=self.backend_config_sha256,
                request_id=_backend_request_id(context.decision_id),
                canonical=self.canonical,
                allow_auto_fallback=self.allow_auto_fallback,
            )
        )
        result = execution.result
        if tuple(
            sequence.sequence_digest for sequence in result.scenario_sequences
        ) != tuple(sequence.sequence_digest for sequence in sequences):
            raise ValueError("backend scenario sequences differ from the flow input")
        result_root_ids = tuple(
            sorted(int(evidence.root_action) for evidence in result.root_evidence)
        )
        expected_root_ids = tuple(sorted(int(root["action"]) for root in roots))
        if result_root_ids != expected_root_ids:
            raise ValueError("backend root identities differ from legal placements")
        backend_diagnostics = _json_ready(execution.diagnostics)
        return StepResult(
            outputs={
                SCENARIO_SEARCH_RESULTS_ARTIFACT: {
                    "schema_version": "puyo.deep_chain_builder.search_results.v1",
                    "result": result,
                    "root_state": root_state,
                    "scenario_ids": tuple(
                        sequence.scenario_id for sequence in sequences
                    ),
                    "root_ids": tuple(int(root["action"]) for root in roots),
                    "target_chain_count": target_chain_count,
                    "backend": backend_diagnostics,
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
                "search_diagnostics": result.root_diagnostics.get(
                    int(evidence.root_action), {}
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

    def summarize_inputs(self, context: DecisionContext) -> Mapping[str, Any]:
        values = context.require(AGGREGATED_ROOT_SCORES_ARTIFACT)
        return {
            "aggregated_root_count": len(values),
            "root_actions": [int(value["root_action"]) for value in values],
        }

    def run(self, context: DecisionContext) -> StepResult:
        values = context.require(AGGREGATED_ROOT_SCORES_ARTIFACT)
        if not isinstance(values, Sequence) or not values:
            raise ValueError("deep-chain selection requires aggregated root scores")
        ranked = sorted(
            values,
            key=lambda value: (
                tuple(value["ranking_key"]),
                -int(value["root_action"]),
            ),
            reverse=True,
        )
        selected = ranked[0]
        action = int(selected["root_action"])
        representative = selected.get("representative")
        search_payload = context.artifacts.get(SCENARIO_SEARCH_RESULTS_ARTIFACT)
        if not isinstance(representative, Mapping) and isinstance(
            search_payload, Mapping
        ):
            representative = _representative_payload(
                search_payload.get("result"),
                action,
                search_payload.get("root_state"),
            )
            selected = {**selected, "representative": representative}
        if not isinstance(representative, Mapping):
            raise TypeError("selected deep-chain root has no representative trajectory")
        actions = representative.get("actions")
        if not isinstance(actions, Sequence) or not actions:
            raise ValueError("selected deep-chain trajectory has no actions")
        if int(actions[0]) != action:
            raise ValueError("selected root and representative first action disagree")

        plan = _selected_plan(
            context.profile,
            selected,
            previous_plan=context.artifacts.get(PREVIOUS_PLAN_ARTIFACT),
            target_chain_count=_target_chain_count_from_context(
                context,
                search_payload,
            ),
            backend=(
                search_payload.get("backend", {})
                if isinstance(search_payload, Mapping)
                else {}
            ),
        )
        evidence = _selection_evidence(
            ranked,
            selected,
            plan,
            backend=(
                search_payload.get("backend", {})
                if isinstance(search_payload, Mapping)
                else {}
            ),
        )
        reason = "highest_aggregated_root_ranking"
        return StepResult(
            outputs={
                SELECTED_ACTION_ARTIFACT: action,
                SELECTED_PLAN_ARTIFACT: plan,
                SELECTION_EVIDENCE_ARTIFACT: evidence,
            },
            candidate_count=len(ranked),
            selection_reason=reason,
        )


class EmitDecisionTraceStep(_DeferredDeepChainStep):
    implementation_ticket = "PUYO-187"
    contract = DecisionStepContract(
        step_id="emit_decision_trace",
        requires=(
            SELECTED_ACTION_ARTIFACT,
            SELECTED_PLAN_ARTIFACT,
            SELECTION_EVIDENCE_ARTIFACT,
        ),
        provides=(DECISION_OUTPUT_ARTIFACT,),
        purpose=(
            "Serialize the selected action, plan, evidence, and flow trace for "
            "policy diagnostics and replay output."
        ),
    )

    def run(self, context: DecisionContext) -> StepResult:
        action = int(context.require(SELECTED_ACTION_ARTIFACT))
        plan = context.require(SELECTED_PLAN_ARTIFACT)
        evidence = context.require(SELECTION_EVIDENCE_ARTIFACT)
        return StepResult(
            outputs={
                DECISION_OUTPUT_ARTIFACT: _decision_output(
                    action=action,
                    plan=plan,
                    evidence=evidence,
                    decision_trace=_decision_trace_payload(context),
                )
            },
            candidate_count=1,
            selection_reason="decision_payload_serialized",
        )


class DeepChainBuildFlow(DecisionFlow):
    """Default inheritable flow for ``deep_chain_builder`` decisions."""

    def __init__(
        self,
        steps: Sequence[DecisionStep] | None = None,
        *,
        search_step: RunLongRangeSearchStep | None = None,
    ) -> None:
        self._search_step = search_step or RunLongRangeSearchStep()
        super().__init__(steps=steps)

    def default_steps(self) -> Sequence[DecisionStep]:
        return (
            NormalizeObservationStep(),
            CompleteVisibleQueueScenariosStep(),
            EnumerateRootPlacementsStep(),
            self._search_step,
            AggregateScenarioScoresStep(),
            SelectPlacementStep(),
            EmitDecisionTraceStep(),
        )


class DeepChainBuilderPolicy:
    """Headless placement policy around an injectable ``DeepChainBuildFlow``."""

    policy_id = DEEP_CHAIN_BUILDER_POLICY_ID

    def __init__(
        self,
        profile: str | DeepChainBuilderProfile = "reference",
        *,
        flow: DecisionFlow | None = None,
        config: DeepChainBuilderConfig | None = None,
        config_path: str | Path = DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH,
        backend: str | None = None,
        search_backend: LongHorizonSearchBackend | None = None,
        backend_config: LongHorizonBackendConfig | None = None,
        backend_config_path: str | Path = DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH,
        target_chain_count: int = DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
    ) -> None:
        self.config = config or load_deep_chain_builder_config(config_path)
        self.profile = (
            profile
            if isinstance(profile, DeepChainBuilderProfile)
            else self.config.profile(profile)
        )
        self.backend_config = backend_config or load_long_horizon_backend_config(
            backend_config_path
        )
        self.backend_mode = str(backend or self.backend_config.default_backend)
        self.target_chain_count = _validate_target_chain_count(target_chain_count)
        if self.backend_mode not in LONG_HORIZON_BACKEND_CHOICES:
            raise ValueError(f"unsupported deep-chain backend: {self.backend_mode}")
        canonical = self.backend_mode == "native" or (
            self.backend_config.is_canonical_profile(self.profile.name)
            and self.backend_mode == "auto"
        )
        allow_auto_fallback = (
            self.backend_mode == "auto"
            and self.backend_config.allows_auto_fallback(self.profile.name)
        )
        resolved_backend = search_backend
        if flow is None and resolved_backend is None:
            resolved_backend = make_long_horizon_search_backend(
                self.backend_mode,
                profile_name=self.profile.name,
                config=self.backend_config,
            )
        self.search_backend = resolved_backend
        search_config_sha256 = (
            file_sha256(config_path)
            if config is None
            else semantic_sha256(self.config.to_dict())
        )
        backend_config_sha256 = (
            file_sha256(backend_config_path)
            if backend_config is None
            else semantic_sha256(self.backend_config.to_dict())
        )
        if flow is None:
            if resolved_backend is None:
                raise RuntimeError("deep-chain search backend was not initialized")
            flow = DeepChainBuildFlow(
                search_step=RunLongRangeSearchStep(
                    resolved_backend,
                    evaluator_config=load_chain_structure_config(),
                    evaluator_config_sha256=file_sha256(
                        DEFAULT_CHAIN_STRUCTURE_CONFIG_PATH
                    ),
                    search_config_version=self.config.config_version,
                    search_config_sha256=search_config_sha256,
                    backend_config=self.backend_config,
                    backend_config_sha256=backend_config_sha256,
                    canonical=canonical,
                    allow_auto_fallback=allow_auto_fallback,
                )
            )
        self.flow = flow
        self.last_context: DecisionContext | None = None
        self._decision_count = 0
        self._last_plan: dict[str, Any] | None = None

    def reset(self) -> None:
        self.last_context = None
        self._decision_count = 0
        self._last_plan = None

    def decide(
        self,
        observation: Mapping[str, Any],
        info: Mapping[str, Any],
    ) -> DecisionContext:
        self._decision_count += 1
        visible_input = build_visible_runtime_input(observation, info)
        artifacts: dict[str, Any] = {
            RUNTIME_INPUT_ARTIFACT: visible_input,
            TARGET_CHAIN_COUNT_ARTIFACT: self.target_chain_count,
        }
        if self._last_plan is not None:
            artifacts[PREVIOUS_PLAN_ARTIFACT] = copy.deepcopy(self._last_plan)
        context = DecisionContext(
            decision_id=f"{self.policy_id}-decision-{self._decision_count:08d}",
            profile=self.profile,
            artifacts=artifacts,
        )
        # Native contract failures fail closed. Other evaluator/plugin failures
        # retain the pre-existing deterministic legal fallback.
        try:
            result = self.flow.execute(context)
            _require_legal_selected_action(result, visible_input)
        except DeepChainNativeError:
            # Explicit native and canonical auto modes fail closed. The auto
            # router has already handled any permitted smoke-only rollback.
            raise
        except Exception as exc:
            # A native/auto decision must never be converted into the legacy
            # legal-action fallback, including adapter contract failures that
            # are not extension error types. Auto's only permitted rollback is
            # handled inside AutoLongHorizonSearchBackend and is diagnostic.
            if self.backend_mode != "python":
                raise
            result = _fallback_context(context, visible_input, exc)
        result = _finalize_decision_output(result)
        self.last_context = result
        plan = result.artifacts.get(SELECTED_PLAN_ARTIFACT)
        if isinstance(plan, Mapping):
            self._last_plan = copy.deepcopy(dict(plan))
        return result

    def select_action(
        self,
        observation: Mapping[str, Any],
        info: Mapping[str, Any],
    ) -> int:
        result = self.decide(observation, info)
        return int(result.require(SELECTED_ACTION_ARTIFACT))

    @property
    def tactical_diagnostics(self) -> dict[str, Any]:
        context = self.last_context
        if context is None:
            return {
                "schema_version": DEEP_CHAIN_DIAGNOSTICS_SCHEMA_VERSION,
                "policy_id": self.policy_id,
                "profile": self.profile.to_dict(),
                "backend": self._backend_description(),
                "target_chain_count": self.target_chain_count,
                "decision_trace": {},
                "selected_action": None,
                "plan_id": "",
                "plan": {},
                "replan_reason": "",
                "fallback": {"used": False, "reason": None, "detail": ""},
            }
        return copy.deepcopy(_policy_diagnostics(context, self.profile))

    def _backend_description(self) -> dict[str, Any]:
        if self.search_backend is None:
            return {
                "requested_backend": self.backend_mode,
                "backend": "custom_flow",
                "fallback": {"used": False, "reason": None, "detail": ""},
            }
        return _json_ready(self.search_backend.describe())


def _selection_evidence(
    ranked: Sequence[Mapping[str, Any]],
    selected: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    backend: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    scenario_aggregates = []
    for value in ranked:
        evidence = value.get("evidence")
        evidence_payload = (
            evidence.to_dict() if hasattr(evidence, "to_dict") else evidence
        )
        scenario_aggregates.append(
            {
                "root_action": int(value["root_action"]),
                "ranking_key": _json_ready(value.get("ranking_key", ())),
                "score_breakdown": _json_ready(value.get("score_breakdown", {})),
                "evidence": _json_ready(evidence_payload),
                "search_diagnostics": _json_ready(value.get("search_diagnostics", {})),
            }
        )
    representative = selected.get("representative", {})
    representative_summary = {
        key: _json_ready(representative.get(key))
        for key in (
            "scenario_id",
            "sample_id",
            "actions",
            "queue_digest",
            "root_state_fingerprint",
            "final_state_fingerprint",
            "trajectory_source",
        )
        if isinstance(representative, Mapping) and key in representative
    }
    return {
        "schema_version": DEEP_CHAIN_SELECTION_SCHEMA_VERSION,
        "candidate_count": len(ranked),
        "selection_reason": "highest_aggregated_root_ranking",
        "selected_root_action": int(selected["root_action"]),
        "selected_ranking_key": _json_ready(selected.get("ranking_key", ())),
        "selected_score_breakdown": _json_ready(selected.get("score_breakdown", {})),
        "selected_representative": representative_summary,
        "scenario_aggregation": scenario_aggregates,
        "plan_id": str(plan.get("plan_id", "")),
        "target_chain_count": int(
            plan.get("objective", {}).get(
                "minimum_chain_count",
                DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
            )
        ),
        "backend": _json_ready(backend or {}),
        "fallback": {"used": False, "reason": None, "detail": ""},
    }


def _selected_plan(
    profile: Any,
    selected: Mapping[str, Any],
    *,
    previous_plan: Any = None,
    target_chain_count: int = DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
    backend: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target_chain_count = _validate_target_chain_count(target_chain_count)
    representative = selected.get("representative")
    if not isinstance(representative, Mapping):
        raise TypeError("selected root requires a representative mapping")
    raw_steps = representative.get("steps")
    if not isinstance(raw_steps, Sequence) or not raw_steps:
        raise ValueError("selected representative requires at least one plan step")
    steps = [_json_ready(step) for step in raw_steps]
    action = int(selected["root_action"])
    if not isinstance(steps[0], Mapping) or int(steps[0]["action"]) != action:
        raise ValueError("plan step 1 must match the selected policy action")

    profile_key = str(getattr(profile, "profile_id", DEEP_CHAIN_BUILDER_POLICY_ID))
    profile_name = str(getattr(profile, "name", "deep_chain_builder"))
    profile_id = int(hashlib.sha256(profile_key.encode("utf-8")).hexdigest()[:8], 16)
    plan_basis = {
        "schema_version": N_TURN_PLAN_SCHEMA_VERSION,
        "profile_id": profile_key,
        "selected_root_action": action,
        "scenario_id": representative.get("scenario_id"),
        "queue_digest": representative.get("queue_digest"),
        "root_state_fingerprint": representative.get("root_state_fingerprint"),
        "steps": [
            {
                "action": step.get("action"),
                "known_tsumo": step.get("known_tsumo"),
                "scenario_id": step.get("scenario_id"),
                "tsumo": step.get("tsumo"),
                "state_fingerprint": step.get("state_fingerprint"),
            }
            for step in steps
        ],
    }
    plan_id = _stable_payload_digest(plan_basis, prefix="deep-chain-plan-v1")[:16]
    previous_id = (
        str(previous_plan.get("plan_id", ""))
        if isinstance(previous_plan, Mapping)
        else ""
    )
    if not previous_id:
        replan_reason = "initial_plan"
    elif previous_id == plan_id:
        replan_reason = "plan_unchanged"
    else:
        replan_reason = "new_observation"

    known_steps = sum(bool(step.get("known_tsumo")) for step in steps)
    predicted_chain_counts = [
        int(step.get("predicted_chain_count", 0)) for step in steps
    ]
    predicted_scores = [int(step.get("predicted_score", 0)) for step in steps]
    predicted_attacks = [int(step.get("predicted_attack", 0)) for step in steps]
    profile_payload = (
        profile.to_dict()
        if hasattr(profile, "to_dict")
        else {"profile_id": profile_key}
    )
    return {
        "schema_version": N_TURN_PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "profile_id": profile_id,
        "profile_key": profile_key,
        "profile_name": profile_name,
        "strategy": DEEP_CHAIN_BUILDER_POLICY_ID,
        "max_steps": int(getattr(profile, "depth", len(steps))),
        "visible_steps": int(known_steps),
        "update_reason": replan_reason,
        "replan_reason": replan_reason,
        "replan_conditions": [
            {
                "reason": "opponent_event",
                "detail": "opponent score, chain, or incoming attack changed",
            },
            {
                "reason": "incoming_attack_landed",
                "detail": "reserved ojama landed before the plan was consumed",
            },
            {
                "reason": "new_observation",
                "detail": "a fresh public observation always triggers a new search",
            },
            {
                "reason": "search_result_changed",
                "detail": "the selected trajectory changed its deterministic plan id",
            },
            {
                "reason": "input_failure",
                "detail": "the selected placement is no longer legal",
            },
        ],
        "objective": {
            "kind": "deep_chain_construction",
            "minimum_chain_count": target_chain_count,
        },
        "search_control": {
            **_json_ready(profile_payload),
            "backend": _backend_plan_control(backend),
        },
        "planner_request": {},
        "planner_latency_overrun": False,
        "scenario_id": representative.get("scenario_id"),
        "sample_id": representative.get("sample_id"),
        "queue_digest": representative.get("queue_digest"),
        "root_state_fingerprint": representative.get("root_state_fingerprint"),
        "selected_root_action": action,
        "selection_reason": "highest_aggregated_root_ranking",
        "prediction_summary": {
            "maximum_chain_count": max(predicted_chain_counts, default=0),
            "cumulative_score": sum(predicted_scores),
            "cumulative_attack": sum(predicted_attacks),
            "final_state_fingerprint": representative.get("final_state_fingerprint"),
        },
        "attack_summary": {
            "initial_score_carry": 0,
            "final_score_carry": 0,
            "initial_incoming_attack": 0,
            "incoming_remaining": 0,
            "generated": sum(predicted_attacks),
            "canceled": 0,
            "outgoing": sum(predicted_attacks),
        },
        "steps": steps,
    }


def _decision_output(
    *,
    action: int,
    plan: Any,
    evidence: Any,
    decision_trace: Mapping[str, Any],
) -> dict[str, Any]:
    plan_payload = _json_ready(plan) if isinstance(plan, Mapping) else {}
    evidence_payload = _json_ready(evidence) if isinstance(evidence, Mapping) else {}
    return {
        "schema_version": DEEP_CHAIN_DIAGNOSTICS_SCHEMA_VERSION,
        "policy_id": DEEP_CHAIN_BUILDER_POLICY_ID,
        "action": int(action),
        "plan_id": str(plan_payload.get("plan_id", "")),
        "plan": plan_payload,
        "target_chain_count": int(
            plan_payload.get("objective", {}).get(
                "minimum_chain_count",
                DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
            )
        ),
        "selection_evidence": evidence_payload,
        "decision_trace": _json_ready(decision_trace),
        "fallback": evidence_payload.get(
            "fallback", {"used": False, "reason": None, "detail": ""}
        ),
    }


def _finalize_decision_output(context: DecisionContext) -> DecisionContext:
    action = int(context.require(SELECTED_ACTION_ARTIFACT))
    plan = context.artifacts.get(SELECTED_PLAN_ARTIFACT, {})
    evidence = context.artifacts.get(SELECTION_EVIDENCE_ARTIFACT, {})
    artifacts = dict(context.artifacts)
    artifacts[DECISION_OUTPUT_ARTIFACT] = _decision_output(
        action=action,
        plan=plan,
        evidence=evidence,
        decision_trace=_decision_trace_payload(context),
    )
    return DecisionContext(
        decision_id=context.decision_id,
        profile=context.profile,
        artifacts=artifacts,
        trace_entries=context.trace_entries,
    )


def _require_legal_selected_action(
    context: DecisionContext,
    visible_input: VisibleRuntimeInput,
) -> None:
    action = int(context.require(SELECTED_ACTION_ARTIFACT))
    if visible_input.action_mask:
        if (
            not 0 <= action < len(visible_input.action_mask)
            or not visible_input.action_mask[action]
        ):
            raise ValueError("deep-chain flow selected an illegal placement action")
        return
    legal = legal_action_indices(_compact_state_from_observation(visible_input))
    if action not in legal:
        raise ValueError("deep-chain flow selected an illegal placement action")


def _fallback_context(
    context: DecisionContext,
    visible_input: VisibleRuntimeInput,
    error: Exception,
) -> DecisionContext:
    reason = _fallback_reason(error)
    action = _deterministic_fallback_action(visible_input)
    representative = _fallback_representative(visible_input, action)
    selected = {
        "root_action": action,
        "representative": representative,
    }
    plan = _selected_plan(
        context.profile,
        selected,
        previous_plan=context.artifacts.get(PREVIOUS_PLAN_ARTIFACT),
        target_chain_count=context.artifacts.get(
            TARGET_CHAIN_COUNT_ARTIFACT,
            DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
        ),
    )
    plan["selection_reason"] = f"deterministic_fallback:{reason}"
    detail = f"{type(error).__name__}: {error}".strip()
    fallback = {"used": True, "reason": reason, "detail": detail}
    evidence = {
        "schema_version": DEEP_CHAIN_SELECTION_SCHEMA_VERSION,
        "candidate_count": int(visible_input.legal_action_count),
        "selection_reason": f"deterministic_fallback:{reason}",
        "selected_root_action": action,
        "selected_ranking_key": [],
        "selected_score_breakdown": {},
        "selected_representative": {
            key: _json_ready(representative.get(key))
            for key in (
                "scenario_id",
                "actions",
                "root_state_fingerprint",
                "final_state_fingerprint",
                "trajectory_source",
            )
        },
        "scenario_aggregation": [],
        "plan_id": plan["plan_id"],
        "fallback": fallback,
    }
    output = _decision_output(
        action=action,
        plan=plan,
        evidence=evidence,
        decision_trace={},
    )
    outputs = {
        SELECTED_ACTION_ARTIFACT: action,
        SELECTED_PLAN_ARTIFACT: plan,
        SELECTION_EVIDENCE_ARTIFACT: evidence,
        DECISION_OUTPUT_ARTIFACT: output,
    }
    trace_entry = DecisionTraceEntry(
        index=0,
        step_id="deterministic_fallback",
        step_type="DeterministicFallback",
        input_summary=visible_input.summary(),
        output_keys=tuple(outputs),
        candidate_count=visible_input.legal_action_count,
        selection_reason=f"deterministic_fallback:{reason}",
        elapsed_seconds=0.0,
    )
    return context.with_step_result(StepResult(outputs=outputs), trace_entry)


def _fallback_reason(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "search_timeout"
    if isinstance(error, NotImplementedError):
        return "search_unavailable"
    if isinstance(error, (KeyError, TypeError, ValueError)):
        return "invalid_search_result"
    return "search_failure"


def _deterministic_fallback_action(visible_input: VisibleRuntimeInput) -> int:
    if visible_input.action_mask:
        for action, allowed in enumerate(visible_input.action_mask):
            if allowed:
                try:
                    action_to_placement(action)
                except ValueError:
                    continue
                return int(action)
    try:
        legal = legal_action_indices(_compact_state_from_observation(visible_input))
    except (TypeError, ValueError):
        legal = ()
    return 0 if not legal else int(legal[0])


def _fallback_representative(
    visible_input: VisibleRuntimeInput,
    action: int,
) -> dict[str, Any]:
    try:
        state = _compact_state_from_observation(visible_input)
        pair = _decode_visible_pairs(visible_input.next_pairs)[0]
        result = transition(state, pair, action, capture_visuals=True)
        if not result.valid:
            raise ValueError("fallback action has no compact transition")
        step = _transition_plan_step(
            state,
            result,
            pair,
            cursor=0,
            known_tsumo=True,
            scenario_id=-1,
            reason="deterministic_fallback",
            cumulative_score=0,
            cumulative_attack=0,
        )
        root_fingerprint = _state_fingerprint(state)
        return {
            "scenario_id": -1,
            "sample_id": "deterministic-fallback",
            "actions": [int(action)],
            "queue_digest": _stable_payload_digest(
                [[pair[0].name, pair[1].name]], prefix="fallback-visible-pair"
            ),
            "root_state_fingerprint": root_fingerprint,
            "predicted_boards": [step["predicted_board"]],
            "state_fingerprints": [step["state_fingerprint"]],
            "final_state_fingerprint": step["state_fingerprint"],
            "trajectory_source": "deterministic_fallback",
            "steps": [step],
        }
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        placement = action_to_placement(action)
        return {
            "scenario_id": -1,
            "sample_id": "deterministic-fallback",
            "actions": [int(action)],
            "queue_digest": "",
            "root_state_fingerprint": "",
            "predicted_boards": [],
            "state_fingerprints": [],
            "final_state_fingerprint": "",
            "trajectory_source": "deterministic_fallback_prediction_unavailable",
            "steps": [
                {
                    "step_index": 0,
                    "action": int(action),
                    "axis_x": int(placement.axis_x),
                    "rotation": placement.rotation.name,
                    "known_tsumo": True,
                    "scenario": "fallback",
                    "scenario_id": -1,
                    "tsumo": [],
                    "valid": True,
                    "predicted_chain_count": 0,
                    "predicted_score": 0,
                    "predicted_attack": 0,
                    "cumulative_score": 0,
                    "cumulative_attack": 0,
                    "danger": 0.0,
                    "predicted_board": [],
                    "placement_cells": [],
                    "state_fingerprint": "",
                    "chains": [],
                    "reason": "deterministic_fallback_prediction_unavailable",
                }
            ],
        }


def _policy_diagnostics(context: DecisionContext, profile: Any) -> dict[str, Any]:
    plan = context.artifacts.get(SELECTED_PLAN_ARTIFACT, {})
    evidence = context.artifacts.get(SELECTION_EVIDENCE_ARTIFACT, {})
    evidence_payload = _json_ready(evidence) if isinstance(evidence, Mapping) else {}
    plan_payload = _json_ready(plan) if isinstance(plan, Mapping) else {}
    search_payload = context.artifacts.get(SCENARIO_SEARCH_RESULTS_ARTIFACT)
    search_diagnostics: dict[str, Any] = {}
    if isinstance(search_payload, Mapping):
        result = search_payload.get("result")
        counters = getattr(result, "counters", None)
        search_diagnostics = {
            "schema_version": search_payload.get("schema_version"),
            "scenario_ids": _json_ready(search_payload.get("scenario_ids", ())),
            "root_ids": _json_ready(search_payload.get("root_ids", ())),
            "target_chain_count": int(
                search_payload.get(
                    "target_chain_count",
                    DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
                )
            ),
            "deterministic_digest": getattr(result, "deterministic_digest", ""),
            "counters": (counters.to_dict() if hasattr(counters, "to_dict") else {}),
            "backend": _json_ready(search_payload.get("backend", {})),
        }
    fallback = evidence_payload.get(
        "fallback", {"used": False, "reason": None, "detail": ""}
    )
    return {
        "schema_version": DEEP_CHAIN_DIAGNOSTICS_SCHEMA_VERSION,
        "policy_id": DEEP_CHAIN_BUILDER_POLICY_ID,
        "profile": _json_ready(profile.to_dict()),
        "target_chain_count": _validate_target_chain_count(
            context.artifacts.get(
                TARGET_CHAIN_COUNT_ARTIFACT,
                DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
            )
        ),
        "decision_trace": _decision_trace_payload(context),
        "selected_action": int(context.require(SELECTED_ACTION_ARTIFACT)),
        "candidate_count": evidence_payload.get("candidate_count"),
        "selection_reason": evidence_payload.get("selection_reason"),
        "scenario_aggregation": evidence_payload.get("scenario_aggregation", []),
        "selection_evidence": evidence_payload,
        "search": _json_ready(search_diagnostics),
        "backend": _json_ready(
            search_payload.get("backend", {})
            if isinstance(search_payload, Mapping)
            else {}
        ),
        "plan_id": str(plan_payload.get("plan_id", "")),
        "plan": plan_payload,
        "replan_reason": str(plan_payload.get("replan_reason", "")),
        "fallback": fallback,
        "decision_output": _json_ready(
            context.artifacts.get(DECISION_OUTPUT_ARTIFACT, {})
        ),
    }


def _validate_target_chain_count(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError("deep-chain target chain count must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("deep-chain target chain count must be an integer") from exc
    if parsed != value:
        raise ValueError("deep-chain target chain count must be an integer")
    if not 1 <= parsed <= MAX_DEEP_CHAIN_TARGET_CHAIN_COUNT:
        raise ValueError(
            "deep-chain target chain count must be in "
            f"[1, {MAX_DEEP_CHAIN_TARGET_CHAIN_COUNT}]"
        )
    return parsed


def _target_chain_count_from_context(
    context: DecisionContext,
    search_payload: Any,
) -> int:
    configured = _validate_target_chain_count(
        context.artifacts.get(
            TARGET_CHAIN_COUNT_ARTIFACT,
            DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
        )
    )
    if not isinstance(search_payload, Mapping) or "target_chain_count" not in search_payload:
        return configured
    searched = _validate_target_chain_count(search_payload["target_chain_count"])
    if searched != configured:
        raise ValueError("deep-chain plan target differs from the search target")
    return searched


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if hasattr(value, "to_dict"):
        return _json_ready(value.to_dict())
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return repr(value)


def _stable_payload_digest(value: Any, *, prefix: str) -> str:
    payload = json.dumps(
        _json_ready(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(prefix.encode("utf-8") + b":" + payload).hexdigest()


def _backend_request_id(decision_id: str) -> int:
    digest = hashlib.sha256(str(decision_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little")


def _backend_plan_control(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return _json_ready(
        {
            key: value.get(key)
            for key in (
                "schema_version",
                "requested_backend",
                "backend",
                "canonical",
                "request_id",
                "execution_mode",
                "configuration",
                "provenance",
                "fallback",
            )
            if key in value
        }
    )


def _decision_trace_payload(context: DecisionContext) -> dict[str, Any]:
    payload = context.trace.to_dict()
    search = context.artifacts.get(SCENARIO_SEARCH_RESULTS_ARTIFACT)
    if isinstance(search, Mapping):
        payload["backend"] = _json_ready(search.get("backend", {}))
    return payload


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
    if isinstance(value, Mapping):
        return {
            str(key): _plain_nested(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
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


def _compact_state_from_observation(
    observation: VisibleRuntimeInput,
) -> CompactSearchState:
    score = max(0, int(observation.score))
    last_chain_end_score = max(
        0,
        min(score, int(observation.last_chain_end_score)),
    )
    return _compact_state_from_board(
        observation.board,
        score=score,
        last_chain_end_score=last_chain_end_score,
    )


def _compact_state_from_board(
    board: Any,
    *,
    score: int = 0,
    last_chain_end_score: int = 0,
) -> CompactSearchState:
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
    return CompactSearchState(
        planes=tuple(planes),
        score=max(0, int(score)),
        last_chain_end_score=max(
            0,
            min(int(score), int(last_chain_end_score)),
        ),
    )


def _state_fingerprint(state: CompactSearchState) -> str:
    return hashlib.sha256(state.to_bytes()).hexdigest()[:24]


def _transition_plan_step(
    state: CompactSearchState,
    result: Any,
    pair: Sequence[PuyoColor],
    *,
    cursor: int,
    known_tsumo: bool,
    scenario_id: int,
    reason: str,
    cumulative_score: int,
    cumulative_attack: int,
) -> dict[str, Any]:
    placement_cells = []
    for y, row in enumerate(result.placement_board):
        for x, color in enumerate(row):
            if color != PuyoColor.EMPTY and state.color_at(x, y) == PuyoColor.EMPTY:
                placement_cells.append({"x": int(x), "y": int(y), "color": color.name})
    predicted_attack = max(0, int(result.attack_score_delta) // 70)
    next_cumulative_score = cumulative_score + int(result.score_delta)
    next_cumulative_attack = cumulative_attack + predicted_attack
    predicted_board = [
        [cell.name for cell in row] for row in result.state.to_color_grid()
    ]
    maximum_height = max(result.state.column_heights, default=0)
    danger = min(1.0, maximum_height / float(max(1, GRID_HEIGHT - 1)))
    return {
        "step_index": int(cursor),
        "action": int(result.action_id),
        "axis_x": int(result.action.axis_x),
        "axis_y": None if result.axis_y is None else int(result.axis_y),
        "rotation": result.action.rotation.name,
        "known_tsumo": bool(known_tsumo),
        "scenario": "visible" if known_tsumo else "unknown_scenario",
        "scenario_id": int(scenario_id),
        "scenario_label": f"scenario-{scenario_id}",
        "tsumo": [pair[0].name, pair[1].name],
        "valid": bool(result.valid),
        "predicted_chain_count": int(result.chain_count),
        "predicted_score": int(result.score_delta),
        "predicted_attack": predicted_attack,
        "cumulative_score": int(next_cumulative_score),
        "cumulative_attack": int(next_cumulative_attack),
        "attack_score_delta": int(result.attack_score_delta),
        "score_carry_before": 0,
        "score_carry_after": 0,
        "attack_generated": predicted_attack,
        "attack_canceled": 0,
        "attack_outgoing": predicted_attack,
        "incoming_remaining": 0,
        "all_clear_achieved": bool(result.all_clear_achieved),
        "all_clear_bonus_pending": bool(result.all_clear_bonus_pending),
        "all_clear_bonus_consumed": bool(result.all_clear_bonus_consumed),
        "all_clear_bonus_score": int(result.all_clear_bonus_score),
        "danger": float(danger),
        "objective_result": {
            "achieved": int(result.chain_count) >= 6,
            "possible_by_deadline": not bool(result.game_over),
            "miss_reasons": (
                [] if int(result.chain_count) >= 6 else ["target_chain_not_fired"]
            ),
            "surplus_attack": 0,
            "score_delta": int(result.score_delta),
            "chain_delta": int(result.chain_count) - 6,
            "deadline_missed": False,
            "danger_excess": 0.0,
            "time_overrun_ticks": 0,
            "response_capacity": 0,
            "incoming_coverage": 0.0,
            "trigger_preserved": True,
            "immediate_fire": int(result.chain_count) > 0,
        },
        "predicted_board": predicted_board,
        "placement_cells": placement_cells,
        "state_fingerprint": _state_fingerprint(result.state),
        "chains": [
            {
                "chain_index": int(chain.chain_index),
                "vanished_count": int(chain.vanished_count),
                "score": int(chain.score),
            }
            for chain in result.chains
        ],
        "game_over": bool(result.game_over),
        "reason": reason,
    }


def _representative_payload(
    result: Any, root_action: int, root_state: CompactSearchState
) -> dict[str, Any] | None:
    node = result.representatives.get(int(root_action))
    scenario_id = (
        int(node.scenario_id)
        if node is not None
        else int(result.scenario_sequences[0].scenario_id)
    )
    sequence = next(
        (item for item in result.scenario_sequences if item.scenario_id == scenario_id),
        None,
    )
    if sequence is None:
        return None
    path = tuple(node.path) if node is not None else (int(root_action),)
    trajectory_source = (
        "representative_search_trajectory"
        if node is not None
        else "root_only_recovery_trajectory"
    )
    state = root_state
    steps = []
    cumulative_score = 0
    cumulative_attack = 0
    for cursor, action in enumerate(path):
        pair = sequence.pair_at(cursor)
        transition_result = transition(
            state,
            pair,
            int(action),
            capture_visuals=True,
        )
        if not transition_result.valid:
            break
        plan_step = _transition_plan_step(
            state,
            transition_result,
            pair,
            cursor=cursor,
            known_tsumo=cursor < sequence.known_pair_count,
            scenario_id=int(sequence.scenario_id),
            reason=trajectory_source,
            cumulative_score=cumulative_score,
            cumulative_attack=cumulative_attack,
        )
        steps.append(plan_step)
        cumulative_score = int(plan_step["cumulative_score"])
        cumulative_attack = int(plan_step["cumulative_attack"])
        state = transition_result.state
    if not steps:
        return None
    return {
        "scenario_id": int(sequence.scenario_id),
        "sample_id": sequence.sample_id,
        "actions": [int(step["action"]) for step in steps],
        "queue_digest": sequence.queue_digest,
        "known_pair_count": int(sequence.known_pair_count),
        "unknown_boundary_cursor": int(sequence.known_pair_count),
        "root_state_fingerprint": _state_fingerprint(root_state),
        "predicted_boards": [step["predicted_board"] for step in steps],
        "state_fingerprints": [step["state_fingerprint"] for step in steps],
        "final_state_fingerprint": steps[-1]["state_fingerprint"],
        "search_final_state_fingerprint": (
            None if node is None else node.state_fingerprint
        ),
        "trajectory_source": trajectory_source,
        "steps": steps,
    }


__all__ = [
    "AGGREGATED_ROOT_SCORES_ARTIFACT",
    "DECISION_OUTPUT_ARTIFACT",
    "DEEP_CHAIN_BENCHMARK_SCHEMA_VERSION",
    "DEEP_CHAIN_BUILDER_CONFIG_SCHEMA_VERSION",
    "DEEP_CHAIN_BUILDER_POLICY_ID",
    "DEEP_CHAIN_BUILDER_PROFILE_SCHEMA_VERSION",
    "DEEP_CHAIN_TARGET_CHAIN_CHOICES",
    "DEFAULT_DEEP_CHAIN_BUILDER_CONFIG_PATH",
    "DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT",
    "DEFAULT_LONG_HORIZON_BACKEND_CONFIG_PATH",
    "TARGET_CHAIN_COUNT_ARTIFACT",
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
