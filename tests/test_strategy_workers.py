import unittest

try:
    from agents.strategy_workers import (
        FixedProfilePolicy,
        StrategyOrchestrator,
        board_danger,
        default_worker_profiles,
        estimate_immediate_threat,
        smoke_worker_profiles,
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

    def test_default_profiles_cover_four_strategies(self):
        profiles = default_worker_profiles()

        self.assertEqual([profile.profile_id for profile in profiles], [0, 1, 2, 3])
        self.assertEqual(
            [profile.strategy for profile in profiles],
            ["large_chain", "quick_attack", "fire", "survival"],
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
            self.assertIs(simulator, info["simulator"])

    def test_fixed_profile_policy_exposes_last_proposal(self):
        _, observation, info = self._state(seed=7)
        policy = FixedProfilePolicy(2, smoke_worker_profiles())

        action = policy.select_action(observation, info)

        self.assertEqual(action, policy.last_proposal.action)
        self.assertEqual(policy.last_proposal.strategy, "fire")

    def test_threat_and_danger_are_bounded(self):
        simulator, _, _ = self._state(seed=9)

        chain, attack = estimate_immediate_threat(simulator)

        self.assertGreaterEqual(chain, 0)
        self.assertGreaterEqual(attack, 0)
        self.assertGreaterEqual(board_danger(simulator.game), 0.0)
        self.assertLessEqual(board_danger(simulator.game), 1.0)


if __name__ == "__main__":
    unittest.main()
