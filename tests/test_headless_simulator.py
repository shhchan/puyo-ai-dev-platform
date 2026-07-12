import unittest

from src.core.constants import Direction, PuyoColor
from src.core.game import GameState
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo


class TestHeadlessSimulator(unittest.TestCase):
    def _sim_with_pair(self, axis_color, child_color):
        game = GameState(seed=0)
        game.spawn_puyo()
        game.current_puyo_1 = Puyo(axis_color)
        game.current_puyo_2 = Puyo(child_color)
        return HeadlessPuyoSimulator(game_state=game)

    def test_seed_reproduces_tsumo_queue_and_simulation(self):
        actions = [
            (2, Direction.UP),
            (3, Direction.RIGHT),
            (1, Direction.DOWN),
            (4, Direction.LEFT),
        ]
        sim_a = HeadlessPuyoSimulator(seed=1234)
        sim_b = HeadlessPuyoSimulator(seed=1234)

        queue_a = [(a.color, b.color) for a, b in sim_a.game.next_puyo_queue]
        queue_b = [(a.color, b.color) for a, b in sim_b.game.next_puyo_queue]
        self.assertEqual(queue_a, queue_b)

        results_a = [sim_a.step(action) for action in actions]
        results_b = [sim_b.step(action) for action in actions]

        self.assertEqual([result.score_delta for result in results_a], [result.score_delta for result in results_b])
        self.assertEqual(sim_a.game.score, sim_b.game.score)
        self.assertEqual(sim_a.game.field.to_color_grid(), sim_b.game.field.to_color_grid())

    def test_legal_actions_start_with_22_basic_placements(self):
        sim = HeadlessPuyoSimulator(seed=1)

        actions = sim.legal_actions()

        self.assertEqual(len(actions), 22)

    def test_one_chain_golden_score_is_40(self):
        sim = self._sim_with_pair(PuyoColor.RED, PuyoColor.RED)
        sim.game.field.place_puyo(1, 0, Puyo(PuyoColor.RED))
        sim.game.field.place_puyo(1, 1, Puyo(PuyoColor.RED))

        result = sim.step((2, Direction.UP))

        self.assertTrue(result.valid)
        self.assertEqual(result.score_delta, 40)
        self.assertEqual(result.chain_count, 1)
        self.assertEqual(result.chains[0].vanished_count, 4)
        self.assertEqual(result.chains[0].score, 40)
        self.assertEqual(result.chains[0].board, ())
        self.assertEqual(result.placement_board, ())
        self.assertTrue(result.all_clear_achieved)
        self.assertTrue(result.all_clear_bonus_pending)
        self.assertFalse(result.all_clear_bonus_consumed)
        self.assertEqual(result.all_clear_bonus_score, 0)

    def test_all_clear_bonus_is_consumed_once_by_next_chain(self):
        sim = self._sim_with_pair(PuyoColor.RED, PuyoColor.RED)
        sim.game.field.place_puyo(1, 0, Puyo(PuyoColor.RED))
        sim.game.field.place_puyo(1, 1, Puyo(PuyoColor.RED))
        first = sim.step((2, Direction.UP))
        self.assertTrue(first.all_clear_bonus_pending)

        sim.game.current_puyo_1 = Puyo(PuyoColor.BLUE)
        sim.game.current_puyo_2 = Puyo(PuyoColor.BLUE)
        sim.game.field.place_puyo(1, 0, Puyo(PuyoColor.BLUE))
        sim.game.field.place_puyo(1, 1, Puyo(PuyoColor.BLUE))
        sim.game.field.place_puyo(5, 0, Puyo(PuyoColor.RED))
        second = sim.step((2, Direction.UP))

        self.assertEqual(second.score_delta, 2140)
        self.assertEqual(second.chains[0].all_clear_bonus_score, 2100)
        self.assertTrue(second.all_clear_bonus_consumed)
        self.assertEqual(second.all_clear_bonus_score, 2100)
        self.assertFalse(second.all_clear_achieved)
        self.assertFalse(second.all_clear_bonus_pending)

        sim.game.current_puyo_1 = Puyo(PuyoColor.GREEN)
        sim.game.current_puyo_2 = Puyo(PuyoColor.GREEN)
        sim.game.field.place_puyo(1, 0, Puyo(PuyoColor.GREEN))
        sim.game.field.place_puyo(1, 1, Puyo(PuyoColor.GREEN))
        third = sim.step((2, Direction.UP))

        self.assertEqual(third.score_delta, 40)
        self.assertEqual(third.chains[0].all_clear_bonus_score, 0)
        self.assertFalse(third.all_clear_bonus_consumed)

    def test_two_chain_golden_score_is_360(self):
        game = GameState(seed=0)
        for coord in {(0, 0), (1, 0), (1, 1), (2, 0)}:
            game.field.place_puyo(*coord, Puyo(PuyoColor.RED))
        for coord in {(0, 1), (1, 2), (2, 1), (3, 1)}:
            game.field.place_puyo(*coord, Puyo(PuyoColor.BLUE))

        chains = game.resolve_chains_synchronously(capture_visuals=True)

        self.assertEqual(game.score, 360)
        self.assertEqual(len(chains), 2)
        self.assertEqual([chain["score"] for chain in chains], [40, 320])
        self.assertEqual([chain["all_clear_bonus_score"] for chain in chains], [0, 0])
        self.assertTrue(all(chain["board"] for chain in chains))
        self.assertEqual(
            {chains[0]["board"][y][x] for x, y in chains[0]["vanished"]},
            {PuyoColor.RED},
        )
        self.assertEqual(
            {chains[1]["board"][y][x] for x, y in chains[1]["vanished"]},
            {PuyoColor.BLUE},
        )

    def test_simultaneous_two_color_clear_golden_score_is_240(self):
        game = GameState(seed=0)
        red_group = {(0, 0), (1, 0), (0, 1), (1, 1)}
        blue_group = {(4, 0), (5, 0), (4, 1), (5, 1)}
        for coord in red_group:
            game.field.place_puyo(*coord, Puyo(PuyoColor.RED))
        for coord in blue_group:
            game.field.place_puyo(*coord, Puyo(PuyoColor.BLUE))

        chains = game.resolve_chains_synchronously()

        self.assertEqual(game.score, 240)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["vanished_count"], 8)
        self.assertEqual(chains[0]["score"], 240)

    def test_ojama_adjacent_to_vanishing_group_is_cleared_without_score(self):
        game = GameState(seed=0)
        red_group = {(0, 0), (1, 0), (0, 1), (1, 1)}
        for coord in red_group:
            game.field.place_puyo(*coord, Puyo(PuyoColor.RED))
        game.field.place_puyo(2, 0, Puyo(PuyoColor.OJAMA))

        chains = game.resolve_chains_synchronously()

        self.assertEqual(game.score, 40)
        self.assertEqual(chains[0]["vanished_count"], 4)
        self.assertTrue(game.field.get_puyo(2, 0).is_empty())

    def test_headless_step_reports_invalid_placement(self):
        sim = HeadlessPuyoSimulator(seed=0)

        result = sim.step((99, Direction.UP))

        self.assertFalse(result.valid)
        self.assertEqual(result.score_delta, 0)

    def test_spawn_after_headless_step_detects_choke_point(self):
        sim = self._sim_with_pair(PuyoColor.RED, PuyoColor.RED)
        for y in range(12):
            color = PuyoColor.BLUE if y % 2 == 0 else PuyoColor.GREEN
            sim.game.field.place_puyo(2, y, Puyo(color))

        result = sim.step((0, Direction.UP))

        self.assertTrue(result.valid)
        self.assertTrue(result.game_over)
        self.assertEqual(sim.game.state, "gameover")


if __name__ == "__main__":
    unittest.main()
