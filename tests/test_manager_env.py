import unittest
import tempfile
from pathlib import Path

try:
    from agents.strategy_workers import smoke_worker_profiles
    from puyo_env.manager_env import MANAGER_FEATURE_DIM, ManagerSelfPlayEnv
    from selfplay.policies import RandomPolicy

    AVAILABLE = True
except ImportError:
    AVAILABLE = False

try:
    import torch

    from agents.networks import PuyoActorCritic
    from agents.strategy_manager import StrategyManagerPolicy, manager_checkpoint_metadata
    from puyo_env.manager_env import MANAGER_VECTOR_DIM

    TORCH_AVAILABLE = True
except (ImportError, OSError):
    TORCH_AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "manager dependencies are not installed")
class TestManagerEnvironment(unittest.TestCase):
    def test_manager_step_executes_selected_profile(self):
        env = ManagerSelfPlayEnv(
            seed=3,
            max_steps=2,
            opponent_policy=RandomPolicy(seed=4),
            profiles=smoke_worker_profiles(),
        )
        observation, info = env.reset(seed=3)

        next_observation, _, _, _, next_info = env.step(4)

        self.assertEqual(observation["manager_features"].shape, (MANAGER_FEATURE_DIM,))
        self.assertEqual(next_observation["manager_features"].shape, (MANAGER_FEATURE_DIM,))
        self.assertEqual(next_info["manager_profile_id"], 4)
        self.assertEqual(next_info["search_proposal"].strategy, "fire_max")
        self.assertEqual(sum(next_info["manager_profile_counts"]), 1)
        env.close()

    def test_manager_observation_contains_opponent_state(self):
        env = ManagerSelfPlayEnv(seed=5, max_steps=1, profiles=smoke_worker_profiles())
        _, info = env.reset(seed=5)

        self.assertIn("opponent_simulator", info)
        self.assertIn("opponent_pending_ojama", info)
        self.assertEqual(len(info["action_mask"]), 6)
        env.close()

    def test_curriculum_stage_expands_available_profiles(self):
        env = ManagerSelfPlayEnv(seed=5, max_steps=1, profiles=smoke_worker_profiles())
        env.set_curriculum_stage("safe_build")
        _, info = env.reset(seed=5)

        self.assertTrue(info["action_mask"][0])
        self.assertFalse(info["action_mask"][2])
        self.assertFalse(info["action_mask"][3])
        env.set_curriculum_stage("counter")
        self.assertTrue(env._manager_action_mask().all())
        env.close()

    @unittest.skipUnless(TORCH_AVAILABLE, "torch is not installed")
    def test_manager_checkpoint_round_trip_returns_legal_worker_action(self):
        profiles = smoke_worker_profiles()
        env = ManagerSelfPlayEnv(seed=7, max_steps=2, profiles=profiles)
        raw_observations, raw_infos = env.versus_env.reset(seed=7)
        board_shape = raw_observations["player_0"]["board"].shape
        model = PuyoActorCritic(
            board_shape=board_shape,
            vector_dim=MANAGER_VECTOR_DIM,
            action_dim=len(profiles),
        )
        payload = {
            **manager_checkpoint_metadata(profiles),
            "model_state_dict": model.state_dict(),
            "board_shape": board_shape,
        }
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "manager.pt"
            torch.save(payload, checkpoint)
            policy = StrategyManagerPolicy(checkpoint)
            action = policy.select_action(raw_observations["player_0"], raw_infos["player_0"])

        self.assertTrue(raw_infos["player_0"]["action_mask"][action])
        self.assertIsNotNone(policy.current_profile_name)
        env.close()


if __name__ == "__main__":
    unittest.main()
