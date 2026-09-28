"""Compare batch reachability with independent target searches and real placement."""
import copy
import pickle
import random
import unittest

from puyo_env.action_planner import plan_placement_action
from puyo_env.actions import PLACEMENT_ACTIONS
from puyo_env.realtime_ai import nextgen_authoritative_action_mask, realtime_reachable_action_mask
from src.core.constants import Direction, PuyoColor
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo


class ReachableMaskBatchTests(unittest.TestCase):
    def assert_matches_individual_searches(self, sim, budget=2000):
        before = pickle.dumps(sim.game)
        expected = [plan_placement_action(sim, action, max_expanded_states=budget).reachable
                    for action in PLACEMENT_ACTIONS]
        actual = realtime_reachable_action_mask(sim, max_expanded_states=budget)
        self.assertEqual(expected, actual.tolist())
        self.assertEqual(before, pickle.dumps(sim.game), "mask calculation mutated live state")

    def test_empty_and_tight_boards_match_each_target_at_budget_boundary(self):
        rng = random.Random(273)
        for heights in ([0]*6, [11, 0, 0, 11, 0, 0], [2, 6, 1, 8, 3, 10],
                        *[[rng.randrange(11) for _ in range(6)] for _ in range(5)]):
            sim = HeadlessPuyoSimulator(seed=55)
            for x, height in enumerate(heights):
                for y in range(height):
                    sim.game.field.place_puyo(x, y, Puyo(PuyoColor.RED))
            for budget in (0, 8, 50, 2000):
                with self.subTest(heights=heights, budget=budget):
                    self.assert_matches_individual_searches(sim, budget)

    def test_falling_pair_rotation_counters_and_changed_board_recompute(self):
        sim = HeadlessPuyoSimulator(seed=55)
        for x, y, rotation, count in ((2, 4, Direction.UP, 0),
                                      (0, 1, Direction.RIGHT, 1),
                                      (5, 2, Direction.LEFT, 0)):
            sim.game.puyo_x, sim.game.puyo_y = x, y
            sim.game.puyo_rot, sim.game.blocked_rotate_input_count = rotation, count
            sim.game.vertical_interpolation_progress = 0.5
            sim.game.floor_kick_horizontal_grace = True
            self.assert_matches_individual_searches(sim)
        prior = nextgen_authoritative_action_mask(sim)
        for y in range(12):
            sim.game.field.place_puyo(3, y, Puyo(PuyoColor.BLUE))
        self.assert_matches_individual_searches(sim)
        self.assertNotEqual(prior, nextgen_authoritative_action_mask(sim))

    def test_noncontrol_and_gameover_have_no_reachable_actions(self):
        sim = HeadlessPuyoSimulator(seed=55)
        for state, game_over in (("ready", False), ("animate", False), ("control", True)):
            sim.game.state, sim.game.game_over = state, game_over
            self.assertFalse(realtime_reachable_action_mask(sim).any())

    def test_authoritative_mask_still_intersects_placement_legality(self):
        sim = HeadlessPuyoSimulator(seed=55)
        sim.game.puyo_y = 2
        reachable = realtime_reachable_action_mask(sim)
        legal = set(copy.deepcopy(sim).legal_actions())
        self.assertEqual(nextgen_authoritative_action_mask(sim),
                         tuple(bool(reachable[i] and action in legal)
                               for i, action in enumerate(PLACEMENT_ACTIONS)))
