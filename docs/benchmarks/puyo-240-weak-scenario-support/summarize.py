"""試行rawの厳密再検証と比較。局所試行を品質全体PASSにはしない。"""

import json
from pathlib import Path

import trial
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation

ROOT = Path(__file__).parent
summary = {"ticket": "PUYO-240", "conditions": {}}
all_runs = {}
for condition in ("baseline", "candidate2"):
    root = ROOT / condition
    manifest = baseline._read_json(root / "experiment_manifest.json")
    assert manifest["manifest_sha256"] == ablation.digest({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    runs = ablation.load_runs(root, manifest, contract=trial.CONTRACT)
    assert len(runs) == 16
    all_runs[condition] = runs
    unique = [r for r in runs if r["repeat"] == 1]
    repeat_results = []
    for seed in trial.SEEDS:
        left, right = [r for r in runs if r["seed"] == seed]
        repeat_results.append({"seed": seed, **{key: left[key] == right[key] for key in
                              ("action_digest", "plan_digest", "trajectory_digest")}})
    fixed = [baseline._read_json(path) for path in sorted(root.glob("fixed-*.json"))]
    assert len(fixed) == 3
    def stats(values):
        return {"count": len(values), "p50": baseline.percentile(values, .5),
                "p95": baseline.percentile(values, .95), "mean": sum(values) / len(values)}
    fixed_groups = {}
    for seed in (123, 135):
        for label in ("cold", "warm"):
            fixed_groups[f"initial-{seed}-{label}"] = stats([
                s["elapsed_seconds"] for f in fixed for s in f["samples"]
                if s["seed"] == seed and s["label"] == label
            ])
    for turn in (10, 11):
        fixed_groups[f"seed135-turn{turn + 1}-backend-plus-materialization"] = stats([
            s["elapsed_seconds"] for f in fixed for s in f["saved_request_samples"] if s["turn"] == turn
        ])
    summary["conditions"][condition] = {
        "evaluated_commit": manifest["build_provenance"]["evaluated_commit"],
        "manifest_sha256": manifest["manifest_sha256"],
        "run_count": len(runs), "fully_evaluated": sum(r["fully_evaluated"] for r in runs),
        "decision_count": sum(r["completed_turns"] for r in runs),
        "unique_seed_count": len(unique),
        "target10_seed_count": sum(r["maximum_actual_fire_chain_count"] >= 10 for r in unique),
        "mean_maximum_actual_chain": sum(r["maximum_actual_fire_chain_count"] for r in unique) / len(unique),
        "premature_fires_both_repeats": sum(r["premature_fire_count"] for r in runs),
        "game_overs_both_repeats": sum(r["game_over"] for r in runs),
        "no_fire_unique_seeds": sum(not r["actual_fire_chain_counts"] for r in unique),
        "per_seed": [{"seed": r["seed"], "maximum_chain": r["maximum_actual_fire_chain_count"],
                      "premature": r["premature_fire_count"], "game_over": r["game_over"],
                      "termination": r["termination_reason"],
                      "trajectory_seconds_repeats": [x["elapsed_seconds"] for x in runs if x["seed"] == r["seed"]],
                      "expanded_nodes_repeat1": sum(x["search"]["counters"]["expanded_nodes"] for x in r["records"] if "action" in x),
                      "evaluated_nodes_repeat1": sum(x["search"]["counters"]["evaluated_nodes"] for x in r["records"] if "action" in x),
                      "guard_turns_1based": [x["turn"] + 1 for x in r["records"] if x.get("selection", {}).get("selected_score", {}).get("evidence", {}).get("ranking_rule_version", "").endswith(".v3")]} for r in unique],
        "parity_mismatches": sum(r["simulator_parity_mismatch_count"] for r in runs),
        "fallbacks": sum(r["fallback_count"] for r in runs),
        "repeat_determinism": repeat_results,
        "latency": baseline._aggregate_latency(runs),
        "search": baseline._aggregate_search(runs),
        "trajectory_seconds": stats([r["elapsed_seconds"] for r in runs]),
        "peak_rss_kib": max(r["process_resources"]["peak_rss_kib"] for r in runs),
        "fixed": fixed_groups,
        "private_boundary_passed": all(f["future_boundary"]["passed"] for f in fixed),
        "saved_request_guard_applied_count": sum(s["guard_applied"] for f in fixed for s in f["saved_request_samples"]),
    }
    guard_fixed = [baseline._read_json(path) for path in sorted(root.glob("guard-fixed-*.json*"))]
    assert len(guard_fixed) == 6
    guard_samples = [s for f in guard_fixed for s in f["samples"]]
    assert len({s["digest"] for s in guard_samples}) == 1
    assert all(s["guard_applied"] == (condition == "candidate2") for s in guard_samples)
    summary["conditions"][condition]["active_guard_fixed"] = {
        "elapsed_seconds": stats([s["elapsed_seconds"] for s in guard_samples]),
        "materialization_seconds": stats([s["materialization_seconds"] for s in guard_samples]),
        "per_repeat_seconds": [stats([s["elapsed_seconds"] for s in f["samples"]])["mean"] for f in guard_fixed],
        "action": guard_samples[0]["action"], "counters": guard_samples[0]["counters"],
    }
    assert all(r["fully_evaluated"] for r in runs)
    assert all(all(item[k] for k in ("action_digest", "plan_digest", "trajectory_digest")) for item in repeat_results)
    assert all(r["simulator_parity_mismatch_count"] == r["fallback_count"] == 0 for r in runs)
    assert all(record["scenario_accounting"]["failure_count"] == 0
               for run in runs for record in run["records"] if "action" in record)
    assert summary["conditions"][condition]["private_boundary_passed"]

left, right = (summary["conditions"][c] for c in ("baseline", "candidate2"))
summary["comparison"] = {
    "mean_maximum_actual_chain_delta": right["mean_maximum_actual_chain"] - left["mean_maximum_actual_chain"],
    "target10_seed_count_delta": right["target10_seed_count"] - left["target10_seed_count"],
    "decision_p95_ratio": right["latency"]["p95_seconds"] / left["latency"]["p95_seconds"],
    "trajectory_mean_seconds_ratio": right["trajectory_seconds"]["mean"] / left["trajectory_seconds"]["mean"],
    "fixed_mean_ratios": {k: right["fixed"][k]["mean"] / left["fixed"][k]["mean"] for k in left["fixed"]},
    "active_guard_fixed_mean_ratio": right["active_guard_fixed"]["elapsed_seconds"]["mean"] / left["active_guard_fixed"]["elapsed_seconds"]["mean"],
    "active_guard_fixed_node_counters_equal": left["active_guard_fixed"]["counters"] == right["active_guard_fixed"]["counters"],
    "per_seed": [
        {"seed": old["seed"], "maximum_chain_delta": new["maximum_chain"] - old["maximum_chain"],
         "trajectory_seconds_ratios": [n / o for o, n in zip(old["trajectory_seconds_repeats"], new["trajectory_seconds_repeats"])],
         "expanded_nodes_delta_repeat1": new["expanded_nodes_repeat1"] - old["expanded_nodes_repeat1"],
         "evaluated_nodes_delta_repeat1": new["evaluated_nodes_repeat1"] - old["evaluated_nodes_repeat1"]}
        for old, new in zip(left["per_seed"], right["per_seed"])
    ],
}
summary["adoption"] = {
    "decision": "No-Go",
    "reason": "seed130/147の品質改善に伴い両repeatで40手時間と実expanded/evaluated nodesが増加",
    "final_puyo236_60_run_started": False,
    "default_adopted": False,
    "threshold3_closed_loop_evaluated": False,
}
source_files = sorted({path for condition in ("baseline", "candidate2")
                       for path in (ROOT / condition).rglob("*") if path.is_file()})
summary["source_sha256"] = {str(p.relative_to(ROOT)): baseline.file_sha256(p) for p in source_files}
baseline._write_json(ROOT / "comparison.json", summary)
print(json.dumps({k: v for k, v in summary.items() if k not in ("conditions", "source_sha256")}, ensure_ascii=False))
