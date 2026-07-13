"""Learned tactic arbitration core for the v1.7.1 strategy manager."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import torch
    from torch import nn
except (ImportError, OSError):  # pragma: no cover - dependency guard
    torch = None
    nn = None

from agents.state_analyzer import (
    ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
    ANALYZER_INPUT_SCHEMA_VERSION,
    AnalyzerDiagnostics,
    AnalyzerInput,
    StateAnalyzer,
)
from agents.strategy_workers import (
    NTurnPlan,
    SearchProposal,
    StrategyOrchestrator,
    WorkerProfile,
    default_worker_profiles,
    profile_id_by_name,
)
from agents.v1_7_planner import (
    PLANNER_REQUEST_SCHEMA_VERSION,
    PlannerRequest,
    build_planner_request,
)
from agents.v1_7_tactics import (
    TACTIC_SCHEMA_VERSION,
    ParameterSpec,
    TacticRegistry,
    TacticSpec,
    build_tactic_diagnostics,
    load_tactic_registry,
)
from src.core.diagnostics import ALL_CLEAR_DIAGNOSTICS_SCHEMA_VERSION
from train.artifacts import (
    CHECKPOINT_SCHEMA_VERSION,
    json_digest,
    validate_checkpoint_payload,
)
from train.restore import checkpoint_state_hash


FEATURE_SCHEMA_VERSION = "puyo.v1_7_strategy_manager.features.v1"
PREVIEW_FEATURE_SCHEMA_VERSION = "puyo.v1_7_strategy_manager.preview.v1"
STRATEGY_MANAGER_DIAGNOSTICS_SCHEMA_VERSION = (
    "puyo.v1_7_strategy_manager.diagnostics.v1"
)
MODEL_FAMILY = "Adaptive Chain Manager"
MODEL_VERSION = "v1.7.1"
POLICY_TYPE = "v1_7_bootstrap_manager"
LINEAGE_NODE_ID = "model_version:v1.7.1"
PARENT_LINEAGE_NODE_ID = "model_version:v1.7.0"
BOOTSTRAP_TRAINER_NAME = "v1_7_manager_bootstrap"
CHECKPOINT_METADATA_SCHEMA_VERSION = (
    "puyo.v1_7_strategy_manager.checkpoint_metadata.v1"
)
DEFAULT_PREVIEW_TOP_K = 3
LIFECYCLE_CARRY_FEATURES = (
    "own.score_carry",
    "own.all_clear_achieved",
    "own.all_clear_bonus_pending",
    "own.all_clear_bonus_consumed",
    "opponent.score_carry",
    "opponent.all_clear_achieved",
    "opponent.all_clear_bonus_pending",
    "opponent.all_clear_bonus_consumed",
)

_PARAMETER_SECTIONS = ("objective", "constraints", "planner")
_TACTIC_TO_WORKER = {
    "build_main": "build_large",
    "prepare_response": "counter",
    "counter_or_return": "counter",
    "pressure": "punish",
    "lethal_attack": "punish",
    "all_clear": "fire_max",
    "fire_main": "fire_max",
    "survive": "survival",
}

_PLAYER_FEATURE_SUFFIXES = (
    "input.score_carry",
    "input.all_clear_achieved",
    "input.all_clear_bonus_pending",
    "input.all_clear_bonus_consumed",
    "diagnostics.danger",
    "diagnostics.vulnerability",
    "diagnostics.board_empty",
    "diagnostics.is_all_clear",
    "diagnostics.all_clear_achieved",
    "diagnostics.all_clear_bonus_pending",
    "diagnostics.all_clear_bonus_consumed",
    "diagnostics.forecast.immediate_attack",
    "diagnostics.forecast.short_attack",
    "diagnostics.forecast.turns_to_best",
    "diagnostics.forecast.main_chain.present",
    "diagnostics.forecast.main_chain.turns",
    "diagnostics.forecast.main_chain.chain_count",
    "diagnostics.forecast.main_chain.attack",
    "diagnostics.forecast.main_chain.attack_per_turn",
    "diagnostics.forecast.main_chain.is_all_clear",
    "diagnostics.forecast.main_chain.hard_to_answer",
    "diagnostics.attack_options.count",
    "diagnostics.attack_options.max_attack",
    "diagnostics.attack_options.max_attack_per_turn",
    "diagnostics.attack_options.hard_to_answer_count",
)

_PREVIOUS_PLAN_FEATURE_NAMES = (
    "previous_plan.present",
    "previous_plan.visible_steps",
    "previous_plan.max_steps",
    "previous_plan.latency_overrun",
    "previous_plan.initial_score_carry",
    "previous_plan.initial_incoming_attack",
    "previous_plan.first_step.present",
    "previous_plan.first_step.predicted_chain_count",
    "previous_plan.first_step.predicted_attack",
    "previous_plan.first_step.danger",
    "previous_plan.first_step.incoming_remaining",
    "previous_plan.first_step.all_clear_achieved",
    "previous_plan.first_step.all_clear_bonus_pending",
    "previous_plan.first_step.all_clear_bonus_consumed",
    "previous_plan.first_step.objective_achieved",
    "previous_plan.first_step.possible_by_deadline",
    "previous_plan.first_step.deadline_missed",
)

CONTEXT_FEATURE_NAMES = (
    "input.turn",
    "input.tick",
    "input.policy_deadline",
    *(f"own.{suffix}" for suffix in _PLAYER_FEATURE_SUFFIXES),
    *(f"opponent.{suffix}" for suffix in _PLAYER_FEATURE_SUFFIXES),
    "incoming.amount",
    "incoming.deadline",
    "incoming.max_return_by_deadline",
    "incoming.can_cancel",
    "incoming.can_counter",
    "incoming.counter_deficit",
    "incoming.window_count",
    *_PREVIOUS_PLAN_FEATURE_NAMES,
)

PREVIEW_FEATURE_NAMES = (
    "predicted_chain_count",
    "predicted_score",
    "predicted_attack",
    "danger",
    "latency_ratio",
    "expanded_nodes",
    "candidate_value",
    "target_attack",
    "incoming_attack",
    "deadline",
    "objective.achieved",
    "objective.possible_by_deadline",
    "objective.surplus_attack",
    "objective.deadline_missed",
    "objective.danger_excess",
    "first_step.present",
    "first_step.attack_generated",
    "first_step.attack_canceled",
    "first_step.attack_outgoing",
    "first_step.incoming_remaining",
    "first_step.all_clear_achieved",
    "first_step.all_clear_bonus_pending",
    "first_step.all_clear_bonus_consumed",
)


def build_v1_7_checkpoint_metadata(
    registry: TacticRegistry,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Return the complete runtime compatibility snapshot for a checkpoint."""

    return {
        "schema_version": CHECKPOINT_METADATA_SCHEMA_VERSION,
        "policy_type": POLICY_TYPE,
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "lineage": {
            "node_id": LINEAGE_NODE_ID,
            "parent_node_id": PARENT_LINEAGE_NODE_ID,
            "training_run_id": str(run_id),
        },
        "schemas": {
            "checkpoint": CHECKPOINT_SCHEMA_VERSION,
            "analyzer_input": ANALYZER_INPUT_SCHEMA_VERSION,
            "analyzer_diagnostics": ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
            "all_clear_diagnostics": ALL_CLEAR_DIAGNOSTICS_SCHEMA_VERSION,
            "tactic_registry": TACTIC_SCHEMA_VERSION,
            "tactic_registry_version": registry.registry_version,
            "planner_request": PLANNER_REQUEST_SCHEMA_VERSION,
            "strategy_features": FEATURE_SCHEMA_VERSION,
            "planner_preview_features": PREVIEW_FEATURE_SCHEMA_VERSION,
            "strategy_diagnostics": STRATEGY_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
        },
    }


