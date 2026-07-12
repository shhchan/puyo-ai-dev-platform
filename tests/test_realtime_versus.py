import unittest

from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.constants import Action
from src.core.realtime import TickInput


class TestRealtimeVersusMatch(unittest.TestCase):
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

    def test_due_ojama_drops_on_arrival_tick(self):
        match = RealtimeVersusMatch(seed=123, attack_delay_ticks=0)
        match.schedule_attack("player_0", 3, delay_ticks=0)

        result = match.step()

        self.assertEqual(result.dropped_ojama["player_1"], 3)
        self.assertEqual(match.player_states["player_1"].received_ojama_total, 3)
        self.assertEqual(match.player_states["player_1"].pending_ojama, 0)

    def test_score_carry_matches_chain_end_boundary_conversion(self):
        match = RealtimeVersusMatch(seed=123)

        generated = [
            match._attack_units_from_score("player_0", score_delta)
            for score_delta in (40, 29, 1, 71)
        ]

        self.assertEqual(generated, [0, 0, 1, 1])
        self.assertEqual(match.player_states["player_0"].score_carry, 1)


if __name__ == "__main__":
    unittest.main()
