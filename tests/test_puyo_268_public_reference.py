"""Fair comparison boundary tests; no reference-budget search needed."""
import copy
import unittest
from dataclasses import replace

from agents import deep_chain_builder as reference
from agents.nextgen_shared_search import _public_state
from eval.puyo_268_public_reference import (
    PublicReferencePolicy, compare_request, estimated_state,
    observation_from_request, public_observation,
)
from tests.test_template_preserving_integration import make_request, selected_catalog


class PublicReferenceTests(unittest.TestCase):
    def setUp(self):
        self.policy = PublicReferencePolicy(123, backend='python')
        self.policy.public_config = replace(self.policy.public_config, depth=2, width=2,
                                           scenarios=1, max_expanded_nodes=44)
        self.policy.profile = replace(self.policy.profile, depth=2, width=2,
                                      scenarios=1, max_expanded_nodes=44)
        self.request = make_request(selected_catalog('gtr'), self.policy.public_config,
                                    rows=['123400', '240000'], quota=44, unknown=True)

    def test_hidden_and_private_poisoning_does_not_change_input_or_identity(self):
        observation, info = observation_from_request(self.request)
        expected = public_observation(observation, info)
        original = copy.deepcopy(observation)
        poisoned = copy.deepcopy(observation)
        poisoned['own_board'][0][0] = [1] * 6
        poisoned['ghost_row'] = object()
        poisoned['private_future'] = object()
        dirty_info = info | {'score': 99999, 'step_count': 777, 'game_state': object()}
        self.assertEqual(expected, public_observation(poisoned, dirty_info))
        self.assertEqual(self.policy.decision_input_identity(observation, info),
                         self.policy.decision_input_identity(poisoned, dirty_info))
        self.assertEqual(observation, original)

    def test_public_cells_pairs_and_reachability_survive_encoding(self):
        observation, info = public_observation(*observation_from_request(self.request))
        visible = reference.build_visible_runtime_input(observation, info)
        self.assertEqual(estimated_state(visible), _public_state(self.request)[0])
        self.assertIsNone(visible.ghost_row)
        self.assertFalse(visible.summary()['complete_board_observed'])
        changed = copy.deepcopy(observation)
        changed['own_board'][0][-1][5] = 1
        self.assertNotEqual(estimated_state(reference.build_visible_runtime_input(changed, info)),
                            estimated_state(visible))

    def test_every_reference_search_has_equal_public_input_seed_and_budget(self):
        before_state = reference._compact_state_from_observation
        before_seed = reference._visible_decision_seed
        result = compare_request(self.request, self.policy)
        self.assertTrue(result['input_equal'])
        self.assertTrue(result['scenario_equal'])
        self.assertTrue(result['shared_budget_equal'])
        self.assertFalse(result['public_board_complete'])
        self.assertIs(reference._compact_state_from_observation, before_state)
        self.assertIs(reference._visible_decision_seed, before_seed)
        self.assertEqual(self.policy.flow.step_ids, reference.DeepChainBuildFlow().step_ids)

    def test_budget_mismatch_is_rejected(self):
        self.policy.profile = replace(self.policy.profile, max_expanded_nodes=45)
        with self.assertRaisesRegex(ValueError, 'budget'):
            compare_request(self.request, self.policy)


if __name__ == '__main__':
    unittest.main()
