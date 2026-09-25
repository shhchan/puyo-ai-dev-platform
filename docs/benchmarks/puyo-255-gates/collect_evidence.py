"""Reproduce fixture/reference and historical summaries without editing old raw.

Run from the repository root after the timed single-worker measurements finish:
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-255-gates/collect_evidence.py
"""

import argparse
import json
from pathlib import Path

from agents import nextgen_contracts as c
from agents.nextgen_tactic_manager import RuleTacticSelector
from eval.nextgen_gate_benchmark import read, write
from eval.nextgen_gates import diagnose_selection, digest, safe_build_summary
from eval.nextgen_response_fixtures import TIMING, build, make_request, report
from eval.nextgen_template_fixtures import run_cases
from puyo_env.actions import PLACEMENT_ACTIONS
from puyo_env.nextgen_public_snapshot import derive_timing_summary
from src.core.game import GameState
from src.core.puyo import Puyo
from train.artifacts import file_sha256

OUTPUT = Path("runs/puyo-255-gates")
HISTORICAL = Path(
    "/home/sion2/workspaces/puyo-236-four-patterns-20260910/measurement/only240"
)


def historical():
    manifest = read(HISTORICAL / "experiment_manifest.json")
    rows = []
    for path in sorted((HISTORICAL / "target-10").glob("seed-*-repeat-*.json.gz")):
        r = read(path)
        assert r["manifest_sha256"] == manifest["manifest_sha256"]
        assert r["evaluated_commit"] == manifest["build_provenance"]["evaluated_commit"]
        assert r["completed_turns"] == len(r["records"])
        assert r["maximum_actual_fire_chain_count"] == max(
            r["actual_fire_chain_counts"], default=0
        )
        rows.append(
            {
                "seed": r["seed"],
                "repeat": r["repeat"],
                "termination": "placements"
                if r["termination_reason"] == "turn_limit"
                else r["termination_reason"],
                "placements": r["completed_turns"],
                "game_over": r["game_over"],
                "max_chain": r["maximum_actual_fire_chain_count"],
                "premature": r["premature_fire_count"],
                "semantic_digest": digest(
                    [
                        r["action_digest"],
                        r["trajectory_digest"],
                        r["final_board_digest"],
                    ]
                ),
                "decision_seconds": [v["elapsed_seconds"] for v in r["records"]],
                "raw_path": str(path),
                "raw_sha256": file_sha256(path),
                "source": r["evaluated_commit"],
                "configuration_sha256": r["configuration_sha256"],
                "backend_configuration_sha256": r["backend_configuration_sha256"],
            }
        )
    summary = safe_build_summary(rows)
    write(
        OUTPUT / "historical-only240.json",
        {
            "schema": "puyo.nextgen.historical_baseline.v1",
            "arm": "only240",
            "scope": "historical reference/native; not the current nextgen runtime",
            "quality_status": "FAIL",
            "adoption_is_quality_pass": False,
            "manifest_path": str(HISTORICAL / "experiment_manifest.json"),
            "manifest_file_sha256": file_sha256(
                HISTORICAL / "experiment_manifest.json"
            ),
            "original_manifest": manifest,
            "summary": summary,
            "rows": rows,
            "selection_regret": {
                "status": "UNKNOWN",
                "reason": "Historical raw has no nextgen six-tactic batch; no inferred candidate rank or tactical regret.",
            },
        },
    )


def public_reference():
    # Independent exhaustive one-ply engine witness, no search-produced ranks and
    # no unknown future. 40 points + visible carry 30 cancel one incoming unit.
    request = make_request(incoming=1, carry=30)
    good = []
    actual = []
    for index, action in enumerate(PLACEMENT_ACTIONS):
        game = GameState(seed=0)
        for y, row in enumerate(reversed(request.public.own.visible_board)):
            for x, value in enumerate(row):
                game.field.grid[y][x] = Puyo(c.PUBLIC_CELL_TO_COLOR[value])
        colors = request.public.own.known_pieces[0]
        game.current_puyo_1, game.current_puyo_2 = [
            Puyo(c.PUBLIC_CELL_TO_COLOR[v]) for v in colors
        ]
        game.state = "control"
        result = game.place_current_pair_and_resolve(
            action.axis_x, action.rotation, spawn_next=False
        )
        if result is not None and not result["game_over"] and result["chain_count"] > 0:
            # Each clearing red group guarantees >=40 score, enough with carry30.
            good.append(index)
            actual.append({"action": index, "chain_count": result["chain_count"]})
    assert good
    sidecar = {
        "snapshot_digest": request.public.digest,
        "tactic": "cancel",
        "good_root_actions": good,
        "source": "public_reference",
    }
    result = build(request)
    features = c.build_features({}, result.batch.action_mask)
    timing = derive_timing_summary(request.public, TIMING, request_tick=0)
    selection = RuleTacticSelector().select(request, result.batch, features, timing)
    diagnostic = c.Diagnostics(request, result.batch, features, selection, None)
    # The reference sidecar is physically separate from the public runtime data.
    write(
        OUTPUT / "public-reference-sidecar.json",
        {"reference": sidecar, "actual_engine_roots": actual},
    )
    write(OUTPUT / "public-reference-runtime.json", diagnostic.to_dict())
    return diagnose_selection(diagnostic.to_dict(), sidecar)


def main():
    global OUTPUT, HISTORICAL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--historical-dir", type=Path, default=HISTORICAL)
    args = parser.parse_args()
    OUTPUT, HISTORICAL = args.output, args.historical_dir
    threats = report()
    templates = run_cases()
    write(OUTPUT / "threat-fixtures.json", threats)
    write(OUTPUT / "template-fixtures.json", templates)
    classification = public_reference()
    write(OUTPUT / "classification.json", classification)
    historical()
    print(
        json.dumps(
            {
                "threat_candidate_gap": threats["candidate_gap"],
                "template_cases": len(templates["cases"]),
                "classification": classification,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
