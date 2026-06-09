import copy
import unittest

from agents.beam_search import BeamSearchConfig, BeamSearchPolicy, evaluate_board
from puyo_env.actions import action_to_placement, legal_action_mask
from src.core.constants import PuyoColor
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo
from selfplay.policies import make_policy


class TestBeamSearchPolicy(unittest.TestCase):
    def test_config_rejects_invalid_search_size(self):
        with self.assertRaises(ValueError):
            BeamSearchConfig(depth=0)
        with self.assertRaises(ValueError):
            BeamSearchConfig(width=0)
        with self.assertRaises(ValueError):
            BeamSearchConfig(scenarios=7)

    def test_policy_returns_deterministic_legal_action(self):
        simulator = HeadlessPuyoSimulator(seed=7)
        policy = BeamSearchPolicy(BeamSearchConfig(depth=2, width=8))
        info = {"action_mask": legal_action_mask(simulator), "simulator": simulator}

        action_a = policy.select_action({}, info)
        action_b = policy.select_action({}, info)

        self.assertTrue(info["action_mask"][action_a])
        self.assertEqual(action_a, action_b)
        self.assertIsNotNone(policy.last_diagnostics)
        self.assertGreater(policy.last_diagnostics.expanded_nodes, 0)

    def test_policy_factory_builds_beam_policy(self):
        policy = make_policy("beam", beam_depth=2, beam_width=8, beam_scenarios=1)

        self.assertIsInstance(policy, BeamSearchPolicy)
        self.assertEqual(policy.config.depth, 2)

    def test_policy_takes_available_chain(self):
        simulator = HeadlessPuyoSimulator(seed=3)
        for x in range(3):
            simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
        simulator.game.current_puyo_1 = Puyo(PuyoColor.RED)
        simulator.game.current_puyo_2 = Puyo(PuyoColor.BLUE)
        policy = BeamSearchPolicy(BeamSearchConfig(depth=1, width=22, minimum_chain_count=1))
        info = {"action_mask": legal_action_mask(simulator), "simulator": simulator}

        action = policy.select_action({}, info)
        result = copy.deepcopy(simulator).step(action_to_placement(action))

        self.assertEqual(result.chain_count, 1)

    def test_board_evaluation_rewards_connected_colors(self):
        isolated = HeadlessPuyoSimulator(seed=1)
        connected = HeadlessPuyoSimulator(seed=1)
        isolated.game.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
        isolated.game.field.place_puyo(5, 0, Puyo(PuyoColor.RED))
        connected.game.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
        connected.game.field.place_puyo(1, 0, Puyo(PuyoColor.RED))

        self.assertGreater(evaluate_board(connected.game), evaluate_board(isolated.game))


if __name__ == "__main__":
    unittest.main()
