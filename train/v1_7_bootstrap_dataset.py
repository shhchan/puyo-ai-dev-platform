"""Build and validate versioned v1.7.1 behavior-cloning datasets."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from agents.beam_search import BUILD_POTENTIAL_SCHEMA_VERSION
from agents.state_analyzer import (
    ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
    ANALYZER_INPUT_SCHEMA_VERSION,
    AnalyzerInput,
    StateAnalyzer,
    simulator_from_snapshot,
)
from agents.v1_7_analyzer_manager import (
    ANALYZER_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
    V17AnalyzerManagerPolicy,
)
from agents.v1_7_strategy_manager import (
    FEATURE_SCHEMA_VERSION,
    PREVIEW_FEATURE_SCHEMA_VERSION,
    STRATEGY_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
    V17StrategyFeatureEncoder,
)
from agents.v1_7_tactics import TacticRegistry, load_tactic_registry
from eval.analyzer_scenarios import (
    DEFAULT_DATASET as DEFAULT_SCENARIO_DATASET,
    SCENARIO_REPORT_SCHEMA_VERSION,
    SCENARIO_SCHEMA_VERSION,
    evaluate_scenarios,
    load_scenarios,
    scenario_input,
)
from puyo_env.actions import NUM_ACTIONS, legal_action_indices, legal_action_mask


MANIFEST_SCHEMA_VERSION = "puyo.v1_7_bootstrap_dataset.manifest.v1"
SAMPLE_SCHEMA_VERSION = "puyo.v1_7_bootstrap_dataset.sample.v1"
DEFAULT_SPLIT_SEED = 125
DEFAULT_VALIDATION_RATIO = 0.2
SPLIT_FILENAMES = {
    "train": "train.jsonl",
    "validation": "validation.jsonl",
    "legacy": "legacy.jsonl",
    "rejected": "rejected.jsonl",
}
_CURRENT_DIAGNOSTIC_SCHEMAS = {
    ANALYZER_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
    STRATEGY_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
}
_SHAPE_COMPATIBLE_DIAGNOSTIC_SCHEMAS = {
    "puyo.v1_7_analyzer_manager.diagnostics.v1",
    "puyo.v1_7_strategy_manager.diagnostics.v1",
}
_SUPPORTED_DIAGNOSTIC_SCHEMAS = (
    _CURRENT_DIAGNOSTIC_SCHEMAS | _SHAPE_COMPATIBLE_DIAGNOSTIC_SCHEMAS
)
_STRATEGY_DIAGNOSTIC_SCHEMAS = {
    STRATEGY_MANAGER_DIAGNOSTICS_SCHEMA_VERSION,
    "puyo.v1_7_strategy_manager.diagnostics.v1",
}
_LEGACY_ANALYZER_DIAGNOSTICS_SCHEMA_VERSION = (
    "puyo.state_analyzer.diagnostics.v1"
)
_LEGACY_TACTIC_SCHEMA_VERSION = "tactic-schema-v1"
_LEGACY_TACTIC_REGISTRY_VERSION = "v1.7.0"
_LIFECYCLE_FIELDS = (
    "score_carry",
    "all_clear_achieved",
    "all_clear_bonus_pending",
    "all_clear_bonus_consumed",
)
_REQUIRED_FEATURES = tuple(
    f"{side}.{field}"
    for side in ("own", "opponent")
    for field in _LIFECYCLE_FIELDS
)
_VOLATILE_KEYS = {
    "elapsed_seconds",
    "planner_latency_overrun",
    "policy_elapsed_seconds",
}


@dataclass(frozen=True)
class _SourceRecord:
    source_path: str
    source_kind: str
    record_id: str
    payload: Mapping[str, Any]
    source_schema_version: str | None = None
    envelope: Mapping[str, Any] | None = None
    force_validation: bool = False
    scenario: Mapping[str, Any] | None = None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _json_value(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _json_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_volatile(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_strip_volatile(item) for item in value]
    return value


def _builder_revision() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    relative = Path(__file__).resolve().relative_to(repo_root)
    commands = (
        ["git", "log", "-1", "--format=%H", "--", str(relative)],
        ["git", "rev-parse", "HEAD"],
    )
    for command in commands:
        try:
            value = subprocess.check_output(
                command,
                cwd=repo_root,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            continue
        if value:
            return value
    return "unknown"


def _read_source(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as document_error:
        records = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as line_error:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON/JSONL: {line_error}"
                ) from document_error
        if not records:
            raise ValueError(f"{path}: source is empty") from document_error
        return records


def _runtime_info(analyzer_input: AnalyzerInput) -> dict[str, Any]:
    own = simulator_from_snapshot(analyzer_input.own)
    opponent = simulator_from_snapshot(analyzer_input.opponent)
    own_packets = [
        {"amount": packet.amount, "turns_to_arrival": packet.deadline}
        for packet in analyzer_input.own.incoming
    ]
    opponent_packets = analyzer_input.opponent.incoming
    return {
        "simulator": own,
        "opponent_simulator": opponent,
        "score": analyzer_input.own.score,
        "score_carry": analyzer_input.own.score_carry,
        "sent_ojama_total": analyzer_input.own.sent_ojama_total,
        "canceled_ojama_total": analyzer_input.own.canceled_ojama_total,
        "received_ojama_total": analyzer_input.own.received_ojama_total,
        "opponent_score": analyzer_input.opponent.score,
        "opponent_score_carry": analyzer_input.opponent.score_carry,
        "opponent_sent_ojama_total": analyzer_input.opponent.sent_ojama_total,
        "opponent_canceled_ojama_total": analyzer_input.opponent.canceled_ojama_total,
        "opponent_received_ojama_total": analyzer_input.opponent.received_ojama_total,
        "incoming_attack_packets": own_packets,
        "incoming_ojama": sum(packet.amount for packet in analyzer_input.own.incoming),
        "pending_ojama": sum(packet.amount for packet in analyzer_input.own.incoming),
        "incoming_turns": min(
            (packet.deadline for packet in analyzer_input.own.incoming),
            default=0,
        ),
        "opponent_pending_ojama": sum(packet.amount for packet in opponent_packets),
        "opponent_incoming_turns": min(
            (packet.deadline for packet in opponent_packets),
            default=0,
        ),
        "step_count": analyzer_input.turn,
        "tick": analyzer_input.tick,
        "policy_deadline": analyzer_input.policy_deadline,
        "action_mask": legal_action_mask(own),
    }


def _scenario_records(path: Path) -> tuple[list[_SourceRecord], dict[str, Any]]:
    source_path = _display_path(path)
    scenarios = load_scenarios(path)
    results = evaluate_scenarios(scenarios)
    records = []
    for scenario, result in zip(scenarios, results):
        analyzer_input = scenario_input(scenario)
        policy = V17AnalyzerManagerPolicy()
        policy.select_action({}, _runtime_info(analyzer_input))
        if policy.last_analyzer_input != analyzer_input:
            raise ValueError(
                f"scenario {scenario['name']} cannot be reconstructed without input loss"
            )
        records.append(
            _SourceRecord(
                source_path=source_path,
                source_kind="scripted_scenario",
                record_id=str(scenario["name"]),
                payload=policy.tactical_diagnostics,
                source_schema_version=SCENARIO_SCHEMA_VERSION,
                force_validation=True,
                scenario={
                    "name": result.name,
                    "category": result.category,
                    "situation": result.situation,
                    "expected_behavior": list(result.expected_behavior),
                    "non_goals": list(result.non_goals),
                    "passed": result.passed,
                    "checks": list(result.checks),
                },
            )
        )
    return records, {
        "path": source_path,
        "sha256": _file_sha256(path),
        "kind": "scripted_scenario",
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "records": len(records),
        "duplicates_removed": 0,
    }


def _scenario_report_records(
    path: Path,
    payload: Mapping[str, Any],
) -> tuple[list[_SourceRecord], dict[str, Any]]:
    source_path = _display_path(path)
    records = []
    for index, result in enumerate(payload.get("results", ())):
        if not isinstance(result, Mapping) or not isinstance(result.get("input"), Mapping):
            continue
        analyzer_input = AnalyzerInput.from_dict(result["input"])
        policy = V17AnalyzerManagerPolicy()
        policy.select_action({}, _runtime_info(analyzer_input))
        records.append(
            _SourceRecord(
                source_path=source_path,
                source_kind="scenario_report",
                record_id=str(result.get("name", index)),
                payload=policy.tactical_diagnostics,
                source_schema_version=SCENARIO_REPORT_SCHEMA_VERSION,
                force_validation=True,
                scenario={
                    key: copy.deepcopy(result.get(key))
                    for key in (
                        "name",
                        "category",
                        "situation",
                        "expected_behavior",
                        "non_goals",
                        "passed",
                        "checks",
                    )
                },
            )
        )
    return records, {
        "path": source_path,
        "sha256": _file_sha256(path),
        "kind": "scenario_report",
        "schema_version": SCENARIO_REPORT_SCHEMA_VERSION,
        "records": len(records),
        "duplicates_removed": 0,
    }


def _replay_records(
    path: Path,
    payload: Mapping[str, Any],
) -> tuple[list[_SourceRecord], dict[str, Any]]:
    source_path = _display_path(path)
    records = []
    seen = set()
    duplicates = 0
    ticks = payload.get("ticks", ())
    for tick in ticks if isinstance(ticks, list) else ():
        if not isinstance(tick, Mapping):
            continue
        policy_diagnostics = tick.get("policy_diagnostics", {})
        if not isinstance(policy_diagnostics, Mapping):
            continue
        agents = sorted(str(agent) for agent in policy_diagnostics)
        for agent in agents:
            diagnostics = policy_diagnostics.get(agent)
            if not isinstance(diagnostics, Mapping):
                continue
            schema_version = str(diagnostics.get("schema_version", ""))
            if schema_version not in _SUPPORTED_DIAGNOSTIC_SCHEMAS:
                continue
            plan_id = str(diagnostics.get("plan_id") or _json_digest(diagnostics))
            decision_id = f"{agent}:{plan_id}"
            if decision_id in seen:
                duplicates += 1
                continue
            seen.add(decision_id)
            other_agents = [candidate for candidate in agents if candidate != agent]
            opponent = other_agents[0] if other_agents else ""
            attacks = tick.get("attack_diagnostics", {})
            runtime_features = {}
            if isinstance(attacks, Mapping):
                runtime_features = {
                    "own": attacks.get(agent),
                    "opponent": attacks.get(opponent),
                }
            records.append(
                _SourceRecord(
                    source_path=source_path,
                    source_kind=(
                        "planner_preview"
                        if schema_version in _STRATEGY_DIAGNOSTIC_SCHEMAS
                        else "v1_7_action_log"
                    ),
                    record_id=(
                        f"tick:{tick.get('tick', 0)}:agent:{agent}:plan:{plan_id}"
                    ),
                    payload=diagnostics,
                    source_schema_version=schema_version,
                    envelope={"runtime_features": runtime_features},
                )
            )
    kinds = sorted({record.source_kind for record in records})
    return records, {
        "path": source_path,
        "sha256": _file_sha256(path),
        "kind": kinds[0] if len(kinds) == 1 else "mixed_action_log",
        "schema_version": str(payload.get("format", "")) or None,
        "records": len(records),
        "duplicates_removed": duplicates,
    }


def _list_records(
    path: Path,
    values: Sequence[Any],
) -> tuple[list[_SourceRecord], dict[str, Any]]:
    source_path = _display_path(path)
    records = []
    kinds = set()
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            continue
        schema_version = str(value.get("schema_version", ""))
        if schema_version in _SUPPORTED_DIAGNOSTIC_SCHEMAS:
            kind = (
                "planner_preview"
                if schema_version in _STRATEGY_DIAGNOSTIC_SCHEMAS
                else "v1_7_action_log"
            )
        elif "manager_features" in value and "selected_profile_name" in value:
            kind = "legacy_checkpoint_teacher"
        else:
            kind = "unknown"
        kinds.add(kind)
        records.append(
            _SourceRecord(
                source_path=source_path,
                source_kind=kind,
                record_id=str(value.get("sample_id", value.get("scenario", index))),
                payload=value,
                source_schema_version=schema_version or None,
            )
        )
    return records, {
        "path": source_path,
        "sha256": _file_sha256(path),
        "kind": next(iter(kinds)) if len(kinds) == 1 else "mixed_records",
        "schema_version": None,
        "records": len(records),
        "duplicates_removed": 0,
    }


def _source_records(path: Path) -> tuple[list[_SourceRecord], dict[str, Any]]:
    payload = _read_source(path)
    if isinstance(payload, Mapping):
        schema_version = str(payload.get("schema_version", ""))
        if schema_version == SCENARIO_SCHEMA_VERSION:
            return _scenario_records(path)
        if schema_version == SCENARIO_REPORT_SCHEMA_VERSION:
            return _scenario_report_records(path, payload)
        if payload.get("format") == "puyo-realtime-match-v1":
            return _replay_records(path, payload)
        if schema_version in _SUPPORTED_DIAGNOSTIC_SCHEMAS:
            return _list_records(path, [payload])
        return _list_records(path, [payload])
    if isinstance(payload, list):
        return _list_records(path, payload)
    raise ValueError(f"{path}: unsupported JSON root type {type(payload).__name__}")


def _source_feature_presence(analyzer_input: Mapping[str, Any]) -> dict[str, bool]:
    return {
        feature: (
            isinstance(analyzer_input.get(side), Mapping)
            and field in analyzer_input[side]
        )
        for feature in _REQUIRED_FEATURES
        for side, field in [feature.split(".", 1)]
    }


def _recovery_candidates(record: _SourceRecord, side: str) -> list[tuple[str, Mapping[str, Any]]]:
    candidates = []
    lifecycle = record.payload.get("lifecycle_features")
    if isinstance(lifecycle, Mapping) and isinstance(lifecycle.get(side), Mapping):
        candidates.append((f"lifecycle_features.{side}", lifecycle[side]))
    envelope = record.envelope or {}
    runtime = envelope.get("runtime_features") if isinstance(envelope, Mapping) else None
    if isinstance(runtime, Mapping) and isinstance(runtime.get(side), Mapping):
        candidates.append((f"runtime_features.{side}", runtime[side]))
    if side == "own":
        request = record.payload.get("planner_request")
        if isinstance(request, Mapping) and isinstance(request.get("runtime_context"), Mapping):
            candidates.append(("planner_request.runtime_context", request["runtime_context"]))
    return candidates


def _recover_features(
    record: _SourceRecord,
    analyzer_input: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, bool], list[str], list[str]]:
    value = copy.deepcopy(dict(analyzer_input))
    source_presence = _source_feature_presence(analyzer_input)
    missing = []
    migrations = []
    for side in ("own", "opponent"):
        player = value.get(side)
        if not isinstance(player, Mapping):
            missing.extend(f"{side}.{field}" for field in _LIFECYCLE_FIELDS)
            continue
        player = dict(player)
        value[side] = player
        candidates = _recovery_candidates(record, side)
        for field in _LIFECYCLE_FIELDS:
            feature = f"{side}.{field}"
            if field in player:
                continue
            recovered = False
            for source, candidate in candidates:
                if field not in candidate:
                    continue
                player[field] = candidate[field]
                migrations.append(f"{feature}<-{source}.{field}")
                recovered = True
                break
            if not recovered:
                missing.append(feature)
    return value, source_presence, missing, migrations


def _source_descriptor(record: _SourceRecord) -> dict[str, Any]:
    return {
        "kind": record.source_kind,
        "path": record.source_path,
        "record_id": record.record_id,
        "schema_version": record.source_schema_version,
        "record_sha256": _json_digest(record.payload),
    }


def _finalize_sample(sample: dict[str, Any], *, group_id: str) -> dict[str, Any]:
    sample["group_id"] = group_id
    sample["sample_id"] = _json_digest(sample)
    return sample


def _nontraining_sample(
    record: _SourceRecord,
    *,
    split: str,
    status: str,
    reasons: Sequence[str],
    source_presence: Mapping[str, bool] | None = None,
    legacy_payload: Any | None = None,
) -> dict[str, Any]:
    presence = dict(source_presence or {feature: False for feature in _REQUIRED_FEATURES})
    sample = {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "split": split,
        "source": _source_descriptor(record),
        "analyzer": None,
        "features": None,
        "teacher": None,
        "outcomes": None,
        "compatibility": {
            "status": status,
            "training_eligible": False,
            "feature_presence": presence,
            "source_feature_presence": presence,
            "missing_features": [key for key, present in presence.items() if not present],
            "migration_sources": [],
            "reasons": sorted(set(str(reason) for reason in reasons)),
        },
        "legacy_payload": _strip_volatile(legacy_payload),
    }
    return _finalize_sample(
        sample,
        group_id=_json_digest(
            {"source": _source_descriptor(record), "status": status, "reasons": reasons}
        ),
    )


def _validate_parameters(registry: TacticRegistry, tactic_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("teacher parameters must be an object")
    tactic = registry.tactic(tactic_id)
    result = {}
    for section in ("objective", "constraints", "planner"):
        section_value = value.get(section)
        if not isinstance(section_value, Mapping):
            raise ValueError(f"teacher parameters.{section} must be an object")
        expected = tactic.parameters[section]
        if set(section_value) != set(expected):
            raise ValueError(
                f"teacher parameters.{section} keys do not match tactic registry"
            )
        result[section] = {}
        for name, spec in expected.items():
            parameter = section_value[name]
            spec.validate(parameter)
            result[section][name] = parameter
    return result


def _teacher_label(
    diagnostics: Mapping[str, Any],
    analyzer_input: AnalyzerInput,
    encoded: Any,
    registry: TacticRegistry,
) -> dict[str, Any]:
    selected = diagnostics.get("selected_tactic")
    if not isinstance(selected, Mapping):
        raise ValueError("selected_tactic is required")
    tactic_id = str(selected.get("tactic_id", ""))
    if tactic_id not in encoded.contract.tactic_ids:
        raise ValueError(f"unknown teacher tactic: {tactic_id or '<empty>'}")
    tactic_index = encoded.contract.tactic_ids.index(tactic_id)
    if not encoded.eligibility_mask[tactic_index]:
        raise ValueError(f"teacher tactic is not eligible: {tactic_id}")
    parameters = selected.get("parameters")
    if parameters is None:
        request = diagnostics.get("planner_request")
        parameters = request.get("parameters") if isinstance(request, Mapping) else None
    parameters = _validate_parameters(registry, tactic_id, parameters)
    worker = diagnostics.get("worker")
    result = worker.get("result") if isinstance(worker, Mapping) else None
    if not isinstance(result, Mapping) or "action" not in result:
        raise ValueError("worker.result.action is required")
    action = int(result["action"])
    if not 0 <= action < NUM_ACTIONS:
        raise ValueError(f"teacher action {action} is outside [0, {NUM_ACTIONS})")
    legal = legal_action_indices(simulator_from_snapshot(analyzer_input.own))
    if action not in legal:
        raise ValueError(f"teacher action {action} is illegal for the analyzer snapshot")
    return {
        "tactic_id": tactic_id,
        "tactic_index": tactic_index,
        "parameters": parameters,
        "action": action,
        "reason": selected.get("reason", diagnostics.get("reason")),
        "reason_code": selected.get("reason_code", diagnostics.get("reason_code")),
        "source_policy_type": (
            diagnostics.get("model_metadata", {}).get("policy_type")
            if isinstance(diagnostics.get("model_metadata"), Mapping)
            else None
        ),
    }


def _outcomes(record: _SourceRecord) -> dict[str, Any]:
    diagnostics = record.payload
    candidates = diagnostics.get("tactic_candidates", ())
    previews = []
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or not candidate.get("previewed"):
                continue
            previews.append(
                {
                    "tactic_id": candidate.get("tactic_id"),
                    "preview": _strip_volatile(candidate.get("preview")),
                    "final_score": candidate.get("final_score"),
                    "selected": bool(candidate.get("selected")),
                }
            )
    return {
        "planner_request": _strip_volatile(diagnostics.get("planner_request")),
        "worker": _strip_volatile(diagnostics.get("worker")),
        "plan": _strip_volatile(diagnostics.get("plan")),
        "candidate_previews": previews,
        "tactic_candidates": _strip_volatile(candidates),
        "scenario": _json_value(record.scenario) if record.scenario is not None else None,
    }


def _normalize_current_record(
    record: _SourceRecord,
    *,
    analyzer: StateAnalyzer,
    encoder: V17StrategyFeatureEncoder,
    registry: TacticRegistry,
) -> dict[str, Any]:
    analyzer_section = record.payload.get("analyzer")
    raw_input = analyzer_section.get("input") if isinstance(analyzer_section, Mapping) else None
    if not isinstance(raw_input, Mapping):
        return _nontraining_sample(
            record,
            split="legacy",
            status="legacy",
            reasons=["missing_analyzer_input"],
            legacy_payload=record.payload,
        )
    recovered, source_presence, missing, migrations = _recover_features(record, raw_input)
    if missing:
        return _nontraining_sample(
            record,
            split="legacy",
            status="legacy",
            reasons=[f"missing_required_feature:{feature}" for feature in missing],
            source_presence=source_presence,
            legacy_payload=record.payload,
        )
    if recovered.get("schema_version") != ANALYZER_INPUT_SCHEMA_VERSION:
        return _nontraining_sample(
            record,
            split="legacy",
            status="legacy",
            reasons=[
                "unsupported_analyzer_input_schema:"
                f"{recovered.get('schema_version') or '<missing>'}"
            ],
            source_presence=source_presence,
            legacy_payload=record.payload,
        )
    try:
        analyzer_input = AnalyzerInput.from_dict(recovered)
        diagnostics = analyzer.analyze(analyzer_input)
        encoded = encoder.encode(analyzer_input, diagnostics)
        teacher = _teacher_label(record.payload, analyzer_input, encoded, registry)
    except (KeyError, TypeError, ValueError) as exc:
        return _nontraining_sample(
            record,
            split="rejected",
            status="rejected",
            reasons=[f"validation_error:{exc}"],
            source_presence=source_presence,
        )
    status = "migrated" if migrations else "current"
    feature_presence = {feature: True for feature in _REQUIRED_FEATURES}
    sample = {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "split": "validation" if record.force_validation else "pending",
        "source": _source_descriptor(record),
        "analyzer": {
            "input": analyzer_input.to_dict(),
            "diagnostics": diagnostics.to_dict(),
        },
        "features": {
            "schema_version": FEATURE_SCHEMA_VERSION,
            "context": list(encoded.context),
            "tactics": [list(values) for values in encoded.tactics],
            "eligibility_mask": list(encoded.eligibility_mask),
        },
        "teacher": teacher,
        "outcomes": _outcomes(record),
        "compatibility": {
            "status": status,
            "training_eligible": True,
            "feature_presence": feature_presence,
            "source_feature_presence": source_presence,
            "missing_features": [],
            "migration_sources": migrations,
            "reasons": [],
        },
        "legacy_payload": None,
    }
    return _finalize_sample(sample, group_id=_json_digest(analyzer_input.to_dict()))


def _normalize_record(
    record: _SourceRecord,
    *,
    analyzer: StateAnalyzer,
    encoder: V17StrategyFeatureEncoder,
    registry: TacticRegistry,
) -> dict[str, Any]:
    if record.source_kind == "legacy_checkpoint_teacher":
        return _nontraining_sample(
            record,
            split="legacy",
            status="legacy",
            reasons=["legacy_manager_feature_schema"],
            legacy_payload={
                key: copy.deepcopy(record.payload.get(key))
                for key in (
                    "scenario",
                    "category",
                    "board",
                    "next_pairs",
                    "manager_features",
                    "selected_profile_id",
                    "selected_profile_name",
                    "selected_action_id",
                    "counterfactuals",
                )
            },
        )
    if record.source_kind == "unknown":
        return _nontraining_sample(
            record,
            split="rejected",
            status="rejected",
            reasons=["unsupported_source_record"],
        )
    return _normalize_current_record(
        record,
        analyzer=analyzer,
        encoder=encoder,
        registry=registry,
    )


def _split_value(group_id: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{group_id}".encode("ascii")).digest()
    return int.from_bytes(digest, "big") / float(1 << (len(digest) * 8))


def _assign_splits(
    samples: list[dict[str, Any]],
    *,
    split_seed: int,
    validation_ratio: float,
) -> None:
    forced_groups = {
        sample["group_id"]
        for sample in samples
        if sample["split"] == "validation"
        and sample["compatibility"]["training_eligible"]
    }
    candidates = []
    for sample in samples:
        if not sample["compatibility"]["training_eligible"]:
            continue
        candidates.append(sample)
        if sample["group_id"] in forced_groups:
            sample["split"] = "validation"
        else:
            sample["split"] = (
                "validation"
                if _split_value(sample["group_id"], split_seed) < validation_ratio
                else "train"
            )
    if candidates and not any(sample["split"] == "train" for sample in candidates):
        movable = [sample for sample in candidates if sample["group_id"] not in forced_groups]
        if movable:
            min(movable, key=lambda item: (_split_value(item["group_id"], split_seed), item["sample_id"]))[
                "split"
            ] = "train"


def _write_jsonl(path: Path, samples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(samples, key=lambda sample: str(sample["sample_id"]))
    text = "".join(_canonical_json(sample) + "\n" for sample in ordered)
    path.write_text(text, encoding="utf-8")
    return {
        "path": path.name,
        "sha256": _file_sha256(path),
        "size_bytes": path.stat().st_size,
        "samples": len(ordered),
    }


def _reason_counts(samples: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for sample in samples:
        counts.update(str(reason) for reason in sample["compatibility"].get("reasons", ()))
    return dict(sorted(counts.items()))


def _migration_counts(samples: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for sample in samples:
        for source in sample["compatibility"].get("migration_sources", ()):
            counts.update([str(source).split("<-", 1)[-1]])
    return dict(sorted(counts.items()))


def _identity_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(manifest[key])
        for key in (
            "schema_version",
            "sample_schema_version",
            "builder",
            "schemas",
            "feature_contract",
            "sources",
            "split",
            "files",
            "counts",
            "compatibility",
            "validation_scenarios",
        )
    }


def build_bootstrap_dataset(
    source_paths: Sequence[str | Path],
    output_dir: str | Path,
    *,
    split_seed: int = DEFAULT_SPLIT_SEED,
    validation_ratio: float = DEFAULT_VALIDATION_RATIO,
    include_default_scenarios: bool = True,
) -> dict[str, Any]:
    """Normalize supported sources and write a deterministic split dataset."""

    if split_seed < 0:
        raise ValueError("split_seed must be non-negative")
    if not 0.0 <= validation_ratio < 1.0:
        raise ValueError("validation_ratio must be in [0, 1)")
    paths = [Path(path) for path in source_paths]
    if include_default_scenarios:
        paths.append(DEFAULT_SCENARIO_DATASET)
    unique_paths = {str(path.resolve()): path for path in paths}
    if not unique_paths:
        raise ValueError("at least one source is required")

    records = []
    source_metadata = []
    for path in sorted(unique_paths.values(), key=lambda item: str(item)):
        if not path.is_file():
            raise FileNotFoundError(path)
        source_records, metadata = _source_records(path)
        records.extend(source_records)
        source_metadata.append(metadata)

    registry = load_tactic_registry()
    analyzer = StateAnalyzer()
    encoder = V17StrategyFeatureEncoder(registry)
    normalized = [
        _normalize_record(
            record,
            analyzer=analyzer,
            encoder=encoder,
            registry=registry,
        )
        for record in records
    ]
    deduplicated = {}
    cross_source_duplicates = 0
    for sample in sorted(normalized, key=lambda item: (item["sample_id"], item["source"]["path"])):
        if sample["sample_id"] in deduplicated:
            cross_source_duplicates += 1
            continue
        deduplicated[sample["sample_id"]] = sample
    samples = list(deduplicated.values())
    _assign_splits(samples, split_seed=split_seed, validation_ratio=validation_ratio)
    for sample in samples:
        sample_without_id = dict(sample)
        sample_without_id.pop("sample_id", None)
        sample["sample_id"] = _json_digest(sample_without_id)

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    file_records = {
        split: _write_jsonl(
            target / filename,
            (sample for sample in samples if sample["split"] == split),
        )
        for split, filename in SPLIT_FILENAMES.items()
    }
    counts = Counter(sample["split"] for sample in samples)
    status_counts = Counter(sample["compatibility"]["status"] for sample in samples)
    scenario_names = sorted(
        str(sample["outcomes"]["scenario"]["name"])
        for sample in samples
        if sample["split"] == "validation"
        and isinstance(sample.get("outcomes"), Mapping)
        and isinstance(sample["outcomes"].get("scenario"), Mapping)
    )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "sample_schema_version": SAMPLE_SCHEMA_VERSION,
        "dataset_id": "",
        "builder": {
            "module": "train.v1_7_bootstrap_dataset",
            "revision": _builder_revision(),
        },
        "schemas": {
            "analyzer_input": ANALYZER_INPUT_SCHEMA_VERSION,
            "analyzer_diagnostics": ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
            "build_potential": BUILD_POTENTIAL_SCHEMA_VERSION,
            "scenario": SCENARIO_SCHEMA_VERSION,
            "scenario_report": SCENARIO_REPORT_SCHEMA_VERSION,
            "feature": FEATURE_SCHEMA_VERSION,
            "preview_feature": PREVIEW_FEATURE_SCHEMA_VERSION,
            "tactic_registry": registry.schema_version,
            "tactic_registry_version": registry.registry_version,
        },
        "feature_contract": encoder.contract.to_metadata(),
        "sources": source_metadata,
        "split": {
            "seed": split_seed,
            "validation_ratio": validation_ratio,
            "group_key": "analyzer_input_sha256",
            "forced_validation_source": SCENARIO_SCHEMA_VERSION,
        },
        "files": file_records,
        "counts": {
            "source_records": len(records),
            "samples": len(samples),
            "train": counts["train"],
            "validation": counts["validation"],
            "legacy": counts["legacy"],
            "rejected": counts["rejected"],
            "current": status_counts["current"],
            "migrated": status_counts["migrated"],
            "duplicates_removed": (
                cross_source_duplicates
                + sum(int(source["duplicates_removed"]) for source in source_metadata)
            ),
        },
        "compatibility": {
            "migration_sources": _migration_counts(samples),
            "legacy_reasons": _reason_counts(
                sample for sample in samples if sample["split"] == "legacy"
            ),
            "rejection_reasons": _reason_counts(
                sample for sample in samples if sample["split"] == "rejected"
            ),
        },
        "validation_scenarios": {
            "schema_version": SCENARIO_SCHEMA_VERSION,
            "count": len(scenario_names),
            "names": scenario_names,
        },
    }
    manifest["dataset_id"] = _json_digest(_identity_payload(manifest))
    (target / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    errors = validate_bootstrap_dataset(target)
    if errors:
        raise ValueError("generated dataset is invalid: " + "; ".join(errors))
    return manifest


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: sample must be an object")
        records.append(value)
    return records


def _shape_compatible_legacy_manifest_errors(
    manifest: Mapping[str, Any],
    *,
    encoder: V17StrategyFeatureEncoder,
    registry: TacticRegistry,
) -> list[str]:
    """Validate the metadata boundary for the one shape-compatible legacy path."""

    errors = []
    feature_contract = manifest.get("feature_contract")
    expected_contract = encoder.contract.to_metadata()
    if not isinstance(feature_contract, Mapping):
        return ["legacy feature_contract must be a mapping"]
    source_registry_version = feature_contract.get("registry_version")
    if source_registry_version not in {
        _LEGACY_TACTIC_REGISTRY_VERSION,
        encoder.contract.registry_version,
    }:
        errors.append(
            "legacy feature_contract.registry_version is not shape-compatible"
        )
    for field, expected in expected_contract.items():
        if field == "registry_version":
            continue
        if feature_contract.get(field) != expected:
            errors.append(
                "legacy feature_contract shape mismatch for "
                f"{field}: expected {expected!r}, got {feature_contract.get(field)!r}"
            )

    schemas = manifest.get("schemas")
    if not isinstance(schemas, Mapping):
        return errors + ["legacy manifest schemas must be a mapping"]
    expected_schemas = {
        "analyzer_input": ANALYZER_INPUT_SCHEMA_VERSION,
        "analyzer_diagnostics": _LEGACY_ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
        "feature": FEATURE_SCHEMA_VERSION,
        "preview_feature": PREVIEW_FEATURE_SCHEMA_VERSION,
    }
    for field, expected in expected_schemas.items():
        if schemas.get(field) != expected:
            errors.append(
                "legacy manifest schema mismatch for "
                f"{field}: expected {expected!r}, got {schemas.get(field)!r}"
            )
    if schemas.get("build_potential") is not None:
        errors.append("legacy manifest must not declare a BuildPotential schema")
    if schemas.get("tactic_registry") not in {
        _LEGACY_TACTIC_SCHEMA_VERSION,
        registry.schema_version,
    }:
        errors.append("legacy tactic registry schema is unsupported")
    if schemas.get("tactic_registry_version") != source_registry_version:
        errors.append(
            "legacy tactic registry version does not match the feature contract"
        )
    return errors


def _legacy_analyzer_diagnostics_projection(diagnostics: Any) -> Any:
    """Project current diagnostics onto the stored v1 JSON contract."""

    payload = _json_value(diagnostics.to_dict())
    payload["schema_version"] = _LEGACY_ANALYZER_DIAGNOSTICS_SCHEMA_VERSION
    for side in ("own", "opponent"):
        player = payload.get(side)
        if isinstance(player, Mapping):
            player = dict(player)
            player.pop("build_potential", None)
            payload[side] = player
    return _json_value(payload)


def _numeric_vector_errors(
    value: Any,
    *,
    expected_length: int,
    label: str,
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return [f"{label} must be a numeric array"]
    errors = []
    if len(value) != expected_length:
        errors.append(
            f"{label} length mismatch: expected {expected_length}, got {len(value)}"
        )
    if any(not _is_finite_number(item) for item in value):
        errors.append(f"{label} must contain only finite numbers")
    return errors


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _validate_legacy_features(
    features: Any,
    *,
    encoded: Any,
    encoder: V17StrategyFeatureEncoder,
) -> tuple[list[str], tuple[bool, ...] | None]:
    errors = []
    if not isinstance(features, Mapping):
        return ["features must be a mapping"], None
    if features.get("schema_version") != FEATURE_SCHEMA_VERSION:
        errors.append("feature schema_version is unsupported")

    context = features.get("context")
    errors.extend(
        _numeric_vector_errors(
            context,
            expected_length=encoder.contract.context_dim,
            label="features.context",
        )
    )
    if isinstance(context, (list, tuple)) and list(context) != list(encoded.context):
        errors.append("context features do not match analyzer input")

    tactics = features.get("tactics")
    if not isinstance(tactics, (list, tuple)):
        errors.append("features.tactics must be an array")
    else:
        expected_tactics = len(encoder.contract.tactic_ids)
        if len(tactics) != expected_tactics:
            errors.append(
                "features.tactics length mismatch: "
                f"expected {expected_tactics}, got {len(tactics)}"
            )
        for index, values in enumerate(tactics):
            errors.extend(
                _numeric_vector_errors(
                    values,
                    expected_length=encoder.contract.tactic_dim,
                    label=f"features.tactics[{index}]",
                )
            )

    raw_mask = features.get("eligibility_mask")
    if not isinstance(raw_mask, (list, tuple)):
        errors.append("features.eligibility_mask must be a boolean array")
        return errors, None
    if len(raw_mask) != len(encoder.contract.tactic_ids):
        errors.append(
            "features.eligibility_mask length mismatch: expected "
            f"{len(encoder.contract.tactic_ids)}, got {len(raw_mask)}"
        )
    if any(not isinstance(item, bool) for item in raw_mask):
        errors.append("features.eligibility_mask must contain only booleans")
        return errors, None
    return errors, tuple(raw_mask)


def _validate_training_sample(
    sample: Mapping[str, Any],
    *,
    encoder: V17StrategyFeatureEncoder,
    registry: TacticRegistry,
    legacy_projection: bool = False,
) -> list[str]:
    errors = []
    try:
        analyzer_section = sample["analyzer"]
        analyzer_input = AnalyzerInput.from_dict(analyzer_section["input"])
        diagnostics = StateAnalyzer().analyze(analyzer_input)
        encoded = encoder.encode(analyzer_input, diagnostics)
        stored_mask = None
        if legacy_projection:
            feature_errors, stored_mask = _validate_legacy_features(
                sample.get("features"),
                encoded=encoded,
                encoder=encoder,
            )
            errors.extend(feature_errors)
            expected_diagnostics = _legacy_analyzer_diagnostics_projection(
                diagnostics
            )
            stored_diagnostics = _json_value(
                analyzer_section.get("diagnostics")
            )
        else:
            expected_features = {
                "schema_version": FEATURE_SCHEMA_VERSION,
                "context": list(encoded.context),
                "tactics": [list(values) for values in encoded.tactics],
                "eligibility_mask": list(encoded.eligibility_mask),
            }
            if sample.get("features") != expected_features:
                errors.append("encoded features do not match analyzer input")
            expected_diagnostics = _json_value(diagnostics.to_dict())
            stored_diagnostics = analyzer_section.get("diagnostics")
        if stored_diagnostics != expected_diagnostics:
            errors.append("analyzer diagnostics do not match analyzer input")
        expected_group = _json_digest(analyzer_input.to_dict())
        if sample.get("group_id") != expected_group:
            errors.append("group_id does not match analyzer input")
        teacher = sample.get("teacher")
        if not isinstance(teacher, Mapping):
            errors.append("teacher is required")
        else:
            _validate_parameters(registry, str(teacher.get("tactic_id", "")), teacher.get("parameters"))
            tactic_id = str(teacher.get("tactic_id", ""))
            if tactic_id not in encoder.contract.tactic_ids:
                errors.append("teacher tactic is unknown")
            else:
                index = encoder.contract.tactic_ids.index(tactic_id)
                if teacher.get("tactic_index") != index:
                    errors.append("teacher tactic_index is invalid")
                eligibility_mask = (
                    encoded.eligibility_mask
                    if stored_mask is None
                    else stored_mask
                )
                if index >= len(eligibility_mask) or not eligibility_mask[index]:
                    errors.append("teacher tactic is ineligible")
            action = int(teacher.get("action", -1))
            if not 0 <= action < NUM_ACTIONS:
                errors.append("teacher action is out of range")
            elif action not in legal_action_indices(simulator_from_snapshot(analyzer_input.own)):
                errors.append("teacher action is illegal")
        compatibility = sample.get("compatibility")
        if not isinstance(compatibility, Mapping) or not compatibility.get("training_eligible"):
            errors.append("training sample must be training_eligible")
        elif not all(compatibility.get("feature_presence", {}).get(key) for key in _REQUIRED_FEATURES):
            errors.append("training sample is missing required feature presence")
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"training sample validation failed: {exc}")
    return errors


def validate_bootstrap_dataset(
    dataset_dir: str | Path,
    *,
    allow_shape_compatible_legacy: bool = False,
) -> list[str]:
    """Return all manifest, checksum, schema, feature, and action errors.

    The opt-in legacy path accepts only the audited v1.7.0/pre-BuildPotential
    metadata contract. Checksums, sample identities, analyzer projections,
    numeric feature shapes, stored eligibility, and action legality stay strict.
    """

    root = Path(dataset_dir)
    manifest_path = root / "dataset_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest is unreadable: {exc}"]
    errors = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append("manifest schema_version is unsupported")
    if manifest.get("sample_schema_version") != SAMPLE_SCHEMA_VERSION:
        errors.append("sample schema_version is unsupported")
    try:
        if manifest.get("dataset_id") != _json_digest(_identity_payload(manifest)):
            errors.append("dataset_id mismatch")
    except KeyError as exc:
        errors.append(f"manifest is missing {exc.args[0]}")

    registry = load_tactic_registry()
    encoder = V17StrategyFeatureEncoder(registry)
    schemas = manifest.get("schemas")
    legacy_projection = bool(
        allow_shape_compatible_legacy
        and isinstance(schemas, Mapping)
        and schemas.get("analyzer_diagnostics")
        == _LEGACY_ANALYZER_DIAGNOSTICS_SCHEMA_VERSION
        and schemas.get("build_potential") is None
    )
    if legacy_projection:
        errors.extend(
            _shape_compatible_legacy_manifest_errors(
                manifest,
                encoder=encoder,
                registry=registry,
            )
        )
    else:
        try:
            encoder.contract.validate_metadata(manifest.get("feature_contract", {}))
        except ValueError as exc:
            errors.append(str(exc))
    all_samples = []
    split_samples = {}
    for split, filename in SPLIT_FILENAMES.items():
        path = root / filename
        file_record = manifest.get("files", {}).get(split)
        if not path.is_file() or not isinstance(file_record, Mapping):
            errors.append(f"{split} split file is missing")
            continue
        try:
            samples = _load_jsonl(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{split} split is unreadable: {exc}")
            continue
        split_samples[split] = samples
        all_samples.extend(samples)
        if file_record.get("path") != filename:
            errors.append(f"{split} split path mismatch")
        if file_record.get("sha256") != _file_sha256(path):
            errors.append(f"{split} split checksum mismatch")
        if file_record.get("size_bytes") != path.stat().st_size:
            errors.append(f"{split} split size mismatch")
        if file_record.get("samples") != len(samples):
            errors.append(f"{split} split sample count mismatch")
        for sample in samples:
            if sample.get("schema_version") != SAMPLE_SCHEMA_VERSION:
                errors.append(f"{split} sample schema_version is unsupported")
            if sample.get("split") != split:
                errors.append(f"sample {sample.get('sample_id')} is stored in the wrong split")
            sample_without_id = dict(sample)
            sample_id = sample_without_id.pop("sample_id", None)
            if sample_id != _json_digest(sample_without_id):
                errors.append(f"sample {sample_id} digest mismatch")
            if split in {"train", "validation"}:
                for issue in _validate_training_sample(
                    sample,
                    encoder=encoder,
                    registry=registry,
                    legacy_projection=legacy_projection,
                ):
                    errors.append(f"sample {sample_id}: {issue}")
            else:
                compatibility = sample.get("compatibility", {})
                if compatibility.get("training_eligible"):
                    errors.append(f"sample {sample_id}: nontraining split is training_eligible")

    ids = [sample.get("sample_id") for sample in all_samples]
    if len(ids) != len(set(ids)):
        errors.append("sample_id values are not unique")
    manifest_counts = manifest.get("counts", {})
    for split in SPLIT_FILENAMES:
        if manifest_counts.get(split) != len(split_samples.get(split, ())):
            errors.append(f"manifest {split} count mismatch")
    if manifest_counts.get("samples") != len(all_samples):
        errors.append("manifest total sample count mismatch")
    expected_scenarios = set(manifest.get("validation_scenarios", {}).get("names", ()))
    actual_scenarios = {
        str(sample["outcomes"]["scenario"]["name"])
        for sample in split_samples.get("validation", ())
        if isinstance(sample.get("outcomes"), Mapping)
        and isinstance(sample["outcomes"].get("scenario"), Mapping)
    }
    if expected_scenarios != actual_scenarios:
        errors.append("validation scenario set mismatch")
    if manifest.get("validation_scenarios", {}).get("count") != len(actual_scenarios):
        errors.append("validation scenario count mismatch")
    return errors


def load_bootstrap_split(dataset_dir: str | Path, split: str) -> list[dict[str, Any]]:
    """Load one validated split for the bootstrap training pipeline."""

    if split not in SPLIT_FILENAMES:
        raise ValueError(f"unknown split: {split}")
    errors = validate_bootstrap_dataset(dataset_dir)
    if errors:
        raise ValueError("invalid bootstrap dataset: " + "; ".join(errors))
    return _load_jsonl(Path(dataset_dir) / SPLIT_FILENAMES[split])


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build a normalized dataset")
    build.add_argument("--output", required=True)
    build.add_argument("--source", action="append", default=[])
    build.add_argument("--split-seed", type=int, default=DEFAULT_SPLIT_SEED)
    build.add_argument("--validation-ratio", type=float, default=DEFAULT_VALIDATION_RATIO)
    build.add_argument("--no-default-scenarios", action="store_true")
    validate = subparsers.add_parser("validate", help="validate an existing dataset")
    validate.add_argument("dataset_dir")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "build":
        manifest = build_bootstrap_dataset(
            args.source,
            args.output,
            split_seed=args.split_seed,
            validation_ratio=args.validation_ratio,
            include_default_scenarios=not args.no_default_scenarios,
        )
        print(f"dataset_id: {manifest['dataset_id']}")
        print(
            "samples: "
            f"train={manifest['counts']['train']} "
            f"validation={manifest['counts']['validation']} "
            f"legacy={manifest['counts']['legacy']} "
            f"rejected={manifest['counts']['rejected']}"
        )
        print(f"output: {args.output}")
        return 0
    errors = validate_bootstrap_dataset(args.dataset_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"valid: {args.dataset_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
