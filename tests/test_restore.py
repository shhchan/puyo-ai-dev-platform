import tempfile
import unittest
from pathlib import Path

try:
    import torch

    from agents.versus_ppo import VersusPPOConfig, train_versus_ppo
    from train.restore import RestoreError, load_training_checkpoint, state_hash

    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "training restore dependencies are not installed")
class TestTrainingRestore(unittest.TestCase):
    def _config(self, root: Path, run_id: str, *, total_timesteps: int, resume_checkpoint_path: str = ""):
        return VersusPPOConfig(
            seed=11,
            total_timesteps=total_timesteps,
            num_envs=1,
            num_steps=2,
            minibatch_size=2,
            max_episode_steps=2,
            log_dir=str(root),
            run_id=run_id,
            checkpoint_interval_updates=0,
            keep_best_checkpoint=False,
            resume_checkpoint_path=resume_checkpoint_path,
        )

    def test_versus_exact_resume_matches_continuous_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = train_versus_ppo(self._config(root, "base", total_timesteps=4))
            resumed = train_versus_ppo(
                self._config(
                    root,
                    "resumed",
                    total_timesteps=8,
                    resume_checkpoint_path=base["checkpoint_path"],
                )
            )
            continuous = train_versus_ppo(self._config(root, "continuous", total_timesteps=8))

            resumed_checkpoint = load_training_checkpoint(
                resumed["checkpoint_path"],
                expected_trainer_name="versus_ppo",
                require_exact=True,
            )
            continuous_checkpoint = torch.load(
                continuous["checkpoint_path"],
                map_location="cpu",
                weights_only=False,
            )

            self.assertEqual(resumed_checkpoint["global_step"], 8)
            self.assertEqual(continuous_checkpoint["global_step"], 8)
            for key in (
                "model_state_dict",
                "optimizer_state_dict",
                "episode_scores",
                "episode_returns",
                "episode_wins",
                "episode_lengths",
            ):
                self.assertEqual(
                    state_hash(continuous_checkpoint.get(key)),
                    state_hash(resumed_checkpoint.get(key)),
                )

    def test_exact_resume_rejects_config_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = train_versus_ppo(self._config(root, "base", total_timesteps=4))
            config = self._config(
                root,
                "bad-resume",
                total_timesteps=8,
                resume_checkpoint_path=base["checkpoint_path"],
            )
            config.num_steps = 1

            with self.assertRaises(RestoreError):
                train_versus_ppo(config)


if __name__ == "__main__":
    unittest.main()
