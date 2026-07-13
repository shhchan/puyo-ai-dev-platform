"""Reproducible behavior-cloning pipeline for the v1.7.1 Strategy Manager."""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

try:
    import numpy as np
    import torch
    from torch import optim
    from torch.nn import functional as F
except ImportError:  # pragma: no cover - dependency guard
    np = None
    torch = None
    optim = None
    F = None

from agents.state_analyzer import ANALYZER_INPUT_SCHEMA_VERSION
from agents.v1_7_strategy_manager import (
    BOOTSTRAP_TRAINER_NAME,
    FEATURE_SCHEMA_VERSION,
    LIFECYCLE_CARRY_FEATURES,
    MODEL_FAMILY,
    MODEL_VERSION,
    POLICY_TYPE,
    PREVIEW_FEATURE_SCHEMA_VERSION,
    PREVIEW_FEATURE_NAMES,
    V17StrategyFeatureEncoder,
    V17StrategyManagerNetwork,
    build_v1_7_checkpoint_metadata,
    decode_tactic_parameters,
    validate_v1_7_strategy_manager_checkpoint_payload,
)
from agents.v1_7_tactics import ParameterSpec, TacticRegistry, load_tactic_registry
from eval.analyzer_scenarios import (
    SCENARIO_SCHEMA_VERSION,
    build_report as build_analyzer_scenario_report,
    evaluate_scenarios,
    load_scenarios,
)
from train.artifacts import (
    attach_checkpoint_schema,
    file_sha256,
    git_commit,
    utc_timestamp,
    validate_artifact_manifest,
    write_artifact_manifest,
)
from train.restore import checkpoint_state_hash
from train.v1_7_bootstrap_dataset import (
    MANIFEST_SCHEMA_VERSION as DATASET_MANIFEST_SCHEMA_VERSION,
    SAMPLE_SCHEMA_VERSION as DATASET_SAMPLE_SCHEMA_VERSION,
    validate_bootstrap_dataset,
)


TRAINER_NAME = BOOTSTRAP_TRAINER_NAME
SUMMARY_SCHEMA_VERSION = "puyo.v1_7_manager_bootstrap.summary.v1"
METRICS_SCHEMA_VERSION = "puyo.v1_7_manager_bootstrap.metrics.v1"
CONFUSION_SCHEMA_VERSION = "puyo.v1_7_manager_bootstrap.confusion.v1"
PARAMETER_REPORT_SCHEMA_VERSION = "puyo.v1_7_manager_bootstrap.parameters.v1"
SCENARIO_REPORT_SCHEMA_VERSION = "puyo.v1_7_manager_bootstrap.scenarios.v1"
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config") / "v1_7_manager_bootstrap.yaml"
DEFAULT_DATASET_DIR = "docs/benchmarks/puyo-v1-7-1-bootstrap-dataset-smoke"
DEFAULT_SCENARIO_DATASET = "eval/scenarios/v1_7_analyzer.json"
_PARAMETER_SECTIONS = ("objective", "constraints", "planner")


@dataclass
class V17ManagerBootstrapConfig:
    """Complete, serializable configuration for one bootstrap training run."""

    seed: int = 126
    run_id: str = "v1-7-manager-bootstrap-seed126"
    log_dir: str = "runs/v1_7_manager"
    dataset_dir: str = DEFAULT_DATASET_DIR
    scenario_dataset: str = DEFAULT_SCENARIO_DATASET
    epochs: int = 20
    batch_size: int = 16
    learning_rate: float = 0.001
    weight_decay: float = 0.0
    hidden_dim: int = 64
    max_grad_norm: float = 1.0
    tactic_loss_weight: float = 1.0
    arbitration_loss_weight: float = 1.0
    value_loss_weight: float = 0.25
    risk_loss_weight: float = 0.25
    parameter_loss_weight: float = 1.0
    allow_audited_nontraining_records: bool = False
    deterministic: bool = True
    device: str = "cpu"


@dataclass(frozen=True)
class _LoadedDataset:
    root: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    train: tuple[Mapping[str, Any], ...]
    validation: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class _Evaluation:
    metrics: Mapping[str, float]
    teacher_labels: tuple[int, ...]
    proposal_predictions: tuple[int, ...]
    arbitration_predictions: tuple[int, ...]
    parameter_records: tuple[Mapping[str, Any], ...]
    sample_predictions: tuple[Mapping[str, Any], ...]


def _require_deps() -> None:
    if np is None or torch is None or optim is None or F is None:
        raise ImportError("v1.7 manager bootstrap training requires numpy and torch")


