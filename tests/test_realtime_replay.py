import unittest
from pathlib import Path

from src.core.replay import assert_replay_matches_fixture, inputs_from_fixture, load_replay_fixture
from src.core.realtime import RealtimeHeadlessSimulator
from src.core.constants import Action


class TestRealtimeReplay(unittest.TestCase):
    def test_ordered_input_fixture_replays_same_hash_without_stale_hold(self):
        fixture = {"inputs": [{
            "tick": 0, "press": ["RIGHT"], "release": ["RIGHT"],
            "edges": [["press", "RIGHT"], ["release", "RIGHT"]],
        }]}
        inputs = inputs_from_fixture(fixture)
        first = RealtimeHeadlessSimulator(seed=55)
        second = RealtimeHeadlessSimulator(seed=55)
        self.assertEqual(
            [result.snapshot_hash for result in first.advance_ticks(12, inputs)],
            [result.snapshot_hash for result in second.advance_ticks(12, inputs)],
        )
        self.assertNotIn(Action.RIGHT, first.held_actions)

    def test_fixture_replays_to_expected_hashes(self):
        fixture_path = Path(__file__).parent / "fixtures" / "realtime_replay_seed123.json"
        fixture = load_replay_fixture(fixture_path)

        result = assert_replay_matches_fixture(fixture)

        self.assertEqual(result.ticks, 80)
        self.assertEqual(result.final_hash, fixture["expected_final_hash"])

    def test_replay_resume_from_clone_reaches_same_final_hash(self):
        fixture_path = Path(__file__).parent / "fixtures" / "realtime_replay_seed123.json"
        fixture = load_replay_fixture(fixture_path)

        inputs = inputs_from_fixture(fixture)
        full = RealtimeHeadlessSimulator(seed=fixture["seed"])
        full.advance_ticks(fixture["ticks"], inputs_by_tick=inputs)

        resumed = RealtimeHeadlessSimulator(seed=fixture["seed"])
        resumed.advance_ticks(40, inputs_by_tick=inputs)
        resumed = resumed.clone()
        resumed.advance_ticks(fixture["ticks"] - 40, inputs_by_tick=inputs)

        self.assertEqual(full.state_hash(), resumed.state_hash())


if __name__ == "__main__":
    unittest.main()
