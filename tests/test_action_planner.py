import unittest

from puyo_env.action_planner import execute_planned_placement, plan_placement_action
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.constants import Direction
from src.core.headless import HeadlessPuyoSimulator, PlacementAction


class TestActionPlanner(unittest.TestCase):
    def test_all_empty_board_actions_execute_to_same_field_as_placement_simulator(self):
        for action in PLACEMENT_ACTIONS:
            with self.subTest(action=action):
                planner_source = HeadlessPuyoSimulator(seed=123)
                plan = plan_placement_action(planner_source, action)
                self.assertTrue(plan.reachable, plan.reason)

                expected = HeadlessPuyoSimulator(seed=123)
                expected_result = expected.step(action)
                actual = execute_planned_placement(HeadlessPuyoSimulator(seed=123), plan)

                self.assertTrue(expected_result.valid)
                self.assertEqual(
                    expected.game.field.to_color_grid(),
                    actual.game.field.to_color_grid(),
                )

    def test_unreachable_action_reports_failure(self):
        sim = HeadlessPuyoSimulator(seed=123)

        plan = plan_placement_action(sim, PlacementAction(99, Direction.UP))

        self.assertFalse(plan.reachable)
        self.assertEqual(plan.inputs, ())
        self.assertIn("not legal", plan.reason)


if __name__ == "__main__":
    unittest.main()
