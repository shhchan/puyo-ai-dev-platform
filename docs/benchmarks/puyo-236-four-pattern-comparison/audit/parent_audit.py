"""Independent raw-result audit; no imports from the candidate or its runner.

Run only after performance measurement. Inputs are ARM=canonical-output-dir.
The optional output file is created exclusively, never overwritten.
"""

import argparse
import collections
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics


OLD_SEEDS = {123, 126, 130, 133, 135, 138, 147, 151}
EXPECTED = {(seed, repeat) for seed in range(123, 153) for repeat in (1, 2)}


def reject_constant(value):
    raise ValueError(f"Non-standard JSON constant: {value}")


def digest(value, prefix):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256((prefix + ":" + encoded).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def percentile(values, quantile):
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def distribution(values):
    return dict(count=len(values), mean=statistics.mean(values),
                median=statistics.median(values), p95=percentile(values, .95),
                minimum=min(values), maximum=max(values))


def audit_run(run):
    records = run["records"]
    require(run["fully_evaluated"] is True, "Incomplete result")
    require(all("action" in r for r in records), "Decision error in purported complete run")
    require(run["max_steps"] == 40 and run["target_chain_count"] == 10, "Changed condition")
    require(run["profile"] == "reference" and run["backend"] == "native", "Changed backend/profile")
    require(run["completed_turns"] == len(records), "Placement count mismatch")
    require([r["turn"] for r in records] == list(range(len(records))), "Turn sequence mismatch")
    require(all(r["actual_result"]["valid"] for r in records), "Invalid actual placement")
    gameover = bool(records[-1]["actual_result"]["game_over"]) if records else False
    require(run["game_over"] == gameover, "Game-over summary mismatch")
    require(not any(r["actual_result"]["game_over"] for r in records[:-1]), "Continued after game over")
    reason = run["termination_reason"]
    require((reason == "turn_limit" and len(records) == 40 and not gameover)
            or (reason == "game_over" and gameover and 0 < len(records) <= 40),
            "Invalid termination")
    chains = [r["actual_result"]["chain_count"] for r in records if r["actual_result"]["chain_count"] > 0]
    require(chains == run["actual_fire_chain_counts"], "Fire-list mismatch")
    require(max(chains, default=0) == run["maximum_actual_fire_chain_count"], "Maximum-chain mismatch")
    require(sum(c < 10 for c in chains) == run["premature_fire_count"], "Premature-count mismatch")
    require(run["fallback_count"] == 0, "Fallback observed")
    require(run["simulator_parity_mismatch_count"] == 0 and all(r["parity"]["passed"] for r in records),
            "Parity mismatch observed")
    for record in records:
        require(record["target_chain_count"] == 10, "Decision target changed")
        require(record["plan"]["objective"]["minimum_chain_count"] == 10, "Plan target changed")
        require(0 <= record["search"]["counters"]["expanded_nodes"] <= 600000, "Node cap exceeded")
        require(math.isfinite(record["elapsed_seconds"]) and record["elapsed_seconds"] >= 0, "Invalid latency")
        decision = {k: record[k] for k in ("turn", "action", "board_before", "board_after", "actual_result", "plan", "fallback")}
        decision["search_digest"] = record["search"]["deterministic_digest"]
        require(digest(decision, "deep-chain-decision") == record["decision_digest"], "Decision digest mismatch")
    plans = [{"turn": r["turn"], "plan_id": r["plan"].get("plan_id", ""),
              "actions": [s.get("action") for s in r["plan"].get("steps", [])],
              "state_fingerprints": [s.get("state_fingerprint") for s in r["plan"].get("steps", [])]}
             for r in records]
    for field, payload, prefix in (
        ("action_digest", [r["action"] for r in records], "deep-chain-run-actions"),
        ("plan_digest", plans, "deep-chain-run-plans"),
        ("trajectory_digest", [r["decision_digest"] for r in records], "deep-chain-run-trajectory"),
    ):
        require(digest(payload, prefix) == run[field], f"{field} mismatch")
    audit_seconds = run.get("request_audit_seconds", [0.0] * len(records))
    require(len(audit_seconds) == len(records), "Audit timer count mismatch")
    require(all(math.isfinite(a) and 0 <= a <= r["elapsed_seconds"] for a, r in zip(audit_seconds, records)), "Invalid audit timer")
    return {"maximum_chain": max(chains, default=0), "success": max(chains, default=0) >= 10,
            "clean_success": max(chains, default=0) >= 10 and not gameover and all(c >= 10 for c in chains),
            "premature_fires": sum(c < 10 for c in chains), "game_over": gameover, "no_fire": not chains,
            "placements": len(records), "run_seconds": run["elapsed_seconds"],
            "decision_seconds": [r["elapsed_seconds"] for r in records],
            "receipt_audit_seconds": audit_seconds,
            "decision_seconds_minus_audit_reference": [r["elapsed_seconds"] - a for r, a in zip(records, audit_seconds)],
            "run_seconds_minus_audit_reference": run["elapsed_seconds"] - sum(audit_seconds),
            "expanded_nodes": sum(r["search"]["counters"]["expanded_nodes"] for r in records),
            "evaluated_nodes": sum(r["search"]["counters"].get("evaluated_nodes", 0) for r in records),
            "peak_rss_kib": run["process_resources"]["peak_rss_kib"]}


def quality(rows):
    return {"unique_seeds": len(rows), "target_success_count": sum(r["success"] for r in rows),
            "clean_success_count": sum(r["clean_success"] for r in rows),
            "maximum_chain": distribution([r["maximum_chain"] for r in rows]),
            "chain_distribution": dict(sorted(collections.Counter(r["maximum_chain"] for r in rows).items())),
            "premature_fires": sum(r["premature_fires"] for r in rows),
            "premature_seed_count": sum(r["premature_fires"] > 0 for r in rows),
            "game_over_seeds": sum(r["game_over"] for r in rows), "no_fire_seeds": sum(r["no_fire"] for r in rows)}


def audit_arm(root):
    runs, rows, checksums = {}, {}, {}
    paths = sorted((root / "target-10").glob("seed-*-repeat-*.json.gz"))
    require(len(paths) == 60, f"Expected 60 raw runs, got {len(paths)}: {root}")
    for path in paths:
        raw = path.read_bytes()
        run = json.loads(gzip.decompress(raw), parse_constant=reject_constant)
        require("request_audit_seconds" in run, "Full measurement lacks separate receipt timer")
        identity = (run["seed"], run["repeat"])
        require(identity not in runs, "Duplicate identity")
        runs[identity] = run
        rows[identity] = audit_run(run)
        checksums[str(path.relative_to(root))] = hashlib.sha256(raw).hexdigest()
    require(set(runs) == EXPECTED, "Missing or unexpected identities")
    provenance = {}
    for field in ("evaluated_commit", "configuration_sha256", "backend_configuration_sha256", "manifest_sha256"):
        values = {run[field] for run in runs.values()}
        require(len(values) == 1, f"Mixed {field}")
        provenance[field] = next(iter(values))
    for seed in range(123, 153):
        for field in ("action_digest", "plan_digest", "trajectory_digest", "final_board_digest"):
            require(runs[(seed, 1)][field] == runs[(seed, 2)][field], f"Repeat mismatch {seed} {field}")
    unique = {seed: rows[(seed, 1)] for seed in range(123, 153)}
    return {"passed": True, "root": str(root), "provenance": provenance, "valid_runs": 60,
            "matching_repeat_pairs": 30, "raw_checksums": checksums,
            "quality_all_30": quality(list(unique.values())),
            "quality_previous_selected_8": quality([r for s, r in unique.items() if s in OLD_SEEDS]),
            "quality_additional_22": quality([r for s, r in unique.items() if s not in OLD_SEEDS]),
            "run_seconds_all_60": distribution([r["run_seconds"] for r in rows.values()]),
            "decision_seconds_all_60": distribution([v for r in rows.values() for v in r["decision_seconds"]]),
            "receipt_audit_seconds_all_60": distribution([v for r in rows.values() for v in r["receipt_audit_seconds"]]),
            "run_seconds_minus_audit_reference_all_60": distribution([r["run_seconds_minus_audit_reference"] for r in rows.values()]),
            "decision_seconds_minus_audit_reference_all_60": distribution([v for r in rows.values() for v in r["decision_seconds_minus_audit_reference"]]),
            "expanded_nodes_all_60": sum(r["expanded_nodes"] for r in rows.values()),
            "evaluated_nodes_all_60": sum(r["evaluated_nodes"] for r in rows.values()),
            "placements_all_60": sum(r["placements"] for r in rows.values()),
            "rss_peak_kib_all_60": max(r["peak_rss_kib"] for r in rows.values()),
            "per_seed": {str(s): {**{k: v for k, v in row.items() if k not in ("decision_seconds", "receipt_audit_seconds", "decision_seconds_minus_audit_reference")},
                                    "paired_repeat_mean_run_seconds": statistics.mean(rows[(s, rep)]["run_seconds"] for rep in (1, 2))}
                         for s, row in unique.items()}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("arms", nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    arms = {name: audit_arm(Path(path)) for name, path in (arg.split("=", 1) for arg in args.arms)}
    require(set(arms) == {"none", "only240", "only242", "both"}, "Four arms required")
    baseline = arms["none"]
    for name, arm in arms.items():
        q, bq = arm["quality_all_30"], baseline["quality_all_30"]
        changes = [{"seed": seed, "maximum_chain_delta": arm["per_seed"][str(seed)]["maximum_chain"] - baseline["per_seed"][str(seed)]["maximum_chain"]}
                   for seed in range(123, 153)]
        arm["vs_none"] = {
            "target_success_percentage_point_delta": 100 * (q["target_success_count"] - bq["target_success_count"]) / 30,
            "mean_maximum_chain_relative_percent": 100 * (q["maximum_chain"]["mean"] / bq["maximum_chain"]["mean"] - 1),
            "mean_run_seconds_relative_percent": 100 * (arm["run_seconds_all_60"]["mean"] / baseline["run_seconds_all_60"]["mean"] - 1),
            "mean_run_seconds_minus_audit_reference_relative_percent": 100 * (arm["run_seconds_minus_audit_reference_all_60"]["mean"] / baseline["run_seconds_minus_audit_reference_all_60"]["mean"] - 1),
            "maximum_chain_improved_seeds": [r["seed"] for r in changes if r["maximum_chain_delta"] > 0],
            "maximum_chain_regressed_seeds": [r["seed"] for r in changes if r["maximum_chain_delta"] < 0],
            "rescued_success_seeds": [s for s in range(123, 153) if arm["per_seed"][str(s)]["success"] and not baseline["per_seed"][str(s)]["success"]],
            "lost_success_seeds": [s for s in range(123, 153) if not arm["per_seed"][str(s)]["success"] and baseline["per_seed"][str(s)]["success"]],
        }
    result = {"schema": "puyo236.parent_raw_audit.v1", "passed": True,
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "percentile_method": "linear interpolation at (n-1)*q", "quality_scope": "repeat1 only, 30 unique fixed seeds",
              "limits": "No population inference, no adoption decision; replay verifier and future-isolation diagnostics reviewed separately.", "arms": arms}
    rendered = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x") as handle:
            handle.write(rendered)
        print(json.dumps({"passed": True, "output": str(args.output), "valid_runs": 240}))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
