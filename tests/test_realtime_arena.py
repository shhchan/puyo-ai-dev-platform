import unittest

from eval.realtime_arena import (
    replay_realtime_match,
    run_realtime_match,
    run_realtime_paired_series,
    summarize_realtime_result,
)
from agents.strategy_workers import FixedProfilePolicy, smoke_worker_profiles
from puyo_env.realtime_ai import RealtimeDecisionConfig
from selfplay.policies import FirstLegalPolicy, RandomPolicy


class TestRealtimeArena(unittest.TestCase):
    def test_realtime_match_runs_existing_policies_with_replay(self):
        match = run_realtime_match(
            FirstLegalPolicy(),
            RandomPolicy(seed=1),
            seed=123,
            max_ticks=120,
            decision_config=RealtimeDecisionConfig(inference_latency_ticks=1),
            record_replay=True,
        )

        self.assertGreater(match.decisions_player_0, 0)
        self.assertGreater(match.emitted_input_ticks_player_0, 0)
        self.assertIsNotNone(match.replay)
        self.assertEqual(replay_realtime_match(match.replay), match.final_hash)

    def test_realtime_replay_records_search_objective_diagnostics(self):
        match = run_realtime_match(
            FixedProfilePolicy(4, smoke_worker_profiles()),
            RandomPolicy(seed=1),
            seed=123,
            max_ticks=120,
            decision_config=RealtimeDecisionConfig(inference_latency_ticks=1),
            record_replay=True,
        )

        ticks = match.replay["ticks"]
        diagnostics = [tick["policy_diagnostics"]["player_0"] for tick in ticks]

        self.assertTrue(any(item["search_objective"] for item in diagnostics))
        self.assertTrue(any(item["search_objective_result"] for item in diagnostics))
        self.assertTrue(any(item["plan_id"] for item in diagnostics))
        self.assertTrue(any(item["plan"].get("schema_version") == "n-turn-plan-v1" for item in diagnostics))

    def test_realtime_paired_series_swaps_policy_a_side(self):
        result = run_realtime_paired_series(
            FirstLegalPolicy(),
            RandomPolicy(seed=1),
            games=1,
            seed=4,
            max_ticks=80,
        )
        summary = summarize_realtime_result(
            result,
            label="smoke",
            policy_a="first",
            policy_b="random",
            games=len(result.matches),
            seed=4,
            max_ticks=80,
        )

        self.assertEqual(len(result.matches), 2)
        self.assertEqual([match.policy_a_side for match in result.matches], ["player_0", "player_1"])
        self.assertIn("score_rate_policy_a_ci95_low", summary)
        self.assertIn("mean_deadline_misses_policy_a", summary)


if __name__ == "__main__":
    unittest.main()
