"""Preregistered esports solo quality gate, separate from legacy random G2.

init freezes a clean source/build/config/provider identity. run-all launches
120 fresh processes in sequence. verify replays every recorded tick without
running a policy. Missing or unclassified evidence never becomes PASS.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from agents.deep_chain_builder import load_deep_chain_builder_config
from agents.nextgen_profiles import nextgen_search_settings
from eval.nextgen_gates import digest, distribution
from eval.nextgen_realtime_diagnostic import source_identity
from eval.nextgen_safe_build_gate import build_identity, read, write_new
from eval.nextgen_single_quality_runtime import (
    POLICIES,
    POLICY_SEED,
    PROOF_NODE_LIMIT,
    SingleQualityMatch,
    checkpoint,
    classify_small_clear,
    make_policy,
    measure,
    public_state,
    replay_audit,
    semantic_payload,
)
from src.core.tsumo import EsportsTsuSource

ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION = ROOT / "tests/fixtures/nextgen_single_preregistration.json"
SCHEMA = "puyo.nextgen.esports_single_quality.v1"
PATTERN_IDS = tuple((i * 65535) // 29 for i in range(30))
REPEATS = (1, 2)


def declaration():
    value = read(PREREGISTRATION)
    if (value["pattern_ids"] != list(PATTERN_IDS) or value["repeats"] != list(REPEATS)
            or value["policies"] != list(POLICIES)
            or value["construction_placements"] != 40
            or value["maximum_activation_placements"] != 6
            or value["thresholds"] != {"mean_maximum_actual_chain": 10,
                                        "unjustified_premature": 0, "avoidable_suffocation": 0}):
        raise ValueError("preregistered cohort/thresholds changed")
    return value


def current_source():
    value = source_identity()
    paths = subprocess.check_output(["git", "ls-files", "tests"], cwd=ROOT, text=True).splitlines()
    value["test_files_sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
    return value


def require_clean():
    changed = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "agents", "puyo_env",
         "src", "eval", "train", "tests"], cwd=ROOT, text=True).strip()
    if changed:
        raise ValueError("commit runtime, gate and tests before freezing/executing")


def configuration(source_path):
    source = EsportsTsuSource(source_path)
    reference = load_deep_chain_builder_config().profile("reference")
    profile, search = nextgen_search_settings("nextgen_safe_build", seed=POLICY_SEED)
    if any(getattr(search, key) != getattr(reference, key) for key in (
            "depth", "width", "scenarios", "max_expanded_nodes")):
        raise ValueError("reference and nextgen budgets differ")
    policy = make_policy("nextgen_tactic_manager")
    return {
        "preregistration": declaration(), "preregistration_sha256": digest(declaration()),
        "pattern_ids": list(PATTERN_IDS), "repeats": list(REPEATS), "policies": list(POLICIES),
        "source_path": str(Path(source_path).resolve()),
        "provider": {"mode": "esports_tsu", "sha256": source.checksum, "version": source.version,
                     "pattern_mapping": {str(p): {k: v.name for k, v in source.color_mapping(p).items()}
                                         for p in PATTERN_IDS}},
        "construction_placements": 40, "maximum_activation_placements": 6,
        "total_resolved_placements": 46, "max_ticks": 30000,
        "termination": "same unchanged policy until 46 resolutions or game over; tick limit is incomplete",
        "policy_seed": POLICY_SEED, "environment_seed": 55,
        "seed_contract": "independent of provider pattern ID; no pattern-derived policy seed",
        "nextgen_profile": profile.to_dict(), "nextgen_search": asdict(search),
        "reference_profile": reference.to_dict(), "target_chain_count": 10,
        "catalog_sha256": policy.catalog.semantic_digest,
        "template_binding_budget": policy.template_binding_budget,
        "information_boundary": "current/NEXT/NEXT2 and visible board; hidden only deduced from public actual-lock lifecycle; unknown blocks",
        "reference_adapter": "public snapshot plus PublicInferenceTracker; no actual ghost/private field/provider objects",
        "comparison_limits": ["GTR phase exists only in nextgen",
                              "reference uses public inferred hidden in search; nextgen uses it only for survival",
                              "scenario seed derivation differs; both exclude private future"],
        "environment": "solo; frozen opponent; attacks suppressed in both directions",
        "controller": {"latency_mode": "configured", "inference_latency_ticks": 0,
                       "use_reachable_action_mask": True},
        "backend": "native", "workers": 1, "process_isolation": "fresh process per policy/pattern/repeat",
        "offline_proof_node_limit": PROOF_NODE_LIMIT,
        "thresholds": declaration()["thresholds"],
        "training_allowed": False,
    }


def initialize(output, source_path):
    require_clean()
    config = configuration(source_path)
    manifest = {"schema": SCHEMA, "source": current_source(), "build": build_identity(),
                "config": config, "config_sha256": digest(config),
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    manifest["sha256"] = digest(manifest)
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "manifest.json", manifest)
    return manifest


def load_manifest(output, *, execution=False):
    manifest = read(output / "manifest.json")
    if (manifest["schema"] != SCHEMA
            or digest({k: v for k, v in manifest.items() if k != "sha256"}) != manifest["sha256"]
            or digest(manifest["config"]) != manifest["config_sha256"]):
        raise ValueError("manifest schema/checksum mismatch")
    config = manifest["config"]
    if (config["preregistration"] != declaration()
            or config["pattern_ids"] != list(PATTERN_IDS)
            or config["repeats"] != list(REPEATS) or config["policies"] != list(POLICIES)
            or config["total_resolved_placements"] != 46
            or config["thresholds"] != declaration()["thresholds"]):
        raise ValueError("manifest differs from preregistered contract")
    if execution:
        require_clean()
        if current_source() != manifest["source"] or build_identity() != manifest["build"]:
            raise ValueError("source/build/host changed after declaration")
        if configuration(config["source_path"]) != config:
            raise ValueError("current provider/configuration differs from declaration")
    return manifest


def new_match(config, pattern_id):
    return SingleQualityMatch(seed=config["environment_seed"], tsumo_mode="esports_tsu",
                              tsumo_source=config["source_path"], tsumo_pattern_id=pattern_id)


def artifact_name(policy, pattern_id, repeat):
    return f"{policy}-pattern-{pattern_id}-repeat-{repeat}.json.gz"


def identity_check(policy, pattern_id, repeat):
    if policy not in POLICIES or pattern_id not in PATTERN_IDS or repeat not in REPEATS:
        raise ValueError("unexpected cohort identity")


def integrity_issues(raw):
    issues = []
    chains = [e["chain_count"] for e in raw["events"] if e["type"] == "resolution_complete"]
    if chains != raw["chains"] or raw["final"]["placements"] != len(chains):
        raise ValueError("actual resolution/summary mismatch")
    if raw["final"] != checkpoint(chains, len(raw["ticks"]), raw["ticks"][-1]["state_hash"]):
        raise ValueError("final checkpoint mismatch")
    resolutions = [e for e in raw["events"] if e["type"] == "resolution_complete"]
    forty = None
    if len(chains) >= 40:
        tick = resolutions[39]["tick"]
        saved = next(v for v in raw["ticks"] if v["tick"] == tick)
        forty = checkpoint(chains[:40], tick + 1, saved["state_hash"])
    if forty != raw["checkpoint40"]:
        raise ValueError("40-placement checkpoint mismatch")
    if raw["errors"]:
        issues.append("scheduler_errors")
    if raw["controller"]["fallback_actions"] or raw["controller"]["stale_decisions"]:
        issues.append("fallback_or_stale")
    if raw["termination"] not in ("placements", "game_over") or (
            raw["termination"] == "placements" and len(chains) != 46):
        issues.append("incomplete_window")
    if (raw["termination"] == "game_over") != raw["game_over"]:
        raise ValueError("termination/game_over mismatch")
    for row in raw["rows"]:
        if row["inference"]["status"] != "known":
            issues.append("public_inference_unknown")
        counters = row["counters"]
        if raw["policy"] == "nextgen_tactic_manager":
            d = next((d for d in raw["ledger"] if d["request"]["execution"]["request_tick"] == row["tick"]), None)
            if d is None:
                issues.append("missing_nextgen_receipt")
                continue
            c.Diagnostics.from_dict(d)
            if any(counters[k] > limit for k, limit in (
                    ("shared_nodes", 600000), ("template_nodes", 128), ("response_nodes", 256))):
                issues.append("quota_exceeded")
            phase = d["request"]["control"]["phase"]
            if phase["active"] and (phase["decision_limit"] != 14 or phase["remaining_decisions"] <= 0):
                issues.append("template_limit_exceeded")
            if row["phase"].get("exit_reason") == "completed" and phase["active"]:
                issues.append("completed_template_still_active")
        elif counters["expanded_nodes"] > 600000 or row["fallback"]["used"]:
            issues.append("reference_quota_or_fallback")
    audit = raw["audit"]
    if not audit["all_tick_hashes_match"] or audit["lock_mismatches"] or audit["unlocked_requests"]:
        issues.append("replay_or_actual_lock_mismatch")
    if sum(r["resolution"] is not None for r in audit["records"]) != len(chains):
        issues.append("unattributed_resolution")
    for r in raw["receipts"]:
        executed = r["executed_action"] if r["executed_action"] is not None else r["action_index"]
        if r["outcome"] != "activated" or r["requested_action"] != executed:
            issues.append("receipt_adoption_mismatch")
    return sorted(set(issues))


def classify_suffocation(raw):
    if not raw["game_over"]:
        return {"status": "none", "reason": "no_suffocation"}
    # A local no-alternative position never proves the whole game unavoidable.
    result = {"status": "unknown", "reason": "no_public_causal_avoidance_proof"}
    for row in reversed(raw["rows"]):
        try:
            public = c.PublicPlayerState.from_dict(row["public"])
            state = public_state(public, c.PublicBoardInference.from_dict(row["inference"]))
        except ValueError:
            continue
        pair = tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in public.known_pieces[0])
        chosen = transition(state, pair, row["action"])
        if chosen.valid and chosen.game_over:
            alternatives = [a for a in legal_action_indices(state) if row["reachable_mask"][a]
                            and (value := transition(state, pair, a)).valid and not value.game_over]
            if alternatives:
                return {"status": "avoidable", "reason": "public_reachable_nonfatal_alternative",
                        "request_tick": row["tick"], "alternatives": alternatives,
                        "scope": "immediate suffocation, not long-term survival"}
    return result


def complete_evidence(raw, match):
    raw["audit"] = replay_audit(raw, match)
    by_tick = {r["tick"]: r for r in raw["rows"]}
    small = []
    for record in raw["audit"]["records"]:
        event = record["resolution"]
        if event and 0 < event["chain_count"] < 10:
            small.append(classify_small_clear(by_tick[record["request_tick"]], event["chain_count"]))
    raw["small_clear_classifications"] = small
    raw["suffocation_classification"] = classify_suffocation(raw)
    raw["semantic_digest"] = digest(semantic_payload(raw))
    raw["integrity_issues"] = integrity_issues(raw)
    return raw


def worker(output, policy, pattern_id, repeat):
    identity_check(policy, pattern_id, repeat)
    manifest = load_manifest(output, execution=True)
    path = output / artifact_name(policy, pattern_id, repeat)
    failure = output / (path.name.removesuffix(".json.gz") + ".failure.json")
    progress_path = output / (path.name.removesuffix(".json.gz") + ".progress.json.gz")
    if path.exists() or failure.exists() or progress_path.exists():
        raise ValueError("identity already recorded; refusing overwrite/retry")

    def save_progress(partial):
        partial = dict(partial, schema=SCHEMA, pattern_id=pattern_id, repeat=repeat,
                       manifest_sha256=manifest["sha256"], partial=True)
        staging = progress_path.with_suffix(".tmp")
        staging.write_bytes(gzip.compress(json.dumps(partial, allow_nan=False).encode(), mtime=0))
        staging.replace(progress_path)

    raw = None
    try:
        raw = measure(new_match(manifest["config"], pattern_id), make_policy(policy), policy,
                      progress=save_progress)
        raw.update(schema=SCHEMA, pattern_id=pattern_id, repeat=repeat, manifest_sha256=manifest["sha256"])
        complete_evidence(raw, new_match(manifest["config"], pattern_id))
        load_manifest(output, execution=True)
        write_new(path, raw)
        progress_path.unlink()
    except Exception as error:
        if raw is not None:
            write_new(output / (path.name.removesuffix(".json.gz") + ".partial.json.gz"), raw)
        write_new(failure, {"policy": policy, "pattern_id": pattern_id, "repeat": repeat,
                            "manifest_sha256": manifest["sha256"],
                            "error_type": type(error).__name__, "error": str(error)})
        raise
    print(json.dumps({"policy": policy, "pattern_id": pattern_id, "repeat": repeat,
                      "final": raw["final"], "integrity_issues": raw["integrity_issues"]}), flush=True)
    return raw


def validated_row(raw, manifest):
    identity_check(raw["policy"], raw["pattern_id"], raw["repeat"])
    if raw["schema"] != SCHEMA or raw["manifest_sha256"] != manifest["sha256"]:
        raise ValueError("run belongs to another declaration")
    if raw["semantic_digest"] != digest(semantic_payload(raw)):
        raise ValueError("trajectory semantic checksum mismatch")
    provider = manifest["config"]["provider"]
    tsumo = raw["match_rules"].get("tsumo", {})
    mapping = provider["pattern_mapping"][str(raw["pattern_id"])]
    if (tsumo.get("mode") != "esports_tsu" or tsumo.get("pattern_id") != raw["pattern_id"]
            or tsumo.get("source_sha256") != provider["sha256"]
            or tsumo.get("source_version") != provider["version"]
            or tsumo.get("color_mapping") != {"player_0": mapping, "player_1": mapping}):
        raise ValueError("run provider identity differs from manifest")
    issues = integrity_issues(raw)
    if issues != raw["integrity_issues"]:
        raise ValueError("integrity summary mismatch")
    counts = {key: sum(v["status"] == key for v in raw["small_clear_classifications"])
              for key in ("necessary_survival", "unjustified", "unknown")}
    if sum(counts.values()) != sum(0 < chain < 10 for chain in raw["chains"]):
        raise ValueError("missing or invalid small-clear classification")
    suffocation = raw["suffocation_classification"]["status"]
    if suffocation not in ("none", "unknown", "avoidable") or ((suffocation == "none") == raw["game_over"]):
        raise ValueError("invalid suffocation classification")
    return {"policy": raw["policy"], "pattern_id": raw["pattern_id"], "repeat": raw["repeat"],
            "termination": raw["termination"], "final": raw["final"], "checkpoint40": raw["checkpoint40"],
            "game_over": raw["game_over"], "small_clears": counts, "suffocation": suffocation,
            "semantic_digest": raw["semantic_digest"], "integrity_issues": issues,
            "decision_seconds": [r["seconds"] for r in raw["rows"]]}


def summarize(rows):
    seen = {(v["policy"], v["pattern_id"], v["repeat"]) for v in rows}
    if len(seen) != len(rows):
        raise ValueError("duplicate cohort identity")
    expected = {(p, i, r) for p in POLICIES for i in PATTERN_IDS for r in REPEATS}
    if seen - expected:
        raise ValueError("unexpected cohort identity")
    policies = {}
    for policy in POLICIES:
        selected = [r for r in rows if r["policy"] == policy]
        repeat_one = [r for r in selected if r["repeat"] == 1]
        by_id = {(r["pattern_id"], r["repeat"]): r for r in selected}
        missing = sorted((i, r) for i in PATTERN_IDS for r in REPEATS if (i, r) not in by_id)
        mismatches = [i for i in PATTERN_IDS if all((i, r) in by_id for r in REPEATS)
                      and by_id[i, 1]["semantic_digest"] != by_id[i, 2]["semantic_digest"]]
        mean = sum(r["final"]["max_chain"] for r in repeat_one) / 30 if len(repeat_one) == 30 else None
        counts = {key: sum(r["small_clears"][key] for r in selected)
                  for key in ("necessary_survival", "unjustified", "unknown")}
        avoidable = sum(r["suffocation"] == "avoidable" for r in selected)
        unknown_deaths = sum(r["suffocation"] == "unknown" for r in selected)
        failed = []
        if mean is not None and mean < 10:
            failed.append("mean_maximum_actual_chain_below_10")
        if counts["unjustified"]:
            failed.append("unjustified_premature")
        if avoidable:
            failed.append("avoidable_suffocation")
        blockers = []
        if missing:
            blockers.append("missing_runs")
        if mismatches:
            blockers.append("repeat_mismatch")
        if counts["unknown"] or unknown_deaths:
            blockers.append("unclassified_quality_events")
        if any(r["integrity_issues"] for r in selected):
            blockers.append("integrity_or_incomplete_run")
        policies[policy] = {
            "status": "FAIL" if failed else "BLOCKED" if blockers else "PASS",
            "failed_conditions": failed, "blocked_conditions": blockers,
            "completed_artifacts": len(selected), "missing": missing, "repeat_mismatch": mismatches,
            "mean_maximum_actual_chain": mean, "small_clears": counts,
            "avoidable_suffocations": avoidable, "unknown_suffocations": unknown_deaths,
            "game_overs": sum(r["game_over"] for r in selected),
            "decision_seconds": distribution([v for r in selected for v in r["decision_seconds"]]),
        }
    nextgen = policies[POLICIES[0]]
    status = nextgen["status"]
    if status == "PASS" and policies[POLICIES[1]]["blocked_conditions"]:
        status = "BLOCKED"
    return {"schema": SCHEMA, "single_quality_status": status, "policies": policies,
            "rows": rows, "G2": "BLOCKED", "long_training_allowed": False,
            "other_requirements": "PUYO-277 battle gate and remaining integration/human QA are separate"}


def finalize(output, *, verify_replays=False):
    manifest = load_manifest(output)
    rows, artifacts = [], {}
    for path in sorted(output.glob("*-pattern-*-repeat-*.json.gz")):
        if path.name.endswith((".partial.json.gz", ".progress.json.gz")):
            continue
        raw = read(path)
        if path.name != artifact_name(raw["policy"], raw["pattern_id"], raw["repeat"]):
            raise ValueError("filename/identity mismatch")
        if verify_replays:
            audit = replay_audit(raw, new_match(manifest["config"], raw["pattern_id"]))
            if audit != raw["audit"]:
                raise ValueError("saved replay audit mismatch")
            by_tick = {r["tick"]: r for r in raw["rows"]}
            classifications = [classify_small_clear(by_tick[r["request_tick"]], r["resolution"]["chain_count"])
                               for r in audit["records"] if r["resolution"] and 0 < r["resolution"]["chain_count"] < 10]
            if (classifications != raw["small_clear_classifications"]
                    or classify_suffocation(raw) != raw["suffocation_classification"]):
                raise ValueError("public quality proof mismatch")
        rows.append(validated_row(raw, manifest))
        artifacts[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = summarize(rows)
    failures = [read(p) for p in sorted(output.glob("*.failure.json"))]
    report.update(manifest_sha256=manifest["sha256"], artifacts=artifacts, failed_workers=failures,
                  source_changed_since_declaration=current_source() != manifest["source"])
    if failures and report["single_quality_status"] == "PASS":
        report["single_quality_status"] = "BLOCKED"
    if not verify_replays:
        write_new(output / "report.json", report)
    return report


def run_all(output):
    manifest = load_manifest(output, execution=True)
    for pattern_id in PATTERN_IDS:
        for policy in POLICIES:
            for repeat in REPEATS:
                path = output / artifact_name(policy, pattern_id, repeat)
                failure = output / (path.name.removesuffix(".json.gz") + ".failure.json")
                progress = output / (path.name.removesuffix(".json.gz") + ".progress.json.gz")
                if path.exists():
                    validated_row(read(path), manifest)
                    print("RESUME_SKIP_COMPLETE", policy, pattern_id, repeat, flush=True)
                    continue
                if progress.exists() and not failure.exists():
                    write_new(failure, {"policy": policy, "pattern_id": pattern_id, "repeat": repeat,
                                       "manifest_sha256": manifest["sha256"],
                                       "error_type": "InterruptedWorker", "error": "saved partial run; preserved without retry"})
                if failure.exists():
                    print("RESUME_SKIP_FAILED", policy, pattern_id, repeat, flush=True)
                    continue
                cmd = [sys.executable, "-m", "eval.nextgen_single_quality_gate", "--output", str(output),
                       "worker", "--policy", policy, "--pattern-id", str(pattern_id), "--repeat", str(repeat)]
                result = subprocess.run(cmd, cwd=ROOT, check=False)
                if result.returncode:
                    print("FAILED", policy, pattern_id, repeat, result.returncode, flush=True)


def smoke(output, source_path):
    """Fixed first preregistered pattern, three placements per unchanged policy."""
    require_clean()
    config = configuration(source_path)
    manifest = {"schema": SCHEMA + ".diagnostic", "source": current_source(),
                "build": build_identity(), "config": config, "pattern_id": PATTERN_IDS[0],
                "placements": 3, "quality_status": "DIAGNOSTIC_ONLY"}
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "manifest.json", manifest)
    summaries = []
    for policy in POLICIES:
        started = time.perf_counter()
        progress_path = output / f"{policy}.progress.json.gz"

        def preserve_partial(value):
            staging = progress_path.with_suffix(".tmp")
            staging.write_bytes(gzip.compress(json.dumps(value, allow_nan=False).encode(), mtime=0))
            staging.replace(progress_path)

        try:
            raw = measure(new_match(config, PATTERN_IDS[0]), make_policy(policy), policy,
                          placements=3, progress=preserve_partial)
            write_new(output / f"{policy}.raw.json.gz", raw)
            complete_evidence(raw, new_match(config, PATTERN_IDS[0]))
            path = output / f"{policy}.json.gz"
            write_new(path, raw)
            progress_path.unlink()
            summaries.append({"policy": policy, "elapsed_seconds": time.perf_counter() - started,
                              "decision_seconds": distribution([r["seconds"] for r in raw["rows"]]),
                              "placements": len(raw["chains"]), "raw_gzip_bytes": path.stat().st_size,
                              "integrity_issues": raw["integrity_issues"], "max_chain": raw["final"]["max_chain"]})
        except Exception as error:
            write_new(output / f"{policy}.failure.json", {"error_type": type(error).__name__, "error": str(error)})
            raise
    if current_source() != manifest["source"] or build_identity() != manifest["build"]:
        raise ValueError("smoke source/build changed")
    report = {"quality_status": "DIAGNOSTIC_ONLY", "policies": summaries,
              "formal_run_count": 120, "formal_maximum_resolved_placements": 5520,
              "linear_elapsed_estimate_seconds": sum(r["elapsed_seconds"] * 46 / 3 * 60 for r in summaries),
              "linear_raw_gzip_estimate_bytes": sum(r["raw_gzip_bytes"] * 46 / 3 * 60 for r in summaries),
              "limit": "three opening decisions per policy do not estimate late-game proof/replay overhead reliably"}
    write_new(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "worker", "run-all", "finalize", "verify", "smoke"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--policy", choices=POLICIES)
    parser.add_argument("--pattern-id", type=int, choices=PATTERN_IDS)
    parser.add_argument("--repeat", type=int, choices=REPEATS)
    args = parser.parse_args()
    if args.command in ("init", "smoke") and args.source is None:
        parser.error("init/smoke requires --source")
    if args.command == "worker" and any(v is None for v in (args.policy, args.pattern_id, args.repeat)):
        parser.error("worker requires --policy, --pattern-id and --repeat")
    if args.command == "init":
        result = initialize(args.output, args.source)
    elif args.command == "smoke":
        result = smoke(args.output, args.source)
    elif args.command == "worker":
        worker(args.output, args.policy, args.pattern_id, args.repeat)
        return
    elif args.command == "run-all":
        run_all(args.output)
        return
    else:
        result = finalize(args.output, verify_replays=args.command == "verify")
    print(json.dumps({k: result[k] for k in ("sha256", "single_quality_status", "policies", "G2") if k in result}, indent=2))


if __name__ == "__main__":
    main()
