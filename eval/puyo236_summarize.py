"""Revalidate all 240 runs and report separate quality, timing and safety facts."""

from __future__ import annotations

import argparse
import itertools
import statistics
from collections import Counter
from pathlib import Path

if __package__:
    from . import puyo236_four_pattern_trial as trial
    from .puyo236_run_comparison import schedule, verify_saved
else:
    import puyo236_four_pattern_trial as trial
    from puyo236_run_comparison import schedule, verify_saved

from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation


def stats(values):
    return {"count": len(values), "mean": statistics.mean(values),
            "median": statistics.median(values), "p50": baseline.percentile(values, .5),
            "p95": baseline.percentile(values, .95), "min": min(values), "max": max(values),
            "total": sum(values)}


def validate_digests(run):
    records = run["records"]
    for record in records:
        assert record["decision_digest"] == baseline._stable_digest({
            "turn": record["turn"], "action": record["action"], "board_before": record["board_before"],
            "board_after": record["board_after"], "actual_result": record["actual_result"],
            "plan": record["plan"], "search_digest": record["search"]["deterministic_digest"],
            "fallback": record["fallback"],
        }, prefix="deep-chain-decision")
    plans = [{"turn": r["turn"], "plan_id": r["plan"]["plan_id"],
              "actions": [s["action"] for s in r["plan"]["steps"]],
              "state_fingerprints": [s["state_fingerprint"] for s in r["plan"]["steps"]]} for r in records]
    for name, data, prefix in (
        ("action_digest", [r["action"] for r in records], "deep-chain-run-actions"),
        ("plan_digest", plans, "deep-chain-run-plans"),
        ("trajectory_digest", [r["decision_digest"] for r in records], "deep-chain-run-trajectory"),
    ):
        assert run[name] == baseline._stable_digest(data, prefix=prefix), name


def quality(runs):
    chains = [r["maximum_actual_fire_chain_count"] for r in runs]
    reached = sum(c >= 10 for c in chains)
    clean = sum(r["maximum_actual_fire_chain_count"] >= 10 and r["premature_fire_count"] == 0
                and not r["game_over"] for r in runs)
    return {
        "unique_seed_count": len(runs), "target10_seed_count": reached,
        "target10_rate_percent": 100 * reached / len(runs),
        "clean_target10_seed_count": clean, "clean_target10_rate_percent": 100 * clean / len(runs),
        "maximum_actual_chain": stats(chains),
        "maximum_actual_chain_distribution": dict(sorted(Counter(chains).items())),
        "premature_fires_repeat1": sum(r["premature_fire_count"] for r in runs),
        "premature_seed_count": sum(r["premature_fire_count"] > 0 for r in runs),
        "game_over_seed_count": sum(r["game_over"] for r in runs),
        "no_fire_seed_count": sum(not r["actual_fire_chain_counts"] for r in runs),
    }


def per_seed(runs):
    a, b = runs
    return {
        "seed": a["seed"], "cohort": "prior_selected_8" if a["seed"] in trial.OLD_SEEDS else "additional_22",
        "maximum_chain": a["maximum_actual_fire_chain_count"], "premature": a["premature_fire_count"],
        "game_over": a["game_over"], "no_fire": not a["actual_fire_chain_counts"],
        "turns": a["completed_turns"],
        "actual_fires": [{"placement": r["turn"] + 1, "chain": r["actual_result"]["chain_count"]}
                         for r in a["records"] if r["actual_result"]["chain_count"]],
        "run_seconds_repeats": [r["elapsed_seconds"] for r in runs],
        "run_seconds_minus_receipt_audit_reference_repeats": [r["elapsed_seconds"] - sum(r["request_audit_seconds"]) for r in runs],
        "expanded_nodes_repeat1": sum(r["search"]["counters"]["expanded_nodes"] for r in a["records"]),
        "evaluated_nodes_repeat1": sum(r["search"]["counters"]["evaluated_nodes"] for r in a["records"]),
        "repeat_digests_match": all(a[k] == b[k] for k in
                                    ("action_digest", "plan_digest", "trajectory_digest", "request_receipts")),
    }


