"""Read-only, offline feasibility; no policy, quota or repository changes."""

import argparse
import gzip
import json
from pathlib import Path
from collections import deque
from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState, transition, legal_action_indices
from agents.nextgen_shared_search import _pairs, _public_state, ResponseBudget
from agents.nextgen_survival import probe
from puyo_env.actions import PLACEMENT_ACTIONS
from puyo_env.action_planner import (
    _transition_piece_state,
    _geometric_paths,
    PLANNER_ACTIONS,
)
from src.core.game import GameState
from src.core.puyo import Puyo
from eval.realtime_arena import match_from_replay
from src.core.realtime import TickInput

ROOT = Path("docs/benchmarks/puyo-266-safe-build/desktop-survival-20260928/before")


def read(p):
    return json.loads(gzip.decompress(p.read_bytes()))


class GeometryCache:
    def __init__(self):
        self.cache = {}
        self.nodes = 0

    def check(self, state, pair, action):
        occupied = 0
        for plane in state.planes:
            occupied |= plane
        if occupied not in self.cache:
            game = GameState(seed=0)
            game.state = "control"
            game.current_puyo_1, game.current_puyo_2 = map(Puyo, pair)
            game.next_puyo_queue.clear()
            game.puyo_sequence = None
            for y, row in enumerate(state.to_color_grid()):
                for x, color in enumerate(row):
                    game.field.grid[y][x] = Puyo(color)
            start = (game.puyo_x, game.puyo_y, game.puyo_rot, 0)
            self.cache[occupied] = (game, deque([start]), {start}, set())
        game, queue, seen, found = self.cache[occupied]
        pose = PLACEMENT_ACTIONS[action]
        target = (
            pose.axis_x,
            game.find_landing_y(pose.axis_x, pose.rotation),
            pose.rotation,
        )
        if target[1] is None:
            return False
        while queue and target not in found:
            current = queue.popleft()
            found.add(current[:3])
            # Charge each expanded control state, including the goal.
            self.nodes += 1
            for op in PLANNER_ACTIONS:
                nxt = _transition_piece_state(game, current, op)
                if nxt is not None and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        result = target in found
        reference = _geometric_paths(game, (pose,), 10000)[pose][1] is not None
        assert result == reference, (action, result, reference)
        return result


def check(state, pairs, witness, cache):
    control_start = cache.nodes
    steps = []
    placements = 0
    for depth, action in enumerate(witness):
        if depth:
            reachable = cache.check(state, pairs[depth], action)
            steps.append({"depth": depth, "action": action, "reachable": reachable})
            if not reachable:
                return {
                    "proven": False,
                    "control_nodes": cache.nodes - control_start,
                    "placement_nodes": placements,
                    "steps": steps,
                }
        state = transition(state, pairs[depth], action).state
        placements += 1
    return {
        "proven": True,
        "control_nodes": cache.nodes - control_start,
        "placement_nodes": placements,
        "steps": steps,
    }