@dataclass(frozen=True)
class StrategyFeatureContract:
    """Checkpoint-visible feature order and shape contract."""

    registry_version: str
    tactic_ids: tuple[str, ...]
    context_feature_names: tuple[str, ...]
    tactic_feature_names: tuple[str, ...]
    parameter_signatures: tuple[tuple[str, ...], ...]
    parameter_logit_counts: tuple[int, ...]
    schema_version: str = FEATURE_SCHEMA_VERSION
    preview_schema_version: str = PREVIEW_FEATURE_SCHEMA_VERSION
    dtype: str = "float32"

    @property
    def context_dim(self) -> int:
        return len(self.context_feature_names)

    @property
    def tactic_dim(self) -> int:
        return len(self.tactic_feature_names)

    @property
    def preview_dim(self) -> int:
        return len(PREVIEW_FEATURE_NAMES)

    @property
    def max_parameter_logits(self) -> int:
        return max(self.parameter_logit_counts, default=0)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "preview_schema_version": self.preview_schema_version,
            "dtype": self.dtype,
            "registry_version": self.registry_version,
            "tactic_ids": list(self.tactic_ids),
            "context_feature_names": list(self.context_feature_names),
            "tactic_feature_names": list(self.tactic_feature_names),
            "preview_feature_names": list(PREVIEW_FEATURE_NAMES),
            "context_dim": self.context_dim,
            "tactic_dim": self.tactic_dim,
            "preview_dim": self.preview_dim,
            "parameter_signatures": [list(items) for items in self.parameter_signatures],
            "parameter_logit_counts": list(self.parameter_logit_counts),
            "max_parameter_logits": self.max_parameter_logits,
        }

    def validate_metadata(self, metadata: Mapping[str, Any]) -> None:
        expected = self.to_metadata()
        for key, expected_value in expected.items():
            actual_value = metadata.get(key)
            if actual_value != expected_value:
                raise ValueError(
                    "strategy feature contract mismatch for "
                    f"{key}: expected {expected_value!r}, got {actual_value!r}"
                )

    @classmethod
    def from_registry(cls, registry: TacticRegistry) -> "StrategyFeatureContract":
        tactic_ids = tuple(tactic.identity.tactic_id for tactic in registry.tactics)
        parameter_names = sorted(
            {
                (section, name)
                for tactic in registry.tactics
                for section in _PARAMETER_SECTIONS
                for name in tactic.parameters[section]
            }
        )
        tactic_feature_names = (
            *(f"identity.{tactic_id}" for tactic_id in tactic_ids),
            "eligible",
            "previous_selected",
            "candidate_pass_ratio",
            "active_context_ratio",
            "fallback.present",
            "fallback.registry_index",
            *(
                name
                for section, parameter_name in parameter_names
                for name in (
                    f"parameter.{section}.{parameter_name}.present",
                    f"parameter.{section}.{parameter_name}.default",
                )
            ),
        )
        signatures = tuple(_parameter_signatures(tactic) for tactic in registry.tactics)
        counts = tuple(_parameter_logit_count(tactic) for tactic in registry.tactics)
        return cls(
            registry_version=registry.registry_version,
            tactic_ids=tactic_ids,
            context_feature_names=tuple(CONTEXT_FEATURE_NAMES),
            tactic_feature_names=tuple(tactic_feature_names),
            parameter_signatures=signatures,
            parameter_logit_counts=counts,
        )


@dataclass(frozen=True)
class EncodedStrategyFeatures:
    contract: StrategyFeatureContract
    context: tuple[float, ...]
    tactics: tuple[tuple[float, ...], ...]
    eligibility_mask: tuple[bool, ...]
    candidates: tuple[Mapping[str, Any], ...]

    @property
    def context_values(self) -> dict[str, float]:
        return dict(zip(self.contract.context_feature_names, self.context))


