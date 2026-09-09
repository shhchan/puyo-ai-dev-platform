"""Strictly revalidate the 32-run local trial and summarize separate gates."""

import argparse
import json
from pathlib import Path

import puyo241_risk_weight_trial as trial
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation


def stats(values):
    return {"count": len(values), "p50": baseline.percentile(values, .5),
            "p95": baseline.percentile(values, .95), "mean": sum(values) / len(values)}


def summarize(root):
    summary = {"ticket": "PUYO-241", "conditions": {}}
    all_runs = {}
    for condition in trial.CONDITIONS:
        manifest = ablation.load_manifest(root / condition, contract=trial.CONTRACT)
        runs = ablation.load_runs(root / condition, manifest, contract=trial.CONTRACT)
        assert len(runs) == 16
        all_runs[condition] = runs
        for run in runs:
            trial.validate_evaluator(run, manifest)
            assert run["fully_evaluated"]
            assert run["simulator_parity_mismatch_count"] == run["fallback_count"] == 0
        unique = [r for r in runs if r["repeat"] == 1]
        repeat_results = []
        for seed in trial.SEEDS:
            a, b = [r for r in runs if r["seed"] == seed]
            result = {k: a[k] == b[k] for k in ("action_digest", "plan_digest", "trajectory_digest", "request_receipts")}
            assert all(result.values()), (condition, seed, result)
            repeat_results.append({"seed": seed, **result})
        fixed = [baseline._read_json(path) for path in sorted((root / condition).glob("fixed-*.json"))]
        assert len(fixed) == 3
        fixed_groups = {}
        for seed, turn in trial.FIXED_BOARDS:
            samples = [s for f in fixed for s in f["samples"] if (s["seed"], s["turn"]) == (seed, turn)]
            assert len(samples) == 6
            assert len({s["native_digest"] for s in samples}) == len({s["python_digest"] for s in samples}) == 1
            assert all(s["strict_all_root_parity_passed"] for s in samples)
            assert all(s["evaluator_config_sha256"] == manifest["effective_evaluator_config_sha256"] for s in samples)
            assert all(s["counters"] == samples[0]["counters"] for s in samples)
            fixed_groups[f"seed{seed}-turn{turn + 1}"] = {
                "elapsed_seconds": stats([s["elapsed_seconds"] for s in samples]),
                "materialization_seconds": stats([s["materialization_seconds"] for s in samples]),
                "native_search_seconds": stats([s["native_telemetry"]["search_ns"] / 1e9 for s in samples]),
                "per_process_mean_seconds": [stats([s["elapsed_seconds"] for s in f["samples"] if (s["seed"], s["turn"]) == (seed, turn)])["mean"] for f in fixed],
                "counters": samples[0]["counters"], "native_ranking": samples[0]["native_ranking"],
            }
        for f in fixed:
            assert f["manifest_sha256"] == manifest["manifest_sha256"]
            assert f["inputs_sha256"] == baseline.file_sha256(root / "fixed-inputs.json")
            assert f["future_boundary"]["passed"]
            for seed in (123, 135):
                samples = [s for s in f["private_samples"] if s["seed"] == seed]
                assert len(samples) == 3
                assert samples[0]["semantic"] == samples[1]["semantic"] == samples[2]["semantic"]
                assert all(not s["fallback"]["used"] and s["scenario_accounting"]["failure_count"] == 0 for s in samples)
                assert all(s["backend"]["configuration"]["evaluator_config_sha256"] == manifest["effective_evaluator_config_sha256"] for s in samples)
        summary["conditions"][condition] = {
            "evaluated_commit": manifest["build_provenance"]["evaluated_commit"],
            "runner": manifest["runner"], "manifest_sha256": manifest["manifest_sha256"],
            "effective_evaluator_config_sha256": manifest["effective_evaluator_config_sha256"],
            "run_count": len(runs), "fully_evaluated": sum(r["fully_evaluated"] for r in runs),
            "decision_count": sum(r["completed_turns"] for r in runs),
            "unique_seed_count": len(unique),
            "target10_seed_count": sum(r["maximum_actual_fire_chain_count"] >= 10 for r in unique),
            "mean_maximum_actual_chain": sum(r["maximum_actual_fire_chain_count"] for r in unique) / len(unique),
            "premature_fires_both_repeats": sum(r["premature_fire_count"] for r in runs),
            "game_overs_both_repeats": sum(r["game_over"] for r in runs),
            "no_fire_unique_seeds": sum(not r["actual_fire_chain_counts"] for r in unique),
            "per_seed": [{
                "seed": r["seed"], "maximum_chain": r["maximum_actual_fire_chain_count"],
                "premature": r["premature_fire_count"], "game_over": r["game_over"],
                "termination": r["termination_reason"], "completed_turns": r["completed_turns"],
                "actual_fires": [{"turn_1based": x["turn"] + 1, "chain_count": x["actual_result"]["chain_count"]}
                                 for x in r["records"] if "action" in x and x["actual_result"]["chain_count"] > 0],
                "trajectory_seconds_repeats": [x["elapsed_seconds"] for x in runs if x["seed"] == r["seed"]],
                "expanded_nodes_repeat1": sum(x["search"]["counters"]["expanded_nodes"] for x in r["records"] if "action" in x),
                "evaluated_nodes_repeat1": sum(x["search"]["counters"]["evaluated_nodes"] for x in r["records"] if "action" in x),
                "decision_p95_repeats": [baseline.percentile([d["elapsed_seconds"] for d in x["records"] if "action" in d], .95) for x in runs if x["seed"] == r["seed"]],
            } for r in unique],
            "parity_mismatches": sum(r["simulator_parity_mismatch_count"] for r in runs),
            "fallbacks": sum(r["fallback_count"] for r in runs),
            "selected_forced_safety_decisions": sum(
                record.get("selection", {}).get("selected_score", {}).get("evidence", {}).get("fire_class") == "forced_safety_fire"
                for run in runs for record in run["records"]),
            "repeat_determinism": repeat_results,
            "latency": baseline._aggregate_latency(runs), "search": baseline._aggregate_search(runs),
            "trajectory_seconds": stats([r["elapsed_seconds"] for r in runs]),
            "peak_rss_kib": max(r["process_resources"]["peak_rss_kib"] for r in runs),
            "fixed": fixed_groups, "private_boundary_passed": True,
            "initial_fixed": {f"seed{seed}-{label}": stats([s["elapsed_seconds"] for f in fixed for s in f["private_samples"] if s["seed"] == seed and s["label"] == label]) for seed in (123, 135) for label in ("cold", "warm")},
        }
    left, right = (summary["conditions"][c] for c in trial.CONDITIONS)
    summary["comparison"] = {
        "mean_maximum_actual_chain_delta": right["mean_maximum_actual_chain"] - left["mean_maximum_actual_chain"],
        "target10_seed_count_delta": right["target10_seed_count"] - left["target10_seed_count"],
        "decision_p95_ratio": right["latency"]["p95_seconds"] / left["latency"]["p95_seconds"],
        "trajectory_mean_seconds_ratio": right["trajectory_seconds"]["mean"] / left["trajectory_seconds"]["mean"],
        "fixed_mean_ratios": {k: right["fixed"][k]["elapsed_seconds"]["mean"] / left["fixed"][k]["elapsed_seconds"]["mean"] for k in left["fixed"]},
        "per_seed": [{
            "seed": a["seed"], "maximum_chain_delta": b["maximum_chain"] - a["maximum_chain"],
            "premature_delta": b["premature"] - a["premature"],
            "game_over_delta": int(b["game_over"]) - int(a["game_over"]),
            "trajectory_seconds_ratios": [n / o for o, n in zip(a["trajectory_seconds_repeats"], b["trajectory_seconds_repeats"], strict=True)],
            "expanded_nodes_delta_repeat1": b["expanded_nodes_repeat1"] - a["expanded_nodes_repeat1"],
            "evaluated_nodes_delta_repeat1": b["evaluated_nodes_repeat1"] - a["evaluated_nodes_repeat1"],
            "first_action_divergence_turn_1based": next((old["turn"] + 1 for old, new in zip(all_runs["baseline"][2 * i]["records"], all_runs["danger2"][2 * i]["records"]) if old.get("action") != new.get("action")), None),
        } for i, (a, b) in enumerate(zip(left["per_seed"], right["per_seed"], strict=True))],
    }
    stable_compute_regressions = [c["seed"] for c in summary["comparison"]["per_seed"]
                                  if all(ratio > 1 for ratio in c["trajectory_seconds_ratios"])
                                  and c["expanded_nodes_delta_repeat1"] > 0
                                  and c["evaluated_nodes_delta_repeat1"] > 0]
    previous_success_regressions = [a["seed"] for a, b in zip(left["per_seed"], right["per_seed"], strict=True)
                                    if a["maximum_chain"] >= 10 and (
                                        b["maximum_chain"] < 10 or b["premature"] > a["premature"]
                                        or int(b["game_over"]) > int(a["game_over"]))]
    summary["adoption"] = {
        "decision": "No-Go" if stable_compute_regressions or previous_success_regressions else "pending review",
        "reasons": (["Repeated trajectory time increase accompanied by more actual expanded/evaluated nodes"] if stable_compute_regressions else [])
                   + (["Previously successful target10 seeds now have additional premature fire or other quality regression"] if previous_success_regressions else []),
        "no_automatic_go": "Manual review is required even when neither rejection is detected",
        "repeat_stable_compute_regression_seeds": stable_compute_regressions,
        "previous_target10_success_quality_regression_seeds": previous_success_regressions,
        "default_adopted": False, "final_puyo236_60_run_started": False,
        "additional_conditions_evaluated": False,
    }
    summary["trajectory_windows"] = []
    for seed in trial.SEEDS:
        candidate = next(r for r in all_runs["danger2"] if r["seed"] == seed and r["repeat"] == 1)
        first_fire = next((r["turn"] for r in candidate["records"] if "action" in r and r["actual_result"]["chain_count"] > 0), None)
        if first_fire is None:
            continue
        for condition in trial.CONDITIONS:
            for run in (r for r in all_runs[condition] if r["seed"] == seed):
                windows = {}
                for label, select in (("through_candidate_first_fire", lambda t: t <= first_fire),
                                      ("after_candidate_first_fire", lambda t: t > first_fire)):
                    records = [r for r in run["records"] if "action" in r and select(r["turn"])]
                    windows[label] = {
                        "decision_count": len(records),
                        "decision_seconds": sum(r["elapsed_seconds"] for r in records),
                        "expanded_nodes": sum(r["search"]["counters"]["expanded_nodes"] for r in records),
                        "evaluated_nodes": sum(r["search"]["counters"]["evaluated_nodes"] for r in records),
                    }
                summary["trajectory_windows"].append({"seed": seed, "condition": condition,
                                                       "repeat": run["repeat"], "split_turn_1based": first_fire + 1,
                                                       "windows": windows})
    source_files = [p for condition in trial.CONDITIONS for p in (root / condition).rglob("*") if p.is_file()]
    source_files += [root / "fixed-inputs.json", root / "pretrial-diagnosis.json.gz", root / "python-tests.log"]
    source_files += list(root.glob("final-python-tests.log"))
    summary["source_sha256"] = {str(p.relative_to(root)): baseline.file_sha256(p) for p in sorted(source_files)}
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    summary = summarize(args.root)
    baseline._write_json(args.root / "comparison.json", summary)
    print(json.dumps(summary["comparison"], ensure_ascii=False))
