import unittest


try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401

    from puyo_env.single_env import SinglePuyoEnv

    SINGLE_ENV_AVAILABLE = True
except Exception:
    SINGLE_ENV_AVAILABLE = False
    SinglePuyoEnv = None


@unittest.skipUnless(SINGLE_ENV_AVAILABLE, "gymnasium/numpy are not installed")
class TestSinglePuyoEnv(unittest.TestCase):
    def test_reset_exposes_action_mask(self):
        env = SinglePuyoEnv(seed=123, max_steps=10)

        observation, info = env.reset(seed=123)

        self.assertIn("board", observation)
        self.assertEqual(observation["board"].shape, (6, 13, 6))
        self.assertEqual(int(info["action_mask"].sum()), 22)

    def test_masked_random_policy_runs_until_truncated(self):
        env = SinglePuyoEnv(seed=123, max_steps=5)
        _, info = env.reset(seed=123)

        done = False
        steps = 0
        while not done:
            action = int(numpy.flatnonzero(info["action_mask"])[0])
            _, _, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            steps += 1

        self.assertEqual(steps, 5)


if __name__ == "__main__":
    unittest.main()