class V17StrategyFeatureEncoder:
    """Encode Analyzer, tactic, and previous-plan data without raw board cells."""

    def __init__(self, registry: TacticRegistry):
        self.registry = registry
        self.contract = StrategyFeatureContract.from_registry(registry)
        self._parameter_names = sorted(
            {
                (section, name)
                for tactic in registry.tactics
                for section in _PARAMETER_SECTIONS
                for name in tactic.parameters[section]
            }
        )

    def encode(
        self,
        analyzer_input: AnalyzerInput,
        analyzer_diagnostics: AnalyzerDiagnostics,
        *,
        previous_plan: NTurnPlan | None = None,
        previous_tactic_id: str | None = None,
    ) -> EncodedStrategyFeatures:
        tactic_payload = build_tactic_diagnostics(
            self.registry,
            analyzer_input,
            analyzer_diagnostics,
        )
        context = self._context_features(
            analyzer_input,
            analyzer_diagnostics,
            previous_plan,
        )
        candidates = tuple(tactic_payload["candidates"])
        tactics = tuple(
            self._tactic_features(tactic, candidate, previous_tactic_id)
            for tactic, candidate in zip(self.registry.tactics, candidates)
        )
        if len(context) != self.contract.context_dim:
            raise RuntimeError("strategy context feature count does not match contract")
        if any(len(values) != self.contract.tactic_dim for values in tactics):
            raise RuntimeError("strategy tactic feature count does not match contract")
        return EncodedStrategyFeatures(
            contract=self.contract,
            context=context,
            tactics=tactics,
            eligibility_mask=tuple(bool(candidate["eligible"]) for candidate in candidates),
            candidates=candidates,
        )

    def _context_features(
        self,
        analyzer_input: AnalyzerInput,
        diagnostics: AnalyzerDiagnostics,
        previous_plan: NTurnPlan | None,
    ) -> tuple[float, ...]:
        values = [
            _unit(analyzer_input.turn, 200.0),
            _unit(analyzer_input.tick, 20_000.0),
            _unit(analyzer_input.policy_deadline, 5_000.0),
        ]
        values.extend(_player_features(analyzer_input.own, diagnostics.own))
        values.extend(_player_features(analyzer_input.opponent, diagnostics.opponent))
        incoming = diagnostics.incoming
        values.extend(
            (
                _unit(incoming.amount, 180.0),
                _unit(incoming.deadline, 8.0),
                _unit(incoming.max_return_by_deadline, 180.0),
                _flag(incoming.can_cancel),
                _flag(incoming.can_counter),
                _signed_unit(incoming.counter_deficit, 180.0),
                _unit(len(incoming.windows), 8.0),
            )
        )
        values.extend(_previous_plan_features(previous_plan))
        return tuple(values)

    def _tactic_features(
        self,
        tactic: TacticSpec,
        candidate: Mapping[str, Any],
        previous_tactic_id: str | None,
    ) -> tuple[float, ...]:
        tactic_id = tactic.identity.tactic_id
        values = [
            _flag(candidate_id == tactic_id)
            for candidate_id in self.contract.tactic_ids
        ]
        group_results = candidate.get("candidate_condition_groups", ())
        pass_ratio = (
            1.0
            if not group_results
            else sum(bool(group["passed"]) for group in group_results) / len(group_results)
        )
        active_contexts = candidate.get("active_contexts", ())
        fallback_id = tactic.fallback.get("tactic_id")
        fallback_present = fallback_id in self.contract.tactic_ids
        fallback_index = (
            self.contract.tactic_ids.index(str(fallback_id))
            if fallback_present
            else 0
        )
        values.extend(
            (
                _flag(candidate["eligible"]),
                _flag(previous_tactic_id == tactic_id),
                float(pass_ratio),
                _unit(len(active_contexts), max(1, len(tactic.contexts))),
                _flag(fallback_present),
                _unit(fallback_index, max(1, len(self.contract.tactic_ids) - 1)),
            )
        )
        for section, name in self._parameter_names:
            spec = tactic.parameters[section].get(name)
            values.append(_flag(spec is not None))
            values.append(0.0 if spec is None else _normalized_parameter(spec, spec.default))
        return tuple(values)


@dataclass(frozen=True)
class LightweightStrategyOutputs:
    proposal_logits: Any
    values: Any
    risks: Any
    parameter_logits: Any
    tactic_hidden: Any


if torch is not None:

    class V17StrategyManagerNetwork(nn.Module):
        """Shared context encoder with tactic-specific and arbitration heads."""

        def __init__(
            self,
            feature_contract: StrategyFeatureContract,
            hidden_dim: int = 128,
        ):
            super().__init__()
            self.feature_contract = feature_contract
            self.hidden_dim = int(hidden_dim)
            self.shared_encoder = nn.Sequential(
                nn.Linear(feature_contract.context_dim, self.hidden_dim),
                nn.ReLU(),
                nn.Linear(self.hidden_dim, self.hidden_dim),
                nn.ReLU(),
            )
            self.tactic_heads = nn.ModuleList(
                nn.Sequential(
                    nn.Linear(self.hidden_dim + feature_contract.tactic_dim, self.hidden_dim),
                    nn.ReLU(),
                )
                for _ in feature_contract.tactic_ids
            )
            output_dim = 3 + feature_contract.max_parameter_logits
            self.proposal_heads = nn.ModuleList(
                nn.Linear(self.hidden_dim, output_dim)
                for _ in feature_contract.tactic_ids
            )
            self.final_arbitration = nn.Sequential(
                nn.Linear(
                    self.hidden_dim + 3 + feature_contract.preview_dim,
                    self.hidden_dim,
                ),
                nn.ReLU(),
                nn.Linear(self.hidden_dim, 1),
            )

        def forward_lightweight(
            self,
            context: Any,
            tactic_features: Any,
            eligibility_mask: Any | None = None,
        ) -> LightweightStrategyOutputs:
            shared = self.shared_encoder(context.float())
            hidden_items = []
            raw_items = []
            for index, (tactic_head, proposal_head) in enumerate(
                zip(self.tactic_heads, self.proposal_heads)
            ):
                hidden = tactic_head(
                    torch.cat([shared, tactic_features[:, index, :].float()], dim=1)
                )
                hidden_items.append(hidden)
                raw_items.append(proposal_head(hidden))
            tactic_hidden = torch.stack(hidden_items, dim=1)
            raw = torch.stack(raw_items, dim=1)
            proposal_logits = raw[:, :, 0]
            if eligibility_mask is not None:
                proposal_logits = proposal_logits.masked_fill(
                    ~eligibility_mask.bool(),
                    -1.0e9,
                )
            return LightweightStrategyOutputs(
                proposal_logits=proposal_logits,
                values=raw[:, :, 1],
                risks=torch.sigmoid(raw[:, :, 2]),
                parameter_logits=raw[:, :, 3:],
                tactic_hidden=tactic_hidden,
            )

        def forward_arbitration(
            self,
            lightweight: LightweightStrategyOutputs,
            preview_features: Any,
            preview_mask: Any,
        ) -> Any:
            inputs = torch.cat(
                [
                    lightweight.tactic_hidden,
                    lightweight.proposal_logits.unsqueeze(-1),
                    lightweight.values.unsqueeze(-1),
                    lightweight.risks.unsqueeze(-1),
                    preview_features.float(),
                ],
                dim=2,
            )
            scores = self.final_arbitration(inputs).squeeze(-1)
            return scores.masked_fill(~preview_mask.bool(), -1.0e9)


else:

    class V17StrategyManagerNetwork:  # pragma: no cover - dependency guard
        def __init__(self, *args: Any, **kwargs: Any):
            _ = (args, kwargs)
            raise ImportError("V17StrategyManagerNetwork requires torch")


def _append_checkpoint_mismatch(
    errors: list[str],
    field: str,
    actual: Any,
    expected: Any,
) -> None:
    if actual != expected:
        errors.append(f"{field}: expected {expected!r}, got {actual!r}")