def comparison(left, right):
    ql, qr = left["quality"], right["quality"]
    pairs = []
    for a, b in zip(left["per_seed"], right["per_seed"], strict=True):
        assert a["seed"] == b["seed"]
        chain_delta = b["maximum_chain"] - a["maximum_chain"]
        pairs.append({
            "seed": a["seed"], "cohort": a["cohort"], "maximum_chain_before": a["maximum_chain"],
            "maximum_chain_after": b["maximum_chain"], "maximum_chain_delta": chain_delta,
            "target_rescue": a["maximum_chain"] < 10 <= b["maximum_chain"],
            "target_regression": b["maximum_chain"] < 10 <= a["maximum_chain"],
            "premature_delta": b["premature"] - a["premature"],
            "game_over_delta": int(b["game_over"]) - int(a["game_over"]),
            "run_seconds_delta_by_repeat": [n - o for o, n in zip(a["run_seconds_repeats"], b["run_seconds_repeats"], strict=True)],
            "run_seconds_ratio_by_repeat": [n / o for o, n in zip(a["run_seconds_repeats"], b["run_seconds_repeats"], strict=True)],
            "expanded_nodes_delta_repeat1": b["expanded_nodes_repeat1"] - a["expanded_nodes_repeat1"],
            "evaluated_nodes_delta_repeat1": b["evaluated_nodes_repeat1"] - a["evaluated_nodes_repeat1"],
        })
    return {
        "target10_percentage_point_delta": qr["target10_rate_percent"] - ql["target10_rate_percent"],
        "clean_target10_percentage_point_delta": qr["clean_target10_rate_percent"] - ql["clean_target10_rate_percent"],
        "mean_maximum_actual_chain_delta": qr["maximum_actual_chain"]["mean"] - ql["maximum_actual_chain"]["mean"],
        "mean_maximum_actual_chain_relative_percent": 100 * (qr["maximum_actual_chain"]["mean"] / ql["maximum_actual_chain"]["mean"] - 1),
        "run_mean_seconds_delta": right["timing"]["run_seconds"]["mean"] - left["timing"]["run_seconds"]["mean"],
        "run_mean_relative_percent": 100 * (right["timing"]["run_seconds"]["mean"] / left["timing"]["run_seconds"]["mean"] - 1),
        "run_minus_receipt_audit_mean_relative_percent": 100 * (right["timing"]["run_minus_receipt_audit_reference_seconds"]["mean"] /
                                                               left["timing"]["run_minus_receipt_audit_reference_seconds"]["mean"] - 1),
        "rescued_seeds": [p["seed"] for p in pairs if p["target_rescue"]],
        "regressed_seeds": [p["seed"] for p in pairs if p["target_regression"]],
        "any_quality_regression_seeds": [p["seed"] for p in pairs if p["maximum_chain_delta"] < 0
                                         or p["premature_delta"] > 0 or p["game_over_delta"] > 0],
        "paired_seed_chain_delta": stats([p["maximum_chain_delta"] for p in pairs]),
        "paired_seed_mean_run_time_delta": stats([statistics.mean(p["run_seconds_delta_by_repeat"]) for p in pairs]),
        "per_seed": pairs,
    }


