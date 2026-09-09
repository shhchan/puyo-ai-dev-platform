"""全root証跡から弱支持/継続の競合と不成立理由を再計算する。"""

from pathlib import Path
from eval import deep_chain_builder_benchmark as b

ROOT = Path(__file__).parent
SEEDS = (123, 126, 130, 133, 135, 138, 147, 151)


def failures(root, *, require_best_depth):
    result = []
    for v in root["scenario_values"]:
        reasons = []
        if not v["evaluated"]:
            reasons.append("unevaluated")
        if not v["search_complete"]:
            reasons.append("incomplete")
        if v["selected_fire_class"] != "quiet_continuation" or not v["quiet_survivor"]:
            reasons.append("not_quiet")
        if v["survivor_evaluator_score"] is None or v["survivor_evaluator_score"] <= -1e12:
            reasons.append("fatal_or_missing_score")
        if v["reached_depth"] != 16 or v["survivor_coverage"]["retained_counts"].get("16", 0) == 0:
            reasons.append("no_full_depth_survivor")
        if require_best_depth and v["survivor_evaluator_depth"] != 16:
            reasons.append("score_from_previous_depth")
        if reasons:
            result.append({"scenario_id": v["scenario_id"], "reasons": reasons})
    return result


payload = {"ticket": "PUYO-240", "baseline_seeds": [], "seed135_detailed_comparisons": []}
for seed in SEEDS:
    path = ROOT / f"seed-{seed}-all-roots.json.gz"
    source = b._read_json(path)
    counts = []
    for case in source["cases"]:
        top = case["roots"][0]
        quiet = [r for r in case["roots"] if r["fire_class"] == "quiet_continuation"]
        counts.append({"turn": case["turn"], "selected_action": top["root_action"],
                       "selected_class": top["fire_class"],
                       "target_support": top["fire_class_support"]["target_fire"],
                       "quiet_roots": len(quiet),
                       "necessary_conditions_only_roots": [r["root_action"] for r in quiet
                                                            if not failures(r, require_best_depth=False)]})
    payload["baseline_seeds"].append({"seed": seed, "source_sha256": b.file_sha256(path),
                                      "decisions": len(counts), "turns": counts})

source = b._read_json(ROOT / "candidate2-seed-135-all-roots.json.gz")
rerank = []
for case in source["cases"]:
    roots = case["roots"]
    eligible = {r["root_action"] for r in roots if r["fire_class"] == "quiet_continuation"
                and not failures(r, require_best_depth=True)}
    for threshold in (2, 3):
        weak = {r["root_action"] for r in roots if r["fire_class"] == "target_fire"
                and r["fire_class_support"]["target_fire"] < threshold}
        def key(root):
            values = [float(v) if isinstance(v, str) and v in ("-inf", "inf") else v
                      for v in root["ranking_key"]]
            if weak and eligible:
                if root["root_action"] in weak:
                    values[0] = 2.25
                elif root["root_action"] in eligible:
                    values[0] = 2.5
            return tuple(values)
        ordered = sorted(roots, key=key, reverse=True)
        rerank.append({"turn": case["turn"], "threshold": threshold,
                       "eligible_quiet_roots": sorted(eligible), "weak_target_roots": sorted(weak),
                       "old_action": roots[0]["root_action"], "reranked_action": ordered[0]["root_action"],
                       "all_root_order_changed": [r["root_action"] for r in roots] != [r["root_action"] for r in ordered]})
    if case["turn"] in (9, 10, 11, 17, 23, 34, 35, 36, 37, 38, 39):
        payload["seed135_detailed_comparisons"].append({"turn": case["turn"], "roots": [
            {"action": r["root_action"], "class": r["fire_class"],
             "support": r["fire_class_support"], "ranking_key": r["ranking_key"],
             "candidate_value": r["candidate_value"],
             "survivor_scores": [v["survivor_evaluator_score"] for v in r["scenario_values"]],
             "best_survivor_depths": [v["survivor_evaluator_depth"] for v in r["scenario_values"]],
             "reached_depths": [v["reached_depth"] for v in r["scenario_values"]],
             "continuation_rejections": failures(r, require_best_depth=True)} for r in roots]})
payload["seed135_offline_diagnosis_not_closed_loop_quality"] = rerank
payload["seed135_candidate_action_equal"] = source["trajectory"]["action_digest"] == b._read_json(ROOT / "seed-135-all-roots.json.gz")["trajectory"]["action_digest"]
payload["improved_seed_first_divergence"] = []
for seed in (130, 147):
    old_path = ROOT / f"seed-{seed}-all-roots.json.gz"
    new_path = ROOT / f"candidate2-seed-{seed}-all-roots.json.gz"
    old, new = (b._read_json(path) for path in (old_path, new_path))
    before, after = next((a, c) for a, c in zip(old["cases"], new["cases"])
                         if a["native_selected_action"] != c["native_selected_action"])
    assert before["request_hex"] == after["request_hex"]
    assert before["counters"] == after["counters"]
    before_by_action = {r["root_action"]: r for r in before["roots"]}
    for root in after["roots"]:
        previous = before_by_action[root["root_action"]]
        for field in ("fire_class", "fire_class_support", "chain_count", "chain_score",
                      "terminal_score", "candidate_value", "best_fire"):
            assert root[field] == previous[field], (seed, root["root_action"], field)
    assert not failures(after["roots"][0], require_best_depth=True)
    payload["improved_seed_first_divergence"].append({
        "seed": seed, "turn": after["turn"], "request_bytes_equal": True,
        "node_counters_equal": True, "all_root_aggregate_values_equal": True,
        "before_action": before["native_selected_action"],
        "after_action": after["native_selected_action"],
        "before_ranking": before["native_ranking"], "after_ranking": after["native_ranking"],
        "roots": after["roots"],
        "source_sha256": {str(p.name): b.file_sha256(p) for p in (old_path, new_path)},
    })
b._write_json(ROOT / "all-root-diagnosis.json", payload)
print({"baseline_decisions": sum(s["decisions"] for s in payload["baseline_seeds"]),
       "seed135_action_changes_by_threshold": {t: sum(x["old_action"] != x["reranked_action"] for x in rerank if x["threshold"] == t) for t in (2, 3)}})
