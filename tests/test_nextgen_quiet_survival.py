"""Bounded quiet-prefix proof from the fixed public pattern6779 failure."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from agents.nextgen_shared_search import ResponseBudget, _pairs, _public_state
from agents.nextgen_survival import _ControlProof, inferred_state, probe, refine_inferred
from tests.test_nextgen_inferred_survival import fixtures, selection
from tests.test_nextgen_inference_wire import bind


class QuietSurvivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = json.loads((Path(__file__).parent / 'fixtures' /
                               'nextgen_single_quiet_6779_public.json').read_text())
        cls.req = c.NextgenRequest.from_dict(cls.saved['request'])
        cls.state = inferred_state(cls.req, _public_state(cls.req)[0])
        cls.order = tuple(cls.saved['root_order'])

    def execute(self, quota=256, target=10, request=None, state=None, order=None):
        request = self.req if request is None else request
        state = self.state if state is None else state
        budget, cache = ResponseBudget(quota), {}
        roots, before = probe(request, state, legal_action_indices(state), budget,
                              transition_cache=cache, reuse_transitions=True)
        result, diag = refine_inferred(request, state, roots, before, budget,
                                      self.order if order is None else order, cache,
                                      target_chain_count=target)
        return result, diag, budget, roots

    def test_quiet_alternative_uses_original_order_and_actual_envelope(self):
        result, diag, budget, original = self.execute()
        proof = diag['control_proof']
        self.assertEqual((proof['certified_root'], budget.nodes), (0, 116))
        self.assertEqual(result[0].witness, (0, 5, 7))
        self.assertEqual([v['root'] for v in proof['quiet_alternative']['trials']], [2, 0])
        self.assertGreater(proof['quiet_alternative']['control_graph_hits'], 0)
        bound = proof['quiet_alternative']['terminal_prefilter']
        self.assertEqual(bound['charged_as'], 'terminal')
        self.assertEqual(bound['evaluations'], 6)
        self.assertGreater(bound['cache_hits'], 0)
        self.assertEqual(proof['charged']['terminal'], bound['evaluations'] + 2)
        self.assertEqual(budget.nodes, 28 + sum(proof['charged'].values()))
        batch = c.CandidateBatch.from_dict(self.saved['batch'])
        selected = c.Selection.from_dict(self.saved['selection'])
        self.assertEqual(selection(batch, selected, result, diag['active']), (0, 'survival_safe_nonfire'))
        self.assertEqual({a for a, v in result.items() if v.status == 'fatal'},
                         {a for a, v in original.items() if v.status == 'fatal'})
        self.assertFalse(proof['safety_guarantee'])

    def test_target_fire_is_not_replaced_by_quiet_search(self):
        # The same immediate one-chain clear is a target fire under this
        # injected target; the official gate still fixes target10.
        with patch('agents.nextgen_survival._quiet_control_witness') as alternate:
            result, diag, _, _ = self.execute(target=1)
        alternate.assert_not_called()
        self.assertEqual(diag['control_proof']['certified_root'], 1)
        self.assertEqual(result[1].root_chain, 1)

    def test_cutoffs_preserve_bounds_and_never_claim_fatal_from_unknown(self):
        for quota in (0, 28, 60, 70, 80, 109, 110, 115, 116, 128):
            with self.subTest(quota=quota):
                result, diag, budget, original = self.execute(quota=quota)
                self.assertLessEqual(budget.nodes, min(quota, 128))
                self.assertEqual({a for a, v in result.items() if v.status == 'fatal'},
                                 {a for a, v in original.items() if v.status == 'fatal'})
                self.assertFalse(diag['control_proof']['safety_guarantee'])

    def test_landed_garbage_and_pending_packets_keep_old_path(self):
        request, _, _, order = fixtures()['human-127/26']
        state = inferred_state(request, _public_state(request)[0])
        self.assertTrue(state.planes[5])
        for boundary in ('landed', 'pending'):
            with self.subTest(boundary=boundary):
                req = request
                if boundary == 'pending':
                    own = replace(req.public.own, attack_packets=(c.PublicAttackPacket('p', 1, 9999, None),))
                    public = replace(req.public, own=own)
                    req = replace(req, public=public, identity=replace(req.identity, snapshot_digest=public.digest))
                    req = replace(req, inference=bind(req, hidden=req.inference.hidden_rows))
                old = self.execute(request=req, state=state, order=order, target=None)
                new = self.execute(request=req, state=state, order=order)
                self.assertEqual(old[:2], new[:2])
                self.assertEqual(old[2].nodes, new[2].nodes)
                self.assertNotIn('quiet_alternative', new[1]['control_proof'])

    def test_control_graph_reuses_only_equal_occupied_geometry(self):
        state = self.state
        pair = _pairs(self.req.public.own.known_pieces)[0]
        cached = _ControlProof(ResponseBudget(256), 0, reuse_control_graph=True)
        colored = replace(state, planes=tuple(reversed(state.planes)))
        for action in (0, 2, 3, 5, 6, 7, 9):
            expected = _ControlProof(ResponseBudget(256), 0).reachable(state, pair, action)
            self.assertEqual(cached.reachable(colored, tuple(reversed(pair)), action), expected)
        self.assertGreater(cached.control_graph_hits, 0)
        before = set(cached.control_graph)
        changed = transition(state, pair, 0).state
        self.assertNotEqual(changed.occupied_mask, state.occupied_mask)
        expected = _ControlProof(ResponseBudget(256), 0).reachable(changed, pair, 5)
        self.assertEqual(cached.reachable(changed, pair, 5), expected)
        self.assertTrue(set(cached.control_graph) - before)
        self.assertTrue(all(key[0] in (state.occupied_mask, changed.occupied_mask)
                            for key in cached.control_graph))
        self.assertEqual(_ControlProof(ResponseBudget(256), 0, reuse_control_graph=True).control_graph, {})


if __name__ == '__main__':
    unittest.main()
