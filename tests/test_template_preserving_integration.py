"""PUYO-268: committed catalog constraints through batch, selector and receipt."""
import json
import unittest
from dataclasses import replace
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import transition
from agents.deep_chain_search_backend import NativeLongHorizonSearchBackend, PythonLongHorizonSearchBackend
from agents.nextgen_shared_search import SharedSearchBatchBuilder, SharedSearchCache, _pairs, _public_state, scenario_provenance
from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy
from agents.template_catalog import TemplateCatalog, _conditions, _evaluate, compile_selected_template, load_template_catalog, match_templates
from agents.template_phase import TemplatePhaseController
from eval.nextgen_gate_benchmark import SafeNoThreatMatch
from puyo_env.realtime_ai import RealtimePolicyController, RealtimeDecisionConfig
from src.core.puyo import Puyo
from tests.test_nextgen_shared_search import config, request
from tests.test_selected_template_search import state
from tests.test_template_catalog import catalog as raw_catalog

CATALOG = Path('train/config/nextgen_templates.yaml')
CORPUS = json.loads(Path('tests/fixtures/puyo_271_regression_cases.json').read_text())


def selected_catalog(name, mirror=False):
    import yaml
    raw = yaml.safe_load(CATALOG.read_text())
    raw['templates'] = [v for v in raw['templates'] if v['id'] == name]
    if mirror:
        raw['templates'][0]['variants'][0]['transforms'].append('mirror_x')
    return TemplateCatalog.from_dict(raw)


def key(cat, binding=None, transform='identity'):
    template = cat.templates[0]
    return (template.id, template.variants[0].id, transform,
            tuple(sorted((binding or {'A': 1, 'B': 2, 'C': 3}).items())))


def public_board(rows, unknown=False):
    bottom = [tuple(int(v) for v in row) for row in rows]
    bottom += [(0,) * 6] * (14 - len(bottom))
    result = list(reversed(bottom))
    if unknown:
        result[:2] = [(None,) * 6] * 2
    return tuple(result)


def make_request(cat, cfg, rows=(), known=((1, 3), (2, 3), (2, 2)), quota=132, unknown=False, mask=None):
    req = request(cfg, active=True, cat=cat, board=public_board(rows, unknown), quota=(quota, 0, 128), mask=mask)
    own = replace(req.public.own, known_pieces=known)
    public = replace(req.public, own=own)
    return replace(req, identity=replace(req.identity, snapshot_digest=public.digest), public=public,
                   control=replace(req.control, phase=replace(req.control.phase, template_id=cat.templates[0].id),
                                   scenario_provenance=scenario_provenance(known, cfg)))


