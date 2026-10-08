"""Idle allocation shortcuts retain authoritative clock, hold and first lock."""
import pickle
import unittest

from puyo_env.action_planner import _IDLE_INPUT, _control_probe, _step_control_probe
from src.core.constants import Action
from src.core.realtime import RealtimeHeadlessSimulator, TickInput
from tests.test_timed_placement_planner import CASES, simulator


class PlannerIdleControlTests(unittest.TestCase):
    def test_idle_and_release_match_real_control_across_gravity_and_repeat(self):
        sources = [RealtimeHeadlessSimulator(seed=55), *(simulator(case) for case in CASES)]
        for source in sources:
            for hold in (None, Action.LEFT, Action.RIGHT, Action.DOWN):
                for gravity_offset in (0, 1, 20):
                    with self.subTest(tick=source.tick, hold=hold, gravity_offset=gravity_offset):
                        real = source.clone()
                        real._next_gravity_tick = real.tick + gravity_offset
                        # Expired repeat entries must remain unchanged without a
                        # hold, while an actual held key still fires on schedule.
                        for action in real._next_repeat_tick:
                            real._next_repeat_tick[action] = real.tick - 1
                        if hold is not None:
                            real.held_actions.add(hold)
                        before = pickle.dumps(real)
                        probe = _control_probe(real, real.timing)
                        inputs = [_IDLE_INPUT] * 2
                        inputs += [TickInput(release=(Action.LEFT, Action.RIGHT, Action.DOWN)),
                                   TickInput(press=(Action.DOWN,)), _IDLE_INPUT,
                                   TickInput(release=(Action.DOWN,))]
                        inputs += [_IDLE_INPUT] * 60
                        for tick_input in inputs:
                            if real.game.state != 'control':
                                break
                            result = real.step(tick_input)
                            _step_control_probe(probe, tick_input)
                            for field in ('puyo_x', 'puyo_y', 'puyo_rot', 'blocked_rotate_input_count',
                                          'ground_frame_count', 'ground_contact_count',
                                          'floor_kick_horizontal_grace', 'score', 'state'):
                                self.assertEqual(getattr(real.game, field), getattr(probe.game, field), field)
                            self.assertEqual(real.held_actions, probe.held_actions)
                            self.assertEqual(real._next_repeat_tick, probe._next_repeat_tick)
                            self.assertEqual(real._next_gravity_tick, probe._next_gravity_tick)
                            self.assertEqual(real.tick, probe.tick)
                            self.assertEqual(real.game.field.to_color_grid(), probe.game.field.to_color_grid())
                            locks = [event for event in result.events if event.type == 'lock']
                            if locks:
                                lock = locks[0].data
                                self.assertEqual((probe.game._planner_lock[0], probe.game._planner_lock[1],
                                                  probe.game._planner_lock[2].name),
                                                 (lock['axis_x'], lock['axis_y'], lock['rotation']))
                        self.assertNotEqual(pickle.dumps(real), before)


if __name__ == '__main__':
    unittest.main()
