"""Deterministic challenger evaluation, promotion, and rollback gate."""

from __future__ import annotations

import argparse
import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import yaml

from eval.realtime_arena import RealtimeArenaResult, run_realtime_paired_series
from puyo_env.realtime_ai import RealtimeDecisionConfig
from selfplay.policies import make_policy
from train.artifacts import file_sha256, json_digest, utc_timestamp
from human_data.audit import append_audit_event


REGISTRY_SCHEMA_VERSION = "puyo.model_role_registry.v1"
EVALUATION_SCHEMA_VERSION = "puyo.promotion_evaluation.v1"


@dataclass(frozen=True)
class PromotionCriteria:
    minimum_win_rate: float = 0.50
    maximum_tactical_score_drop: float = 0.05
    maximum_chain_drop: float = 0.50
    maximum_operation_failure_rate: float = 0.05
    maximum_deadline_miss_rate: float = 0.01
    maximum_mean_policy_elapsed_ms: float = 80.0


@dataclass(frozen=True)
class GateConfig:
    arena_seeds: tuple[int, ...] = (58, 59, 60, 61)
    tactical_seeds: tuple[int, ...] = (145, 245, 345, 445)
    max_ticks: int = 600
    device: str = "cpu"
    criteria: PromotionCriteria = field(default_factory=PromotionCriteria)
    opponent_pool_limit: int = 8


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _checkpoint_record(path: str | Path) -> dict[str, Any]:
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"checkpoint does not exist: {target}")
    return {"path": str(target), "sha256": file_sha256(target)}


def _validate_checkpoint_record(record: Mapping[str, Any], *, role: str) -> None:
    path = Path(str(record.get("path", "")))
    expected = str(record.get("sha256", ""))
    if not path.is_file() or not expected or file_sha256(path) != expected:
        raise RuntimeError(f"{role} checkpoint integrity check failed: {path}")


def load_config(path: str | Path) -> GateConfig:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(value, Mapping):
        raise ValueError("promotion gate config must be a mapping")
    criteria = PromotionCriteria(**dict(value.get("criteria", {})))
    config = GateConfig(
        arena_seeds=tuple(int(seed) for seed in value.get("arena_seeds", GateConfig.arena_seeds)),
        tactical_seeds=tuple(int(seed) for seed in value.get("tactical_seeds", GateConfig.tactical_seeds)),
        max_ticks=int(value.get("max_ticks", GateConfig.max_ticks)),
        device=str(value.get("device", GateConfig.device)),
        criteria=criteria,
        opponent_pool_limit=int(value.get("opponent_pool_limit", GateConfig.opponent_pool_limit)),
    )
    if not config.arena_seeds or not config.tactical_seeds:
        raise ValueError("arena_seeds and tactical_seeds must not be empty")
    if config.max_ticks <= 0 or config.opponent_pool_limit < 1:
        raise ValueError("max_ticks and opponent_pool_limit must be positive")
    return config


def initialize_registry(
    registry_path: str | Path,
    *,
    champion_path: str | Path,
    challenger_path: str | Path | None = None,
) -> dict[str, Any]:
    target = Path(registry_path)
    if target.exists():
        raise FileExistsError(f"registry already exists: {target}")
    now = utc_timestamp()
    registry = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "revision": 1,
        "created_at_utc": now,
        "updated_at_utc": now,
        "roles": {
            "champion": _checkpoint_record(champion_path),
            "challenger": None if challenger_path is None else _checkpoint_record(challenger_path),
            "previous_stable": None,
        },
        "evaluations": [],
        "transitions": [],
        "opponent_pool": [],
        "opponent_pool_limit": 8,
    }
    _atomic_write_json(target, registry)
    return registry


def load_registry(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"unsupported model role registry: {target}")
    roles = value.get("roles")
    if not isinstance(roles, dict) or not isinstance(roles.get("champion"), dict):
        raise ValueError(f"registry has no champion: {target}")
    return value


@contextmanager
def _registry_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _role_metric(result: RealtimeArenaResult, stem: str, *, challenger: bool) -> float:
    if not result.matches:
        return 0.0
    values = []
    for match in result.matches:
        challenger_suffix = "player_0" if match.policy_a_side == "player_0" else "player_1"
        suffix = challenger_suffix if challenger else ("player_1" if challenger_suffix == "player_0" else "player_0")
        values.append(float(getattr(match, f"{stem}_{suffix}")))
    return sum(values) / len(values)


