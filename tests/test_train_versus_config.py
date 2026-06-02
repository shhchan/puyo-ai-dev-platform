import unittest

from train.train_versus import build_config, parse_args


class TestTrainVersusConfig(unittest.TestCase):
    def test_long_smoke_config_loads_and_accepts_overrides(self):
        args = parse_args(
            [
                "--config",
                "train/config/versus_long_smoke.yaml",
                "--set",
                "total_timesteps=2048",
                "--set",
                "keep_best_checkpoint=false",
            ]
        )

        config = build_config(args)

        self.assertEqual(config.run_name, "versus_long_smoke")
        self.assertEqual(config.total_timesteps, 2048)
        self.assertFalse(config.keep_best_checkpoint)
        self.assertEqual(config.best_checkpoint_metric, "mean_win_rate")


if __name__ == "__main__":
    unittest.main()
