"""Public-history certainty, lifecycle failures and private non-interference."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from agents import nextgen_contracts as c
from puyo_env.nextgen_public_inference import PublicInferenceTracker
from puyo_env.nextgen_public_snapshot import PublicPlacementRecord
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.puyo import Puyo


def visible(public, cells, *, phase="control"):
    rows = [[0] * 6 for _ in range(12)]
    for x, y, value in cells:
        rows[y][x] = value
    return replace(public, visible_board=((None,) * 6,) * 2 + tuple(map(tuple, reversed(rows))),
                   phase=phase)


class PublicInferenceTests(unittest.TestCase):
    def setUp(self):
        self.match = RealtimeVersusMatch(seed=127)
        self.public = self.match.public_snapshot().own

    def tracker(self, *, empty_reset=True):
        return PublicInferenceTracker(self.public, episode_id="test-episode", player_id=0,
                                      started_tick=0, empty_reset=empty_reset)

    def test_explicit_reset_origin_midgame_and_episode_reset(self):
        self.assertEqual(self.tracker(empty_reset=False).snapshot().status, "unknown")
        before = self.match.public_board_inference()
        self.assertEqual(before.status, "known")
        self.match.reset()
        after = self.match.public_board_inference()
        self.assertNotEqual(before.episode_id, after.episode_id)
        self.assertEqual(after.status, "known")
        late = RealtimeVersusMatch(seed=127)
        late.step()
        self.assertEqual(late.public_board_inference().status, "unknown")
        # Reassigning the clock does not fabricate reset provenance.
        late.tick = 0
        self.assertEqual(late.public_board_inference().status, "unknown")

    def test_private_rows_future_and_rng_do_not_change_inference(self):
        before = self.match.public_board_inference()
        game = self.match.player_states["player_0"].simulator.game
        game.field.grid[12][0] = Puyo(c.PUBLIC_CELL_TO_COLOR[1])
        game.field.grid[13][1] = Puyo(c.PUBLIC_CELL_TO_COLOR[2])
        game.next_puyo_queue.append((Puyo(c.PUBLIC_CELL_TO_COLOR[3]), Puyo(c.PUBLIC_CELL_TO_COLOR[4])))
        self.match._ojama_rngs["player_0"].seed(123456)
        original = game.field.get_puyo

        def visible_only(x, y):
            if y >= 12:
                raise AssertionError("private row read")
            return original(x, y)

        game.field.get_puyo = visible_only
        self.match.public_snapshot()
        self.assertEqual(self.match.public_board_inference(), before)
        self.assertEqual(before.hidden_rows, ((0,) * 6,) * 2)

    def test_missing_tick_missing_lock_and_cross_episode_fail_closed(self):
        event = SimpleNamespace(player_id=0, kind="placement")
        for tick, episode, events, reason in (
            (1, "test-episode", (), "lifecycle_gap"),
            (0, "other-episode", (), "lifecycle_gap"),
            (0, "test-episode", (event,), "lock_history_gap"),
        ):
            tracker = self.tracker()
            tracker.observe(self.public, tick=tick, episode_id=episode, events=events)
            result = tracker.snapshot()
            self.assertEqual((result.status, result.reason), ("unknown", reason))
            self.assertTrue(all(v is None for row in result.hidden_rows for v in row))

    def lock_once(self):
        tracker = self.tracker()
        pair = self.public.known_pieces[0]
        placed = visible(self.public, ((0, 0, pair[0]), (0, 1, pair[1])), phase="animate")
        record = PublicPlacementRecord("0:0:lock", 0, 0, 0, pair)
        tracker.observe(placed, tick=0, episode_id="test-episode", locks=(record,),
                        events=(SimpleNamespace(player_id=0, kind="placement"),))
        self.assertEqual(tracker.snapshot().reason, "unsettled")
        return tracker, replace(placed, phase="control")

    def test_clear_consistency_is_required_before_certainty(self):
        for chain, expected in ((0, "known"), (1, "unknown")):
            tracker, settled = self.lock_once()
            tracker.observe(settled, tick=1, episode_id="test-episode",
                            resolutions=(SimpleNamespace(player_id=0, chain_count=chain),),
                            events=(SimpleNamespace(player_id=0, kind="clear"),) if chain else ())
            self.assertEqual(tracker.snapshot().status, expected)

    def test_settled_visible_mismatch_and_missing_clear_fail_closed(self):
        tracker, settled = self.lock_once()
        changed = visible(settled, ((0, 0, 4),))
        tracker.observe(changed, tick=1, episode_id="test-episode")
        self.assertEqual(tracker.snapshot().reason, "settled_visible_mismatch")
        tracker = self.tracker()
        tracker.observe(self.public, tick=0, episode_id="test-episode",
                        events=(SimpleNamespace(player_id=0, kind="clear"),))
        self.assertEqual(tracker.snapshot().reason, "clear_history_gap")

    def test_garbage_is_observed_only_in_visible_empty_cells(self):
        for color, expected in ((c.PUBLIC_GARBAGE_ID, "known"), (1, "unknown")):
            tracker = self.tracker()
            after = visible(self.public, ((5, 0, color),))
            tracker.observe(after, tick=0, episode_id="test-episode",
                            events=(SimpleNamespace(player_id=0, kind="drop"),))
            result = tracker.snapshot()
            self.assertEqual(result.status, expected)
            self.assertEqual(result.visible_digest, c.semantic_digest(after.visible_board))

    def test_contract_roundtrip_and_unknown_never_carries_guesses(self):
        known = self.tracker().snapshot()
        self.assertEqual(c.PublicBoardInference.from_dict(known.to_dict()), known)
        with self.assertRaises(ValueError):
            replace(known, status="unknown")
        with self.assertRaises(ValueError):
            replace(known, player_id=2)
        with self.assertRaises(ValueError):
            replace(known, visible_digest="wrong")


if __name__ == "__main__":
    unittest.main()