def _safe_name(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "-"
        for character in value.strip()
    )
    return safe.strip("-") or "v1-7-manager-bootstrap"


def _coerce(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        normalized = raw.strip().lower()
        if normalized not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
            raise ValueError(f"invalid boolean value: {raw}")
        return normalized in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def load_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
    overrides: Sequence[str] = (),
) -> V17ManagerBootstrapConfig:
    """Load a YAML config and apply validated ``KEY=VALUE`` overrides."""

    target = Path(path)
    values = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(values, dict):
        raise ValueError(f"{target} must contain a YAML mapping")
    defaults = V17ManagerBootstrapConfig()
    valid = {field.name for field in fields(V17ManagerBootstrapConfig)}
    unknown = sorted(set(values) - valid)
    if unknown:
        raise ValueError(f"unknown config fields: {', '.join(unknown)}")
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"override must be KEY=VALUE: {override}")
        key, raw = override.split("=", 1)
        if key not in valid:
            raise ValueError(f"unknown config field: {key}")
        values[key] = _coerce(raw, getattr(defaults, key))
    config = V17ManagerBootstrapConfig(**values)
    validate_config(config)
    return config


def validate_config(config: V17ManagerBootstrapConfig) -> None:
    if config.seed < 0:
        raise ValueError("seed must be non-negative")
    if config.epochs <= 0 or config.batch_size <= 0 or config.hidden_dim <= 0:
        raise ValueError("epochs, batch_size, and hidden_dim must be positive")
    if config.learning_rate <= 0.0 or config.weight_decay < 0.0:
        raise ValueError("learning_rate must be positive and weight_decay non-negative")
    if config.max_grad_norm <= 0.0:
        raise ValueError("max_grad_norm must be positive")
    weights = (
        config.tactic_loss_weight,
        config.arbitration_loss_weight,
        config.value_loss_weight,
        config.risk_loss_weight,
        config.parameter_loss_weight,
    )
    if any(weight < 0.0 for weight in weights) or not any(weight > 0.0 for weight in weights):
        raise ValueError("loss weights must be non-negative with at least one positive value")
    if not config.dataset_dir or not config.scenario_dataset:
        raise ValueError("dataset_dir and scenario_dataset are required")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        records.append(value)
    return tuple(records)


def _load_and_validate_dataset(config: V17ManagerBootstrapConfig) -> _LoadedDataset:
    root = Path(config.dataset_dir)
    errors = validate_bootstrap_dataset(root)
    if errors:
        raise ValueError("invalid bootstrap dataset: " + "; ".join(errors))
    manifest_path = root / "dataset_manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != DATASET_MANIFEST_SCHEMA_VERSION:
        raise ValueError("bootstrap dataset manifest schema is unsupported")
    if manifest.get("sample_schema_version") != DATASET_SAMPLE_SCHEMA_VERSION:
        raise ValueError("bootstrap dataset sample schema is unsupported")
    counts = manifest.get("counts", {})
    legacy = int(counts.get("legacy", 0))
    rejected = int(counts.get("rejected", 0))
    if (legacy or rejected) and not config.allow_audited_nontraining_records:
        raise ValueError(
            "bootstrap dataset contains audited nontraining records; "
            f"legacy={legacy}, rejected={rejected}. Set "
            "allow_audited_nontraining_records=true to train only current/migrated records."
        )
    train_samples = _read_jsonl(root / "train.jsonl")
    validation_samples = _read_jsonl(root / "validation.jsonl")
    if not train_samples:
        raise ValueError("bootstrap dataset train split is empty")
    if not validation_samples:
        raise ValueError("bootstrap dataset validation split is empty")
    return _LoadedDataset(
        root=root,
        manifest=manifest,
        manifest_sha256=file_sha256(manifest_path),
        train=train_samples,
        validation=validation_samples,
    )


def _validate_scenarios(
    config: V17ManagerBootstrapConfig,
    dataset: _LoadedDataset,
) -> tuple[list[dict[str, Any]], Mapping[str, Any]]:
    scenarios = load_scenarios(config.scenario_dataset)
    expected_names = set(dataset.manifest.get("validation_scenarios", {}).get("names", ()))
    actual_names = {str(scenario["name"]) for scenario in scenarios}
    if expected_names != actual_names:
        raise ValueError("bootstrap dataset validation scenarios do not match scenario_dataset")
    if dataset.manifest.get("validation_scenarios", {}).get("schema_version") != SCENARIO_SCHEMA_VERSION:
        raise ValueError("bootstrap dataset scenario schema is unsupported")
    results = evaluate_scenarios(scenarios)
    report = build_analyzer_scenario_report(results)
    if report["summary"]["failed"]:
        failed = [result.name for result in results if not result.passed]
        raise ValueError("PUYO-153 scenario validation failed: " + ", ".join(failed))
    return scenarios, report


