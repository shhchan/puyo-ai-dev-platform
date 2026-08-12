import tempfile
import unittest
from pathlib import Path

from selfplay.opponent_pool import (
    OPPONENT_POOL_SCHEMA_VERSION,
    STRATIFIED_ELO_STRATEGY,
    OpponentPool,
    OpponentSnapshot,
    build_schedule_artifact,
    default_opponent_pool,
)
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
    def stratified_pool(self) -> OpponentPool:
        quotas = {
            "large_builder": 0.30,
            "rush": 0.20,
            "counter_survival": 0.20,
            "rule_manager": 0.15,
            "historical": 0.15,
        }
        policy_types = {
            "large_builder": "worker_large",
            "rush": "worker_quick",
            "counter_survival": "worker_counter",
            "rule_manager": "manager_rule",
            "historical": "greedy",
        }
        return OpponentPool(
            snapshots=[
                OpponentSnapshot(
                    name=f"{stratum}_opponent",
                    policy_type=policy_type,
                    stratum=stratum,
                    role=f"fixed_{stratum}",
                    rating=900.0 + index * 100.0,
                )
                for index, (stratum, policy_type) in enumerate(policy_types.items())
            ],
            schema_version=OPPONENT_POOL_SCHEMA_VERSION,
            pool_id="test-stratified-v1",
            sampling_strategy=STRATIFIED_ELO_STRATEGY,
            quotas=quotas,
            fallback_by_stratum={
                stratum: {
                    "name": f"{stratum}_fallback",
                    "policy_type": policy_type,
                    "policy_kwargs": {},
                }
                for stratum, policy_type in policy_types.items()
            },
        )

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

    def test_legacy_checkpoint_pool_does_not_require_v2_hash_contract(self):
        pool = OpponentPool.from_dict(
            {
                "snapshots": [
                    {
                        "name": "legacy",
                        "policy_type": "checkpoint",
                        "checkpoint_path": "legacy.pt",
                    }
                ]
            }
        )

        self.assertEqual(pool.validate(), [])

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

    def test_stratified_schedule_has_exact_quota_and_paired_sides(self):
        pool = self.stratified_pool()

        first = pool.build_schedule(pairs=20, seed=129, target_rating=1000.0)
        second = pool.build_schedule(pairs=20, seed=129, target_rating=1000.0)

        self.assertEqual(
            [assignment.to_dict() for assignment in first],
            [assignment.to_dict() for assignment in second],
        )
        pair_records = [record for record in first if record.learner_side == "player_0"]
        counts = {
            stratum: sum(record.stratum == stratum for record in pair_records)
            for stratum in pool.quotas
        }
        self.assertEqual(
            counts,
            {
                "large_builder": 6,
                "rush": 4,
                "counter_survival": 4,
                "rule_manager": 3,
                "historical": 3,
            },
        )
        for pair_index in range(20):
            pair = [record for record in first if record.pair_index == pair_index]
            self.assertEqual(
                [record.learner_side for record in pair],
                ["player_0", "player_1"],
            )
            self.assertEqual(pair[0].game_seed, 129 + pair_index)
            self.assertEqual(pair[0].requested_opponent, pair[1].requested_opponent)

    def test_stratum_batch_preserves_opponent_coverage_before_elo_weighting(self):
        pool = self.stratified_pool()
        pool.snapshots.append(
            OpponentSnapshot(
                name="large_builder_distant",
                policy_type="worker_large",
                stratum="large_builder",
                role="fixed_large_builder",
                rating=2000.0,
            )
        )

        assignments = pool.build_schedule(pairs=20, seed=129, target_rating=1000.0)
        large_builder_pairs = {
            assignment.requested_opponent
            for assignment in assignments
            if assignment.stratum == "large_builder"
        }

        self.assertEqual(
            large_builder_pairs,
            {"large_builder_opponent", "large_builder_distant"},
        )

    def test_missing_checkpoint_uses_deterministic_fallback_with_evidence(self):
        pool = OpponentPool(
            snapshots=[
                OpponentSnapshot(
                    name="missing_history",
                    policy_type="checkpoint",
                    checkpoint_path="missing.pt",
                    checkpoint_sha256="0" * 64,
                    checkpoint_schema="puyo.checkpoint.v1",
                    stratum="historical",
                    role="historical_checkpoint",
                )
            ],
            schema_version=OPPONENT_POOL_SCHEMA_VERSION,
            pool_id="fallback-test-v1",
            sampling_strategy=STRATIFIED_ELO_STRATEGY,
            quotas={"historical": 1.0},
            fallback_by_stratum={
                "historical": {
                    "name": "manager_rule_fallback",
                    "policy_type": "manager_rule",
                    "policy_kwargs": {},
                }
            },
        )

        assignments = pool.build_schedule(pairs=1, seed=7)
        artifact = build_schedule_artifact(pool, assignments, pairs=1, seed=7)

        self.assertEqual(
            {assignment.effective_opponent for assignment in assignments},
            {"manager_rule_fallback"},
        )
        self.assertEqual(assignments[0].fallback["reason"], "missing_checkpoint")
        self.assertEqual(artifact["summary"]["fallback_pairs"], 1)
        self.assertEqual(len(artifact["sampling_history"]), 2)
        self.assertEqual(
            artifact["fallback_evidence"][0]["requested"]["checkpoint_sha256"],
            "0" * 64,
        )

    def test_checkpoint_hash_mismatch_is_recorded_as_corrupt_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint = Path(tmpdir) / "corrupt.pt"
            checkpoint.write_bytes(b"corrupt-checkpoint")
            pool = OpponentPool(
                snapshots=[
                    OpponentSnapshot(
                        name="corrupt_history",
                        policy_type="checkpoint",
                        checkpoint_path=str(checkpoint),
                        checkpoint_sha256="0" * 64,
                        checkpoint_schema="puyo.checkpoint.v1",
                        stratum="historical",
                    )
                ],
                schema_version=OPPONENT_POOL_SCHEMA_VERSION,
                pool_id="corrupt-test-v1",
                sampling_strategy=STRATIFIED_ELO_STRATEGY,
                quotas={"historical": 1.0},
                fallback_by_stratum={
                    "historical": {
                        "name": "manager_rule_fallback",
                        "policy_type": "manager_rule",
                        "policy_kwargs": {},
                    }
                },
            )

            assignments = pool.build_schedule(pairs=1, seed=9)

        self.assertEqual(
            assignments[0].fallback["reason"],
            "checkpoint_sha256_mismatch",
        )
        self.assertNotEqual(assignments[0].fallback["actual_sha256"], "0" * 64)

    def test_versioned_pool_round_trips_and_preserves_manifest_contract(self):
        pool = self.stratified_pool()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pool.json"
            pool.save(path)
            loaded = OpponentPool.load(path)

        self.assertEqual(loaded.schema_version, OPPONENT_POOL_SCHEMA_VERSION)
        self.assertEqual(loaded.pool_id, "test-stratified-v1")
        self.assertEqual(loaded.quotas, pool.quotas)
        self.assertEqual(loaded.validate(), [])

    def test_checked_in_v1_7_2_manifest_is_structurally_valid(self):
        pool = OpponentPool.load("train/config/v1_7_2_opponent_pool.json")

        self.assertEqual(pool.schema_version, OPPONENT_POOL_SCHEMA_VERSION)
        self.assertEqual(pool.pool_id, "v1.7.2-mixed-opponents-v1")
        self.assertEqual(pool.validate(), [])
        self.assertEqual(pool.quotas["large_builder"], 0.30)


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