def _operation_rates(result: RealtimeArenaResult) -> tuple[float, float]:
    decisions = _role_metric(result, "decisions", challenger=True)
    failures = sum(
        _role_metric(result, stem, challenger=True)
        for stem in ("timeouts", "unreachable_plans")
    )
    deadlines = _role_metric(result, "deadline_misses", challenger=True)
    return failures / max(decisions, 1.0), deadlines / max(decisions, 1.0)


def _match_records(result: RealtimeArenaResult) -> list[dict[str, Any]]:
    records = []
    for match in result.matches:
        challenger_suffix = "player_0" if match.policy_a_side == "player_0" else "player_1"
        champion_suffix = "player_1" if challenger_suffix == "player_0" else "player_0"
        records.append(
            {
                "seed": match.seed,
                "challenger_side": match.policy_a_side,
                "winner": match.winner,
                "challenger_score": match.score_for_policy_a,
                "challenger_max_chain": getattr(match, f"max_chain_{challenger_suffix}"),
                "champion_max_chain": getattr(match, f"max_chain_{champion_suffix}"),
                "challenger_decisions": getattr(match, f"decisions_{challenger_suffix}"),
                "challenger_timeouts": getattr(match, f"timeouts_{challenger_suffix}"),
                "challenger_deadline_misses": getattr(
                    match, f"deadline_misses_{challenger_suffix}"
                ),
                "challenger_unreachable_plans": getattr(
                    match, f"unreachable_plans_{challenger_suffix}"
                ),
                "challenger_mean_policy_elapsed_ms": getattr(
                    match, f"mean_policy_elapsed_ms_{challenger_suffix}"
                ),
                "final_hash": match.final_hash,
            }
        )
    return records


def _run_seed_suite(
    challenger_path: str,
    champion_path: str,
    *,
    seeds: Sequence[int],
    config: GateConfig,
) -> RealtimeArenaResult:
    matches = []
    for seed in seeds:
        challenger = make_policy(
            "checkpoint", checkpoint_path=challenger_path, device=config.device, deterministic=True
        )
        champion = make_policy(
            "checkpoint", checkpoint_path=champion_path, device=config.device, deterministic=True
        )
        result = run_realtime_paired_series(
            challenger,
            champion,
            games=1,
            seed=int(seed),
            max_ticks=config.max_ticks,
            decision_config=RealtimeDecisionConfig(),
        )
        matches.extend(result.matches)
    return RealtimeArenaResult(matches=tuple(matches))


def collect_metrics(
    challenger_path: str,
    champion_path: str,
    config: GateConfig,
) -> dict[str, Any]:
    arena = _run_seed_suite(challenger_path, champion_path, seeds=config.arena_seeds, config=config)
    tactical = _run_seed_suite(challenger_path, champion_path, seeds=config.tactical_seeds, config=config)
    operation_failure_rate, deadline_miss_rate = _operation_rates(arena)
    return {
        "arena": {
            "seeds": list(config.arena_seeds),
            "matches": len(arena.matches),
            "challenger_score_rate": sum(match.score_for_policy_a for match in arena.matches) / len(arena.matches),
            "results": _match_records(arena),
        },
        "tactical_scenarios": {
            "seeds": list(config.tactical_seeds),
            "matches": len(tactical.matches),
            "challenger_score_rate": (
                sum(match.score_for_policy_a for match in tactical.matches)
                / len(tactical.matches)
            ),
            "champion_score_rate": (
                sum(1.0 - match.score_for_policy_a for match in tactical.matches)
                / len(tactical.matches)
            ),
            "results": _match_records(tactical),
        },
        "chain_benchmark": {
            "challenger_mean_max_chain": _role_metric(arena, "max_chain", challenger=True),
            "champion_mean_max_chain": _role_metric(arena, "max_chain", challenger=False),
        },
        "operation_guard": {
            "failure_rate": operation_failure_rate,
            "deadline_miss_rate": deadline_miss_rate,
        },
        "latency_guard": {
            "mean_policy_elapsed_ms": _role_metric(arena, "mean_policy_elapsed_ms", challenger=True),
        },
    }