def _state_dict_shape_errors(
    state_dict: Mapping[str, Any],
    contract: StrategyFeatureContract,
    hidden_dim: int,
) -> list[str]:
    if torch is None:
        return ["checkpoint model shape validation requires torch"]
    with torch.random.fork_rng(devices=[]):
        expected_state = V17StrategyManagerNetwork(
            contract,
            hidden_dim=hidden_dim,
        ).state_dict()
    errors = []
    missing = sorted(set(expected_state) - set(state_dict))
    unexpected = sorted(set(state_dict) - set(expected_state), key=str)
    if missing:
        errors.append(f"model_state_dict missing keys: {', '.join(missing)}")
    if unexpected:
        errors.append(
            "model_state_dict unexpected keys: "
            + ", ".join(repr(key) for key in unexpected)
        )
    for key in sorted(set(expected_state) & set(state_dict)):
        expected_shape = tuple(expected_state[key].shape)
        actual_shape_value = getattr(state_dict[key], "shape", None)
        actual_shape = (
            None if actual_shape_value is None else tuple(actual_shape_value)
        )
        if actual_shape != expected_shape:
            errors.append(
                "model_state_dict shape mismatch for "
                f"{key}: expected {expected_shape!r}, got {actual_shape!r}"
            )
    return errors


def validate_v1_7_strategy_manager_checkpoint_payload(
    checkpoint: Mapping[str, Any],
    *,
    registry: TacticRegistry | None = None,
) -> list[str]:
    """Validate runtime metadata and tensor shapes without applying weights."""

    if not isinstance(checkpoint, Mapping):
        return ["checkpoint must be a mapping"]
    errors = list(validate_checkpoint_payload(checkpoint))
    selected_registry = registry or load_tactic_registry()
    expected_contract = StrategyFeatureContract.from_registry(selected_registry)

    _append_checkpoint_mismatch(
        errors,
        "artifact_schema_version",
        checkpoint.get("artifact_schema_version"),
        CHECKPOINT_SCHEMA_VERSION,
    )
    _append_checkpoint_mismatch(
        errors,
        "policy_type",
        checkpoint.get("policy_type"),
        POLICY_TYPE,
    )
    _append_checkpoint_mismatch(
        errors,
        "model_family",
        checkpoint.get("model_family"),
        MODEL_FAMILY,
    )
    _append_checkpoint_mismatch(
        errors,
        "model_version",
        checkpoint.get("model_version"),
        MODEL_VERSION,
    )

    config = checkpoint.get("config")
    if not isinstance(config, Mapping):
        errors.append("config must be a mapping")
    elif not config:
        errors.append("config must not be empty")

    schema = checkpoint.get("checkpoint_schema")
    if isinstance(schema, Mapping):
        _append_checkpoint_mismatch(
            errors,
            "checkpoint_schema.trainer_name",
            schema.get("trainer_name"),
            BOOTSTRAP_TRAINER_NAME,
        )
        _append_checkpoint_mismatch(
            errors,
            "checkpoint_schema.checkpoint_kind",
            schema.get("checkpoint_kind"),
            "bootstrap",
        )
        _append_checkpoint_mismatch(
            errors,
            "checkpoint_schema.run_id",
            schema.get("run_id"),
            checkpoint.get("run_id"),
        )
        _append_checkpoint_mismatch(
            errors,
            "checkpoint_schema.global_step",
            schema.get("global_step"),
            checkpoint.get("global_step"),
        )
        if isinstance(config, Mapping):
            _append_checkpoint_mismatch(
                errors,
                "checkpoint_schema.config_digest",
                schema.get("config_digest"),
                json_digest(dict(config)),
            )
        git_commit_value = schema.get("git_commit")
        if not isinstance(git_commit_value, str) or not git_commit_value:
            errors.append("checkpoint_schema.git_commit must be a non-empty string")

    run_id = checkpoint.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        errors.append("run_id must be a non-empty string")
    metadata = checkpoint.get("checkpoint_metadata")
    if not isinstance(metadata, Mapping):
        errors.append("checkpoint_metadata must be a mapping")
    else:
        expected_metadata = build_v1_7_checkpoint_metadata(
            selected_registry,
            run_id="" if not isinstance(run_id, str) else run_id,
        )
        for field in (
            "schema_version",
            "policy_type",
            "model_family",
            "model_version",
        ):
            _append_checkpoint_mismatch(
                errors,
                f"checkpoint_metadata.{field}",
                metadata.get(field),
                expected_metadata[field],
            )
        lineage = metadata.get("lineage")
        expected_lineage = expected_metadata["lineage"]
        if not isinstance(lineage, Mapping):
            errors.append("checkpoint_metadata.lineage must be a mapping")
        else:
            for field, expected_value in expected_lineage.items():
                _append_checkpoint_mismatch(
                    errors,
                    f"checkpoint_metadata.lineage.{field}",
                    lineage.get(field),
                    expected_value,
                )
        schemas = metadata.get("schemas")
        expected_schemas = expected_metadata["schemas"]
        if not isinstance(schemas, Mapping):
            errors.append("checkpoint_metadata.schemas must be a mapping")
        else:
            for field, expected_value in expected_schemas.items():
                _append_checkpoint_mismatch(
                    errors,
                    f"checkpoint_metadata.schemas.{field}",
                    schemas.get(field),
                    expected_value,
                )

    feature_contract = checkpoint.get("feature_contract")
    if not isinstance(feature_contract, Mapping):
        errors.append("feature_contract must be a mapping")
    else:
        try:
            expected_contract.validate_metadata(feature_contract)
        except ValueError as exc:
            errors.append(str(exc))

    dataset = checkpoint.get("dataset")
    if not isinstance(dataset, Mapping):
        errors.append("dataset must be a mapping")
    else:
        for field in ("path", "dataset_id", "manifest_sha256"):
            if not isinstance(dataset.get(field), str) or not dataset.get(field):
                errors.append(f"dataset.{field} must be a non-empty string")
        if not isinstance(dataset.get("compatibility"), Mapping):
            errors.append("dataset.compatibility must be a mapping")
        dataset_schemas = dataset.get("schemas")
        expected_dataset_schemas = {
            "analyzer_input": ANALYZER_INPUT_SCHEMA_VERSION,
            "analyzer_diagnostics": ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
            "feature": FEATURE_SCHEMA_VERSION,
            "preview_feature": PREVIEW_FEATURE_SCHEMA_VERSION,
            "tactic_registry": TACTIC_SCHEMA_VERSION,
            "tactic_registry_version": selected_registry.registry_version,
        }
        if not isinstance(dataset_schemas, Mapping):
            errors.append("dataset.schemas must be a mapping")
        else:
            for field, expected_value in expected_dataset_schemas.items():
                _append_checkpoint_mismatch(
                    errors,
                    f"dataset.schemas.{field}",
                    dataset_schemas.get(field),
                    expected_value,
                )

    lifecycle_contract = checkpoint.get("lifecycle_carry_contract")
    expected_lifecycle = {
        "analyzer_input_schema_version": ANALYZER_INPUT_SCHEMA_VERSION,
        "strategy_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "required_features": list(LIFECYCLE_CARRY_FEATURES),
        "legacy_implicit_defaults_allowed": False,
    }
    if not isinstance(lifecycle_contract, Mapping):
        errors.append("lifecycle_carry_contract must be a mapping")
    else:
        for field, expected_value in expected_lifecycle.items():
            _append_checkpoint_mismatch(
                errors,
                f"lifecycle_carry_contract.{field}",
                lifecycle_contract.get(field),
                expected_value,
            )

    hidden_dim = checkpoint.get("hidden_dim")
    if isinstance(hidden_dim, bool) or not isinstance(hidden_dim, int) or hidden_dim <= 0:
        errors.append(f"hidden_dim must be a positive integer, got {hidden_dim!r}")
    if isinstance(config, Mapping):
        _append_checkpoint_mismatch(
            errors,
            "config.hidden_dim",
            config.get("hidden_dim"),
            hidden_dim,
        )
    state_dict = checkpoint.get("model_state_dict")
    if not isinstance(state_dict, Mapping):
        errors.append("model_state_dict must be a mapping")
    elif isinstance(hidden_dim, int) and not isinstance(hidden_dim, bool) and hidden_dim > 0:
        errors.extend(
            _state_dict_shape_errors(
                state_dict,
                expected_contract,
                hidden_dim,
            )
        )

    saved_state_hash = checkpoint.get("state_hash")
    if not isinstance(saved_state_hash, str) or not saved_state_hash:
        errors.append("state_hash must be a non-empty string")
    else:
        try:
            actual_state_hash = checkpoint_state_hash(checkpoint)
        except Exception as exc:  # pragma: no cover - defensive malformed payload guard
            errors.append(f"state_hash could not be computed: {exc}")
        else:
            _append_checkpoint_mismatch(
                errors,
                "state_hash",
                saved_state_hash,
                actual_state_hash,
            )
    return errors