class CatalogCompilerTests(unittest.TestCase):
    def test_frozen_horizontal_and_l_now_have_distinct_conflicts(self):
        cat = selected_catalog('persian')
        variant = cat.templates[0].variants[0]
        selected = compile_selected_template(cat, key(cat))
        for case in CORPUS['persian_counterexamples']:
            rows = case['rows_bottom_up']
            board = tuple(reversed(public_board(rows)))
            satisfied, conflicts = _evaluate(board, _conditions(variant, 'identity'), case['binding'])
            self.assertEqual(len(satisfied), case['expected_static_satisfied'])
            is_l = case['id'] == 'persian_l_corner'
            self.assertEqual(conflicts, int(is_l))
            self.assertEqual(selected.evaluate(state(rows), state(rows))[0], not is_l)
        # Other-color support in the same dot is allowed, including an L in
        # an unrelated color outside the required footprint.
        for rows in (['110000', '400000'], ['110000', '400000', '440000']):
            self.assertTrue(selected.evaluate(state(rows), state(rows))[0])

    def test_alias_color_permutation_mirror_and_cache_identity(self):
        cat = selected_catalog('gtr', mirror=True)
        variants = [key(cat, {'A': 1, 'B': 2, 'C': 1}),
                    key(cat, {'A': 3, 'B': 4, 'C': 3}),
                    key(cat, {'A': 1, 'B': 2, 'C': 1}, 'mirror_x')]
        compiled = [compile_selected_template(cat, value) for value in variants]
        self.assertEqual(dict(compiled[0].binding), {'A=C': 1, 'B': 2})
        self.assertEqual(len({v.semantic_digest for v in compiled}), 3)
        self.assertEqual({(2-x, y, color) for x, y, color in compiled[0].required_cells}, set(compiled[2].required_cells))
        self.assertEqual({(2-x, y, color) for x, y, color in compiled[0].forbidden_cells}, set(compiled[2].forbidden_cells))
        backend = PythonLongHorizonSearchBackend()
        cache = SharedSearchCache()
        cfg = replace(config(), depth=1, scenarios=1)
        req = make_request(cat, cfg, quota=22)
        builder = SharedSearchBatchBuilder(backend, cfg, template_catalog=cat, shared_cache=cache)
        first = builder.build(req, template_key=variants[0])
        again = builder.build(req, template_key=variants[0])
        self.assertTrue(again.diagnostics['shared_reuse']['hit'])
        for value in variants[1:]:
            different = builder.build(req, template_key=value)
            self.assertFalse(different.diagnostics['shared_reuse']['hit'])
        self.assertEqual(first.diagnostics['selected_template']['phase_key'], variants[0])

    def test_all_shapes_allow_other_color_support_and_unrelated_tail(self):
        for name, rows, binding, guard in (
            ('gtr', ['221000', '112000', '120000'], {'A': 1, 'B': 2, 'C': 1}, (0, 3, 1)),
            ('daa', ['122000', '112000'], {'A': 1, 'B': 2}, (1, 2, 1)),
            ('persian', ['111333', '023200', '002200'], {'A': 1, 'B': 2, 'C': 3}, (0, 1, 1)),
        ):
            cat = selected_catalog(name, mirror=True)
            for transform in ('identity', 'mirror_x'):
                compiled = compile_selected_template(cat, key(cat, binding, transform))
                width = cat.templates[0].variants[0].width
                base = [list(row) for row in public_board(rows)[::-1]]
                if transform == 'mirror_x':
                    base = [row[:width][::-1] + row[width:] for row in base]
                x, y, color = guard
                x = width - 1 - x if transform == 'mirror_x' else x
                base[y][x] = 4
                self.assertTrue(compiled.evaluate(state(base), state(base))[0])
                base[y][x] = color
                self.assertFalse(compiled.evaluate(state(base), state(base))[0])
                # Color permutation preserves the same relation.
                permuted = {s: (5 if c == 1 else c) for s, c in binding.items()}
                new = compile_selected_template(cat, key(cat, permuted, transform))
                replaced = [[5 if c == 1 else c for c in row] for row in base]
                self.assertFalse(new.evaluate(state(replaced), state(replaced))[0])

    def test_unknown_guard_cannot_close_an_otherwise_complete_phase(self):
        cat = selected_catalog('gtr')
        fixed = key(cat, {'A': 1, 'B': 2, 'C': 1})
        rows = [list(row) for row in public_board(['221000', '112000', '120000'])]
        rows[-4][0] = None  # The A guard above the left triple is unobserved.
        result = match_templates(cat, rows, (), node_budget=0, binding_budget=0, preferred_key=fixed)
        chosen = next(v for v in result.candidates if v.key == fixed)
        self.assertFalse(chosen.complete)
        phase = TemplatePhaseController(cat, seed=0)
        phase.start(result, decision_id='1')
        phase.reconcile(result)
        self.assertTrue(phase.can_build_template)

    def test_fixed_binding_survives_zero_matcher_quota(self):
        cat = selected_catalog('persian')
        fixed = key(cat, {'A': 4, 'B': 1, 'C': 2})
        result = match_templates(cat, public_board([]), (), node_budget=0, binding_budget=0, preferred_key=fixed)
        candidate = next(v for v in result.candidates if v.key == fixed)
        self.assertEqual(candidate.fit_status, 'unknown')
        self.assertTrue(candidate.cutoff)
        invalid = (*fixed[:3], (('A', 1), ('B', 1), ('C', 2)))
        with self.assertRaisesRegex(ValueError, 'preferred template binding'):
            match_templates(cat, public_board([]), (), node_budget=0, binding_budget=0, preferred_key=invalid)

    def test_matcher_compares_root_layer_instead_of_first_fit(self):
        cat = TemplateCatalog.from_dict(raw_catalog(rows=('AA',)))
        result = match_templates(cat, public_board([]), ((1, 1),), node_budget=22, binding_budget=1,
                                 preferred_key=('fixture', 'base', 'identity', (('A', 1),)))
        candidate = next(v for v in result.candidates if v.binding == (('A', 1),))
        # Action 0 fits one A; action 1 fits both required cells horizontally.
        self.assertEqual(candidate.witness_actions, (1,))
        self.assertEqual(candidate.score, 1.0)
        self.assertEqual(candidate.coverage_nodes, 22)