def evaluate_criteria(metrics: Mapping[str, Any], criteria: PromotionCriteria) -> dict[str, Any]:
    arena = metrics["arena"]
    tactical = metrics["tactical_scenarios"]
    chain = metrics["chain_benchmark"]
    operations = metrics["operation_guard"]
    latency = metrics["latency_guard"]
    checks = {
        "minimum_win_rate": float(arena["challenger_score_rate"]) >= criteria.minimum_win_rate,
        "tactical_non_degradation": (
            float(tactical["challenger_score_rate"]) + criteria.maximum_tactical_score_drop
            >= float(tactical["champion_score_rate"])
        ),
        "chain_non_degradation": (
            float(chain["challenger_mean_max_chain"]) + criteria.maximum_chain_drop
            >= float(chain["champion_mean_max_chain"])
        ),
        "operation_failure_rate": (
            float(operations["failure_rate"]) <= criteria.maximum_operation_failure_rate
        ),
        "deadline_miss_rate": (
            float(operations["deadline_miss_rate"]) <= criteria.maximum_deadline_miss_rate
        ),
        "latency_guard": (
            float(latency["mean_policy_elapsed_ms"]) <= criteria.maximum_mean_policy_elapsed_ms
        ),
    }
    return {
        "decision": "promote" if all(checks.values()) else "reject",
        "passed": all(checks.values()),
        "checks": checks,
        "failed_checks": sorted(name for name, passed in checks.items() if not passed),
    }


def _evaluation_id(
    champion: Mapping[str, Any], challenger: Mapping[str, Any], config: GateConfig
) -> str:
    return json_digest(
        {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "champion_sha256": champion["sha256"],
            "challenger_sha256": challenger["sha256"],
            "config": asdict(config),
        }
    )[:20]


def build_evaluation(
    champion: Mapping[str, Any],
    challenger: Mapping[str, Any],
    config: GateConfig,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    verdict = evaluate_criteria(metrics, config.criteria)
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "evaluation_id": _evaluation_id(champion, challenger, config),
        "created_at_utc": utc_timestamp(),
        "champion": dict(champion),
        "challenger": dict(challenger),
        "config": asdict(config),
        "config_digest": json_digest(asdict(config)),
        "metrics": dict(metrics),
        "verdict": verdict,
    }


def _append_opponent(registry: dict[str, Any], checkpoint: Mapping[str, Any], *, limit: int) -> None:
    pool = [item for item in registry.get("opponent_pool", []) if item.get("sha256") != checkpoint["sha256"]]
    pool.append({**dict(checkpoint), "added_at_utc": utc_timestamp(), "source": "previous_stable"})
    registry["opponent_pool"] = pool[-limit:]


def apply_evaluation(
    registry_path: str | Path,
    evaluation: Mapping[str, Any],
    *,
    artifact_path: str | Path,
    opponent_pool_limit: int,
) -> dict[str, Any]:
    target = Path(registry_path)
    artifact = Path(artifact_path)
    with _registry_lock(target):
        registry = load_registry(target)
        evaluation_id = str(evaluation["evaluation_id"])
        existing = next(
            (item for item in registry.get("evaluations", []) if item.get("evaluation_id") == evaluation_id),
            None,
        )
        if existing is not None:
            return registry
        champion = registry["roles"]["champion"]
        expected = evaluation["champion"]
        if champion.get("sha256") != expected.get("sha256"):
            raise RuntimeError("champion changed while evaluation was running; retry the gate")
        challenger = evaluation["challenger"]
        _validate_checkpoint_record(champion, role="champion")
        _validate_checkpoint_record(challenger, role="challenger")
        _atomic_write_json(artifact, evaluation)
        registry["roles"]["challenger"] = dict(challenger)
        decision = evaluation["verdict"]["decision"]
        if decision == "promote":
            registry["roles"]["previous_stable"] = dict(champion)
            registry["roles"]["champion"] = dict(challenger)
            registry["roles"]["challenger"] = None
            _append_opponent(registry, champion, limit=opponent_pool_limit)
        registry.setdefault("evaluations", []).append(
            {
                "evaluation_id": evaluation_id,
                "decision": decision,
                "artifact_path": str(artifact.resolve()),
                "challenger_sha256": challenger["sha256"],
                "champion_sha256": champion["sha256"],
                "config_digest": evaluation["config_digest"],
                "created_at_utc": evaluation["created_at_utc"],
            }
        )
        registry.setdefault("transitions", []).append(
            {
                "kind": "promotion" if decision == "promote" else "rejection",
                "evaluation_id": evaluation_id,
                "created_at_utc": utc_timestamp(),
                "from_sha256": champion["sha256"],
                "to_sha256": challenger["sha256"] if decision == "promote" else champion["sha256"],
            }
        )
        registry["opponent_pool_limit"] = opponent_pool_limit
        registry["revision"] = int(registry.get("revision", 0)) + 1
        registry["updated_at_utc"] = utc_timestamp()
        append_audit_event(
            target.parent / "audit_events.jsonl",
            event=f"gate.{'promotion' if decision == 'promote' else 'rejection'}.authorized",
            resource_type="evaluation",
            resource_id=evaluation_id,
            status="authorized",
            details={
                "champion_sha256": champion["sha256"],
                "challenger_sha256": challenger["sha256"],
                "registry_revision": registry["revision"],
            },
        )
        _atomic_write_json(target, registry)
        return registry