@dataclass(frozen=True)
class _PreviewResult:
    tactic_index: int
    parameters: Mapping[str, Mapping[str, Any]]
    planner_request: PlannerRequest
    proposal: SearchProposal
    plan: NTurnPlan
    features: tuple[float, ...]


class V17StrategyManagerPolicy:
    """Run learned tactic evaluation, bounded previews, and one selected worker."""

    def __init__(
        self,
        model: V17StrategyManagerNetwork,
        *,
        analyzer: StateAnalyzer | None = None,
        registry: TacticRegistry | None = None,
        profiles: tuple[WorkerProfile, ...] | None = None,
        preview_top_k: int = DEFAULT_PREVIEW_TOP_K,
        device: str = "cpu",
        deterministic: bool = True,
        checkpoint_path: str | Path | None = None,
        checkpoint_metadata: Mapping[str, Any] | None = None,
    ):
        if torch is None:
            raise ImportError("V17StrategyManagerPolicy requires torch")
        if preview_top_k <= 0:
            raise ValueError("preview_top_k must be positive")
        self.analyzer = analyzer or StateAnalyzer()
        self.registry = registry or load_tactic_registry()
        self.profiles = profiles or default_worker_profiles()
        self.encoder = V17StrategyFeatureEncoder(self.registry)
        model_contract = getattr(model, "feature_contract", None)
        if isinstance(model_contract, StrategyFeatureContract):
            model_metadata = model_contract.to_metadata()
        elif isinstance(model_contract, Mapping):
            model_metadata = model_contract
        else:
            raise ValueError("strategy model must expose feature_contract metadata")
        self.encoder.contract.validate_metadata(model_metadata)
        self.model = model
        self.device = torch.device(device)
        self.model.to(self.device)
        self.model.eval()
        self.preview_top_k = min(int(preview_top_k), len(self.registry.tactics))
        self.deterministic = bool(deterministic)
        self.checkpoint_path = (
            None if checkpoint_path is None else str(Path(checkpoint_path))
        )
        self.checkpoint_metadata = copy.deepcopy(dict(checkpoint_metadata or {}))
        self._last_step_count = -1
        self.last_analyzer_input: AnalyzerInput | None = None
        self.last_analyzer_diagnostics: AnalyzerDiagnostics | None = None
        self.last_tactic_id: str | None = None
        self.last_planner_request: PlannerRequest | None = None
        self.last_proposal: SearchProposal | None = None
        self.last_plan: NTurnPlan | None = None
        self._tactical_diagnostics: dict[str, Any] = {}

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str | Path,
        *,
        analyzer: StateAnalyzer | None = None,
        registry: TacticRegistry | None = None,
        profiles: tuple[WorkerProfile, ...] | None = None,
        preview_top_k: int = DEFAULT_PREVIEW_TOP_K,
        device: str = "cpu",
        deterministic: bool = True,
    ) -> "V17StrategyManagerPolicy":
        """Load one metadata-validated v1.7.1 bootstrap checkpoint."""

        if torch is None:
            raise ImportError("V17StrategyManagerPolicy requires torch")
        target = Path(checkpoint_path)
        if not target.is_file():
            raise FileNotFoundError(
                f"v1.7 bootstrap checkpoint not found: {target}"
            )
        checkpoint = torch.load(target, map_location="cpu", weights_only=False)
        if not isinstance(checkpoint, Mapping):
            raise ValueError(
                f"v1.7 bootstrap checkpoint must be a mapping: {target}"
            )
        selected_registry = registry or load_tactic_registry()
        errors = validate_v1_7_strategy_manager_checkpoint_payload(
            checkpoint,
            registry=selected_registry,
        )
        if errors:
            raise ValueError(
                f"incompatible v1.7 bootstrap checkpoint {target}: "
                + "; ".join(errors)
            )
        contract = StrategyFeatureContract.from_registry(selected_registry)
        with torch.random.fork_rng(devices=[]):
            model = V17StrategyManagerNetwork(
                contract,
                hidden_dim=int(checkpoint["hidden_dim"]),
            )
        model.load_state_dict(checkpoint["model_state_dict"], strict=True)
        checkpoint_schema = checkpoint["checkpoint_schema"]
        dataset = checkpoint["dataset"]
        feature_contract = checkpoint["feature_contract"]
        compatibility = checkpoint["checkpoint_metadata"]
        runtime_metadata = {
            "run_id": checkpoint["run_id"],
            "git_commit": checkpoint_schema["git_commit"],
            "dataset_id": dataset["dataset_id"],
            "feature_schema_version": feature_contract["schema_version"],
            "lineage": copy.deepcopy(compatibility["lineage"]),
            "schemas": copy.deepcopy(compatibility["schemas"]),
        }
        return cls(
            model,
            analyzer=analyzer,
            registry=selected_registry,
            profiles=profiles,
            preview_top_k=preview_top_k,
            device=device,
            deterministic=deterministic,
            checkpoint_path=target,
            checkpoint_metadata=runtime_metadata,
        )

    def reset(self) -> None:
        self._last_step_count = -1
        self.last_analyzer_input = None
        self.last_analyzer_diagnostics = None
        self.last_tactic_id = None
        self.last_planner_request = None
        self.last_proposal = None
        self.last_plan = None
        self._tactical_diagnostics = {}

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        step_count = int(info.get("step_count", info.get("tick_count", 0)))
        if step_count <= self._last_step_count:
            self.reset()
        previous_plan = self.last_plan
        previous_tactic_id = self.last_tactic_id
        analyzer_input = AnalyzerInput.from_runtime_info(info)
        analyzer_diagnostics = self.analyzer.analyze(analyzer_input)
        encoded = self.encoder.encode(
            analyzer_input,
            analyzer_diagnostics,
            previous_plan=previous_plan,
            previous_tactic_id=previous_tactic_id,
        )
        context = torch.tensor(
            [encoded.context],
            dtype=torch.float32,
            device=self.device,
        )
        tactic_features = torch.tensor(
            [encoded.tactics],
            dtype=torch.float32,
            device=self.device,
        )
        eligibility_mask = torch.tensor(
            [encoded.eligibility_mask],
            dtype=torch.bool,
            device=self.device,
        )
        with torch.no_grad():
            lightweight = self.model.forward_lightweight(
                context,
                tactic_features,
                eligibility_mask,
            )
        parameters = [
            decode_tactic_parameters(
                tactic,
                lightweight.parameter_logits[0, index].detach().cpu().tolist(),
            )
            for index, tactic in enumerate(self.registry.tactics)
        ]
        eligible_count = sum(encoded.eligibility_mask)
        if eligible_count <= 0:
            raise RuntimeError("strategy manager has no eligible tactics")
        preview_count = min(self.preview_top_k, eligible_count)
        ranked_indices = sorted(
            (index for index, eligible in enumerate(encoded.eligibility_mask) if eligible),
            key=lambda index: (
                -float(lightweight.proposal_logits[0, index].item()),
                index,
            ),
        )
        preview_indices = ranked_indices[:preview_count]
        previews = {
            index: self._preview_tactic(
                index,
                parameters[index],
                analyzer_input,
                analyzer_diagnostics,
                observation,
                info,
            )
            for index in preview_indices
        }
        preview_tensor = torch.zeros(
            (1, len(self.registry.tactics), self.encoder.contract.preview_dim),
            dtype=torch.float32,
            device=self.device,
        )
        preview_mask = torch.zeros(
            (1, len(self.registry.tactics)),
            dtype=torch.bool,
            device=self.device,
        )
        for index, preview in previews.items():
            preview_tensor[0, index] = torch.tensor(
                preview.features,
                dtype=torch.float32,
                device=self.device,
            )
            preview_mask[0, index] = True
        with torch.no_grad():
            final_scores = self.model.forward_arbitration(
                lightweight,
                preview_tensor,
                preview_mask,
            )
            if self.deterministic:
                selected_index = int(torch.argmax(final_scores[0]).item())
            else:
                selected_index = int(
                    torch.distributions.Categorical(logits=final_scores[0]).sample().item()
                )
        selected_preview = previews[selected_index]
        selected_tactic = self.registry.tactics[selected_index]
        reason = (
            "learned final arbitration selected "
            f"{selected_tactic.identity.tactic_id} after planner preview"
        )
        proposal = selected_preview.proposal
        objective = proposal.objective
        if objective is not None:
            objective = replace(objective, reason=reason)
        proposal = replace(proposal, reason=reason, objective=objective)
        plan = replace(selected_preview.plan, objective=objective)

        self._last_step_count = step_count
        self.last_analyzer_input = analyzer_input
        self.last_analyzer_diagnostics = analyzer_diagnostics
        self.last_tactic_id = selected_tactic.identity.tactic_id
        self.last_planner_request = selected_preview.planner_request
        self.last_proposal = proposal
        self.last_plan = plan
        self._tactical_diagnostics = self._build_diagnostics(
            encoded,
            lightweight,
            parameters,
            previews,
            final_scores,
            selected_index,
            reason,
        )
        return int(proposal.action)

    @property
    def current_profile_name(self) -> str | None:
        return self.last_tactic_id

    @property
    def tactical_diagnostics(self) -> dict[str, Any]:
        return copy.deepcopy(self._tactical_diagnostics)

    @property
    def plan_diagnostics(self) -> dict[str, Any]:
        return {} if self.last_plan is None else self.last_plan.to_dict()

    def _preview_tactic(
        self,
        tactic_index: int,
        parameters: Mapping[str, Mapping[str, Any]],
        analyzer_input: AnalyzerInput,
        analyzer_diagnostics: AnalyzerDiagnostics,
        observation: dict[str, Any],
        info: dict[str, Any],
    ) -> _PreviewResult:
        tactic = self.registry.tactics[tactic_index]
        request = build_planner_request(
            tactic,
            analyzer_input,
            analyzer_diagnostics,
            parameter_overrides=parameters,
        )
        profile_id = profile_id_by_name(
            self.profiles,
            _TACTIC_TO_WORKER[tactic.identity.tactic_id],
        )
        orchestrator = StrategyOrchestrator(self.profiles)
        proposal = orchestrator.propose(
            profile_id,
            observation,
            info,
            planner_request=request,
        )
        if orchestrator.last_plan is None:
            raise RuntimeError("planner preview did not produce a plan")
        plan = orchestrator.last_plan
        return _PreviewResult(
            tactic_index=tactic_index,
            parameters=parameters,
            planner_request=request,
            proposal=proposal,
            plan=plan,
            features=encode_preview_features(proposal, plan, request),
        )

    def _build_diagnostics(
        self,
        encoded: EncodedStrategyFeatures,
        lightweight: LightweightStrategyOutputs,
        parameters: Sequence[Mapping[str, Mapping[str, Any]]],
        previews: Mapping[int, _PreviewResult],
        final_scores: Any,
        selected_index: int,
        reason: str,
    ) -> dict[str, Any]:
        analyzer_input = self.last_analyzer_input
        analyzer_diagnostics = self.last_analyzer_diagnostics
        proposal = self.last_proposal
        plan = self.last_plan
        request = self.last_planner_request
        if any(
            value is None
            for value in (analyzer_input, analyzer_diagnostics, proposal, plan, request)
        ):
            raise RuntimeError("strategy diagnostics requested before selection state was stored")
        candidates = []
        for index, (tactic, candidate) in enumerate(
            zip(self.registry.tactics, encoded.candidates)
        ):
            preview = previews.get(index)
            candidates.append(
                {
                    "tactic_id": tactic.identity.tactic_id,
                    "name": tactic.identity.name,
                    "version": tactic.identity.version,
                    "eligible": bool(candidate["eligible"]),
                    "active_contexts": list(candidate.get("active_contexts", ())),
                    "logit": float(lightweight.proposal_logits[0, index].item()),
                    "value": float(lightweight.values[0, index].item()),
                    "risk": float(lightweight.risks[0, index].item()),
                    "parameters": copy.deepcopy(parameters[index]),
                    "previewed": preview is not None,
                    "preview": None if preview is None else _preview_diagnostics(preview),
                    "final_score": (
                        None
                        if preview is None
                        else float(final_scores[0, index].item())
                    ),
                    "selected": index == selected_index,
                }
            )
        selected = candidates[selected_index]
        selected_tactic = self.registry.tactics[selected_index]
        worker_result = {
            "action": int(proposal.action),
            "predicted_chain_count": int(proposal.predicted_chain_count),
            "predicted_score": int(proposal.predicted_score),
            "predicted_attack": int(proposal.predicted_attack),
            "danger": float(proposal.danger),
            "expanded_nodes": int(proposal.expanded_nodes),
            "candidate_value": float(proposal.candidate_value),
        }
        lifecycle = {
            side: {
                "board_empty": bool(getattr(analyzer_diagnostics, side).board_empty),
                "all_clear_achieved": bool(getattr(analyzer_input, side).all_clear_achieved),
                "all_clear_bonus_pending": bool(
                    getattr(analyzer_input, side).all_clear_bonus_pending
                ),
                "all_clear_bonus_consumed": bool(
                    getattr(analyzer_input, side).all_clear_bonus_consumed
                ),
                "score_carry": int(getattr(analyzer_input, side).score_carry),
            }
            for side in ("own", "opponent")
        }
        model_metadata = {
            "policy_type": POLICY_TYPE,
            "model_family": MODEL_FAMILY,
            "model_version": MODEL_VERSION,
            "checkpoint_required": True,
            "lineage_node_id": LINEAGE_NODE_ID,
        }
        lineage = {"node_id": LINEAGE_NODE_ID}
        if self.checkpoint_metadata:
            checkpoint_lineage = self.checkpoint_metadata.get("lineage", {})
            model_metadata.update(
                {
                    "checkpoint_path": self.checkpoint_path,
                    "checkpoint_run_id": self.checkpoint_metadata.get("run_id"),
                    "checkpoint_git_commit": self.checkpoint_metadata.get("git_commit"),
                    "dataset_id": self.checkpoint_metadata.get("dataset_id"),
                    "feature_schema_version": self.checkpoint_metadata.get(
                        "feature_schema_version"
                    ),
                    "parent_lineage_node_id": checkpoint_lineage.get(
                        "parent_node_id"
                    ),
                }
            )
            lineage.update(copy.deepcopy(dict(checkpoint_lineage)))
        return {
            "schema_version": STRATEGY_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
            "model_metadata": model_metadata,
            "feature_contract": self.encoder.contract.to_metadata(),
            "lifecycle_features": lifecycle,
            "analyzer": {
                "input": analyzer_input.to_dict(),
                "diagnostics": analyzer_diagnostics.to_dict(),
            },
            "tactic_registry": {
                "schema_version": self.registry.schema_version,
                "registry_version": self.registry.registry_version,
                "source_path": self.registry.source_path,
            },
            "tactic_candidates": candidates,
            "selected_tactic": {
                "tactic_id": selected_tactic.identity.tactic_id,
                "name": selected_tactic.identity.name,
                "version": selected_tactic.identity.version,
                "reason": reason,
                "logit": selected["logit"],
                "value": selected["value"],
                "risk": selected["risk"],
                "final_score": selected["final_score"],
                "parameters": copy.deepcopy(selected["parameters"]),
                "worker_profile": proposal.profile_name,
                "worker_strategy": proposal.strategy,
            },
            "planner_request": request.to_dict(),
            "worker": {
                "profile_id": int(proposal.profile_id),
                "profile_name": proposal.profile_name,
                "strategy": proposal.strategy,
                "objective": proposal.objective_dict,
                "objective_result": proposal.objective_result_dict,
                "result": worker_result,
            },
            "plan": plan.to_dict(),
            "lineage": lineage,
            "incoming_attack": int(proposal.incoming_attack),
            "target_attack": int(proposal.target_attack),
            "deadline": int(proposal.deadline),
            "reason": reason,
            "reason_code": "learned_final_arbitration",
            "objective": proposal.objective_dict,
            "objective_result": proposal.objective_result_dict,
            "profile_name": proposal.profile_name,
            "strategy": proposal.strategy,
            "plan_id": plan.plan_id,
            "plan_update_reason": plan.update_reason,
        }


