"""Isolate planner allocation changes without a GUI or search budget changes."""
import hashlib
import json
from pathlib import Path
import pickle
import statistics
import subprocess
import sys
import time
import types

from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.realtime import RealtimeHeadlessSimulator
from tests.test_timed_placement_planner import CASES, first_lock, simulator

BASELINE = '4056baa70de9b1d0ae897e2fcb1d07ae69db3441'
ROOT = Path(__file__).parent
source = subprocess.check_output(['git', 'show', f'{BASELINE}:puyo_env/action_planner.py'], text=True)

def variant(name):
    code = source
    if name in ('pulses', 'pulses_idle', 'all', 'pulses_idle_fast'):
        at = '\n\n@dataclass(frozen=True)'
        code = code.replace(at, '\n\n_PLANNER_PULSES = {action: inputs_from_action_pulses((action,)) for action in PLANNER_ACTIONS}' + at, 1)
        code = code.replace('for tick_input in inputs_from_action_pulses((step_action,)):', 'for tick_input in _PLANNER_PULSES[step_action]:')
    if name in ('idle', 'pulses_idle', 'all', 'idle_fast', 'pulses_idle_fast'):
        code = code.replace('\n\n@dataclass(frozen=True)', '\n\n_IDLE_INPUT = TickInput()\n\n@dataclass(frozen=True)', 1)
        code = code.replace('        tick_input = TickInput()\n', '        tick_input = _IDLE_INPUT\n')
    if name in ('idle_fast', 'pulses_idle_fast'):
        code = code.replace('    fired = probe._collect_fired_actions(probe.tick, tick_input)',
                            '    fired = (() if tick_input is _IDLE_INPUT and not probe.held_actions\n'
                            '             else probe._collect_fired_actions(probe.tick, tick_input))')
    if name in ('clone', 'all'):
        code = code.replace('        probe = copy.copy(source)', '''        if type(source) is RealtimeHeadlessSimulator:
            probe = object.__new__(RealtimeHeadlessSimulator)
            probe.__dict__ = source.__dict__.copy()
        else:
            probe = copy.copy(source)''')
    module = types.ModuleType('allocation_' + name)
    sys.modules[module.__name__] = module
    exec(compile(code, module.__name__, 'exec'), vars(module))
    return module, hashlib.sha256(code.encode()).hexdigest()


def main():
    modules = {name: variant(name) for name in ('baseline', 'pulses', 'idle', 'clone', 'pulses_idle', 'idle_fast', 'pulses_idle_fast')}
    rows = []
    for label, sim in [('empty', RealtimeHeadlessSimulator(seed=55))] + [(f"seed-{c['seed']}", simulator(c)) for c in CASES]:
        original = pickle.dumps(sim)
        expected = modules['baseline'][0]._plans_for_actions(sim, PLACEMENT_ACTIONS)
        for name, (module, _) in modules.items():
            actual = module._plans_for_actions(sim, PLACEMENT_ACTIONS)
            for action in PLACEMENT_ACTIONS:
                before, after = expected.get(action), actual.get(action)
                assert (vars(before) if before else None) == (vars(after) if after else None), (label, name, action)
                if after:
                    assert first_lock(sim.clone(), after.inputs) == (action.axis_x, after.expected_axis_y, action.rotation.name)
            assert pickle.dumps(sim) == original
        samples = {name: [] for name in modules}
        names = list(modules)
        for repeat in range(100):
            order = names[repeat % len(names):] + names[:repeat % len(names)]
            for name in order:
                started = time.perf_counter_ns()
                modules[name][0]._plans_for_actions(sim, PLACEMENT_ACTIONS)
                samples[name].append((time.perf_counter_ns() - started) / 1e6)
        row = {'case': label, 'full_plan_and_lock_parity': True, 'source_unchanged': True,
               'median_ms': {name: statistics.median(values) for name, values in samples.items()}, 'raw_ms': samples}
        rows.append(row)
    result = {'baseline': BASELINE, 'variant_sha256': {n: sha for n, (_, sha) in modules.items()}, 'cases': rows}
    (ROOT / 'experiment.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps([{k:v for k,v in r.items() if k!='raw_ms'} for r in rows],indent=2))

if __name__ == '__main__':
    main()
