"""Actual public locks are distinct from intent, receipts and hidden cells."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from agents.nextgen_contracts import NUM_ACTIONS, PUBLIC_CELL_TO_COLOR
from puyo_env.nextgen_public_snapshot import PublicPlacementHistory, PublicPlacementRecord
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.realtime import RealtimeEvent
from src.core.puyo import Puyo
from tests.test_nextgen_realtime_diagnostic_inputs import FIXTURE
from eval.nextgen_realtime_diagnostic import load_human_inputs
from src.core.realtime import TickInput


class PlacementHistoryTests(unittest.TestCase):
    def test_actual_lock_pair_roundtrip_and_player_scope(self):
        match = RealtimeVersusMatch(seed=127)
        match.public_snapshot()
        inputs, _ = load_human_inputs(FIXTURE, seed=127)
        for _ in range(150):
            match.step({'player_1': inputs.get(match.tick, TickInput())})
        history = match.public_placement_history(1)
        self.assertEqual(history.started_tick, 0)
        self.assertEqual([r.pair for r in history.records], [(1, 1), (1, 1)])
        self.assertEqual([r.action for r in history.records], [0, 0])
        self.assertEqual(PublicPlacementHistory.from_dict(history.to_dict()), history)
        self.assertEqual(match.public_placement_history(0).records, ())
        # The old snapshot/timing transports do not acquire hidden estimates.
        self.assertEqual(match.public_snapshot().own.visible_board[:2], ((None,) * 6,) * 2)
        self.assertNotIn('placements', match.public_timing_history().to_dict())

    def test_midgame_install_reset_and_no_lock_do_not_invent_history(self):
        match = RealtimeVersusMatch(seed=127)
        for _ in range(10):
            match.step()
        history = match.public_placement_history()
        self.assertEqual(history.started_tick, 10)
        self.assertEqual(history.records, ())
        for _ in range(3):
            match.public_placement_history()  # Polling/requests/receipts are not locks.
        self.assertEqual(match.public_placement_history(), history)
        match.reset(seed=127)
        self.assertEqual(match.public_placement_history().started_tick, 0)
        self.assertEqual(match.public_placement_history().records, ())
        with self.assertRaises(ValueError):
            match.public_placement_history(2)

    def test_lock_height_and_private_cells_do_not_enter_records(self):
        histories = []
        for private_color, private_height in ((1, 12), (4, 13)):
            match = RealtimeVersusMatch(seed=127)
            match.public_snapshot()
            game = match.player_states['player_0'].simulator.game
            game.field.grid[13][0] = Puyo(PUBLIC_CELL_TO_COLOR[private_color])
            # Adapter-only read audit: the engine itself needs field access to step.
            game.field.get_puyo = lambda *_: (_ for _ in ()).throw(AssertionError('field read'))
            adapter = match._public_snapshot_adapter
            adapter.before_tick(match)
            result = SimpleNamespace(tick=0, attack_diagnostics={},
                generated_attacks={'player_0': 0, 'player_1': 0},
                dropped_ojama={'player_0': 0, 'player_1': 0},
                player_results={a: SimpleNamespace(state_before='control', state_after='control',
                    events=(RealtimeEvent(type='lock', tick=0, data={
                        'axis_x': 0, 'axis_y': private_height, 'rotation': 'UP'}),) if a=='player_0' else ())
                    for a in match.possible_agents})
            adapter.observe_tick(match, result)
            histories.append(adapter.placement_history())
        self.assertEqual(histories[0], histories[1])
        self.assertEqual(set(histories[0].records[0].to_dict()),
                         {'event_id', 'player_id', 'tick', 'action', 'pair'})

    def test_contract_rejects_bad_action_and_cross_player_history(self):
        record = PublicPlacementRecord('lock', 0, 1, 0, (1, 2))
        with self.assertRaises(ValueError):
            replace(record, action=NUM_ACTIONS)
        with self.assertRaises(ValueError):
            PublicPlacementHistory(1, 0, (record,))
        with self.assertRaises(ValueError):
            PublicPlacementHistory(0, 0, (record, record))


if __name__ == '__main__':
    unittest.main()