def evaluate_and_apply(
    registry_path: str | Path,
    challenger_path: str | Path,
    *,
    config: GateConfig,
    output_dir: str | Path,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    champion = registry["roles"]["champion"]
    challenger = _checkpoint_record(challenger_path)
    audit_path = Path(registry_path).parent / "audit_events.jsonl"
    config_digest = json_digest(asdict(config))
    for record in reversed(registry.get("evaluations", [])):
        same_inputs = (
            record.get("challenger_sha256") == challenger["sha256"]
            and record.get("config_digest") == config_digest
        )
        same_gate = record.get("champion_sha256") == champion["sha256"]
        already_promoted = (
            record.get("decision") == "promote"
            and champion["sha256"] == challenger["sha256"]
        )
        artifact_path = Path(str(record.get("artifact_path", "")))
        if same_inputs and (same_gate or already_promoted) and artifact_path.is_file():
            return json.loads(artifact_path.read_text(encoding="utf-8"))
    evaluation_id = _evaluation_id(champion, challenger, config)
    artifact_path = Path(output_dir) / evaluation_id / "evaluation.json"
    if artifact_path.is_file():
        evaluation = json.loads(artifact_path.read_text(encoding="utf-8"))
    else:
        append_audit_event(
            audit_path,
            event="gate.evaluation.started",
            resource_type="evaluation",
            resource_id=evaluation_id,
            status="started",
            details={"champion_sha256": champion["sha256"], "challenger_sha256": challenger["sha256"]},
        )
        try:
            metrics = collect_metrics(challenger["path"], champion["path"], config)
            evaluation = build_evaluation(champion, challenger, config, metrics)
        except Exception as exc:
            append_audit_event(
                audit_path,
                event="gate.evaluation.failed",
                resource_type="evaluation",
                resource_id=evaluation_id,
                status="failed",
                details={"error_type": type(exc).__name__},
            )
            raise
    apply_evaluation(
        registry_path,
        evaluation,
        artifact_path=artifact_path,
        opponent_pool_limit=config.opponent_pool_limit,
    )
    return evaluation


def rollback(registry_path: str | Path, *, reason: str) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("rollback reason is required")
    target = Path(registry_path)
    with _registry_lock(target):
        registry = load_registry(target)
        champion = registry["roles"]["champion"]
        previous = registry["roles"].get("previous_stable")
        if not isinstance(previous, dict):
            raise ValueError("no previous stable model is available for rollback")
        _validate_checkpoint_record(champion, role="champion")
        _validate_checkpoint_record(previous, role="previous_stable")
        registry["roles"]["champion"] = previous
        registry["roles"]["previous_stable"] = champion
        registry["roles"]["challenger"] = None
        _append_opponent(
            registry,
            champion,
            limit=max(1, int(registry.get("opponent_pool_limit", 8))),
        )
        registry.setdefault("transitions", []).append(
            {
                "kind": "rollback",
                "reason": reason.strip(),
                "created_at_utc": utc_timestamp(),
                "from_sha256": champion["sha256"],
                "to_sha256": previous["sha256"],
            }
        )
        registry["revision"] = int(registry.get("revision", 0)) + 1
        registry["updated_at_utc"] = utc_timestamp()
        append_audit_event(
            target.parent / "audit_events.jsonl",
            event="gate.rollback.authorized",
            resource_type="model_registry",
            resource_id=str(target.resolve()),
            status="authorized",
            details={
                "reason": reason.strip(),
                "from_sha256": champion["sha256"],
                "to_sha256": previous["sha256"],
                "registry_revision": registry["revision"],
            },
        )
        _atomic_write_json(target, registry)
        return registry


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate, promote, reject, or roll back realtime models.")
    parser.add_argument("--registry", default="runs/model_registry.json")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--champion", required=True)
    init.add_argument("--challenger")
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--challenger", required=True)
    evaluate.add_argument("--config", default="train/config/promotion_gate.yaml")
    evaluate.add_argument("--output-dir", default="runs/promotion_gate")
    roll = subparsers.add_parser("rollback")
    roll.add_argument("--reason", required=True)
    subparsers.add_parser("status")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "init":
        result = initialize_registry(args.registry, champion_path=args.champion, challenger_path=args.challenger)
    elif args.command == "evaluate":
        result = evaluate_and_apply(
            args.registry,
            args.challenger,
            config=load_config(args.config),
            output_dir=args.output_dir,
        )
    elif args.command == "rollback":
        result = rollback(args.registry, reason=args.reason)
    else:
        result = load_registry(args.registry)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
