"""Audit activated placement roots against actual locks using recorded inputs.

No policy is run. Authoritative board comparison is an offline replay sidecar,
never a runtime/public-reference witness or a G2 qualification shortcut.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState, transition
from agents.nextgen_shared_search import _pairs, _public_state
from eval.nextgen_gate_benchmark import SafeNoThreatMatch
from puyo_env.actions import action_to_placement
from src.core.realtime import TickInput


def replay_adoptions(raw):
    if len(raw["rows"]) != len(raw["ledger"]):
        raise ValueError("decision/receipt alignment is ambiguous")
    decisions = {row["tick"]: (i, row, diagnostic)
                 for i, (row, diagnostic) in enumerate(zip(raw["rows"], raw["ledger"]), 1)}
    if len(decisions) != len(raw["rows"]):
        raise ValueError("duplicate decision tick")
    match = SafeNoThreatMatch(raw["seed"])
    records, active = [], None
    for recorded_input in raw["semantic"]["inputs"]:
        simulator = match.player_states["player_0"].simulator
        if match.tick in decisions:
            index, row, wire = decisions[match.tick]
            request = c.NextgenRequest.from_dict(wire["request"])
            public, _ = _public_state(request)
            full = CompactSearchState.from_game(simulator.game)
            expected = action_to_placement(row["action"])
            prediction = transition(full, _pairs(request.public.own.known_pieces)[0], row["action"])
            active = {
                "decision": index, "request_tick": match.tick, "action": row["action"],
                "expected_pose": [expected.axis_x, expected.rotation.name],
                "public_predicted_chain": row["predicted_chain"],
                "offline_full_board_predicted_chain": prediction.chain_count,
                "offline_full_board_valid": prediction.valid,
                "public_planes": list(public.planes), "offline_full_planes": list(full.planes),
                "request_public": wire["request"]["public"], "receipt": wire["receipt"],
                "selection": wire["selection"], "phase": row["phase"],
                "survival": row["search"]["survival"],
                "active_pair": list(simulator.snapshot().active_pair),
                "active_position": list(simulator.snapshot().active_position),
                "next_gravity_tick": simulator._next_gravity_tick,
                "path": [],
            }
            records.append(active)
        before = simulator.snapshot().active_position
        result = match.step({key: TickInput.from_names(**value)
                             for key, value in recorded_input["inputs"].items()})
        if result.tick != recorded_input["tick"]:
            raise ValueError("input tick differs from replay")
        if active is not None and "lock" not in active:
            active["path"].append({
                "tick": result.tick, "before": before,
                "input": recorded_input["inputs"].get("player_0"),
                "after": simulator.snapshot().active_position,
                "fired": [a.name for a in result.player_results["player_0"].fired_actions],
            })
        for event in result.player_results["player_0"].events:
            if event.type == "lock":
                if active is None:
                    raise ValueError("lock without a recorded decision")
                active["lock"] = {"tick": event.tick, **event.data}
            if event.type == "resolution_complete":
                if active is None:
                    raise ValueError("resolution without a recorded decision")
                active["resolution"] = {"tick": event.tick, **event.data}
    if match.state_hash() != raw["semantic"]["final_hash"]:
        raise ValueError("replayed final state differs from raw")
    if len(records) != len(raw["rows"]):
        raise ValueError("replay missed a decision")
    mismatches = []
    for record in records:
        lock = record.get("lock")
        if lock is not None and [lock["axis_x"], lock["rotation"]] != record["expected_pose"]:
            mismatches.append(record["decision"])
        record["lock_matches_requested_root"] = None if lock is None else (
            [lock["axis_x"], lock["rotation"]] == record["expected_pose"])
    return {
        "schema": "puyo.nextgen.adoption_replay.v1", "seed": raw["seed"],
        "source": "offline_authoritative_replay; never runtime input or public-reference qualification",
        "input_semantic_digest": raw["semantic_digest"], "final_hash_matches": True,
        "lock_mismatch_decisions": mismatches, "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for path in args.inputs:
        raw = json.loads(gzip.decompress(path.read_bytes()))
        result = replay_adoptions(raw)
        target = args.output / (path.name.removesuffix(".json.gz") + ".adoption.json.gz")
        target.write_bytes(gzip.compress(json.dumps(result).encode(), mtime=0))
        print(path.name, "lock mismatch", result["lock_mismatch_decisions"], flush=True)


if __name__ == "__main__":
    main()