def decode_tactic_parameters(
    tactic: TacticSpec,
    logits: Sequence[float],
) -> dict[str, dict[str, Any]]:
    """Decode one tactic's bounded parameter outputs in registry order."""

    required = _parameter_logit_count(tactic)
    if len(logits) < required:
        raise ValueError(
            f"parameter logits for {tactic.identity.tactic_id} require {required}, got {len(logits)}"
        )
    offset = 0
    values: dict[str, dict[str, Any]] = {}
    for section in _PARAMETER_SECTIONS:
        values[section] = {}
        for name, spec in tactic.parameters[section].items():
            if spec.kind == "discrete":
                width = len(spec.choices)
                choice_index = max(range(width), key=lambda index: (float(logits[offset + index]), -index))
                value = spec.choices[choice_index]
                offset += width
            else:
                unit_value = _sigmoid(float(logits[offset]))
                offset += 1
                minimum = spec.minimum if spec.minimum is not None else spec.default
                maximum = spec.maximum if spec.maximum is not None else spec.default
                value = float(minimum) + unit_value * (float(maximum) - float(minimum))
                if spec.kind == "integer":
                    value = int(round(value))
            spec.validate(value)
            values[section][name] = value
    return values


def encode_preview_features(
    proposal: SearchProposal,
    plan: NTurnPlan,
    request: PlannerRequest,
) -> tuple[float, ...]:
    """Encode worker outcomes, including carry/bonus-aware plan attack fields."""

    objective = proposal.objective_result
    first_step = plan.steps[0] if plan.steps else None
    elapsed_ms = max(0.0, float(proposal.elapsed_seconds) * 1_000.0)
    values = (
        _unit(proposal.predicted_chain_count, 19.0),
        _unit(proposal.predicted_score, 100_000.0),
        _unit(proposal.predicted_attack, 180.0),
        _unit(proposal.danger, 1.0),
        _unit(elapsed_ms, max(1.0, request.latency_budget_ms * 2.0)),
        _unit(proposal.expanded_nodes, 4_096.0),
        _signed_unit(proposal.candidate_value, 1_000_000.0),
        _unit(proposal.target_attack, 180.0),
        _unit(proposal.incoming_attack, 180.0),
        _unit(proposal.deadline, 8.0),
        _flag(objective is not None and objective.achieved),
        _flag(objective is not None and objective.possible_by_deadline),
        _signed_unit(0 if objective is None else objective.surplus_attack, 180.0),
        _flag(objective is not None and objective.deadline_missed),
        _unit(0.0 if objective is None else objective.danger_excess, 1.0),
        _flag(first_step is not None),
        _unit(0 if first_step is None else first_step.attack_generated, 180.0),
        _unit(0 if first_step is None else first_step.attack_canceled, 180.0),
        _unit(0 if first_step is None else first_step.attack_outgoing, 180.0),
        _unit(0 if first_step is None else first_step.incoming_remaining, 180.0),
        _flag(first_step is not None and first_step.all_clear_achieved),
        _flag(first_step is not None and first_step.all_clear_bonus_pending),
        _flag(first_step is not None and first_step.all_clear_bonus_consumed),
    )
    if len(values) != len(PREVIEW_FEATURE_NAMES):
        raise RuntimeError("planner preview feature count does not match contract")
    return values


