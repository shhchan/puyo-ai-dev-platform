import unittest


try:
    import gymnasium  # noqa: F401
    import numpy

    from puyo_env.selfplay_env import VersusSelfPlayEnv
    from puyo_env.versus_env import VersusPuyoEnv
    from selfplay.policies import FirstLegalPolicy

    VERSUS_ENV_AVAILABLE = True
except Exception:
    VERSUS_ENV_AVAILABLE = False
    numpy = None
    VersusPuyoEnv = None
    VersusSelfPlayEnv = None
    FirstLegalPolicy = None


@unittest.skipUnless(VERSUS_ENV_AVAILABLE, "gymnasium/numpy are not installed")
class TestVersusPuyoEnv(unittest.TestCase):
    def test_reset_exposes_both_players_with_same_tsumo_seed(self):
        env = VersusPuyoEnv(seed=123, max_steps=10)

        observations, infos = env.reset(seed=123)

        self.assertEqual(set(observations), {"player_0", "player_1"})
        self.assertEqual(observations["player_0"]["board"].shape, (12, 13, 6))
        self.assertEqual(observations["player_0"]["own_board"].shape, (6, 13, 6))
        self.assertEqual(int(infos["player_0"]["action_mask"].sum()), 22)
        numpy.testing.assert_array_equal(
            observations["player_0"]["next_pairs"],
            observations["player_1"]["next_pairs"],
        )

    def test_first_legal_policies_run_until_truncated(self):
        env = VersusPuyoEnv(seed=123, max_steps=3)
        observations, infos = env.reset(seed=123)
        policy = FirstLegalPolicy()

        done = False
        steps = 0
        while not done:
            actions = {
                agent: policy.select_action(observations[agent], infos[agent])
                for agent in env.agents
            }
            observations, _, terminations, truncations, infos = env.step(actions)
            done = all(terminations.values()) or all(truncations.values())
            steps += 1

        self.assertEqual(steps, 3)
        self.assertIn("episode", infos["player_0"])
        self.assertIn("max_chain", infos["player_0"]["episode"])
        self.assertIn("max_chain_count", infos["player_0"])

    def test_pending_ojama_is_dropped_before_action(self):
        env = VersusPuyoEnv(seed=123, max_steps=5)
        observations, infos = env.reset(seed=123)
        env.player_states["player_1"].pending_ojama = 3
        policy = FirstLegalPolicy()

        actions = {
            agent: policy.select_action(observations[agent], infos[agent])
            for agent in env.agents
        }
        _, _, _, _, infos = env.step(actions)

        self.assertEqual(infos["player_1"]["pending_ojama"], 0)
        self.assertEqual(infos["player_1"]["received_ojama_total"], 3)

    def test_selfplay_wrapper_returns_single_agent_step(self):
        env = VersusSelfPlayEnv(seed=123, max_steps=2, opponent_policy=FirstLegalPolicy())
        _, info = env.reset(seed=123)

        action = int(numpy.flatnonzero(info["action_mask"])[0])
        _, _, terminated, truncated, next_info = env.step(action)

        self.assertFalse(terminated)
        self.assertFalse(truncated)
        self.assertIn("opponent_action", next_info)


if __name__ == "__main__":
    unittest.main()
