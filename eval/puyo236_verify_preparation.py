"""Verify old eight-seed reproduction and actual exercise of each frozen rule."""

import argparse
from pathlib import Path

if __package__:
    from . import puyo236_four_pattern_trial as trial
    from .puyo236_summarize import validate_digests
else:
    import puyo236_four_pattern_trial as trial
    from puyo236_summarize import validate_digests

from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation


def verify(preparation, previous):
    sources = {"none": "puyo-240-evidence/baseline", "only240": "puyo-240-evidence/candidate2",
               "only242": "puyo-242-evidence/candidate"}
    checks = []
    keys = ("action_digest", "plan_digest", "trajectory_digest", "final_board_digest",
            "maximum_actual_fire_chain_count", "premature_fire_count", "game_over",
            "actual_fire_chain_counts", "completed_turns", "termination_reason")
    for arm, directory in sources.items():
        manifest = ablation.load_manifest(preparation / arm, contract=trial.CONTRACT)
        for seed in trial.OLD_SEEDS:
            identity = next(i for i in manifest["identities"] if i["seed"] == seed and i["repeat"] == 1)
            current_path = ablation.run_path(preparation / arm, identity)
            previous_path = ablation.run_path(previous / directory, identity)
            current, old = baseline._read_json(current_path), baseline._read_json(previous_path)
            ablation.validate_run(current, identity, manifest, contract=trial.CONTRACT)
            trial.validate_receipts(current, manifest)
            validate_digests(current)
            values = {key: current[key] == old[key] for key in keys}
            values["every_decision_search_counters"] = [r["search"]["counters"] for r in current["records"]] == [r["search"]["counters"] for r in old["records"]]
            checks.append({"arm": arm, "seed": seed, "checks": values,
                           "source": str(previous_path), "source_sha256": baseline.file_sha256(previous_path),
                           "current_sha256": baseline.file_sha256(current_path)})
    preflights = {arm: baseline._read_json(preparation / arm / "preflight.json.gz") for arm in trial.ARMS}
    fixed = {arm: {(s["seed"], s["turn"]): s for s in value["fixed_samples"]} for arm, value in preflights.items()}
    rules = []
    for case, before, guarded in (((130, 7), 2, 18), ((147, 7), 15, 0)):
        actions = {arm: fixed[arm][case]["receipt"]["ranked_root_actions"][0] for arm in trial.ARMS}
        rules.append({"case_seed_turn0": case, "kind": "weak_support_guard", "actions": actions,
                      "passed": actions["none"] == actions["only242"] == before and actions["only240"] == actions["both"] == guarded})
    for case in ((135, 10), (135, 11)):
        roots = fixed["only240"][case]["roots"]
        rules.append({"case_seed_turn0": case, "kind": "historical_best_depth_does_not_prove_quiet_alternative",
                      "passed": all(r.get("weak_support_priority") is None for r in roots)})
    case = (126, 22)
    actions = {arm: fixed[arm][case]["receipt"]["ranked_root_actions"][0] for arm in trial.ARMS}
    rules.append({"case_seed_turn0": case, "kind": "known_prefix_target", "actions": actions,
                  "passed": actions["none"] == actions["only240"] == 21 and actions["only242"] == actions["both"] == 6})
    for case in fixed["none"]:
        reference = fixed["none"][case]["counters"]
        rules.append({"case_seed_turn0": case, "kind": "same_request_search_counters_all_four_arms",
                      "passed": all(fixed[arm][case]["counters"] == reference for arm in trial.ARMS)})
    result = {"ticket": "PUYO-236", "historical_reproduction": checks, "exercised_rules": rules,
              "all_passed": all(all(c["checks"].values()) for c in checks) and all(c["passed"] for c in rules),
              "old_reproduction_timings_are_not_full_measurement_results": True}
    baseline._write_json(preparation / "verification.json", result)
    assert result["all_passed"], "See saved preparation verification failures"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preparation", type=Path)
    parser.add_argument("previous", type=Path)
    args = parser.parse_args()
    verify(args.preparation, args.previous)
