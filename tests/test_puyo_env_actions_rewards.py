import unittest

from puyo_env.actions import NUM_ACTIONS, action_to_placement, choose_random_legal_action, legal_action_mask
from puyo_env.rewards import RewardConfig, single_player_reward
from src.core.constants import Direction
from src.core.headless import HeadlessPuyoSimulator


class TestPuyoEnvActionsRewards(unittest.TestCase):
    def test_action_table_has_basic_22_placements(self):
        self.assertEqual(NUM_ACTIONS, 22)

        placements = [action_to_placement(index) for index in range(NUM_ACTIONS)]

        self.assertIn((0, Direction.UP), [(action.axis_x, action.rotation) for action in placements])
        self.assertNotIn((0, Direction.LEFT), [(action.axis_x, action.rotation) for action in placements])
        self.assertNotIn((5, Direction.RIGHT), [(action.axis_x, action.rotation) for action in placements])

    def test_empty_board_action_mask_is_all_legal(self):
        sim = HeadlessPuyoSimulator(seed=1)

        mask = legal_action_mask(sim)

        self.assertEqual(len(mask), NUM_ACTIONS)
        self.assertTrue(all(mask))

    def test_random_legal_action_uses_mask(self):
        mask = [False] * NUM_ACTIONS
        mask[3] = True

        self.assertEqual(choose_random_legal_action(mask), 3)

    def test_reward_uses_score_chain_and_terminal_penalty(self):
        sim = HeadlessPuyoSimulator(seed=0)
        result = sim.step(action_to_placement(0))
        config = RewardConfig(game_over_penalty=10.0)

        reward = single_player_reward(result, config)

        self.assertGreaterEqual(reward, 0.01)


if __name__ == "__main__":
    unittest.main()
