"""Independent read-only preflight/reproduction audit before full measurement."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from parent_audit import OLD_SEEDS, audit_run, require, reject_constant


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw, parse_constant=reject_constant)


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("preparation", type=Path)
    parser.add_argument("previous", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reproduction = []
    sources = {"none": "puyo-240-evidence/baseline", "only240": "puyo-240-evidence/candidate2", "only242": "puyo-242-evidence/candidate"}
    keys = ("action_digest", "plan_digest", "trajectory_digest", "final_board_digest",
            "maximum_actual_fire_chain_count", "actual_fire_chain_counts", "premature_fire_count",
            "game_over", "completed_turns", "termination_reason")
    for arm, directory in sources.items():
        for seed in sorted(OLD_SEEDS):
            relative = Path(f"target-10/seed-{seed}-repeat-01.json.gz")
            current_path = args.preparation / arm / relative
            previous_path = args.previous / directory / relative
            current, previous = read(current_path), read(previous_path)
            measured = audit_run(current)
            require(all(current[key] == previous[key] for key in keys), f"Old reproduction mismatch: {arm}/{seed}")
            require([r["search"]["counters"] for r in current["records"]] == [r["search"]["counters"] for r in previous["records"]], f"Search counter mismatch: {arm}/{seed}")
            reproduction.append({"arm": arm, "seed": seed, "maximum_chain": measured["maximum_chain"],
                                 "decisions_audited": measured["placements"], "passed": True,
                                 "current_sha256": checksum(current_path), "previous_sha256": checksum(previous_path)})
    preflights, fixed, declarations = {}, {}, {}
    for arm in ("none", "only240", "only242", "both"):
        path = args.preparation / arm / "preflight.json.gz"
        payload = read(path)
        manifest = read(args.preparation / arm / "experiment_manifest.json")
        require(payload["manifest_sha256"] == manifest["manifest_sha256"], f"Preflight manifest: {arm}")
        require(payload["future_isolation"]["passed"] is True, f"Future boundary: {arm}")
        require([r["seed"] for r in payload["private_counterfactuals"]] == list(range(123, 153)), f"Private seed coverage: {arm}")
        require(all(r["counterfactual_matches"] is True for r in payload["private_counterfactuals"]), f"Private counterfactual: {arm}")
        diagnostic_path = args.preparation / arm / "diagnostic-target-10.json"
        require(checksum(diagnostic_path) == payload["cold_warm_diagnostic_sha256"], f"Diagnostic SHA: {arm}")
        diagnostic = read(diagnostic_path)
        require(diagnostic["manifest_sha256"] == manifest["manifest_sha256"], f"Diagnostic manifest: {arm}")
        require(diagnostic["cold_warm_matches"] and diagnostic["counterfactual_matches"], f"Cold/warm/counterfactual: {arm}")
        require(len(payload["fixed_samples"]) == 12, f"Fixed sample coverage: {arm}")
        fixed[arm] = {(s["seed"], s["turn"]): s for s in payload["fixed_samples"]}
        recovery = fixed[arm][(133, 38)]["receipt"]["selected_root_only_recovery"]
        require(recovery is not None and recovery["source"] == "root_only_recovery_trajectory", f"Missing recovery evidence: {arm}")
        require(len(recovery["steps"]) == 1 and recovery["steps"][0]["action"] == 7 and recovery["steps"][0]["game_over"], f"Invalid recovery evidence: {arm}")
        preflights[arm] = {"passed": True, "fixed_count": 12, "private_seeds": 30, "cold_warm_matches": True,
                           "preflight_sha256": checksum(path), "diagnostic_sha256": checksum(diagnostic_path)}
        declarations[arm] = manifest["manifest_sha256"]
    rules = []
    for case, old_action, new_action, applied in (
        ((130, 7), 2, 18, {"only240", "both"}),
        ((147, 7), 15, 0, {"only240", "both"}),
        ((126, 22), 21, 6, {"only242", "both"}),
    ):
        actions = {arm: cases[case]["receipt"]["ranked_root_actions"][0] for arm, cases in fixed.items()}
        require(all(action == (new_action if arm in applied else old_action) for arm, action in actions.items()), f"Combined rule exercise: {case}")
        rules.append({"seed_turn_zero_based": case, "actions": actions, "passed": True})
    for case, reference in fixed["none"].items():
        require(all(cases[case]["counters"] == reference["counters"] for cases in fixed.values()), f"Fixed-input search workload differs: {case}")
    result = {"schema": "puyo236.parent_preparation_audit.v1", "passed": True,
              "preparation": str(args.preparation), "arm_manifest_identities": declarations,
              "reproduction_count": len(reproduction), "decisions_independently_audited": sum(r["decisions_audited"] for r in reproduction),
              "reproduction": reproduction, "preflight": preflights, "combined_rule_exercise": rules,
              "limits": "This verifies preparation only. No 240-run result, new human QA, adoption or CI completion is claimed."}
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"passed": True, "output": str(args.output), "reproduction_count": len(reproduction), "preflight_arms": 4}))


if __name__ == "__main__":
    main()
