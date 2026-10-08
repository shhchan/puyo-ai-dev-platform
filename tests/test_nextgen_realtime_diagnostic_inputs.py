"""The reported softmax/human condition must survive diagnostic configuration."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from eval.nextgen_realtime_diagnostic import diagnostic_config, load_human_inputs
from eval.nextgen_realtime_audit import audit
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.realtime import TickInput

FIXTURE = Path('docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/human-inputs.json')


class DiagnosticInputTests(unittest.TestCase):
    def config(self, **overrides):
        values = dict(seed=127, seed_a=58, seed_b=None, templates='daa',
                      selection_mode='softmax', temperature=1.0, speed=1.0,
                      opponent='human', profile='nextgen_safe_build', backend='native',
                      max_ticks=3500, write_replay=True, output=Path('/tmp/example'))
        return diagnostic_config(**(values | overrides))

    def test_reported_settings_are_not_replaced_by_defaults(self):
        config = self.config()
        self.assertEqual((config.seed, config.seed_a, config.nextgen_seed), (127, 58, 127))
        self.assertEqual((config.nextgen_selection_mode, config.nextgen_temperature), ('softmax', 1.0))
        self.assertEqual((config.nextgen_templates, config.policy_b, config.speed), ('daa', 'human', 1.0))
        for temperature in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.config(temperature=temperature)
        with self.assertRaises(ValueError):
            self.config(selection_mode='invalid')

    def test_fixture_replays_two_hand_all_clear_and_later_bonus(self):
        inputs, source = load_human_inputs(FIXTURE, seed=127)
        match = RealtimeVersusMatch(seed=127)
        locks, clears, outgoing, ticks = [], [], [], []
        for _ in range(max(inputs) + 1):
            value = inputs.get(match.tick, TickInput())
            result = match.step({'player_1': value})
            ticks.append({'tick': result.tick, 'inputs': {'player_1': value.to_json()},
                          'snapshot_hash': result.snapshot_hash})
            for event in result.player_results['player_1'].events:
                if event.type == 'lock':
                    locks.append(event)
                if event.type == 'resolution_complete':
                    clears.append((len(locks), event.data))
            if result.attack_diagnostics['player_1']['outgoing']:
                outgoing.append((result.tick, result.attack_diagnostics['player_1']['outgoing']))
        achieved = [n for n, event in clears if event['all_clear_achieved']]
        consumed = [n for n, event in clears if event['all_clear_bonus_consumed']]
        self.assertEqual(achieved, [2])
        self.assertEqual(consumed, [12])
        self.assertEqual(outgoing, [(690, 32)])
        replay = {'seed': 127, 'ticks': ticks, 'match_rules': match.replay_rules(),
                  'expected_final_hash': match.state_hash()}
        checked = audit({'attempts': []}, replay)
        self.assertEqual(checked['final_hash_verified'], match.state_hash())
        self.assertEqual(checked['decisions'], [])
        self.assertEqual([e['tick'] for e in checked['events']
                          if e['type'] == 'resolution_complete' and e['player'] == 'player_1'
                          and e['all_clear_bonus_consumed']], [690])
        replay['expected_final_hash'] = 'corrupted'
        with self.assertRaises(AssertionError):
            audit({'attempts': []}, replay)
        self.assertIn('original human input unavailable', source['provenance'])
        with self.assertRaises(ValueError):
            load_human_inputs(FIXTURE, seed=128)

    def test_ambiguous_ticks_and_invalid_edges_rejected(self):
        fixture = json.loads(FIXTURE.read_text())
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inputs.json'
            fixture['inputs'].append(fixture['inputs'][0])
            path.write_text(json.dumps(fixture))
            with self.assertRaises(ValueError):
                load_human_inputs(path, seed=127)
            fixture['inputs'] = [{'tick': 0, 'input': {'edges': [['bad', 'LEFT']]}}]
            path.write_text(json.dumps(fixture))
            with self.assertRaises(ValueError):
                load_human_inputs(path, seed=127)


if __name__ == '__main__':
    unittest.main()
