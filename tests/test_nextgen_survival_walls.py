"""Reject only full-column crossings, preserving legal hidden continuations."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState, legal_action_indices, transition
from agents.nextgen_shared_search import ResponseBudget, _pairs, _public_state
from agents.nextgen_survival import continuation_actions, probe

FIXTURE = Path('tests/fixtures/puyo_266_continuation_walls.json')


class SurvivalWallTests(unittest.TestCase):
    def test_full_wall_blocks_crossing_but_suspended_top_cell_does_not(self):
        full = sum(1 << (y * 6 + 1) for y in range(14))
        wall = CompactSearchState(planes=(full, 0, 0, 0, 0, 0))
        self.assertIn(0, legal_action_indices(wall))
        self.assertNotIn(0, continuation_actions(wall))
        self.assertIn(11, continuation_actions(wall))
        # Row 14 remains after clears; it does not prove a solid column.
        suspended = CompactSearchState(planes=(1 << (13 * 6 + 1), 0, 0, 0, 0, 0))
        self.assertIn(0, continuation_actions(suspended))

    def run_fixture(self, name, legacy=False):
        request = c.NextgenRequest.from_dict(json.loads(FIXTURE.read_text())[name])
        state, _ = _public_state(request)
        if legacy:
            with patch('agents.nextgen_survival.continuation_actions', legal_action_indices):
                result = probe(request, state, legal_action_indices(state), ResponseBudget(256))
        else:
            result = probe(request, state, legal_action_indices(state), ResponseBudget(256))
        return request, state, result

    def test_reported_public_witness_no_longer_crosses_full_wall(self):
        _, _, (before, _) = self.run_fixture('human127_decision26', legacy=True)
        request, state, (after, summary) = self.run_fixture('human127_decision26')
        self.assertEqual(before[3].witness, (3, 0, 11))
        self.assertNotEqual(after[3].witness, before[3].witness)
        for root in after.values():
            current = state
            for depth, action in enumerate(root.witness):
                if depth:
                    self.assertIn(action, continuation_actions(current))
                current = transition(current, _pairs(request.public.own.known_pieces)[depth], action).state
        self.assertLessEqual(summary['nodes'], 128)
        # This necessary condition alone does not establish the required fire.
        self.assertEqual(after[3].status, 'witness')
        self.assertEqual(after[7].root_chain, 4)

    def test_normal_gtr123_hidden_continuation_is_preserved(self):
        _, _, (before, _) = self.run_fixture('gtr123_decision28', legacy=True)
        _, _, (after, summary) = self.run_fixture('gtr123_decision28')
        self.assertEqual(before[1].witness, (1, 3, 8))
        self.assertEqual(after[1], before[1])
        self.assertLessEqual(summary['nodes'], 128)


if __name__ == '__main__':
    unittest.main()
