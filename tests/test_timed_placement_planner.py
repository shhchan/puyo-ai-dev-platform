"""Timed input witnesses must lock the root predicted by the search contract."""
import copy
import json
import pickle
from pathlib import Path
import unittest

from puyo_env.action_planner import (
    PlannedPlacement, _control_probe, _step_control_probe,
    execute_planned_placement, plan_placement_action,
)
from puyo_env.actions import PLACEMENT_ACTIONS, action_to_placement
from puyo_env.realtime_ai import nextgen_authoritative_action_mask
from src.core.constants import Action, Direction, PuyoColor
from src.core.headless import HeadlessPuyoSimulator, PlacementAction
from src.core.puyo import Puyo
from src.core.realtime import RealtimeHeadlessSimulator, RealtimeTimingConfig, TickInput

CASES = json.loads((Path(__file__).parent/'fixtures/puyo_273_timed_placement.json').read_text())


def simulator(case):
    sim = RealtimeHeadlessSimulator(seed=case['seed'], timing=RealtimeTimingConfig(**case['timing']))
    sim.tick, sim._next_gravity_tick = case['tick'], case['next_gravity_tick']
    game = sim.game
    for y, row in enumerate(case['board']):
        for x, color in enumerate(row):
            game.field.grid[y][x] = Puyo(PuyoColor[color])
    game.current_puyo_1, game.current_puyo_2 = (Puyo(PuyoColor[c]) for c in case['pair'])
    game.puyo_x, game.puyo_y = case['position'][:2]
    game.puyo_rot = Direction[case['position'][2]]
    for name, value in case['control'].items():
        setattr(game, name, value)
    return sim


def first_lock(sim, inputs):
    for item in inputs:
        result = sim.step(item)
        for event in result.events:
            if event.type == 'lock':
                return (event.data['axis_x'], event.data['axis_y'], event.data['rotation'])
    return None


