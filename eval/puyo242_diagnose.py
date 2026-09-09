"""Read immutable baseline all-root captures before the single prefix trial."""

import argparse
from pathlib import Path

from agents.deep_chain_native import decode_request
from eval import deep_chain_builder_benchmark as b

SEEDS = (123, 126, 130, 133, 135, 138, 147, 151)
FIXED_BOARDS = ((123, 29), (126, 22), (130, 24), (135, 33), (138, 30), (147, 27), (151, 31))


def diagnose(source_root, output):
    summary = {"ticket": "PUYO-242", "kind": "pretrial existing-candidate diagnosis", "seeds": []}
    fixed = {"kind": "immutable baseline wire inputs; turns zero-based", "cases": []}
    for seed in SEEDS:
        path = source_root / f"seed-{seed}-all-roots.json.gz"
        source = b._read_json(path)
        assert source["build_provenance"]["evaluated_commit"] == "73ab4e8ce066555042f1a20e1b3b59be3a2a8968"
        cases = []
        for case in source["cases"]:
            request = decode_request(bytes.fromhex(case["request_hex"]))
            known_count = len(request.known_pairs)
            near = []
            for rank, root in enumerate(case["roots"]):
                fires = [v["selected_fire"] for v in root["scenario_values"]
                         if v["selected_fire_class"] == "target_fire" and v["selected_fire"]
                         and v["selected_fire"]["depth"] <= known_count]
                if fires:
                    near.append({"root_action": root["root_action"], "baseline_rank": rank + 1,
                                 "ranking_key": root["ranking_key"], "fires": fires})
            top = case["roots"][0]
            if near:
                cases.append({"turn_1based": case["turn"] + 1, "known_count": known_count,
                              "selected_action": top["root_action"], "selected_fire": top["best_fire"],
                              "selected_ranking_key": top["ranking_key"], "near_targets": near})
            if (seed, case["turn"]) in FIXED_BOARDS:
                fixed["cases"].append({"seed": seed, "turn": case["turn"],
                                       "source_sha256": b.file_sha256(path),
                                       "request_hex": case["request_hex"],
                                       "baseline_selected_action": case["native_selected_action"]})
        summary["seeds"].append({"seed": seed, "source": str(path), "source_sha256": b.file_sha256(path),
                                 "decisions": len(source["cases"]), "near_target_decisions": cases})
    assert len(fixed["cases"]) == len(FIXED_BOARDS)
    assert any(c["turn_1based"] == 23 for c in summary["seeds"][1]["near_target_decisions"])
    b._write_json(output / "pretrial-diagnosis.json", summary)
    b._write_json(output / "fixed-inputs.json", fixed)
    print([(s["seed"], len(s["near_target_decisions"])) for s in summary["seeds"]])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    diagnose(args.source_root, args.output)
