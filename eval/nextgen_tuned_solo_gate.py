"""PUYO-280 held-out 60-placement solo gate for nextgen only.

`init` freezes source/build/provider/config before any worker sees a pattern.
Each worker runs in its own process and records public decisions, actual tick
events, quality proofs, and a full deterministic replay audit.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from agents.chain_structure import load_chain_structure_config
from eval.nextgen_fire_goal_comparison import new_match
from eval.nextgen_gates import digest, distribution
from eval.nextgen_safe_build_gate import read, write_new
from eval.nextgen_single_quality_gate import (
    PATTERN_IDS as TUNING_PATTERNS,
    build_identity, complete_evidence, current_source, require_clean,
)
from eval.nextgen_single_quality_runtime import (
    POLICY_SEED, make_policy, measure, replay_audit, semantic_payload,
)
from src.core.tsumo import EsportsTsuSource

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "puyo.nextgen.tuned_solo_gate.v1"
PATTERN_IDS = tuple((i * 65536 + 32768) // 30 + 73 for i in range(30))
assert (len(set(PATTERN_IDS)) == 30 and
        not set(PATTERN_IDS) & (set(TUNING_PATTERNS) | {1092}))
REPEATS = (1, 2)
GOALS = {"chain10": None, "score95000": 95000}
EVALUATORS = {
    "chain10": ROOT / "train/config/nextgen_chain_structure.yaml",
    "score95000": ROOT / "train/config/nextgen_score_structure.yaml",
}
PLACEMENTS = 60


def artifact_name(goal, pattern_id, repeat):
    return f"{goal}-pattern-{pattern_id}-repeat-{repeat}.json.gz"


def policy_for(goal):
    if goal not in GOALS:
        raise ValueError("unknown goal")
    evaluator = load_chain_structure_config(EVALUATORS[goal])
    return make_policy("nextgen_tactic_manager", fire_goal_score=GOALS[goal],
                       evaluator_config=evaluator)


def declaration(source_path):
    source = EsportsTsuSource(source_path)
    policies = {goal: policy_for(goal) for goal in GOALS}
    return {
        "pattern_ids": list(PATTERN_IDS), "repeats": list(REPEATS),
        "placements": PLACEMENTS, "stop_first_actual_chain": 10,
        "goals": GOALS, "policy_seed": POLICY_SEED, "environment_seed": 55,
        "provider": {"mode": "esports_tsu", "source_path": str(Path(source_path).resolve()),
                     "sha256": source.checksum, "version": source.version,
                     "color_mapping": {str(i): {k: v.name for k, v in source.color_mapping(i).items()}
                                       for i in PATTERN_IDS}},
        "policies": {goal: {"profile": policy.profile.to_dict(),
                            "search": asdict(policy.search_config),
                            "selector": asdict(policy.selector.config),
                            "evaluator": policy.evaluator_config.to_dict(),
                            "catalog_sha256": policy.catalog.semantic_digest}
                     for goal, policy in policies.items()},
        "backend": "native", "max_ticks": 30_000,
        "environment": "solo frozen opponent, attacks suppressed, reachable realtime controls",
        "information_boundary": "public current/NEXT/NEXT2; pattern withheld from policy",
        "acceptance": {"mean_maximum_actual_chain": 10, "unjustified_premature": 0,
                       "avoidable_suffocation": 0, "unknown_quality": 0,
                       "repeat_mismatch": 0},
    }


def initialize(output, source_path):
    require_clean()
    config = declaration(source_path)
    manifest = {"schema": SCHEMA, "config": config, "config_sha256": digest(config),
                "source": current_source(), "build": build_identity(),
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    manifest["sha256"] = digest(manifest)
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "manifest.json", manifest)
    return manifest


def manifest_at(output, *, execution=False):
    manifest = read(output / "manifest.json")
    config = manifest["config"]
    if (manifest["schema"] != SCHEMA or
            digest({k: v for k, v in manifest.items() if k != "sha256"}) != manifest["sha256"] or
            digest(config) != manifest["config_sha256"] or
            config["pattern_ids"] != list(PATTERN_IDS) or
            config["repeats"] != list(REPEATS) or config["goals"] != GOALS or
            config["placements"] != PLACEMENTS):
        raise ValueError("formal declaration mismatch")
    if execution:
        require_clean()
        if (current_source() != manifest["source"] or
                build_identity() != manifest["build"] or
                digest(declaration(config["provider"]["source_path"])) != digest(config)):
            raise ValueError("source, native build, provider, or settings changed")
    return manifest


def validate_raw(raw, manifest):
    config = manifest["config"]
    goal, pattern_id, repeat = (raw[k] for k in ("goal", "pattern_id", "repeat"))
    if (goal not in GOALS or pattern_id not in PATTERN_IDS or repeat not in REPEATS or
            raw["schema"] != SCHEMA or raw["manifest_sha256"] != manifest["sha256"] or
            raw["policy"] != "nextgen_tactic_manager" or
            raw["semantic_digest"] != digest(semantic_payload(raw))):
        raise ValueError("artifact identity or semantic digest mismatch")
    tsumo = raw["match_rules"]["tsumo"]
    mapping = config["provider"]["color_mapping"][str(pattern_id)]
    if (tsumo.get("mode") != "esports_tsu" or tsumo.get("pattern_id") != pattern_id or
            tsumo.get("source_sha256") != config["provider"]["sha256"] or
            tsumo.get("source_version") != config["provider"]["version"] or
            tsumo.get("color_mapping") != {"player_0": mapping, "player_1": mapping}):
        raise ValueError("provider identity mismatch")
    chains = [event["chain_count"] for event in raw["events"]
              if event["type"] == "resolution_complete"]
    if (chains != raw["chains"] or len(chains) > PLACEMENTS or
            len(chains) != raw["final"]["placements"]):
        raise ValueError("actual resolution count mismatch")
    first = next((i for i, chain in enumerate(chains) if chain >= 10), None)
    expected = ("game_over" if raw["game_over"] else "first_target_chain" if first is not None
                else "placements" if len(chains) == PLACEMENTS else "tick_limit")
    if (raw["termination"] != expected or
            first is not None and first != len(chains) - 1):
        raise ValueError("termination boundary mismatch")
    if (len(raw["small_clear_classifications"]) != sum(0 < x < 10 for x in chains) or
            raw["suffocation_classification"]["status"] not in ("none", "unknown", "avoidable")):
        raise ValueError("quality evidence incomplete")
    if raw["audit"]["lock_mismatches"] or raw["audit"]["unlocked_requests"]:
        raise ValueError("lock or replay mismatch")
    if (raw["controller"]["fallback_actions"] or raw["controller"]["stale_decisions"] or
            raw["errors"] or raw["integrity_issues"]):
        raise ValueError("controller, scheduler, or integrity failure")
    return raw


def worker(output, goal, pattern_id, repeat):
    manifest = manifest_at(output, execution=True)
    if goal not in GOALS or pattern_id not in PATTERN_IDS or repeat not in REPEATS:
        raise ValueError("unexpected cohort identity")
    path = output / artifact_name(goal, pattern_id, repeat)
    prefix = path.name.removesuffix(".json.gz")
    progress = output / (prefix + ".progress.json.gz")
    failure = output / (prefix + ".failure.json")
    if any(item.exists() for item in (path, progress, failure)):
        raise ValueError("identity already recorded; refusing overwrite")

    def save_progress(value):
        staging = progress.with_suffix(".tmp")
        staging.write_bytes(gzip.compress(json.dumps(value, allow_nan=False).encode(), mtime=0))
        staging.replace(progress)

    try:
        raw = measure(new_match(manifest["config"], pattern_id), policy_for(goal),
                      "nextgen_tactic_manager", placements=PLACEMENTS,
                      stop_first_chain=10, progress=save_progress)
        raw.update(schema=SCHEMA, goal=goal, pattern_id=pattern_id, repeat=repeat,
                   manifest_sha256=manifest["sha256"])
        complete_evidence(raw, new_match(manifest["config"], pattern_id))
        # The historical 46-placement gate flags every different window as
        # incomplete. The 60-placement boundary is checked above instead.
        raw["integrity_issues"] = [x for x in raw["integrity_issues"]
                                   if x != "incomplete_window"]
        validate_raw(raw, manifest)
        manifest_at(output, execution=True)
        write_new(path, raw)
        progress.unlink()
        print(json.dumps({"goal": goal, "pattern_id": pattern_id, "repeat": repeat,
                          "max_chain": raw["final"]["max_chain"],
                          "termination": raw["termination"]}), flush=True)
    except Exception as error:
        write_new(failure, {"goal": goal, "pattern_id": pattern_id, "repeat": repeat,
                            "manifest_sha256": manifest["sha256"],
                            "error_type": type(error).__name__, "error": str(error)})
        raise


def run_all(output, jobs):
    manifest_at(output, execution=True)
    tasks = [(goal, pattern_id, repeat) for pattern_id in PATTERN_IDS
             for goal in GOALS for repeat in REPEATS
             if not any((output / (artifact_name(goal, pattern_id, repeat)
                                     .removesuffix(".json.gz") + suffix)).exists()
                        for suffix in (".json.gz", ".progress.json.gz", ".failure.json"))]
    def launch(task):
        goal, pattern_id, repeat = task
        cmd = [sys.executable, "-m", "eval.nextgen_tuned_solo_gate", "worker",
               "--output", str(output), "--goal", goal,
               "--pattern-id", str(pattern_id), "--repeat", str(repeat)]
        process = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        (output / f"{goal}-pattern-{pattern_id}-repeat-{repeat}.log").write_text(
            process.stdout + process.stderr, encoding="utf-8")
        return task, process.returncode, process.stdout.strip().splitlines()[-1:]

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for future in as_completed(pool.submit(launch, task) for task in tasks):
            task, code, tail = future.result()
            print(json.dumps({"task": task, "exit_code": code, "result": tail}), flush=True)


def report(output, *, verify_replays=False):
    manifest = manifest_at(output)
    rows, artifacts = [], {}
    for path in sorted(output.glob("*-pattern-*-repeat-*.json.gz")):
        if path.name.endswith(".progress.json.gz"):
            continue
        raw = validate_raw(read(path), manifest)
        if path.name != artifact_name(raw["goal"], raw["pattern_id"], raw["repeat"]):
            raise ValueError("artifact path mismatch")
        if (verify_replays and replay_audit(raw, new_match(manifest["config"],
                                             raw["pattern_id"])) != raw["audit"]):
            raise ValueError("stored tick replay differs")
        fires = [event for event in raw["events"]
                 if event["type"] == "resolution_complete" and event["chain_count"] > 0]
        rows.append({"goal": raw["goal"], "pattern_id": raw["pattern_id"],
                     "repeat": raw["repeat"], "max_chain": raw["final"]["max_chain"],
                     "max_score": max((event["score_delta"] for event in fires), default=0),
                     "placements": len(raw["chains"]), "game_over": raw["game_over"],
                     "termination": raw["termination"],
                     "small_clears": {key: sum(v["status"] == key for v in raw["small_clear_classifications"])
                                      for key in ("necessary_survival", "unjustified", "unknown")},
                     "suffocation": raw["suffocation_classification"]["status"],
                     "semantic_digest": raw["semantic_digest"],
                     "decision_seconds": [v["seconds"] for v in raw["rows"]]})
        artifacts[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    failures = [read(path) for path in sorted(output.glob("*.failure.json"))]
    goals = {}
    for goal in GOALS:
        selected = [row for row in rows if row["goal"] == goal]
        keyed = {(row["pattern_id"], row["repeat"]): row for row in selected}
        primary = [row for row in selected if row["repeat"] == 1]
        missing = [(pattern, repeat) for pattern in PATTERN_IDS for repeat in REPEATS
                   if (pattern, repeat) not in keyed]
        mismatches = [pattern for pattern in PATTERN_IDS
                      if (pattern, 1) in keyed and (pattern, 2) in keyed and
                          keyed[pattern, 1]["semantic_digest"] != keyed[pattern, 2]["semantic_digest"]]
        counts = {key: sum(row["small_clears"][key] for row in selected)
                  for key in ("necessary_survival", "unjustified", "unknown")}
        mean = sum(row["max_chain"] for row in primary) / 30 if len(primary) == 30 else None
        failed = (mean is not None and mean < 10 or counts["unjustified"] > 0 or
                  any(row["suffocation"] == "avoidable" for row in selected))
        blocked = (bool(missing or mismatches or counts["unknown"] or failures) or
                   any(row["suffocation"] == "unknown" or row["termination"] == "tick_limit"
                       for row in selected))
        goals[goal] = {"status": "FAIL" if failed else "BLOCKED" if blocked else "PASS",
                       "runs": len(selected), "missing": missing, "repeat_mismatch": mismatches,
                       "mean_maximum_actual_chain": mean,
                       "actual_10_plus": sum(row["max_chain"] >= 10 for row in primary),
                       "actual_95000_plus": sum(row["max_score"] >= 95000 for row in primary),
                       "mean_maximum_actual_score": (sum(row["max_score"] for row in primary) / 30
                                                     if len(primary) == 30 else None),
                       "small_clears": counts,
                       "avoidable_suffocations": sum(row["suffocation"] == "avoidable" for row in selected),
                       "unknown_suffocations": sum(row["suffocation"] == "unknown" for row in selected),
                       "game_overs": sum(row["game_over"] for row in primary),
                       "decision_seconds": distribution([seconds for row in selected
                                                         for seconds in row["decision_seconds"]])}
    result = {"schema": SCHEMA, "manifest_sha256": manifest["sha256"],
              "PUYO-266": goals["chain10"]["status"], "goals": goals,
              "rows": rows, "artifacts": artifacts, "failed_workers": failures,
              "source_changed_since_declaration": current_source() != manifest["source"]}
    (output / "report.json").write_text(json.dumps(result, indent=2, sort_keys=True),
                                          encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "worker", "run-all", "report"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tsumo-source", type=Path)
    parser.add_argument("--goal", choices=GOALS)
    parser.add_argument("--pattern-id", type=int)
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--verify-replays", action="store_true")
    args = parser.parse_args()
    if args.command == "init":
        if args.tsumo_source is None:
            parser.error("init requires --tsumo-source")
        value = initialize(args.output, args.tsumo_source)
        print(value["sha256"])
    elif args.command == "worker":
        if args.goal is None or args.pattern_id is None or args.repeat is None:
            parser.error("worker requires --goal, --pattern-id, and --repeat")
        worker(args.output, args.goal, args.pattern_id, args.repeat)
    elif args.command == "run-all":
        if args.jobs < 1:
            parser.error("--jobs must be positive")
        run_all(args.output, args.jobs)
    else:
        result = report(args.output, verify_replays=args.verify_replays)
        print(json.dumps({"PUYO-266": result["PUYO-266"], "goals": result["goals"],
                          "failed_workers": result["failed_workers"]}, indent=2))


if __name__ == "__main__":
    main()
