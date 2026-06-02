import tempfile
import unittest
from pathlib import Path

from selfplay.opponent_pool import OpponentPool, OpponentSnapshot, default_opponent_pool
from selfplay.rating import expected_score, update_elo


try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401

    from eval.arena import run_series
    from selfplay.policies import FirstLegalPolicy, RandomPolicy

    ARENA_AVAILABLE = True
except Exception:
    ARENA_AVAILABLE = False
    run_series = None
    FirstLegalPolicy = None
    RandomPolicy = None


class TestSelfPlayRatingAndPool(unittest.TestCase):
    def test_elo_update_rewards_winner(self):
        self.assertAlmostEqual(expected_score(1000.0, 1000.0), 0.5)

        rating_a, rating_b = update_elo(1000.0, 1000.0, 1.0)

        self.assertGreater(rating_a, 1000.0)
        self.assertLess(rating_b, 1000.0)

    def test_opponent_pool_round_trips_json(self):
        pool = OpponentPool(
            snapshots=[
                OpponentSnapshot(name="random", policy_type="random"),
                OpponentSnapshot(name="greedy", policy_type="greedy", rating=1010.0),
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pool.json"
            pool.save(path)
            loaded = OpponentPool.load(path)

        self.assertEqual([snapshot.name for snapshot in loaded.snapshots], ["random", "greedy"])
        self.assertEqual(loaded.get("greedy").rating, 1010.0)

    def test_default_pool_has_fixed_baselines(self):
        pool = default_opponent_pool()

        self.assertIsNotNone(pool.get("random"))
        self.assertIsNotNone(pool.get("greedy_score"))


@unittest.skipUnless(ARENA_AVAILABLE, "gymnasium/numpy are not installed")
class TestArena(unittest.TestCase):
    def test_arena_runs_headless_series(self):
        result = run_series(FirstLegalPolicy(), RandomPolicy(seed=1), games=2, seed=1, max_steps=3)

        self.assertEqual(len(result.matches), 2)
        self.assertEqual(result.wins_player_0 + result.wins_player_1 + result.draws, 2)


if __name__ == "__main__":
    unittest.main()
