import tempfile
import unittest
from pathlib import Path

from selfplay.opponent_pool import OpponentPool, OpponentSnapshot, default_opponent_pool
from selfplay.rating import expected_score, update_elo


try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401

    from eval.arena import (
        read_matches_csv,
        run_paired_series,
        run_parallel_paired_series,
        summarize_result,
        run_series,
        write_matches_csv,
        write_summary_csv,
    )
    from selfplay.policies import FirstLegalPolicy, RandomPolicy

    ARENA_AVAILABLE = True
except Exception:
    ARENA_AVAILABLE = False
    run_series = None
    run_paired_series = None
    run_parallel_paired_series = None
    summarize_result = None
    write_matches_csv = None
    write_summary_csv = None
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
        self.assertIsNotNone(pool.get("manager_rule"))
        for name in (
            "worker_large",
            "worker_quick",
            "worker_punish",
            "worker_counter",
            "worker_fire",
            "worker_survival",
        ):
            self.assertIsNotNone(pool.get(name))

    def test_balanced_sampling_prefers_less_used_opponent(self):
        class StubRandom:
            def choices(self, population, weights, k):
                return [population[weights.index(max(weights))]]

        pool = OpponentPool(
            snapshots=[
                OpponentSnapshot(name="overused", games_played=100),
                OpponentSnapshot(name="fresh", games_played=0),
            ]
        )

        selected = pool.sample(StubRandom(), strategy="balanced")

        self.assertEqual(selected.name, "fresh")


@unittest.skipUnless(ARENA_AVAILABLE, "gymnasium/numpy are not installed")
class TestArena(unittest.TestCase):
    def test_arena_runs_headless_series(self):
        result = run_series(FirstLegalPolicy(), RandomPolicy(seed=1), games=2, seed=1, max_steps=3)

        self.assertEqual(len(result.matches), 2)
        self.assertEqual(result.wins_player_0 + result.wins_player_1 + result.draws, 2)

    def test_arena_writes_match_and_summary_metrics(self):
        result = run_series(FirstLegalPolicy(), RandomPolicy(seed=1), games=1, seed=1, max_steps=3)
        summary = summarize_result(
            result,
            label="test",
            policy_a="first",
            policy_b="random",
            checkpoint_a=None,
            checkpoint_b=None,
            games=1,
            seed=1,
            max_steps=3,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            match_path = Path(tmpdir) / "matches.csv"
            summary_path = Path(tmpdir) / "summary.csv"
            write_matches_csv(match_path, result.matches)
            write_summary_csv(summary_path, summary)
            loaded_matches = read_matches_csv(match_path)

            match_text = match_path.read_text(encoding="utf-8")
            summary_text = summary_path.read_text(encoding="utf-8")
            match_bytes = match_path.read_bytes()
            summary_bytes = summary_path.read_bytes()

        self.assertIn("max_chain_player_0", match_text)
        self.assertIn("elo_delta_player_0", summary_text)
        self.assertIn("score_rate_policy_a_ci95_low", summary_text)
        self.assertIn("profile_counts_policy_a", summary_text)
        self.assertIn("canceled_ojama_player_0", match_text)
        self.assertNotIn(b"\r\n", match_bytes)
        self.assertNotIn(b"\r\n", summary_bytes)
        self.assertEqual(loaded_matches, result.matches)
        self.assertIn("mean_canceled_ojama_policy_a", summary)

    def test_paired_series_swaps_sides_for_each_seed(self):
        result = run_paired_series(FirstLegalPolicy(), RandomPolicy(seed=1), games=2, seed=4, max_steps=2)

        self.assertEqual(len(result.matches), 4)
        self.assertEqual([match.policy_a_side for match in result.matches], ["player_0", "player_1"] * 2)
        self.assertGreaterEqual(result.win_rate_policy_a, 0.0)
        self.assertLessEqual(result.win_rate_policy_a, 1.0)

    def test_parallel_paired_series_preserves_pair_order(self):
        result = run_parallel_paired_series(
            {"policy_type": "first"},
            {"policy_type": "random", "seed": 1},
            games=2,
            seed=4,
            max_steps=2,
            workers=2,
        )

        self.assertEqual(len(result.matches), 4)
        self.assertEqual([match.policy_a_side for match in result.matches], ["player_0", "player_1"] * 2)


if __name__ == "__main__":
    unittest.main()