def _preview_diagnostics(preview: _PreviewResult) -> dict[str, Any]:
    proposal = preview.proposal
    plan = preview.plan
    return {
        "feature_schema_version": PREVIEW_FEATURE_SCHEMA_VERSION,
        "features": dict(zip(PREVIEW_FEATURE_NAMES, preview.features)),
        "planner_request": preview.planner_request.to_dict(),
        "worker": {
            "profile_id": int(proposal.profile_id),
            "profile_name": proposal.profile_name,
            "strategy": proposal.strategy,
            "predicted_chain_count": int(proposal.predicted_chain_count),
            "predicted_score": int(proposal.predicted_score),
            "predicted_attack": int(proposal.predicted_attack),
            "danger": float(proposal.danger),
            "objective_result": proposal.objective_result_dict,
        },
        "plan": plan.to_dict(),
    }


def _player_features(player_input: Any, player_diagnostics: Any) -> tuple[float, ...]:
    forecast = player_diagnostics.forecast
    main_chain = forecast.main_chain
    options = player_diagnostics.attack_options
    return (
        _unit(player_input.score_carry, 69.0),
        _flag(player_input.all_clear_achieved),
        _flag(player_input.all_clear_bonus_pending),
        _flag(player_input.all_clear_bonus_consumed),
        _unit(player_diagnostics.danger, 1.0),
        _unit(player_diagnostics.vulnerability, 1.0),
        _flag(player_diagnostics.board_empty),
        _flag(player_diagnostics.is_all_clear),
        _flag(player_diagnostics.all_clear_achieved),
        _flag(player_diagnostics.all_clear_bonus_pending),
        _flag(player_diagnostics.all_clear_bonus_consumed),
        _unit(forecast.immediate_attack, 180.0),
        _unit(forecast.short_attack, 180.0),
        _unit(forecast.turns_to_best, 8.0),
        _flag(main_chain is not None),
        _unit(0 if main_chain is None else main_chain.turns, 8.0),
        _unit(0 if main_chain is None else main_chain.chain_count, 19.0),
        _unit(0 if main_chain is None else main_chain.attack, 180.0),
        _unit(0.0 if main_chain is None else main_chain.attack_per_turn, 180.0),
        _flag(main_chain is not None and main_chain.is_all_clear),
        _flag(main_chain is not None and main_chain.hard_to_answer),
        _unit(len(options), 8.0),
        _unit(max((option.attack for option in options), default=0), 180.0),
        _unit(max((option.attack_per_turn for option in options), default=0.0), 180.0),
        _unit(sum(option.hard_to_answer for option in options), 8.0),
    )


