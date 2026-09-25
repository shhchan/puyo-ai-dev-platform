"""PUYO-270: frozen public counterexamples, rank, quota and actual receipt."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from agents.nextgen_shared_search import _public_state, _pairs, ResponseBudget
from agents.nextgen_survival import apply_envelope, needs_probe, probe, value
from agents.nextgen_tactic_manager import RuleTacticSelector
from eval.nextgen_response_fixtures import build, make_request, TIMING
from puyo_env.nextgen_public_snapshot import TickInterval
from tests.test_nextgen_contracts import make_diagnostics

FIXTURE = Path(__file__).parent / 'fixtures/puyo_271_regression_cases.json'


def choke_board():
    case = json.loads(FIXTURE.read_text())['survival_counterexamples'][0]
    rows = [tuple(map(int, row)) for row in case['rows_bottom_up']]
    return tuple(reversed(rows + [(0,) * 6] * (14 - len(rows))))


def mask(*actions):
    return tuple(i in actions for i in range(c.NUM_ACTIONS))


def select(req, execution):
    batch = execution.batch
    normal = RuleTacticSelector().select(req, batch, c.build_features({}, batch.action_mask), SimpleNamespace(threat='none'))
    return apply_envelope(batch, normal)


class SurvivalTests(unittest.TestCase):
    def request(self, **kwargs):
        return make_request(**dict(board=choke_board(), pieces=((1, 2),), incoming=0,
                                   quota=256, **kwargs))

    def test_needed_single_clear_and_safe_nonfire_are_distinct(self):
        for reachable, chain, reason in ((mask(7, 9), 1, 'legitimate_survival_exception'),
                                         (mask(0, 7, 9), 0, 'survival_safe_nonfire')):
            req = self.request(mask=reachable)
            ex = build(req)
            selection = select(req, ex)
            candidate = selection.validate_batch(ex.batch)
            state, _ = _public_state(req)
            result = transition(state, _pairs(req.public.own.known_pieces)[0], candidate.root_action)
            self.assertFalse(result.game_over)
            self.assertEqual(result.chain_count, chain)
            self.assertEqual(selection.reason, reason)
            self.assertEqual(value(candidate, 'survival_safe'), 1)
            self.assertLessEqual(ex.batch.counters.response_nodes, 256)
            self.assertEqual(c.from_json(ex.batch.to_json()), ex.batch)
            self.assertEqual(ex.batch.schema_version, 'puyo.nextgen.candidate_batch.v3')
            # The same output has deterministic semantics despite wall timing.
            self.assertEqual(ex.deterministic_digest, build(req).deterministic_digest)

    def test_unreachable_safe_root_is_not_a_witness(self):
        req = self.request(mask=mask(9))
        ex = build(req)
        self.assertEqual(ex.diagnostics['survival']['status'], 'unavoidable')
        self.assertIn(7, ex.diagnostics['survival']['unreachable_roots'])
        self.assertEqual(selection_action := select(req, ex).validate_batch(ex.batch).root_action, 9)
        self.assertNotEqual(selection_action, 7)

    def test_clear_can_still_die_after_resolution(self):
        rows = [[0, 0, 2 + y % 2, (3 - y % 2) if y < 8 else 1, 0, 0] for y in range(11)]
        req = make_request(board=tuple(reversed(rows + [[0] * 6] * 3)), pieces=((2, 1),),
                           incoming=0, mask=mask(8), quota=256)
        ex = build(req)
        root = ex.diagnostics['survival']['roots'][0]
        self.assertEqual((root['root_chain'], root['status']), (1, 'fatal'))
        self.assertEqual(value(ex.select('build_main'), 'survival_safe'), 0)

    def test_known_next_only_recovery_after_public_deadline(self):
        timing = replace(TIMING, operation_cadence=TickInterval(30, 30, 'public_estimate', 'fixed'))
        for next_pair, status in (((1, 2), 'witness'), ((3, 4), 'fatal')):
            req = make_request(board=choke_board(), pieces=((3, 4), next_pair), incoming=6,
                               arrival=48, timing=timing, mask=mask(0), quota=256)
            ex = build(req, timing=timing)
            root = ex.diagnostics['survival']['roots'][0]
            self.assertEqual(root['status'], status)
            if status == 'witness':
                self.assertEqual(root['witness'], [0, 7])
                self.assertEqual(root['root_chain'], 0)
        # Arrival possibly at the first boundary cannot promise the NEXT rescue.
        uncertain = make_request(board=choke_board(), pieces=((3, 4), (1, 2)), incoming=6,
                                 arrival=20, timing=timing, mask=mask(0), quota=256)
        self.assertNotEqual(build(uncertain, timing=timing).diagnostics['survival']['roots'][0]['status'], 'witness')

    def test_quota_timeout_hidden_and_visible_unknown(self):
        req = make_request(board=choke_board(), pieces=((1, 2),), incoming=0, mask=mask(7, 9), quota=0)
        ex = build(req)
        self.assertEqual(ex.diagnostics['survival']['status'], 'cutoff')
        self.assertEqual(ex.batch.counters.response_nodes, 0)
        timing = replace(TIMING, inference_latency_ticks=121)
        req = make_request(board=choke_board(), pieces=((1, 2),), incoming=0, timing=timing, mask=mask(7, 9))
        ex = build(req, timing=timing)
        self.assertTrue(all(v['status'] == 'deadline_unreachable' for v in ex.diagnostics['survival']['roots']))
        hidden = ((None,) * 6,) * 2 + choke_board()[2:]
        req = make_request(board=hidden, pieces=((1, 2),), incoming=0, mask=mask(7, 9))
        ex = build(req)
        candidate = select(req, ex).validate_batch(ex.batch)
        ev = next(e.evidence for e in candidate.evidence if e.name == 'survival_safe')
        self.assertEqual((ev.value, ev.source, ev.status), (1, 'public_estimate', 'partial'))
        self.assertFalse(ex.diagnostics['survival']['safety_guarantee'])
        unknown = list(hidden)
        unknown[-1] = (None,) * 6
        req = make_request(board=tuple(unknown), pieces=((1, 2),), incoming=0)
        self.assertEqual(build(req).diagnostics['survival']['status'], 'unknown')

    def test_root_coverage_precedes_next_search_and_unknown_arrival_stays_unknown(self):
        req = make_request(board=choke_board(), incoming=0, quota=22)
        ex = build(req)
        roots = ex.diagnostics['survival']['roots']
        self.assertEqual(len(roots), 22)
        self.assertTrue(all(row['root_chain'] is not None for row in roots))
        self.assertEqual({row['status'] for row in roots}, {'fatal', 'cutoff'})
        self.assertEqual(ex.batch.counters.response_nodes, 22)
        req = make_request(board=choke_board(), pieces=((3, 4),), incoming=6,
                           arrival=None, mask=mask(0), quota=256)
        ex = build(req)
        self.assertEqual(ex.diagnostics['survival']['roots'][0]['status'], 'unknown')
        self.assertIsNone(value(ex.select('build_main'), 'survival_safe'))

    def test_no_threat_gate_and_border_do_not_search(self):
        req = make_request(board=((0,) * 6,) * 14, incoming=0)
        self.assertFalse(needs_probe(req))
        state, _ = _public_state(req)
        with patch('agents.nextgen_survival.transition', side_effect=AssertionError('safe probe')):
            results, diag = probe(req, state, legal_action_indices(state), ResponseBudget(256))
        self.assertEqual((results, diag['nodes']), ({}, 0))
        hidden = ((None,) * 6,) * 2 + ((0,) * 6,) * 8 + ((0, 0, 2, 0, 0, 0),) * 4
        self.assertTrue(needs_probe(make_request(board=hidden, incoming=0)))
        for height, expected in ((5, False), (6, True)):
            rows = [(0, 0, 2 + i % 2, 0, 0, 0) for i in range(height)]
            req = make_request(board=tuple(reversed(rows + [(0,) * 6] * (14 - height))), incoming=0)
            self.assertEqual(needs_probe(req), expected)

    def test_safe_deliberate_fire_is_preserved_but_fatal_fire_is_repaired(self):
        req = self.request(mask=mask(0, 7, 9))
        ex = build(req)
        for tactic in ('fire_main', 'cancel', 'counter', 'decisive_short_attack'):
            for action, preserve in ((7, True), (9, False)):
                chosen = next(v for v in ex.batch.candidates if v.root_action == action and len(v.plan) == 1)
                candidates = tuple(replace(v, tactics=tuple(dict.fromkeys((*v.tactics, tactic))), fallback=False)
                                   if v == chosen else v for v in ex.batch.candidates)
                members = [v.candidate_id for v in candidates if tactic in v.tactics and v.root_reachable]
                members.remove(chosen.candidate_id)
                members.insert(0, chosen.candidate_id)
                rows = tuple(replace(row, candidate_ids=tuple(members), best_id=members[0],
                                     available=True, mask_reason='available', evaluation_status='partial')
                             if row.tactic_id == tactic else row for row in ex.batch.tactics)
                batch = replace(ex.batch, candidates=candidates, tactics=rows)
                selection = c.Selection(tactic, chosen.candidate_id, batch.digest, 'rule', None, None, None, 'deliberate_fire')
                actual = apply_envelope(batch, selection)
                if preserve:
                    self.assertEqual(actual, selection)
                else:
                    self.assertEqual(actual.validate_batch(batch).root_action, 0)
                    self.assertEqual(actual.reason, 'survival_safe_nonfire')

    def test_rl_envelope_and_legacy_codecs(self):
        req = self.request(mask=mask(7, 9))
        ex = build(req)
        safe = select(req, ex)
        unsafe = next(v for v in ex.batch.candidates if v.root_action == 9)
        # A template teacher/actor can prefer an unsafe tactic's fixed best.
        candidates = tuple(replace(v, tactics=(*v.tactics, 'build_template'), fallback=False) if v == unsafe else v for v in ex.batch.candidates)
        rows = tuple(replace(row, candidate_ids=(unsafe.candidate_id,), best_id=unsafe.candidate_id,
                             available=True, mask_reason='available', evaluation_status='partial') if row.tactic_id == 'build_template' else row
                     for row in ex.batch.tactics)
        batch = replace(ex.batch, candidates=candidates, tactics=rows)
        learned = c.Selection('build_template', unsafe.candidate_id, batch.digest, 'rl', 'a' * 64, -.3, .2, 'learned')
        repaired = apply_envelope(batch, learned)
        self.assertEqual(repaired.selector_kind, 'rule')
        self.assertIsNone(repaired.behavior_log_prob)
        self.assertEqual(repaired.validate_batch(batch).root_action, safe.validate_batch(ex.batch).root_action)
        for schema in (c.LEGACY_CANDIDATE_BATCH_SCHEMA_VERSION, c.PRE_SURVIVAL_CANDIDATE_BATCH_SCHEMA_VERSION, c.CANDIDATE_BATCH_SCHEMA_VERSION):
            d = make_diagnostics(batch_schema=schema)
            self.assertEqual(c.from_json(d.to_json()), d)
        with self.assertRaisesRegex(ValueError, 'requires candidate batch v3'):
            replace(ex.batch, schema_version=c.PRE_SURVIVAL_CANDIDATE_BATCH_SCHEMA_VERSION)


class SurvivalReceiptTests(unittest.TestCase):
    def setup_runtime(self, *, latency=0, timeout=None):
        from tests.test_nextgen_tactic_manager import policy
        from eval.nextgen_gate_benchmark import SafeNoThreatMatch
        from puyo_env.realtime_ai import RealtimePolicyController, RealtimeDecisionConfig
        from src.core.puyo import Puyo
        p = policy()
        p.profile = c.SearchProfile('survival-receipt', 80, 100, 256)
        match = SafeNoThreatMatch(270)
        game = match.player_states['player_0'].simulator.game
        for y, row in enumerate(reversed(choke_board())):
            for x, cell in enumerate(row):
                game.field.place_puyo(x, y, Puyo(c.PUBLIC_CELL_TO_COLOR[cell]))
        game.current_puyo_1, game.current_puyo_2 = (Puyo(c.PUBLIC_CELL_TO_COLOR[v]) for v in (1, 2))
        controller = RealtimePolicyController(p, config=RealtimeDecisionConfig(
            inference_latency_ticks=latency, timeout_ticks=timeout))
        return match, controller

    def test_survival_receipt_then_actual_clear_and_return_to_build(self):
        match, controller = self.setup_runtime()
        with patch('puyo_env.realtime_ai.nextgen_authoritative_action_mask', return_value=mask(7, 9)):
            first_input = controller.next_input(match, 'player_0')
        d = controller.nextgen_scheduler.ledger[0]
        self.assertEqual(d.selection.reason, 'legitimate_survival_exception')
        self.assertEqual((d.receipt.requested_action, d.receipt.executed_action, d.receipt.outcome), (7, 7, 'activated'))
        self.assertIn('survival_adopted', d.receipt.reason)
        self.assertEqual(c.from_json(d.to_json()), d)
        result = match.step({'player_0': first_input})
        for _ in range(300):
            events = result.player_results['player_0'].events
            resolved = [e for e in events if e.type == 'resolution_complete']
            if resolved:
                self.assertEqual(resolved[0].data['chain_count'], 1)
                break
            result = match.step({'player_0': controller.next_input(match, 'player_0')})
        else:
            self.fail('clear did not resolve')
        self.assertFalse(match.player_states['player_0'].simulator.game.game_over)
        controller.next_input(match, 'player_0')
        latest = controller.nextgen_scheduler.ledger[-1]
        self.assertNotEqual(latest.selection.reason, 'legitimate_survival_exception')

    def test_late_and_reachability_fallback_are_not_reported_as_adopted(self):
        match, controller = self.setup_runtime(latency=1)
        with patch('puyo_env.realtime_ai.nextgen_authoritative_action_mask', return_value=mask(7, 9)):
            controller.next_input(match, 'player_0')
        match.step({})
        import puyo_env.realtime_ai as ai
        real = ai.realtime_reachable_action_mask
        def without_selected(simulator, **kwargs):
            result = real(simulator, **kwargs)
            result[7] = False
            return result
        with patch.object(ai, 'realtime_reachable_action_mask', side_effect=without_selected):
            controller.next_input(match, 'player_0')
        receipt = controller.nextgen_scheduler.ledger[0].receipt
        self.assertEqual(receipt.outcome, 'fallback')
        self.assertNotEqual(receipt.executed_action, receipt.requested_action)
        self.assertIn('survival_not_adopted_fallback', receipt.reason)
        self.assertFalse(receipt.actor_trainable)
        match, controller = self.setup_runtime(latency=2, timeout=0)
        with patch('puyo_env.realtime_ai.nextgen_authoritative_action_mask', return_value=mask(7, 9)):
            controller.next_input(match, 'player_0')
        receipt = controller.nextgen_scheduler.ledger[0].receipt
        self.assertEqual(receipt.outcome, 'timeout')
        self.assertFalse(receipt.actor_trainable)
        survival = controller.nextgen_scheduler.last_payload['search']['survival']
        self.assertTrue(all(r['status'] == 'deadline_unreachable' for r in survival['roots']))
