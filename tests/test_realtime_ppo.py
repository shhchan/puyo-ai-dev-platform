import json
import tempfile
import unittest
from pathlib import Path

import torch

from agents.realtime_ppo import (
    RealtimePPOConfig,
    RealtimeRolloutAdapter,
    compute_gae,
    stack_realtime_masks,
    stack_realtime_observations,
    train_realtime_ppo,
    validate_realtime_training_checkpoint,
)
from puyo_env.actions import NUM_ACTIONS
from train.artifacts import attach_checkpoint_schema, validate_artifact_manifest
from train.restore import RestoreError, load_training_checkpoint


class TestRealtimePPO(unittest.TestCase):
    def _config(self, root: Path, *, total_timesteps: int = 4, resume_checkpoint_path: str = ""):
        return RealtimePPOConfig(
            seed=3,
            total_timesteps=total_timesteps,
            num_envs=1,
            num_steps=2,
            update_epochs=1,
            minibatch_size=2,
            max_ticks=60,
            log_dir=str(root),
            run_name="realtime-test",
            run_id=f"run-{total_timesteps}",
            checkpoint_interval_updates=0,
            keep_best_checkpoint=False,
            eval_games=1,
            eval_max_ticks=40,
            decision_tick_limit=120,
            resume_checkpoint_path=resume_checkpoint_path,
        )

    def test_rollout_adapter_shapes_masks_and_step(self):
        cfg = RealtimePPOConfig(seed=5, num_envs=2, num_steps=2, max_ticks=60, decision_tick_limit=120)
        adapter = RealtimeRolloutAdapter(cfg)
        try:
            obs = stack_realtime_observations(adapter.observations, "cpu")
            masks = stack_realtime_masks(adapter.infos, "cpu")

            self.assertEqual(obs["board"].shape[0], 2)
            self.assertEqual(masks.shape, (2, NUM_ACTIONS))
            actions = [int(torch.nonzero(mask).flatten()[0].item()) for mask in masks]

            step = adapter.step(actions)

            self.assertEqual(len(step.rewards), 2)
            self.assertEqual(len(step.dones), 2)
            self.assertIn("trainer_step_metrics", adapter.infos[0])
        finally:
            adapter.close()

    def test_compute_gae_shapes(self):
        rewards = torch.ones((3, 2))
        dones = torch.zeros((3, 2))
        values = torch.zeros((3, 2))
        next_done = torch.zeros(2)
        next_value = torch.zeros(2)

        advantages, returns = compute_gae(
            rewards,
            dones,
            values,
            next_done,
            next_value,
            gamma=0.99,
            gae_lambda=0.95,
        )

        self.assertEqual(advantages.shape, rewards.shape)
        self.assertEqual(returns.shape, rewards.shape)
        self.assertTrue(torch.isfinite(advantages).all())

    def test_training_checkpoint_manifest_and_resume_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = train_realtime_ppo(self._config(root, total_timesteps=4))
            checkpoint = load_training_checkpoint(
                first["checkpoint_path"],
                expected_trainer_name="realtime_ppo",
                require_exact=True,
            )
            self.assertEqual(checkpoint["global_step"], 4)
            self.assertEqual(checkpoint["realtime_policy"]["policy_contract"], "realtime_native")

            manifest_path = Path(first["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(validate_artifact_manifest(manifest, run_dir=manifest_path.parent), [])
            validate_realtime_training_checkpoint(
                first["checkpoint_path"],
                manifest_path=first["manifest_path"],
                require_exact=True,
            )

            resumed = train_realtime_ppo(
                self._config(
                    root,
                    total_timesteps=8,
                    resume_checkpoint_path=first["checkpoint_path"],
                )
            )
            resumed_checkpoint = load_training_checkpoint(
                resumed["checkpoint_path"],
                expected_trainer_name="realtime_ppo",
                require_exact=True,
            )
            self.assertEqual(resumed_checkpoint["global_step"], 8)
            self.assertEqual(
                resumed_checkpoint["checkpoint_schema"]["parent_checkpoint_path"],
                first["checkpoint_path"],
            )

    def test_turn_based_checkpoint_is_rejected_for_realtime_training(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "turn.pt"
            payload = attach_checkpoint_schema(
                {
                    "model_state_dict": {"cnn.0.weight": torch.zeros((1, 1, 1, 1))},
                    "optimizer_state_dict": {},
                    "config": {},
                    "rng_state": {},
                    "trainer_state": {},
                },
                trainer_name="versus_ppo",
                run_id="turn",
                checkpoint_kind="latest",
                global_step=0,
                config={},
                git_commit="test",
                seed=1,
            )
            torch.save(payload, checkpoint_path)

            with self.assertRaises(RestoreError):
                validate_realtime_training_checkpoint(checkpoint_path, require_exact=True)


if __name__ == "__main__":
    unittest.main()
