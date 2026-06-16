import unittest

from src.core.constants import Action, Direction, PuyoColor
from src.core.game import GameState
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo
from src.core.realtime import RealtimeHeadlessSimulator, TickInput


class TestRealtimeHeadlessSimulator(unittest.TestCase):
    def test_fixed_seed_and_input_sequence_reproduce_hashes(self):
        inputs = {
            0: TickInput(press=(Action.LEFT,)),
            1: TickInput(release=(Action.LEFT,)),
            2: TickInput(press=(Action.ROTATE_RIGHT,)),
            3: TickInput(release=(Action.ROTATE_RIGHT,)),
            4: TickInput(press=(Action.DOWN,)),
            5: TickInput(release=(Action.DOWN,)),
        }
        sim_a = RealtimeHeadlessSimulator(seed=123)
        sim_b = RealtimeHeadlessSimulator(seed=123)

        hashes_a = [result.snapshot_hash for result in sim_a.advance_ticks(40, inputs_by_tick=inputs)]
        hashes_b = [result.snapshot_hash for result in sim_b.advance_ticks(40, inputs_by_tick=inputs)]

        self.assertEqual(hashes_a, hashes_b)
        self.assertEqual(sim_a.state_hash(), sim_b.state_hash())

    def test_pause_clone_and_fast_forward_keep_results_identical(self):
        inputs = {
            0: TickInput(press=(Action.RIGHT,)),
            1: TickInput(release=(Action.RIGHT,)),
            10: TickInput(press=(Action.DOWN,)),
            11: TickInput(release=(Action.DOWN,)),
        }
        sim_fast = RealtimeHeadlessSimulator(seed=456)
        sim_fast.advance_ticks(60, inputs_by_tick=inputs)

        sim_step = RealtimeHeadlessSimulator(seed=456)
        sim_step.advance_ticks(20, inputs_by_tick=inputs)
        paused_hash = sim_step.state_hash()
        self.assertEqual(paused_hash, sim_step.state_hash())
        sim_step.advance_ticks(40, inputs_by_tick=inputs)

        self.assertEqual(sim_fast.state_hash(), sim_step.state_hash())

    def test_resolution_complete_event_reports_chain_score(self):
        game = GameState(seed=0)
        game.spawn_puyo()
        game.current_puyo_1 = Puyo(PuyoColor.RED)
        game.current_puyo_2 = Puyo(PuyoColor.RED)
        game.field.place_puyo(1, 0, Puyo(PuyoColor.RED))
        game.field.place_puyo(1, 1, Puyo(PuyoColor.RED))
        game.puyo_x = 2
        game.puyo_y = game.find_landing_y(2, Direction.UP)
        game.puyo_rot = Direction.UP
        game.lock_puyo()
        sim = RealtimeHeadlessSimulator(game_state=game)

        events = []
        for result in sim.advance_ticks(120):
            events.extend(result.events)
            if events:
                break

        self.assertEqual(events[0].type, "resolution_complete")
        self.assertEqual(events[0].data["score_delta"], 40)
        self.assertEqual(events[0].data["chain_count"], 1)


if __name__ == "__main__":
    unittest.main()