def _seed_everything(config: V17ManagerBootstrapConfig) -> None:
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(config.seed)
    torch.use_deterministic_algorithms(config.deterministic)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = config.deterministic
        torch.backends.cudnn.benchmark = not config.deterministic


def _device(config: V17ManagerBootstrapConfig):
    device = torch.device(config.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA device requested but CUDA is unavailable")
    return device


def _initialize_preview_weights(model: V17StrategyManagerNetwork) -> None:
    """Keep absent preview features neutral until preview-bearing samples train them."""

    first_layer = model.final_arbitration[0]
    preview_start = model.hidden_dim + 3
    with torch.no_grad():
        first_layer.weight[:, preview_start:].zero_()


def _batch_tensors(samples: Sequence[Mapping[str, Any]], device) -> dict[str, Any]:
    context = torch.tensor(
        [sample["features"]["context"] for sample in samples],
        dtype=torch.float32,
        device=device,
    )
    tactics = torch.tensor(
        [sample["features"]["tactics"] for sample in samples],
        dtype=torch.float32,
        device=device,
    )
    eligibility = torch.tensor(
        [sample["features"]["eligibility_mask"] for sample in samples],
        dtype=torch.bool,
        device=device,
    )
    labels = torch.tensor(
        [int(sample["teacher"]["tactic_index"]) for sample in samples],
        dtype=torch.long,
        device=device,
    )
    tactic_count = tactics.shape[1]
    preview_dim = len(PREVIEW_FEATURE_NAMES)
    previews = torch.zeros(
        (len(samples), tactic_count, preview_dim),
        dtype=torch.float32,
        device=device,
    )
    preview_mask = torch.zeros(
        (len(samples), tactic_count),
        dtype=torch.bool,
        device=device,
    )
    risk_targets = torch.zeros(
        (len(samples), tactic_count),
        dtype=torch.float32,
        device=device,
    )
    risk_mask = torch.zeros(
        (len(samples), tactic_count),
        dtype=torch.bool,
        device=device,
    )
    for row, sample in enumerate(samples):
        preview_records = sample.get("outcomes", {}).get("candidate_previews", ())
        preview_count = 0
        for record in preview_records if isinstance(preview_records, list) else ():
            if not isinstance(record, Mapping):
                continue
            preview = record.get("preview")
            if not isinstance(preview, Mapping):
                continue
            features = preview.get("features")
            tactic_id = str(record.get("tactic_id", ""))
            contract = sample.get("features", {}).get("schema_version")
            if contract != FEATURE_SCHEMA_VERSION:
                raise ValueError(f"sample {sample.get('sample_id')} feature schema mismatch")
            if preview.get("feature_schema_version") != PREVIEW_FEATURE_SCHEMA_VERSION:
                raise ValueError(f"sample {sample.get('sample_id')} preview schema mismatch")
            if not isinstance(features, Mapping) or set(features) != set(PREVIEW_FEATURE_NAMES):
                raise ValueError(f"sample {sample.get('sample_id')} preview feature shape mismatch")
            candidate_ids = [
                str(candidate.get("tactic_id", ""))
                for candidate in sample.get("outcomes", {}).get("tactic_candidates", ())
                if isinstance(candidate, Mapping)
            ]
            if tactic_id not in candidate_ids:
                raise ValueError(f"sample {sample.get('sample_id')} has an unknown preview tactic")
            index = candidate_ids.index(tactic_id)
            previews[row, index] = torch.tensor(
                [float(features[name]) for name in PREVIEW_FEATURE_NAMES],
                dtype=torch.float32,
                device=device,
            )
            preview_mask[row, index] = True
            preview_count += 1
        if preview_count:
            teacher_index = int(labels[row].item())
            if not bool(preview_mask[row, teacher_index].item()):
                raise ValueError(f"sample {sample.get('sample_id')} teacher tactic has no preview")
        else:
            preview_mask[row] = eligibility[row]

        candidates = sample.get("outcomes", {}).get("tactic_candidates", ())
        if isinstance(candidates, list):
            for index, candidate in enumerate(candidates[:tactic_count]):
                if not isinstance(candidate, Mapping):
                    continue
                target = candidate.get("risk")
                if not isinstance(target, (int, float)) or isinstance(target, bool):
                    scoring = candidate.get("scoring")
                    target = scoring.get("danger") if isinstance(scoring, Mapping) else None
                if isinstance(target, (int, float)) and not isinstance(target, bool):
                    risk_targets[row, index] = min(1.0, max(0.0, float(target)))
                    risk_mask[row, index] = bool(eligibility[row, index].item())
    return {
        "context": context,
        "tactics": tactics,
        "eligibility": eligibility,
        "labels": labels,
        "previews": previews,
        "preview_mask": preview_mask,
        "risk_targets": risk_targets,
        "risk_mask": risk_mask,
    }


def _normalized_parameter_target(spec: ParameterSpec, value: Any) -> float:
    minimum = spec.minimum if spec.minimum is not None else spec.default
    maximum = spec.maximum if spec.maximum is not None else spec.default
    if float(maximum) == float(minimum):
        return 0.0
    return min(1.0, max(0.0, (float(value) - float(minimum)) / (float(maximum) - float(minimum))))


def _parameter_loss(
    parameter_logits,
    samples: Sequence[Mapping[str, Any]],
    registry: TacticRegistry,
):
    losses = []
    for row, sample in enumerate(samples):
        tactic_index = int(sample["teacher"]["tactic_index"])
        tactic = registry.tactics[tactic_index]
        targets = sample["teacher"]["parameters"]
        offset = 0
        for section in _PARAMETER_SECTIONS:
            for name, spec in tactic.parameters[section].items():
                target = targets[section][name]
                if spec.kind == "discrete":
                    width = len(spec.choices)
                    target_index = spec.choices.index(target)
                    losses.append(
                        F.cross_entropy(
                            parameter_logits[row, tactic_index, offset : offset + width].unsqueeze(0),
                            torch.tensor([target_index], dtype=torch.long, device=parameter_logits.device),
                        )
                    )
                    offset += width
                else:
                    predicted = torch.sigmoid(parameter_logits[row, tactic_index, offset])
                    expected = torch.tensor(
                        _normalized_parameter_target(spec, target),
                        dtype=torch.float32,
                        device=parameter_logits.device,
                    )
                    losses.append(F.smooth_l1_loss(predicted, expected))
                    offset += 1
    if not losses:
        return parameter_logits.sum() * 0.0
    return torch.stack(losses).mean()


def _loss_components(
    model: V17StrategyManagerNetwork,
    samples: Sequence[Mapping[str, Any]],
    registry: TacticRegistry,
    config: V17ManagerBootstrapConfig,
    device,
) -> tuple[Any, Mapping[str, Any], Mapping[str, Any]]:
    tensors = _batch_tensors(samples, device)
    lightweight = model.forward_lightweight(
        tensors["context"],
        tensors["tactics"],
        tensors["eligibility"],
    )
    final_scores = model.forward_arbitration(
        lightweight,
        tensors["previews"],
        tensors["preview_mask"],
    )
    tactic_loss = F.cross_entropy(lightweight.proposal_logits, tensors["labels"])
    arbitration_loss = F.cross_entropy(final_scores, tensors["labels"])
    value_targets = F.one_hot(
        tensors["labels"],
        num_classes=len(registry.tactics),
    ).float()
    value_loss = F.binary_cross_entropy_with_logits(
        lightweight.values[tensors["eligibility"]],
        value_targets[tensors["eligibility"]],
    )
    if bool(tensors["risk_mask"].any()):
        risk_loss = F.smooth_l1_loss(
            lightweight.risks[tensors["risk_mask"]],
            tensors["risk_targets"][tensors["risk_mask"]],
        )
    else:
        risk_loss = lightweight.risks.sum() * 0.0
    parameter_loss = _parameter_loss(lightweight.parameter_logits, samples, registry)
    total = (
        config.tactic_loss_weight * tactic_loss
        + config.arbitration_loss_weight * arbitration_loss
        + config.value_loss_weight * value_loss
        + config.risk_loss_weight * risk_loss
        + config.parameter_loss_weight * parameter_loss
    )
    components = {
        "total_loss": total,
        "tactic_loss": tactic_loss,
        "arbitration_loss": arbitration_loss,
        "value_loss": value_loss,
        "risk_loss": risk_loss,
        "parameter_loss": parameter_loss,
    }
    outputs = {"lightweight": lightweight, "final_scores": final_scores, "tensors": tensors}
    return total, components, outputs


def _parameter_records(
    parameter_logits,
    samples: Sequence[Mapping[str, Any]],
    registry: TacticRegistry,
) -> list[dict[str, Any]]:
    records = []
    for row, sample in enumerate(samples):
        tactic_index = int(sample["teacher"]["tactic_index"])
        tactic = registry.tactics[tactic_index]
        tactic_id = tactic.identity.tactic_id
        predicted = decode_tactic_parameters(
            tactic,
            parameter_logits[row, tactic_index].detach().cpu().tolist(),
        )
        expected = sample["teacher"]["parameters"]
        for section in _PARAMETER_SECTIONS:
            for name, spec in tactic.parameters[section].items():
                target = expected[section][name]
                value = predicted[section][name]
                record: dict[str, Any] = {
                    "sample_id": sample["sample_id"],
                    "tactic_id": tactic_id,
                    "parameter": f"{section}.{name}",
                    "kind": spec.kind,
                    "target": target,
                    "prediction": value,
                }
                if spec.kind == "discrete":
                    record["correct"] = value == target
                    record["normalized_error"] = 0.0 if value == target else 1.0
                else:
                    absolute = abs(float(value) - float(target))
                    minimum = spec.minimum if spec.minimum is not None else spec.default
                    maximum = spec.maximum if spec.maximum is not None else spec.default
                    width = max(0.0, float(maximum) - float(minimum))
                    record["absolute_error"] = absolute
                    record["squared_error"] = absolute * absolute
                    record["normalized_error"] = 0.0 if width == 0.0 else absolute / width
                records.append(record)
    return records


def _evaluate(
    model: V17StrategyManagerNetwork,
    samples: Sequence[Mapping[str, Any]],
    registry: TacticRegistry,
    config: V17ManagerBootstrapConfig,
    device,
) -> _Evaluation:
    totals = {
        "total_loss": 0.0,
        "tactic_loss": 0.0,
        "arbitration_loss": 0.0,
        "value_loss": 0.0,
        "risk_loss": 0.0,
        "parameter_loss": 0.0,
    }
    teacher_labels = []
    proposal_predictions = []
    arbitration_predictions = []
    parameter_records = []
    sample_predictions = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(samples), config.batch_size):
            selected = samples[start : start + config.batch_size]
            _, components, outputs = _loss_components(
                model,
                selected,
                registry,
                config,
                device,
            )
            for name in totals:
                totals[name] += float(components[name].item()) * len(selected)
            labels = outputs["tensors"]["labels"].detach().cpu().tolist()
            proposal = torch.argmax(outputs["lightweight"].proposal_logits, dim=1).detach().cpu().tolist()
            arbitration = torch.argmax(outputs["final_scores"], dim=1).detach().cpu().tolist()
            teacher_labels.extend(int(value) for value in labels)
            proposal_predictions.extend(int(value) for value in proposal)
            arbitration_predictions.extend(int(value) for value in arbitration)
            parameter_records.extend(
                _parameter_records(outputs["lightweight"].parameter_logits, selected, registry)
            )
            for sample, teacher, proposed, final in zip(selected, labels, proposal, arbitration):
                scenario = sample.get("outcomes", {}).get("scenario")
                sample_predictions.append(
                    {
                        "sample_id": sample["sample_id"],
                        "scenario": scenario.get("name") if isinstance(scenario, Mapping) else None,
                        "teacher_tactic": registry.tactics[int(teacher)].identity.tactic_id,
                        "proposal_tactic": registry.tactics[int(proposed)].identity.tactic_id,
                        "arbitration_tactic": registry.tactics[int(final)].identity.tactic_id,
                        "correct": int(final) == int(teacher),
                    }
                )
    count = len(samples)
    metrics = {name: value / count for name, value in totals.items()}
    metrics["proposal_tactic_accuracy"] = sum(
        actual == predicted for actual, predicted in zip(teacher_labels, proposal_predictions)
    ) / count
    metrics["arbitration_tactic_accuracy"] = sum(
        actual == predicted for actual, predicted in zip(teacher_labels, arbitration_predictions)
    ) / count
    parameter_errors = [float(record["normalized_error"]) for record in parameter_records]
    metrics["parameter_mean_normalized_error"] = (
        sum(parameter_errors) / len(parameter_errors) if parameter_errors else 0.0
    )
    return _Evaluation(
        metrics=metrics,
        teacher_labels=tuple(teacher_labels),
        proposal_predictions=tuple(proposal_predictions),
        arbitration_predictions=tuple(arbitration_predictions),
        parameter_records=tuple(parameter_records),
        sample_predictions=tuple(sample_predictions),
    )


