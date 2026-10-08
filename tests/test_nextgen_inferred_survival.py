"""Fixed public deductions, actual envelope selection and bounded proof failures."""
from dataclasses import replace
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from agents.nextgen_shared_search import _public_state, _pairs, ResponseBudget
from agents.nextgen_survival import (
    _ControlProof, apply_envelope, evidence_for, inferred_state, probe,
    refine_inferred, value,
)
from tests.test_nextgen_inference_wire import bind

ROOT = Path(__file__).resolve().parents[1] / 'docs/benchmarks/puyo-266-safe-build'
CASES = ('gtr-123/28', 'gtr-132/30', 'gtr-135/34', 'gtr-135/35', 'gtr-135/36', 'human-127/26')


def fixtures():
    def read(path):
        return json.loads(gzip.decompress(path.read_bytes()))
    wires = {}
    for seed, decisions in ((123, (28,)), (132, (30,)), (135, (34, 35, 36))):
        raw = read(ROOT / f'desktop-survival-20260928/before/gtr-{seed}.json.gz')
        for decision in decisions:
            wires[f'gtr-{seed}/{decision}'] = raw['ledger'][decision - 1]
    raw = read(ROOT / 'sprint14-human-20261008/before/report.json.gz')
    wires['human-127/26'] = next(a['payload']['nextgen'] for a in raw['attempts']
                               if a['record']['outcome'] == 'activated'
                               and a['payload']['nextgen']['request']['identity']['decision_id'] == 26)
    # The audit stored PUBLIC observer output; offline_matches is never input.
    audit = json.loads((ROOT / 'sprint14-human-20261008/public-inference-audit.json').read_text())
    hidden = {r['case']: tuple(map(tuple, r['hidden_rows'])) for r in audit['rows']}
    result = {}
    for name, wire in wires.items():
        req = replace(c.NextgenRequest.from_dict(wire['request']), schema_version=c.REQUEST_SCHEMA_VERSION)
        req = replace(req, inference=bind(req, hidden=hidden[name]))
        batch = c.CandidateBatch.from_dict(wire['batch'])
        candidates = {v.candidate_id: v for v in batch.candidates}
        ids = next(t.candidate_ids for t in batch.tactics if t.tactic_id == 'build_main')
        order = tuple(dict.fromkeys(candidates[cid].root_action for cid in ids))
        result[name] = req, batch, c.Selection.from_dict(wire['selection']), order
    return result


def execute(req, order, quota=256):
    original, _ = _public_state(req)
    state = inferred_state(req, original)
    budget, cache = ResponseBudget(quota), {}
    roots, diagnostics = probe(req, state, legal_action_indices(state), budget, transition_cache=cache)
    result, diagnostics = refine_inferred(req, state, roots, diagnostics, budget, order, cache)
    return result, diagnostics, budget, roots


def selection(batch, selected, roots, active):
    candidates = []
    for candidate in batch.candidates:
        root = roots.get(candidate.root_action)
        if root:
            evidence = {e.name: e for e in evidence_for(root, board_complete=False)}
            candidate = replace(candidate, evidence=tuple(evidence.get(e.name, e) for e in candidate.evidence))
        candidates.append(candidate)
    by_id = {v.candidate_id: v for v in candidates}
    tactics = []
    for tactic in batch.tactics:
        def rank(cid):
            candidate = by_id[cid]
            safe = value(candidate, 'survival_safe')
            return (0 if safe == 1 and (not value(candidate, 'survival_root_chain')
                    or tactic.tactic_id not in ('build_main', 'build_template')) else 1 if safe == 1
                    else 3 if safe == 0 else 2)
        ids = tuple(sorted(tactic.candidate_ids, key=rank)) if active else tactic.candidate_ids
        tactics.append(replace(tactic, candidate_ids=ids, best_id=ids[0] if ids else None))
    repaired = replace(batch, candidates=tuple(candidates), tactics=tuple(tactics))
    row = next(t for t in repaired.tactics if t.tactic_id == selected.selected_tactic_id)
    actual = apply_envelope(repaired, replace(selected, candidate_id=row.best_id, batch_digest=repaired.digest))
    return actual.validate_batch(repaired).root_action, actual.reason


class InferredSurvivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = fixtures()

    def test_fixed_decisions_and_actual_envelope(self):
        expected = ((1, 62), (11, 100), (3, 91), (15, 87), (None, 18), (8, 117))
        for name, (root, nodes) in zip(CASES, expected):
            with self.subTest(case=name):
                req, batch, selected, order = self.fixtures[name]
                roots, diag, budget, _ = execute(req, order)
                proof = diag['control_proof']
                self.assertEqual((proof['certified_root'], budget.nodes), (root, nodes))
                self.assertLessEqual(budget.nodes, 128)
                self.assertFalse(proof['safety_guarantee'])
                self.assertEqual(proof['charged']['placement'], 0)
                self.assertGreater(proof['placement_cache_hits'], 0)
                actual, reason = selection(batch, selected, roots, diag['active'])
                if root is not None:
                    self.assertEqual(actual, root)
                if name == 'human-127/26':
                    self.assertEqual(reason, 'legitimate_survival_exception')
                    self.assertEqual(roots[root].root_chain, 4)
                    self.assertEqual(roots[12].status, 'unknown')

    def test_cutoff_is_unknown_and_preserves_finite_horizon_fallback(self):
        req, batch, selected, order = self.fixtures['human-127/26']
        for quota in (66, 100):
            result, diag, budget, original = execute(req, order, quota)
            self.assertEqual(budget.nodes, quota)
            self.assertEqual(result, original)
            self.assertEqual(diag['control_proof']['status'], 'unknown_cutoff')
            self.assertIsNone(diag['control_proof']['certified_root'])
            self.assertTrue(diag['control_proof']['fallback'])
            self.assertEqual(selection(batch, selected, result, diag['active'])[0], 3)

    def test_missing_unknown_mismatch_and_pending_garbage_use_original_path(self):
        req, _, _, _ = self.fixtures['human-127/26']
        state, _ = _public_state(req)
        for inf in (None, replace(req.inference, visible_digest='a' * 64),
                    replace(req.inference, status='unknown', hidden_rows=((None,) * 6,) * 2)):
            self.assertIsNone(inferred_state(replace(req, inference=inf), state))
        from eval.nextgen_response_fixtures import make_request
        threatened = make_request(incoming=6)
        threatened = replace(threatened, inference=bind(threatened))
        self.assertIsNone(inferred_state(threatened, _public_state(threatened)[0]))

    def test_geometry_cache_is_local_occupancy_target_not_colors(self):
        req, _, _, order = self.fixtures['gtr-123/28']
        state = inferred_state(req, _public_state(req)[0])
        pair = _pairs(req.public.own.known_pieces)[0]
        control = _ControlProof(ResponseBudget(256), 0)
        control.reachable(state, pair, order[0])
        count = control.budget.nodes
        # Color permutation leaves collision rules unchanged.
        colored = replace(state, planes=tuple(reversed(state.planes)))
        control.reachable(colored, tuple(reversed(pair)), order[0])
        self.assertEqual((control.budget.nodes, control.hits), (count, 1))
        self.assertEqual(_ControlProof(ResponseBudget(256), 0).cache, {})
        control.reachable(replace(state, planes=(0,) * 6), pair, order[0])
        self.assertGreater(control.budget.nodes, count)

    def test_terminal_color_dependent_witness_is_not_certified(self):
        req, _, _, _ = self.fixtures['human-127/26']
        state = inferred_state(req, _public_state(req)[0])
        pairs = _pairs(req.public.own.known_pieces)
        for pair, action in zip(pairs, (12, 3, 11)):
            state = transition(state, pair, action).state
        self.assertEqual(state.column_heights, (14, 14, 11, 14, 13, 12))
        proof = _ControlProof(ResponseBudget(256), 0)
        self.assertIsNone(proof.terminal(state, pairs[0]))
        self.assertEqual(proof.charged['terminal'], 0)

    def test_cache_does_not_make_uncharged_transitions(self):
        req, _, _, order = self.fixtures['gtr-123/28']
        state = inferred_state(req, _public_state(req)[0])
        budget = ResponseBudget(256)
        roots, diag = probe(req, state, legal_action_indices(state), budget)
        with patch('agents.nextgen_survival.transition', wraps=transition) as called:
            _, diag = refine_inferred(req, state, roots, diag, budget, order, {})
        self.assertEqual(called.call_count, diag['control_proof']['charged']['placement'])
        self.assertEqual(called.call_count, 3)


if __name__ == '__main__':
    unittest.main()
