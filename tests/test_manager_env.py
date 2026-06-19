import unittest
import tempfile
from pathlib import Path

try:
    from agents.strategy_workers import (
        baseline_search_controls,
        default_search_controls,
        default_tactical_options,
        smoke_worker_profiles,
    )
    from puyo_env.manager_env import MANAGER_FEATURE_DIM, ManagerSelfPlayEnv, manager_feature_dim
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

        action = 4 * env.search_control_count
        next_observation, _, _, _, next_info = env.step(action)

        self.assertEqual(observation["manager_features"].shape, (MANAGER_FEATURE_DIM,))
        self.assertEqual(next_observation["manager_features"].shape, (MANAGER_FEATURE_DIM,))
        self.assertEqual(next_info["manager_profile_id"], 4)
        self.assertEqual(next_info["manager_search_control_id"], 0)
        self.assertEqual(next_info["search_proposal"].strategy, "fire_max")
        self.assertEqual(next_info["search_objective"]["schema_version"], "search-objective-v1")
        self.assertIn("achieved", next_info["search_objective_result"])
        self.assertEqual(next_info["search_control"]["schema_version"], "search-control-v1")
        self.assertEqual(next_info["search_plan"]["schema_version"], "n-turn-plan-v1")
        self.assertTrue(next_info["search_plan_id"])
        self.assertEqual(sum(next_info["manager_profile_counts"]), 1)
        self.assertEqual(sum(next_info["manager_search_control_counts"]), 1)
        env.close()

    def test_manager_observation_contains_opponent_state(self):
        env = ManagerSelfPlayEnv(seed=5, max_steps=1, profiles=smoke_worker_profiles())
        _, info = env.reset(seed=5)

        self.assertIn("opponent_simulator", info)
        self.assertIn("opponent_pending_ojama", info)
        self.assertEqual(len(info["action_mask"]), 6 * len(default_search_controls()))
        env.close()

    def test_curriculum_stage_expands_available_profiles(self):
        env = ManagerSelfPlayEnv(seed=5, max_steps=1, profiles=smoke_worker_profiles())
        env.set_curriculum_stage("safe_build")
        _, info = env.reset(seed=5)

        self.assertTrue(info["action_mask"][0])
        punish_action = 2 * env.search_control_count
        counter_action = 3 * env.search_control_count
        self.assertFalse(info["action_mask"][punish_action])
        self.assertFalse(info["action_mask"][counter_action])
        env.set_curriculum_stage("counter")
        self.assertTrue(env._manager_action_mask().all())
        env.close()

    def test_latency_budget_masks_expensive_search_controls(self):
        env = ManagerSelfPlayEnv(
            seed=5,
            max_steps=1,
            profiles=smoke_worker_profiles(),
            max_search_latency_ms=30.0,
        )
        _, info = env.reset(seed=5)

        broad_value_action = 2

        self.assertFalse(info["action_mask"][broad_value_action])
        env.close()

    def test_baseline_search_controls_keep_fixed_worker_action_space(self):
        env = ManagerSelfPlayEnv(
            seed=5,
            max_steps=1,
            profiles=smoke_worker_profiles(),
            search_controls=baseline_search_controls(),
        )
        _, info = env.reset(seed=5)

        self.assertEqual(env.action_space.n, 6)
        self.assertEqual(len(info["action_mask"]), 6)
        env.close()

    def test_option_strategy_space_executes_non_fixed_option(self):
        options = default_tactical_options()
        env = ManagerSelfPlayEnv(
            seed=6,
            max_steps=2,
            profiles=smoke_worker_profiles(),
            tactical_options=options,
            strategy_space="option",
        )
        observation, info = env.reset(seed=6)

        action = 4 * env.search_control_count
        next_observation, _, _, _, next_info = env.step(action)

        self.assertEqual(env.action_space.n, len(options) * len(default_search_controls()))
        self.assertEqual(
            observation["manager_features"].shape,
            (manager_feature_dim(len(options), len(default_search_controls())),),
        )
        self.assertEqual(next_observation["manager_features"].shape, observation["manager_features"].shape)
        self.assertEqual(next_info["manager_strategy_space"], "option")
        self.assertEqual(next_info["manager_option_id"], 4)
        self.assertEqual(next_info["tactical_option"]["name"], "early_release")
        self.assertEqual(sum(next_info["manager_option_counts"]), 1)
        self.assertEqual(next_info["search_proposal"].profile_name, "early_release")
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
            action_dim=len(profiles) * len(default_search_controls()),
        )
        payload = {
            **manager_checkpoint_metadata(profiles, default_search_controls()),
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

    @unittest.skipUnless(TORCH_AVAILABLE, "torch is not installed")
    def test_option_checkpoint_round_trip_returns_legal_worker_action(self):
        profiles = smoke_worker_profiles()
        options = default_tactical_options()
        env = ManagerSelfPlayEnv(seed=8, max_steps=2, profiles=profiles, strategy_space="option")
        raw_observations, raw_infos = env.versus_env.reset(seed=8)
        board_shape = raw_observations["player_0"]["board"].shape
        model = PuyoActorCritic(
            board_shape=board_shape,
            vector_dim=env.manager_vector_dim,
            action_dim=len(options) * len(default_search_controls()),
        )
        payload = {
            **manager_checkpoint_metadata(
                profiles,
                default_search_controls(),
                tactical_options=options,
                strategy_space="option",
                decision_count=len(options),
            ),
            "model_state_dict": model.state_dict(),
            "board_shape": board_shape,
        }
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "manager-option.pt"
            torch.save(payload, checkpoint)
            policy = StrategyManagerPolicy(checkpoint)
            action = policy.select_action(raw_observations["player_0"], raw_infos["player_0"])

        self.assertTrue(raw_infos["player_0"]["action_mask"][action])
        self.assertIsNotNone(policy.current_profile_name)
        self.assertIn("tactical_option", policy.tactical_diagnostics)
        env.close()


if __name__ == "__main__":
    unittest.main()
