import unittest

try:
    from agents.strategy_workers import (
        FixedProfilePolicy,
        StrategyOrchestrator,
        board_danger,
        default_worker_profiles,
        estimate_immediate_threat,
        build_tactical_context,
        objective_for_profile,
        objective_from_v1_profile,
        smoke_worker_profiles,
        scaled_worker_profiles,
    )
    from puyo_env.obs import encode_observation
    from puyo_env.actions import legal_action_mask
    from src.core.headless import HeadlessPuyoSimulator

    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "strategy worker dependencies are not installed")
class TestStrategyWorkers(unittest.TestCase):
    def _state(self, seed=1):
        simulator = HeadlessPuyoSimulator(seed=seed)
        mask = legal_action_mask(simulator)
        observation = encode_observation(simulator, step_count=0, max_steps=40)
        info = {"simulator": simulator, "action_mask": mask}
        return simulator, observation, info

    def test_default_profiles_cover_six_tactical_strategies(self):
        profiles = default_worker_profiles()

        self.assertEqual([profile.profile_id for profile in profiles], [0, 1, 2, 3, 4, 5])
        self.assertEqual(
            [profile.strategy for profile in profiles],
            ["build_large", "build_budget", "punish", "counter", "fire_max", "survival"],
        )

    def test_each_worker_returns_a_legal_proposal(self):
        simulator, observation, info = self._state()
        orchestrator = StrategyOrchestrator(smoke_worker_profiles())

        for profile in orchestrator.profiles:
            proposal = orchestrator.propose(profile.profile_id, observation, info)
            self.assertTrue(info["action_mask"][proposal.action])
            self.assertEqual(proposal.profile_id, profile.profile_id)
            self.assertGreaterEqual(proposal.expanded_nodes, 1)
            self.assertGreaterEqual(proposal.elapsed_seconds, 0.0)
            self.assertEqual(proposal.objective_dict["schema_version"], "search-objective-v1")
            self.assertIn("achieved", proposal.objective_result_dict)
            self.assertIs(simulator, info["simulator"])

    def test_fixed_profile_policy_exposes_last_proposal(self):
        _, observation, info = self._state(seed=7)
        policy = FixedProfilePolicy(4, smoke_worker_profiles())

        action = policy.select_action(observation, info)

        self.assertEqual(action, policy.last_proposal.action)
        self.assertEqual(policy.last_proposal.strategy, "fire_max")

    def test_tactical_context_exposes_deadline_and_counter_deficit(self):
        simulator, observation, info = self._state(seed=11)
        info.update(
            {
                "opponent_simulator": simulator,
                "incoming_ojama": 8,
                "incoming_turns": 2,
                "incoming_ticks": 34,
                "opponent_pending_ojama": 0,
            }
        )

        tactical = build_tactical_context(info)

        self.assertEqual(tactical.incoming_attack, 8)
        self.assertEqual(tactical.incoming_deadline, 2)
        self.assertEqual(tactical.incoming_deadline_ticks, 34)
        self.assertEqual(tactical.counter_deficit, tactical.counter_target - tactical.max_return_by_deadline)

    def test_objective_schema_covers_profiles_and_v1_compatibility(self):
        _, _, info = self._state(seed=13)
        tactical = build_tactical_context(info)
        profiles = smoke_worker_profiles()

        build = objective_for_profile(tactical, profiles[0])
        punish = objective_for_profile(tactical, profiles[2])
        counter = objective_for_profile(tactical, profiles[3])
        compat = objective_from_v1_profile(profiles[3], tactical)

        self.assertEqual(build.kind, "build")
        self.assertGreaterEqual(build.target_chain, profiles[0].minimum_chain_count)
        self.assertEqual(punish.kind, "punish")
        self.assertGreater(punish.target_attack, 0)
        self.assertEqual(counter.kind, "counter")
        self.assertEqual(counter.fallback_strategy, "survival")
        self.assertEqual(compat.to_dict(), counter.to_dict())

    def test_impossible_deadline_is_reported_in_objective_result(self):
        simulator, observation, info = self._state(seed=17)
        info.update(
            {
                "opponent_simulator": simulator,
                "incoming_ojama": 99,
                "incoming_turns": 1,
                "opponent_pending_ojama": 0,
            }
        )
        orchestrator = StrategyOrchestrator(smoke_worker_profiles())

        proposal = orchestrator.propose(3, observation, info)

        self.assertFalse(proposal.objective_result.possible_by_deadline)
        self.assertIn("impossible_by_deadline", proposal.objective_result.miss_reasons)

    def test_training_profile_scaling_preserves_ids_and_names(self):
        profiles = default_worker_profiles()

        scaled = scaled_worker_profiles(profiles, depth_scale=0.5, width_scale=0.25)

        self.assertEqual([profile.profile_id for profile in scaled], list(range(6)))
        self.assertEqual([profile.name for profile in scaled], [profile.name for profile in profiles])
        self.assertLess(scaled[0].depth, profiles[0].depth)
        self.assertLess(scaled[0].width, profiles[0].width)

    def test_threat_and_danger_are_bounded(self):
        simulator, _, _ = self._state(seed=9)

        chain, attack = estimate_immediate_threat(simulator)

        self.assertGreaterEqual(chain, 0)
        self.assertGreaterEqual(attack, 0)
        self.assertGreaterEqual(board_danger(simulator.game), 0.0)
        self.assertLessEqual(board_danger(simulator.game), 1.0)


if __name__ == "__main__":
    unittest.main()
