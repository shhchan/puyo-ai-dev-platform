import unittest

try:
    from train.train_manager import build_config, parse_args

    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "manager training dependencies are not installed")
class TestManagerTrainingConfig(unittest.TestCase):
    def test_smoke_config_and_overrides(self):
        config = build_config(
            parse_args(
                [
                    "--config",
                    "train/config/manager_smoke.yaml",
                    "--set",
                    "seed=9",
                    "--set",
                    "use_smoke_profiles=false",
                ]
            )
        )

        self.assertEqual(config.seed, 9)
        self.assertFalse(config.use_smoke_profiles)
        self.assertEqual(config.num_steps, 4)

    def test_medium_and_long_configs_enable_curriculum_and_pool(self):
        medium = build_config(parse_args(["--config", "train/config/manager_medium.yaml"]))
        long_run = build_config(parse_args(["--config", "train/config/manager_long.yaml"]))

        self.assertTrue(medium.curriculum_enabled)
        self.assertEqual(medium.opponent_sampling, "balanced")
        self.assertGreater(medium.selfplay_snapshot_interval, 0)
        self.assertEqual(long_run.opponent_sampling, "elo")
        self.assertEqual(long_run.total_timesteps, 1_000_000)


if __name__ == "__main__":
    unittest.main()