class SharedConstraintTests(unittest.TestCase):
    def test_python_native_multiple_roots_ranking_and_destructive_rejection(self):
        cat = selected_catalog('persian')
        cfg = replace(config(), depth=3, width=4, scenarios=2)
        req = make_request(cat, cfg, ['110000'], quota=132)
        executions = [SharedSearchBatchBuilder(backend, cfg, template_catalog=cat).build(req, template_key=key(cat))
                      for backend in (PythonLongHorizonSearchBackend(), NativeLongHorizonSearchBackend(execution_mode='oracle-1'))]
        self.assertEqual(executions[0].batch.to_dict() | {'counters': None}, executions[1].batch.to_dict() | {'counters': None})
        for ex in executions:
            roots = ex.shared_result.selected_template['roots']
            row = next(v for v in ex.batch.tactics if v.tactic_id == 'build_template')
            ordered = [next(v.root_action for v in ex.batch.candidates if v.candidate_id == cid) for cid in row.candidate_ids]
            self.assertGreater(len(ordered), 1)
            expected = sorted(enumerate(ex.shared_result.compatible_ranked_roots),
                              key=lambda item: (0 if roots[item[1].root_action]['known_witness'] == (item[1].root_action,)
                                                else 1 if roots[item[1].root_action]['known_witness'] else 2, item[0]))
            self.assertEqual(ordered, [v.root_action for _, v in expected])
            self.assertTrue(roots[0]['root_violation'])  # attaching A above the left A
            self.assertNotIn(0, ordered)
            self.assertEqual(ex.select('build_template').root_action, ordered[0])
            state_before, _ = _public_state(req)
            selected = compile_selected_template(cat, key(cat))
            for action in ordered:
                resolved = transition(state_before, _pairs(req.public.own.known_pieces)[0], action)
                self.assertTrue(selected.evaluate(resolved.state, state_before)[0])
            c.validate_request_batch(req, ex.batch)

    def test_unknown_cutoff_and_public_completion_are_not_conflated(self):
        cat = TemplateCatalog.from_dict(raw_catalog(rows=('A',)))
        fixed = ('fixture', 'base', 'identity', (('A', 1),))
        cfg = replace(config(), depth=2, scenarios=1)
        for known, quota, unknown in ((((1, 2),), 22, False), (((2, 3),), 22, False), (((1, 2),), 22, True), (((1, 2),), 0, False)):
            req = make_request(cat, cfg, known=known, quota=quota, unknown=unknown)
            ex = SharedSearchBatchBuilder(PythonLongHorizonSearchBackend(), cfg, template_catalog=cat).build(req, template_key=fixed)
            row = next(v for v in ex.batch.tactics if v.tactic_id == 'build_template')
            if unknown or known[0] == (2, 3) or not quota:
                self.assertFalse(row.known_witness)
            else:
                self.assertTrue(row.known_witness)
            if quota and known[0] == (2, 3):
                self.assertEqual({v['status'] for v in ex.shared_result.selected_template['roots'].values() if v['compatible']}, {'cutoff'})
                self.assertTrue(row.available)
        cfg = replace(cfg, decision_seed=0)
        req = make_request(cat, cfg, known=((2, 3),), quota=200)
        ex = SharedSearchBatchBuilder(PythonLongHorizonSearchBackend(), cfg, template_catalog=cat).build(req, template_key=fixed)
        self.assertFalse(ex.batch.tactics[1].known_witness)
        self.assertTrue(all(not r['known_witness'] for r in ex.shared_result.selected_template['roots'].values()))
        self.assertTrue(any(r['sampled_witness'] for r in ex.shared_result.selected_template['roots'].values()))
        self.assertEqual(ex.diagnostics['selected_template']['completion_boundary_roots'], [])

    def test_required_survival_clear_overrides_all_template_violations(self):
        from agents.nextgen_survival import apply_envelope
        cat = selected_catalog('persian')
        cfg = replace(config(), depth=2, scenarios=1)
        rows = CORPUS['survival_counterexamples'][0]['rows_bottom_up']
        req = make_request(cat, cfg, rows, known=((1, 2),), quota=44,
                           mask=tuple(a in (7, 9) for a in range(c.NUM_ACTIONS)))
        ex = SharedSearchBatchBuilder(PythonLongHorizonSearchBackend(), cfg, template_catalog=cat).build(req, template_key=key(cat))
        self.assertFalse(ex.batch.tactics[1].available)
        self.assertTrue(all(r['root_violation'] for r in ex.shared_result.selected_template['roots'].values()))
        chosen = ex.select('build_main')
        selection = c.Selection('build_main', chosen.candidate_id, ex.batch.digest, 'rule', None, None, None, 'test')
        actual = apply_envelope(ex.batch, selection)
        self.assertEqual(actual.reason, 'legitimate_survival_exception')
        self.assertEqual(actual.validate_batch(ex.batch).root_action, 7)
        self.assertTrue(ex.diagnostics['survival']['active'])

    def test_fixed_conflict_closes_phase_and_releases_on_next_request(self):
        cat = selected_catalog('persian')
        fixed = key(cat)
        phase = TemplatePhaseController(cat, seed=0)
        before = match_templates(cat, public_board(['110000']), (), node_budget=0, binding_budget=0, preferred_key=fixed)
        phase.start(before, decision_id='1')
        conflict = match_templates(cat, public_board(['110000', '100000']), (), node_budget=0, binding_budget=0, preferred_key=fixed)
        phase.reconcile(conflict)
        self.assertEqual(phase.exit_reason, 'no_compatible_candidate')
        self.assertEqual(phase.candidate.key, fixed)
        self.assertEqual(phase.candidate.reason, 'fixed_binding_conflict')
        self.assertFalse(phase.can_build_template)
        self.assertEqual(phase.diagnostics()['constraint_retention'], 'released')

    def test_three_catalog_rollouts_complete_with_backend_ranked_roots(self):
        cfg = replace(config(), depth=3, width=8, scenarios=1)
        for case in CORPUS['template_rollouts'][:3]:
            cat = selected_catalog(case['template_id'])
            binding = {'A': 1, 'B': 2}
            if case['template_id'] != 'daa':
                binding['C'] = 1 if case['template_id'] == 'gtr' else 3
            fixed = key(cat, binding)
            rows = case['rows_bottom_up']
            phase = TemplatePhaseController(cat, seed=0)
            matched = match_templates(cat, public_board(rows), (), node_budget=0, binding_budget=0, preferred_key=fixed)
            phase.start(matched, decision_id='1')
            pieces = tuple(map(tuple, case['public_pieces']))
            for step in range(len(pieces)):
                req = make_request(cat, cfg, rows, known=pieces[step:], quota=1024)
                ex = SharedSearchBatchBuilder(NativeLongHorizonSearchBackend(execution_mode='oracle-1'), cfg, template_catalog=cat).build(req, template_key=fixed)
                self.assertTrue(ex.batch.tactics[1].known_witness)
                if case['template_id'] == 'persian' and step == 1:
                    self.assertIn(ex.select('build_template').root_action, ex.diagnostics['selected_template']['completion_boundary_roots'])
                self.assertTrue(phase.can_build_template)  # future witness isn't current completion
                before, _ = _public_state(req)
                after = transition(before, _pairs(req.public.own.known_pieces)[0], ex.select('build_template').root_action)
                self.assertEqual(after.chain_count, 0)
                rows = [[next((color + 1 for color, plane in enumerate(after.state.planes) if plane & (1 << (y*6+x))), 0)
                         for x in range(6)] for y in range(14)]
                match = match_templates(cat, public_board(rows), (), node_budget=0, binding_budget=0, preferred_key=fixed)
                phase.reconcile(match)
                if not phase.can_build_template:
                    break
            self.assertEqual(phase.exit_reason, 'completed', case['id'])

    def test_completion_and_limit_release_backend_constraint(self):
        cfg = replace(config(), depth=1, scenarios=1)
        for name, rows, binding in (
            ('gtr', ['221000', '112000', '120000'], {'A': 1, 'B': 2, 'C': 1}),
            ('daa', ['122000', '112000'], {'A': 1, 'B': 2}),
            ('persian', ['111333', '023200', '002200'], {'A': 1, 'B': 2, 'C': 3}),
        ):
            cat = selected_catalog(name)
            fixed = key(cat, binding)
            result = match_templates(cat, public_board(rows), (), node_budget=0, binding_budget=0, preferred_key=fixed)
            controller = TemplatePhaseController(cat, seed=0)
            controller.start(result, decision_id='1')
            controller.reconcile(result)
            self.assertFalse(controller.can_build_template)
            self.assertEqual(controller.exit_reason, 'completed')
            req = make_request(cat, cfg, rows, quota=22)
            req = replace(req, control=replace(req.control, phase=controller.phase_snapshot()))
            ex = SharedSearchBatchBuilder(PythonLongHorizonSearchBackend(), cfg, template_catalog=cat).build(req, template_key=fixed)
            self.assertIsNone(ex.shared_result.selected_template)
            self.assertEqual(ex.diagnostics['selected_template']['retention'], 'released')