def record(name, wire, row, planes):
    req = c.NextgenRequest.from_dict(wire["request"])
    public, _ = _public_state(req)
    full = CompactSearchState(planes=tuple(planes))
    pairs = _pairs(req.public.own.known_pieces)
    _, survival = probe(req, public, legal_action_indices(public), ResponseBudget(256))
    roots = {r["action"]: r for r in survival.get("roots", [])}
    chosen = roots.get(row["action"], {})
    hidden = [
        [x, y, full.color_at(x, y).name]
        for y in (12, 13)
        for x in range(6)
        if public.color_at(x, y) != full.color_at(x, y)
    ]
    clears = []
    for a in legal_action_indices(full):
        if req.execution.reachable_mask[a]:
            result = transition(full, pairs[0], a)
            if result.valid and result.chain_count and (not result.game_over):
                clears.append({"action": a, "chain": result.chain_count})
    output = {
        "case": name,
        "selected_root": row["action"],
        "selected_status": chosen.get("status"),
        "selected_witness": chosen.get("witness"),
        "missing_hidden": hidden,
        "probe_nodes": survival["nodes"],
        "offline_immediate_nonfatal_clears": clears,
    }
    if chosen.get("witness"):
        output["selected_public"] = check(
            public, pairs, chosen["witness"], GeometryCache()
        )
        output["selected_offline_actual"] = check(
            full, pairs, chosen["witness"], GeometryCache()
        )
    batch = wire["batch"]
    candidates = {v["candidate_id"]: v for v in batch["candidates"]}
    ids = next(
        (t["candidate_ids"] for t in batch["tactics"] if t["tactic_id"] == "build_main")
    )
    seen = set()
    ranked = []
    cache = GeometryCache()
    charged_placements = 0
    for cid in ids:
        action = candidates[cid]["root_action"]
        if action in seen:
            continue
        seen.add(action)
        r = roots.get(action, {})
        if not r.get("witness"):
            continue
        result = check(public, pairs, r["witness"], cache)
        charged_placements += result["placement_nodes"]
        ranked.append(
            {
                "root": action,
                "witness": r["witness"],
                "chain": r["root_chain"],
                **result,
                "cumulative_control": cache.nodes,
                "placement_revalidation": charged_placements,
                "probe_plus_control": survival["nodes"] + cache.nodes,
                "probe_plus_control_plus_revalidation": survival["nodes"]
                + cache.nodes
                + charged_placements,
            }
        )
        if result["proven"]:
            ranked[-1]["offline_actual"] = check(
                full, pairs, r["witness"], GeometryCache()
            )
            break
    output["ranked_proof_to_first_public_witness"] = ranked
    return output


rows = []
for seed, decisions in [
    (123, [28]),
    (126, [33, 34, 35, 36]),
    (128, [36, 37, 38, 39]),
    (132, [30, 31, 32, 33]),
    (135, [32, 33, 34, 35, 36, 37]),
    (144, [35, 36, 37, 38]),
]:
    raw = read(ROOT / f"gtr-{seed}.json.gz")
    locks = read(ROOT / f"gtr-{seed}.locks.json.gz")
    for d in decisions:
        rows.append(
            record(
                f"gtr-{seed}/{d}",
                raw["ledger"][d - 1],
                raw["rows"][d - 1],
                locks["records"][d - 1]["offline_full_planes"],
            )
        )
# Full planes below are reconstructed offline, never supplied to the public probe.
root = Path("docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/before")
report, replay = (read(root / "report.json.gz"), read(root / "replay.json.gz"))
match = match_from_replay(replay)
selected = next(
    (
        a
        for a in report["attempts"]
        if a["record"]["outcome"] == "activated"
        and a["payload"]["nextgen"]["request"]["identity"]["decision_id"] == 26
    )
)
for t in replay["ticks"]:
    if match.tick == selected["record"]["activation_tick"]:
        planes = CompactSearchState.from_game(
            match.player_states["player_0"].simulator.game
        ).planes
        rows.append(
            record(
                "human-127/26",
                selected["payload"]["nextgen"],
                {
                    "action": selected["record"]["executed_action"],
                    "search": selected["payload"]["search"],
                },
                planes,
            )
        )
        break
    match.step({k: TickInput.from_names(**v) for k, v in t["inputs"].items()})
result = {
    "scope": "offline feasibility at c735ea7 only; control counters measure unconstrained target-proof cost and are NOT executed as policy; cache uses public occupancy, fresh spawn and zero interpolation; actual planes used only to classify saved failures; no runtime change",
    "cases": rows,
}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--output",
    type=Path,
    required=True,
    help="New JSON output path; saved evidence is not overwritten by default.",
)
args = parser.parse_args()
args.output.write_text(json.dumps(result, indent=2) + "\n")
for r in rows:
    print(
        r["case"],
        "selected",
        r["selected_root"],
        r["selected_status"],
        "hidden",
        r["missing_hidden"],
        "clears",
        r["offline_immediate_nonfatal_clears"],
        "P/F",
        r.get("selected_public", {}).get("proven"),
        r.get("selected_offline_actual", {}).get("proven"),
        "priority",
        [
            (
                x["root"],
                x["proven"],
                x["cumulative_control"],
                x["probe_plus_control_plus_revalidation"],
            )
            for x in r["ranked_proof_to_first_public_witness"]
        ],
    )
