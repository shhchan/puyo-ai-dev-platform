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
        self.assertEqual(config.search_control_mode, "hybrid")
        self.assertEqual(
            config.curriculum_stages,
            "chain_construction,deadline_counter,punish,survival,full_match",
        )
        self.assertEqual(config.selfplay_eval_games, 0)
        self.assertEqual(config.rollback_min_episodes, 0)

    def test_medium_and_long_configs_enable_curriculum_and_pool(self):
        medium = build_config(parse_args(["--config", "train/config/manager_medium.yaml"]))
        long_run = build_config(parse_args(["--config", "train/config/manager_long.yaml"]))

        self.assertTrue(medium.curriculum_enabled)
        self.assertEqual(medium.search_control_mode, "hybrid")
        self.assertEqual(medium.opponent_sampling, "balanced")
        self.assertGreater(medium.selfplay_snapshot_interval, 0)
        self.assertGreater(medium.selfplay_eval_games, 0)
        self.assertGreater(medium.rollback_min_episodes, 0)
        self.assertEqual(long_run.opponent_sampling, "elo")
        self.assertEqual(long_run.total_timesteps, 100_000)
        self.assertEqual(long_run.num_envs, 8)
        self.assertTrue(long_run.parallel_envs)
        self.assertEqual(long_run.behavior_cloning_epochs, 0)
        self.assertEqual(long_run.best_window_episodes, 50)
        self.assertEqual(long_run.best_min_episodes, 50)
        self.assertEqual(
            long_run.curriculum_stages,
            "chain_construction,deadline_counter,punish,survival,full_match",
        )
        self.assertEqual(long_run.selfplay_eval_opponents, 3)

    def test_initial_checkpoint_override(self):
        config = build_config(
            parse_args(
                [
                    "--config",
                    "train/config/manager_long.yaml",
                    "--set",
                    "initial_checkpoint_path=runs/medium/best.pt",
                    "--set",
                    "load_optimizer_state=false",
                ]
            )
        )

        self.assertEqual(config.initial_checkpoint_path, "runs/medium/best.pt")
        self.assertFalse(config.load_optimizer_state)


if __name__ == "__main__":
    unittest.main()
