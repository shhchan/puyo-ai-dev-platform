import unittest

from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.constants import Action, Direction, PuyoColor
from src.core.puyo import Puyo
from src.core.realtime import RealtimeHeadlessSimulator, TickInput


class TestRealtimeVersusMatch(unittest.TestCase):
    @staticmethod
    def _place_group(game, color, coords):
        for x, y in coords:
            game.field.place_puyo(x, y, Puyo(color))

    @staticmethod
    def _lock_pair(game, first, second, axis_x=2):
        game.current_puyo_1 = Puyo(first)
        game.current_puyo_2 = Puyo(second)
        game.puyo_x = axis_x
        game.puyo_y = game.find_landing_y(axis_x, Direction.UP)
        game.puyo_rot = Direction.UP
        game.lock_puyo()

    def _run_until_resolution(self, match):
        for _ in range(200):
            result = match.step()
            if any(
                event.type == "resolution_complete"
                for player_result in result.player_results.values()
                for event in player_result.events
            ):
                return result
        self.fail("realtime chain resolution did not complete")

    def test_players_advance_independently_on_same_match_clock(self):
        match = RealtimeVersusMatch(seed=123)

        match.step({"player_0": TickInput(press=(Action.DOWN,))})
        match.step({"player_0": TickInput(release=(Action.DOWN,))})

        player_0 = match.player_states["player_0"].simulator.game
        player_1 = match.player_states["player_1"].simulator.game
        self.assertEqual(player_0.puyo_y, 11)
        self.assertEqual(player_1.puyo_y, 12)

    def test_simultaneous_attacks_cancel_without_player_order_bias(self):
        match = RealtimeVersusMatch(seed=123, attack_delay_ticks=10)

        attacks = match.resolve_generated_attacks({"player_0": 8, "player_1": 3})

        self.assertEqual(attacks["player_0"], {"generated": 8, "canceled": 3, "outgoing": 5})
        self.assertEqual(attacks["player_1"], {"generated": 3, "canceled": 3, "outgoing": 0})
        self.assertEqual(match.player_states["player_1"].pending_ojama, 5)

    def test_due_ojama_waits_for_placement_boundary(self):
        match = RealtimeVersusMatch(seed=123, attack_delay_ticks=0)
        match.schedule_attack("player_0", 3, delay_ticks=0)
        game = match.player_states["player_1"].simulator.game
        active_pair = (game.current_puyo_1.color, game.current_puyo_2.color)
        board_before = game.field.to_color_grid()

        result = match.step()

        self.assertEqual(result.dropped_ojama["player_1"], 0)
        self.assertEqual(match.player_states["player_1"].pending_ojama, 3)
        self.assertEqual(game.field.to_color_grid(), board_before)
        self.assertEqual((game.current_puyo_1.color, game.current_puyo_2.color), active_pair)

        self._lock_pair(game, active_pair[0], active_pair[1])
        match.player_states["player_1"].simulator = RealtimeHeadlessSimulator(
            game_state=game,
            timing=match.timing,
        )
        result = self._run_until_resolution(match)

        self.assertEqual(result.dropped_ojama["player_1"], 3)
        self.assertEqual(match.player_states["player_1"].received_ojama_total, 3)
        self.assertEqual(match.player_states["player_1"].pending_ojama, 0)
        self.assertIsNotNone(game.current_puyo_1)
        self.assertIsNotNone(game.current_puyo_2)

    def test_due_ojama_overflow_waits_for_next_placement_boundary(self):
        match = RealtimeVersusMatch(seed=123, attack_delay_ticks=0)
        match.schedule_attack("player_0", 35, delay_ticks=0)
        game = match.player_states["player_1"].simulator.game

        self._lock_pair(game, PuyoColor.RED, PuyoColor.BLUE, axis_x=0)
        match.player_states["player_1"].simulator = RealtimeHeadlessSimulator(
            game_state=game,
            timing=match.timing,
        )
        first = self._run_until_resolution(match)

        self.assertEqual(first.dropped_ojama["player_1"], 30)
        self.assertEqual(match.player_states["player_1"].pending_ojama, 5)
        between = match.step()
        self.assertEqual(between.dropped_ojama["player_1"], 0)
        self.assertEqual(match.player_states["player_1"].pending_ojama, 5)

        self._lock_pair(game, PuyoColor.YELLOW, PuyoColor.PURPLE, axis_x=0)
        match.player_states["player_1"].simulator = RealtimeHeadlessSimulator(
            game_state=game,
            timing=match.timing,
        )
        second = self._run_until_resolution(match)

        self.assertEqual(second.dropped_ojama["player_1"], 5)
        self.assertEqual(match.player_states["player_1"].pending_ojama, 0)
        self.assertEqual(match.player_states["player_1"].received_ojama_total, 35)

    def test_resolution_attack_cancels_due_ojama_before_drop(self):
        match = RealtimeVersusMatch(seed=123, attack_delay_ticks=0)
        match.schedule_attack("player_0", 1, delay_ticks=0)
        state = match.player_states["player_1"]
        state.score_carry = 69
        game = state.simulator.game
        self._place_group(game, PuyoColor.RED, ((1, 0), (1, 1)))
        self._lock_pair(game, PuyoColor.RED, PuyoColor.RED)
        state.simulator = RealtimeHeadlessSimulator(game_state=game, timing=match.timing)

        result = self._run_until_resolution(match)

        self.assertEqual(result.generated_attacks["player_1"], 1)
        self.assertEqual(result.attack_diagnostics["player_1"]["canceled"], 1)
        self.assertEqual(result.dropped_ojama["player_1"], 0)
        self.assertEqual(state.pending_ojama, 0)
        self.assertEqual(state.received_ojama_total, 0)

    def test_boundary_drop_is_side_symmetric_and_deterministic(self):
        def run_match():
            match = RealtimeVersusMatch(seed=321, attack_delay_ticks=0)
            match.schedule_attack("player_0", 6, delay_ticks=0)
            match.schedule_attack("player_1", 6, delay_ticks=0)
            for agent in match.possible_agents:
                game = match.player_states[agent].simulator.game
                self._lock_pair(game, PuyoColor.RED, PuyoColor.BLUE, axis_x=0)
                match.player_states[agent].simulator = RealtimeHeadlessSimulator(
                    game_state=game,
                    timing=match.timing,
                )
            result = self._run_until_resolution(match)
            return match, result

        first_match, first = run_match()
        second_match, second = run_match()

        self.assertEqual(first.dropped_ojama, {"player_0": 6, "player_1": 6})
        self.assertEqual(
            [first_match.player_states[agent].pending_ojama for agent in first_match.possible_agents],
            [0, 0],
        )
        self.assertEqual(
            [
                first_match.player_states[agent].received_ojama_total
                for agent in first_match.possible_agents
            ],
            [6, 6],
        )
        self.assertEqual(first.snapshot_hash, second.snapshot_hash)
        self.assertEqual(first_match.state_hash(), second_match.state_hash())

    def test_score_carry_matches_chain_end_boundary_conversion(self):
        match = RealtimeVersusMatch(seed=123)

        generated = [
            match._attack_units_from_score("player_0", score_delta)
            for score_delta in (40, 29, 1, 71)
        ]

        self.assertEqual(generated, [0, 0, 1, 1])
        self.assertEqual(match.player_states["player_0"].score_carry, 1)

    def test_all_clear_bonus_is_reported_in_resolved_attack_diagnostics(self):
        match = RealtimeVersusMatch(seed=123, attack_delay_ticks=100)
        game_0 = match.player_states["player_0"].simulator.game
        self._place_group(game_0, PuyoColor.RED, ((1, 0), (1, 1)))
        self._lock_pair(game_0, PuyoColor.RED, PuyoColor.RED)
        match.player_states["player_0"].simulator = RealtimeHeadlessSimulator(
            game_state=game_0,
            timing=match.timing,
        )

        first = self._run_until_resolution(match)

        self.assertEqual(first.generated_attacks["player_0"], 0)
        self.assertEqual(
            first.attack_diagnostics["player_0"],
            {
                "generated": 0,
                "canceled": 0,
                "outgoing": 0,
                "attack_score_delta": 40,
                "all_clear_bonus_consumed": False,
                "all_clear_bonus_score": 0,
            },
        )
        self.assertTrue(game_0.all_clear_bonus_pending)
        self.assertEqual(match.player_states["player_0"].score_carry, 40)

        game_1 = match.player_states["player_1"].simulator.game
        self._place_group(game_0, PuyoColor.BLUE, ((1, 0), (1, 1)))
        self._place_group(game_0, PuyoColor.RED, ((5, 0),))
        self._lock_pair(game_0, PuyoColor.BLUE, PuyoColor.BLUE)
        self._place_group(
            game_1,
            PuyoColor.RED,
            ((0, 0), (0, 1), (1, 0), (1, 1)),
        )
        self._place_group(
            game_1,
            PuyoColor.BLUE,
            ((3, 0), (3, 1), (4, 0), (4, 1)),
        )
        self._lock_pair(game_1, PuyoColor.PURPLE, PuyoColor.YELLOW)
        match.player_states["player_0"].simulator = RealtimeHeadlessSimulator(
            game_state=game_0,
            timing=match.timing,
        )
        match.player_states["player_1"].simulator = RealtimeHeadlessSimulator(
            game_state=game_1,
            timing=match.timing,
        )
        match.schedule_attack("player_1", 5, delay_ticks=1000)

        second = self._run_until_resolution(match)

        self.assertEqual(
            second.attack_diagnostics["player_0"],
            {
                "generated": 31,
                "canceled": 8,
                "outgoing": 23,
                "attack_score_delta": 2140,
                "all_clear_bonus_consumed": True,
                "all_clear_bonus_score": 2100,
            },
        )
        self.assertEqual(
            second.attack_diagnostics["player_1"],
            {
                "generated": 3,
                "canceled": 3,
                "outgoing": 0,
                "attack_score_delta": 240,
                "all_clear_bonus_consumed": False,
                "all_clear_bonus_score": 0,
            },
        )
        self.assertEqual(match.player_states["player_0"].score_carry, 10)
        self.assertEqual(match.player_states["player_1"].pending_ojama, 23)


if __name__ == "__main__":
    unittest.main()