def _confusion_split(
    labels: Sequence[int],
    predictions: Sequence[int],
    tactic_ids: Sequence[str],
) -> dict[str, Any]:
    size = len(tactic_ids)
    matrix = [[0 for _ in range(size)] for _ in range(size)]
    for actual, predicted in zip(labels, predictions):
        matrix[int(actual)][int(predicted)] += 1
    per_tactic = {}
    f1_values = []
    for index, tactic_id in enumerate(tactic_ids):
        true_positive = matrix[index][index]
        support = sum(matrix[index])
        predicted_count = sum(row[index] for row in matrix)
        precision = true_positive / predicted_count if predicted_count else 0.0
        recall = true_positive / support if support else 0.0
        f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
        if support:
            f1_values.append(f1)
        per_tactic[tactic_id] = {
            "support": support,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    total = len(labels)
    return {
        "samples": total,
        "accuracy": sum(matrix[index][index] for index in range(size)) / max(1, total),
        "macro_f1": sum(f1_values) / len(f1_values) if f1_values else 0.0,
        "matrix": matrix,
        "per_tactic": per_tactic,
    }


def _build_confusion_report(
    train: _Evaluation,
    validation: _Evaluation,
    tactic_ids: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": CONFUSION_SCHEMA_VERSION,
        "tactic_ids": list(tactic_ids),
        "matrix_axes": {"rows": "teacher", "columns": "arbitration_prediction"},
        "splits": {
            "train": _confusion_split(train.teacher_labels, train.arbitration_predictions, tactic_ids),
            "validation": _confusion_split(
                validation.teacher_labels,
                validation.arbitration_predictions,
                tactic_ids,
            ),
        },
    }


def _aggregate_parameter_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    by_tactic: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        key = f"{record['tactic_id']}.{record['parameter']}"
        grouped.setdefault(key, []).append(record)
        by_tactic.setdefault(str(record["tactic_id"]), []).append(record)

    def summarize(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        normalized = [float(item["normalized_error"]) for item in items]
        numeric = [item for item in items if item["kind"] != "discrete"]
        discrete = [item for item in items if item["kind"] == "discrete"]
        result: dict[str, Any] = {
            "count": len(items),
            "mean_normalized_error": sum(normalized) / len(normalized) if normalized else 0.0,
        }
        if numeric:
            result["mae"] = sum(float(item["absolute_error"]) for item in numeric) / len(numeric)
            result["rmse"] = math.sqrt(
                sum(float(item["squared_error"]) for item in numeric) / len(numeric)
            )
        if discrete:
            result["accuracy"] = sum(bool(item["correct"]) for item in discrete) / len(discrete)
        return result

    return {
        "overall": summarize(records),
        "by_parameter": {key: summarize(items) for key, items in sorted(grouped.items())},
        "by_tactic": {key: summarize(items) for key, items in sorted(by_tactic.items())},
    }


def _build_parameter_report(train: _Evaluation, validation: _Evaluation) -> dict[str, Any]:
    return {
        "schema_version": PARAMETER_REPORT_SCHEMA_VERSION,
        "splits": {
            "train": _aggregate_parameter_records(train.parameter_records),
            "validation": _aggregate_parameter_records(validation.parameter_records),
        },
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _run_paths(config: V17ManagerBootstrapConfig) -> dict[str, Path | str]:
    run_id = _safe_name(config.run_id)
    run_dir = Path(config.log_dir) / run_id
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "config": run_dir / "config.yaml",
        "metrics": run_dir / "metrics.json",
        "confusion": run_dir / "confusion_report.json",
        "parameters": run_dir / "parameter_report.json",
        "scenarios": run_dir / "scenario_report.json",
        "summary": run_dir / "summary.json",
        "checkpoint": run_dir / "checkpoints" / "bootstrap.pt",
        "manifest": run_dir / "artifact_manifest.json",
    }


def _lifecycle_carry_contract(dataset: _LoadedDataset) -> dict[str, Any]:
    schemas = dataset.manifest.get("schemas", {})
    return {
        "analyzer_input_schema_version": schemas.get("analyzer_input"),
        "strategy_feature_schema_version": schemas.get("feature"),
        "required_features": list(LIFECYCLE_CARRY_FEATURES),
        "legacy_implicit_defaults_allowed": False,
    }


def train_v1_7_manager(
    config: V17ManagerBootstrapConfig | None = None,
) -> dict[str, Any]:
    """Train and persist one schema-checked v1.7.1 bootstrap checkpoint."""

    _require_deps()
    cfg = config or V17ManagerBootstrapConfig()
    validate_config(cfg)
    dataset = _load_and_validate_dataset(cfg)
    _, analyzer_scenario_report = _validate_scenarios(cfg, dataset)
    registry = load_tactic_registry()
    encoder = V17StrategyFeatureEncoder(registry)
    encoder.contract.validate_metadata(dataset.manifest.get("feature_contract", {}))
    if dataset.manifest.get("schemas", {}).get("analyzer_input") != ANALYZER_INPUT_SCHEMA_VERSION:
        raise ValueError("dataset Analyzer input schema does not match the current model")
    _seed_everything(cfg)
    device = _device(cfg)
    model = V17StrategyManagerNetwork(encoder.contract, hidden_dim=cfg.hidden_dim).to(device)
    _initialize_preview_weights(model)
    optimizer = optim.Adam(
        model.parameters(),
        lr=cfg.learning_rate,
        weight_decay=cfg.weight_decay,
    )
    history = []
    for epoch in range(cfg.epochs):
        indices = list(range(len(dataset.train)))
        random.Random(cfg.seed + epoch).shuffle(indices)
        model.train()
        for start in range(0, len(indices), cfg.batch_size):
            selected = [dataset.train[index] for index in indices[start : start + cfg.batch_size]]
            total, _, _ = _loss_components(model, selected, registry, cfg, device)
            optimizer.zero_grad(set_to_none=True)
            total.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.max_grad_norm)
            optimizer.step()
        train_evaluation = _evaluate(model, dataset.train, registry, cfg, device)
        validation_evaluation = _evaluate(model, dataset.validation, registry, cfg, device)
        history.append(
            {
                "epoch": epoch + 1,
                "train": dict(train_evaluation.metrics),
                "validation": dict(validation_evaluation.metrics),
            }
        )

    train_evaluation = _evaluate(model, dataset.train, registry, cfg, device)
    validation_evaluation = _evaluate(model, dataset.validation, registry, cfg, device)
    tactic_ids = tuple(encoder.contract.tactic_ids)
    confusion_report = _build_confusion_report(train_evaluation, validation_evaluation, tactic_ids)
    parameter_report = _build_parameter_report(train_evaluation, validation_evaluation)
    scenario_predictions = [
        record for record in validation_evaluation.sample_predictions if record["scenario"] is not None
    ]
    scenario_report = {
        "schema_version": SCENARIO_REPORT_SCHEMA_VERSION,
        "analyzer": analyzer_scenario_report,
        "model_validation": {
            "samples": len(scenario_predictions),
            "tactic_accuracy": (
                sum(bool(record["correct"]) for record in scenario_predictions)
                / max(1, len(scenario_predictions))
            ),
            "predictions": scenario_predictions,
        },
    }
    metrics = {
        "schema_version": METRICS_SCHEMA_VERSION,
        "seed": cfg.seed,
        "dataset_id": dataset.manifest["dataset_id"],
        "loss_weights": {
            "tactic": cfg.tactic_loss_weight,
            "arbitration": cfg.arbitration_loss_weight,
            "value": cfg.value_loss_weight,
            "risk": cfg.risk_loss_weight,
            "parameter": cfg.parameter_loss_weight,
        },
        "history": history,
        "final": {
            "train": dict(train_evaluation.metrics),
            "validation": dict(validation_evaluation.metrics),
        },
    }
    paths = _run_paths(cfg)
    run_dir = paths["run_dir"]
    checkpoint_path = paths["checkpoint"]
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    paths["config"].parent.mkdir(parents=True, exist_ok=True)
    paths["config"].write_text(yaml.safe_dump(asdict(cfg), sort_keys=True), encoding="utf-8")
    _write_json(paths["metrics"], metrics)
    _write_json(paths["confusion"], confusion_report)
    _write_json(paths["parameters"], parameter_report)
    _write_json(paths["scenarios"], scenario_report)

    config_payload = asdict(cfg)
    global_step = cfg.epochs * len(dataset.train)
    checkpoint = attach_checkpoint_schema(
        {
            "policy_type": POLICY_TYPE,
            "model_family": MODEL_FAMILY,
            "model_version": MODEL_VERSION,
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "config": config_payload,
            "run_id": paths["run_id"],
            "global_step": global_step,
            "hidden_dim": cfg.hidden_dim,
            "feature_contract": encoder.contract.to_metadata(),
            "checkpoint_metadata": build_v1_7_checkpoint_metadata(
                registry,
                run_id=str(paths["run_id"]),
            ),
            "dataset": {
                "path": str(dataset.root),
                "dataset_id": dataset.manifest["dataset_id"],
                "manifest_sha256": dataset.manifest_sha256,
                "schemas": dict(dataset.manifest.get("schemas", {})),
                "counts": dict(dataset.manifest.get("counts", {})),
                "compatibility": dict(dataset.manifest.get("compatibility", {})),
            },
            "lifecycle_carry_contract": _lifecycle_carry_contract(dataset),
            "scenario_validation": {
                "schema_version": SCENARIO_SCHEMA_VERSION,
                **dict(analyzer_scenario_report["summary"]),
            },
            "training_metrics": metrics["final"],
        },
        trainer_name=TRAINER_NAME,
        run_id=str(paths["run_id"]),
        checkpoint_kind="bootstrap",
        global_step=global_step,
        config=config_payload,
        git_commit=git_commit(),
        seed=cfg.seed,
        environment_progress={
            "epochs": cfg.epochs,
            "training_samples": len(dataset.train),
            "validation_samples": len(dataset.validation),
        },
    )
    checkpoint["state_hash"] = checkpoint_state_hash(checkpoint)
    checkpoint_errors = validate_v1_7_strategy_manager_checkpoint_payload(
        checkpoint,
        registry=registry,
    )
    if checkpoint_errors:
        raise ValueError("generated checkpoint is invalid: " + "; ".join(checkpoint_errors))
    torch.save(checkpoint, checkpoint_path)

    warnings = []
    counts = dataset.manifest.get("counts", {})
    if int(counts.get("legacy", 0)) or int(counts.get("rejected", 0)):
        warnings.append("audited_nontraining_records_excluded")
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_id": paths["run_id"],
        "created_at_utc": utc_timestamp(),
        "seed": cfg.seed,
        "policy_type": POLICY_TYPE,
        "model_version": MODEL_VERSION,
        "dataset": checkpoint["dataset"],
        "checkpoint_metadata": checkpoint["checkpoint_metadata"],
        "feature_contract": encoder.contract.to_metadata(),
        "lifecycle_carry_contract": checkpoint["lifecycle_carry_contract"],
        "training_samples": len(dataset.train),
        "validation_samples": len(dataset.validation),
        "final_metrics": metrics["final"],
        "scenario_validation": {
            "analyzer": analyzer_scenario_report["summary"],
            "model_tactic_accuracy": scenario_report["model_validation"]["tactic_accuracy"],
        },
        "warnings": warnings,
        "checkpoint_path": str(checkpoint_path),
    }
    _write_json(paths["summary"], summary)
    manifest = write_artifact_manifest(
        run_dir=run_dir,
        run_id=str(paths["run_id"]),
        trainer_name=TRAINER_NAME,
        config=config_payload,
        git_commit=git_commit(),
        seed=cfg.seed,
        artifacts={
            "config": paths["config"],
            "metrics": paths["metrics"],
            "confusion_report": paths["confusion"],
            "parameter_report": paths["parameters"],
            "scenario_report": paths["scenarios"],
            "summary": paths["summary"],
        },
        checkpoints={"bootstrap": checkpoint_path},
        manifest_path=paths["manifest"],
        extra={
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_manifest_sha256": dataset.manifest_sha256,
            "checkpoint_metadata": checkpoint["checkpoint_metadata"],
            "feature_contract": encoder.contract.to_metadata(),
            "lifecycle_carry_contract": checkpoint["lifecycle_carry_contract"],
            "scenario_validation": analyzer_scenario_report["summary"],
            "compatibility": dataset.manifest.get("compatibility", {}),
        },
    )
    manifest_errors = validate_artifact_manifest(manifest, run_dir=run_dir)
    if manifest_errors:
        raise ValueError("generated artifact manifest is invalid: " + "; ".join(manifest_errors))
    return {
        **summary,
        "run_dir": str(run_dir),
        "metrics_path": str(paths["metrics"]),
        "confusion_report_path": str(paths["confusion"]),
        "parameter_report_path": str(paths["parameters"]),
        "scenario_report_path": str(paths["scenarios"]),
        "manifest_path": str(paths["manifest"]),
        "state_hash": checkpoint["state_hash"],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the v1.7.1 bootstrap Strategy Manager.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    result = train_v1_7_manager(load_config(args.config, args.set))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
