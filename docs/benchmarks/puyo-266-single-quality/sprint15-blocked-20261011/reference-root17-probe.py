import gzip
import json
import pathlib
from eval import nextgen_single_quality_gate as g
from puyo_env.realtime_ai import RealtimeDecisionConfig, RealtimePolicyController
from src.core.realtime import TickInput
from puyo_env.actions import action_to_placement

root = pathlib.Path("runs/puyo-266-quality-targeted-v1")
raw = g.read(root / "deep_chain_builder-pattern-4519-repeat-1.json.gz")
m = g.read(root / "manifest.json")
assert g.current_source() == m["source"]
g.require_clean()


def prefix():
    match = g.new_match(m["config"], 4519)
    for tick in raw["ticks"]:
        if match.tick == 1560:
            break
        match.step({k: TickInput.from_names(**v) for k, v in tick["inputs"].items()})
        assert match.state_hash() == tick["state_hash"]
    assert match.tick == 1560
    return match


match = prefix()
public = match.public_snapshot().own
saved = next(r for r in raw["rows"] if r["tick"] == 1560)
assert public.to_dict() == saved["public"] and saved["reachable_mask"][17]


class Fixed:
    policy_id = "counterfactual_public_root17"

    def select_action(self, observation, info):
        return 17


controller = RealtimePolicyController(
    Fixed(),
    config=RealtimeDecisionConfig(
        latency_mode="configured", use_reachable_action_mask=True
    ),
)
ticks = []
events = []
for _ in range(1000):
    inputs = {"player_0": controller.next_input(match, "player_0")}
    result = match.step(inputs)
    ticks.append(
        {
            "tick": result.tick,
            "inputs": {k: v.to_json() for k, v in inputs.items()},
            "state_hash": match.state_hash(),
        }
    )
    events.extend(
        {"type": e.type, "tick": e.tick, **e.data}
        for e in result.player_results["player_0"].events
        if e.type in ("lock", "resolution_complete")
    )
    if any(e["type"] == "resolution_complete" for e in events):
        break
lock = next(e for e in events if e["type"] == "lock")
pose = action_to_placement(17)
assert (lock["axis_x"], lock["rotation"]) == (pose.axis_x, pose.rotation.name)
remaining = g.measure(
    match, g.make_policy("deep_chain_builder"), "deep_chain_builder", placements=15
)
replay = prefix()
for tick in ticks + remaining["ticks"]:
    replay.step({k: TickInput.from_names(**v) for k, v in tick["inputs"].items()})
    assert replay.state_hash() == tick["state_hash"]
assert g.current_source() == m["source"]
g.require_clean()
report = {
    "purpose": "diagnostic single public known-prefix counterfactual; not acceptance",
    "source": m["source"],
    "baseline_tick": 1560,
    "replacement_root": 17,
    "replacement_events": events,
    "replacement_ticks": ticks,
    "continuation": remaining,
    "all_replay_hashes_match": True,
    "total_placements": 31 + len(remaining["chains"]),
    "max_actual_chain": max(
        raw["chains"][:30]
        + [e["chain_count"] for e in events if e["type"] == "resolution_complete"]
        + remaining["chains"]
    ),
}
p = pathlib.Path(
    "runs/puyo-266-quality-targeted-control-v1/reference-root17-counterfactual.json.gz"
)
p.write_bytes(gzip.compress(json.dumps(report).encode(), mtime=0))
print(
    json.dumps(
        {
            k: v
            for k, v in report.items()
            if k not in ("source", "replacement_ticks", "continuation")
        }
    )
)
print(
    json.dumps(
        {
            "final": remaining["final"],
            "game_over": remaining["game_over"],
            "chains": remaining["chains"],
        }
    )
)
