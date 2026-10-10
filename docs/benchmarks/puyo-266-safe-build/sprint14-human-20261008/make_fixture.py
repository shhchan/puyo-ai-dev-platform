"""Build synthetic 2P edges, not the unrecorded human input.

Greedy is used only OFFLINE to make this fixture. The diagnostic reads the
frozen edges; no fixture-generator simulator enters the nextgen policy.
"""
import json
from pathlib import Path

from puyo_env.realtime_ai import RealtimeDecisionConfig, RealtimePolicyController
from puyo_env.realtime_versus import RealtimeVersusMatch
from selfplay.policies import GreedyScorePolicy
from src.core.constants import Action
from src.core.realtime import TickInput


def make_fixture():
    match = RealtimeVersusMatch(seed=127)
    controller = RealtimePolicyController(
        GreedyScorePolicy(), config=RealtimeDecisionConfig(latency_mode="configured"))
    inputs, events = [], []
    for _ in range(2000):
        value = controller.next_input(match, "player_1")
        if value.press or value.release or value.edges:
            inputs.append({"tick": match.tick, "input": value.to_json()})
        result = match.step({"player_1": value})
        for event in result.player_results["player_1"].events:
            if event.type in ("lock", "resolution_complete"):
                events.append({"type": event.type, "tick": event.tick, "data": event.data,
                               "attack": result.attack_diagnostics["player_1"]})
        if match.player_states["player_1"].sent_ojama_total >= 30:
            inputs.append({"tick": match.tick,
                           "input": TickInput(release=tuple(Action)).to_json()})
            break
    else:
        raise AssertionError("fixture did not send its all-clear bonus")
    return {"schema": "puyo.diagnostic.human_inputs.v1", "seed": 127,
            "provenance": "synthetic greedy-generated 2P edges; original human input unavailable",
            "inputs": inputs, "generator_events": events}


if __name__ == "__main__":
    output = Path(__file__).with_name("human-inputs.json")
    output.write_text(json.dumps(make_fixture(), indent=2) + "\n")
