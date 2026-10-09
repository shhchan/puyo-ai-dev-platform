"""Omitted duplicate transitions fund alternate proofs without quota refunds."""
from dataclasses import replace
import gzip
import json
import unittest
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from agents.nextgen_shared_search import ResponseBudget, _public_state
from agents.nextgen_survival import inferred_state, probe, refine_inferred
from tests.test_nextgen_inferred_survival import ROOT, selection


def fixture():
    path = ROOT / 'sprint14-human-20261008/inference-v1-fixed/gtr-128.json.gz'
    wire = json.loads(gzip.decompress(path.read_bytes()))['ledger'][37]
    req = c.NextgenRequest.from_dict(wire['request'])
    batch = c.CandidateBatch.from_dict(wire['batch'])
    by_id = {v.candidate_id: v for v in batch.candidates}
    ids = next(t.candidate_ids for t in batch.tactics if t.tactic_id == 'build_main')
    order = tuple(dict.fromkeys(by_id[cid].root_action for cid in ids))
    return req, batch, c.Selection.from_dict(wire['selection']), order


class AlternateSurvivalTests(unittest.TestCase):
    def execute(self, quota=256, reuse=True):
        req, batch, selected, order = fixture()
        state = inferred_state(req, _public_state(req)[0])
        budget, cache = ResponseBudget(quota), {}
        with patch('agents.nextgen_survival.transition', wraps=transition) as called:
            roots, before = probe(req, state, legal_action_indices(state), budget,
                                  transition_cache=cache, reuse_transitions=reuse)
            probe_calls = called.call_count
            result, diag = refine_inferred(req, state, roots, before, budget, order, cache)
            total_calls = called.call_count
        return req, batch, selected, result, diag, budget, before, probe_calls, total_calls

    def test_alternate_prefix_uses_saved_work_and_real_envelope(self):
        req, batch, selected, roots, diag, budget, before, calls, total = self.execute()
        self.assertTrue(req.execution.reachable_mask[12])
        self.assertEqual((before['nodes'], calls, before['reused_transition_nodes']), (110, 70, 40))
        proof = diag['control_proof']
        alternate = proof['alternate_prefix']
        self.assertEqual((budget.nodes, alternate['actual_nodes'], budget.quota - budget.nodes), (128, 115, 128))
        self.assertEqual((alternate['charged'], total), ({'control': 22, 'placement': 4, 'terminal': 1}, 74))
        self.assertEqual(proof['charged']['control'] + alternate['charged']['control'] + total + 1, 115)
        self.assertEqual((roots[12].witness, roots[11].status), ((12, 19, 14), 'unknown'))
        self.assertEqual(selection(batch, selected, roots, diag['active'])[0], 12)
        self.assertFalse(proof['safety_guarantee'])
        old = self.execute(reuse=False)
        self.assertEqual(old[5].nodes, 128)
        self.assertEqual(selection(old[1], old[2], old[3], old[4]['active'])[0], 11)

    def test_small_quotas_remain_bounded_and_cutoff_never_becomes_fatal(self):
        for quota in (0, 40, 70, 110, 127, 128):
            with self.subTest(quota=quota):
                new = self.execute(quota=quota)
                old = self.execute(quota=quota, reuse=False)
                self.assertLessEqual(new[5].nodes, min(quota, 128))
                self.assertEqual({a for a, v in new[3].items() if v.status == 'fatal'},
                                 {a for a, v in old[3].items() if v.status == 'fatal'})
                if new[4]['control_proof']['certified_root'] is None:
                    self.assertEqual(new[3], old[3])

    def test_unreachable_first_lock_cannot_receive_alternate_certificate(self):
        req, _, _, order = fixture()
        state = inferred_state(req, _public_state(req)[0])
        budget, cache = ResponseBudget(256), {}
        roots, diag = probe(req, state, legal_action_indices(state), budget,
                            transition_cache=cache, reuse_transitions=True)
        mask = tuple(False if i == 12 else v for i, v in enumerate(req.execution.reachable_mask))
        changed = replace(req, execution=replace(req.execution, reachable_mask=mask))
        # Execution mutation also invalidates the inference digest: fail closed.
        self.assertIsNone(changed.known_inference())
        _, diag = refine_inferred(changed, state, roots, diag, budget, order, cache)
        self.assertNotIn('alternate_prefix', diag['control_proof'])


if __name__ == '__main__':
    unittest.main()