class TimedPlacementTests(unittest.TestCase):
    def test_seed137_and_148_legacy_inputs_reproduce_wrong_root(self):
        for case in CASES:
            with self.subTest(seed=case['seed']):
                sim = simulator(case)
                self.assertEqual(first_lock(sim, [TickInput.from_names(**i) for i in case['old_inputs']]),
                                 tuple(case['old_lock']))
                events = sim.run_until_control_or_game_over()
                self.assertEqual([e.data['chain_count'] for r in events for e in r.events
                                  if e.type == 'resolution_complete'], [case['old_chain']])

    def test_timed_repair_matches_root_and_predicted_chain_without_changing_source(self):
        for case in CASES:
            with self.subTest(seed=case['seed']):
                sim = simulator(case)
                before = pickle.dumps(sim)
                action = action_to_placement(case['action'])
                plan = plan_placement_action(sim, action)
                self.assertTrue(plan.reachable, plan.reason)
                self.assertTrue(nextgen_authoritative_action_mask(sim)[case['action']])
                self.assertEqual(before, pickle.dumps(sim))
                expected = HeadlessPuyoSimulator(game_state=copy.deepcopy(sim.game), auto_spawn=False)
                expected.step(action)
                actual = sim.clone()
                self.assertEqual(first_lock(actual, plan.inputs),
                                 (action.axis_x, plan.expected_axis_y, action.rotation.name))
                events = actual.run_until_control_or_game_over()
                self.assertEqual([e.data['chain_count'] for r in events for e in r.events
                                  if e.type == 'resolution_complete'], [case['predicted_chain']])
                self.assertEqual(actual.game.field.to_color_grid(), expected.game.field.to_color_grid())

    def test_execution_helper_retains_live_clock_instead_of_hiding_gravity_collision(self):
        for case in CASES:
            with self.subTest(seed=case['seed']):
                source = simulator(case)
                legacy = PlannedPlacement(action_to_placement(case['action']), True,
                    tuple(TickInput.from_names(**i) for i in case['old_inputs']), (), None)
                result = execute_planned_placement(source, legacy)
                self.assertGreater(result.tick, source.tick)
                self.assertEqual(result.last_resolution_chain_count, case['old_chain'])
                self.assertEqual(source.tick, case['tick'])

    def test_identical_board_with_a_different_gravity_deadline_needs_a_different_witness(self):
        source = simulator(CASES[0])
        later = source.clone()
        later._next_gravity_tick += later.timing.gravity_interval_ticks
        action = action_to_placement(CASES[0]['action'])
        due_plan, later_plan = plan_placement_action(source, action), plan_placement_action(later, action)
        self.assertTrue(due_plan.reachable and later_plan.reachable)
        self.assertNotEqual(due_plan.inputs, later_plan.inputs)
        self.assertEqual(source.game.field.to_color_grid(), later.game.field.to_color_grid())
        for sim, plan in ((source, due_plan), (later, later_plan)):
            self.assertEqual(first_lock(sim, plan.inputs),
                             (action.axis_x, plan.expected_axis_y, action.rotation.name))

    def test_first_lock_before_target_is_fail_closed(self):
        sim = RealtimeHeadlessSimulator(seed=55)
        sim.game.puyo_y = 0
        sim.game.ground_frame_count = 31
        action = PlacementAction(5, Direction.UP)
        plan = plan_placement_action(sim, action)
        self.assertFalse(plan.reachable)
        self.assertEqual(plan.inputs, ())
        self.assertIn('verified timed', plan.reason)
        self.assertFalse(nextgen_authoritative_action_mask(sim)[PLACEMENT_ACTIONS.index(action)])

    def test_control_probe_matches_authoritative_ticks_with_gravity_down_and_existing_hold(self):
        for case in CASES:
            real = simulator(case)
            real.held_actions.add(Action.LEFT)
            real._next_repeat_tick[Action.LEFT] = real.tick + 1
            probe = _control_probe(real, real.timing)
            inputs = [TickInput(press=(Action.DOWN,)), TickInput(release=(Action.DOWN,)),
                      TickInput(release=(Action.LEFT,), press=(Action.DOWN,)),
                      TickInput(release=(Action.DOWN,), press=(Action.ROTATE_RIGHT,))]
            inputs += [TickInput()] * 35
            for tick_input in inputs:
                if real.game.state != 'control':
                    break
                result = real.step(tick_input)
                _step_control_probe(probe, tick_input)
                for name in ('puyo_x','puyo_y','puyo_rot','blocked_rotate_input_count',
                             'ground_frame_count','ground_contact_count','floor_kick_horizontal_grace'):
                    self.assertEqual(getattr(real.game,name),getattr(probe.game,name),name)
                self.assertEqual(real.held_actions,probe.held_actions)
                self.assertEqual(real._next_repeat_tick,probe._next_repeat_tick)
                self.assertEqual(real._next_gravity_tick,probe._next_gravity_tick)
                self.assertEqual(real.tick,probe.tick)
                self.assertEqual(real.game.state,probe.game.state)

    def test_every_advertised_root_locks_exactly_in_authoritative_simulator(self):
        for case in CASES:
            source = simulator(case)
            before = pickle.dumps(source)
            mask = nextgen_authoritative_action_mask(source)
            self.assertTrue(any(mask))
            for action, allowed in zip(PLACEMENT_ACTIONS, mask):
                if not allowed:
                    continue
                with self.subTest(seed=case['seed'], action=action):
                    plan = plan_placement_action(source, action)
                    self.assertTrue(plan.reachable)
                    self.assertEqual(first_lock(source.clone(), plan.inputs),
                                     (action.axis_x, plan.expected_axis_y, action.rotation.name))
            self.assertEqual(before, pickle.dumps(source))

    def test_small_budget_conservatively_rejects_timed_root(self):
        for case in CASES:
            sim = simulator(case)
            self.assertFalse(plan_placement_action(sim, action_to_placement(case['action']),
                                                   max_expanded_states=0).reachable)