def _previous_plan_features(plan: NTurnPlan | None) -> tuple[float, ...]:
    first_step = None if plan is None or not plan.steps else plan.steps[0]
    objective = None if first_step is None else first_step.objective_result
    return (
        _flag(plan is not None),
        _unit(0 if plan is None else plan.visible_steps, 8.0),
        _unit(0 if plan is None else plan.max_steps, 8.0),
        _flag(plan is not None and plan.planner_latency_overrun),
        _unit(0 if plan is None else plan.initial_score_carry, 69.0),
        _unit(0 if plan is None else plan.initial_incoming_attack, 180.0),
        _flag(first_step is not None),
        _unit(0 if first_step is None else first_step.predicted_chain_count, 19.0),
        _unit(0 if first_step is None else first_step.predicted_attack, 180.0),
        _unit(0.0 if first_step is None else first_step.danger, 1.0),
        _unit(0 if first_step is None else first_step.incoming_remaining, 180.0),
        _flag(first_step is not None and first_step.all_clear_achieved),
        _flag(first_step is not None and first_step.all_clear_bonus_pending),
        _flag(first_step is not None and first_step.all_clear_bonus_consumed),
        _flag(objective is not None and objective.achieved),
        _flag(objective is not None and objective.possible_by_deadline),
        _flag(objective is not None and objective.deadline_missed),
    )


def _parameter_signatures(tactic: TacticSpec) -> tuple[str, ...]:
    signatures = []
    for section in _PARAMETER_SECTIONS:
        for name, spec in tactic.parameters[section].items():
            choices = ",".join(str(choice) for choice in spec.choices)
            signatures.append(
                f"{section}.{name}:{spec.kind}:{spec.minimum}:{spec.maximum}:{choices}"
            )
    return tuple(signatures)


def _parameter_logit_count(tactic: TacticSpec) -> int:
    return sum(
        len(spec.choices) if spec.kind == "discrete" else 1
        for section in _PARAMETER_SECTIONS
        for spec in tactic.parameters[section].values()
    )


def _normalized_parameter(spec: ParameterSpec, value: Any) -> float:
    if spec.kind == "discrete":
        index = spec.choices.index(value)
        return _unit(index, max(1, len(spec.choices) - 1))
    minimum = spec.minimum if spec.minimum is not None else spec.default
    maximum = spec.maximum if spec.maximum is not None else spec.default
    if float(maximum) == float(minimum):
        return 0.0
    return _unit(float(value) - float(minimum), float(maximum) - float(minimum))


def _unit(value: float | int, scale: float | int) -> float:
    if float(scale) <= 0.0:
        return 0.0
    return min(1.0, max(0.0, float(value) / float(scale)))


def _signed_unit(value: float | int, scale: float | int) -> float:
    if float(scale) <= 0.0:
        return 0.0
    return min(1.0, max(-1.0, float(value) / float(scale)))


def _flag(value: Any) -> float:
    return 1.0 if bool(value) else 0.0


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        exponent = math.exp(-value)
        return 1.0 / (1.0 + exponent)
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)