def summarize(root, preparation):
    declared = baseline._read_json(root / "four-arm-manifest.json")
    assert declared["schedule"] == schedule()
    result = {"ticket": "PUYO-236", "conditions": {}, "adoption": "pending_user_choice",
              "new_human_gui_qa": "pending", "unique_seeds": 30, "repeats_are_independent_seeds": False,
              "timing_method": "Raw observed timers include separately measured receipt construction; subtraction is a reference estimate, not a new product timing measurement.",
              "configuration_sha256": trial.CONFIG_SHA, "input_sha256": {}}
    for arm in trial.ARMS:
        manifest = ablation.load_manifest(root / arm, contract=trial.CONTRACT)
        assert manifest["manifest_sha256"] == declared["arm_manifest_sha256"][arm]
        runs = ablation.load_runs(root / arm, manifest, contract=trial.CONTRACT)
        assert len(runs) == 60 and all(r["fully_evaluated"] for r in runs)
        for entry in [e for e in schedule() if e["arm"] == arm]:
            identity = next(i for i in manifest["identities"] if (i["seed"], i["repeat"]) == (entry["seed"], entry["repeat"]))
            path = ablation.run_path(root / arm, identity)
            run = verify_saved(path, entry, manifest)
            validate_digests(run)
            process_path = root / "processes" / f"{entry['ordinal']:03d}-{arm}-seed-{entry['seed']}-repeat-{entry['repeat']}.json"
            process = baseline._read_json(process_path)
            assert process["returncode"] == 0 and process["schedule_entry"] == entry
            assert process["raw_sha256"] == baseline.file_sha256(path)
            result["input_sha256"][str(path.relative_to(root))] = baseline.file_sha256(path)
            result["input_sha256"][str(process_path.relative_to(root))] = baseline.file_sha256(process_path)
        unique = [r for r in runs if r["repeat"] == 1]
        seed_rows = [per_seed([r for r in runs if r["seed"] == seed]) for seed in trial.SEEDS]
        assert all(r["repeat_digests_match"] for r in seed_rows)
        preflight_path = preparation / arm / "preflight.json.gz"
        preflight = baseline._read_json(preflight_path)
        prepared = ablation.load_manifest(preparation / arm, contract=trial.CONTRACT)
        assert prepared["build_provenance"]["evaluated_commit"] == manifest["build_provenance"]["evaluated_commit"]
        assert prepared["runtime"] == manifest["runtime"] and prepared["runner"] == manifest["runner"]
        assert preflight["manifest_sha256"] == prepared["manifest_sha256"]
        assert preflight["future_isolation"]["passed"]
        assert [r["seed"] for r in preflight["private_counterfactuals"]] == list(trial.SEEDS)
        assert all(r["counterfactual_matches"] for r in preflight["private_counterfactuals"])
        records = [r for run in runs for r in run["records"]]
        audit = [elapsed for run in runs for elapsed in run["request_audit_seconds"]]
        decisions = [r["elapsed_seconds"] for r in records]
        q = quality(unique)
        result["conditions"][arm] = {
            "evaluated_commit": manifest["build_provenance"]["evaluated_commit"],
            "wheel_sha256": manifest["build_provenance"]["wheels"][0]["sha256"],
            "run_count": 60, "fully_evaluated": 60, "decision_count": len(records),
            "quality": q, "prior_selected_8": quality([r for r in unique if r["seed"] in trial.OLD_SEEDS]),
            "additional_22": quality([r for r in unique if r["seed"] not in trial.OLD_SEEDS]),
            "premature_fires_both_repeats": sum(r["premature_fire_count"] for r in runs),
            "game_overs_both_repeats": sum(r["game_over"] for r in runs),
            "per_seed": seed_rows, "search": baseline._aggregate_search(runs),
            "timing": {"decision_seconds": stats(decisions), "receipt_audit_seconds": stats(audit),
                       "decision_minus_receipt_audit_reference_seconds": stats([d - a for d, a in zip(decisions, audit, strict=True)]),
                       "run_seconds": stats([r["elapsed_seconds"] for r in runs]),
                       "run_minus_receipt_audit_reference_seconds": stats([r["elapsed_seconds"] - sum(r["request_audit_seconds"]) for r in runs])},
            "rss_kib": stats([r["process_resources"]["peak_rss_kib"] for r in runs]),
            "preflight_sha256": baseline.file_sha256(preflight_path),
            "absolute_gates": {
                "mean_maximum_actual_chain_at_least10": "PASS" if q["maximum_actual_chain"]["mean"] >= 10 else "FAIL",
                "premature_zero": "PASS" if q["premature_fires_repeat1"] == 0 else "FAIL",
                "game_over_zero": "PASS" if q["game_over_seed_count"] == 0 else "FAIL",
                "decision_p95_at_most_one_second_observed": "PASS" if baseline.percentile(decisions, .95) <= 1 else "FAIL",
                "parity_private_fallback_accounting_candidate_plan": "PASS",
                "new_human_gui_qa": "pending", "adoption": "pending_user_choice",
            },
        }
    result["comparisons"] = {f"{a}_to_{b}": comparison(result["conditions"][a], result["conditions"][b])
                             for a, b in itertools.combinations(trial.ARMS, 2)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("preparation", type=Path)
    args = parser.parse_args()
    baseline._write_json(args.root / "comparison.json", summarize(args.root, args.preparation))


if __name__ == "__main__":
    main()
