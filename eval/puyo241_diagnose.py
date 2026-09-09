"""Inspect existing baseline request states before the risk-weight trial.

This does not rerank old beams to claim quality improvement. Saved pruning
coverage is observational; scores for discarded internal nodes are unavailable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agents.chain_structure import ChainStructureAction, ChainStructureEvaluator
from agents.compact_search import legal_action_indices, transition
from agents.deep_chain_native import decode_request
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation

SEEDS = (123, 126, 130, 133, 135, 138, 147, 151)


def compact(evaluation):
    features = evaluation.features
    return {
        "features": features.to_dict(),
        "score_breakdown": evaluation.score_breakdown.to_dict(),
        "potential_count_reward": features.potential_chain_count * 45000,
        "hidden_row_penalty": features.hidden_row_count * -1500,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to overwrite diagnostic evidence")
    evaluator = ChainStructureEvaluator()
    cases, checksums = [], {}
    for seed in SEEDS:
        path = args.source / f"seed-{seed}-all-roots.json.gz"
        checksums[path.name] = baseline.file_sha256(path)
        source = baseline._read_json(path)
        assert source["build_provenance"]["evaluated_commit"] == "73ab4e8ce066555042f1a20e1b3b59be3a2a8968"
        for case in source["cases"]:
            request = decode_request(bytes.fromhex(case["request_hex"]))
            root_eval = evaluator.evaluate(request.state, target_chain_count=10)
            action_rows = []
            # Same public current pair: these are immediate legal child scores,
            # not the unavailable score distribution inside historical beams.
            for action in legal_action_indices(request.state):
                result = transition(request.state, request.known_pairs[0], action)
                if not result.valid:
                    continue
                evaluation = evaluator.evaluate(
                    result.state, parent=root_eval,
                    action=ChainStructureAction.from_result(result), target_chain_count=10,
                )
                action_rows.append({"action": action, **compact(evaluation)})
            chosen = next(r for r in case["roots"] if r["root_action"] == case["native_selected_action"])
            cases.append({
                "seed": seed, "turn_1based": case["turn"] + 1,
                "state": compact(root_eval), "selected_action": chosen["root_action"],
                "immediate_children": action_rows, "search_counters": case["counters"],
                "selected_root_scenarios": [{k: s[k] for k in (
                    "scenario_id", "pruned_nodes", "quiet_survivor", "survivor_evaluator_score",
                    "survivor_coverage", "reached_depth", "selected_fire_class",
                )} for s in chosen["scenario_values"]],
            })
        print({"seed": seed, "cases": len(source["cases"])}, flush=True)
    ablation.write_run(args.output, {
        "ticket": "PUYO-241", "kind": "pretrial diagnosis; excluded from timings and quality trials",
        "source_commit": "73ab4e8ce066555042f1a20e1b3b59be3a2a8968",
        "script_sha256": baseline.file_sha256(Path(__file__)),
        "source_sha256": checksums, "cases": cases,
        "limitation": "Historical retained/pruned counts are known; per-node discarded beam scores are not captured. Immediate children do not reconstruct deep beam competition.",
    })


if __name__ == "__main__":
    main()
