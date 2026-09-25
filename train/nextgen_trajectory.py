"""Versioned decision ledger and isolated training view for nextgen fixtures/runs.

The caller supplies the actual Diagnostics observed at decision time.  This module
never derives policy features from a later snapshot or uses private reproduction
metadata to construct an actor input.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from agents.nextgen_contracts import (
    CANDIDATE_BATCH_SCHEMA_VERSION,
    LEGACY_CANDIDATE_BATCH_SCHEMA_VERSION,
    DIAGNOSTICS_SCHEMA_VERSION,
    FEATURE_REGISTRY,
    FEATURE_REGISTRY_HASH,
    FEATURE_SCHEMA_VERSION,
    REQUEST_SCHEMA_VERSION,
    SELECTION_SCHEMA_VERSION,
    TACTIC_IDS,
    TACTIC_REGISTRY_HASH,
    CandidateBatch,
    Diagnostics,
    NextgenRequest,
    PolicyFeatures,
    Selection,
    ExecutionReceipt,
    semantic_digest,
)
from puyo_env.nextgen_public_snapshot import PublicTimingHistory
from train.artifacts import file_sha256, json_digest, validate_artifact_manifest, write_artifact_manifest

TRAJECTORY_SCHEMA = "puyo.nextgen.trajectory.v1"
EPISODE_SCHEMA = "puyo.nextgen.episode.v1"
EVENT_SCHEMA = "puyo.nextgen.events.v1"
EVIDENCE_SCHEMA = "puyo.nextgen.evidence.v1"
PUBLIC_REPLAY_SCHEMA = "puyo.nextgen.public_replay.v1"
PRIVATE_SCHEMA = "puyo.nextgen.private_reproduction.v1"
ORACLE_SCHEMA = "puyo.nextgen.oracle.v1"
RUN_SCHEMA = "puyo.nextgen.run.v1"
POLICY_INPUT_KEYS = frozenset(
    {"schema_version", "values", "missing", "action_mask", "feature_registry_hash", "tactic_registry_hash"}
)
PROVENANCE_KEYS = frozenset(
    {"source", "native", "checkpoint", "opponent", "seed_streams", "search_profile", "timing", "environment", "gate_report", "dataset_split", "template_schema"}
)
SEED_STREAMS = frozenset({"environment", "search", "selector"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _keys(value: Any, names: set[str] | frozenset[str], where: str) -> None:
    _require(isinstance(value, dict) and set(value) == names, f"{where} fields mismatch")


def _finite(value: Any, where: str) -> float:
    _require(type(value) in (int, float) and math.isfinite(value), f"{where} must be finite")
    return float(value)


def _decision_key(run_id: str, episode_id: str, player_id: int, decision_id: int) -> str:
    return f"{run_id}/{episode_id}/{player_id}/{decision_id}"


def _safe_id(value: str, where: str) -> None:
    _require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) is not None, f"unsafe {where}")


def _read_json(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"nonfinite JSON: {value}")))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line, object_pairs_hook=_unique_pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"nonfinite JSON: {value}")))
                for line in handle if line.strip()]


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8") as handle:
                for value in values:
                    handle.write(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


@dataclass(frozen=True)
class DecisionRecord:
    diagnostics: Diagnostics
    reward_components: Mapping[str, float]
    elapsed_match_ticks: int
    next_decision_id: int | None
    terminated: bool = False
    truncated: bool = False
    phase_after: Mapping[str, Any] | None = None
    switch_reason: str | None = None
    template_score: float | None = None
    template_probability: float | None = None
    template_rng_position: int | None = None
    behavior_logits: Sequence[float] | None = None
    value_trainable: bool = True


@dataclass(frozen=True)
class EpisodeRecord:
    episode_id: str
    decisions: Sequence[DecisionRecord]
    result: Mapping[str, Any]
    events: Sequence[Mapping[str, Any]] = ()
    private_reproduction: Mapping[str, Any] = field(default_factory=dict)
    oracle: Mapping[str, Any] = field(default_factory=dict)
    actual_metrics: Mapping[str, Any] = field(default_factory=dict)
    public_timing_history: PublicTimingHistory | None = None


def _validate_provenance(provenance: Mapping[str, Any]) -> None:
    _keys(dict(provenance), PROVENANCE_KEYS, "provenance")
    seeds = provenance["seed_streams"]
    _keys(seeds, SEED_STREAMS, "seed_streams")
    _require(all(type(x) is int and x >= 0 for x in seeds.values()), "invalid seed stream")
    _require(len(set(seeds.values())) == len(seeds), "seed streams must be independent")
    for name in PROVENANCE_KEYS - {"seed_streams"}:
        _require(isinstance(provenance[name], Mapping) and bool(provenance[name]), f"missing {name} provenance")
    for name in ("source", "native"):
        _require(re.fullmatch(r"[0-9a-f]{64}", str(provenance[name].get("sha256", ""))) is not None,
                 f"{name} SHA required")
    _require(isinstance(provenance["source"].get("git_commit"), str) and
             bool(provenance["source"]["git_commit"]), "source commit required")
    checkpoint_sha = provenance["checkpoint"].get("sha256")
    _require(checkpoint_sha is None or re.fullmatch(r"[0-9a-f]{64}", str(checkpoint_sha)) is not None,
             "invalid checkpoint SHA")
    _require(set(provenance["search_profile"]) >= {"profile_id", "shared_quota", "template_quota", "response_quota"},
             "search profile/quota required")
    _require(set(provenance["timing"]) >= {"schema_version", "digest", "latency_mode"}, "timing provenance required")
    _require(re.fullmatch(r"[0-9a-f]{64}", str(provenance["timing"].get("digest", ""))) is not None,
             "timing digest required")
    _require(set(provenance["environment"]) >= {"os", "cpu", "threads", "p50_ms", "p95_ms"},
             "environment provenance required")
    _require("p50_ms" in provenance["environment"] and "p95_ms" in provenance["environment"], "latency evidence required")
    _require("failures" in provenance["gate_report"], "gate failures must be explicit")
    _require(isinstance(provenance["gate_report"]["failures"], list), "gate failures must be a list")
    split = provenance["dataset_split"]
    _require(set(split) == {"unit", "train", "validation", "test"} and
             split["unit"] in ("episode", "color_series") and
             all(isinstance(split[name], list) for name in ("train", "validation", "test")),
             "dataset split must be explicit")


def _line(run_id: str, record: DecisionRecord, evidence_path: str, evidence_sha: str, summary_path: str) -> dict[str, Any]:
    d = record.diagnostics
    _require(isinstance(d, Diagnostics), "actual Diagnostics required")
    identity = d.request.identity
    key = _decision_key(run_id, identity.episode_id, identity.player_id, identity.decision_id)
    receipt, selection = d.receipt, d.selection
    actor_trainable = bool(receipt and receipt.outcome == "activated")
    if receipt is None:
        exclusion = "missing_receipt"
    elif not actor_trainable:
        exclusion = receipt.outcome
    else:
        exclusion = None
    _require(type(record.elapsed_match_ticks) is int and record.elapsed_match_ticks >= 0, "invalid elapsed ticks")
    _require(not (record.terminated and record.truncated), "terminated and truncated are exclusive")
    rewards = dict(record.reward_components)
    _require(all(isinstance(k, str) and k for k in rewards), "invalid reward name")
    rewards = {k: _finite(v, f"reward {k}") for k, v in rewards.items()}
    if record.template_score is not None:
        _finite(record.template_score, "template score")
    if record.template_probability is not None:
        _require(0 <= _finite(record.template_probability, "template probability") <= 1, "invalid template probability")
    if record.template_rng_position is not None:
        _require(type(record.template_rng_position) is int and record.template_rng_position >= 0, "invalid RNG position")
    if record.behavior_logits is not None:
        _require(selection is not None and selection.selector_kind == "rl" and len(record.behavior_logits) == 6,
                 "logits require RL selection and six values")
        logits = [_finite(x, "behavior logit") for x in record.behavior_logits]
    else:
        logits = None
    return {
        "schema_version": TRAJECTORY_SCHEMA,
        "identity": {"run_id": run_id, **identity.to_dict(), "batch_digest": d.batch.digest,
                     "decision_schema_hash": semantic_digest({"schema": TRAJECTORY_SCHEMA, "feature": FEATURE_REGISTRY_HASH, "tactic": TACTIC_REGISTRY_HASH})},
        "policy_input": d.features.to_dict(),
        "decision": {"selector_kind": selection.selector_kind if selection else None,
                     "selector_checkpoint": selection.selector_checkpoint if selection else None,
                     "selected_tactic_id": selection.selected_tactic_id if selection else None,
                     "candidate_id": selection.candidate_id if selection else None,
                     "behavior_logits": logits,
                     "behavior_log_prob": selection.behavior_log_prob if selection else None,
                     "value": selection.value if selection else None,
                     "phase_before": d.request.control.phase.to_dict(), "phase_after": dict(record.phase_after) if record.phase_after is not None else None,
                     "switch_reason": record.switch_reason, "selection_reason": selection.reason if selection else None,
                     "template_score": record.template_score, "template_probability": record.template_probability,
                     "template_rng_position": record.template_rng_position},
        "evidence_ref": {"path": evidence_path, "sha256": evidence_sha, "record_key": key},
        "execution": {"receipt": receipt.to_dict() if receipt else None,
                      "latency_mode": d.request.execution.latency_mode,
                      "elapsed_ms": d.batch.counters.elapsed_ms},
        "transition": {"next_decision_key": _decision_key(run_id, identity.episode_id, identity.player_id, record.next_decision_id) if record.next_decision_id is not None else None,
                       "elapsed_match_ticks": record.elapsed_match_ticks, "reward_components": rewards,
                       "terminated": record.terminated, "truncated": record.truncated,
                       "episode_result_ref": summary_path,
                       "actor_trainable": actor_trainable, "actor_exclusion_reason": exclusion,
                       "value_trainable": bool(record.value_trainable)},
    }


def write_nextgen_run(*, run_dir: str | Path, run_id: str, episodes: Sequence[EpisodeRecord],
                      config: Mapping[str, Any], provenance: Mapping[str, Any], git_commit: str) -> dict[str, Any]:
    """Write closed episode files, then hash them into the shared artifact manifest."""
    _safe_id(run_id, "run_id")
    _validate_provenance(provenance)
    _require(bool(episodes), "at least one episode required")
    root = Path(run_dir)
    _require(not root.exists() or not any(root.iterdir()), "run directory must be empty")
    root.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Path] = {}
    config_path = root / "config_resolved.yaml"
    _write_json(config_path, dict(config))  # JSON is a YAML 1.2 document.
    artifacts["config_resolved"] = config_path
    snapshots = {
        "feature": {"schema_version": FEATURE_SCHEMA_VERSION, "registry_hash": FEATURE_REGISTRY_HASH,
                    "registry": [entry.to_dict() for entry in FEATURE_REGISTRY]},
        "tactic": {"schema_version": "puyo.nextgen.tactics.v1", "registry_hash": TACTIC_REGISTRY_HASH,
                   "tactic_ids": list(TACTIC_IDS)},
        "template": dict(provenance["template_schema"]),
    }
    for name, snapshot in snapshots.items():
        path = root / f"{name}_schema.json"
        _write_json(path, snapshot)
        artifacts[f"{name}_schema"] = path
    gate_path = root / "gate_report.json"
    _write_json(gate_path, dict(provenance["gate_report"]))
    artifacts["gate_report"] = gate_path
    seen_episodes: set[str] = set()
    all_keys: set[str] = set()
    batch_schemas = {record.diagnostics.batch.schema_version
                     for episode in episodes for record in episode.decisions}
    _require(len(batch_schemas) <= 1, "mixed candidate batch schemas")
    batch_schema = next(iter(batch_schemas), CANDIDATE_BATCH_SCHEMA_VERSION)
    for episode in episodes:
        _safe_id(episode.episode_id, "episode_id")
        _require(episode.episode_id not in seen_episodes, "duplicate episode")
        seen_episodes.add(episode.episode_id)
        folder = root / "episodes" / episode.episode_id
        names = {name: folder / filename for name, filename in {
            "trajectory": "trajectory.jsonl.gz", "events": "events.jsonl.gz", "evidence": "evidence.jsonl.gz",
            "public_replay": "public_replay.jsonl.gz", "summary": "summary.json",
            "private": "private_reproduction.json", "oracle": "oracle.json",
            "public_history": "public_history.json"}.items()}
        evidence: list[dict[str, Any]] = []
        replay: list[dict[str, Any]] = []
        for rec in episode.decisions:
            d = rec.diagnostics
            ident = d.request.identity
            _require(ident.episode_id == episode.episode_id, "episode/diagnostic join mismatch")
            key = _decision_key(run_id, episode.episode_id, ident.player_id, ident.decision_id)
            _require(key not in all_keys, "duplicate decision")
            all_keys.add(key)
            evidence.append({"schema_version": EVIDENCE_SCHEMA, "record_key": key, "batch": d.batch.to_dict()})
            replay.append({"schema_version": PUBLIC_REPLAY_SCHEMA, "record_key": key, "request": d.request.to_dict()})
        _write_jsonl(names["evidence"], evidence)
        _write_jsonl(names["public_replay"], replay)
        evidence_rel = str(names["evidence"].relative_to(root))
        summary_rel = str(names["summary"].relative_to(root))
        evidence_sha = file_sha256(names["evidence"])
        lines = [_line(run_id, rec, evidence_rel, evidence_sha, summary_rel)
                 for rec in episode.decisions]
        _write_jsonl(names["trajectory"], lines)
        _write_jsonl(names["events"], [{"schema_version": EVENT_SCHEMA, "episode_id": episode.episode_id,
                                         "event": dict(event)} for event in episode.events])
        _write_json(names["private"], {"schema_version": PRIVATE_SCHEMA, "episode_id": episode.episode_id,
                                      "metadata": dict(episode.private_reproduction)})
        _write_json(names["oracle"], {"schema_version": ORACLE_SCHEMA, "episode_id": episode.episode_id,
                                     "labels": dict(episode.oracle)})
        _require(episode.public_timing_history is None or
                 isinstance(episode.public_timing_history, PublicTimingHistory), "invalid public timing history")
        _write_json(names["public_history"], {"schema_version": "puyo.nextgen.public_history_sidecar.v1",
                                              "episode_id": episode.episode_id,
                                              "history": episode.public_timing_history.to_dict() if episode.public_timing_history else None})
        result = dict(episode.result)
        _require(set(result) == {"status", "winner", "end_reason"}, "episode result fields mismatch")
        _require(result["status"] in ("complete", "incomplete", "truncated"), "invalid episode status")
        _require(result["winner"] in (None, 0, 1), "invalid winner")
        _require(result["status"] == "complete" or result["winner"] is None,
                 "incomplete/truncated episode cannot imply a loss")
        summary = {"schema_version": EPISODE_SCHEMA, "run_id": run_id, "episode_id": episode.episode_id,
                   "result": result, "actual_metrics": dict(episode.actual_metrics),
                   "decision_count": len(lines), "missing_receipt_count": sum(x["execution"]["receipt"] is None for x in lines),
                   "partial_batch_count": sum(rec.diagnostics.batch.status == "partial" for rec in episode.decisions)}
        _write_json(names["summary"], summary)
        artifacts.update({f"{episode.episode_id}_{role}": path for role, path in names.items()})
    lineage = {"schema_version": RUN_SCHEMA, "trajectory_schema": TRAJECTORY_SCHEMA,
               "request_schema": REQUEST_SCHEMA_VERSION, "batch_schema": batch_schema,
               "diagnostics_schema": DIAGNOSTICS_SCHEMA_VERSION, "selection_schema": SELECTION_SCHEMA_VERSION,
               "feature_registry_hash": FEATURE_REGISTRY_HASH, "tactic_registry_hash": TACTIC_REGISTRY_HASH,
               "episode_ids": sorted(seen_episodes), "provenance": dict(provenance)}
    split = provenance["dataset_split"]
    assignments = [str(value) for name in ("train", "validation", "test") for value in split[name]]
    _require(len(assignments) == len(set(assignments)) and
             set(assignments) == seen_episodes, "dataset split episode overlap/mismatch")
    manifest = write_artifact_manifest(run_dir=root, run_id=run_id, trainer_name="nextgen_trajectory",
                                       config=config, git_commit=git_commit,
                                       seed=provenance["seed_streams"]["environment"], artifacts=artifacts,
                                       checkpoints={}, extra={"nextgen": lineage})
    validate_nextgen_run(root)
    return manifest


def validate_nextgen_run(run_dir: str | Path) -> dict[str, Any]:
    """Fail closed on schema, hashes/bytes, missing sidecars, and broken joins."""
    root = Path(run_dir)
    manifest = _read_json(root / "artifact_manifest.json")
    errors = validate_artifact_manifest(manifest, run_dir=root)
    _require(not errors, "; ".join(errors))
    _keys(manifest["extra"], {"nextgen"}, "manifest.extra")
    nextgen = manifest["extra"]["nextgen"]
    _keys(nextgen, {"schema_version", "trajectory_schema", "request_schema", "batch_schema", "diagnostics_schema",
                    "selection_schema", "feature_registry_hash", "tactic_registry_hash", "episode_ids", "provenance"}, "nextgen")
    _require(nextgen["schema_version"] == RUN_SCHEMA and nextgen["trajectory_schema"] == TRAJECTORY_SCHEMA
             and nextgen["request_schema"] == REQUEST_SCHEMA_VERSION
             and nextgen["batch_schema"] in (LEGACY_CANDIDATE_BATCH_SCHEMA_VERSION, CANDIDATE_BATCH_SCHEMA_VERSION)
             and nextgen["diagnostics_schema"] == DIAGNOSTICS_SCHEMA_VERSION
             and nextgen["selection_schema"] == SELECTION_SCHEMA_VERSION
             and nextgen["feature_registry_hash"] == FEATURE_REGISTRY_HASH
             and nextgen["tactic_registry_hash"] == TACTIC_REGISTRY_HASH, "old/unknown nextgen schema")
    _validate_provenance(nextgen["provenance"])
    _require(manifest["run"]["trainer_name"] == "nextgen_trajectory" and
             manifest["run"]["seed"] == nextgen["provenance"]["seed_streams"]["environment"] and
             manifest["run"]["git_commit"] == nextgen["provenance"]["source"]["git_commit"],
             "run/source provenance mismatch")
    records = manifest["artifacts"]
    _require(len(records) == len({r["role"] for r in records}), "duplicate artifact role")
    for entry in records:
        _require(entry.get("required") is True and entry.get("exists") is True and
                 type(entry.get("size_bytes")) is int and entry["size_bytes"] >= 0 and
                 re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")) is not None,
                 "artifact requires SHA and byte count")
        path = root / entry["path"]
        _require(path.resolve().is_relative_to(root.resolve()) and path.is_file(), "unsafe/missing artifact path")
        _require(path.stat().st_size == entry["size_bytes"], "artifact byte count mismatch")
    roles = {r["role"]: r for r in records}
    for base in ("config_resolved", "feature_schema", "tactic_schema", "template_schema", "gate_report"):
        _require(base in roles, f"missing {base}")
    _require(json_digest(_read_json(root / roles["config_resolved"]["path"])) == manifest["run"]["config_digest"],
             "resolved config digest mismatch")
    feature_snapshot = _read_json(root / roles["feature_schema"]["path"])
    tactic_snapshot = _read_json(root / roles["tactic_schema"]["path"])
    _require(feature_snapshot == {"schema_version": FEATURE_SCHEMA_VERSION, "registry_hash": FEATURE_REGISTRY_HASH,
                                  "registry": [entry.to_dict() for entry in FEATURE_REGISTRY]}
             and tactic_snapshot == {"schema_version": "puyo.nextgen.tactics.v1",
                                     "registry_hash": TACTIC_REGISTRY_HASH, "tactic_ids": list(TACTIC_IDS)},
             "registry snapshot mismatch")
    _require(_read_json(root / roles["template_schema"]["path"]) == nextgen["provenance"]["template_schema"],
             "template schema snapshot mismatch")
    _require(_read_json(root / roles["gate_report"]["path"]) == nextgen["provenance"]["gate_report"],
             "gate report mismatch")
    _require(isinstance(nextgen["episode_ids"], list) and len(nextgen["episode_ids"]) == len(set(nextgen["episode_ids"]))
             and bool(nextgen["episode_ids"]), "duplicate/empty episode IDs")
    split = nextgen["provenance"]["dataset_split"]
    assignments = [str(value) for name in ("train", "validation", "test") for value in split[name]]
    _require(len(assignments) == len(set(assignments)) and set(assignments) == set(nextgen["episode_ids"]),
             "dataset split episode overlap/mismatch")
    expected_roles = {"config_resolved", "feature_schema", "tactic_schema", "template_schema", "gate_report"}
    expected_roles.update(f"{episode_id}_{role}" for episode_id in nextgen["episode_ids"]
                          for role in ("trajectory", "events", "evidence", "public_replay", "summary", "private", "oracle", "public_history"))
    _require(set(roles) == expected_roles, "extra/missing episode artifacts")
    seen: set[str] = set()
    for episode_id in nextgen["episode_ids"]:
        _safe_id(episode_id, "episode_id")
        paths = {}
        for role in ("trajectory", "events", "evidence", "public_replay", "summary", "private", "oracle", "public_history"):
            name = f"{episode_id}_{role}"
            _require(name in roles, f"missing {name}")
            paths[role] = root / roles[name]["path"]
        summary = _read_json(paths["summary"])
        _keys(summary, {"schema_version", "run_id", "episode_id", "result", "actual_metrics", "decision_count",
                        "missing_receipt_count", "partial_batch_count"}, "episode summary")
        _keys(summary["result"], {"status", "winner", "end_reason"}, "episode result")
        _require(set(summary["actual_metrics"]) >= {"chains", "attack", "canceled", "received", "survival_ticks"},
                 "episode actual metrics missing")
        _require(all(type(summary["actual_metrics"][name]) in (int, float)
                     and math.isfinite(summary["actual_metrics"][name])
                     and summary["actual_metrics"][name] >= 0
                     for name in ("chains", "attack", "canceled", "received", "survival_ticks")),
                 "invalid episode actual metrics")
        _require(summary["schema_version"] == EPISODE_SCHEMA and summary["episode_id"] == episode_id
                 and summary["run_id"] == manifest["run"]["run_id"], "episode summary mismatch")
        _require(summary["result"]["status"] in ("complete", "incomplete", "truncated")
                 and summary["result"]["winner"] in (None, 0, 1)
                 and type(summary["result"]["end_reason"]) is str
                 and bool(summary["result"]["end_reason"])
                 and (summary["result"]["status"] == "complete" or summary["result"]["winner"] is None),
                 "invalid episode result")
        private, oracle = _read_json(paths["private"]), _read_json(paths["oracle"])
        public_history = _read_json(paths["public_history"])
        _keys(public_history, {"schema_version", "episode_id", "history"}, "public history sidecar")
        _require(public_history["schema_version"] == "puyo.nextgen.public_history_sidecar.v1"
                 and public_history["episode_id"] == episode_id, "public history sidecar mismatch")
        if public_history["history"] is not None:
            PublicTimingHistory.from_dict(public_history["history"])
        _keys(private, {"schema_version", "episode_id", "metadata"}, "private sidecar")
        _keys(oracle, {"schema_version", "episode_id", "labels"}, "oracle sidecar")
        _require(private["schema_version"] == PRIVATE_SCHEMA and oracle["schema_version"] == ORACLE_SCHEMA
                 and private["episode_id"] == episode_id and oracle["episode_id"] == episode_id,
                 "private/oracle schema mismatch")
        events = _read_jsonl(paths["events"])
        _require(all(set(x) == {"schema_version", "episode_id", "event"} and
                     x["schema_version"] == EVENT_SCHEMA and x["episode_id"] == episode_id for x in events),
                 "event schema/episode mismatch")
        evidence = _read_jsonl(paths["evidence"])
        replay = _read_jsonl(paths["public_replay"])
        evidence_by_key = {x["record_key"]: x for x in evidence}
        replay_by_key = {x["record_key"]: x for x in replay}
        _require(all(set(x) == {"schema_version", "record_key", "batch"} for x in evidence)
                 and all(set(x) == {"schema_version", "record_key", "request"} for x in replay),
                 "sidecar fields mismatch")
        _require(len(evidence_by_key) == len(evidence) and len(replay_by_key) == len(replay), "duplicate sidecar decision")
        lines = _read_jsonl(paths["trajectory"])
        _require(summary["decision_count"] == len(lines), "decision count mismatch")
        _require(summary["missing_receipt_count"] == sum(x["execution"]["receipt"] is None for x in lines),
                 "missing receipt count mismatch")
        _require(summary["partial_batch_count"] == sum(e["batch"]["status"] == "partial" for e in evidence),
                 "partial batch count mismatch")
        episode_keys = set()
        by_player: dict[int, list[dict[str, Any]]] = {0: [], 1: []}
        for line in lines:
            _keys(line, {"schema_version", "identity", "policy_input", "decision", "evidence_ref", "execution", "transition"}, "trajectory")
            _require(line["schema_version"] == TRAJECTORY_SCHEMA, "old/unknown trajectory schema")
            identity = line["identity"]
            _keys(identity, {"run_id", "episode_id", "player_id", "decision_id", "request_id", "snapshot_digest",
                             "batch_digest", "decision_schema_hash"}, "decision identity")
            key = _decision_key(identity["run_id"], identity["episode_id"], identity["player_id"], identity["decision_id"])
            _require(identity["run_id"] == manifest["run"]["run_id"] and identity["episode_id"] == episode_id
                     and key not in seen, "duplicate/mismatched decision")
            seen.add(key)
            episode_keys.add(key)
            by_player[identity["player_id"]].append(line)
            ref = line["evidence_ref"]
            _keys(ref, {"path", "sha256", "record_key"}, "evidence_ref")
            _require(ref == {"path": str(paths["evidence"].relative_to(root)),
                             "sha256": file_sha256(paths["evidence"]), "record_key": key}, "bad evidence reference")
            _require(line["transition"]["episode_result_ref"] == str(paths["summary"].relative_to(root)),
                     "bad summary reference")
            _require(key in evidence_by_key and key in replay_by_key, "missing decision sidecar")
            batch_payload, request_payload = evidence_by_key[key], replay_by_key[key]
            _require(batch_payload["schema_version"] == EVIDENCE_SCHEMA and
                     request_payload["schema_version"] == PUBLIC_REPLAY_SCHEMA, "sidecar schema mismatch")
            batch = CandidateBatch.from_dict(batch_payload["batch"])
            _require(batch.schema_version == nextgen["batch_schema"], "manifest/batch schema mismatch")
            request = NextgenRequest.from_dict(request_payload["request"])
            _keys(line["policy_input"], POLICY_INPUT_KEYS, "policy_input")
            features = PolicyFeatures.from_dict(line["policy_input"])
            decision, execution, transition = line["decision"], line["execution"], line["transition"]
            _keys(decision, {"selector_kind", "selector_checkpoint", "selected_tactic_id", "candidate_id",
                             "behavior_logits", "behavior_log_prob", "value", "phase_before", "phase_after",
                             "switch_reason", "selection_reason", "template_score", "template_probability",
                             "template_rng_position"}, "decision")
            _keys(execution, {"receipt", "latency_mode", "elapsed_ms"}, "execution")
            _keys(transition, {"next_decision_key", "elapsed_match_ticks", "reward_components", "terminated",
                               "truncated", "episode_result_ref", "actor_trainable", "actor_exclusion_reason",
                               "value_trainable"}, "transition")
            _require(type(transition["elapsed_match_ticks"]) is int and transition["elapsed_match_ticks"] >= 0
                     and type(transition["terminated"]) is bool and type(transition["truncated"]) is bool
                     and not (transition["terminated"] and transition["truncated"])
                     and type(transition["value_trainable"]) is bool,
                     "invalid transition timing/flags")
            _require(isinstance(transition["reward_components"], dict) and
                     all(isinstance(k, str) and k and type(v) in (int, float) and math.isfinite(v)
                         for k, v in transition["reward_components"].items()), "invalid reward")
            _require(decision["phase_before"] == request.control.phase.to_dict(), "phase before mismatch")
            _require(decision["phase_after"] is None or isinstance(decision["phase_after"], dict),
                     "invalid phase after")
            _require(decision["switch_reason"] is None or isinstance(decision["switch_reason"], str),
                     "invalid switch reason")
            if decision["selected_tactic_id"] is None:
                _require(all(decision[name] is None for name in
                             ("selector_kind", "selector_checkpoint", "candidate_id", "behavior_logits",
                              "behavior_log_prob", "value", "selection_reason")),
                         "selection null fields mismatch")
            if decision["behavior_logits"] is not None:
                _require(decision["selector_kind"] == "rl" and len(decision["behavior_logits"]) == 6,
                         "invalid behavior logits")
                for value in decision["behavior_logits"]:
                    _finite(value, "behavior logit")
            if decision["template_score"] is not None:
                _finite(decision["template_score"], "template score")
            if decision["template_probability"] is not None:
                _require(0 <= _finite(decision["template_probability"], "template probability") <= 1,
                         "invalid template probability")
            if decision["template_rng_position"] is not None:
                _require(type(decision["template_rng_position"]) is int and decision["template_rng_position"] >= 0,
                         "invalid template RNG position")
            selection = None
            if decision["selected_tactic_id"] is not None:
                selection = Selection(selected_tactic_id=decision["selected_tactic_id"], candidate_id=decision["candidate_id"],
                                      batch_digest=identity["batch_digest"], selector_kind=decision["selector_kind"],
                                      selector_checkpoint=decision["selector_checkpoint"],
                                      behavior_log_prob=decision["behavior_log_prob"], value=decision["value"],
                                      reason=decision["selection_reason"])
            receipt = ExecutionReceipt.from_dict(execution["receipt"]) if execution["receipt"] is not None else None
            Diagnostics(request=request, batch=batch, features=features, selection=selection, receipt=receipt)
            _require(identity["request_id"] == request.identity.request_id
                     and identity["snapshot_digest"] == request.identity.snapshot_digest
                     and identity["decision_id"] == request.identity.decision_id
                     and identity["player_id"] == request.identity.player_id
                     and identity["batch_digest"] == batch.digest, "identity join mismatch")
            profile = nextgen["provenance"]["search_profile"]
            _require(request.control.search_profile.to_dict() == profile, "search profile provenance mismatch")
            timing = nextgen["provenance"]["timing"]
            _require(request.execution.timing_schema == timing["schema_version"] and
                     request.execution.timing_digest == timing["digest"] and
                     request.execution.latency_mode == timing["latency_mode"], "timing provenance mismatch")
            if selection and selection.selector_kind == "rl":
                _require(selection.selector_checkpoint == nextgen["provenance"]["checkpoint"].get("sha256"),
                         "checkpoint provenance mismatch")
            _require(identity["decision_schema_hash"] == semantic_digest({"schema": TRAJECTORY_SCHEMA,
                      "feature": FEATURE_REGISTRY_HASH, "tactic": TACTIC_REGISTRY_HASH}), "decision schema hash mismatch")
            expected_actor = receipt is not None and receipt.actor_trainable
            _require(transition["actor_trainable"] is expected_actor and
                     transition["actor_exclusion_reason"] == (None if expected_actor else receipt.outcome if receipt else "missing_receipt"),
                     "actor eligibility mismatch")
            _require(execution["latency_mode"] == request.execution.latency_mode and
                     execution["elapsed_ms"] == batch.counters.elapsed_ms, "execution timing join mismatch")
        _require(set(evidence_by_key) == episode_keys and set(replay_by_key) == episode_keys,
                 "unreferenced sidecar record")
        for player_rows in by_player.values():
            player_rows.sort(key=lambda x: x["identity"]["decision_id"])
            for index, line in enumerate(player_rows):
                next_key = line["transition"]["next_decision_key"]
                if next_key is not None:
                    _require(next_key in episode_keys, "missing next decision")
                expected_next = (_decision_key(manifest["run"]["run_id"], episode_id,
                                               line["identity"]["player_id"],
                                               player_rows[index + 1]["identity"]["decision_id"])
                                 if index + 1 < len(player_rows) else None)
                _require(next_key == expected_next, "invalid next decision order/player")
                _require(not (line["transition"]["terminated"] or line["transition"]["truncated"])
                         or index == len(player_rows) - 1, "terminal transition has successor")
            if player_rows:
                last = player_rows[-1]["transition"]
                status = summary["result"]["status"]
                _require(last["terminated"] is (status == "complete") and
                         last["truncated"] is (status == "truncated"),
                         "episode result/terminal transition mismatch")
    return manifest


def iter_actor_samples(run_dir: str | Path) -> Iterator[tuple[dict[str, Any], str]]:
    """Yield only the recorded policy input and activated tactic target.

    Validation completes before yielding any row; raw replay, seeds and oracle data
    are never returned by this loader.
    """
    manifest = validate_nextgen_run(run_dir)
    root = Path(run_dir)
    roles = {r["role"]: r for r in manifest["artifacts"]}
    for episode_id in manifest["extra"]["nextgen"]["episode_ids"]:
        for line in _read_jsonl(root / roles[f"{episode_id}_trajectory"]["path"]):
            if line["transition"]["actor_trainable"]:
                yield dict(line["policy_input"]), line["decision"]["selected_tactic_id"]
