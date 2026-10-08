"""Offline comparison only: the tracker never accepts the oracle field."""

import argparse
import gzip
import json
from pathlib import Path
from agents import nextgen_contracts as c
from eval.nextgen_gate_benchmark import SafeNoThreatMatch
from eval.realtime_arena import match_from_replay
from src.core.realtime import TickInput

ROOT = Path("docs/benchmarks/puyo-266-safe-build")


def read(p):
    return json.loads(gzip.decompress(p.read_bytes()))


def check(name, match, inputs, decisions):
    match.public_board_inference()
    rows = []
    for item in inputs:
        if match.tick in decisions:
            inference = match.public_board_inference()
            game = match.player_states["player_0"].simulator.game
            actual = tuple(
                (
                    tuple(
                        (
                            c.PUBLIC_CELL_TO_COLOR.index(game.field.grid[y][x].color)
                            for x in range(6)
                        )
                    )
                    for y in (12, 13)
                )
            )
            rows.append(
                {
                    "case": f"{name}/{decisions[match.tick]}",
                    "status": inference.status,
                    "hidden_rows": inference.hidden_rows,
                    "offline_matches": inference.hidden_rows == actual,
                    "reason": inference.reason,
                }
            )
        match.step({k: TickInput.from_names(**v) for k, v in item["inputs"].items()})
    return rows


rows = []
for seed in [123, 132, 135]:
    raw = read(ROOT / f"desktop-survival-20260928/before/gtr-{seed}.json.gz")
    rows += check(
        f"gtr-{seed}",
        SafeNoThreatMatch(seed),
        raw["semantic"]["inputs"],
        {r["tick"]: i for i, r in enumerate(raw["rows"], 1)},
    )
root = ROOT / "sprint14-human-20261008/before"
report = read(root / "report.json.gz")
replay = read(root / "replay.json.gz")
rows += check(
    "human-127",
    match_from_replay(replay),
    replay["ticks"],
    {
        a["record"]["activation_tick"]: a["payload"]["nextgen"]["request"]["identity"][
            "decision_id"
        ]
        for a in report["attempts"]
        if a["record"]["outcome"] == "activated"
    },
)
result = {
    "scope": "Public observer receives public snapshot/lock/lifecycle only; full hidden field is compared offline in this driver, never used by observer or policy.",
    "rows": rows,
    "count": len(rows),
    "unknown": sum((r["status"] != "known" for r in rows)),
    "mismatches": sum((not r["offline_matches"] for r in rows)),
}
parser = argparse.ArgumentParser(
    description="Offline replay audit; oracle hidden rows never enter inference."
)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.write_text(json.dumps(result, indent=2) + "\n")
print({k: v for k, v in result.items() if k != "rows"})
for r in rows:
    if r["case"] in [
        "gtr-123/28",
        "gtr-132/30",
        "gtr-135/34",
        "gtr-135/35",
        "gtr-135/36",
        "human-127/26",
    ]:
        print(r)
assert result["unknown"] == result["mismatches"] == 0
