"""Cross-check the frozen summary and sequential process receipts independently."""

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MEASUREMENT = ROOT / "measurement"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def equal(left, right):
    if isinstance(left, (int, float)) and not isinstance(left, bool):
        assert math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12), (left, right)
    else:
        assert left == right, (left, right)


def main():
    independent = read(ROOT / "parent-measurement-raw-audit.json")
    summary = read(MEASUREMENT / "comparison.json")
    declared = read(MEASUREMENT / "four-arm-manifest.json")
    assert independent["passed"] and summary["adoption"] == "pending_user_choice"
    assert summary["new_human_gui_qa"] == "pending"
    checked = {}
    for arm, source in independent["arms"].items():
        target = summary["conditions"][arm]
        equal(source["valid_runs"], target["run_count"])
        equal(source["placements_all_60"], target["decision_count"])
        equal(source["provenance"]["evaluated_commit"], target["evaluated_commit"])
        for source_key, target_key in (("quality_all_30", "quality"),
                                       ("quality_previous_selected_8", "prior_selected_8"),
                                       ("quality_additional_22", "additional_22")):
            a, b = source[source_key], target[target_key]
            for ak, bk in (("unique_seeds", "unique_seed_count"),
                           ("target_success_count", "target10_seed_count"),
                           ("clean_success_count", "clean_target10_seed_count"),
                           ("premature_fires", "premature_fires_repeat1"),
                           ("premature_seed_count", "premature_seed_count"),
                           ("game_over_seeds", "game_over_seed_count"),
                           ("no_fire_seeds", "no_fire_seed_count"),
                           ("chain_distribution", "maximum_actual_chain_distribution")):
                equal(a[ak], b[bk])
            for key in ("count", "mean", "median", "p95"):
                equal(a["maximum_chain"][key], b["maximum_actual_chain"][key])
        for ak, bk in (("run_seconds_all_60", "run_seconds"),
                       ("decision_seconds_all_60", "decision_seconds"),
                       ("receipt_audit_seconds_all_60", "receipt_audit_seconds"),
                       ("run_seconds_minus_audit_reference_all_60", "run_minus_receipt_audit_reference_seconds"),
                       ("decision_seconds_minus_audit_reference_all_60", "decision_minus_receipt_audit_reference_seconds")):
            for key in ("count", "mean", "median", "p95"):
                equal(source[ak][key], target["timing"][bk][key])
        for name in ("expanded", "evaluated"):
            equal(source[f"{name}_nodes_all_60"], target["search"]["counters"][f"{name}_nodes"]["total"])
        equal(source["rss_peak_kib_all_60"], target["rss_kib"]["max"])
        for row in target["per_seed"]:
            actual = source["per_seed"][str(row["seed"])]
            for ak, bk in (("maximum_chain", "maximum_chain"), ("premature_fires", "premature"),
                           ("game_over", "game_over"), ("no_fire", "no_fire"), ("placements", "turns")):
                equal(actual[ak], row[bk])
            assert row["repeat_digests_match"]
        if arm != "none":
            other = summary["comparisons"][f"none_to_{arm}"]
            for ak, bk in (("target_success_percentage_point_delta", "target10_percentage_point_delta"),
                           ("mean_maximum_chain_relative_percent", "mean_maximum_actual_chain_relative_percent"),
                           ("mean_run_seconds_relative_percent", "run_mean_relative_percent"),
                           ("mean_run_seconds_minus_audit_reference_relative_percent", "run_minus_receipt_audit_mean_relative_percent"),
                           ("rescued_success_seeds", "rescued_seeds"), ("lost_success_seeds", "regressed_seeds")):
                equal(source["vs_none"][ak], other[bk])
        checked[arm] = {"quality_all_30_prior8_additional22": "PASS", "per_seed": "PASS",
                        "raw_and_reference_timing": "PASS", "nodes_rss": "PASS"}
    receipts = []
    previous_end = None
    for entry in declared["schedule"]:
        arm, seed, repeat, ordinal = (entry[k] for k in ("arm", "seed", "repeat", "ordinal"))
        path = MEASUREMENT / "processes" / f"{ordinal:03d}-{arm}-seed-{seed}-repeat-{repeat}.json"
        receipt = read(path)
        equal(receipt["schedule_entry"], entry)
        equal(receipt["returncode"], 0)
        raw_relative = f"target-10/seed-{seed}-repeat-{repeat:02d}.json.gz"
        equal(receipt["raw_sha256"], independent["arms"][arm]["raw_checksums"][raw_relative])
        equal(receipt["raw_sha256"], sha(MEASUREMENT / arm / raw_relative))
        command = [str(ROOT / arm / ".venv/bin/python"),
                   str(ROOT / "control/eval/puyo236_four_pattern_trial.py"),
                   "worker", str(MEASUREMENT), arm, "--seed", str(seed), "--repeat", str(repeat)]
        equal(receipt["command"], command)
        start, end = (datetime.fromisoformat(receipt[k]) for k in ("started_at_utc", "finished_at_utc"))
        assert end >= start and (previous_end is None or start >= previous_end)
        previous_end = end
        assert receipt["fresh_process_seconds"] > 0
        receipts.append({"ordinal": ordinal, "sha256": sha(path),
                         "started_at_utc": receipt["started_at_utc"], "finished_at_utc": receipt["finished_at_utc"]})
    assert len(receipts) == len(list((MEASUREMENT / "processes").glob("*.json"))) == 240
    result = {"passed": True, "schema": "puyo236.parent_final_comparison_audit.v1",
              "summary_sha256": sha(MEASUREMENT / "comparison.json"),
              "independent_raw_audit_sha256": sha(ROOT / "parent-measurement-raw-audit.json"),
              "script_sha256": sha(Path(__file__)), "arms": checked,
              "process_count": 240, "processes_non_overlapping_at_saved_second_resolution": True,
              "receipts": receipts, "adoption": "pending_user_choice", "human_gui_qa": "pending"}
    output = ROOT / "parent-final-comparison-audit.json"
    with output.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"passed": True, "output": str(output), "process_count": 240}))


if __name__ == "__main__":
    main()
