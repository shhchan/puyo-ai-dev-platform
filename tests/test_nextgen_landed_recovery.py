"""Landed-garbage recovery is public, bounded and limited to build_main."""
from dataclasses import replace
import gzip
import json
import unittest
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices
from agents.nextgen_shared_search import ResponseBudget, SharedSearchBatchBuilder, _public_state
from agents.nextgen_survival import RootSurvival, inferred_state, probe, refine_inferred
from tests.test_nextgen_inferred_survival import ROOT, selection
from tests.test_nextgen_shared_search import config, request
from tests.test_nextgen_inference_wire import bind


class LandedRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'sprint14-human-20261008/inference-v1-human/report.json.gz'
        raw = json.loads(gzip.decompress(path.read_bytes()))
        cls.wires = {a['payload']['nextgen']['request']['identity']['decision_id']: a['payload']['nextgen']
                     for a in raw['attempts'] if a['record']['outcome'] == 'activated'}

    def fixture(self, decision=17):
        wire = self.wires[decision]
        req = c.NextgenRequest.from_dict(wire['request'])
        batch = c.CandidateBatch.from_dict(wire['batch'])
        selected = c.Selection.from_dict(wire['selection'])
        by_id = {v.candidate_id: v for v in batch.candidates}
        row = next(t for t in batch.tactics if t.tactic_id == 'build_main')
        order = tuple(dict.fromkeys(by_id[cid].root_action for cid in row.candidate_ids))
        state = inferred_state(req, _public_state(req)[0])
        budget, cache = ResponseBudget(256), {}
        roots, diag = probe(req, state, legal_action_indices(state), budget, transition_cache=cache)
        return req, batch, selected, order, state, budget, cache, roots, diag

    def test_saved_early_choices_use_real_envelope_with_fixed_budget(self):
        expected = {17: (12, 82, 6), 21: (5, 87, 9), 23: (21, 78, 14),
                    27: (17, 73, 14), 28: (17, 68, 14), 29: (17, 38, 14)}
        for decision, (action, nodes, removed) in expected.items():
            with self.subTest(decision=decision):
                req, batch, selected, order, state, budget, cache, roots, diag = self.fixture(decision)
                result, diag = refine_inferred(req, state, roots, diag, budget, order, cache)
                recovery = diag['control_proof']['landed_garbage_recovery']
                self.assertEqual((recovery['preferred_root'], budget.nodes, recovery['garbage_removed']),
                                 (action, nodes, removed))
                self.assertEqual(selection(batch, selected, result, diag['active'], action),
                                 (action, 'legitimate_survival_exception'))
                self.assertFalse(diag['control_proof']['safety_guarantee'])
                self.assertLessEqual(budget.nodes, 128)
                # No previously untested quiet root is relabeled fatal.
                self.assertEqual({a for a, v in roots.items() if v.status == 'fatal'},
                                 {a for a, v in result.items() if v.status == 'fatal'})

    def test_no_recovery_without_known_landed_pressure_fatal_root_and_real_removal(self):
        for boundary in ('missing', 'unknown', 'mismatch', 'pending', 'no_fatal', 'no_removal', 'fatal_clear'):
            with self.subTest(boundary=boundary):
                req, _, _, order, state, budget, cache, roots, diag = self.fixture()
                if boundary == 'missing':
                    req = replace(req, inference=None)
                elif boundary == 'unknown':
                    req = replace(req, inference=replace(req.inference, status='unknown', hidden_rows=((None,) * 6,) * 2))
                elif boundary == 'mismatch':
                    req = replace(req, inference=replace(req.inference, visible_digest='a' * 64))
                elif boundary == 'pending':
                    own = replace(req.public.own, attack_packets=(c.PublicAttackPacket('pending', 1, 9999, None),))
                    public = replace(req.public, own=own)
                    req = replace(req, public=public, identity=replace(req.identity, snapshot_digest=public.digest))
                    req = replace(req, inference=bind(req, hidden=req.inference.hidden_rows))
                    self.assertIsNotNone(req.known_inference())
                elif boundary == 'no_fatal':
                    roots = {a: replace(v, status='unknown') if v.status == 'fatal' else v for a, v in roots.items()}
                else:
                    cache = {key: replace(v, **({'garbage_cleared_count': 0} if boundary == 'no_removal' else {'game_over': True}))
                             if key[0] == state else v for key, v in cache.items()}
                _, result = refine_inferred(req, state, roots, diag, budget, order, cache)
                self.assertNotIn('landed_garbage_recovery', result['control_proof'])

    def test_failed_terminal_or_cutoff_never_promotes_recovery(self):
        for cutoff in (False, True):
            req, _, _, order, state, budget, cache, roots, diag = self.fixture()
            if cutoff:
                budget.quota = budget.nodes
            with patch('agents.nextgen_survival._ControlProof.terminal', return_value=None):
                result, diag = refine_inferred(req, state, roots, diag, budget, order, cache)
            self.assertIsNone(diag['control_proof']['landed_garbage_recovery']['preferred_root'])
            self.assertEqual(result, roots)
            self.assertTrue(diag['control_proof']['fallback'])
            if cutoff:
                self.assertEqual(diag['control_proof']['status'], 'unknown_cutoff')
            self.assertLessEqual(budget.nodes, 128)

    def test_real_batch_builder_applies_priority_only_to_build_main(self):
        cfg = config()
        req = request(cfg, quota=(0, 0, 256))
        state = _public_state(req)[0]
        roots = {0: RootSurvival(0, 'fatal'), 1: RootSurvival(1, 'witness', 0, (1,)),
                 2: RootSurvival(2, 'witness', 1, (2,))}
        base = {'active': True, 'nodes': 1}
        builder = SharedSearchBatchBuilder(None, cfg)
        batches = []
        for preferred in (None, 2):
            diag = {**base, 'control_proof': {'landed_garbage_recovery': {'preferred_root': preferred}}}
            with patch('agents.nextgen_shared_search.inferred_state', return_value=state), \
                 patch('agents.nextgen_shared_search.survival_probe', return_value=(roots, base)), \
                 patch('agents.nextgen_shared_search.refine_inferred', return_value=(roots, diag)):
                batches.append(builder.build(req).batch)
        for tactic in c.TACTIC_IDS:
            rows = [next(t for t in b.tactics if t.tactic_id == tactic) for b in batches]
            if tactic == 'build_main':
                actions = [next(v.root_action for v in b.candidates if v.candidate_id == row.best_id)
                           for b, row in zip(batches, rows)]
                self.assertEqual(actions, [1, 2])
            else:
                self.assertEqual(rows[0], rows[1])


if __name__ == '__main__':
    unittest.main()
