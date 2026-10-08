"""Compare the final planner with the fixed baseline, including live clocks."""
import hashlib
import json
from pathlib import Path
import pickle
import statistics
import time

from experiment import BASELINE, variant
from puyo_env.action_planner import _plans_for_actions
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.constants import Action
from src.core.realtime import RealtimeHeadlessSimulator
from tests.test_timed_placement_planner import CASES, first_lock, simulator

ROOT = Path(__file__).parent
reference, baseline_hash = variant('baseline')
rows = []
for label, source in [('empty', RealtimeHeadlessSimulator(seed=55))] + [
    (f"seed-{case['seed']}", simulator(case)) for case in CASES
]:
    parity_cases = 0
    for held in (None, Action.LEFT, Action.DOWN):
        for gravity_offset in (0, 1, 20):
            sim = source.clone()
            sim._next_gravity_tick = sim.tick + gravity_offset
            if held is not None:
                sim.held_actions.add(held)
                sim._next_repeat_tick[held] = sim.tick
            for budget in (1, 16, 2000):
                original = pickle.dumps(sim)
                before = reference._plans_for_actions(sim, PLACEMENT_ACTIONS, max_expanded_states=budget)
                after = _plans_for_actions(sim, PLACEMENT_ACTIONS, max_expanded_states=budget)
                for action in PLACEMENT_ACTIONS:
                    a, b = before.get(action), after.get(action)
                    assert (vars(a) if a else None) == (vars(b) if b else None), (label, held, gravity_offset, budget, action)
                    if b:
                        assert first_lock(sim.clone(), b.inputs) == (action.axis_x, b.expected_axis_y, action.rotation.name)
                assert pickle.dumps(sim) == original
                parity_cases += 1
    samples = {'before': [], 'after': []}
    for repeat in range(100):
        order = [('before', reference._plans_for_actions), ('after', _plans_for_actions)]
        for name, function in order if repeat % 2 else reversed(order):
            started = time.perf_counter_ns()
            function(source, PLACEMENT_ACTIONS)
            samples[name].append((time.perf_counter_ns() - started) / 1e6)
    rows.append({'case': label, 'parity_cases': parity_cases, 'all_roots_input_lock_parity': True,
                 'source_unchanged': True, 'median_ms': {k: statistics.median(v) for k,v in samples.items()},
                 'raw_ms': samples})
result = {'baseline': BASELINE, 'baseline_source_sha256': baseline_hash,
          'final_source_sha256': hashlib.sha256(Path('puyo_env/action_planner.py').read_bytes()).hexdigest(), 'cases': rows}
(ROOT / 'verify.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps([{k:v for k,v in r.items() if k!='raw_ms'} for r in rows],indent=2))