class TemplateReceiptTests(unittest.TestCase):
    def test_phase_releases_only_after_actual_completion_is_observed(self):
        cat = selected_catalog('gtr')
        cfg = replace(config(), depth=3, width=8, scenarios=1)
        policy = NextgenTacticManagerPolicy(catalog=cat, search_config=cfg,
                    profile=c.SearchProfile('268-complete', 1024, 22, 128), backend='native')
        match = SafeNoThreatMatch(268)
        game = match.player_states['player_0'].simulator.game
        rows = ['221000', '112000']
        for y, row in enumerate(rows):
            for x, color in enumerate(row):
                game.field.place_puyo(x, y, Puyo(c.PUBLIC_CELL_TO_COLOR[int(color)]))
        game.current_puyo_1, game.current_puyo_2 = [Puyo(c.PUBLIC_CELL_TO_COLOR[v]) for v in (1, 2)]
        controller = RealtimePolicyController(policy, config=RealtimeDecisionConfig())
        runtime = controller.nextgen_scheduler
        fixed = key(cat, {'A': 1, 'B': 2, 'C': 1})
        matched = match_templates(cat, public_board(rows), (), node_budget=0, binding_budget=0, preferred_key=fixed)
        runtime.phase.start(matched, decision_id='prepared')
        first = controller.next_input(match, 'player_0')
        self.assertEqual(runtime.ledger[0].receipt.outcome, 'activated')
        self.assertEqual(runtime.ledger[0].selection.selected_tactic_id, 'build_template')
        self.assertTrue(runtime.phase.can_build_template)
        match.step({'player_0': first})
        for _ in range(600):
            value = controller.next_input(match, 'player_0')
            if len(runtime.ledger) >= 2:
                break
            match.step({'player_0': value})
        self.assertGreaterEqual(len(runtime.ledger), 2)
        self.assertFalse(runtime.ledger[1].request.control.phase.active)
        self.assertEqual(runtime.phase.exit_reason, 'completed')
        self.assertIsNone(runtime.ledger_metadata[1]['selected_template']['constraint'])
        self.assertEqual(runtime.errors, [])

    def test_real_scheduler_keeps_public_trace_and_adoption_outcome(self):
        cat = selected_catalog('persian')
        cfg = replace(config(), depth=3, width=4, scenarios=1)
        p = NextgenTacticManagerPolicy(catalog=cat, search_config=cfg,
                                      profile=c.SearchProfile('268', 132, 22, 128), backend='python')
        for timeout in (None, 0):
            match = SafeNoThreatMatch(268)
            game = match.player_states['player_0'].simulator.game
            for x in (0, 1):
                game.field.place_puyo(x, 0, Puyo(c.PUBLIC_CELL_TO_COLOR[1]))
            game.current_puyo_1, game.current_puyo_2 = [Puyo(c.PUBLIC_CELL_TO_COLOR[v]) for v in (1, 3)]
            controller = RealtimePolicyController(p, config=RealtimeDecisionConfig(inference_latency_ticks=1 if timeout == 0 else 0, timeout_ticks=timeout))
            runtime = controller.nextgen_scheduler
            fixed = key(cat)
            matched = match_templates(cat, public_board(['110000']), (), node_budget=0, binding_budget=0, preferred_key=fixed)
            runtime.phase.start(matched, decision_id='prepared')
            controller.next_input(match, 'player_0')
            self.assertEqual(runtime.errors, [])
            receipt = runtime.ledger[0].receipt
            trace = runtime.ledger_metadata[0]['selected_template']
            self.assertEqual(trace['phase_key'], fixed)
            root = trace['result']['roots'][receipt.requested_action]
            self.assertTrue(root['compatible'])
            self.assertFalse(root['root_violation'])
            self.assertIn('template_adopted' if timeout is None else 'template_not_adopted_timeout', receipt.reason)
            self.assertEqual(runtime.phase.phase.consumed_decisions, int(timeout is None))
            self.assertEqual(c.Diagnostics.from_dict(runtime.last_payload['nextgen']), runtime.ledger[0])


if __name__ == '__main__':
    unittest.main()
